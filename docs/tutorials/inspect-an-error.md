# 5. Inspect an error

This continues the frame defined in {doc}`define-and-pack-a-structure`. All
public errors derive from {py:exc}`rustruct.RustructError`:

- {py:exc}`rustruct.SchemaError` means the declaration is inconsistent.
- {py:exc}`rustruct.PackError` means Python values cannot satisfy the schema.
- {py:exc}`rustruct.InvalidDataError` means input bytes violate the schema.

Pack and decode errors expose machine-readable details:

<!-- name: test_inspect_an_error -->
```python
from rustruct import InvalidDataError, Struct, U8, U16, U32, digest, slice


class ChatFrame(Struct, byteorder="network"):
    kind: U8
    seq: U16
    body_length: U16
    body: bytes = slice(len="body_length")
    crc: U32 = digest("crc32", over="*")


try:
    ChatFrame.unpack(b"\x01")
except InvalidDataError as exc:
    print(exc.kind, exc.path, exc.offset)
```

Nested errors carry a dotted field path; array failures include the item
location where applicable. A digest mismatch is its own
{py:attr}`rustruct.InvalidDataError.kind`, distinct from a truncated or
malformed buffer. A single bit flipped in transit -- the kind of damage a
noisy link can cause -- unpacks as a checksum failure, not silent garbage:

<!-- name: test_inspect_an_error -->
```python
wire = ChatFrame(kind=2, seq=1, body=b"hello").pack()
corrupted = wire[:-1] + bytes([wire[-1] ^ 0xFF])  # flip the crc's last byte

try:
    ChatFrame.unpack(corrupted)
except InvalidDataError as exc:
    assert exc.kind == "checksum"
    assert exc.path == "crc"
    assert exc.offset == 10
```

Previous: {doc}`try-partial-input`.

You now have the basic pack, unpack, and incremental-parse workflow, and a
frame format that could actually run over a socket. Continue with
{doc}`/how-to/model-tagged-unions` to give `kind` real per-type payloads
(join, text, ping), or read {doc}`/explanation/schema-model` to understand
why lengths and bounded regions behave this way.
