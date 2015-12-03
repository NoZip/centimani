import asyncio
import logging
import asyncioplus

from asyncio import coroutine
from centimani import __version__
from .handlers import RequestHandler
from .http1 import Http1Connection
from .router import Router


_LOGGER = logging.getLogger(__name__)

DEFAULT_PROTOCOL_MAP = {
    "http/1.1" : Http1Connection,
}

DEFAULT_SERVER_AGENT = "Centimani/{0}".format(__version__)


class ConnectionManager:
    """This class listen to client connections and send them to
    connections instances.

    Attributes:
    :loop: The manager event loop.
    :router: The ``Router`` instance used to associate request handlers
        to requests.
    :protocol_map: A mapping that associate the ALPN name for a protocol
        to its connection class.
    :server_agent: The name of this server, as sent in the server header
        field.
    """

    def __init__(
            self,
            routes,
            protocol_map = DEFAULT_PROTOCOL_MAP,
            server_agent = DEFAULT_SERVER_AGENT,
            loop = None):
        """Initializes the manager.
        
        Arguments:
        :routes: A sequence of (pattern, handler_factory) that would
            be parsed by the ``Router`` class.
        :protocol_map: The manager protocol_map.
        :server_agent: The manager server_agent.
        :loop: The manager event loop.
        """
        self.loop = loop or asyncio.get_event_loop()
        self.router = Router(routes)
        self.protocol_map = protocol_map
        self.server_agent = server_agent
        self._connections = {}

        _LOGGER.debug(self.router._routes)

    @property
    def supported_protocols(self):
        """Returns a tuple of all supported ALPN protocols."""
        return tuple(self.protocols_map.keys())

    @coroutine
    def create_connection(self, reader, writer):
        """Create a connection instance and run it.

        This coroutine is called each time a new client connects to this
        server. This function will returns when the connection is over.
        """
        peername = writer.get_extra_info("peername")
        ssl_object = writer.get_extra_info("ssl_object")

        if ssl_object:
            protocol = "http/1.1"
            # protocol = ssl_object.selected_alpn_protocol()
        else:
            protocol = "http/1.1"

        connection_factory = self.protocol_map[protocol]
        connection = connection_factory(self, reader, writer, peername)
        task = self.loop.create_task(connection.run())

        self._connections[peername] = (connection, task)

        yield from task

        connection.close()
        del self._connections[peername]

    @coroutine
    def listen(self, host="localhost", port=8080):
        """Start the dispatcher from listening on given port,
        binded to given host.
        """

        # ssl_context = SSLContext(PROTOCOL_SSLv23)
        # ssl_context.set_alpn_protocols(self.supported_protocols)

        server = yield from asyncioplus.start_server(
            self.create_connection,
            host = host,
            port = port,
            # ssl = ssl_context,
            loop = self.loop
        )

        _LOGGER.info("server listening on %s:%d", host, port)

        return server
