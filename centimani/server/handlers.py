import asyncio
import functools
import os

from asyncio import coroutine
from asyncioplus.iofile import File
from copy import deepcopy

from centimani.headers import Headers
from centimani.utils import HTTP_METHODS


class Request:
    __slots__ = ("method", "path", "query", "headers")

    def __init__(self, method="GET", path="/", query={}):
        self.method = method
        self.path = path
        self.query = query
        self.headers = Headers()

    def __repr__(self):
        fields = ("{0}: {1!r}".format(name, getattr(self, name)) for name in self.__slots__)
        return "".join(("Request(", ", ".join(fields), ")"))


class Response:
    __slots__ = ("status", "headers")

    def __init__(self, status, headers = None):
        self.status = status
        self.headers = deepcopy(headers) if headers else Headers()

    def __repr__(self):
        fields = ("{0}: {1!r}".format(name, getattr(self, name)) for name in self.__slots__)
        return "".join(("Response(", ", ".join(fields), ")"))


#====================#
# Connection handler #
#====================#

class AbstractConnection:
    def __init__(self, dispatcher, reader, writer, peername, loop = None):
        self._loop = loop or asyncio.get_event_loop()
        self.dispatcher = dispatcher
        self.reader = reader
        self.writer = writer
        self.peername = peername

    @coroutine
    def run():
        raise NotImplementedError

    def close(self):
        # self.logger.info("closing connection")
        del self.dispatcher.connections[peername]
        self.writer.close()


#=================#
# Request handler #
#=================#

class AbstractRequestHandler:
    def create_body_reader(self, handler):
        raise NotImplementedError

    @coroutine
    def handle_request():
        raise NotImplementedError

    @coroutine
    def send_response(self, status, headers = None, body = None, body_producer = None):
        raise NotImplementedError

    @coroutine
    def send_error(self, status, headers = None, request = None, **kwargs):
        raise NotImplementedError

    @coroutine
    def cleanup(self):
        raise not NotImplementedError


#===================#
# Response handlers #
#===================#

class AbstractResponseHandler:
    def __init__(self, request_handler, request = None):
        self.request_handler = request_handler
        self.request = request

        self._body_reader = None

        self.is_response_sent = False

    def body_reader(self):
        if self._body_reader is None:
            self._body_reader = self.request_handler.create_body_reader(self)
        
        return self._body_reader

    @coroutine
    def can_continue(self):
        return True

    def send_response(self, status, headers = None, body = None, body_producer = None):
        assert not self.is_response_sent

        if status >= 200:
            self.is_response_sent = True

        return self.request_handler.send_response(status, headers, body, body_producer)

    def send_error(self, status, headers = None, **kwargs):
        assert not self.is_response_sent

        self.is_response_sent = True
        return self.request_handler.send_error(status, headers, self.request, **kwargs)

    @coroutine
    def cleanup(self):
        if self.request:
            null_writer = File.open(os.devnull, "wb")
            body_reader = self.body_reader()
            yield from body_reader.read_into(null_writer)

    
class ErrorResponseHandler(AbstractResponseHandler):
    @coroutine
    def error(self, status, headers = None, **kwargs):
        yield from self.request_handler.send_response(status, headers)


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
    def __init__(self, request_handler, request):
        super().__init__(request_handler, request)
