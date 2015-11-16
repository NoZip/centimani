import asyncio
import os
import logging
import re
import sys

from asyncio import coroutine
from copy import deepcopy
from datetime import datetime
from urllib.parse import unquote_plus, parse_qs

import centimani.log

from centimani.headers import Headers, HeaderParseError
from centimani.log import MaxLevelFilter
from centimani.server.dispatcher import RoutingError
from centimani.server.handlers import AbstractConnectionHandler, AbstractResponseHandler, Request
from centimani.utils import HTTP_STATUSES, HTTP_METHODS, BufferedBodyReader, ChunkedBodyReader

if "StopAsyncIteration" not in dir(__builtins__):
    from asyncioplus.utils import StopAsyncIteration

logger = logging.getLogger(__name__)

_segment = rb"(?:[-._~A-Za-z0-9!$&'()*+,;=:@]|%[0-9A-F]{2})+"
_path = rb"/(?:" + _segment + rb"(?:/" + _segment + rb")*/?)?"
_query = rb"(?:[-._~A-Za-z0-9!$&'()*+,;=:@/?]|%[0-9A-F]{2})*"


_request_line = rb"^([A-Z]+)[ \t]+(\*|" + _path + rb"(?:\?" + _query + rb")?)[ \t]+HTTP/(\d+\.\d+)$"
REQUEST_LINE_REGEX = re.compile(_request_line)


class ConnectionLogger(logging.LoggerAdapter):
    def __init__(self, logger, peername):
        super().__init__(logger, {"peername": peername})

    def process(self, msg, kwargs):
        return "@{0[0]}:{0[1]}\n{1}".format(self.extra["peername"], msg), kwargs


class ConnectionHandler(AbstractConnectionHandler):
    def __init__(self, dispatcher, reader, writer, peername, loop = None):
        super().__init__(dispatcher, reader, writer, peername, loop = loop)
        self.logger = ConnectionLogger(logger, peername)

        self.client_version = "1.0" 
        self.switch_to = None

    @coroutine
    def read_body(self, handler, stream):
        assert isinstance(handler, AbstractResponseHandler)
        assert not handler.is_body_read

        headers = handler.request.headers

        transfert_encoding = headers.get("transfert-encoding", [])
        content_length = headers.get("content-length", [])

        assert "chunked" in transfert_encoding or content_length

        if content_length:
            assert(len(content_length) == 1)

            self.logger.debug("reading body")

            body_size = int(content_length[0])

            if body_size > 0:
                body_reader = BufferedBodyReader(self.reader, body_size)

                running = True
                while running:
                    try:
                        block = yield from body_reader.__anext__()
                    except StopAsyncIteration:
                        running = False
                    else:
                        stream.write(block)

        elif "chunked" in transfert_encoding:
            assert transfert_encoding[-1] == "chunked"

            self.logger.debug("reading chunked body")
            
            body_reader = ChunkedBodyReader(self.reader)

            if transfert_encoding[:-1]:
                raise NotImplementedError

            running = True
            while running:
                try:
                    chunk = yield from body_reader.__anext__()
                except StopAsyncIteration:
                    running = False
                else:
                    stream.write(chunk)

        handler.is_body_read = True

        self.logger.debug("body read")

    @coroutine
    def send_response(self, handler, status, headers = None, body = None, body_producer = None):
        assert isinstance(handler, AbstractResponseHandler)
        assert status in HTTP_STATUSES.keys()
        assert not handler.is_body_sent

        status_line = "HTTP/{!s} {:d} {!s}\r\n".format(
            self.client_version,
            status,
            HTTP_STATUSES[status]
        )

        headers_addons = Headers(
            date = datetime.utcnow(),
            server = self.dispatcher.server_agent
        )

        headers = headers if headers else Headers()
        headers.update(headers_addons)

        if body:
            headers.set("content-length", len(body))

        elif body_producer:
            raise NotImplementedError

            # if body_producer.has_size:
            #     headers.set("content-length", body_producer.size)
            # else:
            #     headers.set("transfert-encoding", "chunked")

        else:
            headers.set("content-length", 0)

        header = status_line + headers.http_encode() + "\r\n"
        self.writer.write(header.encode("ascii"))

        if body:
            self.writer.write(body)
        # elif body_producer:
        #     pass

        yield from self.writer.drain()

        handler.is_response_sent = True

        logger.debug("response sent:\n{0}", header)

    @coroutine
    def send_error(self, status, headers = None, request = None, **kwargs):
        assert status in HTTP_STATUSES.keys()

        ErrorHandler = self.dispatcher._error_handler_factory
        handler = ErrorHandler(self, request)
        self.current_handler = handler

        tmp = handler.error(status, headers, **kwargs)
        yield from self._loop.create_task(tmp)

    @coroutine
    def cleanup(self):
        if self.current_handler:
            yield from self.current_handler.cleanup()

    @coroutine
    def run(self):
        #-----------------#
        # Receive request #
        #-----------------#

        try:
            read_coroutine = self.reader.read_until(b"\r\n\r\n")
            header = yield from asyncio.wait_for(read_coroutine, 90)
        except asyncio.TimeoutError as error:
            # After request timeout, send an error response then close the connection
            self.logger.info("request waiting timeout")
            yield from self.send_error(408)
            return False
        except ConnectionError:
            self.logger.exception("connection error during request waiting")
            return False

        request_line, *headers_lines = header.split(b"\r\n")

        #-------------------------#
        # Request line processing #
        #-------------------------#

        if not request_line:
            # No request line = EOF, so we close the connection
            self.logger.info("no request line, at EOF")
            return False

        self.logger.debug(request_line)

        match = REQUEST_LINE_REGEX.match(request_line)

        if match:
            method, url, version = map(lambda s: s.decode("ascii"), match.groups(b""))
        
        # A request is well formed if and only if:
        # - the method is a valid HTTP method
        # - the version string is in the form "HTTP/1.1"
        # - the url match the format defined in RFC 3986
        # - for security reasons, the percent encoded values for "/" and "\", respectively %2F and
        #   %5C cannot be present in the url.
        is_malformed = (
            not match
            or method not in HTTP_METHODS
            or ("%2F" in url or "%5C" in url)
        )

        if is_malformed:
            # Bad request, connection is closed after error is send
            self.logger.info("request line malformed")
            yield from self.send_error(400)
            return False

        url_split = url.split("?", maxsplit = 1)

        path = unquote_plus(url_split[0])
        query = url_split[1] if len(url_split) > 1 else ""

        if query:
            try:
                query = parse_qs(query, strict_parsing = True)
            except ValueError:
                # malformed - ignore the query
                self.logger.info("malformed query")
                query = {}
        else:
            query = {}

        request = Request(method, path, query)

        self.logger.debug(request)

        # upgrade client_version if needed
        if self.client_version != version:
            self.client_version = version
            self.logger.info("client version set to {0}", version)

        #-----------------------#
        # Parsing header fields #
        #-----------------------#

        try:
            request.headers.parse_lines(headers_lines)
        except HeaderParseError as error:
            # Bad request, connection is closed after error is send
            self.logger.info("malformed header field {!s}", error)
            yield from self.send_error(400)
            return False

        self.logger.debug(request.headers)

        # the client wants a 100 Continue response before sending data
        if "100-continue" in request.headers.get("except", []):
            self.logger.info("no support for 100-continue expectations")
            headers = Headers(connection = "close")
            yield from self.send_error(417, headers)
            return False


        #-------------------------------------#
        # Body length and encoding validation #
        #-------------------------------------#

        transfert_encoding = request.headers.get("transfert-encoding", [])
        content_length = request.headers.get("content-length", [])

        if transfert_encoding:
            if content_length:
                self.info("transfert-encoding and content-length headers present")
                del request.headers["content-length"]
            
            if transfert_encoding[-1] != "chunked":
                yield from self.send_error(400)
                return False
            
        
        elif content_length:
            if len(content_length) > 1:
                self.logger.info("multiple content-length headers")
                yield from self.send_error(400)
                return False

            if not re.match(r"^[1-9][0-9]*$", content_length[0]):
                self.logger.info("malformed content-length value")
                yield from self.send_error(400)
                return False

        else:
            request.headers.set("content-length", 0)

        #-----------------------------#
        # Connection keep alive check #
        #-----------------------------#

        connection = request.headers.get("connection", [])

        keep_alive = (
            version == "1.1" and "close" not in connection
            or version == "1.0" and "keep_alive" in connection
        )

        #-----------------#
        # Request routing #
        #-----------------#

        try:
            request_handler_factory, args, kwargs = self.dispatcher.find_route(path)
        except RoutingError as error:
            # No route finded, send 404 not find error
            self.logger.info("route not find")
            yield from self.send_error(404, request = request)
            return keep_alive

        if method.lower() not in request_handler_factory.methods:
            # Method not implemented, send error 405 not implemented
            self.logger.info("method not implemented")
            error_headers = Headers(allowed=request_handler_factory.methods)
            yield from self.send_error(405, error_headers, request = request)
            return keep_alive

        #-------------------------#
        # Request handler calling #
        #-------------------------#

        self.logger.debug("request handling")

        handler = request_handler_factory(self, request)
        method_handler = getattr(handler, method.lower())

        try:
            tmp = method_handler(*args, **kwargs)
            yield from self._loop.create_task(tmp)
        except ConnectionError as error:
            self.logger.exception("connection error during response handling")
            return False
        except Exception as error:
            self.logger.exception("error during response handling")
            yield from self.send_error(500, request = request)
            return keep_alive

        return keep_alive
