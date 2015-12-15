"""This module mimics ``asyncio.stream`` with additionnal features.

Additionnal features:
- ``read_until`` method, that can read until a delimiter
  is found.
"""

import asyncio


# internal StreamReader buffer maximum size
DEFAULT_READ_BUFFER_LIMIT = 1 << 16


async def open_connection(host, port, limit=None, loop=None, **kwargs):
    """Connect to a remote client and returns a (reader, writer) tuple.

    Keyword arguments will be passed to ``EventLoop.create_connection``.
    """
    if loop is None:
        loop = asyncio.get_event_loop()

    _, protocol = await loop.create_connection(
        lambda: StreamingProtocol(limit=limit, loop=loop),
        host, port,
        **kwargs
    )

    return (protocol.reader, protocol.writer)

async def start_server(
        connection_callback,
        host=None, port=None,
        limit=None,
        loop=None,
        **kwargs):
    """Start listening connections."""
    if loop is None:
        loop = asyncio.get_event_loop()

    server = await loop.create_server(
        lambda: StreamingProtocol(connection_callback, limit=limit, loop=loop),
        host, port,
        **kwargs
    )

    return server

class StreamReader:

    def __init__(self, transport, limit=None, loop=None):
        self._loop = loop or asyncio.get_event_loop() 
        self._transport = transport
        self._buffer = bytearray()
        self._eof = False
        self._pending = None
        self._limit = limit or DEFAULT_READ_BUFFER_LIMIT
        self._paused = False
        self._exception = None

    @property
    def at_eof(self):
        return (self._eof and not self._buffer)

    async def _wait(self, parameter):
        if self._pending is not None:
            parameter, event = self._pending
            if not event.done():
                raise RuntimeError("another read call already pending")

        event = asyncio.Future(loop=self._loop)
        self._pending = (parameter, event)

        try:
            await event
        finally:
            self._pending = None

    def _maybe_pause(self):
        if not self._paused and len(self._buffer) > self._limit:
            try:
                self._transport.pause_reading()
            except NotImplementedError:
                raise RuntimeError("transport cannot be paused")

            # pausing with read call pending 
            if self._pending is not None and not self._pending[1].done():
                error = BufferError("buffer limit reached during read call")
                self._pending[1].set_exception(error)

            self._paused = True

    def _maybe_resume(self):
        if self._paused and len(self._buffer) < self._limit:
            try:
                self._transport.resume_reading()
            except NotImplementedError:
                raise RuntimeError("transport cannot be resumed")

            self._paused = False

    def set_exception(self, exception):
        assert isinstance(exception, Exception)

        self._exception = exception

        # send exception to pending read call, if any
        pending = self._pending
        if pending is not None:
            parameter, event = pending
            if not event.done():
                event.set_exception(exception)

    def feed(self, data):
        assert isinstance(data, bytes)
        assert not self._eof

        self._buffer.extend(data)
        
        # test pending read calls
        if self._pending:
            parameter, event = self._pending

            if not event.done():
                # read call
                if isinstance(parameter, int):
                    if parameter <= len(self._buffer):
                        event.set_result(None)

                # read_until call
                elif isinstance(parameter, bytes):
                    search_length = len(data) - len(parameter) - 1
                    if parameter in self._buffer[-search_length:]:
                        event.set_result(None)

        self._maybe_pause()

    def feed_eof(self):
        self._eof = True

        if self._pending is None:
            return

        # release pending read call
        parameter, event = self._pending

        if not event.done():
            event.set_result(None)

        self._pending = None

    async def read(self, count):
        assert isinstance(count, int)
        assert count >= 0

        if count == 0:
            return b""

        if self._exception is not None:
            raise self._exception

        if count > self._limit:
            raise ValueError("trying to read more bytes than buffer limit")

        if not self._eof and len(self._buffer) < count:
            await self._wait(count)

        data = bytes(self._buffer[:count])
        del self._buffer[:count]

        self._maybe_resume()

        return data

    async def read_until(self, delimiter=b"\n"):
        assert isinstance(delimiter, bytes)

        if self._exception is not None:
            raise self._exception

        if not self._eof and delimiter not in self._buffer:
            await self._wait(delimiter)

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
    def __init__(self, transport, loop=None):
        assert transport

        self._loop = loop or asyncio.get_event_loop()
        self._transport = transport
        self._pending = None
        self._paused = False

    def is_closing(self):
        return self._transport.is_closing()

    def get_extra_info(self, name, default=None):
        assert not self.is_closing()
        return self._transport.get_extra_info(name, default)

    def can_write_eof(self):
        assert not self.is_closing()
        return self._transport.can_write_eof()

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

        pending = self._pending
        if self._pending is not None:
            self._pending = None
            if not pending.done():
                pending.set_result(None)

    def write(self, data):
        assert not self.is_closing()
        self._transport.write(data)

    def write_eof(self):
        assert not self.is_closing()
        self._transport.write_eof()

    async def drain(self):
        """Waits until all data is sended to remote peer.

        Intended to be used like this:

            w.write(data)
            await w.drain()
        """
        assert not self.is_closing()

        if not self._paused:
            return

        if self._pending is not None and not self._pending.done():
            raise RuntimeError("another drain call pending")

        if self._transport is not None and self.is_closing():
            await asyncio.sleep(0)

        event = asyncio.Future(loop=self._loop)
        self._pending = event

        try:
            await event
        finally:
            self._pending = None

    def close(self):
        assert not self.is_closing()
        self._transport.close()


class StreamingProtocol(asyncio.Protocol):
    def __init__(self, connection_callback=None, limit=None, loop=None):
        self._loop = loop or asyncio.get_event_loop()
        self._connection_callback = connection_callback
        self._limit = limit

        self._transport = None
        self.reader = None
        self.writer = None

    def connection_made(self, transport):
        self._transport = transport

        self.reader = StreamReader(transport, limit=self._limit, loop=self._loop)
        self.writer = StreamWriter(transport, loop=self._loop)

        if self._connection_callback is not None:
            task = self._connection_callback(self.reader, self.writer)
            self._loop.create_task(task)

    def data_received(self, chunk):
        self.reader.feed(chunk)

    def eof_received(self):
        self.reader.feed_eof()
        return None

    def connection_lost(self, exception):
        if exception is None:
            self.reader.feed_eof()
        else:
            self.reader.set_exception(exception)

    def pause_writing(self):
        self.writer.pause()

    def resume_writing(self):
        self.writer.resume()
