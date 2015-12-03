"""Body readers are classes that implements the asynchronous iterator
interface, and are useful for reading data from a stream without worrying
about the encoding or blocking operations.

:BufferedBodyReader:
    Its main purpose is to reduce the blocking overhead caused by an
    unique ``read`` call that read many bytes. This class split read
    data into many blocks.

:ChunkedBodyReader:
    This class is a wrapper around a stream in order to support the
    chunked encoding used in HTTP.
"""
import io

from asyncio import coroutine
from centimani.headers import Headers

if "StopAsyncIteration" not in dir(__builtins__):
    from asyncioplus import StopAsyncIteration

class AbstractBodyReader:
    @coroutine
    def read_into(self, stream):
        running = True
        while running:
            try:
                data = yield from self.__anext__()
            except StopAsyncIteration:
                running = False
            else:
                stream.write(data)


class BufferedBodyReader(AbstractBodyReader):
    """This class is used to read data from a ``StreamReader`` in blocks,
    in order to avoid event loop blocking.

    This class is meant to be used as an asynchronous iterator, and sould
    be used with the ``async for`` statement.

    Attributes:
        :bytes_read: The number of bytes read from the stream, useful
            to check if the reading is complete.
    """
    def __init__(self,
        reader,
        body_size=None,
        block_size=io.DEFAULT_BUFFER_SIZE
    ):
        """Initialize a ``BufferedBodyReader``

        Parameters:
            :reader: The ``StreamReader`` used to read data.
            :body_size: The number of bytes to read, as defined in the
                content-length header for exemple.
            :block_size: The block size used for buffering.
        """
        self._reader = reader
        self._body_size = body_size
        self._block_size = block_size
        self._current_block = 0
        self._eof = False

        if self._body_size is not None:
            tmp = divmod(self._body_size, self._block_size)
            self._block_count, self._last_block_size = tmp

        self.bytes_read = 0

    @property
    def is_complete(self):
        """Checks if all data was read.

        If this function returns False, there is two options:
        * reading is not finished yet.
        * an EOF was received before all data was read.
        """
        return self.bytes_read == self._body_size

    @coroutine
    def __aiter__(self):
        return self

    @coroutine
    def __anext__(self):
        """Returns the next read block."""
        if self._body_size is not None:
            if self._current_block < self._block_count:
                block_size = self._block_size
            elif self._current_block == self._block_count:
                block_size = self._last_block_size
            elif self._current_block > self._block_count:
                block_size = 0
        else:
            block_size = self._block_size

        if block_size == 0:
            self._eof = True
            raise StopAsyncIteration

        block = yield from self._reader.read(block_size)

        if block == b"":
            self._eof = True
            raise StopAsyncIteration

        self.bytes_read += len(block)
        self._current_block += 1

        return block


class ChunkedBodyReader(AbstractBodyReader):
    """This class is used to handle the chunked transfert encoding.
    
    Attributes:
        :body_size: The current number of bytes read since the beginning.
        :headers: Contains the trailing headers, if any.
    """

    def __init__(self, reader):
        """Initialize a ``ChunkedBodyReader``.

        Parameters:
            :reader: The ``StreamReader`` used to read data.
        """
        self._reader = reader
        self._current_chunk = 0
        self._eof = False

        self.body_size = 0
        self.headers = Headers()

    @property
    def is_complete(self):
        return self._eof

    @coroutine
    def __aiter__(self):
        return self

    @coroutine
    def __anext__(self):
        """Returns the next chunk of data."""
        assert not self._eof

        chunk_header = yield from self._reader.read_until(b"\r\n")
        chunk_size = int(chunk_header, base=16)

        if chunk_size == 0:
            yield from self._parse_trailer_headers()
            self._eof = True
            raise StopAsyncIteration

        chunk = yield from self._reader.read(chunk_size)
        check_crlf = yield from self._reader.read(2)

        if len(chunk) != chunk_size:
            raise Exception("chunk has a wrong size")

        if check_crlf != b"\r\n":
            raise Exception("chunk not followed by CRLF")

        self.body_size += len(chunk)
        self._current_chunk += 1

        return chunk

    @coroutine
    def _parse_trailer_headers(self):
        """When the last chunk of data is received, this method parse
        the trailing headers, if present.

        TODO: implements this.
        """
        pass
