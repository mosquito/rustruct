# How to extend the schema

## Convert a field to a domain value

Choose the narrowest extension mechanism that can express the format:

| Goal | Mechanism |
| --- | --- |
| Convert one decoded value | {py:func}`convert() <rustruct.convert>` |
| Select one of several layouts | `switch()` |
| Add cases without editing the owner | {py:class}`Struct <rustruct.Struct>` registry |
| Generate fields at runtime | {py:func}`compile() <rustruct.compile>` |
| Use absolute offsets or message-wide state | hand-written outer codec |

Keep the base wire operation declarative and transform only its completed
value:

<!-- name: test_extend_schema -->
```python
import uuid

from rustruct import Struct, convert, raw


class Record(Struct):
    identifier: object = convert(
        raw(16),
        decode=lambda value: uuid.UUID(bytes=value),
        encode=lambda value: value.bytes,
    )


identifier = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
assert Record(identifier=identifier).pack() == identifier.bytes
assert Record.unpack(identifier.bytes).identifier == identifier
```

The callbacks receive a completed field value. They cannot move the cursor,
inspect arbitrary sibling state, or change the base field's encoded width.

## Compose an algorithmic outer codec

Keep layout-shaped components as {py:class}`rustruct.Struct` classes, then write only the
algorithmic layer by hand. This is appropriate when decoding depends on
absolute offsets, backward pointers, or state shared across distant fields.

The bundled DNS model follows this pattern: its name compression parser owns
the message-wide cursor and suffix table, while flags and fixed RDATA types
remain declarative.

Read {doc}`/explanation/extension-model` for the design rationale and
{doc}`/reference/extensions` for the supported public extension points.
