import asyncio
import logging

from centimani.headers import Headers


_LOGGER = logging.getLogger(__name__)

REQUEST_METHODS = frozenset(
    {"GET", "HEAD", "POST", "OPTIONS", "PUT", "PATCH", "DELETE"}
)


#======================================#
# HTTP Response and Request Structures #
#======================================#

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


#======================================#
# Connection and Protocol base classes #
#======================================#

class ConnectionLogger(logging.LoggerAdapter):
    """A logging adapter used to log message associated with connection
    peername.
    """
    def __init__(self, logger, peername):
        super().__init__(logger, {"peername": peername})

    def process(self, msg, kwargs):
        tmp = "@{0[0]}:{0[1]}\n{1}".format(self.extra["peername"], msg)
        return tmp, kwargs


class Connection:
    """Interface for connections initiated in ``Server``.

    Attributes:
    :server: The server that have created this connection.
    :reader: The sream for reading data from the remote client.
    :writer: The stream for sending data to the remote client.
    :peername: A (host, port) tuple associated to the client, as returned
        by ``Socket.getpeername``.
    """

    def __init__(self, server, reader, writer, peername, logger=_LOGGER):
        """Initialize the connection."""
        self._server = server
        self._reader = reader
        self._writer = writer
        self._peername = peername
        self._logger = ConnectionLogger(logger, self.peername)

    @property
    def server(self):
        return self._server

    @property
    def reader(self):
        return self._reader

    @property
    def writer(self):
        return self._writer

    @property
    def peername(self):
        return self._peername

    @property
    def logger(self):
        return self._logger

    @property
    def _loop(self):
        """Shortcut property that returns server's loop."""
        return self._server._loop

    async def listen(self):
        """ This method is executed when the client connects to the
        server and returns when the connection should be closed.
        """
        raise NotImplementedError

    def close(self):
        """Close the connection."""
        if not self.writer.is_closing():
            self.writer.close()


class ProtocolHandler:
    """This class defines the layer between ``RequestHandler`` and
    ``Connection``. It will handle the reception of the request, the
    delegation to a request handler, receiving the payload of the
    request and sending the response.

    Attributes:
    :connection: The connection that created this transport.
    :request: The current request.
    :body_reader: The current body reader, used to read the request
        payload.
    :handler: The current handler, chosen from the current request.
    :response: The response sent, may be sent by the handler, or an
        error sent by the transport.
    :error: The current HTTP error, is None if there is no error.
    """

    def __init__(self, connection):
        """Initialize the transport."""
        self._connection = connection

        self._request = None
        self._body_reader = None
        self._handler = None
        self._response = None
        self._error = None

    @property
    def request(self):
        return self._request

    @property
    def body_reader(self):
        return self._body_reader

    @property
    def handler(self):
        return self._handler

    @property
    def response(self):
        return self._response

    @property
    def error(self):
        return self._error

    #------------------------------------#
    # Shortcuts to connection attributes #
    #------------------------------------#

    @property
    def _loop(self):
        return self._connection._loop

    @property
    def _server(self):
        return self._connection._server

    @property
    def _logger(self):
        return self._connection._logger

    @property
    def _reader(self):
        return self._connection._reader

    @property
    def _writer(self):
        return self._connection._writer

    @property
    def _peername(self):
        return self._connection._peername

    #------------------#
    # Abstract methods #
    #------------------#

    async def send_response(self, status, headers=None, body=None):
        """Send an HTTP response to the remote client.

        Arguments:
        :status: The HTTP status of the response.
        :headers: A collection of header fields sent in the response.
        :body: the response payload body.
        """
        raise NotImplementedError

    async def send_error(self, code, headers=None, **kwargs):
        """Shortcut used to send HTTP errors to the client."""
        assert 400 <= code < 600
        await self.send_response(code, headers)

    async def process_request(self):
        """Process a single request, then returns."""
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

        for method in REQUEST_METHODS:
            method_handler = getattr(cls, method.lower(), None)
            if method_handler and asyncio.iscoroutinefunction(method_handler):
                methods.add(method.lower())

        cls.methods = frozenset(methods)


class RequestHandler:
    """Base class for all user defined request handlers.

    Each method defined in a sublass of this class that have the same
    name as an HTTP method will be called to handle this HTTP method.
    """

    @classmethod
    def allowed_methods(cls):
        return frozenset(
            method for method in REQUEST_METHODS
            if hasattr(cls, method.lower())
        )

    def __init__(self, protocol):
        self._protocol = protocol
        self.request = protocol.request
        self.body_reader = protocol.body_reader

    def send_response(self, status, headers=None, body=None):
        """A shortcut to the protocol ``send_response`` method."""
        assert self.request is self._protocol.request

        if isinstance(body, str):
            body = body.encode("utf-8")

        return self._protocol.send_response(status, headers, body)

    def send_error(self, status, headers=None, **kwargs):
        """A shortcut to the protocol ``send_response`` method."""
        assert self.request is self._protocol.request
        return self._protocol.send_error(status, headers, **kwargs)

    async def can_continue(self):
        """Checks if the validity of the request may be asserted before
        reading the payload body.

        This function should retuns True if the request is fine or if
        this handler requires the payload body.
        When this function returns False, it should have send an error
        prior to returning, or the default error 417 will be sent to the
        client.
        """
        return True
