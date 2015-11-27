"""This module contains miscellaneous functions and classes used by many
other modules of centimani.


Constants
=========

:HTTP_STATUSES: A mapping of all HTTP statuses mapped to their reason.
:HTTP_METHODS: A set of all HTTP methods.


RFC1123 datetime conversion
===========================

RFC1123 defines a date format used in HTTP. This module part defines
the functions to deal with this date format.

:is_rfc1123_datetime: Checks if a string is a valid datetime.
:rfc1123_datetime_encode: convert a ``datetime`` instance to a
    datetime string.
:rfc1123_datetime_decode: Parses a string in order to extract a
    ``datetime`` from it.
"""

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


SUPPORTED_METHODS = frozenset(("GET", "HEAD", "POST", "OPTIONS",
    "PUT", "PATCH", "DELETE"))


#=============================#
# RFC 1123 datetime functions #
#=============================#

WEEKDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

MONTH = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul",
    "Aug", "Sep", "Oct", "Nov", "Dec")

_RFC1123_DATETIME = r"^[A-Z][a-z]{2}, "
_RFC1123_DATETIME += r"([0-9]{1,2}) ([A-Z][a-z]{2}) ([0-9]{2}|[0-9]{4}) "
_RFC1123_DATETIME += r"([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT$"
RFC1123_DATETIME_REGEX = re.compile(_RFC1123_DATETIME)

def is_rfc1123_datetime(string):
    """Checks if ``string`` is a valid datetime.""" 
    assert isinstance(string, str)
    return RFC1123_DATETIME_REGEX.match(string) is not None

def rfc1123_datetime_encode(dt):
    """Convert a ``datetime`` object into a RFC1123 datetime string."""
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
    """Parse ``string`` to get a corresponding datetime object.
    Raise a ``ValueError if ``string```is not a valid RFC1123 datetime
    representation.
    """
    assert isinstance(string, str)

    match = RFC1123_DATETIME_REGEX.match(string)

    if not match:
        msg = "{} is not a valid RFC1123 datetime".format(string)
        raise ValueError(msg)

    groups = match.groups()
    year = int(groups[2]) if len(groups[2]) == 4 else int("19" + groups[2])

    return datetime(
        year=year,
        month=MONTH.index(groups[1]) + 1,
        day=int(groups[0]),
        hour=int(groups[3]),
        minute=int(groups[4]),
        second=int(groups[5])
    )
