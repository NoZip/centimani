import asyncio
import asyncioplus.iostream
import logging
import re

from asyncio import coroutine
from ssl import SSLContext, PROTOCOL_SSLv23

class RoutingError(Exception):
    pass

import centimani.log
import centimani.server.http1 as http1

from centimani.server.handlers import ErrorResponseHandler


logger = logging.getLogger(__name__)


DEFAULT_PROTOCOL_MAP = {
    "http/1.1" : http1.ConnectionHandler,
}


class Dispatcher:

    def __init__(self,
        routes,
        error_handler_factory = ErrorResponseHandler,
        protocols_map = DEFAULT_PROTOCOL_MAP,
        server_agent = "Centimani/0.1",
        loop = None
    ):
        self._loop = loop or asyncio.get_event_loop()
        self.routes = []
        self.error_handler_factory = error_handler_factory
        self.protocol_map = protocols_map
        self.server_agent = server_agent
        self.connections = {}

        for pattern, handler_factory in routes:
            route = (re.compile(pattern), handler_factory)
            self.routes.append(route)

    @property
    def supported_protocols(self):
        return frozenset(self.protocols_map.keys())

    def create_connection(self, protocol, reader, writer, peername):
        return self.protocol_map[protocol](self, reader, writer, peername, loop = self._loop)

    def find_route(self, path):
        for pattern, handler_factory in self.routes:
            match = pattern.match(path)
            if match:
                return (handler_factory, match.groups(), match.groupdict())

        raise RoutingError("Route not found")

    @coroutine
    def handle_connection(self, reader, writer):
        peername = writer.get_extra_info("peername")
        ssl_object = writer.get_extra_info("ssl_object")

        if ssl_object:
            protocol = "http/1.1"
            # protocol = ssl_object.selected_alpn_protocol()
        else:
            protocol = "http/1.1"

        connection = self.create_connection(protocol, reader, writer, peername)
        task = self._loop.create_task(connection.run())
        self.connections[peername] = (connection, task)

    @coroutine
    def listen(self, host="localhost", port=8080):
        """
        Start the dispatcher from listening on given port, binded to given host.
        """

        ssl_context = SSLContext(PROTOCOL_SSLv23)
        # ssl_context.set_alpn_protocols(self.supported_protocols)

        server = yield from asyncioplus.iostream.start_server(
            self.handle_connection,
            host = host,
            port = port,
            # ssl = ssl_context,
            loop = self._loop
        )

        logger.info("server listening on {0}:{1}", host, port)

        return server