# rustruct

```{only} html
[![Latest Version](https://img.shields.io/pypi/v/rustruct.svg)](https://pypi.python.org/pypi/rustruct/)
[![Python Versions](https://img.shields.io/pypi/pyversions/rustruct.svg)](https://pypi.python.org/pypi/rustruct/)
[![Tests](https://github.com/mosquito/rustruct/workflows/tests/badge.svg)](https://github.com/mosquito/rustruct/actions?query=workflow%3Atests)
[![License](https://img.shields.io/pypi/l/rustruct.svg)](https://pypi.python.org/pypi/rustruct/)
[![Stars](https://img.shields.io/github/stars/mosquito/rustruct.svg)](https://github.com/mosquito/rustruct)
```

`rustruct` is a binary format compiler with a Rust execution core and a
declarative Python frontend. Define a wire layout as a `Struct` subclass and
work with ordinary Python values:

<!-- name: test_documentation_index -->
```python
from rustruct import Struct, U8, U16


class Header(Struct, byteorder="network"):
    version: U8
    flags: U8
    payload_length: U16


header = Header(version=1, flags=0x80, payload_length=4)
wire = header.pack()

assert wire == b"\x01\x80\x00\x04"
assert Header.unpack(wire) == header
```

For applications that prefer mappings or generate schemas dynamically, the
same core is available through `compile()` and `Codec`.

Compared to the standard library's `struct`, the schema -- not your own
bookkeeping -- tracks lengths, nested regions, and fields derived from
sibling data, and it validates that schema before any bytes move. Compared
to a pure-Python interpreter like `construct`, or a generated-code system
like Protocol Buffers, a schema compiles once into a native program with no
interpretation loop and no build step.

## Performance

A schema compiles once; every pack or unpack after that is one call into
the Rust core, not a Python loop over field descriptors. Against
`construct` -- the closest feature-comparable pure-Python schema library,
per the {doc}`capability matrix <explanation/performance>` -- a mixed
telemetry frame (dynamic text, nested records, dynamic payloads) packed and
unpacked about 6.9x and 8.1x faster in one measured environment:

| Workload                    | `construct` | `rustruct` | Speedup |
| ---------------------------- | ----------: | ---------: | ------: |
| telemetry pack, ns/record    |     7,102.1 |    1,024.7 |    6.9x |
| telemetry unpack, ns/record  |     9,632.8 |    1,193.7 |    8.1x |

These are fitted incremental costs from one machine and one Python build,
not a universal guarantee. See {doc}`explanation/performance` for the full
capability matrix, the complete snapshot across workloads, and how to
reproduce or extend it against your own schema.

## Learn

Start with the {doc}`tutorials/index`. The first lesson takes one message from
declaration through packing, unpacking, incremental input, and error handling.
For every field type and descriptor with a minimal runnable example on one
page, see {doc}`reference/schema-language`.

## Solve a problem

Use the {doc}`how-to/index` when you already know the result you need. Guides
cover field selection, collections, computed fields, dispatch, generated
schemas, extension patterns, bundled protocol models, and benchmarking.

## Understand

Read the {doc}`explanation/index` for the schema model, Python-to-Rust
execution path, extension boundary, and interpretation of performance
measurements.

## Look up the API

The {doc}`reference/index` is organised by the public modules, classes,
functions, descriptors, results, and exceptions. It is the concise source of
truth once you know which API element you need.

## Design goals

- **One native execution per structure.** A schema compiles once and the Rust
  core interprets the resulting program for each pack or unpack, so
  per-message Python overhead does not grow with how many fields, nested
  structures, or arrays a schema declares.
- **Normal Python values.** The frontend returns `Struct` instances containing
  `int`, `float`, `bool`, `bytes`, `str`, lists, and converted values, so
  application code never touches an intermediate parser-specific type.
- **Bounded decoding.** Nested windows cannot consume bytes from following
  fields, so a malformed length inside one part of a message cannot corrupt
  how the rest of it is parsed.
- **Streaming-aware errors.** `parse()` distinguishes incomplete input from
  malformed complete input, so a socket read that stopped mid-message is not
  indistinguishable from an attack or a bug.
- **Round-trippable unknown data.** Switch defaults can preserve unrecognized
  payloads as bytes, so decoding a message from a newer version of your own
  protocol does not mean discarding fields this build does not know about
  yet.

```{toctree}
:hidden:
:maxdepth: 2

tutorials/index
how-to/index
explanation/index
reference/index
```
