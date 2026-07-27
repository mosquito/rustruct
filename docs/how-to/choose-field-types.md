# How to choose field types

Choose a field according to the value your application should receive and
how its wire width is determined:

## Encode fixed-width values

| Annotation | Python value | Width |
| --- | --- | ---: |
| {py:class}`rustruct.I8` / {py:class}`rustruct.U8` | `int` | 1 byte |
| {py:class}`rustruct.I16` / {py:class}`rustruct.U16` | `int` | 2 bytes |
| {py:class}`rustruct.I32` / {py:class}`rustruct.U32` | `int` | 4 bytes |
| {py:class}`rustruct.I64` / {py:class}`rustruct.U64` | `int` | 8 bytes |
| {py:class}`rustruct.F32` / {py:class}`rustruct.F64` | `float` | 4 / 8 bytes |
| {py:class}`rustruct.Bool` | `bool` | 1 byte |

Integer values are range-checked. Scalars use the containing structure's byte
order unless a nested structure declares its own.

## Store fixed bytes

{py:func}`rustruct.raw`(length) reads and writes exactly `length` bytes:

<!-- name: test_choose_field_types -->
```python
from rustruct import Struct, U16, raw


class EthernetName(Struct):
    kind: U16
    address: bytes = raw(6)


value = EthernetName(kind=1, address=bytes.fromhex("001122334455"))
assert value.pack() == bytes.fromhex("0001 001122334455")
```

| Offset | Bytes | Field     | Value                 |
| -----: | ----: | --------- | ---------------------- |
|      0 |     2 | `kind`    | `0x0001`                |
|      2 |     6 | `address` | `00 11 22 33 44 55`     |

Use `const=` for signatures and magic bytes. A const field is verified on
unpack and does not need to be supplied on pack.

## Store runtime-sized bytes

{py:func}`rustruct.slice` represents an owned `bytes` value:

<!-- name: test_choose_field_types -->
```python
from rustruct import Struct, U8, slice


class Blob(Struct):
    length: U8
    data: bytes = slice(len="length")
```

`len` can be a constant, `"*"`, a field name, or an expression lambda.
`max` adds a schema-specific allocation limit.

## Decode text

{py:func}`rustruct.string` uses the same byte-length rules and decodes to `str`. Supported
encodings are UTF-8, ASCII, and Latin-1; errors are strict.

<!-- name: test_choose_field_types -->
```python
from rustruct import Struct, U8, string


class Label(Struct):
    size: U8
    text: str = string(len="size")
```

Lengths count encoded bytes, not Unicode code points. {py:func}`rustruct.cstring` reads a
NUL-terminated string and should normally specify `max=`.

## Convert a wire value

{py:func}`rustruct.convert` wraps a scalar or descriptor with Python-level decode and encode
functions:

<!-- name: test_choose_field_types -->
```python
import ipaddress

from rustruct import Struct, U32, convert


class Endpoint(Struct):
    address: object = convert(
        U32,
        decode=ipaddress.IPv4Address,
        encode=int,
    )
```

The core still handles the base wire value; conversion happens at the typed
frontend boundary.

## Add defaults and descriptions

{py:func}`rustruct.described` attaches a default, default factory, and help text to a plain
scalar or nested-structure annotation:

<!-- name: test_choose_field_types -->
```python
from rustruct import Struct, U8, described


class Header(Struct):
    version: U8 = described(1, help="wire format version")
```

Help is schema metadata intended for inspection and generated documentation.
