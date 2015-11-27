import asyncio

from asyncio import coroutine
from copy import deepcopy

from centimani.headers import Headers
from centimani.utils import SUPPORTED_METHODS


class Request:
    __slots__ = ("method", "path", "query", "headers")

    def __init__(self, method="GET", path="/", query=None, headers=None):
        self.method = method
        self.path = path
        self.query = query or {}
        self.headers = headers or Headers()

    def __repr__(self):
        fields = (
            "{0}: {1!r}".format(name, getattr(self, name))
            for name in self.__slots__
        )
        return "".join(("Request(", ", ".join(fields), ")"))


class Response:
    __slots__ = ("status", "headers")

    def __init__(self, status, headers=None):
        self.status = status
        self.headers = Headers()
        self.headers.update(headers)

    def __repr__(self):
        fields = (
            "{0}: {1!r}".format(name, getattr(self, name))
            for name in self.__slots__
        )
        return "".join(("Response(", ", ".join(fields), ")"))


class AbstractConnection:
    def __init__(self, manager, reader, writer, peername):
        self.manager = manager
        self.reader = reader
        self.writer = writer
        self.peername = peername

    @property
    def loop(self):
        return self.manager.loop

    @property
    def router(self):
        return self.manager.router

    @coroutine
    def run(self):
        raise NotImplementedError

    def close(self):
        self.writer.close()


class AbstractTransport:
    def __init__(self, connection):
        self.connection = connection
        
        self.request = None
        self.body_reader = None
        self.handler = None
        self.response = None
        self.error = None

    @property
    def loop(self):
        return self.connection.loop

    @property
    def logger(self):
        return self.connection.logger

    @property
    def reader(self):
        return self.connection.reader
    
    @property
    def writer(self):
        return self.connection.writer

    @property
    def router(self):
        return self.connection.router
    
    def rethrow(self):
        raise self.error

    def create_body_reader(self, request):
        raise NotImplementedError

    @coroutine
    def send_response(self, status, headers=None, body=None):
        raise NotImplementedError

    @coroutine
    def send_error(self, status, headers=None, **kwargs):
        raise NotImplementedError

    @coroutine
    def run(self):
        raise NotImplementedError

    @coroutine
    def receive_request(self):
        raise NotImplementedError

    @coroutine
    def handle_request(self):
        raise NotImplementedError

    @coroutine
    def cleanup(self):
        raise NotImplementedError


#=================#
# Request handler #
#=================#

class MetaRequestHandler(type):
    """
    Metaclass for all user-defined request handlers.

    Populate the methods attribute of the request handler in order to easyly
    acces to handler's allowed methods.
    """
    def __init__(cls, name, bases, namespace):
        methods = set()

        for method in SUPPORTED_METHODS:
            method_handler = getattr(cls, method.lower(), None)
            if method_handler and asyncio.iscoroutinefunction(method_handler):
                methods.add(method.lower())

        cls.methods = frozenset(methods)


class RequestHandler(metaclass=MetaRequestHandler):
    def __init__(self, transport):
        self.transport = transport
        self.request = transport.request
        self.body_reader = transport.body_reader

    def send_response(self, status, headers=None, body=None):
        assert self.request is self.transport.request

        if isinstance(body, str):
            body = body.encode("utf-8")

        return self.transport.send_response(status, headers, body)

    def send_error(self, status, headers=None, **kwargs):
        assert self.request is self.transport.request
        return self.transport.send_error(status, headers, **kwargs)

    @coroutine
    def can_continue(self):
        return True
