"""byteorder string handling: big/little/network alias, native forbidden."""

import pytest

from helpers import u
from rustruct import SchemaError, compile


def test_network_is_an_alias_for_big():
    codec = compile((u("x", "u16"),), byteorder="network")
    assert codec.unpack(b"\x12\x34") == {"x": 0x1234}
    assert codec.pack({"x": 0x1234}) == b"\x12\x34"


def test_network_field_override():
    codec = compile((u("x", "u16", byteorder="network"),), byteorder="little")
    assert codec.unpack(b"\x12\x34") == {"x": 0x1234}


def test_native_is_forbidden():
    with pytest.raises(SchemaError):
        compile((u("x", "u16"),), byteorder="native")
    with pytest.raises(SchemaError):
        compile((u("x", "u16", byteorder="native"),))


def test_unknown_byteorder_string_is_schema_error():
    with pytest.raises(SchemaError):
        compile((u("x", "u16"),), byteorder="middle")
