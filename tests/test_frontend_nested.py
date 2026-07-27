"""Nested structs (plain and windowed), dynamic bytes, arrays, and bit
fields declared through the Struct frontend."""

from rustruct import U8, U16, Struct, array, bits, sized, slice, string


def test_nested_struct_plain():
    class Inner(Struct):
        a: U8
        b: U16

    class Outer(Struct):
        tag: U8
        inner: Inner

    outer = Outer(tag=1, inner=Inner(a=2, b=0x0304))
    wire = outer.pack()
    assert wire == b"\x01\x02\x03\x04"
    back = Outer.unpack(wire)
    assert isinstance(back.inner, Inner)
    assert back == outer


def test_nested_struct_own_byteorder_overrides_outer():
    class LEInner(Struct, byteorder="little"):
        x: U16

    class Outer(Struct, byteorder="big"):
        outer_x: U16
        inner: LEInner

    outer = Outer(outer_x=0x0102, inner=LEInner(x=0x0304))
    wire = outer.pack()
    assert wire == b"\x01\x02\x04\x03"
    assert Outer.unpack(wire) == outer


def test_sized_struct_window_tlv():
    class Body(Struct):
        payload: bytes = slice(len="*")

    class Packet(Struct):
        size: U8
        body: object = sized(Body, size="size")

    pkt = Packet(body=Body(payload=b"abc"))
    wire = pkt.pack()
    assert wire == b"\x03abc"
    back = Packet.unpack(wire)
    assert isinstance(back.body, Body)
    assert back.body.payload == b"abc"
    assert back.size == 3


def test_bytes_field_len_by_sibling_name():
    class Rec(Struct):
        n: U8
        data: bytes = slice(len="n")

    r = Rec(data=b"hello")
    wire = r.pack()
    assert wire == b"\x05hello"
    back = Rec.unpack(wire)
    assert back.data == b"hello"
    assert back.n == 5


def test_bytes_field_len_by_expression():
    # len = (ihl - 5) * 4, the IPv4-options-style linear expression.
    class Rec(Struct):
        ihl: U8
        options: bytes = slice(len=lambda f: (f.ihl - 5) * 4)

    r = Rec(options=b"\x01\x02\x03\x04")
    wire = r.pack()
    assert wire == b"\x06\x01\x02\x03\x04"
    back = Rec.unpack(wire)
    assert back.options == b"\x01\x02\x03\x04"
    assert back.ihl == 6


def test_str_field():
    class Rec(Struct):
        n: U8
        s: str = string(len="n")

    r = Rec(s="hi")
    wire = r.pack()
    assert wire == b"\x02hi"
    assert Rec.unpack(wire).s == "hi"


def test_array_field_count_from_sibling():
    class Rec(Struct):
        n: U8
        items: list = array(elem=U16, count="n")

    r = Rec(items=[1, 2, 3])
    wire = r.pack()
    assert wire == bytes([3, 0, 1, 0, 2, 0, 3])
    back = Rec.unpack(wire)
    assert back.items == [1, 2, 3]
    assert back.n == 3


def test_array_field_until_eof():
    class Body(Struct):
        items: list = array(elem=U8, until_eof=True)

    class Packet(Struct):
        size: U8
        body: object = sized(Body, size="size")

    pkt = Packet(body=Body(items=[7, 8, 9]))
    wire = pkt.pack()
    assert wire == bytes([3, 7, 8, 9])
    assert Packet.unpack(wire).body.items == [7, 8, 9]


def test_array_of_struct_elements_are_typed():
    class Point(Struct):
        x: U8
        y: U8

    class Rec(Struct):
        n: U8
        points: list = array(elem=Point, count="n")

    r = Rec(points=[Point(x=1, y=2), Point(x=3, y=4)])
    wire = r.pack()
    back = Rec.unpack(wire)
    assert all(isinstance(p, Point) for p in back.points)
    assert back.points == [Point(x=1, y=2), Point(x=3, y=4)]


def test_bits_field_flat():
    # Two byte-aligned bit runs (4+4 bits, then 1+1+1+13 bits) -- a lone bit
    # run must always sum to a whole number of bytes.
    class Flags(Struct):
        version: int = bits(4)
        ihl: int = bits(4)
        reserved: int = bits(1)
        df: int = bits(1)
        mf: int = bits(1)
        offset: int = bits(13)

    f = Flags(version=4, ihl=5, reserved=0, df=1, mf=0, offset=100)
    wire = f.pack()
    back = Flags.unpack(wire)
    assert back == f
    assert back.version == 4
    assert back.df == 1
    assert back.offset == 100
