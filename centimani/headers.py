import re

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from .utils import is_rfc1123_datetime, rfc1123_datetime_encode


_header_field = rb"^([^\x00-\x20\x7F\"(),/:;<=>?@[\]{}]+):([\t !-~]*)$"
HEADER_FIELD_REGEX = re.compile(_header_field)


class HeaderParseError(Exception):
    pass


class Headers(defaultdict):
    """
    Used to handle HTTP headers.

    >>> HTTPHeaders(content_length=23, transfert_encoding=["chunked", "gzip"])
    {'transfert-encoding': ['chunked', 'gzip'], 'content-length': ['23']}
    """

    @classmethod
    def split_field_value(cls, value):
        if "," in value and not is_rfc1123_datetime(value):
            return [v.strip() for v in value.split(",")]
        else:
            return value

    @classmethod
    def parse_line(cls, line):
        """
        Parse an header line, return a tuple (name, value).
        """

        match = HEADER_FIELD_REGEX.match(line)

        if not match:
            raise HeaderParseError(line)

        name, value = map(lambda s: s.decode("ascii"), match.groups(b""))
        name = name.lower()
        value = cls.split_field_value(value.strip())

        return (name, value)

    def __init__(self, **kwargs):
        """
        Initialize headers with named parameters.

        Named parameters will be converted from "thing_header" to "thing-header"
        in order to normalize headers names.
        """
        super().__init__(list)

        for name, value in kwargs.items():
            normalized_name = "-".join(name.split("_")).lower()
            self.add(normalized_name, value)

    def __repr__(self):
        return "Headers" + repr(dict(self))

    def parse_lines(self, lines):
        assert isinstance(lines, Iterable)

        for line in lines:
            name, values = self.parse_line(line)
            self.add(name, values)

    def set(self, name, value):
        """
        Set an header field to an unique value.
        """

        self.__getitem__(name).clear()
        self.add(name, value)

    def add(self, name, value):
        """
        Add one or multiples values to the headers.
        """
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
        for name, values in self.items():
            if name == "set-cookie":
                # Set-Cookie special case
                for value in values:
                    yield (name, value)
            else:
                yield (name, ", ".join(values))

    def http_encode(self):
        """
        Returns all headers as a string containing each header line.
        """
        lines = ("{0}: {1}\r\n".format(name, value) for name, value in self.header_fields())
        return "".join(lines)
