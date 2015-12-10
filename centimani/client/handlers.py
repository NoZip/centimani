import asyncio
import urllib.parse as urlparse

from asyncio import coroutine

from centimani.headers import Headers


class Request:

    def __init__(
            self,
            url,
            method="GET",
            header_fields=None,
            body=None,
            body_streaming_callback=None):
        self.url = url
        self.method = method
        self.header_fields = header_fields or Headers()
        self.body = body
        self.body_streaming_callback = body_streaming_callback
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


class AbstractConnection:

    def __init__(self, manager, reader, writer, peername, loop=None):
        self._manager = manager
        self._reader = reader
        self._writer = writer
        self._peername = peername
        self._loop = loop or asyncio.get_event_loop()
        self._last_activity = self._loop.time()

    def __repr__(self):
        return "<{0} {1[0]}:{1[1]} closing={2}>".format(
            self.__class__.__name__,
            self._peername,
            self.is_closing()
        )

    @property
    def protocol(self):
        raise NotImplementedError

    @property
    def last_activity(self):
        return self._last_activity

    def is_closing(self):
        return self._writer.is_closing()

    def touch(self):
        self._last_activity = self._loop.time()

    def acquire(self):
        raise NotImplementedError

    def release(self):
        raise NotImplementedError

    @coroutine
    def fetch(self, request):
        raise NotImplementedError

    def close(self):
        assert not self._writer.is_closing()
        self._writer.close()
