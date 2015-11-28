import asyncio
import logging
import os
import re

from asyncio import coroutine
from datetime import datetime
from urllib.parse import unquote_plus, parse_qs

from centimani.errors import HttpError
from centimani.headers import Headers, HeaderParseError
from centimani.server.router import RoutingError
from centimani.server.handlers import Request, Response
from centimani.server.handlers import AbstractConnection, AbstractTransport
from centimani.streamutils import BufferedBodyReader, ChunkedBodyReader
from centimani.utils import HTTP_STATUSES, SUPPORTED_METHODS

if "StopAsyncIteration" not in dir(__builtins__):
    from asyncioplus.utils import StopAsyncIteration


_LOGGER = logging.getLogger(__name__)

_SEGMENT = rb"(?:[-._~A-Za-z0-9!$&'()*+,;=:@]|%[0-9A-F]{2})+"
_PATH = rb"/(?:" + _SEGMENT + rb"(?:/" + _SEGMENT + rb")*/?)?"
_QUERY = rb"(?:[-._~A-Za-z0-9!$&'()*+,;=:@/?]|%[0-9A-F]{2})*"


_REQUEST_LINE = rb"^([A-Z]+)[ \t]+(\*|" + _PATH + rb")"
_REQUEST_LINE += rb"(?:\?(" + _QUERY + rb"))?[ \t]+HTTP/(\d+\.\d+)$"
REQUEST_LINE_REGEX = re.compile(_REQUEST_LINE)


class ConnectionLogger(logging.LoggerAdapter):
    def __init__(self, logger, peername):
        super().__init__(logger, {"peername": peername})

    def process(self, msg, kwargs):
        tmp = "@{0[0]}:{0[1]}\n{1}".format(self.extra["peername"], msg)
        return tmp, kwargs


class Http1Connection(AbstractConnection):
    """Handles HTTP/1.x connections."""

    def __init__(self, manager, reader, writer, peername):
        super().__init__(manager, reader, writer, peername)
        self.logger = ConnectionLogger(_LOGGER, peername)
        self.transport = Http1Transport(self)

    @coroutine
    def run(self):
        """Start listening to requests, using a pipelined transport."""

        self.logger.info("connection ready")

        while self.transport.keep_alive:
            try:
                yield from self.transport.run()
            except ConnectionError:
                break

        self.logger.info("connection closing")
        self.close()


class Http1Transport(AbstractTransport):
    """This transport implements functions for receiving HTTp requests
    and send HTTP responses.
    """

    def __init__(self, connection, timeout=90):
        super().__init__(connection)
        self.timeout = timeout
        self.keep_alive = True
        self.client_version = "1.0"

    def create_body_reader(self):
        """Create the current request body reader, based on its header
        fields informations.

        A request with a content-length header field will be given a
        ``BufferedBodyReader``, and a request with a chunked
        transfert-encoding will retuns a ``ChunkedBodyReader``.
        """
        headers = self.request.headers

        transfert_encoding = headers.get("transfert-encoding", [])
        content_length = headers.get("content-length", [])

        assert "chunked" in transfert_encoding or content_length

        if content_length:
            assert len(content_length) == 1

            body_size = int(content_length[0])
            body_reader = BufferedBodyReader(self.reader, body_size)

        elif "chunked" in transfert_encoding:
            assert transfert_encoding[-1] == "chunked"

            if transfert_encoding[:-1]:
                raise NotImplementedError
            
            body_reader = ChunkedBodyReader(self.reader)

        return body_reader

    @coroutine
    def send_response(self, status, headers=None, body=None):
        """Send an HTTP response.

        This function will add the date, server and connection header
        fields to the given hedaer fields.
        """
        assert self.response is None

        tmp = ("HTTP/1.1", str(status), HTTP_STATUSES[status])
        tmp = (s.encode("ascii") for s in tmp)
        status_line = b" ".join(tmp) + b"\r\n"

        response_headers = Headers(
            date=datetime.utcnow(),
            server=self.connection.manager.server_agent,
            connection="keep-alive" if self.keep_alive else "close"
        )

        if headers is not None:
            response_headers.update(headers)

        if body is None:
            response_headers.set("content-length", 0)
        elif isinstance(body, bytes):
            response_headers.set("content-length", len(body))
        else:
            raise Exception("body must be bytes or None")

        response_header = bytearray(status_line)

        for field in response_headers.fields():
            tmp = (s.encode("ascii") for s in field)
            tmp = b": ".join(tmp)
            response_header.extend(tmp)
            response_header.extend(b"\r\n")

        response_header.extend(b"\r\n")

        self.logger.debug(response_header.decode("ascii"))

        self.writer.write(response_header)

        if body is not None:
            self.writer.write(body)

        yield from self.writer.drain()

        if status >= 200:
            self.response = Response(status, response_headers)

    @coroutine
    def send_error(self, code, headers=None, **kwargs):
        """Shortcut used to send HTTP errors to the client."""
        assert 400 <= code < 600
        yield from self.send_response(code, headers)

    @coroutine
    def run(self):
        """Receive a request, then send an appropriate response.

        This coroutine will returns when the request processing is
        over.
        """
        self.request = None
        self.body_reader = None
        self.handler = None
        self.response = None
        self.error = None

        try:
            self.request = yield from self.receive_request()
            self.body_reader = self.create_body_reader()
            yield from self.handle_request()

        except EOFError:
            self.keep_alive = False

        except HttpError as error:
            self.error = error
            yield from self.send_response(error.code, error.headers)

        except ConnectionError:
            self.logger.exception("connection error occurred")
            raise

        except Exception:
            self.logger.exception("unexpected error occurred")
            self.error = HttpError(500)
            yield from self.send_error(500)

        finally:
            yield from self.cleanup()

    @coroutine
    def receive_request(self):
        """Coroutine called by ``run`` in order the receive and parse
        a new request.
        """
        tmp = self.reader.read_until(b"\r\n\r\n")
        tmp = asyncio.wait_for(tmp, self.timeout)

        try:
            header = yield from tmp
        except asyncio.TimeoutError:
            self.keep_alive = False
            raise HttpError(408)

        request_line, *headers_lines = header.split(b"\r\n")

        if not request_line:
            # No request line = EOF, so we close the connection
            self.logger.info("no request line, at EOF")
            raise EOFError

        self.logger.debug(request_line)

        #----------------------#
        # Request line parsing #
        #----------------------#

        match = REQUEST_LINE_REGEX.match(request_line)

        # A request is well formed if and only if:
        # - the version string is in the form "HTTP/1.1"
        # - the url match the format defined in RFC 3986
        # - for security reasons, the percent encoded values for "/" and
        #  "\", respectively %2F and %5C cannot be present in the url.
        is_malformed = (
            not match
            or (b"%2F" in request_line or b"%5C" in request_line)
        )

        if is_malformed:
            # Bad request
            self.logger.info("request line malformed")
            raise HttpError(400)

        self.logger.debug(match.groups(b""))

        tmp = (s.decode("ascii") for s in match.groups(b""))
        method, path, query, version = tmp

        path = unquote_plus(path)

        if query:
            try:
                query = parse_qs(query, strict_parsing=True)
            except ValueError:
                # malformed - ignore the query
                self.logger.info("malformed query")
                query = {}
        else:
            query = {}

        # upgrade client_version if needed
        if self.client_version < version:
            self.client_version = version
            self.logger.info("client version set to %s", version)

        request = Request(method, path, query)

        #-----------------------#
        # Header fields parsing #
        #-----------------------#

        try:
            request.headers.parse_lines(headers_lines)
        except HeaderParseError as error:
            # Bad request
            self.logger.info("malformed header field")
            raise HttpError(400)

        self.logger.debug(request.headers)

        #-------------------------------------#
        # Body length and encoding validation #
        #-------------------------------------#

        transfert_encoding = request.headers.get("transfert-encoding", [])
        content_length = request.headers.get("content-length", [])

        if transfert_encoding:
            if content_length:
                msg = "transfert-encoding and content-length headers present"
                self.logger.info(msg)
                del request.headers["content-length"]
            
            if transfert_encoding[-1] != "chunked":
                self.logger.info("chunked is not the final encoding")
                self.keep_alive = False
                raise HttpError(400)
            
        
        elif content_length:
            if len(content_length) > 1:
                self.logger.info("multiple content-length headers")
                self.keep_alive = False
                raise HttpError(400)

            if not re.match(r"^[1-9][0-9]*$", content_length[0]):
                self.logger.info("malformed content-length value")
                self.keep_alive = False
                raise HttpError(400)

        else:
            request.headers.set("content-length", 0)

        #-----------------------------#
        # Connection keep alive check #
        #-----------------------------#

        connection = request.headers.get("connection", [])

        self.keep_alive = (
            version == "1.1" and "close" not in connection
            or version == "1.0" and "keep_alive" in connection
        )

        return request

    @coroutine
    def handle_request(self):
        """This coroutine, called by ``run``, will route the request
        to the associated  request handler.
        """
        #-----------------#
        # Request routing #
        #-----------------#

        method = self.request.method

        try:
            tmp = self.router.find_route(self.request.path)
            request_handler_factory, args, kwargs = tmp
        except RoutingError as error:
            # No route finded, send 404 not find error
            self.logger.info("route not find")
            raise HttpError(404)

        if method.lower() not in request_handler_factory.methods:
            # Method not implemented, send error 405 not implemented
            self.logger.info("method not implemented")
            error_headers = Headers(allowed=request_handler_factory.methods)
            raise HttpError(405, error_headers)

        #-------------------------#
        # Request handler calling #
        #-------------------------#

        self.handler = request_handler_factory(self)
        method_handler = getattr(self.handler, method.lower())

        can_continue = yield from self.handler.can_continue()

        if not can_continue:
            if "100-continue" in self.request.headers.get("except", []):
                if not self.response:
                    # if the can_continue method does not send an error,
                    # send a 417 error.
                    self.logger.info("expectation failed")
                    raise HttpError(417)

        if "100-continue" in self.request.headers.get("except", []):
            yield from self.handler.send_response(100)


        tmp = method_handler(*args, **kwargs)
        yield from self.loop.create_task(tmp)

    @coroutine
    def cleanup(self):
        """Cleanup the transport after each exchange.

        Request body not completely read will be read into devnull.
        """
        if self.body_reader and not self.body_reader.is_complete:
            with open(os.devnull, "wb") as null:
                yield from self.body_reader.read_into(null)
