import asyncio
import functools
import os

from asyncio import coroutine
from asyncioplus.iofile import File
from copy import deepcopy

from centimani.headers import Headers
from centimani.utils import HTTP_METHODS


class Request:
    def __init__(self, method="GET", path="/", query={}):
        self.method = method
        self.path = path
        self.query = query
        self.headers = Headers()


class Response:
    def __init__(self, status, headers = None):
        self.status = status
        self.headers = deepcopy(headers) if headers else Headers()


#====================#
# Connection handler #
#====================#

class AbstractConnectionHandler:
    def __init__(self, dispatcher, reader, writer, peername, loop = None):
        self._loop = loop or asyncio.get_event_loop()
        self.dispatcher = dispatcher
        self.reader = reader
        self.writer = writer
        self.peername = peername

        self.current_handler = None

        # self.logger.info("peer connected")

    @coroutine
    def read_body(self, handler, stream):
        raise NotImplementedError

    @coroutine
    def send_response(self, handler, status, headers = None, body = None, body_producer = None):
        raise NotImplementedError

    @coroutine
    def send_error(self, status, headers = None, request = None, **kwargs):
        raise NotImplementedError

    @coroutine
    def cleanup(self):
        raise not NotImplementedError

    def close(self):
        # self.logger.info("closing connection")
        self.writer.close()

    @coroutine
    def run(self):
        raise NotImplementedError


#===================#
# Response handlers #
#===================#

class AbstractResponseHandler:
    def __init__(self, connection_handler, request = None):
        assert isinstance(connection_handler, AbstractConnectionHandler)

        self.connection_handler = connection_handler
        self.request = request

        self.bytes_read = 0
        self.bytes_sent = 0

        self.is_body_read = False
        self.is_body_sent = False

    def read_body(self, stream):
        return self.connection_handler.read_body(self, stream)

    def send_response(self, status, headers = None, body = None, body_producer = None):
        return self.connection_handler.send_response(self, status, headers, body, body_producer)

    def send_error(self, status, headers = None, request = None, **kwargs):
        return self.connection_handler.send_error(status, headers, request, **kwargs)

    @coroutine
    def cleanup(self):
        if self.request and not self.is_body_read:
            null_writer = File.open(os.devnull, "wb")
            yield from self.read_body(stream = null_writer)

    
class ErrorResponseHandler(AbstractResponseHandler):
    @coroutine
    def error(self, status, headers = None, **kwargs):
        yield from self.send_response(status, headers)


class MetaResponseHandler(type):
    """
    Metaclass for all user-defined request handlers.

    Populate the methods attribute of the request handler in order to easyly
    acces to handler's allowed methods.
    """
    def __init__(cls, name, bases, namespace):
        methods = set()

        for method in HTTP_METHODS:
            method_handler = getattr(cls, method.lower(), None)
            if method_handler and asyncio.iscoroutinefunction(method_handler):
                methods.add(method.lower())

        cls.methods = frozenset(methods)


class ResponseHandler(AbstractResponseHandler, metaclass = MetaResponseHandler):
    def __init__(self, connection_handler, request):
        super().__init__(connection_handler, request)
