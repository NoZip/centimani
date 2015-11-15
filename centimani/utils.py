import io
import re

from asyncio import coroutine
from datetime import datetime

if "StopAsyncIteration" not in dir(__builtins__):
    from asyncioplus.utils import StopAsyncIteration


# HTTP statuses
# non-annotted statuses are defined in RFC7231
# see: http://www.iana.org/assignments/http-status-codes/http-status-codes.xhtml
HTTP_STATUSES = {
    100: "Continue",
    101: "Switching Protocols",
    102: "Processing", #RFC2518

    200: "OK",
    201: "Created",
    202: "Accepted",
    203: "Non-Authoritative Information",
    204: "No Content",
    205: "Reset Content",
    206: "Partial Content", #RFC7233
    207: "Multi-Status", #RFC4918
    208: "Already Reported", #RFC5842
    226: "IM Used", #RFC3229

    300: "Multiple Choices",
    301: "Moved Permanently",
    302: "Moved Temporarily",
    303: "See Other",
    304: "Not Modified", #RFC7232
    305: "Use Proxy",
    307: "Temporary redirect",
    308: "Permanent Redirect", #RFC7538

    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    407: "Proxy Authentification Required", #RFC7235
    408: "Request Time-out",
    409: "Conflict",
    410: "Gone",
    411: "Length Required",
    412: "Precondition Failed", #RFC7232
    413: "Payload Too Large",
    414: "URI Too Long",
    415: "Unsupported Media Type",
    416: "Range Not Satisfiable", #RFC7233
    417: "Exception Failed",
    421: "Misdirected Request", #RFC7540
    422: "Unprocessable Entity", #RFC4918
    423: "Locked", #RFC4918
    424: "Failed Dependency", #RFC4918
    426: "Upgrade Required",
    428: "Precondition Required", #RFC6585
    429: "Too Many Request", #RFC6585
    431: "Request Header Fields Too Large", #RFC6585

    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Time-out",
    505: "HTTP Version Not Supported",
    506: "Variant Also Negociates", #RFC2295
    507: "Insufficient Storage", #RFC4918
    508: "Loop Detected", #RFC5842
    510: "Not Extended", #RFC2774
    511: "Network Authentification Required" #RFC6585
}


HTTP_METHODS = frozenset((
    "GET", "HEAD", "POST", "OPTIONS", "CONNECT", "TRACE", "PUT", "PATCH", "DELETE"
))


#=============================#
# RFC 1123 datetime functions #
#=============================#

WEEKDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_rfc1123_datetime = r"^[A-Z][a-z]{2}, ([0-9]{1,2}) ([A-Z][a-z]{2}) ([0-9]{2}|[0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT$"
RFC1123_DATETIME_REGEX = re.compile(_rfc1123_datetime)

def is_rfc1123_datetime(value):
    assert isinstance(value, str)
    return RFC1123_DATETIME_REGEX.match(value) is not None

def rfc1123_datetime_encode(dt):
    assert isinstance(dt, datetime)

    return "{0}, {1} {2} {3} {4:02}:{5:02}:{6:02} {7}".format(
        WEEKDAY[dt.weekday()],
        dt.day,
        MONTH[dt.month - 1],
        dt.year,
        dt.hour,
        dt.minute,
        dt.second,
        "GMT"
    )

def rfc1123_datetime_decode(string):
    match = RFC1123_DATETIME_REGEX.match(string)

    if not match:
        msg = "{} is not a valid RFC1123 date".format(string)
        raise ValueError(msg)

    groups = match.groups()

    return datetime(
        year = int(groups[2]) if len(groups[2]) == 4 else int("19" + groups[2]),
        month = MONTH.index(groups[1]) + 1,
        day = int(groups[0]),
        hour = int(groups[3]),
        minute = int(groups[4]),
        second = int(groups[5])
    )


class BufferedBodyReader:
    def __init__(self, reader, body_size = None, block_size = io.DEFAULT_BUFFER_SIZE):
        self.reader = reader
        self.body_size = body_size
        self.block_size = block_size

        self.current_block = 0

        if self.body_size is not None:
            self.block_count, self.last_block_size = divmod(self.body_size, self.block_size)

        self.bytes_read = 0

    @property
    def is_complete(self):
        return self.bytes_read == self.body_size

    @coroutine
    def __aiter__(self):
        return self

    @coroutine
    def __anext__(self):
        if self.body_size is not None:
            if self.current_block < self.block_count:
                block_size = self.block_size
            elif self.current_block == self.block_count:
                block_size = self.last_block_size
            elif self.current_block > self.block_count:
                block_size = 0
        else:
            block_size = self.block_size

        if block_size == 0:
            raise StopAsyncIteration

        block = yield from self.reader.read(block_size)

        if block == b"":
            raise StopAsyncIteration

        self.bytes_read += len(block)
        self.current_block += 1

        return block


class ChunkedBodyReader:
    def __init__(self, reader):
        self.reader = reader

        self.current_chunk = 0
        # self.body_size = 0

    @coroutine
    def __aiter__(self):
        return self

    @coroutine
    def __anext__(self):
        chunk_header = yield from self.reader.read_until(b"\r\n")
        chunk_size = int(chunk_header, base = 16)

        if chunk_size == 0:
            raise StopAsyncIteration

        chunk = yield from self.reader.read(chunk_size + 2)
        chunk, check_crlf = chunk[:-2], chunk[-2:]

        if check_crlf != b"\r\n":
            raise Exception("chunk not followed by CRLF")

        if chunk == b"":
            # TODO: parse trailer headers
            raise StopAsyncIteration

        # self.body_size += len(chunk)
        self.current_chunk += 1

        return chunk
