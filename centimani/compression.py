import zlib

from asyncio import coroutine

DECOMPRESSOR_MAP = {
    "gzip": zlib.decompressobj(wbits = zlib.MAX_WBITS|16),
    "deflate": zlib.decompressobj(wbits = -zlib.MAX_WBITS),
}

class DecompressPipe:
    def __init__(self, aiterator, encoding_chain):
        self._aiterator = aiterator
        self._decoder_chain = [DECOMPRESSOR_MAP[name] for name in reversed(encoding_chain)]

    @coroutine
    def __aiter__(self):
        self._aiterator = yield from self._aiterator.__aiter__()
        return self

    @coroutine
    def __anext__(self):
        try:
            data = yield from self._aiterator.__anext__()
        except StopAsyncIteration:
            raise StopAsyncIteration

        for decoder in self._decoder_chain:
            data = decoder.decompress(data)

        return data
