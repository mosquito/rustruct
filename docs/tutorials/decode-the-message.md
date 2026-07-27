# 3. Decode the message

This continues the frame defined in {doc}`define-and-pack-a-structure`. A
relay receiving a complete frame from a client decodes it back into the same
fields it started from. {py:meth}`rustruct.Struct.unpack` requires the whole
buffer to contain exactly one value:

<!-- name: test_decode_the_message -->
```python
from rustruct import Struct, U8, U16, U32, digest, slice


class ChatFrame(Struct, byteorder="network"):
    kind: U8
    seq: U16
    body_length: U16
    body: bytes = slice(len="body_length")
    crc: U32 = digest("crc32", over="*")


wire = ChatFrame(kind=2, seq=1, body=b"hello").pack()

decoded = ChatFrame.unpack(wire)
assert decoded.seq == 1
assert decoded.body == b"hello"
```

Use {py:meth}`rustruct.Struct.unpack_from` when a buffer has a tail -- for
example, the next frame already sitting in the same read buffer:

<!-- name: test_decode_the_message -->
```python
decoded, position = ChatFrame.unpack_from(wire + b"tail")
assert position == len(wire)
```

Both methods accept any contiguous object implementing the buffer protocol,
including `bytes`, `bytearray`, and `memoryview`.

Previous: {doc}`define-and-pack-a-structure`. Next: {doc}`try-partial-input`.
