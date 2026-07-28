"""`when_field()`: a conditionally-present field through the declarative
Struct frontend, layered over the core `when(pred)` construct -- see
crates/rustruct/tests/conditional.rs for the core-level test suite this
builds on."""

import pytest

from rustruct import U8, U16, PackError, Struct, array, described, sized, when


class Message(Struct, byteorder="big"):
    has_extra: U8 = described(0, help="0/1: whether `extra` is present")
    extra: object = when(pred="has_extra", then=U16, default=None)


def test_present_when_pred_is_true_and_roundtrips():
    msg = Message(has_extra=1, extra=0xABCD)
    wire = msg.pack()
    assert wire == b"\x01\xab\xcd"
    back = Message.unpack(wire)
    assert back.extra == 0xABCD


def test_absent_when_pred_is_false():
    msg = Message(has_extra=0)
    wire = msg.pack()
    assert wire == b"\x00"
    back = Message.unpack(wire)
    assert back.extra is None


def test_default_is_none_no_need_to_supply_it():
    msg = Message(has_extra=0)
    assert msg.extra is None


def test_present_but_missing_value_raises_on_pack():
    msg = Message(has_extra=1)  # extra defaults to None, but pred is true
    with pytest.raises(PackError) as excinfo:
        msg.pack()
    assert excinfo.value.kind == "missing"


def test_absent_ignores_a_leftover_value_even_if_one_was_set():
    # has_extra=0 but extra was set anyway -- pred is still false, so it's
    # simply never looked at (matches the core-level contract, see
    # conditional.rs::pack_absent_never_looks_up_the_value_even_if_supplied).
    msg = Message(has_extra=0, extra=0x1234)
    assert msg.pack() == b"\x00"


class WithComparisonPred(Struct, byteorder="big"):
    """A PE32/ELF-style condition: presence depends on a comparison, not a
    bare truthy ref."""

    optionalheader_size: U16 = described(0)
    optionalheader: object = when(pred=lambda f: f.optionalheader_size > 0, then=U16, default=None)


def test_pred_as_a_comparison_lambda():
    present = WithComparisonPred(optionalheader_size=2, optionalheader=0x1122)
    assert WithComparisonPred.unpack(present.pack()).optionalheader == 0x1122

    absent = WithComparisonPred(optionalheader_size=0)
    wire = absent.pack()
    assert wire == b"\x00\x00"
    assert WithComparisonPred.unpack(wire).optionalheader is None


class Header(Struct, byteorder="big"):
    magic: U16 = described(0xCAFE)


class WithStructThen(Struct, byteorder="big"):
    """`then` is a nested Struct, not a scalar -- StructShape.to_wire must
    handle a None value gracefully when the field is left absent."""

    has_header: U8 = described(0)
    header: object = when(pred="has_header", then=sized(Header, size=2), default=None)


def test_struct_then_present():
    msg = WithStructThen(has_header=1, header=Header(magic=0x1234))
    back = WithStructThen.unpack(msg.pack())
    assert back.header == Header(magic=0x1234)


def test_struct_then_absent_does_not_crash_on_pack():
    msg = WithStructThen(has_header=0)
    assert msg.pack() == b"\x00"
    assert WithStructThen.unpack(msg.pack()).header is None


class WithArrayThen(Struct, byteorder="big"):
    """`then` is an array -- ArrayShape.to_wire would crash iterating a
    bare None if compile_to_mapping ever called it unconditionally."""

    has_items: U8 = described(0)
    count: U8 = described(0)
    items: object = when(pred="has_items", then=array(elem=U16, count="count"), default=None)


def test_array_then_present_and_absent():
    msg = WithArrayThen(has_items=1, count=2, items=[1, 2])
    back = WithArrayThen.unpack(msg.pack())
    assert back.items == [1, 2]

    absent = WithArrayThen(has_items=0, count=0)
    assert absent.pack() == b"\x00\x00"  # has_items=0, count=0 -- no crash
    assert WithArrayThen.unpack(absent.pack()).items is None


class TwoKindsOfDefault(Struct, byteorder="big"):
    """A plain default and a conditional one in the same class.

    `__init__` and `from_mapping` each bind their defaults into the globals
    of the compiled methods. Those are generated separately and compiled
    together, so the names have to be unique across all of them -- when
    they were not, this class's `pad` came back as 999.
    """

    has_extra: U8 = 1
    pad: U8 = 42
    extra: object = when(pred="has_extra", then=U16, default=999)


def test_a_plain_default_survives_a_conditional_one():
    assert TwoKindsOfDefault(has_extra=1).pad == 42
    assert TwoKindsOfDefault.from_mapping({"has_extra": 0, "pad": 5}).extra == 999
    assert TwoKindsOfDefault.from_mapping({"has_extra": 0, "pad": 5}).pad == 5
