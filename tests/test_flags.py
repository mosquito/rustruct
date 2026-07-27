"""`flags`: bit masks over a decoded integer, the `rest` policy."""

import pytest

from helpers import u
from rustruct import InvalidDataError, PackError, SchemaError, compile


def test_flags():
    codec = compile(
        (
            u(
                "fl",
                "flags",
                base="u16",
                byteorder="little",
                names=(("ack", 0x0001), ("syn", 0x0002), ("kind", 0x00F0)),
                rest="keep",
            ),
        )
    )
    values = codec.unpack(b"\x53\x01")
    assert values == {"fl": {"ack": True, "syn": True, "kind": 5, "_rest": 0x0100}}
    assert codec.pack(values) == b"\x53\x01"
    # missing keys default to 0/false
    assert codec.pack({"fl": {"syn": True}}) == b"\x02\x00"
    with pytest.raises(PackError) as ei:
        codec.pack({"fl": {"sin": True}})
    assert ei.value.kind == "unknown_flag"


def test_flags_u32_little_endian():
    codec = compile(
        (
            u(
                "fl",
                "flags",
                base="u32",
                byteorder="little",
                names=(("low", 0x000000FF), ("high", 0xFF000000)),
                rest="ignore",
            ),
        )
    )
    values = codec.unpack(bytes([0x42, 0x00, 0x00, 0x80]))
    assert values["fl"]["low"] == 0x42
    assert values["fl"]["high"] == 0x80


def test_flags_strict():
    codec = compile((u("fl", "flags", base="u8", names=(("a", 1),), rest="strict"),))
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x03")
    assert ei.value.kind == "reserved_bits"
    assert codec.unpack(b"\x01") == {"fl": {"a": True}}


def test_flags_ignore_drops_leftover():
    codec = compile((u("fl", "flags", base="u8", names=(("a", 1),), rest="ignore"),))
    values = codec.unpack(b"\xff")
    assert values == {"fl": {"a": True}}
    assert codec.pack(values) == b"\x01"


def test_flags_validation_errors():
    with pytest.raises(SchemaError):
        compile((u("fl", "flags", base="u8", names=(("a", 0b101),)),))  # non-contiguous
    with pytest.raises(SchemaError):
        compile((u("fl", "flags", base="u8", names=(("a", 0b11), ("b", 0b10))),))  # overlap
    with pytest.raises(SchemaError):
        compile(
            (
                u("fl", "flags", base="u8", names=(("a", 1),)),
                u("data", "bytes", len=("ref", "fl")),
            )
        )  # ref to flags


def test_flags_mask_out_of_base_range():
    with pytest.raises(SchemaError):
        compile((u("fl", "flags", base="u8", names=(("a", 0x100),)),))


def test_flags_reserved_rest_name():
    with pytest.raises(SchemaError):
        compile((u("fl", "flags", base="u8", names=(("_rest", 1),)),))
