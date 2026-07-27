# How to compile mapping-based schemas

## Build the field sequence

Use the low-level API when field declarations are generated at runtime or when
the application wants dictionaries instead of typed {py:class}`rustruct.Struct`
instances.

Create one {py:class}`Field(name, kind, opts) <rustruct.Field>` tuple per wire
field:

<!-- name: test_compile_mapping_schemas -->
```python
from rustruct import Field, compile

fields = (
    Field(name="kind", kind="u8", opts={}),
    Field(name="length", kind="u16", opts={}),
    Field(name="payload", kind="bytes", opts={"len": ("ref", "length")}),
)
codec = compile(fields, byteorder="big")
```

The reference from `payload` makes `length` a derived field during pack.

## Pack and unpack mappings

<!-- name: test_compile_mapping_schemas -->
```python
wire = codec.pack({"kind": 3, "payload": b"hello"})
assert wire == b"\x03\x00\x05hello"

decoded = codec.unpack(wire)
assert decoded == {
    "kind": 3,
    "length": 5,
    "payload": b"hello",
}
```

Use {py:meth}`pack_into() <rustruct.Codec.pack_into>` or
{py:meth}`unpack_from() <rustruct.Codec.unpack_from>` when the value occupies
part of an existing buffer. Use {py:meth}`parse() <rustruct.Codec.parse>` when
a short buffer should return {py:class}`rustruct.Incomplete` instead of
raising an invalid-data error.

## Apply safety limits

Pass `max_default` to cap dynamic byte fields without a smaller field-specific
maximum, and `max_count` to cap arrays:

<!-- name: test_compile_mapping_schemas -->
```python
limited_codec = compile(
    fields,
    byteorder="big",
    max_default=1024 * 1024,
    max_count=4096,
)
assert limited_codec.min_size == 3
```

See {doc}`/reference/low-level-schema` for every field kind, expression form,
operation, and size attribute.
