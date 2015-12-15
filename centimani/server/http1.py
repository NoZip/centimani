import asyncio
import logging
import os
import re

from datetime import datetime
from urllib.parse import unquote_plus, parse_qs

from centimani.errors import HttpError
from centimani.headers import Headers, HeaderParseError
from centimani.streamutils import BufferedBodyReader, ChunkedBodyReader
from centimani.utils import HTTP_STATUSES, SUPPORTED_METHODS
from .router import RoutingError
from .handlers import Request, Response
from .handlers import Connection, ProtocolHandler


_LOGGER = logging.getLogger(__name__)

_SEGMENT = rb"(?:[-._~A-Za-z0-9!$&'()*+,;=:@]|%[0-9A-F]{2})+"
_PATH = rb"/(?:" + _SEGMENT + rb"(?:/" + _SEGMENT + rb")*/?)?"
_QUERY = rb"(?:[-._~A-Za-z0-9!$&'()*+,;=:@/?]|%[0-9A-F]{2})*"

_REQUEST_LINE = rb"^([A-Z]+)[ \t]+(\*|" + _PATH + rb")"
_REQUEST_LINE += rb"(?:\?(" + _QUERY + rb"))?[ \t]+HTTP/(\d+\.\d+)$"
REQUEST_LINE_REGEX = re.compile(_REQUEST_LINE)


class Http1Connection(Connection):
    """Handles HTTP/1.x connections."""

    def __init__(self, server, reader, writer, peername):
        super().__init__(server, reader, writer, peername, _LOGGER)
        self._pipeline = Http1Pipeline(self)

    async def listen(self):
        """Start listening to requests, using a pipeline."""

        self._logger.info("connection ready")

        while self._pipeline.keep_alive:
            try:
                await self._pipeline.process_request()
            except ConnectionError:
                break

        self._logger.info("connection closing")
        if not self._writer.is_closing():
            self.close()


class Http1Pipeline(ProtocolHandler):
    """This transport implements functions for receiving HTTP requests
    and send HTTP responses.
    """

    def __init__(self, connection, timeout=60):
        super().__init__(connection)
        self._timeout = timeout
        self._keep_alive = True
        self._client_version = "1.0"

    @property
    def timeout(self):
        return self._timeout

    @property
    def keep_alive(self):
        return self._keep_alive

    @property
    def client_version(self):
        return self._client_version

    def _create_body_reader(self):
        """Create the current request body reader, based on its header
        fields informations.

        A request with a content-length header field will be given a
        ``BufferedBodyReader``, and a request with a chunked
        transfert-encoding will retuns a ``ChunkedBodyReader``.
        """
        headers = self._request.headers

        transfert_encoding = headers.get("transfert-encoding", [])
        content_length = headers.get("content-length", [])

        assert "chunked" in transfert_encoding or content_length

        if content_length:
            assert len(content_length) == 1

            body_size = int(content_length[0])
            body_reader = BufferedBodyReader(self._reader, body_size)

        elif "chunked" in transfert_encoding:
            assert transfert_encoding[-1] == "chunked"

            if transfert_encoding[:-1]:
                raise NotImplementedError

            body_reader = ChunkedBodyReader(self.reader)

        return body_reader

    async def send_response(self, status, headers=None, body=None):
        """Send an HTTP response.

        This function will add the date, server and connection header
        fields to the given hedaer fields.
        """
        assert self._response is None

        reason = HTTP_STATUSES[status]
        status_line = "HTTP/1.1 {0} {1}\r\n".format(status, reason)

        # User defined "connection" header for closing connection
        # after response.
        if headers:
            if self._keep_alive and "close" in headers.pop("connection", []):
                self._keep_alive = False

        response_headers = Headers(
            date=datetime.utcnow(),
            server=self._server.server_agent,
            connection="keep-alive" if self._keep_alive else "close"
        )

        if headers is not None:
            response_headers.update(headers)

        if body is None:
            response_headers.set("content-length", 0)
        elif isinstance(body, bytes):
            response_headers.set("content-length", len(body))
        else:
            raise Exception("body must be bytes or None")

        response_header = bytearray(status_line.encode("ascii"))

        for name, content in response_headers.fields():
            field_line = "{0}: {1}\r\n".format(name.title(), content)
            response_header.extend(field_line.encode("ascii"))

        # HTTP header trailing blank line
        response_header.extend(b"\r\n")

        self._logger.debug(response_header.decode("ascii"))

        self._writer.write(response_header)

        if body is not None:
            self._writer.write(body)

        await self._writer.drain()

        if status >= 200:
            self._response = Response(status, response_headers)

    async def process_request(self):
        """Receive a request, then send an appropriate response.

        This coroutine will returns when the request processing is
        over.
        """
        self._request = None
        self._body_reader = None
        self._handler = None
        self._response = None
        self._error = None

        try:
            self._request = await self._receive_request()
            self._body_reader = self._create_body_reader()
            await self._handle_request()

        except EOFError:
            self._keep_alive = False

        except HttpError as error:
            self._error = error
            await self.send_response(error.code, error.headers)

        except ConnectionError:
            self._logger.exception("connection error occurred")
            raise

        except Exception:
            self._logger.exception("unexpected error occurred")
            self._error = HttpError(500)
            await self.send_error(500)

        finally:
            if self._keep_alive:
                await self.cleanup()

    async def _receive_request(self):
        """Coroutine called by ``run`` in order the receive and parse
        a new request.
        """
        tmp = self._reader.read_until(b"\r\n\r\n")

        try:
            header = await asyncio.wait_for(tmp, self._timeout)
        except asyncio.TimeoutError as error:
            self._keep_alive = False
            raise HttpError(408) from error

        request_line, *headers_lines = header.split(b"\r\n")

        if not request_line:
            # No request line = EOF, so we close the connection
            self._logger.info("no request line, at EOF")
            raise EOFError

        self._logger.debug(request_line)

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
            self._logger.info("request line malformed")
            raise HttpError(400)

        self._logger.debug(match.groups(b""))

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
        if self._client_version < version:
            self._client_version = version
            self._logger.info("client version set to %s", version)

        request = Request(method, path, query)

        #-----------------------#
        # Header fields parsing #
        #-----------------------#

        try:
            request.headers.parse_lines(headers_lines)
        except HeaderParseError as error:
            # Bad request
            self._logger.info("malformed header field")
            raise HttpError(400)

        self._logger.debug(request.headers)

        #-------------------------------------#
        # Body length and encoding validation #
        #-------------------------------------#

        transfert_encoding = request.headers.get("transfert-encoding", [])
        content_length = request.headers.get("content-length", [])

        if transfert_encoding:
            if content_length:
                msg = "transfert-encoding and content-length headers present"
                self._logger.info(msg)
                del request.headers["content-length"]

            if transfert_encoding[-1] != "chunked":
                self._logger.info("chunked is not the final encoding")
                self._keep_alive = False
                raise HttpError(400)


        elif content_length:
            if len(content_length) > 1:
                self._logger.info("multiple content-length headers")
                self._keep_alive = False
                raise HttpError(400)

            if not re.match(r"^[1-9][0-9]*$", content_length[0]):
                self._logger.info("malformed content-length value")
                self._keep_alive = False
                raise HttpError(400)

        else:
            request.headers.set("content-length", 0)

        #-----------------------------#
        # Connection keep alive check #
        #-----------------------------#

        connection = request.headers.get("connection", [])

        self._keep_alive = (
            version == "1.1" and "close" not in connection
            or version == "1.0" and "keep_alive" in connection
        )

        return request

    async def _handle_request(self):
        """This coroutine, called by ``run``, will route the request
        to the associated  request handler.
        """
        #-----------------#
        # Request routing #
        #-----------------#

        method = self._request.method

        try:
            tmp = self._server.router.find_route(self._request.path)
            request_handler_factory, args, kwargs = tmp
        except RoutingError as error:
            # No route finded, send 404 not find error
            self._logger.info("route not find")
            raise HttpError(404) from error

        allowed_methods = request_handler_factory.allowed_methods()
        if method not in allowed_methods:
            # Method not implemented, send error 405 not implemented
            self._logger.info("method not implemented")
            error_headers = Headers(allowed=allowed_methods)
            raise HttpError(405, error_headers)

        #-------------------------#
        # Request handler calling #
        #-------------------------#

        self._handler = request_handler_factory(self)
        method_handler = getattr(self._handler, method.lower())

        can_continue = await self.handler.can_continue()

        if not can_continue:
            if "100-continue" in self._request.headers.get("except", []):
                if not self._response:
                    # if the can_continue method does not send an error,
                    # send a 417 error.
                    self._logger.info("expectation failed")
                    raise HttpError(417)

        if "100-continue" in self._request.headers.get("except", []):
            await self._handler.send_response(100)

        tmp = method_handler(*args, **kwargs)
        await self._loop.create_task(tmp)

    async def cleanup(self):
        """Cleanup the transport after each exchange.

        Request body not completely read will be read into devnull.
        """
        if self._body_reader and not self._body_reader.is_complete:
            with open(os.devnull, "wb") as null:
                async for data in self._body_reader:
                    null.write(data)
