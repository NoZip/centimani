import struct
import itertools
import functools
import enum

from . import huffman

STATIC_TABLE = (
    (":authority",), #1
    (":method", "GET"), #2
    (":method", "POST"), #3
    (":path", "/"), #4
    (":path", "/index.html"), #5
    (":scheme", "http"), #6
    (":scheme", "https"), #7
    (":status", "200"), #8
    (":status", "204"), #9
    (":status", "206"), #10
    (":status", "304"), #11
    (":status", "400"), #12
    (":status", "404"), #13
    (":status", "500"), #14
    ("accept-charset",), #15
    ("accept-encoding", "gzip, deflate"), #16
    ("accept-language",), #17
    ("accept-ranges",), #18
    ("accept",), #19
    ("access-control-allow-origin",), #20
    ("age",), #21
    ("allow",), #22
    ("authorization",), #23
    ("cache-control",), #24
    ("content-disposition",), #25
    ("content-encoding",), #26
    ("content-language",), #27
    ("content-length",), #28
    ("content-location",), #29
    ("content-range",), #30
    ("content-type",), #31
    ("cookie",), #32
    ("date",), #33
    ("etag",), #34
    ("except",), #35
    ("expires",), #36
    ("from",), #37
    ("host",), #38
    ("if-match",), #39
    ("if-modified-since",), #40
    ("if-none-match",), #41
    ("if-range",), #42
    ("if-unmodified-since",), #43
    ("last-modified",), #44
    ("link",), #45
    ("location",), #46
    ("max-forwards",), #47
    ("proxy-authenticate",), #48
    ("proxy-authorization",), #49
    ("range",), #50
    ("referer",), #51
    ("refresh",), #52
    ("retry-after",), #53
    ("server",), #54
    ("set-cookie",), #55
    ("strict-transport-security",), #56
    ("transfert-encoding",), #57
    ("user-agent",), #58
    ("vary",), #59
    ("via",), #60
    ("www-authenticate",) #61
)


IndexType = enum.Enum("IndexType", ("NONE", "NAME", "FULL"))


class HpackContext:
    """This class contains an index of most used headers fields used in
    HPACK compression.

    The index is composed of a static table, defined by the HPACK
    specification, and a dynamic table. the two tables are merged into
    an unique index.

    <---------- Index Address Space ---------->
    <-- Static Table --> <-- Dynamic Table -->
    +---+-----------+---+ +---+-----------+---+
    | 1 |    ...    | s | |s+1|    ...    |s+k|
    +---+-----------+---+ +---+-----------+---+
                          ^                   |
                          |                   V
                    Insertion Point       Dropping Point
    """

    @staticmethod
    def _entry_size(entry):
        """Computes an entry size."""
        return 32 + sum(len(s.encode("ascii")) for s in entry)

    def __init__(self, limit=4096, max_size=None):
        assert isinstance(limit, int)
        assert max_size is None or isinstance(max_size, int)
        assert max_size is None or max_size < limit

        self._limit = limit
        self._max_size = max_size or limit

        self._dynamic_table = []
        self._size = 0

    #------------------#
    # Container Mixins #
    #------------------#

    def __getitem__(self, index):
        assert isinstance(index, int)
        assert index > 0

        index -= 1

        if index < len(STATIC_TABLE):
            return STATIC_TABLE[index]
        else:
            return self._dynamic_table[index - len(STATIC_TABLE)]

    def __iter__(self):
        return itertools.chain(STATIC_TABLE, self._dynamic_table)

    def __contains__(self, value):
        return value in self.__iter__()

    def __len__(self):
        return len(STATIC_TABLE) + len(self._dynamic_table)

    #-------------------#
    # Max Size Managing #
    #-------------------#

    def get_limit(self):
        """Get current dynamic table size limit as set by the protocol."""
        return self._limit

    def set_limit(self, value):
        """Set current dynamic table size limit.

        If the new size is inferior than the max size managed by HPACK,
        this max size ids reduced to fit.
        """
        self._limit = value

        if self._max_size > self._limit:
            self.set_max_size(self._limit)

    # property for user-friendly access
    limit = property(get_limit, set_limit)

    def get_max_size(self):
        """Get current maximum size of the dynamic table, as set by HPACK."""
        return self._max_size

    def set_max_size(self, value):
        """Set current max size of the dynamic table.

        If the new maximum size is superior to the protocol limit size,
        a ``ValueError`` is raised. If the dynamic table is bigger than
        the given size, entries are removed until the table fits into
        the new given max size.
        """
        if value > self._limit:
            msg = "max size must be lower than {}".format(self._limit)
            raise ValueError(msg)

        self._max_size = value

        while self._size > self._max_size:
            entry = self._dynamic_table.pop(-1)
            self._size -= self._entry_size(entry)

    # property for user-friendly access
    max_size = property(get_max_size, set_max_size)

    #--------------------#
    # Index Manipulation #
    #--------------------#

    def get_index(self, header_field):
        """Returns the index of the ``header_field`` as (index type, index)."""
        for index, entry in enumerate(self.__iter__()):
            if entry == header_field:
                return (IndexType.FULL, index + 1)

        for index, entry in enumerate(self.__iter__()):
            if entry[0] == header_field[0]:
                return (IndexType.NAME, index + 1)

        return (IndexType.NONE, None)

    def add(self, header_field):
        """Adds an entry into the dynamic table.

        In order to fit into the max size constraint, entries will be
        dropped from the dynamic table until the new entry fits.
        If the entry size is larger than the context max_size, all entries in
        the dynamic table are discarded.
        """
        self._dynamic_table.insert(0, header_field)
        self._size += self._entry_size(header_field)

        while self._size > self._max_size:
            existant_entry = self._dynamic_table.pop(-1)
            self._size -= self._entry_size(existant_entry)


class HpackEncoder:

    def __init__(self, context=None, indexing_policy=None, huffman_policy=None):
        self._context = context or HpackContext()
        self._indexing_policy = indexing_policy or (lambda header_field: False)
        self._huffman_policy = huffman_policy or (lambda string: False)

    def _encode_int(self, value, prefix_length, bit_pattern=0x00):
        assert isinstance(value, int)
        assert isinstance(prefix_length, int)
        assert isinstance(bit_pattern, int)
        assert 0 < prefix_length < 8
        assert 0x00 <= bit_pattern < 0x100

        mask = (1 << prefix_length) - 1
        data = bytearray()

        if value < mask:
            data.append(value|bit_pattern)
        else:
            data.append(mask|bit_pattern)
            value -= mask

            while value >= 128:
                value, chunk = divmod(value, 128)
                chunk |= 0x80
                data.append(chunk)

            data.append(value)

        return bytes(data)

    def _encode_string(self, string):
        assert isinstance(string, str)

        huffman_encode = self._huffman_policy(string)
        ascii_string = string.encode("ascii")
        data = bytearray()

        if not huffman_encode:
            encoded_string = ascii_string
            encoded_length = self._encode_int(len(string), 7)

        else:
            encoded_string = huffman.encode(ascii_string)
            encoded_length = self._encode_int(len(encoded_string), 7, 0x80)

        return b"".join((encoded_length, encoded_string))

    def encode(self, header_field):
        data = bytearray()
        name, value = header_field
        index_type, index = self._context.get_index(header_field)

        if index_type is not IndexType.FULL:
            is_indexable = self._indexing_policy(header_field)
        else:
            is_indexable = False

        if is_indexable:
            self._context.add(header_field)

        if index_type is IndexType.NONE:
            if is_indexable:
                data.append(0x40)
            else:
                data.append(0x00)

            encoded_name = self._encode_string(name)
            encoded_value = self._encode_string(value)
            data.extend(encoded_name)
            data.extend(encoded_value)

        elif index_type is IndexType.NAME:
            if is_indexable:
                encoded_index = self._encode_int(index, 6, 0x40)
            else:
                encoded_index = self._encode_int(index, 4)

            encoded_value = self._encode_string(value)
            data.extend(encoded_index)
            data.extend(encoded_value)

        elif index_type is IndexType.FULL:
            encoded_index = self._encode_int(index, 7, 0x80)
            data.extend(encoded_index)

        return bytes(data)


class HpackDecoder:
    @classmethod
    def _decode_int(cls, iterator, prefix_length, first_byte=None):
        mask = (1 << prefix_length) - 1

        if first_byte is None:
            first_byte = next(iterator)

        first_byte &= mask

        if first_byte < mask:
            return first_byte

        else:
            chunk_shift = 0
            value = first_byte

            byte = 0x80
            while byte & 0x80:
                byte = next(iterator)
                value += (byte & 0x7F) << chunk_shift
                chunk_shift += 7

            return value

    @classmethod
    def _decode_string(cls, iterator, first_byte=None):
        if first_byte is None:
            first_byte = next(iterator)

        is_huffman = bool(first_byte & 0x80)
        length = cls._decode_int(iterator, 7, first_byte)
        string = bytes(itertools.islice(iterator, length))

        if is_huffman:
            string = huffman.decode(string)

        return string.decode("ascii")

    def __init__(self, context=None):
        self._context = context or HpackContext()

    def decode(self, iterable):
        iterator = iter(iterable)

        for byte in iterator:
            incremental_indexing = False
            never_indexed = False
            header_field = None

            if byte & 0x80:
                #indexed pair
                index = self._decode_int(iterator, 7, byte)
                header_field = self._context[index]

            elif byte & 0x40:
                # incremental indexing
                incremental_indexing = True

                if byte & 0x3F:
                    # indexed name
                    name_index = self._decode_int(iterator, 6, byte)
                    name = self._context[name_index][0]
                else:
                    name = self._decode_string(iterator)

                value = self._decode_string(iterator)
                header_field = (name, value)

            elif byte & 0x20:
                # dynamic size update
                new_size = self._decode_int(iterator, 5, byte)
                self._context.max_size = new_size

            elif byte & 0x10:
                # never indexed
                never_indexed = True

                if byte & 0x0F:
                    name_index = self._decode_int(iterator, 4, byte)
                    name = self._context[name_index][0]
                else:
                    name = self._decode_string(iterator)

                value = self._decode_string(iterator)
                header_field = (name, value)

            else:
                # no indexing
                if byte & 0x0F:
                    name_index = self._decode_int(iterator, 4, byte)
                    name = self._context[name_index]
                else:
                    name = self._decode_string(iterator)

                value = self._decode_string(iterator)
                header_field = (name, value)
                pass

            if header_field:
                if incremental_indexing:
                    self._context.add(header_field)

                yield header_field
