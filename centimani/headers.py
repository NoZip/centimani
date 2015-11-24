"""This module contain a ``Headers`` class that handle the storage
of HTTP header fields as defined in RFC7230.
"""

import re

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from centimani.utils import is_rfc1123_datetime, rfc1123_datetime_encode


_HEADER_FIELD = rb"^([^\x00-\x20\x7F\"(),/:;<=>?@[\]{}]+):([\t !-~]*)$"
HEADER_FIELD_REGEX = re.compile(_HEADER_FIELD)


class HeaderParseError(Exception):
    """Raised when an header line can't be parsed."""
    pass


class Headers(defaultdict):
    """
    Used to store HTTP headers fields.

    Fields names are normalized to be lowercase, like this:"content-length".
    The values stored in this data structure are lists, actually HTTP
    headers can have multiple values, so a single value field will be
    a list with only one element.
    
    Usage:
    >>> HTTPHeaders(content_length=23, transfert_encoding=["chunked", "gzip"])
    {'transfert-encoding': ['chunked', 'gzip'], 'content-length': ['23']}
    """

    @classmethod
    def split_field_content(cls, string):
        """Returns the string splitted at the commas."""
        if "," in string and not is_rfc1123_datetime(string):
            return [v.strip() for v in string.split(",")]
        else:
            return string

    @classmethod
    def parse_line(cls, line):
        """Parse a header line, returns a (name, content) tuple."""
        assert isinstance(line, bytes)

        match = HEADER_FIELD_REGEX.match(line)

        if not match:
            raise HeaderParseError(line)

        name, content = (s.decode("ascii") for s in match.groups(b""))
        name = name.lower()
        content = cls.split_field_content(content)

        return (name, content)

    def __init__(self, **kwargs):
        """Initialize the ``Headers``.

        Keyword arguments are used to fill the mapping. snake case names
        like "content_length will be converted to "content-length" like
        normalized names.
        """
        super().__init__(list)

        for name, value in kwargs.items():
            normalized_name = "-".join(name.split("_")).lower()
            self.add(normalized_name, value)

    def __repr__(self):
        return "Headers" + repr(dict(self))

    def parse_lines(self, lines):
        """Parse a sequence of lines add them to the headers."""
        assert isinstance(lines, Iterable)

        for line in lines:
            name, values = self.parse_line(line)
            self.add(name, values)

    def set(self, name, value):
        """Set an header field to an unique value."""
        self.__getitem__(name).clear()
        self.add(name, value)

    def add(self, name, value):
        """Add one or multiples values to the headers."""
        assert isinstance(name, str)

        if isinstance(value, str):
            self.__getitem__(name).append(value)
        elif isinstance(value, Iterable):
            self.__getitem__(name).extend(value)
        elif isinstance(value, datetime):
            self.__getitem__(name).append(rfc1123_datetime_encode(value))
        else:
            self.__getitem__(name).append(str(value))

    def header_fields(self):
        """Yields (name, value) pairs for each field in the headers.

        Usually, only one tuple with the same name should be yielded,
        at the exception of the "set-cookie" field name, that could be
        yielded multiple times.
        """
        for name, values in self.items():
            if name == "set-cookie":
                # Set-Cookie special case
                for value in values:
                    yield (name, value)
            else:
                yield (name, ", ".join(values))

    def http_encode(self):
        """Returns all headers as a string containing each header line."""
        tmp = self.header_fields()
        lines = ("{0}: {1}\r\n".format(name, value) for name, value in tmp)
        return "".join(lines)
