# Declarative schema language

This page describes the wire vocabulary accepted by {py:class}`rustruct.Struct`.
Function signatures are listed in {doc}`fields`.

## Byte order

A structure accepts `byteorder="big"`, `"little"`, or `"network"`.
`"network"` aliases `"big"`. Native byte order is not supported.

Byte order follows Python subclassing: `class Child(Base)` with no
`byteorder=` of its own uses `Base`'s. A structure used as another
structure's *field* is a different relationship -- composition, not
subclassing -- and does not inherit that way: with no `byteorder=` of its
own, a nested structure defaults to `"big"` regardless of what the
structure it's nested in declares. Give a nested structure its own
explicit `byteorder=` whenever it should match, or differ from, its
container.

## Scalar annotations

| Annotation | Python value | Width |
| --- | --- | ---: |
| {py:class}`I8 <rustruct.I8>` / {py:class}`U8 <rustruct.U8>` | `int` | 1 byte |
| {py:class}`I16 <rustruct.I16>` / {py:class}`U16 <rustruct.U16>` | `int` | 2 bytes |
| {py:class}`I32 <rustruct.I32>` / {py:class}`U32 <rustruct.U32>` | `int` | 4 bytes |
| {py:class}`I64 <rustruct.I64>` / {py:class}`U64 <rustruct.U64>` | `int` | 8 bytes |
| {py:class}`F32 <rustruct.F32>` / {py:class}`F64 <rustruct.F64>` | `float` | 4 / 8 bytes |
| {py:class}`Bool <rustruct.Bool>` | `bool` | 1 byte |

Integer values are range-checked during pack.

## Field descriptors

Every field is either a bare annotation (`name: Type`, for a scalar or a
nested `Struct`) or an annotation with a descriptor as its default
(`name: Type = descriptor(...)`) for anything the annotation alone can't
express -- a runtime-determined length, a bit width, a conversion, or a
computed value.

| Descriptor | Python value | Wire extent |
| --- | --- | --- |
| {py:func}`raw <rustruct.raw>`(length) | `bytes` | exact constant length |
| {py:func}`slice <rustruct.slice>`(len=...) | `bytes` | expression or current-region remainder |
| {py:func}`string <rustruct.string>`(len=...) | `str` | encoded byte length |
| {py:func}`cstring <rustruct.cstring>`(max=...) | `str` | through a NUL terminator |
| {py:func}`bits <rustruct.bits>`(width) | `int` | bit width inside a byte-aligned run |
| {py:func}`array <rustruct.array>`(elem, count=...) | `list` | element count expression |
| {py:func}`array <rustruct.array>`(elem, until_eof=True) | `list` | current-region remainder |
| {py:func}`sized <rustruct.sized>`(type, size=...) | typed nested value | exact byte window |
| {py:func}`when <rustruct.when>`(pred, then=...) | branch value or default | selected branch or zero bytes |
| {py:func}`switch <rustruct.switch>`(on, cases, default) | selected branch value | selected branch |
| {py:func}`convert <rustruct.convert>`(base, decode, encode) | converted value | base field extent |
| {py:func}`digest <rustruct.digest>`(algorithm, over=...) | `int` or `bytes` | algorithm width |
| {py:func}`described <rustruct.described>`(...) | base value | base field extent |

### Every descriptor, once

Each of these is a complete, minimal, runnable declaration. `const=` fixes
a value the caller never has to supply:

<!-- name: test_schema_language_raw -->
```python
from rustruct import Struct, raw


class Magic(Struct):
    signature: bytes = raw(4, const=b"RSTC")


assert Magic().pack() == b"RSTC"
```

`len=` accepts a constant, a sibling name, `"*"` for the region remainder,
or an expression lambda:

<!-- name: test_schema_language_slice -->
```python
from rustruct import Struct, U8, slice


class Trailer(Struct):
    kind: U8
    data: bytes = slice(len="*")


assert Trailer(kind=1, data=b"xy").pack() == b"\x01xy"
```

`string()` follows the same `len=` rules as `slice()`, decoding to `str`:

<!-- name: test_schema_language_string -->
```python
from rustruct import Struct, U8, string


class Label(Struct):
    size: U8
    text: str = string(len="size")


assert Label(text="hi").pack() == b"\x02hi"
```

`cstring()` has no length field at all -- pack writes a NUL after the text,
unpack reads through the first one:

<!-- name: test_schema_language_cstring -->
```python
from rustruct import Struct, cstring


class Tag(Struct):
    name: str = cstring(max=16)


assert Tag(name="abc").pack() == b"abc\x00"
```

Consecutive `bits()` fields share one byte-aligned run, packed
most-significant bit first:

<!-- name: test_schema_language_bits -->
```python
from rustruct import Struct, bits


class Flags(Struct):
    a: int = bits(2)
    b: int = bits(6)


assert Flags(a=1, b=5).pack() == b"\x45"
```

`count=` derives the count field from `len(...)` on pack; `until_eof=True`
instead repeats until the current region ends:

<!-- name: test_schema_language_array -->
```python
from rustruct import Struct, U16, array


class Words(Struct):
    values: list = array(U16, until_eof=True)


assert Words(values=[1, 2, 3]).pack() == b"\x00\x01\x00\x02\x00\x03"
```

`sized()` gives a nested structure an exact byte window -- `size` is
derived on pack, and the nested value cannot read past it on unpack:

<!-- name: test_schema_language_sized -->
```python
from rustruct import Struct, U16, sized, slice


class Body(Struct):
    payload: bytes = slice(len="*")


class Envelope(Struct):
    size: U16
    body: object = sized(Body, size="size")


assert Envelope(body=Body(payload=b"hi")).pack() == b"\x00\x02hi"
```

`when()` includes `then` only when `pred` (a field name or expression
lambda) is truthy; otherwise the field consumes zero wire bytes:

<!-- name: test_schema_language_when -->
```python
from rustruct import Struct, U8, U16, described, when


class Msg(Struct):
    has_extra: U8 = described(0)
    extra: object = when(pred="has_extra", then=U16, default=None)


assert Msg(has_extra=1, extra=9).pack() == b"\x01\x00\x09"
assert Msg(has_extra=0).pack() == b"\x00"
```

`switch()` selects a branch by exact match on `on`; `default` (optional)
handles every other tag:

<!-- name: test_schema_language_switch -->
```python
from rustruct import Struct, U8, U16, slice, switch


class Frame(Struct):
    kind: U8
    body: object = switch(on="kind", cases={1: U16}, default=slice(len="*"))


assert Frame(kind=1, body=0x1234).pack() == b"\x01\x12\x34"
```

`convert()` wraps a base scalar/descriptor with Python-level `decode`/
`encode`; the core still only ever sees the base wire value:

<!-- name: test_schema_language_convert -->
```python
import ipaddress

from rustruct import Struct, U32, convert


class Endpoint(Struct):
    address: object = convert(U32, decode=ipaddress.IPv4Address, encode=int)


assert Endpoint(address=ipaddress.IPv4Address("192.0.2.1")).pack() == b"\xc0\x00\x02\x01"
```

`digest()` is always computed on pack, ignoring any caller-supplied value,
and verified on unpack unless `verify=False`:

<!-- name: test_schema_language_digest -->
```python
from rustruct import Struct, U8, U32, digest, slice


class Chunk(Struct):
    size: U8
    data: bytes = slice(len="size")
    crc: U32 = digest("crc32", over=("data",))


assert Chunk(data=b"abc").pack() == bytes.fromhex("03 616263 352441c2")
```

`described()` attaches only a construction default and/or help text; the
wire kind still comes from the annotation:

<!-- name: test_schema_language_described -->
```python
from rustruct import Struct, U8, described


class Header(Struct):
    version: U8 = described(1, help="wire format version")


assert Header().pack() == b"\x01"
```

### Combining several

A derived byte length, encoded text, and a counted array in one structure:

<!-- name: test_reference_schema_language -->
```python
from rustruct import Struct, U8, U16, array, string


class Catalog(Struct, byteorder="big"):
    name_size: U8
    name: str = string(len="name_size")
    item_count: U8
    items: list = array(U16, count="item_count")


catalog = Catalog(name="tools", items=[10, 20])
wire = catalog.pack()
assert wire == b"\x05tools\x02\x00\x0a\x00\x14"
assert Catalog.unpack(wire) == Catalog(
    name_size=5,
    name="tools",
    item_count=2,
    items=[10, 20],
)
```

{py:func}`rustruct.string` supports UTF-8, ASCII, and Latin-1 with strict
errors. Lengths are encoded byte counts, not Unicode code-point counts.

## Length, count, and size expressions

A descriptor option accepts:

- an integer constant;
- `"*"` for the remainder of the current region;
- a preceding field name;
- a lambda over the current or enclosing field namespace.

References must be backward-resolvable during unpack. They cannot reach into a
structure that has not yet been decoded.

When {py:func}`rustruct.slice`(len=...), {py:func}`rustruct.string`(len=...),
{py:func}`rustruct.array`(count=...), or {py:func}`rustruct.sized`(size=...)
refers to a sibling scalar, pack derives that scalar from the encoded value.
Derived fields remain visible after unpack. A {py:func}`rustruct.switch`
discriminant remains explicit input because a branch does not necessarily
identify one unique tag. A field referenced both ways -- as a length/count/
size source in some branches and as a switch discriminant in others -- stays
explicit input everywhere; pack checks it against the actual data instead of
computing it.

## Regions and exact consumption

{py:meth}`rustruct.Struct.unpack` requires exact top-level consumption.
{py:meth}`rustruct.Struct.unpack_from` permits a tail and returns the new
position. {py:func}`rustruct.sized` creates an exact nested region: the
nested value cannot cross its endpoint and must consume the region
completely.

Greedy slices and arrays consume the remainder of their current region. A
partial final array item is invalid.

## Bits, constants, and digests

Consecutive {py:func}`rustruct.bits` fields are packed most-significant bit
first and must end on a byte boundary. `signed=True` selects signed
interpretation. `const=` declares a value that is emitted automatically and
verified during unpack.

Digest algorithms include CRC presets, the IPv4 Internet checksum, MD5,
SHA-1, and SHA-256. `over="*"` covers the enclosing region while treating the
digest field itself as zero. A tuple names sibling spans.

## Limits

Field-level `max=` bounds dynamic bytes or text on both sides: unpack
rejects wire data whose length would exceed it, and pack rejects a
Python value that already exceeds it, before writing anything --
a value that violates `max=` never silently produces wire bytes this
same schema could not unpack again:

<!-- name: test_schema_language_max_limit -->
```python
from rustruct import PackError, Struct, U8, slice


class Blob(Struct):
    length: U8
    data: bytes = slice(len="length", max=4)


try:
    Blob(data=b"toolong").pack()
except PackError as exc:
    assert exc.kind == "limit"
```

{py:func}`rustruct.compile`(max_default=...) sets the fallback dynamic
allocation limit, and {py:func}`rustruct.compile`(max_count=...) bounds
array element counts. Both compile options have finite defaults.
