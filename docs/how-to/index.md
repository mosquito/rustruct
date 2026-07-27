# How-to guides

These guides solve specific tasks. Each page is self-contained and runnable
on its own; they assume you can already define, pack, and unpack a basic
{py:class}`rustruct.Struct` -- complete {doc}`/tutorials/install-rustruct` first if those
operations are unfamiliar.

## Choose a field type

Pick a field by the value your application should receive and how its wire
width is determined: fixed-width scalars, exact or runtime-sized bytes,
encoded text, a converted Python value, or a computed checksum.

- {doc}`choose-field-types`
- {doc}`compute-and-verify-a-digest`

## Model collections and nesting

Choose counted arrays when the wire carries an element count, greedy arrays
when a surrounding region supplies the endpoint, and {py:func}`rustruct.sized` when a nested
message has its own byte-length field.

- {doc}`model-collections`

## Model enums and bit fields

- {doc}`model-enums-and-bits`

## Model computed and conditional fields

- {doc}`model-computed-fields`
- {doc}`compute-and-verify-a-digest`

## Model tagged unions

- {doc}`model-tagged-unions`

## Choose or extend an API layer

- {doc}`compile-mapping-schemas`
- {doc}`extend-schema`

## Use bundled formats

- {doc}`use-ipv4`
- {doc}`use-dns`
- {doc}`use-png`
- {doc}`use-msgpack`
- {doc}`decode-untrusted-msgpack-safely`

## Measure performance

- {doc}`benchmark`

```{toctree}
:hidden:
:maxdepth: 1

choose-field-types
compute-and-verify-a-digest
model-collections
model-enums-and-bits
model-computed-fields
model-tagged-unions
compile-mapping-schemas
extend-schema
use-ipv4
use-dns
use-png
use-msgpack
decode-untrusted-msgpack-safely
benchmark
```
