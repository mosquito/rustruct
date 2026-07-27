# How to decode and encode MessagePack values

## Decode a value

{py:func}`rustruct.formats.msgpack.decode` turns a buffer holding exactly one
MessagePack value into the matching Python object -- `None`, `bool`, `int`,
`float`, `bytes`, `str`, `list`, or `dict`:

<!-- name: test_decode_msgpack_values -->
```python
from rustruct.formats.msgpack import decode

assert decode(b"\xc0") is None
assert decode(b"\x2a") == 42
assert decode(b"\xa5hello") == "hello"
assert decode(b"\x92\x01\x02") == [1, 2]
assert decode(b"\x81\xa1a\x01") == {"a": 1}
```

Use {py:func}`decode_from(buf, offset) <rustruct.formats.msgpack.decode_from>`
instead when the buffer holds more than one
value back to back, or more data than just the value (a length-prefixed
frame, for example): it returns `(value, new_offset)` rather than raising on
trailing bytes, so the caller can keep decoding from where it left off:

<!-- name: test_decode_msgpack_values -->
```python
from rustruct.formats.msgpack import decode_from

wire = b"\x01\xa3two\x93\x03\x03\x03"  # 1, "two", [3, 3, 3]
first, offset = decode_from(wire, 0)
second, offset = decode_from(wire, offset)
third, offset = decode_from(wire, offset)
assert (first, second, third) == (1, "two", [3, 3, 3])
assert offset == len(wire)
```

{py:func}`decode() <rustruct.formats.msgpack.decode>` is
{py:func}`decode_from() <rustruct.formats.msgpack.decode_from>` plus a check
that nothing follows the one value it read.

## Encode a value

{py:func}`rustruct.formats.msgpack.encode` takes a Python `None`/`bool`/`int`/
`float`/`bytes`/`str`/`list`/`dict` value (containers may nest any of these,
including further containers) and returns its MessagePack encoding, choosing
the most compact tag that fits -- the same preference the reference
implementation uses, byte for byte, on the common cases:

<!-- name: test_encode_msgpack_values -->
```python
from rustruct.formats.msgpack import encode

assert encode(None) == b"\xc0"
assert encode(42) == b"\x2a"
assert encode(-1) == b"\xff"
assert encode("hello") == b"\xa5hello"
assert encode([1, 2]) == b"\x92\x01\x02"
assert encode({"a": 1}) == b"\x81\xa1a\x01"
```

{py:func}`decode() <rustruct.formats.msgpack.decode>` reverses it:

<!-- name: test_encode_msgpack_values -->
```python
from rustruct.formats.msgpack import decode

value = {"name": "sensor-1", "readings": [20.5, 21.0, 19.75], "active": True}
wire = encode(value)
assert decode(wire) == value
```

Dict keys keep their insertion order on the wire; MessagePack itself has no
ordering requirement, but {py:func}`encode() <rustruct.formats.msgpack.encode>`/
{py:func}`decode() <rustruct.formats.msgpack.decode>` agree with each other
and with `dict`'s own iteration order.
