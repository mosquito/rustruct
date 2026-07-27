"""`pack_into`, buffer-protocol handling, and Mapping-input edge cases."""

import pytest

from helpers import u
from rustruct import PackError, compile


def test_pack_into():
    codec = compile((u("x", "u16"),))
    buf = bytearray(6)
    pos = codec.pack_into(buf, 2, {"x": 0x0102})
    assert pos == 4
    assert bytes(buf) == b"\x00\x00\x01\x02\x00\x00"


def test_pack_into_buffer_too_small():
    codec = compile((u("x", "u16"),))
    buf = bytearray(6)
    with pytest.raises(PackError) as ei:
        codec.pack_into(buf, 5, {"x": 1})
    assert ei.value.kind == "buffer"


def test_pack_into_rejects_readonly_buffer():
    codec = compile((u("x", "u16"),))
    with pytest.raises(TypeError):
        codec.pack_into(b"\x00\x00", 0, {"x": 1})


def test_pack_into_offset_zero_full_buffer():
    codec = compile((u("x", "u8"), u("y", "u8")))
    buf = bytearray(2)
    pos = codec.pack_into(buf, 0, {"x": 1, "y": 2})
    assert pos == 2
    assert bytes(buf) == b"\x01\x02"


def test_pack_accepts_arbitrary_mapping_via_items():
    codec = compile((u("x", "u8"),))

    class MyMapping:
        def items(self):
            return [("x", 5), ("extra", object())]

    assert codec.pack(MyMapping()) == b"\x05"


def test_pack_rejects_non_mapping():
    codec = compile((u("x", "u8"),))
    with pytest.raises(TypeError):
        codec.pack([("x", 1)])


def test_pack_into_rejects_non_mapping():
    codec = compile((u("x", "u8"),))
    with pytest.raises(TypeError):
        codec.pack_into(bytearray(1), 0, [("x", 1)])
