# How schemas work

This explanation connects Python declarations to the wire: what
annotations mean, when compilation happens, why some fields are derived, and
how bounded regions and expressions constrain decoding.

## Annotations describe the wire

A {py:class}`rustruct.Struct` subclass reads its runtime annotations in declaration order.
Scalar sentinel classes such as {py:class}`rustruct.U8` and {py:class}`rustruct.I32` select fixed-width wire types.
Descriptors such as `slice()`, `array()`, and {py:func}`rustruct.switch` describe fields that
need options or cross-field relationships.

<!-- name: test_concepts -->
```python
from rustruct import Struct, U8, U16, array


class Message(Struct):
    count: U8
    values: list = array(U16, count="count")
```

At runtime the values are normal Python objects: `count` is an `int` and
`values` is a `list[int]`.

## Lazy schema compilation

Class creation records the declarations. The first construction, pack, or
unpack resolves nested types, compiles a core {py:class}`rustruct.Codec`, and generates
field-specific Python conversion methods. Later operations reuse those
artifacts.

The low-level API skips the object frontend. A field is a plain 3-tuple, or
{py:class}`rustruct.Field`, a `NamedTuple` with the same three positions:

<!-- name: test_concepts -->
```python
from rustruct import Field, compile

codec = compile(
    (
        Field(name="kind", kind="u8", opts={}),
        Field(name="value", kind="u16", opts={}),
    )
)
assert codec.unpack(b"\x01\x00\x02") == {"kind": 1, "value": 2}
```

## Derived fields

Length and count fields referenced by a following `slice`, `string`, `array`,
or `sized` field are derived during pack. Callers normally omit them:

<!-- name: test_concepts -->
```python
from rustruct import slice


class Blob(Struct):
    length: U16
    data: bytes = slice(len="length")


assert Blob(data=b"abc").pack() == b"\x00\x03abc"
```

On unpack, derived fields remain visible and contain the wire value.
Switch discriminants are different: callers provide them explicitly because
the selected branch cannot always determine a unique tag -- several branches
can have the same shape, or the same length, so there's no way to work the
tag back out from the decoded data alone.

A field can be both a length/count/size source in some branches and a
switch discriminant in others: a bit-packed format like MessagePack reuses
the same bits as both a count and, in an adjacent range, a further type
selector (see {doc}`/how-to/model-tagged-unions`). Whether such a field
actually becomes derived is decided once its *whole* enclosing structure is
compiled, not eagerly at the first reference: if it's also used as a switch
discriminant anywhere in that structure, it stays an ordinary, explicitly
supplied field instead, and every length/count/size reference to it just
checks the given value against the actual data rather than computing it --
exactly like a `const=` field already does. This is why the two roles can
coexist on one schema: neither compiles away information the other one
needs.

## What a schema cannot express

Compilation lowers a schema to one fixed, fully unrolled program: every
nested structure, every array element, every switch branch is inlined at
compile time, with no call/return or loop-back of any kind. Two schema
shapes fall out of reach as a direct consequence:

- **A type that contains itself**, directly or through a cycle of other
  types -- the general shape needed for a full MessagePack/JSON/S-expression
  value tree, where an array or map can hold values of its own type to
  unbounded depth. A program for such a type would have to be infinitely
  large. A *bounded*-depth version (an explicit "a value of depth N
  contains values of depth N-1" chain, written out by hand for as many
  levels as actually needed) compiles fine -- it's just an ordinary tree of
  distinct types, no different in kind from any other nested structure. See
  {doc}`/how-to/use-msgpack` for how the bundled MessagePack support works
  around this by keeping recursion in Python and the schema strictly
  bounded.
- **Dispatch on a range of values.** `switch()` matches a discriminant by
  exact value, one case per tag; there's no "0x00-0x7F all mean X" case. A
  range has to be either enumerated case by case, or reformulated so an
  already-decoded sub-field (typically a `bits()` field) carries just the
  bits that actually discriminate.

For the exact instruction set compilation produces and how these limits
follow from it mechanically, see {doc}`/explanation/opcodes`.

## Exact regions

{py:meth}`rustruct.Struct.unpack` requires exact top-level consumption. `sized()` creates the same
rule for a nested region: too little data is truncated, and unused bytes
inside the region are trailing data. `slice(len="*")` and
`array(..., until_eof=True)` consume the remainder of their current region,
not necessarily the remainder of the outer buffer.

## Expressions and lexical scope

Descriptor arguments accept:

- an integer constant;
- `"*"` for the current region remainder;
- a sibling field name such as `"length"`;
- a lambda over a field namespace, such as
  `lambda f: (f.ihl - 5) * 4`.

References resolve in the current structure or an enclosing structure. They
never reach forward into a structure that has not been decoded.
