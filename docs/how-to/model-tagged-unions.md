# How to model tagged unions

## When to use switch()

Use {py:func}`rustruct.switch` when a discriminant selects a different wire shape. Unlike
lengths, the discriminant remains explicit pack input. The sections below
cover selecting from a fixed set of cases, registering cases dynamically
through a registry, and bounding a variable-sized branch.

## Select from fixed cases

{py:func}`rustruct.switch`(on=..., cases=...) selects a scalar, descriptor, or structure:

<!-- name: test_model_tagged_unions -->
```python
from rustruct import Struct, U8, U16, slice, switch


class Frame(Struct):
    kind: U8
    body: object = switch(
        on="kind",
        cases={1: U16},
        default=slice(len="*"),
    )


assert Frame(kind=1, body=0x1234).pack() == bytes.fromhex("01 1234")
assert Frame(kind=9, body=b"??").pack() == bytes.fromhex("09 3f3f")
```

`kind=1` selects the declared `U16` case: one byte of tag, then two bytes of
body. `kind=9` has no declared case, so it falls through to the `slice(len="*")`
default and `body` is raw bytes instead of an integer. The `kind` field must
precede `body` on unpack and must be supplied on pack. The default branch is
optional. A raw byte default is useful for preserving unknown extensions.

## Add cases through a registry

Mark a structure base with `registry=True`, then register concrete subclasses
using one arbitrary class keyword:

<!-- name: test_model_tagged_unions -->
```python
from rustruct import U32


class Payload(Struct, registry=True):
    pass


class Ping(Payload, kind=1):
    nonce: U32


class Pong(Payload, kind=2):
    nonce: U32
```

The keyword name is descriptive; only its value is the registry key.
`Ping.registry_key` is `1`.

Connect the registry to a switch:

<!-- name: test_model_tagged_unions -->
```python
class Packet(Struct):
    kind: U8
    payload: object = switch(
        on="kind",
        cases=Payload.dispatch_registry,
        default=slice(len="*"),
    )
```

Decoded registered branches become the corresponding typed subclass.

## Share fields across registered cases

A registry base isn't limited to zero fields: fields it declares are
inherited by every concrete subclass, in addition to that subclass's own
fields (see {doc}`/reference/structures`'s "Field inheritance"). This is
how to give every variant of a tagged union a common prefix without
repeating it:

<!-- name: test_model_tagged_unions -->
```python
class PayloadBase(Struct, registry=True):
    nonce: U32


class Ping(PayloadBase, kind=1):
    pass


class Pong(PayloadBase, kind=2):
    extra: U8


assert [f.name for f in Pong.wire_fields] == ["nonce", "extra"]
```

`Ping`/`Pong` both get `nonce` for free; `Pong` adds its own `extra` after
it. Inheritance decides each variant's own field set; the registry and
`switch()` still decide which variant a given `kind` selects.

## Register cases before compilation

Registration is eager at subclass definition time. A registry freezes when a
schema first reads it during compilation. Define or import all participating
subclasses before constructing, packing, or unpacking a structure that uses
the registry. Late registration raises `TypeError` rather than silently
leaving an already-compiled switch stale.

## Bound a variable-sized branch

A greedy default or branch needs a known endpoint. Put the switch inside a
{py:func}`rustruct.sized` structure when the message carries an explicit payload length. The
IPv4 model uses the packet's total length as the outer window and lets the
selected TCP or UDP payload consume the remainder.
