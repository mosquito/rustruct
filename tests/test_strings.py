"""`str` and `cstr`: byte-length semantics, encodings, terminators."""

import pytest

from helpers import u
from rustruct import InvalidDataError, PackError, SchemaError, compile


def test_str_len_in_bytes():
    codec = compile((u("n", "u8"), u("s", "str", len=("ref", "n"))))
    payload = "你好世界"  # multi-byte UTF-8 sample
    raw = payload.encode()
    buf = bytes([len(raw)]) + raw
    assert codec.unpack(buf) == {"n": len(raw), "s": payload}
    # the length comes from the actual encoded byte count, not len(str)
    assert codec.pack({"s": payload}) == buf


def test_str_decode_error():
    codec = compile((u("s", "str", len=2),))
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\xc3\x28")
    assert ei.value.kind == "decode"
    assert ei.value.path == "s"


def test_str_ascii_encoding():
    codec = compile((u("s", "str", len=3, encoding="ascii"),))
    assert codec.unpack(b"abc") == {"s": "abc"}
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"a\x80c")
    assert ei.value.kind == "decode"


def test_str_latin1_encoding():
    codec = compile((u("s", "str", len=2, encoding="latin-1"),))
    values = codec.unpack(bytes([0x61, 0xE9]))
    assert values == {"s": "aé"}
    assert codec.pack(values) == bytes([0x61, 0xE9])


def test_unsupported_encoding_is_schema_error():
    with pytest.raises(SchemaError):
        compile((u("s", "str", len=4, encoding="cp1251"),))


def test_errors_non_strict_is_schema_error():
    with pytest.raises(SchemaError):
        compile((u("s", "str", len=4, errors="ignore"),))


def test_cstr():
    codec = compile((u("s", "cstr", max=16), u("x", "u8")))
    assert codec.unpack(b"hello\x00\x2a") == {"s": "hello", "x": 0x2A}
    assert codec.pack({"s": "hi", "x": 1}) == b"hi\x00\x01"
    with pytest.raises(PackError) as ei:
        codec.pack({"s": "a\x00b", "x": 1})
    assert ei.value.kind == "nul_in_cstr"


def test_cstr_no_terminator_within_max():
    codec = compile((u("s", "cstr", max=8),))
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"aaaaaaaaaaaa\x00")
    assert ei.value.kind == "limit"


def test_cstr_unterminated_inside_window():
    codec = compile(
        (
            u("size", "u8"),
            u("body", "struct", fields=(u("s", "cstr", max=32),), size=("ref", "size")),
        )
    )
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x04abcd\xff")
    assert ei.value.kind == "unterminated"
