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


class InvalidChunkError(Exception):
    pass


class BufferedBodyReader:
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
        self._is_complete = False

        if self._body_size is not None:
            tmp = divmod(self._body_size, self._block_size)
            self._block_count, self._last_block_size = tmp

    @property
    def is_complete(self):
        """Checks if all data was read.

        If this function returns False, there is two options:
        * reading is not finished yet.
        * an EOF was received before all data was read.
        """
        return self._is_complete

    async def __aiter__(self):
        return self

    async def __anext__(self):
        """Returns the next read block."""
        if self._is_complete:
            raise StopAsyncIteration
        
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
            self._is_complete = True
            raise StopAsyncIteration

        block = await self._reader.read(block_size)

        if block == b"":
            if self._body_size is None:
                self._is_complete = True
                raise StopAsyncIteration
            else:
                raise EOFError

        self._current_block += 1

        return block


class ChunkedBodyReader:
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
        self._is_complete = False

        self._body_size = 0
        self._headers = Headers()

    @property
    def is_complete(self):
        return self._is_complete

    @property
    def body_size(self):
        return self._body_size

    @property
    def headers(self):
        return self._headers

    async def __aiter__(self):
        return self

    async def __anext__(self):
        """Returns the next chunk of data."""
        if self._is_complete:
            raise StopAsyncIteration

        chunk_header = await self._reader.read_until(b"\r\n")
        chunk_size = int(chunk_header, base=16)

        if chunk_size == 0:
            await self._parse_trailer_headers()
            self._is_complete = True
            raise StopAsyncIteration

        chunk = await self._reader.read_until(b"\r\n")

        if len(chunk) != chunk_size:
            raise InvalidChunkError("chunk has a wrong size")

        self._body_size += len(chunk)
        self._current_chunk += 1

        return chunk

    async def _parse_trailer_headers(self):
        """When the last chunk of data is received, this method parse
        the trailing headers, if present.
        """
        trailer_field = await self._reader.read_until(b"\r\n")
        while trailer_field:
            name, content = self._headers.parse_line(trailer_field)
            self._headers.add(name, content)
