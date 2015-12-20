"""
This module defines the ``Server`` class that manages clients connections
and dispatch them to the corresponding ``Connection`` instances.
"""

import asyncio
import logging
import ssl

from centimani import __version__
from centimani.stream import start_server
from .handlers import RequestHandler
from .http1 import Http1Connection
from .router import Router


_LOGGER = logging.getLogger(__name__)

DEFAULT_ALPN_PROTOCOLS = ("http/1.1",)

DEFAULT_PROTOCOL_MAP = {
    "http/1.1" : Http1Connection,
}

DEFAULT_SERVER_AGENT = "Centimani/{0}".format(__version__)


class Server:
    """This class listen to client connections and send them to
    connections instances.

    Attributes:
    :loop: The manager event loop.
    :router: The ``Router`` instance used to associate request handlers
        to requests.
    :server_agent: The name of this server as sended by the "server"
        header field. Defaults to "Centimani/<version>"
    """

    def __init__(
            self,
            routes,
            *,
            ssl_context=None,
            alpn_protocols=DEFAULT_ALPN_PROTOCOLS,
            protocol_map=DEFAULT_PROTOCOL_MAP,
            server_agent=DEFAULT_SERVER_AGENT,
            loop=None):
        """Initializes the manager.

        Arguments:
        :routes: A sequence of (pattern, handler_factory) that would
            be parsed by the ``Router`` class.
        :ssl_context: A SSL context that will be used on the listening socket.
        :alpn_protocols: The protocols supported over TLS by this server,
            ordered by preference.
        :protocol_map: A mapping linking ALPN protocol names to a
            corresponding ``AbstractConnection`` subclass.
        :server_agent: The manager server_agent.
        :loop: The server event loop.
        """
        self._loop = loop or asyncio.get_event_loop()
        self._router = Router(routes)
        self._protocol_map = protocol_map
        self._server_agent = server_agent
        self._connections = {}
        self._server = None

        if ssl_context:
            self._ssl_context = ssl_context

            if ssl.HAS_ALPN:
                self._ssl_context.set_alpn_protocols(alpn_protocols)
        else:
            self._ssl_context = None

        _LOGGER.debug(self.router._routes)

    @property
    def loop(self):
        return self._loop

    @property
    def router(self):
        return self._router

    @property
    def server_agent(self):
        return self._server_agent

    async def create_connection(self, reader, writer):
        """Create a connection instance and run it.

        This coroutine is called each time a new client connects to this
        server. This function will returns when the connection is over.
        """
        peername = writer.get_extra_info("peername")
        ssl_object = writer.get_extra_info("ssl_object")

        if ssl_object and ssl.HAS_ALPN:
            protocol = ssl_object.selected_alpn_protocol()
            _LOGGER.debug("%s protocol chosen with ALPN.", protocol)
        else:
            protocol = "http/1.1"

        connection_factory = self._protocol_map[protocol]
        connection = connection_factory(self, reader, writer, peername)
        task = self.loop.create_task(connection.listen())

        print(task)

        self._connections[peername] = (connection, task)

        await task

        del self._connections[peername]

    async def listen(self, host="localhost", port=8080):
        """Start the dispatcher from listening on given port,
        binded to given host.
        """
        self._server = await start_server(
            self.create_connection,
            host = host,
            port = port,
            ssl = self._ssl_context,
            loop = self.loop
        )

        _LOGGER.info("server listening on %s:%d", host, port)

    def close(self):
        self._server.close()

    async def wait_closed(self):
        await self._server.wait_closed()
