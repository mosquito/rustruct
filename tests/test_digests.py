"""`digest`: CRC/hash presets, scope-exit verification, `over` forms."""

import hashlib
import zlib

import pytest

from helpers import u
from rustruct import InvalidDataError, SchemaError, compile


def test_png_chunk_crc32():
    codec = compile(
        (
            u("length", "u32"),
            u("ctype", "raw", len=4),
            u("data", "bytes", len=("ref", "length")),
            u("crc", "digest", algo="crc32", over=("ctype", "data")),
        )
    )
    packed = codec.pack({"ctype": b"IDAT", "data": b"payload"})
    assert packed[:4] == (7).to_bytes(4, "big")
    expected_crc = zlib.crc32(b"IDATpayload")
    assert packed[-4:] == expected_crc.to_bytes(4, "big")

    values = codec.unpack(packed)
    assert values["crc"] == expected_crc

    bad = bytearray(packed)
    bad[6] ^= 0xFF
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(bytes(bad))
    assert ei.value.kind == "checksum"
    assert ei.value.path == "crc"


def test_digest_verify_false():
    codec = compile(
        (
            u("data", "bytes", len=2),
            u("crc", "digest", algo="crc32", over=("data",), verify=False),
        )
    )
    values = codec.unpack(b"ab\x00\x00\x00\x00")
    assert values["crc"] == 0


def test_digest_ignores_mapping_value():
    codec = compile(
        (
            u("data", "bytes", len=2),
            u("crc", "digest", algo="crc16_ccitt", over=("data",)),
        )
    )
    a = codec.pack({"data": b"hi"})
    b = codec.pack({"data": b"hi", "crc": 0xDEAD})
    assert a == b


def test_ipv4_checksum():
    codec = compile(
        (
            u("version", "bits", width=4),
            u("ihl", "bits", width=4),
            u("tos", "u8"),
            u("total_length", "u16"),
            u("ident", "u16"),
            u("frag", "u16"),
            u("ttl", "u8"),
            u("proto", "u8"),
            u("checksum", "digest", algo="ip", over="*"),
            u("src", "u32"),
            u("dst", "u32"),
        )
    )
    header = {
        "version": 4,
        "ihl": 5,
        "tos": 0,
        "total_length": 0x73,
        "ident": 0,
        "frag": 0x4000,
        "ttl": 64,
        "proto": 17,
        "src": 0xC0A80001,
        "dst": 0xC0A800C7,
    }
    packed = codec.pack(header)
    assert packed[10:12] == bytes([0xB8, 0x61])
    values = codec.unpack(packed)
    assert values["checksum"] == 0xB861
    assert codec.pack(values) == packed


def test_sha256_digest():
    codec = compile(
        (
            u("data", "bytes", len=3),
            u("hash", "digest", algo="sha256", over=("data",)),
        )
    )
    packed = codec.pack({"data": b"abc"})
    assert packed[3:] == hashlib.sha256(b"abc").digest()
    assert codec.unpack(packed)["hash"] == hashlib.sha256(b"abc").digest()


def test_md5_and_sha1_digest():
    codec_md5 = compile((u("data", "bytes", len=3), u("hash", "digest", algo="md5", over=("data",))))
    packed = codec_md5.pack({"data": b"abc"})
    assert packed[3:] == hashlib.md5(b"abc").digest()

    codec_sha1 = compile((u("data", "bytes", len=3), u("hash", "digest", algo="sha1", over=("data",))))
    packed = codec_sha1.pack({"data": b"abc"})
    assert packed[3:] == hashlib.sha1(b"abc").digest()


def test_digest_over_unknown_name_is_schema_error():
    with pytest.raises(SchemaError):
        compile((u("crc", "digest", algo="crc32", over=("nope",)),))


def test_digest_self_coverage_is_schema_error():
    with pytest.raises(SchemaError):
        compile((u("crc", "digest", algo="crc32", over=("crc",)),))


def test_crc_overrides_on_hash_is_schema_error():
    with pytest.raises(SchemaError):
        compile(
            (
                u("data", "bytes", len=1),
                u("h", "digest", algo="sha256", over=("data",), poly=7),
            )
        )


def test_unknown_digest_algo_is_schema_error():
    with pytest.raises(SchemaError):
        compile((u("data", "bytes", len=1), u("h", "digest", algo="crc99", over=("data",))))


def test_nested_digest_innermost_verified_first():
    codec = compile(
        (
            u(
                "inner",
                "struct",
                fields=(
                    u("data", "bytes", len=2),
                    u("inner_crc", "digest", algo="crc16_ccitt", over=("data",)),
                ),
            ),
            u("outer_crc", "digest", algo="crc32", over="*"),
        )
    )
    packed = codec.pack({"inner": {"data": b"ab"}})
    codec.unpack(packed)  # sanity round-trip

    bad = bytearray(packed)
    bad[0] ^= 0xFF
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(bytes(bad))
    assert ei.value.kind == "checksum"
    assert ei.value.path == "inner.inner_crc"
