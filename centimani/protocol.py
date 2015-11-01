import asyncio

from asyncio import coroutine

DEFAULT_LIMIT = 2**16

@coroutine
def start_server(connection_callback, host=None, port=None, loop = None):
    if loop is None:
        loop = asyncio.get_event_loop()

    def factory():
        protocol = StreamingProtocol(connection_callback, loop = loop)
        return protocol

    server = yield from loop.create_server(factory, host, port)
    return server

class StreamReader:
    def __init__(self, transport, limit = DEFAULT_LIMIT, loop = None):
        assert transport

        self._transport = transport
        self._loop = loop or asyncio.get_event_loop() 

        self._buffer = bytearray()
        self._eof = False
        self._pending = None

        self._limit = limit
        self._paused = False

        self.exception = None

    @coroutine
    def _wait(self, parameter):
        if self._pending is not None and not self._pending.done():
            raise RuntimeError("another read call already pending")

        event = asyncio.Future(loop = self._loop)
        self._pending = (parameter, event)
        try:
            yield from event
        finally:
            self._pending = None

    def _maybe_pause(self):
        if not self._paused and len(self._buffer) > self._limit:
            try:
                self._transport.pause_reading()
            except NotImplementedError:
                raise RuntimeError("transport cannot be paused")

            self._paused = True

    def _maybe_resume(self):
        if self._paused and len(self._buffer) < self._limit:
            try:
                self._transport.resume_reading()
            except NotImplementedError:
                raise RuntimeError("transport cannot be resumed")

            self._paused = False

    def feed(self, chunk):
        assert isinstance(chunk, bytes)
        assert chunk

        assert not self._eof

        self._buffer.extend(chunk)
        
        # test pending read calls
        if self._pending:
            parameter, event = self._pending

            # read call
            if isinstance(parameter, int):
                if parameter <= len(self._buffer):
                    event.set_result(None)

            # read_until call
            elif isinstance(parameter, bytes):
                search_length = len(chunk) - len(parameter) - 1
                if parameter in self._buffer[-search_length:]:
                    event.set_result(None)

        self._maybe_pause()

    def feed_eof(self):
        self._eof = True

        if self._pending is None:
            return

        # release pending read call
        parameter, event = self._pending
        event.set_result(None)
        self._pending = None

    @coroutine
    def read(self, count):
        assert isinstance(count, int)
        assert count > 0

        if self.exception is not None:
            raise self.exception

        if count > self._limit:
            raise ValueError("trying to read more bytes than buffer limit")

        if not self._eof and (self._pending or len(self._buffer) < count):
            yield from self._wait(count)

        data = bytes(self._buffer[:count])
        del self._buffer[:count]

        self._maybe_resume()

        return data

    @coroutine
    def read_until(self, delimiter = b"\n"):
        assert(isinstance(delimiter, bytes))
        assert(delimiter)

        if self.exception is not None:
            raise self.exception

        if not self._eof and (self._pending or delimiter not in self._buffer):
            yield from self._wait(delimiter)

        index = self._buffer.find(delimiter)

        data = None
        if self._eof and index < 0:
            # EOF feeded and delimiter not find
            data = bytes(self._buffer[:])
            self._buffer.clear()
        else:
            data = bytes(self._buffer[:index])
            del self._buffer[:index + len(delimiter)]

        self._maybe_resume()

        return data


class StreamWriter:
    def __init__(self, transport, loop = None):
        assert transport

        self._transport = transport
        self._loop = loop or asyncio.get_event_loop()

        self._paused = False
        self._pending = None

        self.exception = None

    def can_write_eof(self):
        return self._transport.can_write_eof()

    def get_extra_info(self, name, default = None):
        return self._transport.get_extra_info(name, default)

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

        if self._pending is not None and not self._pending.done():
            self._pending.set_result(None)

    def write(self, data):
        self._transport.write(data)

    def write_eof(self):
        self._transport.write_eof()

    def close(self):
        self._transport.close()

    @coroutine
    def drain(self):
        if not self._paused:
            return

        if self.exception is not None:
            raise self.exception

        if self._pending is not None and not self._pending.done():
            raise RuntimeError("another drain call pending")

        self._pending = asyncio.Future(loop = self._loop)

        try:
            yield from self._pending
        finally:
            self._pending = None


class StreamingProtocol(asyncio.Protocol):
    def __init__(self, connection_callback, loop = None):
        assert asyncio.iscoroutinefunction(connection_callback)        

        self._transport = None
        self._connection_callback = connection_callback
        self._loop = loop or asyncio.get_event_loop()

        self.reader = None
        self.writer = None

    def connection_made(self, transport):
        self._transport = transport

        self.reader = StreamReader(transport, loop = self._loop)
        self.writer = StreamWriter(transport, loop = self._loop)

        task = self._connection_callback(self.reader, self.writer)
        self._loop.create_task(task)

    def data_received(self, chunk):
        self.reader.feed(chunk)

    def eof_received(self):
        self.reader.feed_eof()
        return True

    def connection_lost(self, exception):
        if exception is None:
            self.reader.feed_eof()

            exception = ConnectionResetError("connection lost")

            self.writer.exception = exception

            if self.writer._pending is not None and not self.writer._pending.done():
                self.writer._pending.set_exception(exception)

            return

        self.reader.exception = exception
        self.writer.exception = exception

        if self.reader._pending is not None and not self.reader._pending.done():
            self.reader._pending.set_exception(exception)

        if self.writer._pending is not None and not self.writer._pending.done():
            self.writer._pending.set_exception(exception)

    def pause_writing(self):
        self.writer.pause()

    def resume_writing(self):
        self.writer.resume()
