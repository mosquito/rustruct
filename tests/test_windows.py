"""`struct` with `size` (a window): TLV framing, trailing-data checks."""

import pytest

from helpers import u
from rustruct import InvalidDataError, SchemaError, compile


def test_tlv_window():
    codec = compile(
        (
            u("tag", "u8"),
            u("size", "u8"),
            u("value", "struct", fields=(u("payload", "bytes", len="*"),), size=("ref", "size")),
        )
    )
    values = codec.unpack(b"\x07\x03abc")
    assert values == {"tag": 7, "size": 3, "value": {"payload": b"abc"}}
    assert codec.pack({"tag": 1, "value": {"payload": b"wxyz"}}) == b"\x01\x04wxyz"


def test_window_trailing_path():
    codec = compile(
        (
            u("size", "u8"),
            u("body", "struct", fields=(u("x", "u8"),), size=("ref", "size")),
        )
    )
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x02\x01\x02")
    assert ei.value.kind == "trailing"
    assert ei.value.path == "body"


def test_window_overrun_is_invalid():
    codec = compile(
        (
            u("size", "u8"),
            u("body", "struct", fields=(u("x", "u16"),), size=("ref", "size")),
        )
    )
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x01\xaa\xbb")
    assert ei.value.kind == "truncated"


def test_nested_windows():
    codec = compile(
        (
            u("outer_size", "u8"),
            u(
                "outer",
                "struct",
                fields=(
                    u("inner_size", "u8"),
                    u("inner", "struct", fields=(u("payload", "bytes", len="*"),), size=("ref", "inner_size")),
                    u("after", "u8"),
                ),
                size=("ref", "outer_size"),
            ),
        )
    )
    values = codec.unpack(bytes([4, 2, ord("a"), ord("b"), 0x2A]))
    assert values["outer"]["inner"]["payload"] == b"ab"
    assert values["outer"]["after"] == 0x2A


def test_struct_size_matching_static_body():
    codec = compile((u("body", "struct", fields=(u("a", "u8"), u("b", "u8")), size=2),))
    assert codec.unpack(b"\x01\x02") == {"body": {"a": 1, "b": 2}}


def test_struct_size_mismatching_static_body_is_schema_error():
    with pytest.raises(SchemaError):
        compile((u("body", "struct", fields=(u("a", "u8"), u("b", "u8")), size=3),))


def test_struct_size_greedy_is_schema_error():
    with pytest.raises(SchemaError):
        compile((u("body", "struct", fields=(u("a", "u8"),), size="*"),))
