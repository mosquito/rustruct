# How to model collections and nesting

Choose counted arrays when the wire carries an element count, greedy arrays
when a surrounding region supplies the endpoint, and {py:func}`rustruct.sized` when a nested
message has its own byte-length field.

## Add a counted array

{py:func}`rustruct.array`(elem, count=...) repeats a scalar, descriptor, or nested structure:

<!-- name: test_model_collections -->
```python
from rustruct import Struct, U8, U16, array


class Numbers(Struct):
    count: U8
    values: list = array(U16, count="count")


numbers = Numbers(values=[10, 20, 30])
assert numbers.pack() == bytes.fromhex("03 000a 0014 001e")
assert Numbers.unpack(numbers.pack()).values == [10, 20, 30]
```

| Offset | Bytes | Field       | Value    |
| -----: | ----: | ----------- | -------- |
|      0 |     1 | `count`     | `0x03`   |
|      1 |     2 | `values[0]` | `0x000a` |
|      3 |     2 | `values[1]` | `0x0014` |
|      5 |     2 | `values[2]` | `0x001e` |

When `count` references a sibling, pack derives that sibling from
`len(values)`. The count must appear before the array for unpack.

## Decode arrays as typed structures

<!-- name: test_model_collections -->
```python
class Point(Struct):
    x: U16
    y: U16


class Polygon(Struct):
    count: U8
    points: list = array(Point, count="count")
```

The frontend returns real `Point` instances for decoded elements. The
low-level {py:class}`rustruct.Codec` API returns dictionaries instead.

## Consume items to the end of a region

{py:func}`rustruct.array`(elem, until_eof=True) repeats until the current region ends:

<!-- name: test_model_collections -->
```python
class Words(Struct):
    values: list = array(U16, until_eof=True)
```

Use it as the last field of a top-level structure or inside {py:func}`rustruct.sized`. A
trailing partial item is invalid rather than silently ignored.

## Nest a fixed structure

A {py:class}`rustruct.Struct` subclass can be used directly as an annotation:

<!-- name: test_model_collections -->
```python
class Segment(Struct):
    start: Point
    end: Point
```

Nested classes retain their own byte order rather than inheriting the
container's: with no `byteorder=` of its own, a nested structure always
defaults to `"big"`, even inside a `"little"`-ordered container. Give it
an explicit `byteorder=` when it should match its container.

Nested classes retain their own byte order.

## Bound a nested structure by size

{py:func}`rustruct.sized`(StructType, size=...) gives the nested structure an exact byte window:

<!-- name: test_model_collections -->
```python
from rustruct import sized, slice


class Body(Struct):
    payload: bytes = slice(len="*")


class Envelope(Struct):
    size: U16
    body: object = sized(Body, size="size")
```

On pack, `size` is derived from the encoded body. On unpack, `Body` cannot
read beyond that window and must consume it exactly. This is the preferred
model for TLV formats.

## Limit untrusted sizes

{py:func}`rustruct.compile` accepts `max_default` and `max_count`. The former bounds dynamic
byte allocations without a smaller field-level `max`; the latter bounds
arrays. Applications handling untrusted formats should choose limits
appropriate for their protocol.
