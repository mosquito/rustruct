# 4. Try partial input

This continues the frame defined in {doc}`define-and-pack-a-structure`. A
relay reads client connections with `socket.recv()`, which returns whatever
bytes happened to be available -- not necessarily a whole frame. `unpack()`
would raise on a buffer that just ends early; there would be no way to tell
"wait for more" apart from "this is corrupt."
{py:meth}`rustruct.Struct.parse` makes that distinction: it returns
{py:class}`rustruct.Incomplete` rather than throwing when more bytes could
complete the value:

<!-- name: test_try_partial_input -->
```python
from rustruct import Incomplete, Struct, U8, U16, U32, digest, slice


class ChatFrame(Struct, byteorder="network"):
    kind: U8
    seq: U16
    body_length: U16
    body: bytes = slice(len="body_length")
    crc: U32 = digest("crc32", over="*")


wire = ChatFrame(kind=2, seq=1, body=b"hello").pack()

result = ChatFrame.parse(wire[:3])
assert isinstance(result, Incomplete)
assert result.needed > 0

decoded, position = ChatFrame.parse(wire)
```

A relay's read loop keeps calling `recv()` and appending to a buffer until
`parse()` stops returning {py:class}`rustruct.Incomplete` -- reading at
least {py:attr}`rustruct.Incomplete.needed` additional bytes before trying
again. Invalid complete data still raises
{py:exc}`rustruct.InvalidDataError`.

Previous: {doc}`decode-the-message`. Next: {doc}`inspect-an-error`.
