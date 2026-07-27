# How to compute and verify a digest

{py:func}`rustruct.digest` computes a checksum or hash during pack and optionally verifies it
during unpack:

<!-- name: test_compute_and_verify_a_digest -->
```python
from rustruct import Struct, U8, U32, digest, slice


class Chunk(Struct):
    size: U8
    data: bytes = slice(len="size")
    crc: U32 = digest("crc32", over=("data",))


chunk = Chunk(data=b"abc")
wire = chunk.pack()
assert wire == bytes.fromhex("03 616263 352441c2")
assert Chunk.unpack(wire).crc == 0x352441C2
```

`over=("data",)` names a specific sibling span rather than `"*"` the whole
structure, so the checksum covers only `data`, not `size`:

| Offset | Bytes | Field  | Value                |
| -----: | ----: | ------ | --------------------- |
|      0 |     1 | `size` | `0x03`                |
|      1 |     3 | `data` | `b"abc"`               |
|      4 |     4 | `crc`  | `0x352441c2`           |

Built-in algorithms include CRC presets, IPv4's Internet checksum, MD5,
SHA-1, and SHA-256. `over="*"` covers the enclosing scope with the digest's
own bytes treated as zero. A tuple covers named sibling spans.

A hash algorithm works the same way as a CRC -- only the algorithm name and
the resulting field's width change. A hash digest is a `bytes` field, not an
integer: its width is whatever the algorithm produces (16 bytes for MD5, 32
for SHA-256), not something declared separately:

<!-- name: test_compute_and_verify_a_digest -->
```python
import hashlib


class HashedChunk(Struct):
    size: U8
    data: bytes = slice(len="size")
    checksum: bytes = digest("sha256", over=("data",))


hashed = HashedChunk.unpack(HashedChunk(data=b"abc").pack())
assert hashed.checksum == hashlib.sha256(b"abc").digest()
```

`"md5"` and `"sha1"` work the same way, each with their own fixed digest
width.

Verification failures raise {py:exc}`rustruct.InvalidDataError`(kind="checksum").
