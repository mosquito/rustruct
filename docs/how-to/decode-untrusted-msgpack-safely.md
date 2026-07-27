# How to decode untrusted, deeply-nested MessagePack safely

A MessagePack array or map can hold values of its own type, to unbounded
depth. A decoder written the obvious way -- one Python function that calls
itself for each nested container -- risks `RecursionError` on maliciously or
accidentally deep input, since each level of nesting consumes another Python
call frame.

{py:func}`rustruct.formats.msgpack.decode`/
{py:func}`decode_from <rustruct.formats.msgpack.decode_from>` don't have this problem: they
walk the buffer with an explicit work stack instead of Python recursion, so
nesting depth is bounded only by available memory:

<!-- name: test_decode_untrusted_msgpack_safely -->
```python
from rustruct.formats.msgpack import decode_from

# 50,000 nested single-element arrays, terminated by nil -- hand-built,
# since a naive recursive encoder couldn't construct this many levels
# either.
depth = 50_000
wire = bytes([0x91]) * depth + b"\xc0"

value, offset = decode_from(wire, 0)
assert offset == len(wire)

seen = 0
while isinstance(value, list):
    seen += 1
    value = value[0] if value else None
assert seen == depth
```

{py:func}`encode() <rustruct.formats.msgpack.encode>` is iterative the same way, so building a deeply-nested Python
structure and encoding it doesn't risk `RecursionError` either -- only
constructing the Python object itself does, which is governed by Python's
own limits, not `rustruct`'s.

This doesn't bound memory or time: an attacker can still send a buffer that
decodes into an enormous structure. Apply your own limits (a maximum input
size, a maximum element count while walking the decoded value) the same way
you would for any other untrusted, dynamically-sized input.
