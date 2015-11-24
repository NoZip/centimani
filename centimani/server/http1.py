import asyncio
import os
import logging
import re
import sys

from asyncio import coroutine
from copy import deepcopy
from datetime import datetime
from urllib.parse import unquote_plus, parse_qs

import centimani.log

from centimani.headers import Headers, HeaderParseError
from centimani.log import MaxLevelFilter
from centimani.server.dispatcher import RoutingError
from centimani.server.handlers import AbstractConnection, AbstractRequestHandler, AbstractResponseHandler, Request
from centimani.streamutils import BufferedBodyReader, ChunkedBodyReader
from centimani.utils import HTTP_STATUSES, HTTP_METHODS

if "StopAsyncIteration" not in dir(__builtins__):
    from asyncioplus.utils import StopAsyncIteration

_LOGGER = logging.getLogger(__name__)

_SEGMENT = rb"(?:[-._~A-Za-z0-9!$&'()*+,;=:@]|%[0-9A-F]{2})+"
_PATH = rb"/(?:" + _SEGMENT + rb"(?:/" + _SEGMENT + rb")*/?)?"
_QUERY = rb"(?:[-._~A-Za-z0-9!$&'()*+,;=:@/?]|%[0-9A-F]{2})*"


_REQUEST_LINE = rb"^([A-Z]+)[ \t]+(\*|" + _PATH + rb")(?:\?(" + _QUERY + rb"))?[ \t]+HTTP/(\d+\.\d+)$"
REQUEST_LINE_REGEX = re.compile(_REQUEST_LINE)


class ConnectionLogger(logging.LoggerAdapter):
    def __init__(self, logger, peername):
        super().__init__(logger, {"peername": peername})

    def process(self, msg, kwargs):
        return "@{0[0]}:{0[1]}\n{1}".format(self.extra["peername"], msg), kwargs


class ConnectionHandler(AbstractConnection, AbstractRequestHandler):
    def __init__(self, dispatcher, reader, writer, peername, request_timeout=60, loop=None):
        super().__init__(dispatcher, reader, writer, peername, loop=loop)
        self.request_timeout = request_timeout
        self.logger = ConnectionLogger(_LOGGER, peername)
        self.current_handler = None
        self.client_version = "1.0"
        self.keep_alive = True

    def create_body_reader(self, handler):
        assert isinstance(handler, AbstractResponseHandler)

        headers = handler.request.headers
        transfert_encoding = headers.get("transfert-encoding", [])
        content_length = headers.get("content-length", [])

        assert "chunked" in transfert_encoding or content_length

        if content_length:
            assert(len(content_length) == 1)

            body_size = int(content_length[0])
            body_reader = BufferedBodyReader(self.reader, body_size)

        elif "chunked" in transfert_encoding:
            assert transfert_encoding[-1] == "chunked"

            if transfert_encoding[:-1]:
                raise NotImplementedError
            
            body_reader = ChunkedBodyReader(self.reader)

        return body_reader

    @coroutine
    def send_response(self, status, headers=None, body=None, body_producer=None):
        assert status in HTTP_STATUSES

        status_line = "HTTP/{!s} {:d} {!s}\r\n".format(
            self.client_version,
            status,
            HTTP_STATUSES[status]
        )

        headers_addons = Headers(
            date = datetime.utcnow(),
            server = self.dispatcher.server_agent,
            connection = "keep-alive" if self.keep_alive else "close"
        )

        headers = headers if headers else Headers()
        headers.update(headers_addons)

        if body:
            headers.set("content-length", len(body))

        elif body_producer:
            raise NotImplementedError

            # if body_producer.has_size:
            #     headers.set("content-length", body_producer.size)
            # else:
            #     headers.set("transfert-encoding", "chunked")

        else:
            headers.set("content-length", 0)

        header = status_line + headers.http_encode() + "\r\n"
        self.writer.write(header.encode("ascii"))

        if body:
            self.writer.write(body)
        # elif body_producer:
        #     pass

        yield from self.writer.drain()

        self.logger.debug("response sent:\n{0}", header)

    @coroutine
    def send_error(self, status, headers=None, request=None, **kwargs):
        assert status >= 400
        assert status in HTTP_STATUSES

        ErrorHandler = self.dispatcher.error_handler_factory
        handler = ErrorHandler(self, request)
        self.current_handler = handler

        tmp = handler.error(status, headers, **kwargs)
        yield from self._loop.create_task(tmp)

    @coroutine
    def cleanup(self):
        if self.current_handler:
            yield from self.current_handler.cleanup()

    @coroutine
    def run(self):
        self.logger.info("connection ready")

        self.keep_alive = True
        while self.keep_alive:
            yield from self.handle_request()

            if self.keep_alive:
                yield from self.cleanup()

            #TODO: upgrade protocol

        self.logger.info("closing connection")
        self.close()

    @coroutine
    def handle_request(self):
        #-----------------#
        # Receive request #
        #-----------------#

        try:
            read_coroutine = self.reader.read_until(b"\r\n\r\n")
            header = yield from asyncio.wait_for(read_coroutine, self.request_timeout)
        except asyncio.TimeoutError as error:
            # After request timeout, send an error response then close the connection
            self.logger.info("request waiting timeout")
            self.keep_alive = False
            yield from self.send_error(408)
            return
        except ConnectionError:
            self.keep_alive = False
            self.logger.exception("connection error during request waiting")
            return

        request_line, *headers_lines = header.split(b"\r\n")

        #-------------------------#
        # Request line processing #
        #-------------------------#

        if not request_line:
            # No request line = EOF, so we close the connection
            self.logger.info("no request line, at EOF")
            self.keep_alive = False
            return

        self.logger.debug(request_line)

        match = REQUEST_LINE_REGEX.match(request_line)
        
        # A request is well formed if and only if:
        # - the method is a valid HTTP method
        # - the version string is in the form "HTTP/1.1"
        # - the url match the format defined in RFC 3986
        # - for security reasons, the percent encoded values for "/" and
        #  "\", respectively %2F and %5C cannot be present in the url.
        is_malformed = (
            not match
            or (b"%2F" in request_line or b"%5C" in request_line)
        )

        if is_malformed:
            # Bad request, connection is closed after error is send
            self.logger.info("request line malformed")
            self.keep_alive = False
            yield from self.send_error(400)
            return

        self.logger.debug(match.groups(b""))
        tmp = (s.decode("ascii") for s in match.groups(b""))
        method, path, query, version = tmp

        if query:
            try:
                query = parse_qs(query, strict_parsing=True)
            except ValueError:
                # malformed - ignore the query
                self.logger.info("malformed query")
                query = {}
        else:
            query = {}

        request = Request(method, path, query)

        self.logger.debug(request)

        # upgrade client_version if needed
        if self.client_version < version:
            self.client_version = version
            self.logger.info("client version set to {0}", version)

        #-----------------------#
        # Parsing header fields #
        #-----------------------#

        try:
            request.headers.parse_lines(headers_lines)
        except HeaderParseError as error:
            # Bad request, connection is closed after error is send
            self.logger.info("malformed header field {!s}", error)
            self.keep_alive = False
            yield from self.send_error(400)
            return

        self.logger.debug(request.headers)

        #-------------------------------------#
        # Body length and encoding validation #
        #-------------------------------------#

        transfert_encoding = request.headers.get("transfert-encoding", [])
        content_length = request.headers.get("content-length", [])

        if transfert_encoding:
            if content_length:
                self.info("transfert-encoding and content-length headers present")
                del request.headers["content-length"]
            
            if transfert_encoding[-1] != "chunked":
                self.keep_alive = False
                yield from self.send_error(400)
                return
            
        
        elif content_length:
            if len(content_length) > 1:
                self.logger.info("multiple content-length headers")
                self.keep_alive = False
                yield from self.send_error(400)
                return

            if not re.match(r"^[1-9][0-9]*$", content_length[0]):
                self.logger.info("malformed content-length value")
                self.keep_alive = False
                yield from self.send_error(400)
                return

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

        #-----------------#
        # Request routing #
        #-----------------#

        try:
            tmp = self.dispatcher.find_route(request.path)
            request_handler_factory, args, kwargs = tmp
        except RoutingError as error:
            # No route finded, send 404 not find error
            self.logger.info("route not find")
            yield from self.send_error(404, request=request)
            return

        if method.lower() not in request_handler_factory.methods:
            # Method not implemented, send error 405 not implemented
            self.logger.info("method not implemented")
            error_headers = Headers(allowed=request_handler_factory.methods)
            yield from self.send_error(405, error_headers, request=request)
            return

        #-------------------------#
        # Request handler calling #
        #-------------------------#

        handler = request_handler_factory(self, request)
        method_handler = getattr(handler, method.lower())

        can_continue = yield from handler.can_continue()

        if not can_continue:
            if "100-continue" in request.headers.get("except", []):
                self.keep_alive = False
                if not handler.is_response_sent:
                    yield from handler.send_error(417)
            return

        if "100-continue" in request.headers.get("except", []):
            yield from handler.send_response(100)

        try:
            tmp = method_handler(*args, **kwargs)
            yield from self._loop.create_task(tmp)
        except ConnectionError as error:
            self.keep_alive = False
            self.logger.exception("connection error during response handling")
            return
        except Exception as error:
            self.logger.exception("error during response handling")
            yield from self.send_error(500, request=request)
            return
