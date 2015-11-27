import asyncio
import logging
import asyncioplus.iostream

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
    def __init__(self,
            routes,
            protocol_map = DEFAULT_PROTOCOL_MAP,
            server_agent = DEFAULT_SERVER_AGENT,
            loop = None):
        self.loop = loop or asyncio.get_event_loop()
        self.router = Router(routes)
        self.protocol_map = protocol_map
        self.server_agent = server_agent
        self.connections = {}

        _LOGGER.debug(self.router._routes)

    @property
    def supported_protocols(self):
        return frozenset(self.protocols_map.keys())

    @coroutine
    def handle_connection(self, reader, writer):
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

        self.connections[peername] = (connection, task)

        yield from task

        connection.close()
        del self.connections[peername]

    @coroutine
    def listen(self, host="localhost", port=8080):
        """Start the dispatcher from listening on given port,
        binded to given host.
        """

        # ssl_context = SSLContext(PROTOCOL_SSLv23)
        # ssl_context.set_alpn_protocols(self.supported_protocols)

        server = yield from asyncioplus.iostream.start_server(
            self.handle_connection,
            host = host,
            port = port,
            # ssl = ssl_context,
            loop = self.loop
        )

        _LOGGER.info("server listening on %s:%d", host, port)

        return server
