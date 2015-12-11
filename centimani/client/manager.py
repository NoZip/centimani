import asyncio
import asyncioplus
import itertools
import logging

from asyncio import coroutine
from collections import defaultdict, namedtuple

from .errors import ClientConnectionError, ClientTimeoutError
from .handlers import Request
from .http1 import Http1Connection


_LOGGER = logging.getLogger(__name__)


class ConnectionManager:

    @staticmethod
    def default_port(scheme):
        """Helper that return the default port linked to a scheme."""
        if scheme == "http":
            return 80
        elif scheme == "https":
            return 443
        else:
            ValueError("{!r} is not a valid scheme.".format(scheme))

    def __init__(
            self,
            *,
            connection_timeout=30,
            keep_alive_timeout=60,
            max_endpoint_connections=None,
            max_connections=None,
            max_redirections=5,
            loop=None):
        self._connection_timeout = connection_timeout
        self._keep_alive_timeout = keep_alive_timeout
        self._max_endpoint_connections = max_endpoint_connections
        self._max_connections = max_connections
        self._max_redirections = max_redirections

        self._loop = loop or asyncio.get_event_loop()
        
        self._connections = defaultdict(list)
        self._semaphores = defaultdict(self._default_semaphore)

        self._permanent_redirects = {}

        self._cleanup_running = False
        self._cleanup_task = self._loop.create_task(self._cleanup())

    def _default_semaphore(self):
        if self._max_endpoint_connections is not None:
            return asyncio.BoundedSemaphore(
                self._max_endpoint_connections,
                loop=self._loop
            )
        else:
            return None

    @coroutine
    def _cleanup(self):
        """Runs the cleanup task.

        Runs ``_cleanup_endpoint`` for each endpoint and remove endpoints
        with no connections.
        """
        self._cleanup_running = True
        while self._cleanup_running:
            _LOGGER.debug("cleanup waked up.")

            for key in self._connections:
                yield from self._cleanup_endpoint(key)

            no_connection_endpoint = [
                key for key, endpoint_connections in self._connections.items()
                if not endpoint_connections
            ]

            for key in no_connection_endpoint:
                _LOGGER.debug("remove endpoint %s", key)
                del self._connections[key]

            yield from asyncio.sleep(10)

    @coroutine
    def _cleanup_endpoint(self, key):
        """Cleanup connections for the endpoint designed by ``key``.

        - Closes timed out connections.
        - Removes closing connections.
        """
        endpoint_connections = self._connections[key]
        now = self._loop.time()

        _LOGGER.debug("connections to %s:\n%s", key, endpoint_connections)

        #------------------------------------------#
        # Remove closing and timed out connections #
        #------------------------------------------#

        timed_out_connections = [
            connection for connection in endpoint_connections
            if not connection.is_closing()
            and now - connection.last_activity > self._keep_alive_timeout
        ]

        if timed_out_connections:
            _LOGGER.debug("timed out connections\n%s", timed_out_connections)

        # close timed out connections
        for connection in timed_out_connections:
            connection.close()

        closing_connections = [
            connection for connection in endpoint_connections
            if connection.is_closing()
        ]

        if closing_connections:
            _LOGGER.debug("closing connections\n%s", closing_connections)

        # remove closing connections
        for connection in closing_connections:
            endpoint_connections.remove(connection)

    @coroutine
    def connect(self, request):
        """Get or create a connection in order to send ``request`` on it."""
        key = (request.scheme, request.authority)

        semaphore = self._semaphores[key]

        if semaphore is not None:
            yield from semaphore.acquire()

        connections = self._connections[key]
        available_connections = [c for c in connections if c.is_available]

        if available_connections:
            # connections are available, we choose the less active one.
            available_connections.sort(key=lambda c: c.last_activity)
            connection = available_connections[0]
            connection.lock(semaphore)
        else:
            # no available connection, open a new connection.
            try:
                connection = yield from self.open_connection(key)
            except ConnectionError as error:
                # connection aborted, release the semaphore
                semaphore.release()
                raise                

            connection.lock(semaphore)
            connections.append(connection)
            
        connection.touch()

        return connection

    @coroutine
    def open_connection(self, key):
        """Open a new connection to endpoint defined by ``scheme``
        and ``authority``.
        """
        scheme, authority = key

        if scheme == "http":
            ssl = None
        elif scheme == "https":
            ssl = True
        else:
            ValueError("{!r} is not a valid scheme.".format(scheme))

        if ":" in authority:
            host, port = authority.split(":")
            port = int(port) if port else self.default_port(scheme)
        else:
            host = authority
            port = self.default_port(scheme)

        reader, writer = yield from asyncioplus.open_connection(
            host, port, ssl=ssl
        )

        peername = writer.get_extra_info("peername")

        Connection = Http1Connection
        return Connection(self, reader, writer, peername)

    @coroutine
    def fetch(self, url_or_request, **kwargs):
        """Send an HTTP request and returns the server response.

        ``url_or_request`` may be a ``Request`` instance or a URL string.
        If it is an URL string, ``kwargs`` are used to fill the newly created
        request object.
        """
        if isinstance(url_or_request, Request):
            request = url_or_request
        else:
            request = Request(url_or_request, **kwargs)

        key = (request.scheme, request.authority)

        try:
            if self._connection_timeout:
                connection = yield from asyncio.wait_for(
                    self.connect(request),
                    self._connection_timeout
                )

            else:
                connection = yield from self.connect(request)

        except asyncio.TimeoutError as error:
            msg = "Connection to {0} timeout.".format(key)
            raise ClientTimeoutError(msg) from error

        except ConnectionError as error:
            msg = "Unable to connect to {0}".format(key)
            raise ClientConnectionError(msg) from error

        response = yield from connection.fetch(request)

        connection.touch()    

        # automatic redirection
        if response.status in {301, 302, 307, 308}:
            if request.redirect_count < self._max_redirections:
                location = response.header_fields.get("location", None)
                if location:
                    location = location[0]

                    # permanent redirect, add route to known redirections.
                    if response.status in {302, 308}:
                        self._permanent_redirects[request.url] = location

                    request.url = location
                    request.redirect_count += 1
                    response = yield from self.fetch(request)

        return response

    def close(self):
        """Closes all connections."""
        self._cleanup_task.cancel()

        connections = itertools.chain.from_iterable(self._connections.values())

        connections_to_close = (
            connection for connection in connections
            if not connection.is_closing()
        )

        for connection in connections_to_close:
            connection.close()
