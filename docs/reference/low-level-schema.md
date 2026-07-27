# Low-level schema language

{py:func}`rustruct.compile` accepts field tuples and returns a
{py:class}`rustruct.Codec` that packs mappings and unpacks dictionaries.

## Field tuples

A field is `(name, kind, options)` -- any 3-element tuple works, including
{py:class}`rustruct.Field`, a `NamedTuple` provided for exactly this: it reads
better than three unlabeled positions, and it's still a plain `tuple`
underneath (the Rust core downcasts it structurally), so nothing else has to
change:

<!-- name: test_low_level_api -->
```python
from rustruct import Field, compile

codec = compile(
    (
        Field(name="tag", kind="u8", opts={}),
        Field(name="length", kind="u16", opts={}),
        Field(name="data", kind="bytes", opts={"len": ("ref", "length")}),
    ),
    byteorder="big",
)

wire = codec.pack({"tag": 7, "data": b"abc"})
assert wire == b"\x07\x00\x03abc"
assert codec.unpack(wire) == {
    "tag": 7,
    "length": 3,
    "data": b"abc",
}
```

A bare tuple like `("tag", "u8", {})` works exactly the same way.

Common kinds are:

- fixed scalars: `u8/i8` through `u64/i64`, `f32`, `f64`, `bool`;
- `raw`, `bytes`, `str`, and `cstr`;
- `bits` and `flags`;
- `struct`, `array`, `cond`, and `switch`;
- `digest`.

The declarative frontend lowers to this same vocabulary.

## Expressions

Options use explicit expression tuples:

<!-- name: test_low_level_api -->
```python
("ref", "length")
("sub", ("ref", "total_length"), 20)
("mul", ("sub", ("ref", "ihl"), 5), 4)
```

The frontend's strings and lambdas compile to these expressions.

## Codec operations

- {py:meth}`pack <rustruct.Codec.pack>`(mapping) -> bytes
- {py:meth}`pack_into <rustruct.Codec.pack_into>`(buffer, offset, mapping) -> new_offset
- {py:meth}`unpack <rustruct.Codec.unpack>`(buffer) -> dict
- {py:meth}`unpack_from <rustruct.Codec.unpack_from>`(buffer, offset) -> (dict, new_offset)
- {py:meth}`parse <rustruct.Codec.parse>`(buffer, offset) -> (dict, new_offset) | {py:class}`rustruct.Incomplete`

{py:attr}`min_size <rustruct.Codec.min_size>` is a lower bound.
{py:attr}`static_size <rustruct.Codec.static_size>` is an exact size for
fully static schemas and `None` otherwise.

## Streaming

{py:meth}`parse() <rustruct.Codec.parse>` never treats a mere data shortage
as malformed:

<!-- name: test_low_level_api -->
```python
from rustruct import Incomplete

result = codec.parse(b"\x07")
assert isinstance(result, Incomplete)
```

{py:attr}`Incomplete.needed <rustruct.Incomplete.needed>` is the minimum
additional byte count known at that point.

## Limits

`max_default` limits dynamic fields without a smaller explicit maximum.
`max_count` limits array elements. Both defaults are intentionally finite.

## Extension boundary

The set of core field kinds is closed in this release. There is no public
Python custom-codec base that runs inside the native program.
{py:func}`rustruct.convert` provides value-level transformation. Algorithms
that require absolute buffer offsets or cross-message state require
composition outside the native program. See
{doc}`/explanation/extension-model`.
