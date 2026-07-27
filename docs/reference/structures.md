# Structures

For an end-to-end example, see {doc}`/tutorials/install-rustruct`.

## Declarative structure

```{eval-rst}
.. autoclass:: rustruct.Struct
   :members: pack, pack_into, unpack, unpack_from, parse
   :undoc-members:
```

Byte order is declared as a class keyword:

<!-- name: test_reference_structures -->
```python
import rustruct


class Big(rustruct.Struct, byteorder="big"): ...


class Little(rustruct.Struct, byteorder="little"): ...
```

`"network"` aliases `"big"`. Byte order is inherited.

## Field inheritance

A subclass's wire fields are its base classes' fields, in their original
declared order, followed by any new fields the subclass declares:

<!-- name: test_reference_structures_inheritance -->
```python
import rustruct


class FrameHeader(rustruct.Struct):
    kind: rustruct.U8
    seq: rustruct.U16


class Ping(FrameHeader):
    nonce: rustruct.U32


assert [f.name for f in Ping.wire_fields] == ["kind", "seq", "nonce"]
assert Ping(kind=1, seq=2, nonce=3).pack() == b"\x01\x00\x02\x00\x00\x00\x03"
```

A subclass field can reference an inherited field by name, the same as any
other sibling reference. Redeclaring an inherited field's name keeps its
original wire position but replaces its annotation/descriptor/default --
useful for changing a default without moving the field:

<!-- name: test_reference_structures_inheritance -->
```python
class Versioned(rustruct.Struct):
    version: rustruct.U8 = rustruct.described(1)


class VersionTwo(Versioned):
    version: rustruct.U8 = rustruct.described(2)


assert Versioned().pack() == b"\x01"
assert VersionTwo().pack() == b"\x02"
```

This inherits *fields*, not dispatch. To pick one of several independent
shapes at runtime by a tag, use a [registry](#registry-classes) instead --
the two compose: an envelope's shared fields can live in one base class
while its variants are chosen through `switch()`. See
{doc}`/how-to/model-tagged-unions` for that combination worked out.

## Registry classes

`class Base(Struct, registry=True)` (where `Struct` is
{py:class}`rustruct.Struct`) creates `Base.dispatch_registry`. Concrete
subclasses register with exactly one class keyword, for example
`class TCP(Base, proto=6)`. The keyword value becomes `registry_key`.
