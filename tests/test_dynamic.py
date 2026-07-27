"""Dynamic-length bytes fields: derived lengths, expressions, limits."""

import pytest

from helpers import u
from rustruct import InvalidDataError, PackError, SchemaError, compile


def test_derived_length_ignored_in_mapping():
    codec = compile((u("n", "u8"), u("data", "bytes", len=("ref", "n"))))
    assert codec.pack({"data": b"hello"}) == b"\x05hello"
    assert codec.pack({"n": 200, "data": b"xy"}) == b"\x02xy"
    # round-trip with a changed body stays consistent
    values = codec.unpack(b"\x02ab")
    values["data"] = b"abcdef"
    assert codec.pack(values) == b"\x06abcdef"


def test_expression_arithmetic():
    codec = compile(
        (
            u("size", "u8"),
            u("data", "bytes", len=("sub", ("ref", "size"), 4)),
        )
    )
    assert codec.pack({"data": b"xy"}) == b"\x06xy"
    assert codec.unpack(b"\x06xy") == {"size": 6, "data": b"xy"}


def test_multiplicative_inversion_and_indivisible():
    codec = compile(
        (
            u("n", "u8"),
            u("data", "bytes", len=("mul", ("ref", "n"), 2)),
        )
    )
    assert codec.pack({"data": b"abcd"}) == b"\x02abcd"
    with pytest.raises(PackError) as ei:
        codec.pack({"data": b"abc"})
    assert ei.value.kind == "indivisible"


def test_multiple_consumers_must_agree():
    codec = compile(
        (
            u("n", "u8"),
            u("a", "bytes", len=("ref", "n")),
            u("b", "bytes", len=("ref", "n")),
        )
    )
    assert codec.pack({"a": b"xy", "b": b"zw"}) == b"\x02xyzw"
    with pytest.raises(PackError) as ei:
        codec.pack({"a": b"xy", "b": b"zzz"})
    assert ei.value.kind == "inconsistent"


def test_nonlinear_expression_is_schema_error():
    with pytest.raises(SchemaError):
        compile(
            (
                u("a", "u8"),
                u("b", "u8"),
                u("data", "bytes", len=("add", ("ref", "a"), ("ref", "b"))),
            )
        )


def test_greedy():
    codec = compile((u("head", "u8"), u("rest", "bytes", len="*")))
    assert codec.unpack(b"\x01payload") == {"head": 1, "rest": b"payload"}


def test_max_limit():
    codec = compile((u("n", "u32"), u("data", "bytes", len=("ref", "n"), max=8)))
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x00\x01\x00\x00" + b"x" * 16)
    assert ei.value.kind == "limit"


def test_derived_length_out_of_prim_range():
    codec = compile((u("n", "u8"), u("data", "bytes", len=("ref", "n"))))
    with pytest.raises(PackError) as ei:
        codec.pack({"data": b"x" * 300})
    assert ei.value.kind == "range"


def test_negative_len_at_runtime():
    codec = compile((u("n", "i8"), u("data", "bytes", len=("ref", "n"))))
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\xff")
    assert ei.value.kind == "negative_len"


def test_div_zero_at_runtime():
    codec = compile(
        (
            u("n", "u8"),
            u("m", "u8"),
            u("body", "switch", on=("div", 10, ("ref", "m")), cases=((5, ("u8", {})),)),
        )
    )
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x01\x00\x07")
    assert ei.value.kind == "div_zero"
    assert codec.unpack(b"\x01\x02\x07") == {"n": 1, "m": 2, "body": 7}
