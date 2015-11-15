import asyncio
import os
import re

from asyncio import coroutine
from asyncioplus.iostream import *
from io import BytesIO
from socket import socket
from urllib.parse import *

from .compression import *
from .headers import Headers


#=================#
# HTTP structures #
#=================#

class Request:
    def __init__(self, url, method = "GET", headers = None):
        self.uel = url
        self.headers = headers or Headers()
        self.method = method

class Response:
    def __init__(self, request, status, headers = None):
        self.request = request
        self.status = status
        self.headers = headers or Headers()


#===================#
# Async HTTP client #
#===================#

_status_line = rb"^HTTP/(\d+\.\d+) (\d{3}) (.+)$"
STATUS_LINE_REGEX = re.compile(_status_line)

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

        request = Request(url, method, headers)

        #--------------#
        # Send request #
        #--------------#

        request.headers.set("host", self.host + ":" + str(self.port))
        request.headers.set("user-Agent", self.user_agent)
        # request.headers.add("accept-encoding", SUPPORTED_COMPRESSIONS)

        if "connection" not in request.headers:
            request.headers.set("connection", "keep-alive")

        if "content-length" not in request.headers:
            if body is not None:
                request.headers.set("content-length", len(body))
            elif body_producer is None:
                request.headers.set("content-length", 0)
            elif body_producer.has_size:
                request.headers.set("content-length", body_producer.size)
            elif (
                "transfert-encoding" not in request.headers
                or "chunked" not in request.headers["transfert-encoding"]
            ):
                request.headers.set("transfert-encoding", "chunked")

        header = "{0} {1} HTTP/{2}\r\n".format(method, url, "1.1")
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
        status_line, *headers_lines = header.split(b"\r\n")

        if not status_line:
            # at EOF
            self.close()

        match = STATUS_LINE_REGEX.match(status_line)

        if not match:
            self.close()
            raise Exception("malformed status line")

        version, status, reason = map(lambda s: s.decode("ascii"), match.groups(b""))
        status = int(status, base = 10)

        if not "1.0" <= version <= "1.1":
            self.close()
            raise Exception("wrong http version")

        response = Response(request, status)
        response.headers.parse_lines(headers_lines)

        #-------------------#
        # Get response body #
        #-------------------#

        body = BytesIO() if body_chunk_callback is None else None

        # some responses has no body, no matter what header is present
        if not self._has_body(method, status):
            return (response, None)

        transfert_encoding = response.headers.get("transfert-encoding", [])
        content_length = response.headers.get("content-length", [])

        if "chunked" not in transfert_encoding:
            # multiple content-length generate an error
            if len(content_length) > 1:
                self.close()
                raise Exception()

            # if content-length header is present, get body size from it
            # else, read stream until EOF
            body_size = None
            if content_length:
                body_size = int(content_length[0])

            if body_size != 0:
                if body_size:
                    data = yield from self.reader.read(body_size)
                else:
                    raise NotImplementedError

                if not body_chunk_callback:
                    body.write(data)
                else:
                    body_chunk_callback(data)
        else:
            raise NotImplementedError
            # chunked is not the final encoding: read until closing
            # TODO
            # if transfert_encoding[-1] != "chunked":
            #     self.close()
            #     raise NotImplementedError

            # if "content-length" in response.headers:
            #     del response.headers["content-length"]

            # encoding_chain = transfert_encoding[:-1]
            # chunks_reader = ChunkTransfertIterator(self.reader)

            # if encoding_chain:
            #     chunks_reader = DecompressPipe(chunks_reader, encoding_chain)

            # running = True
            # while running:
            #     try:
            #         chunk = yield from chunks_reader.__anext__()
            #     except StopAsyncIteration:
            #         running = False
            #     else:
            #         if body_chunk_callback is None:
            #             body.write(chunk)
            #         else:
            #             body_chunk_callback(chunk)


        if "close" in response.headers.get("connection", []):
            self.close()

        return (response, body)
