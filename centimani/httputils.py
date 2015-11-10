import re
import zlib
import gzip

from io import DEFAULT_BUFFER_SIZE
from datetime import datetime
from collections import defaultdict
from collections.abc import *
from asyncio import coroutine

from asyncioplus.utils import *


# HTTP/1.1 status reasons
STATUS_REASON = {
    100: "Continue",
    101: "Switching Protocols",

    200: "OK",
    201: "Created",
    202: "Accepted",
    203: "Non-Authoritative Information",
    204: "No Content",
    205: "Reset Content",
    206: "Partial Content",
    226: "IM Used",

    300: "Multiple Choices",
    301: "Moved Permanently",
    302: "Moved Temporarily",
    303: "See Other",
    304: "Not Modified",
    305: "Use Proxy",
    307: "Temporary redirect",
    308: "Permanent Redirect",
    310: "Too Many Redirects",

    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    407: "Proxy Authentification Required",
    408: "Request Time-out",
    409: "Conflict",
    410: "Gone",
    411: "Length Required",
    412: "Precondition Failed",
    413: "Request Entity Too Large",
    414: "Request-URI Too Long",
    415: "Unsupported Media Type",
    416: "Requested Range Unsatisfiable",
    417: "Exception Failed",
    418: "I'm a Teapot",
    426: "Upgrade Required",
    428: "Precondition Required",
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",

    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Time-out",
    505: "HTTP Version Not Supported",
    506: "Variant Also Negociate",
    509: "Bandwidth Limit Exceeded",
    510: "Not Extended",
    511: "Network Authentification Required",
    520: "Web Server is returning an Unknown Error"
}


SUPPORTED_ENCODINGS = {
    "deflate": zlib.decompress,
    "gzip": gzip.decompress,
    "x-gzip": gzip.decompress
}


#=============================#
# RFC 1123 datetime functions #
#=============================#

WEEKDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

RFC1123_REGEX = re.compile(r"[A-Z][a-z]{2}, ([0-9]{1,2}) ([A-Z][a-z]{2}) ([0-9]{2}|[0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT")

def rfc1123_datetime_encode(dt):
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
    match = RFC1123_REGEX.match(string)

    if not match:
        raise ValueError("Date string not matching")

    groups = match.groups()

    return datetime(
        year = int(groups[2]) if len(groups[2]) == 4 else int("19" + groups[2]),
        month = MONTH.index(groups[1]) + 1,
        day = int(groups[0]),
        hour = int(groups[3]),
        minute = int(groups[4]),
        second = int(groups[5])
    )


#================#
# Body producers #
#================#

class FileBodyProducer:
    """
    Body producer that handle a file on disk.
    """

    has_size = True

    def __init__(self, filename, chunk_size = 2**16):
        self.filename = filename
        self.size = os.stat(filename).st_size

        self._chunk_size = chunk_size

    @coroutine
    def write(self, writer, loop = None):
        loop = loop or asyncio.get_event_loop()

        with open(self.filename, "rb") as src:
            src_socket = socket(fileno = src.fileno())

            chunk_count, last_chunk_size = divmod(self.size, self._chunk_size)

            for chunk_index in range(chunk_count):
                data = yield from loop.socket_recv(src, self._chunk_size)
                writer.write(data)

            data = yield from loop.socket_recv(src, last_chunk_size)
            writer.write(data)


class StreamBodyProducer:
    """
    Body producer that handle streams.

    If size is not given to the constructor, the client will assume that the
    body will be transferred with the chunked encoding.
    """

    def __init__(self, reader, size = None, chunk_size = 2**16):
        self.reader = reader
        self.size = size

        self._chunk_size = chunk_size

    @property
    def has_size(self):
        return self.size is not None

    @coroutine
    def write(self, writer, loop = None):
        loop = loop or asyncio.get_event_loop()

        if self.has_size():
            chunk_count, last_chunk_size = divmod(self.size, self._chunk_size)

            for chunk_index in range(chunk_count):
                data = yield from self.reader.read(self._chunk_size)
                writer.write(data)

            data = yield from self.reader.read(last_chunk_size)
            writer.write(data)
        else:
            chunk = None
            while chunk != b"":
                chunk = yield from self.reader.read(self._chunk_size)
                chunk = "{:X}\r\n".format(len(chunk)).encode("ascii") + chunk
                writer.write(chunk)


#==========================#
# Chunked transfert helper #
#==========================#

class ChunkedTransfertIterator:
    """
    TODO: test this
    """

    def __init__(self, reader):
        self._reader = reader

    @coroutine
    def __aiter__(self):
        return self

    @coroutine
    def __anext__(self):
        chunk_header = yield from self._reader.read_until(b"\r\n")
        chunk_size = int(chunk_header, base = 16)

        if chunk_size == 0:
            raise StopAsyncIteration

        chunk = yield from self._reader.read(chunk_size)
        return chunk


#================#
# HTTP Utilities #
#================#

class HeaderParseError(Exception):
    pass


HEADER_PATTERN = r"^([\w-]+): *(.+) *$"
HEADER_REGEX = re.compile(HEADER_PATTERN)

class HTTPHeaders(defaultdict):
    """
    Used to handle HTTP headers.

    >>> HTTPHeaders(content_length=23, transfert_encoding=["chunked", "gzip"])
    {'Transfert-Encoding': ['chunked', 'gzip'], 'Content-Length': ['23']}
    """

    def __init__(self, **kwargs):
        """
        Initialize headers with named parameters.

        Named parameters will be converted from "thing_header" to "Thing-Header"
        in order to normalize headers names.
        """
        super().__init__(list)

        for name, value in kwargs.items():
            normalized_name = "-".join(w.capitalize() for w in name.split("_"))
            self.add(normalized_name, value)

    def __repr__(self):
        return repr(dict(self))

    @property
    def is_chunked(self):
        if "Transfert-Encoding" in self:
            return "chunked" in self.__getitem__["Transfert-Encoding"]

        return False

    def get_one(self, name):
        """
        Retrieves header value.
        Return one value if the header is unique, and a list othervise.
        """ 

        if name not in self:
            raise KeyError(name)

        value = self.__getitem__(name)
        
        if len(value) == 1:
            return value[0]
        else:
            raise ValueError(value)

    def set(self, name, value):
        """
        Set an header field to an unique value.
        """

        self.__setitem__(name, [])
        self.add(name, value)

    def add(self, name, value):
        """
        Add one or multiples values to the headers.
        """
        assert(isinstance(name, str))

        is_str = isinstance(value, str)

        if isinstance(value, Iterable) and not is_str:
            self.__getitem__(name).extend(value)
        elif isinstance(value, datetime):
            self.__getitem__(name).append(rfc1123_datetime_encode(value))
        elif not is_str:
            self.__getitem__(name).append(str(value))
        else:
            self.__getitem__(name).append(value)

    @staticmethod
    def parse_line(line):
        """
        Parse an header line, return a tuple (name, value).
        """

        match = HEADER_REGEX.match(line)

        if not match:
            raise HeaderParseError(line)

        name, value = match.groups()

        if not RFC1123_REGEX.match(value) and "," in value:
            value = [v.strip() for v in value.split(",")]

        return (name, value)

    def http_encode(self):
        """
        Returns all headers as a string containing each header line.
        """

        string = ""
        for name, values in self.items():
            if name == "Set-Cookie":
                # Set-Cookie special case
                for value in values:
                    string += name + ": " + value + "\r\n"
            else:
                string += name + ": " + ", ".join(values) + "\r\n"

        return string
