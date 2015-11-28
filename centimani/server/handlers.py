import asyncio

from asyncio import coroutine

from centimani.headers import Headers
from centimani.utils import SUPPORTED_METHODS


class Request:
    """Structure used to store server requests."""

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
    """Structure used to store server responses."""

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
    """Interface for connections initiated in ``ConnectionManager``.

    Attributes:
    :manager: The connection manager that have created this connection.
    :reader: The sream for reading data from the remote client.
    :writer: The stream dor sending data to the remote client.
    :peername: A (host, port) tuple associated to the client, as returned
        by ``Socket.getpeername``.
    """

    def __init__(self, manager, reader, writer, peername):
        """Initialize the connection."""
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
        """ This method is executed when the client connects to the
        server and returns when the connection should be closed.
        """
        raise NotImplementedError

    def close(self):
        """Close the connection."""
        self.writer.close()


class AbstractTransport:
    """This class defines the layer between the ``RequestHandler``,
    that will handle the reception of the request, the delegation
    to a request handler, receiving the payload of the request and
    sending the response.

    Attributes:
    :connection: The connection that created this transport.
    :request: The current request.
    :body_reader: The current body reader, used to read the request
        payload.
    :handler: The current handler, chosen from the current request.
    :response: The response sent, may be sent by the handler, or an error
        sent by the transport.
    :error: The current HTTP error, is None if there is no error.
    """

    def __init__(self, connection):
        """Initialize the transport."""
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
        """Raises the current error."""
        if self.error is not None:
            raise self.error

    @coroutine
    def send_response(self, status, headers=None, body=None):
        """Send an HTTP response to the remote client.

        Arguments:
        :status: The HTTP status of the response.
        :headers: A collection of header fields sent in the response.
        :body: the response payload body. 
        """
        raise NotImplementedError

    @coroutine
    def send_error(self, status, headers=None, **kwargs):
        raise NotImplementedError

    @coroutine
    def run(self):
        """Process a single request, then returns."""
        raise NotImplementedError

    @coroutine
    def receive_request(self):
        """Read data from the client to parse its request. Returns that
        request.
        """
        raise NotImplementedError

    @coroutine
    def handle_request(self):
        """Call the adequate ``RequestHandler`` that will send a response."""
        raise NotImplementedError

    @coroutine
    def cleanup(self):
        """Executed after the request handling."""
        raise NotImplementedError


#=================#
# Request handler #
#=================#

class MetaRequestHandler(type):
    """
    Metaclass for all user-defined request handlers.

    Populate the methods attribute of the request handler in order to
    easily acces to handler's allowed methods.
    """
    def __init__(cls, name, bases, namespace):
        methods = set()

        for method in SUPPORTED_METHODS:
            method_handler = getattr(cls, method.lower(), None)
            if method_handler and asyncio.iscoroutinefunction(method_handler):
                methods.add(method.lower())

        cls.methods = frozenset(methods)


class RequestHandler(metaclass=MetaRequestHandler):
    """Base class for all user defined request handlers.

    Each method defined in a sublass of this class that have the same
    name as an HTTP method will be called to handle this HTTP method.
    """

    def __init__(self, transport):
        self.transport = transport
        self.request = transport.request
        self.body_reader = transport.body_reader

    def send_response(self, status, headers=None, body=None):
        """A shortcut to the transport ``send_response`` method."""
        assert self.request is self.transport.request

        if isinstance(body, str):
            body = body.encode("utf-8")

        return self.transport.send_response(status, headers, body)

    def send_error(self, status, headers=None, **kwargs):
        """A shortcut to the transport ``send_response`` method."""
        assert self.request is self.transport.request
        return self.transport.send_error(status, headers, **kwargs)

    @coroutine
    def can_continue(self):
        """Checks if the validity of the request may be asserted before
        reading the payload body.

        This function should retuns True if the request is fine or if
        this handler requires the payload body.
        When this function returns True, it should have send an error
        prior to returning, or the default error 417 wiil be sent to the
        client.
        """
        return True
