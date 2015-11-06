import os
import asyncio

from urllib.parse import *
from io import BytesIO
from socket import socket
from asyncio import coroutine
from asyncioplus.iostream import *

from .httputils import *


#===================#
# Async HTTP client #
#===================#

STATUS_LINE_PATTERN = r"HTTP/(\d+\.\d+) (\d{3}) (.+)"
STATUS_LINE_REGEX = re.compile(STATUS_LINE_PATTERN)

class ClientConnection:
    def __init__(self, user_agent = "Centimani/0.1", loop = None):
        self._loop = loop or asyncio.get_event_loop()

        self.reader = None
        self.writer = None
        self.user_agent = user_agent

    @coroutine
    def open(self, host, port):
        """
        Open the connection to the server.
        """
        self.host = host
        self.port = port

        self.reader, self.writer = yield from open_connection(host, port, loop = self._loop)

    def close(self):
        """
        Close the connection with the server.
        """
        self.writer.close()

    def fetch(self,
        url,
        method = "GET",
        headers = None,
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

        request_headers = headers or HTTPHeaders()

        #--------------#
        # Send request #
        #--------------#

        request_headers.set("Host", self.host + ":" + str(self.port))
        request_headers.set("User-Agent", self.user_agent)

        if "Content-Length" not in request_headers:
            if body is not None:
                request_headers.set("Content-Length", len(body))
            elif body_producer is None:
                request_headers.set("Content-Length", 0)
            elif body_producer.has_size:
                request_headers.set("Content-Length", body_producer.size)
            elif (
                "Transfert-Encoding" not in request_headers
                or "chunked" not in request_headers["Transfert-Encoding"]
            ):
                request_headers.set("Transfert-Encoding", "chunked")

        header = "{} {} HTTP/1.1\r\n".format(method, url)
        header += request_headers.http_encode()
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

        response_headers = HTTPHeaders()
        for line in header_lines:
            line = line.decode("ascii")
            name, value = response_headers.parse_line(line)
            response_headers.add(name, value)

        #-------------------#
        # Get response body #
        #-------------------#

        response = Response(version, status, response_headers)
        body = BytesIO() if body_chunk_callback is None else None

        if not response_headers.is_chunked:
            body_size = int(response_headers.get("Content-Length"))
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
