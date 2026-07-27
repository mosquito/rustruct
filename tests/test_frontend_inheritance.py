"""Wire-field inheritance: a Struct subclass sees its base classes' own
fields in addition to its own, in declaration order."""

import pytest

from rustruct import U8, U16, U32, Struct, described, digest
from rustruct import slice as rslice


def test_subclass_inherits_base_fields():
    class Base(Struct):
        kind: U8

    class Derived(Base):
        extra: U16

    assert [f.name for f in Derived.wire_fields] == ["kind", "extra"]
    d = Derived(kind=1, extra=2)
    wire = d.pack()
    assert wire == b"\x01\x00\x02"
    assert Derived.unpack(wire) == d


def test_base_class_is_unaffected_by_subclass_fields():
    class Base(Struct):
        kind: U8

    class Derived(Base):
        extra: U16

    assert [f.name for f in Base.wire_fields] == ["kind"]
    assert Base(kind=9).pack() == b"\x09"


def test_three_levels_of_inheritance():
    class L1(Struct):
        a: U8

    class L2(L1):
        b: U8

    class L3(L2):
        c: U8

    assert [f.name for f in L3.wire_fields] == ["a", "b", "c"]
    assert L3(a=1, b=2, c=3).pack() == b"\x01\x02\x03"


def test_redeclared_field_overrides_in_place():
    class Base(Struct):
        version: U8 = described(1)
        payload: bytes = rslice(len="*")

    class Derived(Base):
        version: U8 = described(2)

    assert [f.name for f in Derived.wire_fields] == ["version", "payload"]
    assert Base(payload=b"x").pack() == b"\x01x"
    assert Derived(payload=b"x").pack() == b"\x02x"


def test_own_field_can_reference_an_inherited_field_by_name():
    class HeaderBase(Struct):
        length: U8

    class Body(HeaderBase):
        data: bytes = rslice(len="length")

    b = Body(data=b"abc")
    wire = b.pack()
    assert wire == b"\x03abc"
    assert Body.unpack(wire).length == 3


def test_digest_added_in_subclass_covers_inherited_fields():
    class HeaderBase(Struct):
        length: U8

    class Frame(HeaderBase):
        payload: bytes = rslice(len="length")
        crc: U32 = digest("crc32", over="*")

    wire = Frame(payload=b"abc").pack()
    decoded = Frame.unpack(wire)
    assert decoded.length == 3
    assert decoded.payload == b"abc"


def test_missing_inherited_field_still_required():
    class Base(Struct):
        kind: U8

    class Derived(Base):
        extra: U16

    with pytest.raises(TypeError, match="kind"):
        Derived(extra=1)
