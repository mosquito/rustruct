"""`parse` / `Incomplete`: the streaming contract."""

from helpers import u
from rustruct import Incomplete, compile


def test_parse_incomplete_is_falsy():
    codec = compile((u("x", "u32"),))
    r = codec.parse(b"\x01\x02")
    assert isinstance(r, Incomplete)
    assert not r
    assert r.needed == 2


def test_parse_ok_returns_tuple():
    codec = compile((u("x", "u16"),))
    r = codec.parse(b"\x01\x02\x03")
    assert r
    values, pos = r
    assert values == {"x": 0x0102}
    assert pos == 2


def test_empty_buffer_is_incomplete():
    codec = compile((u("x", "u32"),))
    r = codec.parse(b"")
    assert isinstance(r, Incomplete)
    assert r.needed == 4


def test_incomplete_inside_dynamic_field():
    codec = compile((u("n", "u8"), u("data", "bytes", len=("ref", "n"))))
    r = codec.parse(b"\x05ab")
    assert isinstance(r, Incomplete)
    assert r.needed == 3


def test_incomplete_monotonic_progress():
    codec = compile(
        (
            u("magic", "u8", const=0x7F),
            u("name", "cstr", max=32),
            u("blen", "u8"),
            u("body", "struct", fields=(u("items", "array", elem=("u16", {}), until_eof=True),), size=("ref", "blen")),
            u("crc", "digest", algo="crc32", over="*"),
        )
    )
    full = codec.pack({"name": "stream", "body": {"items": [10, 20, 30]}})
    assert codec.unpack(full)["body"]["items"] == [10, 20, 30]

    i = 0
    while i < len(full):
        r = codec.parse(full[:i])
        if isinstance(r, Incomplete):
            assert r.needed > 0
            assert i + r.needed <= len(full)
            i += r.needed
        else:
            break
    assert codec.parse(full)


def test_incomplete_repr():
    codec = compile((u("x", "u32"),))
    r = codec.parse(b"")
    assert repr(r) == "Incomplete(needed=4)"
