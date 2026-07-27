# How to model computed and conditional fields

## Include a field conditionally

{py:func}`rustruct.when`(pred, then=...) includes a field only when its predicate is true:

<!-- name: test_model_computed_fields -->
```python
from rustruct import Struct, U8, U16, described, when


class Message(Struct):
    has_extra: U8 = described(0)
    extra: object = when(pred="has_extra", then=U16, default=None)


assert Message(has_extra=0).pack() == b"\x00"
assert Message(has_extra=1, extra=0xABCD).pack() == b"\x01\xab\xcd"
```

The predicate can be a field name or an expression lambda:

<!-- name: test_model_computed_fields -->
```python
class ExecutableHeader(Struct):
    optional_size: U16
    optional: object = when(
        pred=lambda f: f.optional_size > 0,
        then=U16,
        default=None,
    )
```

Predicates can read fields already decoded in the same or an enclosing scope.
When false, the field consumes zero bytes.

## Derive lengths and counts

Lengths are relationships declared on the consuming field:

<!-- name: test_model_computed_fields -->
```python
from rustruct import U32, digest, slice


class Payload(Struct):
    size: U16
    data: bytes = slice(len="size")
```

Pack derives `size` from `data`; unpack reads `size` first and bounds `data`.
The same rule applies to {py:func}`rustruct.string`(len=...), {py:func}`rustruct.array`(count=...), and
{py:func}`rustruct.sized`(size=...).

Expressions support arithmetic, bitwise operations, comparisons, and boolean
logic:

<!-- name: test_model_computed_fields -->
```python
options: bytes = slice(len=lambda f: (f.ihl - 5) * 4)
```

The compiler only accepts backward-resolvable references and validates
expressions before runtime.
