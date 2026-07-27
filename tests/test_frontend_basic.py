"""The declarative Struct frontend: flat scalar schemas, byteorder,
construction defaults, and error cases."""

import pytest

from rustruct import F32, F64, I8, U8, U16, U32, Struct, described


def test_scalar_roundtrip_and_repr():
    class Header(Struct, byteorder="network"):
        kind: U8
        flags: U8 = described(0)
        request_id: U32

    h = Header(kind=2, flags=1, request_id=0x12345678)
    wire = h.pack()
    assert wire == b"\x02\x01\x124Vx"
    back = Header.unpack(wire)
    assert back == h
    assert repr(back) == "Header(kind=2, flags=1, request_id=305419896)"


def test_byteorder_default_is_big():
    class Plain(Struct):
        x: U16

    assert Plain(x=0x1234).pack() == b"\x12\x34"


def test_byteorder_little():
    class LE(Struct, byteorder="little"):
        x: U16

    assert LE(x=0x1234).pack() == b"\x34\x12"


def test_byteorder_inherited_by_subclass():
    class Base(Struct, byteorder="little"):
        pass

    class Child(Base):
        x: U16

    assert Child(x=0x1234).pack() == b"\x34\x12"


def test_negative_and_float_scalars():
    class Rec(Struct):
        a: I8
        f: F32
        d: F64

    r = Rec(a=-1, f=1.5, d=-2.5)
    wire = r.pack()
    back = Rec.unpack(wire)
    assert back == r
    assert back.a == -1
    assert back.f == 1.5
    assert back.d == -2.5


def test_described_default_used_when_omitted():
    class WithDefault(Struct):
        x: U8 = described(7)

    assert WithDefault().x == 7
    assert WithDefault().pack() == b"\x07"
    assert WithDefault(x=9).pack() == b"\x09"


def test_missing_required_field_raises():
    class Rec(Struct):
        x: U8
        y: U8

    with pytest.raises(TypeError, match="x"):
        Rec(y=1)


def test_unexpected_keyword_raises():
    class Rec(Struct):
        x: U8

    with pytest.raises(TypeError, match="bogus"):
        Rec(x=1, bogus=2)


def test_unpack_accepts_any_buffer():
    class Rec(Struct):
        x: U16

    assert Rec.unpack(b"\x01\x02").x == 0x0102
    assert Rec.unpack(bytearray(b"\x01\x02")).x == 0x0102
    assert Rec.unpack(memoryview(b"\x01\x02")).x == 0x0102


def test_unpack_from_and_parse():
    class Rec(Struct):
        x: U8

    rec, pos = Rec.unpack_from(b"\x07tail", 0)
    assert rec.x == 7 and pos == 1

    from rustruct import Incomplete

    r = Rec.parse(b"")
    assert isinstance(r, Incomplete)
    rec2, pos2 = Rec.parse(b"\x09")
    assert rec2.x == 9 and pos2 == 1


def test_eq_across_types_is_not_equal():
    class A(Struct):
        x: U8

    class B(Struct):
        x: U8

    assert A(x=1) != B(x=1)
