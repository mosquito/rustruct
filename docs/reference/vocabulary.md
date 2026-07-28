# Named constants

The core recognises a closed set of field kinds, digest algorithms, byte
orders, text encodings and error kinds. Each of these has a name you can
import, so a schema does not have to spell them as bare strings and a typo
is visible to a type checker and an editor rather than only to `compile()`.

Every one of them is an {py:class}`enum.StrEnum`, so a member **is** the
string it replaces. Naming a value never changes what gets compiled, and
plain strings keep working everywhere they worked before:

<!-- name: test_vocabulary_reference -->
```python
from rustruct import Algo, ByteOrder, Kind, compile

named = compile(
    (
        ("tag", Kind.U8, {}),
        ("crc", Kind.DIGEST, {"algo": Algo.CRC32, "over": "*"}),
    ),
    byteorder=ByteOrder.BIG,
)
plain = compile(
    (
        ("tag", "u8", {}),
        ("crc", "digest", {"algo": "crc32", "over": "*"}),
    ),
    byteorder="big",
)
assert named.pack({"tag": 7}) == plain.pack({"tag": 7})
assert Kind.U8 == "u8"
```

## Field kinds

{py:class}`rustruct.Kind` names every kind `compile()` accepts: the fixed
scalars `U8`/`I8` through `U64`/`I64`, `F32`, `F64` and `BOOL`; the byte and
text kinds `RAW`, `BYTES`, `STR` and `CSTR`; `BITS` and `FLAGS`; the
composites `STRUCT`, `ARRAY`, `SWITCH` and `COND`; and `DIGEST`.

Annotate a position that accepts one as `rustruct.vocab.KindArg`, not as
`Kind` — plain strings are part of the contract, and a bare `Kind`
annotation would reject them.

## Digest algorithms

{py:class}`rustruct.Algo` names what {py:func}`rustruct.digest` can compute.
CRCs and the Internet checksum produce an integer field; the hashes produce
`bytes` of their natural width.

| Member | Wire name | Field width | Notes |
| --- | --- | --- | --- |
| `Algo.CRC16_CCITT` | `crc16_ccitt` | 2 bytes | CRC-16/IBM-3740, also known as CCITT-FALSE |
| `Algo.CRC16_IBM` | `crc16_ibm` | 2 bytes | CRC-16/ARC |
| `Algo.CRC32` | `crc32` | 4 bytes | the usual zlib/PNG polynomial |
| `Algo.CRC32C` | `crc32c` | 4 bytes | Castagnoli |
| `Algo.CRC64_XZ` | `crc64_xz` | 8 bytes | |
| `Algo.IP` | `ip` | 2 bytes | RFC 1071 Internet checksum (IPv4 headers) |
| `Algo.MD5` | `md5` | 16 bytes | `bytes`, not an integer |
| `Algo.SHA1` | `sha1` | 20 bytes | `bytes`, not an integer |
| `Algo.SHA256` | `sha256` | 32 bytes | `bytes`, not an integer |

Pass `poly=`, `init=`, `xorout=`, `refin=` or `refout=` to vary a CRC model
away from its preset.

## Byte order

{py:class}`rustruct.ByteOrder` has `BIG`, `LITTLE` and `NETWORK`. `NETWORK`
compiles to exactly what `BIG` does, and documents intent the way the
standard library's `struct` format character `!` does. It is a member in its
own right rather than an `enum` alias, so it carries the value `"network"`
and does not compare equal to `BIG`.

There is deliberately no `NATIVE`: the core refuses it, because it would
make the wire format depend on whichever machine happened to do the
encoding.

## Text encodings

{py:class}`rustruct.Encoding` has `UTF8`, `ASCII` and `LATIN1` — a much
smaller set than Python's own codec registry, because these three are what
the core implements. Spellings are normalised exactly as the core normalises
them, so `"UTF-8"`, `"utf8"` and `"utf_8"` all resolve to `Encoding.UTF8`,
and `"us-ascii"` and `"iso-8859-1"` resolve to `Encoding.ASCII` and
`Encoding.LATIN1`.

`errors=` is not an enum: the core implements exactly one policy,
`"strict"`.

## Flag rest policy

{py:class}`rustruct.RestPolicy` decides what a `flags` field does with bits
that no name in it covers: `KEEP` reports them under the
{py:data}`rustruct.REST_KEY` key (`"_rest"`) and accepts one back on pack,
`STRICT` rejects any uncovered set bit, and `IGNORE` drops them on read and
writes zeros.

## Error kinds

{py:class}`rustruct.ErrorKind` names every value that can appear as
{py:attr}`InvalidDataError.kind <rustruct.InvalidDataError>` or
{py:attr}`PackError.kind <rustruct.PackError>`, so branching on one does not
depend on retyping a string correctly:

<!-- name: test_error_kind_reference -->
```python
from rustruct import ErrorKind, InvalidDataError, compile

codec = compile((("x", "u32", {}),))
try:
    codec.unpack(b"\x00")
except InvalidDataError as exc:
    assert exc.kind == ErrorKind.TRUNCATED
```

Unpack can report `TRUNCATED`, `TRAILING`, `RANGE`, `NEGATIVE_LEN`,
`OVERFLOW`, `DIV_ZERO`, `UNTERMINATED`, `NUL_IN_CSTR`, `NO_CASE`, `DECODE`,
`LIMIT`, `DEPTH`, `CHECKSUM`, `CONST` and `RESERVED_BITS`. Pack can report
`MISSING`, `LENGTH`, `INDIVISIBLE`, `INCONSISTENT`, `BUFFER`,
`UNKNOWN_FLAG`, `TYPE` and `ENCODE`.

`DEPTH` is the one a schema from {py:func}`rustruct.compile` cannot produce:
unpacking allows 64 nested structures, and a schema needing more is refused
at compile time rather than left to fail on every input.

## Expression operators

{py:class}`rustruct.BinOp` names the heads an expression tuple can carry:
`ADD`, `SUB`, `MUL`, `DIV`, `SHL`, `SHR`, `AND`, `OR`, `XOR`, `EQ`, `NE`,
`LT`, `LE`, `GT` and `GE`. Ordinary Python operators on a field reference
build these, so it is mainly of interest when writing expression tuples by
hand — see {doc}`low-level-schema`.

## Signatures

```{eval-rst}
.. autoclass:: rustruct.Kind
   :members:
   :undoc-members:
.. autoclass:: rustruct.Algo
   :members:
   :undoc-members:
.. autoclass:: rustruct.ByteOrder
   :members:
   :undoc-members:
.. autoclass:: rustruct.Encoding
   :members:
   :undoc-members:
.. autoclass:: rustruct.RestPolicy
   :members:
   :undoc-members:
.. autoclass:: rustruct.ErrorKind
   :members:
   :undoc-members:
.. autoclass:: rustruct.BinOp
   :members:
   :undoc-members:
.. py:data:: rustruct.REST_KEY

   The key :attr:`RestPolicy.KEEP <rustruct.RestPolicy>` reports uncovered
   flag bits under: ``"_rest"``.
```
