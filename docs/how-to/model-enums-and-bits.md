# How to model enums and bit fields

Use these techniques when a wire integer really represents a closed set of
named values, or when several small fields share a single byte-aligned run:

## Expose an integer as an enum

Wire scalars decode to integers. Use {py:func}`rustruct.convert` when the public value should
be an enum:

<!-- name: test_model_enums_and_bits -->
```python
import enum

from rustruct import Struct, U8, convert


class Status(enum.IntEnum):
    OK = 0
    ERROR = 1


class Reply(Struct):
    status: object = convert(U8, decode=Status, encode=int)
```

A closed `IntEnum` rejects unknown values while decoding. For extensible
protocols, use an enum implementation that preserves unknown integers or
leave the field as a plain scalar.

## Pack adjacent bit fields

Consecutive {py:func}`rustruct.bits` fields are packed most-significant bit first:

<!-- name: test_model_enums_and_bits -->
```python
from rustruct import Struct, bits, slice


class Fragment(Struct):
    reserved: int = bits(1, default=0)
    df: int = bits(1, default=0)
    mf: int = bits(1, default=0)
    offset: int = bits(13, default=0)


fragment = Fragment(df=True)
assert fragment.pack() == b"\x40\x00"
assert Fragment.unpack(b"\x40\x00").df == 1
```

Those 16 bits (`0x40 0x00` = `0100 0000 0000 0000`), most significant bit
first:

| Bit(s) | Field      | Value in `fragment.pack()` |
| -----: | ---------- | --------------------------- |
|      0 | `reserved` | `0`                          |
|      1 | `df`       | `1`                          |
|      2 | `mf`       | `0`                          |
|   3-15 | `offset`   | `0`                          |

Each consecutive bit run must end on a byte boundary. `signed=True` selects a
signed field, and `const=` declares a fixed bit value. Values that do not fit
their width raise a pack error.

Bit fields are flat fields on the containing object. This makes their names
available to later length and condition expressions:

<!-- name: test_model_enums_and_bits -->
```python
from rustruct import Struct, bits, slice


class IPv4Options(Struct):
    version: int = bits(4, default=4)
    ihl: int = bits(4, default=5)
    options: bytes = slice(len=lambda f: (f.ihl - 5) * 4, default=b"")
```

## Return a mapping of flags

The mapping-based schema API also has a `flags` kind. It decodes one integer
into named booleans or bit ranges and can keep, ignore, or reject unnamed
bits. See {doc}`/reference/low-level-schema` when a mapping is more useful than
flat typed fields.
