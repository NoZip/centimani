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

        self.is_closed = True
        self.reader = None
        self.writer = None
        self.user_agent = user_agent

    @staticmethod
    def _has_body(method, status):
        if method == "HEAD":
            return False
        elif 100 <= status < 200:
            return False
        elif status in frozenset({204, 304}):
            return False
        elif method == "CONNECT" and 200 <= status < 300:
            return False

        return True

    @coroutine
    def open(self, host, port):
        """
        Open the connection to the server.
        """
        self.host = host
        self.port = port

        self.reader, self.writer = yield from open_connection(host, port, loop = self._loop)

        self.is_closed = False

    def close(self):
        """
        Close the connection with the server.
        """
        self.writer.close()
        self.is_closed = True

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
        assert not self.is_closed

        request_headers = headers or HTTPHeaders()

        #--------------#
        # Send request #
        #--------------#

        request_headers.set("Host", self.host + ":" + str(self.port))
        request_headers.set("User-Agent", self.user_agent)

        if "Connection" not in request_headers:
            request_headers.set("Connection", "keep-alive")

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
            self.close()
            raise Exception()

        match = STATUS_LINE_REGEX.match(status_line.decode("ascii"))

        if not match:
            self.close()
            raise Exception()

        version, status, reason = match.groups()
        major_version, minor_version = map(int, version.split(".", maxsplit = 1))
        status = int(status, base = 10)

        if major_version != 1:
            self.close()
            raise Exception()

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

        # some responses has no body, no matter what header is present
        if not self._has_body(method, status):
            return (response, None)

        transfert_encoding = response_headers.get("Transfert-Encoding", [])
        content_length = response_headers.get("Content-Length", [])

        if "chunked" not in transfert_encoding:
            # no content_length: read until closing
            # TODO
            if not content_length:
                self.close()
                raise NotImplementedError

            # multiple Content-Length generate an error
            elif len(content_length) > 1:
                self.close()
                raise Exception()

            else:
                body_size = int(content_length[0])

                # non chunked transfert encoding
                # TODO
                if transfert_encoding:
                    self.close()
                    raise NotImplementedError

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
            # chunked is not the final encoding: read until closing
            # TODO
            if transfert_encoding[-1] != "chunked":
                self.close()
                raise NotImplementedError

            if "Content-Length" in response_headers:
                del response_headers["Content-Length"]

            encoding_chain = transfert_encoding[:-1]
            chunks_reader = ChunkTransfertReader(self.reader, encoding_chain)
            while True:
                try:
                    chunk = yield from chunks_reader.__anext__()
                except StopIteration:
                    break

                if body_chunk_callback is None:
                    body.write(chunk)
                else:
                    body_chunk_callback(chunk)


        if "close" in response_headers.get("Connection", []):
            self.close()

        return (response, body)
