import re
import asyncio
import logging

from urllib.parse import urlsplit, unquote_plus, parse_qs
from asyncio import coroutine

from centimani.httputils import *

# Handlers

class BaseHandler:
    def __init__(self, dispatcher, request, reader, writer):
        self.dispatcher = dispatcher
        self.request = request
        self.reader = reader
        self.writer = writer

        self.is_body_read = False

    @coroutine
    def read_body(self):
        assert(self.request)
        
        if (not self.is_body_read
            and "Content-Length" in self.request.headers
        ):
            length = self.request.headers.get("Content-Length")
            body = yield from self.reader.read(length)
            return body

    def write_header(self, response):
        assert(response)
        
        status_line = "HTTP/{0} {1} {2}\r\n".format(
            response.version,
            response.status,
            STATUS_REASON[response.status]
        )

        response_headers = response.headers.http_encode()

        data = status_line + response_headers + "\r\n"

        self.dispatcher.logger.debug(data)

        self.writer.write(data.encode("ascii"))

class ErrorHandler(BaseHandler):
    def __init__(self, dispatcher, request, reader, writer):
        super().__init__(dispatcher, request, reader, writer)

    @coroutine
    def error(self, status, headers=HTTPHeaders(), message=""):
        message_bin = message.encode("utf-8")
        
        response = Response(status=status, headers=headers)

        response.headers.add("Content-Length", len(message_bin))

        self.write_header(response)
        self.writer.write(message_bin)


HTTP11_METHODS = frozenset(("get", "head", "post", "options", "connect", "trace", "put", "patch", "delete"))

class MetaRequestHandler(type):
    def __init__(cls, name, bases, namespace):
        methods = set()

        for method in HTTP11_METHODS:
            method_handler = getattr(cls, method, None)
            if method_handler and asyncio.iscoroutinefunction(method_handler):
                methods.add(method)

        cls.methods = frozenset(methods)
        print(name, cls.methods)

class RequestHandler(BaseHandler, metaclass=MetaRequestHandler):
        pass

# Main application

class RoutingError(Exception):
    pass


REQUEST_PATTERN = r"^([A-Z]+) ((?:/|(?:/[\w%+.-]+)+/?)(?:\?[\w%+.-]+=[\w%+.-]+(?:&[\w%+.-]+=[\w%+.-]+)*)?) HTTP/(\d+\.\d+)$"
REQUEST_REGEX = re.compile(REQUEST_PATTERN)

class Dispatcher:

    def __init__(self, routes, loop=None, logger=None):
        self.routes = routes
        self.error_handler_factory = ErrorHandler
        self.loop = loop or asyncio.get_event_loop()
        self.logger = logger or logging.getLogger("centimani.server")

    def find_route(self, path):
        for pattern, request_handler_factory in self.routes:
            match = pattern.match(path)
            if match:
                return request_handler_factory, match.groups(), match.groupdict()

        raise RoutingError("Route not found")

    @coroutine
    def handle_connection(self, reader, writer):
        handler = None
        
        peername = writer.get_extra_info("peername")

        self.logger.debug("peer {0!r} connected".format(peername))

        while True:
            if handler and handler.request and not handler.is_body_read:
                # read previous request's body if not read
                yield from handler.read_body()
            
            try:
                request_line = yield from asyncio.wait_for(reader.readline(), 90)
            except asyncio.TimeoutError as error:
                # After request timeout, send an error response then close the connection
                self.logger.debug("peer {0!r}: Timeout error".format(peername))
                handler = self.error_handler_factory(self, None, reader, writer)
                yield from self.loop.create_task(handler.error(408))
                break
            except ConnectionResetError as error:
                self.logger.debug("peer {0!r}: Connection reset error".format(peername))
                break

            request_line = request_line.decode("ascii").strip()

            if not request_line:
                # No request line = EOF, so we close the connection
                self.logger.debug("peer {0!r}: No request line, at EOF".format(peername))
                break

            self.logger.debug("{0!r} -> {1}".format(peername,request_line))

            match = REQUEST_REGEX.match(request_line)
            
            if match is None:
                # Bad request, connection is closed after error is send
                self.logger.debug("peer {0!r}: Request line not matching".format(peername))
                handler = self.error_handler_factory(self, None, reader, writer)
                yield from self.loop.create_task(handler.error(400))
                break

            request_line = unquote_plus(request_line)

            method, url, version = match.groups()

            url = urlsplit(url)
            path = url.path
            query = parse_qs(url.query)

            # parse headers
            try:
                headers = HTTPHeaders()
                line = yield from reader.readline()
                while (line != b"\r\n"):
                    headers.parse_line(line.decode("ascii").strip())
                    line = yield from reader.readline()
            except ValueError as error:
                # Bad request, connection is closed after error is send
                self.logger.debug("peer {0!r}: Headers parsing failed".format(peername))
                handler = self.error_handler_factory(self, None, reader, writer)
                yield from self.loop.create_task(handler.error(400))
                break

            request = Request(version, method, path, query, headers)

            #Routing
            try:
                request_handler_factory, args, kwargs = self.find_route(path)
            except RoutingError as error:
                # No route finded, send 404 not find error
                self.logger.debug("Route not find: {0}".format(path))
                handler = self.error_handler_factory(self, request, reader, writer)
                yield from self.loop.create_task(handler.error(404))
                continue

            if method.lower() not in request_handler_factory.methods:
                # Method not implemented, send error 405 not implemented
                self.logger.debug("Method {0} not implemented in {1}".format(
                    method.lower(),
                    request_handler_factory.__name__
                ))
                response_headers = HTTPHeaders(allowed=request_handler_factory.methods)
                handler = self.error_handler_factory(self, request, reader, writer)
                yield from self.loop.create_task(handler.error(405, response_headers))
                continue

            handler = request_handler_factory(self, request, reader, writer)
            method_handler = getattr(handler, method.lower())

            try:
                yield from self.loop.create_task(method_handler(*args, **kwargs))
            except Exception as error:
                handler = self.error_handler_factory(self, request, reader, writer)
                yield from self.loop.create_task(handler.error(500))
                raise error
                
            if "Connection" in request.headers and request.headers.get("Connection") == "close":
                self.logger.debug("peer {0!r}: Connection close found".format(peername))
                break

        # Closing connection
        writer.close()
        self.logger.debug("peer {0!r} disconnected".format(peername))

    @coroutine
    def listen(self, host="localhost", port=8080):
        server = yield from asyncio.start_server(
            self.handle_connection,
            host=host,
            port=port,
            loop=self.loop
        )

        return server
