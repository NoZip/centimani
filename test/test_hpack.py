import unittest

from centimani.http2 import huffman, hpack


class HpackTestCase(unittest.TestCase):
    def test_huffman(self):
        test = b"\xF1\xE3\xC2\xE5\xF2\x3A\x6B\xA0\xAB\x90\xF4\xFF"
        s = b"www.example.com"
        encoded = huffman.encode(s)
        self.assertEqual(encoded, test)
        decoded = huffman.decode(encoded)
        self.assertEqual(decoded, s)

    def test_hpack_c3(self):
        encoder = hpack.HpackEncoder(indexing_policy=lambda header_fields: True)
        decoder = hpack.HpackDecoder()

        #---------------#
        # First request #
        #---------------#

        test1 = b"\x82\x86\x84\x41\x0F\x77\x77\x77\x2E\x65\x78\x61\x6D\x70\x6C\x65\x2E\x63\x6F\x6D"
        header_fields1 = [
            (":method", "GET"),
            (":scheme", "http"),
            (":path", "/"),
            (":authority", "www.example.com"),
        ]

        encoded1 = b"".join(map(encoder.encode, header_fields1))
        self.assertEqual(encoded1, test1)
        decoded1 = list(decoder.decode(encoded1))
        self.assertEqual(decoded1, header_fields1)

        test_dynamic_table = [(":authority", "www.example.com")]
        self.assertEqual(decoder._context._dynamic_table, test_dynamic_table)
        self.assertEqual(decoder._context._dynamic_table, encoder._context._dynamic_table)

        #----------------#
        # Second request #
        #----------------#

        test2 = b"\x82\x86\x84\xBE\x58\x08\x6E\x6F\x2D\x63\x61\x63\x68\x65"
        header_fields2 = [
            (":method", "GET"),
            (":scheme", "http"),
            (":path", "/"),
            (":authority", "www.example.com"),
            ("cache-control", "no-cache"),
        ]

        encoded2 = b"".join(map(encoder.encode, header_fields2))
        self.assertEqual(encoded2, test2)
        decoded2 = list(decoder.decode(encoded2))
        self.assertEqual(decoded2, header_fields2)

        test_dynamic_table = [
            ("cache-control", "no-cache"),
            (":authority", "www.example.com"),
        ]
        self.assertEqual(decoder._context._dynamic_table, test_dynamic_table)
        self.assertEqual(decoder._context._dynamic_table, encoder._context._dynamic_table)

        #----------------#
        # Third request #
        #----------------#

        test3 = b"\x82\x87\x85\xBF\x40\x0A\x63\x75\x73\x74\x6F\x6D\x2D\x6B\x65\x79\x0C\x63\x75\x73\x74\x6F\x6D\x2D\x76\x61\x6C\x75\x65"
        header_fields3 = [
            (":method", "GET"),
            (":scheme", "https"),
            (":path", "/index.html"),
            (":authority", "www.example.com"),
            ("custom-key", "custom-value"),
        ]

        encoded3 = b"".join(map(encoder.encode, header_fields3))
        self.assertEqual(encoded3, test3)
        decoded3 = list(decoder.decode(encoded3))
        self.assertEqual(decoded3, header_fields3)

        test_dynamic_table = [
            ("custom-key", "custom-value"),
            ("cache-control", "no-cache"),
            (":authority", "www.example.com"),
        ]
        self.assertEqual(decoder._context._dynamic_table, test_dynamic_table)
        self.assertEqual(decoder._context._dynamic_table, encoder._context._dynamic_table)

    def test_hpack_c4(self):
        encoder = hpack.HpackEncoder(
            indexing_policy=lambda header_fields: True,
            huffman_policy=lambda string: True)
        decoder = hpack.HpackDecoder()

        #---------------#
        # First request #
        #---------------#

        test1 = b"\x82\x86\x84\x41\x8C\xF1\xE3\xC2\xE5\xF2\x3A\x6B\xA0\xAB\x90\xF4\xFF"
        header_fields1 = [
            (":method", "GET"),
            (":scheme", "http"),
            (":path", "/"),
            (":authority", "www.example.com"),
        ]

        encoded1 = b"".join(map(encoder.encode, header_fields1))
        self.assertEqual(encoded1, test1)
        decoded1 = list(decoder.decode(encoded1))
        self.assertEqual(decoded1, header_fields1)

        test_dynamic_table = [(":authority", "www.example.com")]
        self.assertEqual(decoder._context._dynamic_table, test_dynamic_table)
        self.assertEqual(decoder._context._dynamic_table, encoder._context._dynamic_table)

        #----------------#
        # Second request #
        #----------------#

        test2 = b"\x82\x86\x84\xBE\x58\x86\xA8\xEB\x10\x64\x9C\xBF"
        header_fields2 = [
            (":method", "GET"),
            (":scheme", "http"),
            (":path", "/"),
            (":authority", "www.example.com"),
            ("cache-control", "no-cache"),
        ]

        encoded2 = b"".join(map(encoder.encode, header_fields2))
        self.assertEqual(encoded2, test2)
        decoded2 = list(decoder.decode(encoded2))
        self.assertEqual(decoded2, header_fields2)

        test_dynamic_table = [
            ("cache-control", "no-cache"),
            (":authority", "www.example.com"),
        ]
        self.assertEqual(decoder._context._dynamic_table, test_dynamic_table)
        self.assertEqual(decoder._context._dynamic_table, encoder._context._dynamic_table)

        #----------------#
        # Third request #
        #----------------#

        test3 = b"\x82\x87\x85\xBF\x40\x88\x25\xA8\x49\xE9\x5B\xA9\x7D\x7F\x89\x25\xA8\x49\xE9\x5B\xB8\xE8\xB4\xBF"
        header_fields3 = [
            (":method", "GET"),
            (":scheme", "https"),
            (":path", "/index.html"),
            (":authority", "www.example.com"),
            ("custom-key", "custom-value"),
        ]

        encoded3 = b"".join(map(encoder.encode, header_fields3))
        self.assertEqual(encoded3, test3)
        decoded3 = list(decoder.decode(encoded3))
        self.assertEqual(decoded3, header_fields3)

        test_dynamic_table = [
            ("custom-key", "custom-value"),
            ("cache-control", "no-cache"),
            (":authority", "www.example.com"),
        ]
        self.assertEqual(decoder._context._dynamic_table, test_dynamic_table)
        self.assertEqual(decoder._context._dynamic_table, encoder._context._dynamic_table)


if __name__ == '__main__':
    unitest.main()
