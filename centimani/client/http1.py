import asyncio
import logging
import time

from asyncio import coroutine

from .errors import ClientConnectionError, ClientTimeoutError
from .handlers import AbstractConnection, Response
from centimani.headers import Headers
from centimani.streamutils import BufferedBodyReader, ChunkedBodyReader
from centimani.errors import HttpError


_LOGGER = logging.getLogger(__name__)


class ConnectionLogger(logging.LoggerAdapter):
    def __init__(self, logger, peername):
        super().__init__(logger, {"peername": peername})

    def process(self, msg, kwargs):
        tmp = "@{0[0]}:{0[1]}\n{1}".format(self.extra["peername"], msg)
        return tmp, kwargs


class Http1Connection(AbstractConnection):
    """A Connection that impelment the HTTP/1.1 protocol, as defined in 
    RFC7230 and RFC7231.
    """

    def __init__(self, manager, reader, writer, peername, queue=None, loop=None):
        super().__init__(manager, reader, writer, peername, loop=loop)
        self._logger = ConnectionLogger(_LOGGER, self._peername)
        self._semaphore = None
        self._is_locked = False

    @property
    def protocol(self):
        return "http/1.1"

    @property
    def is_available(self):
        """An HTTP connection is available if it is not closing and
        not locked.
        """
        if not self._writer.is_closing():
            return not self._is_locked
        else:
            return False
    
    def lock(self, semaphore):
        """Lock this connection and link it to the ressource mananged by
        the ``semaphore``.

        The semaphore will be released when the next call to ``fetch``
        retruns or raise an exception.
        """
        assert not self._is_locked
        self._semaphore = semaphore
        self._is_locked = True

    @coroutine
    def fetch(self, request):
        """Send the ``request`` to the server and returns the response.

        When the response is built or an exception is raised, the
        semaphore passed to ``lock`` will be released and the connection
        will be unlocked.

        If an exception is raised, the connection will be closed.
        """
        assert not self._writer.is_closing()
        assert self._is_locked

        try:
            if request.timeout is not None:
                response = yield from asyncio.wait_for(
                    self._fetch(request),
                    request.timeout
                )
            else:
                response = yield from self._fetch(request)

        except ConnectionError as error:
            if not self.is_closing():
                self.close()

            msg = "connection error during handling of {0}".format(request)
            raise ClientConnectionError(msg) from error

        except asyncio.TimeoutError as error:
            if not self.is_closing():
                self.close()

            msg = "request {0} timeout.".format(request)
            raise ClientTimeoutError(msg) from error

        finally:
            if self._semaphore is not None:
                self._semaphore.release()

            self._is_locked = False

        return response

    @coroutine
    def _fetch(self, request):
        """Used by ``fetch`` to actually do the request/response transfert."""
        assert not self._writer.is_closing()
        assert self._is_locked

        start_time = self._loop.time()

        host = request.header_fields.get("host", [])
        if not host:
            request.header_fields.set("host", request.authority)

        #--------------#
        # Send request #
        #--------------#

        request_line = "{0} {1} HTTP/1.1\r\n".format(
            request.method,
            request.relative_url
        ).encode("ascii")

        header_fields = b"".join(
            ": ".join((name, content)).encode("ascii") + b"\r\n"
            for name, content in request.header_fields.fields()
        )

        header = b"".join((request_line, header_fields, b"\r\n"))
        self._writer.write(header)

        if request.body:
            self._writer.write(request.body)

        yield from self._writer.drain()

        #------------------#
        # Receive response #
        #------------------#

        header = yield from self._reader.read_until(b"\r\n\r\n")
        status_line, *header_field_lines = header.split(b"\r\n")

        if not status_line:
            if not self.is_closing():
                self.close()

            raise EOFError

        version, status, reason = status_line.split(maxsplit=2)

        response = Response(int(status), request=request)
        response.header_fields.parse_lines(header_field_lines)

        #--------------#
        # Body reading #
        #--------------#

        transfer_encoding = response.header_fields.get("transfer-encoding", [])
        content_length = response.header_fields.get("content-length", [])

        body = bytearray()

        if transfer_encoding:
            assert transfer_encoding[-1] == "chunked"

            body_reader = ChunkedBodyReader(self._reader)

            it = yield from body_reader.__aiter__()
            running = True
            while running:
                try:
                    block = yield from it.__anext__()
                except StopAsyncIteration:
                    running = False
                else:
                    if request.body_streaming_callback is None:
                        body.extend(block)
                    else:
                        request.body_streaming_callback(block)

        elif content_length:
            body_length = int(content_length[0])
            body_reader = BufferedBodyReader(self._reader, body_length)

            it = yield from body_reader.__aiter__()
            running = True
            while running:
                try:
                    chunk = yield from it.__anext__()
                except StopAsyncIteration:
                    running = False
                else:
                    if request.body_streaming_callback is None:
                        body.extend(chunk)
                    else:
                        request.body_streaming_callback(chunk)

        response.body = bytes(body)

        if "close" in response.header_fields.get("connection", []):
            self._writer.close()

        end_time = self._loop.time()
        delta_time = end_time - start_time
        self._logger.debug("response built in %fs:\n%s\n%s",
            delta_time, request, response)

        return response
