"""This modules is used to process Huffman strings as defined in RFC7541"""

import io


# canonical Huffman codebook defined in RFC7541
HUFFMAN_CODEBOOK = (
    (0x1FF8, 13), #0 Null
    (0x7FFFD8, 23), #1 Start Of Heading
    (0xFFFFFE2, 28), #2 Start Of Text
    (0xFFFFFE3, 28), #3 End Of Text
    (0xFFFFFE4, 28), #4 End Of Transmission
    (0xFFFFFE5, 28), #5 Enquiry
    (0xFFFFFE6, 28), #6 Acknowledge
    (0xFFFFFE7, 28), #7 Bell
    (0xFFFFFE8, 28), #8 Backspace
    (0xFFFFEA, 24), #9 Horizontal Tab
    (0x3FFFFFFC, 30), #10 Line Feed
    (0xFFFFFE9, 28), #11 Vertical Tab
    (0xFFFFFEA, 28), #12 Form Feed
    (0x3FFFFFFD, 30), #13 Carriage Return
    (0xFFFFFEB, 28), #14 Shift Out
    (0xFFFFFEC, 28), #15 Shift In
    (0xFFFFFED, 28), #16 Data Link Escape
    (0xFFFFFEE, 28), #17 Device Control 1
    (0xFFFFFEF, 28), #18 Device Control 2
    (0xFFFFFF0, 28), #19 Device Control 3
    (0xFFFFFF1, 28), #20 Device Control 4
    (0xFFFFFF2, 28), #21 Negative Acknowledge
    (0x3FFFFFFE, 30), #22 Synchronous Idle
    (0xFFFFFF3, 28), #23 End of Transmission Block
    (0xFFFFFF4, 28), #24 Cancel
    (0xFFFFFF5, 28), #25 End of Medium
    (0xFFFFFF6, 28), #26 Substitute
    (0xFFFFFF7, 28), # 27 Escape
    (0xFFFFFF8, 28), #28 File Separator
    (0xFFFFFF9, 28), #29 Group Separator
    (0xFFFFFFA, 28), #30 Record Separator
    (0XFFFFFFB, 28), #31 Unit Separator
    (0x14, 6), #32 Space
    (0x3F8, 10), #33 !
    (0x3F9, 10), #34 "
    (0xFFA, 12), #35 #
    (0x1FF9, 13), #36 $
    (0x15, 6), #37 %
    (0xF8, 8), #38 &
    (0x7FA, 11), #39 '
    (0x3FA, 10), #40 (
    (0x3FB, 10), #41 )
    (0xF9, 8), #42 *
    (0x7FB, 11), #43 +
    (0xFA, 8), #44 ,
    (0x16, 6), #45 -
    (0x17, 6), #46 .
    (0x18, 6), #47 /
    (0x0, 5), #48 0
    (0x1, 5), #49 1
    (0x2, 5), #50 2
    (0x19, 6), #51 3
    (0x1A, 6), #52 4
    (0x1B, 6), #53 5
    (0x1C, 6), #54 6
    (0x1D, 6), #55 7
    (0x1E, 6), #56 8
    (0x1F, 6), #57 9
    (0x5C, 7), #58 :
    (0xFB, 8), #59 ;
    (0x7FFC, 15), #60 <
    (0x20, 6), #61 =
    (0xFFB, 12), #62 >
    (0x3FC, 10), #63 ?
    (0x1FFA, 13), #64 @
    (0x21, 6), #65 A
    (0x5D, 7), #66 B
    (0x5E, 7), #67 C
    (0x5F, 7), #68 D
    (0x60, 7), #69 E
    (0x61, 7), #70 F
    (0x62, 7), #71 G
    (0x63, 7), #72 H
    (0x64, 7), #73 I
    (0x65, 7), #74 J
    (0x66, 7), #75 K
    (0x67, 7), #76 L
    (0x68, 7), #77 M
    (0x69, 7), #78 N
    (0x6A, 7), #79 O
    (0x6B, 7), #80 P
    (0x6C, 7), #81 Q
    (0x6D, 7), #82 R
    (0x6E, 7), #83 S
    (0x6F, 7), #84 T
    (0x70, 7), #85 U
    (0x71, 7), #86 V
    (0x72, 7), #87 W
    (0xFC, 8), #88 X
    (0x73, 7), #89 Y
    (0xFD, 8), #90 Z
    (0x1FFB, 13), #91 [
    (0x7FFF0, 19), #92 \
    (0x1FFC, 13), #93 ]
    (0x3FFC, 14), #94 ^
    (0x22, 6), #95 _
    (0x7FFD, 15), #96 `
    (0x3, 5), #97 a
    (0x23, 6), #98 b
    (0x4, 5), #99 c
    (0x24, 6), #100 d
    (0x5, 5), #101 e
    (0x25, 6), #102 f
    (0x26, 6), #103 g
    (0x27, 6), #104 h
    (0x6, 5), #105 i
    (0x74, 7), #106 j
    (0x75, 7), #107 k
    (0x28, 6), #108 l
    (0x29, 6), #109 m
    (0x2A, 6), #110 n
    (0x7, 5), #111 o
    (0x2B, 6), #112 p
    (0x76, 7), #113 q
    (0x2C, 6), #114 r
    (0x8, 5), #115 s
    (0x9, 5), #116 t
    (0x2D, 6), #117 u
    (0x77, 7), #118 v
    (0x78, 7), #119 w
    (0x79, 7), #120 x
    (0x7A, 7), #121 y
    (0x7B, 7), #122 z
    (0x7FFE, 15), #123 {
    (0x7FC, 11), #124 |
    (0x3FFD, 14), #125 }
    (0x1FFD, 13), #126 ~
    (0xFFFFFFC, 28), #127
    (0xFFFE6, 20), #128
    (0x3FFFD2, 22), #129
    (0xFFFE7, 20), #130
    (0xFFFE8, 20), #131
    (0x3FFFD3, 22), #132
    (0x3FFFD4, 22), #133
    (0x3FFFD5, 22), #134
    (0x7FFFD9, 23), #135
    (0x3FFFD6, 22), #136
    (0x7FFFDA, 23), #137
    (0x7FFFDB, 23), #138
    (0x7FFFDC, 23), #139
    (0x7FFFDD, 23), #140
    (0x7FFFDE, 23), #141
    (0xFFFFEB, 24), #142
    (0x7FFFDF, 23), #143
    (0xFFFFEC, 24), #144
    (0xFFFFED, 24), #145
    (0x3FFFD7, 22), #146
    (0x7FFFE0, 23), #147
    (0xFFFFEE, 24), #148
    (0x7FFFE1, 23), #149
    (0x7FFFE2, 23), #150
    (0x7FFFE3, 23), #151
    (0x7FFFE4, 23), #152
    (0x1FFFDC, 21), #153
    (0x3FFFD8, 22), #154
    (0x7FFFE5, 23), #155
    (0x3FFFD9, 22), #156
    (0x7FFFE6, 23), #157
    (0x7FFFE7, 23), #158
    (0xFFFFEF, 24), #159
    (0x3FFFDA, 22), #160
    (0x1FFFDD, 21), #161
    (0xFFFE9, 20), #162
    (0x3FFFDB, 22), #163
    (0x3FFFDC, 22), #164
    (0x7FFFE8, 23), #165
    (0x7FFFE9, 23), #166
    (0x1FFFDE, 21), #167
    (0x7FFFEA, 23), #168
    (0x3FFFDD, 22), #169
    (0x3FFFDE, 22), #170
    (0xFFFFF0, 24), #171
    (0x1FFFDF, 21), #172
    (0x3FFFDF, 22), #173
    (0x7FFFEB, 23), #174
    (0x7FFFEC, 23), #175
    (0x1FFFE0, 21), #176
    (0x1FFFE1, 21), #177
    (0x3FFFE0, 22), #178
    (0x1FFFE2, 21), #179
    (0x7FFFED, 23), #180
    (0x3FFFE1, 22), #181
    (0x7FFFEE, 23), #182
    (0x7FFFEF, 23), #183
    (0xFFFEA, 20), #184
    (0x3FFFE2, 22), #185
    (0x3FFFE3, 22), #186
    (0x3FFFE4, 22), #187
    (0x7FFFF0, 23), #188
    (0x3FFFE5, 22), #189
    (0x3FFFE6, 22), #190
    (0x7FFFF1, 23), #191
    (0x3FFFFE0, 26), #192
    (0x3FFFFE1, 26), #193
    (0xFFFEB, 20), #194
    (0x7FFF1, 19), #195
    (0x3FFFE7, 22), #196
    (0x7FFFF2, 23), #197
    (0x3FFFE8, 22), #198
    (0x1FFFFEC, 25), #199
    (0x3FFFFE2, 26), #200
    (0x3FFFFE3, 26), #201
    (0x3FFFFE4, 26), #202
    (0x7FFFFDE, 27), #203
    (0x7FFFFDF, 27), #204
    (0x3FFFFE5, 26), #205
    (0xFFFFF1, 24), #206
    (0x1FFFFED, 25), #207
    (0x7FFF2, 19), #208
    (0x1FFFE3, 21), #209
    (0x3FFFFE6, 26), #210
    (0x7FFFFE0, 27), #211
    (0x7FFFFE1, 27), #212
    (0x3FFFFE7, 26), #213
    (0x7FFFFE2, 27), #214
    (0xFFFFF2, 24), #215
    (0x1FFFE4, 21), #216
    (0x1FFFE5, 21), #217
    (0x3FFFFE8, 26), #218
    (0x3FFFFE9, 26), #219
    (0xFFFFFFD, 28), #220
    (0x7FFFFE3, 27), #221
    (0x7FFFFE4, 27), #222
    (0x7FFFFE5, 27), #223
    (0xFFFEC, 20), #224
    (0xFFFFF3, 24), #225
    (0xFFFED, 20), #226
    (0x1FFFE6, 21), #227
    (0x3FFFE9, 22), #228
    (0x1FFFE7, 21), #229
    (0x1FFFE8, 21), #230
    (0x7FFFF3, 23), #231
    (0x3FFFEA, 22), #232
    (0x3FFFEB, 22), #233
    (0x1FFFFEE, 25), #234
    (0x1FFFFEF, 25), #235
    (0xFFFFF4, 24), #236
    (0xFFFFF5, 24), #237
    (0x3FFFFEA, 26), #238
    (0x7FFFF4, 23), #239
    (0x3FFFFEB, 26), #240
    (0x7FFFFE6, 27), #241
    (0x3FFFFEC, 26), #242
    (0x3FFFFED, 26), #243
    (0x7FFFFE7, 27), #244
    (0x7FFFFE8, 27), #245
    (0x7FFFFE9, 27), #246
    (0x7FFFFEA, 27), #247
    (0x7FFFFEB, 27), #248
    (0xFFFFFFE, 28), #249
    (0x7FFFFEC, 27), #250
    (0x7FFFFED, 27), #251
    (0x7FFFFEE, 27), #252
    (0x7FFFFEF, 27), #253
    (0x7FFFFF0, 27), #254
    (0x3FFFFEE, 26), #255
    (0x3FFFFFFF, 30) #256 End Of Stream
)


_HUFFMAN_BINARY_CODES = tuple(
    "{{0:0>{0}b}}".format(bit_length).format(code)
    for code, bit_length in HUFFMAN_CODEBOOK
)


class _Node:
    """A binary tree node."""

    __slots__ = ("left", "right")

    @staticmethod
    def is_leaf(node):
        """Check if the node is a leaf."""
        return isinstance(node, int)

    @staticmethod
    def from_sequence(sequence, index=0):
        """Build a tree from a sorted sequence of canonical Huffman
        codes exprimed as a string of binary digits (0 and 1).
        The ``index`` parameter is used for recursion, it indicates the
        current bit used to find where to split the sequence.
        """

        if len(sequence) < 2:
            raise Exception("single object sequence")

        split_index = len(sequence)
        for rank, value in enumerate(sequence):
            code, char = value

            if index < len(code) and code[index] == "1":
                split_index = rank
                break

        if len(sequence) == 2 and split_index != 1:
            print("index:", index)
            print(sequence)
            raise Exception("unsplittable sequence")

        left = sequence[:split_index]
        right = sequence[split_index:]

        if len(left) > 1:
            # left recursion
            left_node = _Node.from_sequence(left, index=index+1)
        elif len(left) == 1:
            code, char = left[0]
            left_node = char
        else:
            left_node == None

        if len(right) > 1:
            # right recursion
            right_node = _Node.from_sequence(right, index=index+1)
        elif len(right) == 1:
            code, char = right[0]
            right_node = char
        else:
            right_node = None

        node = _Node(left_node, right_node)

        return node

    def __init__(self, left=None, right=None):
        self.left = left
        self.right = right


def _build_huffman_tree():
    binary_codes = _HUFFMAN_BINARY_CODES
    char_map = ((bin_code, char) for char, bin_code in enumerate(binary_codes))
    lookup_table = list(sorted(char_map, key=lambda v: v[0]))

    return _Node.from_sequence(lookup_table)


_HUFFMAN_TREE = _build_huffman_tree()


def encode(string):
    bin_raw = bytearray()
    for char in string:
        code, bit_length = HUFFMAN_CODEBOOK[char]
        bin_code = "{{0:0>{0}b}}".format(bit_length).format(code).encode("ascii")
        bin_raw.extend(bin_code)

    byte_count, last_bits = divmod(len(bin_raw), 8)

    raw = bytearray()
    for i in range(byte_count):
        bin_byte = bin_raw[i*8: (i + 1)*8]
        tmp = int(bin_byte, base=2)
        raw.append(tmp)

    if last_bits:
        padding = 8 - last_bits
        bin_byte = bin_raw[byte_count*8:]
        tmp = int(bin_byte, base=2)
        tmp <<= padding
        tmp |= (1 << padding) - 1
        raw.append(tmp)

    return bytes(raw)


def decode(string):
    decoded_string = bytearray()
    current_node = _HUFFMAN_TREE
    current_code = io.StringIO()
    for byte in string:
        bin_byte = [byte & (0x80 >> shift) for shift in range(0, 8)]

        for bit in bin_byte:
            current_code.write("1" if bit else "0")

            if bit:
                current_node = current_node.right

            else:
                current_node = current_node.left

            if current_node is None:
                raise Exception("Unknown code {0}".format(current_code))

            if _Node.is_leaf(current_node):
                decoded_string.append(current_node)
                current_node = _HUFFMAN_TREE
                current_code = io.StringIO()

    return bytes(decoded_string)

if __name__ == "__main__":
    test = b"\xF1\xE3\xC2\xE5\xF2\x3A\x6B\xA0\xAB\x90\xF4\xFF"
    s = b"www.example.com"
    print(s, len(s))
    tmp = encode(s)
    if tmp == test:
        print("Test successful")
    print(tmp, len(tmp))
    print(decode(tmp))
    # with open("huffman_tree.dot", "w") as f:
    #     f.write(_HUFFMAN_TREE.to_DOT())
