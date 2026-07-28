"""`array`: count/until_eof, limits, nesting."""

import pytest

from helpers import nest_arrays, u
from rustruct import InvalidDataError, compile


def test_array_count():
    codec = compile((u("n", "u8"), u("items", "array", elem=("u16", {}), count=("ref", "n"))))
    assert codec.unpack(b"\x02\x00\x01\x00\x02") == {"n": 2, "items": [1, 2]}
    assert codec.pack({"items": [1, 2, 3]}) == b"\x03\x00\x01\x00\x02\x00\x03"


def test_empty_array():
    codec = compile((u("n", "u8"), u("items", "array", elem=("u8", {}), count=("ref", "n"))))
    assert codec.unpack(b"\x00") == {"n": 0, "items": []}
    assert codec.pack({"items": []}) == b"\x00"


def test_array_count_limit():
    codec = compile(
        (u("n", "u32"), u("items", "array", elem=("u8", {}), count=("ref", "n"))),
        max_count=100,
    )
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\xff\xff\xff\xff")
    assert ei.value.kind == "limit"


def test_array_until_eof_limit():
    codec = compile(
        (
            u("size", "u8"),
            u("body", "struct", fields=(u("items", "array", elem=("u8", {}), until_eof=True),), size=("ref", "size")),
        ),
        max_count=4,
    )
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x05\x01\x02\x03\x04\x05")
    assert ei.value.kind == "limit"


def test_array_greedy_count_alias_for_until_eof():
    codec = compile(
        (
            u("size", "u8"),
            u("body", "struct", fields=(u("items", "array", elem=("u8", {}), count="*"),), size=("ref", "size")),
        )
    )
    values = codec.unpack(b"\x03\x07\x08\x09")
    assert values["body"]["items"] == [7, 8, 9]


def test_nested_arrays():
    codec = compile((u("rows", "array", elem=("array", {"elem": ("u8", {}), "count": 2}), count=2),))
    values = codec.unpack(b"\x01\x02\x03\x04")
    assert values == {"rows": [[1, 2], [3, 4]]}
    assert codec.pack(values) == b"\x01\x02\x03\x04"


def test_deep_array_nesting_decodes_past_the_struct_frame_limit():
    # Unpack frames are capped at 64, but only `struct` costs one, so that
    # cap says nothing about arrays: this nests to the parser's own limit
    # and still decodes. It is also why that limit cannot be lowered to
    # match the frame cap -- it would start rejecting schemas that work.
    codec = compile(nest_arrays(128))
    assert codec.pack(codec.unpack(b"\x07")) == b"\x07"


def test_array_count_limit_is_inclusive():
    # max_count is the largest count that decodes, not the first one that
    # fails; the limit tests above only show that something well past it
    # is rejected, which holds either way round.
    codec = compile(
        (u("n", "u8"), u("items", "array", elem=("u8", {}), count=("ref", "n"))),
        max_count=4,
    )
    assert codec.unpack(b"\x04\x01\x02\x03\x04")["items"] == [1, 2, 3, 4]
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x05\x01\x02\x03\x04\x05")
    assert ei.value.kind == "limit"


def test_array_element_error_reports_index():
    codec = compile((u("items", "array", elem=("u8", {"const": 0xAB}), count=3),))
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(bytes([0xAB, 0xAB, 0xFF]))
    assert ei.value.kind == "const"
    assert ei.value.path == "items[2]"


def test_array_element_struct_refs_enclosing_scope():
    codec = compile(
        (
            u("n", "u8"),
            u("items", "array", elem=("struct", {"fields": (u("value", "bytes", len=("ref", "n")),)}), count=2),
        )
    )
    values = codec.unpack(bytes([2, ord("a"), ord("b"), ord("c"), ord("d")]))
    assert values["items"] == [{"value": b"ab"}, {"value": b"cd"}]


def test_array_count_and_until_eof_are_mutually_exclusive():
    # Previously `count` was silently discarded whenever until_eof was set.
    from rustruct import array

    with pytest.raises(TypeError, match="mutually exclusive"):
        array(elem=("u8", {}), count=3, until_eof=True)
