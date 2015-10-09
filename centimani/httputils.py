import re
from datetime import datetime
from collections import defaultdict as MultiMap


# HTTP 1.1 status codes

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
    301: "Moved Permanentely",
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


# RFC 1123 datetime functions

WEEKDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

REGEX_RFC1123 = re.compile(r"[A-Z][a-z]{2}, ([0-9]{1,2}) ([A-Z][a-z]{2}) ([0-9]{2}|[0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT")

def rfc1123_datetime_print(dt):
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

def rfc1123_datetime_parse(string):
    match = REGEX_RFC1123.match(string)

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


# Utility classes

class HTTPHeaders(MultiMap):
    """
    Used to handle HTTP headers.
    """

    HEADER_REGEX = re.compile(r"^([a-zA-Z-]+):(.+)$")

    def __init__(self, **kwargs):
        super().__init__(list)

        for name, value in kwargs.items():
            normalized_name = "-".join(w.capitalize() for w in name.split("_"))

            self.add(normalized_name, value)

    def get(self, name):
        """
        Retrieves header value.
        Return one value if the header is unique, and a list othervise.
        """ 

        item = self.__getitem__(name)
        
        if len(item) == 1:
            return item[0]
        else:
            return item

    def add(self, name, value):
        """
        Add one or multiples values to the headers.
        """

        self.__getitem__(name).append(value)

    def parse_line(self, line):
        match = self.HEADER_REGEX.match(line)

        if not match:
            raise ValueError("Header line not well formed: {}".format(line))

        name, value = (s.strip() for s in line.split(':', maxsplit=1))
        self.__getitem__(name).append(value)


class Request:
    __slots__ = ("version", "method", "path", "headers")

    def __init__(self,
        version="1.1",
        method="GET",
        path="/",
        headers=HTTPHeaders()
    ):
        self.version = version
        self.method = method
        self.path = path
        self.headers = headers


class Response:
    __slots__ = ("version", "status", "headers")

    def __init__(self,
        version="1.1",
        status=200,
        headers=HTTPHeaders()
    ):
        self.version = version
        self.status = status
        self.headers = headers
