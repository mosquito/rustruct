"""Fixed-width fields: sizes, endianness, const/magic, padding."""

import pytest

from helpers import u
from rustruct import Codec, InvalidDataError, PackError, SchemaError, compile


def test_static_roundtrip():
    codec = compile(
        (
            u("a", "u8"),
            u("b", "u16"),
            u("c", "i32"),
            u("d", "f64"),
            u("e", "bool"),
        )
    )
    assert isinstance(codec, Codec)
    buf = bytes([1, 0x12, 0x34, 0xFF, 0xFF, 0xFF, 0xFE]) + bytes(8) + bytes([1])
    values = codec.unpack(buf)
    assert values == {"a": 1, "b": 0x1234, "c": -2, "d": 0.0, "e": True}
    assert codec.pack(values) == buf


def test_byteorder_little_and_field_override():
    codec = compile((u("x", "u32"), u("y", "u16", byteorder="big")), byteorder="little")
    values = codec.unpack(bytes([0x78, 0x56, 0x34, 0x12, 0xAB, 0xCD]))
    assert values == {"x": 0x12345678, "y": 0xABCD}


def test_negative_and_wide_ints():
    codec = compile((u("a", "i8"), u("b", "i64")))
    values = codec.unpack(bytes([0x80]) + (-1).to_bytes(8, "big", signed=True))
    assert values == {"a": -128, "b": -1}
    assert codec.pack(values) == bytes([0x80]) + (-1).to_bytes(8, "big", signed=True)


def test_float_roundtrip_both_endians():
    import struct as pystruct

    codec = compile((u("be", "f32"), u("le", "f64", byteorder="little")))
    buf = pystruct.pack(">f", 1.5) + pystruct.pack("<d", -2.5)
    values = codec.unpack(buf)
    assert values == {"be": 1.5, "le": -2.5}
    assert codec.pack(values) == buf


def test_sizes():
    codec = compile((u("a", "u32"), u("b", "u8")))
    assert codec.static_size == 5
    assert codec.min_size == 5
    dyn = compile((u("n", "u8"), u("data", "bytes", len=("ref", "n"))))
    assert dyn.static_size is None
    assert dyn.min_size == 1


def test_unpack_accepts_any_buffer():
    codec = compile((u("x", "u16"),))
    assert codec.unpack(b"\x01\x02") == {"x": 0x0102}
    assert codec.unpack(bytearray(b"\x01\x02")) == {"x": 0x0102}
    assert codec.unpack(memoryview(b"\x01\x02")) == {"x": 0x0102}


def test_unpack_from_allows_tail():
    codec = compile((u("x", "u8"),))
    values, pos = codec.unpack_from(b"\x07tail", 0)
    assert values == {"x": 7}
    assert pos == 1
    values, pos = codec.unpack_from(b"junk\x09", 4)
    assert values == {"x": 9}
    assert pos == 5


def test_trailing_error():
    codec = compile((u("x", "u8"),))
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x01\x02")
    assert ei.value.kind == "trailing"
    assert ei.value.offset == 1


def test_truncated_error():
    codec = compile((u("x", "u32"),))
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x01")
    assert ei.value.kind == "truncated"


def test_const_magic_and_padding():
    codec = compile(
        (
            (None, "raw", {"const": b"MAGI"}),
            u("ver", "u8", const=2),
            (None, "raw", {"len": 2}),
            u("x", "u8"),
        )
    )
    values = codec.unpack(b"MAGI\x02\xaa\xbb\x2a")
    assert values == {"ver": 2, "x": 0x2A}
    # pack: the magic comes from the schema, padding is zeros, const ignores input
    assert codec.pack({"x": 0x2A, "ver": 99}) == b"MAGI\x02\x00\x00\x2a"

    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"MAGJ\x02\xaa\xbb\x2a")
    assert ei.value.kind == "const"


def test_const_bool():
    codec = compile((u("flag", "bool", const=True), u("x", "u8")))
    assert codec.unpack(b"\x01\x07") == {"flag": True, "x": 7}
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x00\x07")
    assert ei.value.kind == "const"


def test_bool_pack_is_truthiness():
    codec = compile((u("b", "bool"),))
    assert codec.pack({"b": 5}) == b"\x01"
    assert codec.pack({"b": ""}) == b"\x00"
    assert codec.unpack(b"\x02") == {"b": True}


def test_float_const_is_forbidden_by_ir():
    # f32/f64 don't accept a `const` option at all (bit-pattern consts aren't
    # meaningful without picking u32/u64 instead).
    with pytest.raises(SchemaError):
        compile((u("f", "f32", const=1),))


def test_unnamed_dynamic_is_schema_error():
    with pytest.raises(SchemaError):
        compile(((None, "bytes", {"len": 4}),))


def test_raw_const_len_mismatch_is_schema_error():
    with pytest.raises(SchemaError):
        compile((u("magic", "raw", len=4, const=b"AB"),))


def test_pack_missing_key():
    codec = compile((u("x", "u8"), u("y", "u8")))
    with pytest.raises(PackError) as ei:
        codec.pack({"x": 1})
    assert ei.value.kind == "missing"
    assert ei.value.path == "y"


def test_pack_range():
    codec = compile((u("x", "i8"),))
    with pytest.raises(PackError) as ei:
        codec.pack({"x": 128})
    assert ei.value.kind == "range"
    assert ei.value.path == "x"
    assert codec.pack({"x": -128}) == b"\x80"


def test_extra_keys_ignored_on_pack():
    codec = compile((u("x", "u8"),))
    assert codec.pack({"x": 1, "junk": object(), "more": {1: 2}}) == b"\x01"

    class MyMapping:
        def items(self):
            return [("x", 5), ("extra", object())]

    assert codec.pack(MyMapping()) == b"\x05"


def test_schema_errors():
    with pytest.raises(SchemaError):
        compile((u("x", "u8"), u("x", "u16")))  # duplicate
    with pytest.raises(SchemaError):
        compile((u("data", "bytes", len=("ref", "n")), u("n", "u8")))  # forward ref
    with pytest.raises(SchemaError):
        compile((u("x", "u8", byteorder="native"),))
    with pytest.raises(SchemaError):
        compile((u("x", "u8", const=256),))  # const out of range
    with pytest.raises(SchemaError):
        compile((u("x", "u8", typo=1),))  # unknown option
    with pytest.raises(SchemaError):
        compile((u("x", "wat"),))  # unknown kind


def test_exception_hierarchy():
    import rustruct

    assert issubclass(SchemaError, rustruct.RustructError)
    assert issubclass(InvalidDataError, rustruct.RustructError)
    assert issubclass(PackError, rustruct.RustructError)


def test_serialization_not_implemented():
    codec = compile((u("x", "u8"),))
    with pytest.raises(NotImplementedError):
        codec.to_bytes()
    with pytest.raises(NotImplementedError):
        Codec.from_bytes(b"RSTR")


def test_struct_parity_semantics():
    import struct as pystruct

    codec = compile(
        (
            u("a", "u8"),
            u("b", "u16"),
            u("c", "u32"),
            u("d", "u64"),
            u("e", "i8"),
            u("f", "i16"),
            u("g", "i32"),
            u("h", "i64"),
        )
    )
    buf = bytes(range(30))
    names = ["a", "b", "c", "d", "e", "f", "g", "h"]
    expected = dict(zip(names, pystruct.unpack(">BHIQbhiq", buf), strict=True))
    assert codec.unpack(buf) == expected
