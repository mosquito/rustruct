# 2. Define and pack a structure

A relay message needs a type tag (`kind`), a sequence number the server uses
to notice dropped messages, and a body of arbitrary text. Choose a byte order
on the class and annotate fields in wire order:

<!-- name: test_define_and_pack_a_structure -->
```python
from rustruct import Struct, U8, U16, U32, digest, slice


class ChatFrame(Struct, byteorder="network"):
    kind: U8
    seq: U16
    body_length: U16
    body: bytes = slice(len="body_length")
    crc: U32 = digest("crc32", over="*")


frame = ChatFrame(kind=2, seq=1, body=b"hello")
assert frame.body_length is None  # nothing to supply -- pack() derives it
assert frame.crc is None  # nothing to supply -- pack() always computes it

wire = frame.pack()
assert wire == bytes.fromhex("02 0001 0005 68656c6c6f 1aabbe0e")

decoded = ChatFrame.unpack(wire)
assert decoded.body == b"hello"
assert decoded.crc == 0x1AABBE0E
```

`"network"` is an alias for big-endian. The other accepted orders are `"big"`
and `"little"`. Native byte order is intentionally unsupported so a schema
has the same wire representation on every machine.

Laid out on the wire, those 14 bytes are:

| Offset | Bytes | Field         | Value        |
| -----: | ----: | ------------- | ------------ |
|      0 |     1 | `kind`        | `0x02`       |
|      1 |     2 | `seq`         | `0x0001`     |
|      3 |     2 | `body_length` | `0x0005`     |
|      5 |     5 | `body`        | `b"hello"`   |
|     10 |     4 | `crc`         | `0x1aabbe0e` |

`kind` and `seq` are data: values the caller supplies. `body_length` and
`crc` are different -- they're computed *from* the rest of the frame, so
pack always fills them in and a caller-supplied value would just be
overwritten. `body_length` is derived because `body` references it by name
(`slice(len="body_length")`); {py:func}`rustruct.digest`("crc32", over="*")
covers every other field, including the derived `body_length`, with the
`crc` field's own bytes counting as zero while computing.

A relay that only ever sent fixed `kind` values wouldn't need much more than
this. Real messages come in different shapes though -- a join notice carries
a username, a text message carries a body, a ping carries nothing -- and
`kind` is exactly the field that should pick between them.
{doc}`/how-to/model-tagged-unions` covers that next step. See
{doc}`/how-to/model-computed-fields` for computed fields in general, and
{doc}`/how-to/compute-and-verify-a-digest` for other digest algorithms and
narrower coverage.

Previous: {doc}`install-rustruct`. Next: {doc}`decode-the-message`.
