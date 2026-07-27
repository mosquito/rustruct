"""`bits`: MSB-first bit runs, signed extension, alignment."""

import pytest

from helpers import u
from rustruct import InvalidDataError, PackError, SchemaError, compile


def test_dns_header():
    codec = compile(
        (
            u("id", "u16"),
            u("qr", "bits", width=1),
            u("opcode", "bits", width=4),
            u("aa", "bits", width=1),
            u("tc", "bits", width=1),
            u("rd", "bits", width=1),
            u("ra", "bits", width=1),
            u("z", "bits", width=3),
            u("rcode", "bits", width=4),
            u("qdcount", "u16"),
            u("ancount", "u16"),
            u("nscount", "u16"),
            u("arcount", "u16"),
        )
    )
    assert codec.static_size == 12
    buf = bytes([0x12, 0x34, 0x81, 0x80, 0, 1, 0, 2, 0, 0, 0, 0])
    values = codec.unpack(buf)
    assert values["qr"] == 1
    assert values["rd"] == 1
    assert values["ra"] == 1
    assert values["ancount"] == 2
    assert codec.pack(values) == buf


def test_bits_alignment_error():
    with pytest.raises(SchemaError):
        compile((u("a", "bits", width=3), u("x", "u8")))


def test_bits_width_out_of_range():
    with pytest.raises(SchemaError):
        compile((u("a", "bits", width=0),))
    with pytest.raises(SchemaError):
        compile((u("a", "bits", width=65),))


def test_signed_bits():
    codec = compile((u("a", "bits", width=4, signed=True), u("b", "bits", width=4)))
    assert codec.unpack(b"\xf5") == {"a": -1, "b": 5}
    assert codec.pack({"a": -1, "b": 5}) == b"\xf5"


def test_signed_bits_range_on_pack():
    codec = compile((u("a", "bits", width=4, signed=True), u("b", "bits", width=4)))
    assert codec.pack({"a": -8, "b": 0}) == b"\x80"
    with pytest.raises(PackError) as ei:
        codec.pack({"a": -9, "b": 0})
    assert ei.value.kind == "range"


def test_wide_bits_field():
    codec = compile((u("a", "bits", width=20), u("b", "bits", width=4)))
    values = codec.unpack(bytes([0x12, 0x34, 0x5F]))
    assert values == {"a": 0x12345, "b": 0xF}
    assert codec.pack(values) == bytes([0x12, 0x34, 0x5F])


def test_bits_as_length_ref():
    codec = compile(
        (
            u("hi", "bits", width=4),
            u("n", "bits", width=4),
            u("data", "bytes", len=("ref", "n")),
        )
    )
    assert codec.unpack(b"\x03abc") == {"hi": 0, "n": 3, "data": b"abc"}
    assert codec.pack({"hi": 0xA, "data": b"xy"}) == bytes([0xA2, ord("x"), ord("y")])


def test_bits_unterminated_run_at_eof():
    codec = compile((u("a", "bits", width=4), u("b", "bits", width=4)))
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"")
    assert ei.value.kind == "truncated"
