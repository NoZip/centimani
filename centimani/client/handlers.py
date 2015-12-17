import asyncio
import logging
import urllib.parse as urlparse

from asyncio import coroutine

from centimani.headers import Headers


_LOGGER = logging.getLogger(__name__)


class Request:

    def __init__(
            self,
            url,
            method="GET",
            header_fields=None,
            body=None,
            body_streaming_callback=None,
            timeout=None):
        self.url = url
        self.method = method
        self.header_fields = header_fields or Headers()
        self.body = body
        self.body_streaming_callback = body_streaming_callback
        self.timeout = timeout
        self.redirect_count = 0

    def __repr__(self):
        return "<Request {0} {1}>".format(self.method, self.url)

    def get_url(self):
        return self._url

    def set_url(self, url):
        self._url = url

        url_components = urlparse.urlsplit(self.url)
        scheme, authority, path, query, _ = url_components

        if not scheme:
            ValueError("url scheme must be specified")

        if not authority:
            ValueError("url netloc must be specified")

        self.scheme = scheme
        self.authority = authority

        if not path:
            path = "/"

        if query:
            self.relative_url = "?".join([path, query])
        else:
            self.relative_url = path

    url = property(get_url, set_url)


class Response:

    def __init__(self, status, header_fields=None, request=None):
        self.status = status
        self.header_fields = header_fields or Headers()
        self.request = request
        self.body = b""

    def __repr__(self):
        return "<Response {0}>".format(self.status)


class ConnectionLogger(logging.LoggerAdapter):
    def __init__(self, logger, peername):
        super().__init__(logger, {"peername": peername})

    def process(self, msg, kwargs):
        tmp = "@{0[0]}:{0[1]}\n{1}".format(self.extra["peername"], msg)
        return tmp, kwargs


class Connection:

    def __init__(self, client, reader, writer, peername, logger=_LOGGER):
        self._client = client
        self._reader = reader
        self._writer = writer
        self._peername = peername
        self._logger = ConnectionLogger(logger, self._peername)

        self._last_activity = self._loop.time()

    def __repr__(self):
        return "<{0} {1[0]}:{1[1]} closing={2}>".format(
            self.__class__.__name__,
            self._peername,
            self.is_closing()
        )

    @property
    def _loop(self):
        return self._client._loop

    @property
    def last_activity(self):
        """Time of last activity on this connection."""
        return self._last_activity

    def is_closing(self):
        """Returns True if the connection is closing or closed."""
        return self._writer.is_closing()

    def touch(self):
        """Change last activity time to now."""
        self._last_activity = self._loop.time()

    def lock(self, semaphore):
        """Locks this connection."""
        raise NotImplementedError

    async def fetch(self, request):
        """Send a request to the server and returns the response."""
        raise NotImplementedError

    def close(self):
        """Closes this connection."""
        assert not self._writer.is_closing()
        self._writer.close()
