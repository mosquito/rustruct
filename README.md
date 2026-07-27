# rustruct

[![Tests](https://github.com/mosquito/rustruct/workflows/tests/badge.svg)](https://github.com/mosquito/rustruct/actions?query=workflow%3Atests)
[![Latest Version](https://img.shields.io/pypi/v/rustruct.svg)](https://pypi.python.org/pypi/rustruct/)
[![Python Versions](https://img.shields.io/pypi/pyversions/rustruct.svg)](https://pypi.python.org/pypi/rustruct/)
[![License](https://img.shields.io/pypi/l/rustruct.svg)](https://pypi.python.org/pypi/rustruct/)

A Rust core for parsing and building binary wire formats from Python.

A declarative schema built from Python primitives is compiled into a
program executed with a single Python -> Rust call for the whole
structure. The parse result is a plain `dict`.

## Building and testing

```bash
# Rust core (no Python)
cargo test -p rustruct-core

# Python package (maturin backend) + pytest
uv sync
uv run pytest

# Sphinx documentation (warnings are errors)
make test-docs
```

## Example

<!-- name: test_readme_core -->
```python
from rustruct import Field, Incomplete, compile

codec = compile(
    (
        Field(name="tag", kind="u8", opts={}),
        Field(name="size", kind="u8", opts={}),
        Field(
            name="value",
            kind="struct",
            opts={
                "fields": (Field(name="payload", kind="bytes", opts={"len": "*"}),),
                "size": ("ref", "size"),
            },
        ),
        Field(name="crc", kind="digest", opts={"algo": "crc32", "over": "*"}),
    )
)

data = codec.pack({"tag": 7, "value": {"payload": b"abc"}})
# size and crc are derived: computed and patched in automatically

values = codec.unpack(data)  # full buffer, a tail is an error
values, pos = codec.unpack_from(data, 0)  # a tail is allowed

r = codec.parse(data[:3])  # streaming parse
if not r:  # Incomplete: not enough data yet
    print("need at least", r.needed, "more bytes")
```

## Documentation

The full documentation is at
[mosquito.github.io/rustruct](https://mosquito.github.io/rustruct/) and is
organised by user need: a guided tutorial, task-oriented how-to guides,
explanations of the schema and execution model, and concise API reference.

## Declarative frontend

A `Struct` metaclass on top of `compile()`/`Codec`: class-body annotations
and field descriptors (`described`, `slice`, `array`,
`switch`, `bits`, `convert`, `sized`, an open
`registry()`) compile lazily, per class, into a Codec, and convert to/from
typed instances rather than plain dicts.

<!-- name: test_readme_frontend -->
```python
from rustruct import Struct, U8, U16, switch, slice, registry

Payload = Struct  # a registry base: class Foo(Payload, registry=True): ...
```

See `src/rustruct/protocols/{inet,tcp,udp,dns}.py` for a full worked example
(IPv4/TCP/UDP header dispatch via an open registry, DNS with hand-written
name-compression as the one piece that doesn't fit the declarative model)
and `tests/test_frontend_*.py`, `tests/test_protocols_*.py` for the tests.

## Repository layout

```
crates/rustruct/       core (rustruct-core): schema compiler, unpack/pack,
                       expressions, bits, flags, windows, digests; zero pyo3
crates/rustruct-py/    pyo3 cdylib -> the rustruct.core module
src/rustruct/          Python wrapper: low-level re-export, __abi__ check,
                       the Struct frontend (struct.py/fields.py/scalars.py/
                       expr.py), and protocols/ (IPv4/TCP/UDP/DNS example)
tests/                 pytest tests: public API, frontend, protocols
docs/                  tutorials, how-to guides, explanation, API reference
crates/rustruct/tests/ Rust integration tests, one file per feature, plus
                       tests/common/mod.rs for shared helpers
benchmark/             a separate uv project comparing rustruct against
                       struct/ctypes/dataclasses-struct/construct
```

## Implementation status

Covered:

- all fixed-width types, `raw`, `bytes`/`str`/`cstr`, `bits` (MSB-first),
  `flags` (keep/strict/ignore), `struct` (including `size` windows), `array`
  (`count`/`until_eof`), `switch`, `digest` (CRC presets with Rocksoft
  overrides, md5/sha1/sha256, `over` as a name tuple or `"*"` with
  self-zeroing);
- expressions (the full operator set), lexical backward-only ref resolution,
  registers and span registers, limits (`max`, `max_count`, depth 64, an
  8-deep Expr stack);
- pack with backpatching: derived lengths (linear inversion `a*x + b`,
  including derived bits fields), digests computed innermost-first,
  consistency checks across multiple consumers of one register;
- the streaming contract: `parse` -> `Incomplete.needed` with monotonic
  progress, distinguishing "hit a window boundary" (Invalid) from "ran out
  of buffer" (Incomplete);
- errors carry `kind`/`path`/`offset`; the path is assembled only while
  unwinding;
- coalescing of fixed fields (a static schema lowers to exactly one
  `Fixed` op, enforced by a test), `min_size`/`static_size`.

Known gaps and limitations:

- `to_bytes`/`from_bytes` (Program serialization) raise
  `NotImplementedError`; the cache format hasn't been designed.
- A switch discriminant is never derived from its own `on=` ref at pack
  time: the caller always supplies it explicitly, and a field cannot be
  both derived and a switch discriminant (SchemaError).
- byteorder accepts `"network"` as a struct-module-style alias for
  `"big"`. `"native"` is forbidden, since it would make the wire format
  depend on the machine running the code.
- String encodings in the core: utf-8 / ascii / latin-1, `errors="strict"`
  only. Other CPython codecs are a frontend concern; the core currently
  raises SchemaError for anything else.
- An extra `digest.algo="ip"` preset (RFC 1071, the IPv4 header checksum)
  is needed for the bundled IPv4 reference format.
- No cargo-fuzz targets and no refcount tests yet; deterministic and
  randomized round-trip tests stand in for a proper proptest-based fuzz
  suite.
- The hot path still builds an intermediate `Value` tree (the core stays
  Python-free); raw FFI and METH_FASTCALL benchmarking haven't been done.
