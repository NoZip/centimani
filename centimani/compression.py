import zlib

from asyncio import coroutine

SUPPORTED_COMPRESSIONS = frozenset(("deflate", "gzip"))

class DecompressPipe:
    @staticmethod
    def _get_decoder(name):
        if name == "deflate":
            return zlib.decompressobj(wbits = -zlib.MAX_WBITS)
        elif name == "gzip" or name == "x-gzip":
            return zlib.decompressobj(wbits = zlib.MAX_WBITS|16)
        else:
            raise Exception(name + " compression not supported")

    def __init__(self, reader, encoding_chain):
        self._reader = reader
        self._decoder_chain = [self._get_decoder(name) for name in reversed(encoding_chain)]

    @coroutine
    def __aiter__(self):
        return self

    @coroutine
    def __anext__(self):
        try:
            chunk = yield from self._reader.__anext__()
        except StopAsyncIteration:
            raise StopAsyncIteration

        for decoder in self._decoder_chain:
            chunk = decoder.decompress(chunk)

        return chunk
