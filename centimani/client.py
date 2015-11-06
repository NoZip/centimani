import os
import asyncio

from urllib.parse import *
from io import BytesIO
from socket import socket
from asyncio import coroutine
from asyncioplus.iostream import *

from .httputils import *


#================#
# Body producers #
#================#

class FileBodyProducer:
    """
    Body producer that handle a file on disk.
    """

    has_size = True

    def __init__(self, filename, chunk_size = 2**16):
        self.filename = filename
        self.size = os.stat(filename).st_size

        self._chunk_size = chunk_size

    @coroutine
    def write(self, writer, loop = None):
        loop = loop or asyncio.get_event_loop()

        with open(self.filename, "rb") as src:
            src_socket = socket(fileno = src.fileno())

            for chunk_index in range(self.file_size//self._chunk_size):
                data = yield from loop.socket_recv(src, self._chunk_size)
                writer.write(data)


class StreamBodyProducer:
    """
    Body producer that handle streams.

    If size is not given to the constructor, the client will assume that the
    body will be transferred with the chunked encoding.
    """

    def __init__(self, reader, size = None, chunk_size = 2**16):
        self.reader = reader
        self.size = size

        self._chunk_size = chunk_size

    @property
    def has_size(self):
        return self.size is not None

    @coroutine
    def write(self, writer, loop = None):
        loop = loop or asyncio.get_event_loop()

        if self.has_size():
            chunk_count, last_chunk_size = divmod(self.size, self._chunk_size)

            for chunk_index in range(chunk_count):
                data = yield from self.reader.read(self._chunk_size)
                writer.write(data)

            data = yield from self.reader.read(last_chunk_size)
            writer.write(data)
        else:
            chunk = None
            while chunk != b"":
                chunk = yield from self.reader.read(self._chunk_size)
                chunk = "{:X}\r\n".format(len(chunk)).encode("ascii") + chunk
                writer.write(chunk)


#===================#
# Async HTTP client #
#===================#

STATUS_LINE_PATTERN = r"HTTP/(\d+\.\d+) (\d{3}) (.+)"
STATUS_LINE_REGEX = re.compile(STATUS_LINE_PATTERN)

class Client:
    def __init__(self, loop = None):
        self._loop = loop or asyncio.get_event_loop()

        self.reader = None
        self.writer = None

    @coroutine
    def open(self, host, port):
        """
        Open the connection to the server.
        """
        self.reader, self.writer = yield from open_connection(host, port, loop = self._loop)

    def close(self):
        """
        Close the connection with the server.
        """
        self.writer.close()

    def fetch(self,
        request,
        body = None,
        body_producer = None,
        body_chunk_callback = None
    ):
        """
        Fetch a ressource on the connected server.

        Returns a (response, body) tuple. body is a BytesIO object.

        Parameters:
            request: the request to send.

            body: the request body.

            body_producer: an object that will produce a request body. this
                function will call its write(writer, loop) method, that must
                return an awaitable object. If body_producer is set, body must
                be None.

            body_chunk_callback: this callback will be called each time a chunk
                of the response body is received. If body_chunk_callback is set
                the response body will be None.
        """

        #--------------#
        # Send request #
        #--------------#

        request.headers["User-Agent"] = ["Centimani/0.1"]

        host, port = self.writer.get_extra_info("peername")
        request.headers["Host"] = [":".join((host, str(port)))]

        if "Content-Length" not in request.headers:
            if body is not None:
                request.headers.add("Content-Length", len(body))
            elif body_producer is None:
                request.headers.add("Content-Length", 0)
            elif body_producer.has_size:
                request.headers.add("Content-Length", body_producer.size)
            elif (
                "Transfert-Encoding" not in request.headers
                or "chunked" not in request.headers["Transfert-Encoding"]
            ):
                request.headers["Transfert-Encoding"].insert(0, "chunked")

        uri = urlunsplit(("", "", request.path, urlencode(request.query), ""))
        header = "{} {} HTTP/{}\r\n".format(request.method, uri, request.version)
        header += request.headers.http_encode()
        header += "\r\n"

        self.writer.write(header.encode("ascii"))

        if body is not None:
            self.writer.write(body)
        elif body_producer is not None:
            yield from body_producer.write(self.writer, loop = self._loop)

        yield from self.writer.drain()

        #------------------#
        # Receive response #
        #------------------#

        header = yield from self.reader.read_until(b"\r\n\r\n")
        status_line, *header_lines = header.split(b"\r\n")

        if not status_line:
            raise Exception()

        match = STATUS_LINE_REGEX.match(status_line.decode("ascii"))

        if not match:
            raise Exception()

        version, status, reason = match.groups()

        headers = HTTPHeaders()
        for line in header_lines:
            line = line.decode("ascii")
            name, value = headers.parse_line(line)
            headers.add(name, value)

        #-------------------#
        # Get response body #
        #-------------------#

        response = Response(version, status, headers)
        body = BytesIO() if body_chunk_callback is None else None

        if not headers.is_chunked:
            body_size = int(headers.get("Content-Length"))
            chunks_count, last_chunk_size = divmod(body_size, 2**16)

            for chunk_index in range(chunks_count):
                chunk = yield from self.reader.read(2**16)

                if body_chunk_callback is None:
                    body.write(chunk)
                else:
                    body_chunk_callback(chunk)

            last_chunk = yield from self.reader.read(last_chunk_size)

            if body_chunk_callback is None:
                body.write(last_chunk)
            else:
                body_chunk_callback(last_chunk)

        else:
            chunks_reader = ChunkTransfertReader(self.reader)
            while True:
                try:
                    chunk = yield from chunks_reader.__anext__()
                except StopIteration:
                    break

                if body_chunk_callback is None:
                    body.write(chunk)
                else:
                    body_chunk_callback(chunk)

        return (response, body)
