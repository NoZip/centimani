import os
import re
import asyncio
import logging

from datetime import datetime
from urllib.parse import urlsplit, unquote_plus, parse_qs
from asyncio import coroutine
from asyncioplus.iostream import *
from asyncioplus.iofile import *
from asyncioplus.utils import *

from .httputils import *


class Request:
    def __init__(self, method="GET", path="/", query={}, headers=None, version="1.1"):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers or HTTPHeaders()
        self.version = version


class Response:
    def __init__(self, status, headers = None, version = "1.1"):
        self.status = status
        self.headers = headers or HTTPHeaders()
        self.version = version


#==================#
# Handlers classes #
#==================#

class BaseHandler:
    """
    Base handler semantics.
    """

    def __init__(self, dispatcher, reader, writer, request = None):
        self.dispatcher = dispatcher
        self.reader = reader
        self.writer = writer
        self.request = request

        self.is_body_read = False

    @coroutine
    def cleanup(self):
        assert self.request

        if not self.is_body_read:
            file_writer = File.open(os.devnull, "wb")
            yield from self.read_body(stream = file_writer)

    @coroutine
    def read_body(self, stream):
        assert not self.is_body_read

        transfert_encoding = self.request.headers.get("Transfert-Encoding", [])
        content_length = self.request.headers.get("Content-Length", [])

        assert "chunked" in transfert_encoding or content_length

        if content_length:
            assert(len(content_length) == 1)

            body_size = int(content_length[0])

            if body_size > 0:
                body_reader = BlockReaderIterator(self.reader, body_size)

                running = True
                while running:
                    try:
                        block = yield from body_reader.__anext__()
                    except StopAsyncIteration:
                        running = False
                    else:
                        stream.write(block)

        elif "chunked" in transfert_encoding:
            assert transfert_encoding[-1] == "chunked"

            body_size = 0

            chunked_reader = ChunkedTransfertIterator(self.reader)

            if transfert_encoding[:-1]:
                raise NotImplementedError("no support for chunked body decompression")

            running = True
            while running:
                try:
                    chunk = yield from chunked_reader.__anext__()
                except StopAsyncIteration:
                    running = False
                else:
                    stream.write(chunk)

                stream.write(chunk)
                body_size += len(chunk)

            self.request.headers.set("Content-Length", body_size)
            self.request.headers["Transfert-Encoding"].remove("chunked")

        self.is_body_read = True

    def write_header(self, response):
        """
        Send the HTTP header to the client.
        """
        assert isinstance(response, Response)
        
        status_line = "HTTP/{0} {1} {2}\r\n".format(
            response.version,
            response.status,
            STATUS_REASON[response.status]
        )

        headers_addons = HTTPHeaders(
            date = datetime.utcnow(),
            server = self.dispatcher.server_agent
        )

        response.headers.update(headers_addons)
        data = status_line + response.headers.http_encode() + "\r\n"
        self.writer.write(data.encode("ascii"))

        self.dispatcher.logger.debug(data)


class ErrorHandler(BaseHandler):
    """
    Handle errors.

    Can be subclassed to override the error method in order to change error
    handling behavior.
    """

    @coroutine
    def error(self, status, headers = None):
        response = Response(status, headers)
        response.headers.set("Content-Length", 0)

        self.write_header(response)


HTTP11_METHODS = frozenset(("get", "head", "post", "options", "connect", "trace", "put", "patch", "delete"))

class MetaRequestHandler(type):
    """
    Metaclass for all user-defined request handlers.

    Populate the methods attribute of the request handler in order to easyly
    acces to handler's allowed methods.
    """
    def __init__(cls, name, bases, namespace):
        methods = set()

        for method in HTTP11_METHODS:
            method_handler = getattr(cls, method, None)
            if method_handler and asyncio.iscoroutinefunction(method_handler):
                methods.add(method)

        cls.methods = frozenset(methods)


class RequestHandler(BaseHandler, metaclass=MetaRequestHandler):
    """
    User defined request handler, must be subclassed.
    """
    def __init__(self, dispatcher, reader, writer, request):
        super().__init__(dispatcher, reader, writer, request)


#=============================#
# Web server route dispatcher #
#=============================#

class RoutingError(Exception):
    pass


REQUEST_PATTERN = r"^([A-Z]+) ((?:/|(?:/[\w%+.-]+)+/?)(?:\?[\w%+.-]+=[\w%+.-]+(?:&[\w%+.-]+=[\w%+.-]+)*)?) HTTP/(\d+\.\d+)$"
REQUEST_REGEX = re.compile(REQUEST_PATTERN)

class Dispatcher:

    def __init__(self,
        routes,
        error_handler_factory = ErrorHandler,
        server_agent = "Centimani/0.1",
        logger = None,
        loop = None
    ):
        self._routes = routes
        self._error_handler_factory = ErrorHandler
        self._loop = loop or asyncio.get_event_loop()

        self.server_agent = server_agent
        self.logger = logger or logging.getLogger("centimani.server")

    @coroutine
    def send_error(self, reader, writer, status, headers = None, request = None, **kwargs):
        handler = self._error_handler_factory(self, reader, writer, request)
        yield from self._loop.create_task(handler.error(status, headers, **kwargs))

        if request:
            yield from handler.cleanup()

    def _find_route(self, path):
        for pattern, request_handler_factory in self._routes:
            match = pattern.match(path)
            if match:
                return request_handler_factory, match.groups(), match.groupdict()

        raise RoutingError("Route not found")

    @coroutine
    def _handle_request(self, reader, writer, peername):
        assert reader
        assert writer

        #-----------------#
        # Receive request #
        #-----------------#

        try:
            read_coroutine = reader.read_until(b"\r\n")
            request_line = yield from asyncio.wait_for(read_coroutine, 90)
        except asyncio.TimeoutError as error:
            # After request timeout, send an error response then close the connection
            self.logger.debug("{0[0]}:{0[1]} -> Timeout error".format(peername))
            yield from self.send_error(reader, writer, 408)
            return False
        except ConnectionResetError as error:
            self.logger.debug("{0[0]}:{0[1]} -> Connection reset error".format(peername))
            return False

        request_line = request_line.decode("ascii").strip()

        if not request_line:
            # No request line = EOF, so we close the connection
            self.logger.debug("{0[0]}:{0[1]} -> No request line, at EOF".format(peername))
            return False

        self.logger.debug("{0[0]}:{0[1]} -> {1}".format(peername,request_line))

        match = REQUEST_REGEX.match(request_line)
        
        if match is None:
            # Bad request, connection is closed after error is send
            self.logger.debug("{0[0]}:{0[1]} -> Request line not matching".format(peername))
            yield from self.send_error(reader, writer, 400)
            return False

        request_line = unquote_plus(request_line)

        method, url, version = match.groups()

        url = urlsplit(url)
        path = url.path
        query = parse_qs(url.query)

        request = Request(method, path, query, version = version)

        # parse headers
        try:
            lines = yield from reader.read_until(b"\r\n\r\n")
            for line in lines.split(b"\r\n"):
                line = line.decode("ascii").strip()
                name, value = request.headers.parse_line(line)
                request.headers.add(name, value)
        except HeaderParseError as error:
            # Bad request, connection is closed after error is send
            self.logger.debug("{0[0]}:{0[1]} -> Headers parsing failed".format(peername))
            yield from self.send_error(reader, writer, 400)
            return False

        #-------------------------------------#
        # Body length and encoding validation #
        #-------------------------------------#

        transfert_encoding = request.headers.get("Transfert-Encoding", [])
        content_length = request.headers.get("Content-Length", [])

        if transfert_encoding:
            if content_length:
                del request.headers["Content-Length"]
            
            if transfert_encoding[-1] != "chunked":
                self.logger.debug("{0[0]}:{0[1]} -> chunked is not final encoding".format(peername))
                yield from self.send_error(reader, writer, 400)
                return False
            
        
        elif content_length:
            if len(content_length) > 1:
                self.logger.debug("{0[0]}:{0[1]} -> too many Content-Length values".format(peername))
                yield from self.send_error(reader, writer, 400)
                return False

            if not re.match(r"^[1-9][0-9]*$", content_length[0]):
                self.logger.debug("{0[0]}:{0[1]} -> malformed Content-Length value".format(peername))
                yield from self.send_error(reader, writer, 400)
                return False

        else:
            request.headers.set("Content-Length", 0)

        #-----------------#
        # Request routing #
        #-----------------#

        keep_alive = "close" not in request.headers.get("Connection", [])

        try:
            request_handler_factory, args, kwargs = self._find_route(path)
        except RoutingError as error:
            # No route finded, send 404 not find error
            self.logger.debug("{0[0]}:{0[1]} -> route not find: {1}".format(peername, path))
            yield from self.send_error(reader, writer, 404, request = request)
            return keep_alive

        if method.lower() not in request_handler_factory.methods:
            # Method not implemented, send error 405 not implemented
            self.logger.debug("{0[0]}:{0[1]} -> method {1} not implemented in {0}".format(method.lower(), request_handler_factory.__name__ ))

            yield from self.send_error(
                reader, writer,
                405,
                headers = HTTPHeaders(allowed=request_handler_factory.methods),
                request = request
            )

            return keep_alive

        #-------------------------#
        # Request handler calling #
        #-------------------------#

        handler = request_handler_factory(self, reader, writer, request)
        method_handler = getattr(handler, method.lower())

        try:
            yield from self._loop.create_task(method_handler(*args, **kwargs))
        except ConnectionError as error:
            self.logger.debug("{0[0]}:{0[1]} -> error {1!s}".format(peername, error))
            return False
        except Exception as error:
            self.logger.debug("{0[0]}:{0[1]} -> error {1!s}".format(peername, error))
            yield from self.send_error(reader, writer, 500, request = request)
            return keep_alive

        yield from handler.cleanup()

        return keep_alive

    @coroutine
    def _handle_connection(self, reader, writer):
        peername = peername = writer.get_extra_info("peername")
        self.logger.debug("{0[0]}:{0[1]} -> connected".format(peername))

        keep_alive = True
        while keep_alive:
            keep_alive = yield from self._handle_request(reader, writer, peername)

        # Closing connection
        writer.close()
        self.logger.debug("{0[0]}:{0[1]} -> disconnected".format(peername))

    @coroutine
    def listen(self, host="localhost", port=8080):
        """
        Start the dispatcher from listening on given port, binded to given host.
        """

        server = yield from start_server(
            self._handle_connection,
            host = host,
            port = port,
            loop = self._loop
        )

        return server
