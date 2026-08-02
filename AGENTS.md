# rustruct repository guide

This file is the working context for agents and contributors. Read it before
changing the repository. It summarizes the architecture, schema semantics,
development workflow, testing expectations, and the deliberate boundary
between declarative layouts and algorithmic codecs.

## Project purpose

`rustruct` is a Python library for declaring, packing, and parsing binary wire
formats. It aims for `construct`-style declarative schemas with performance
close to hand-written `struct` code:

- users describe a layout once as a typed `Struct` subclass or a low-level
  mapping schema;
- the schema is validated and compiled once;
- a pure Rust core executes the compiled program;
- the Python frontend returns typed objects without interpreting descriptors
  field by field on every call.

The package targets Python 3.11 and newer. It is built with maturin and PyO3
as an abi3 extension.

## Start here

Set up the development environment:

```console
uv sync
```

Run the normal complete test suite:

```console
make test
```

`make test` runs the Rust workspace tests, rebuilds the Python extension when
Rust sources changed, and runs the Python/Markdown pytest suite.

Before handing off a substantive change, run the checks relevant to it:

```console
uv run ruff check .
uv run ty check src
cargo fmt --all --check
make lint
make test-docs
```

Important details:

- Use `make pytest`, not a bare `uv run pytest`, after changing Rust code.
  `make pytest` first ensures that `src/rustruct/core.abi3.so` reflects the
  current Rust sources. A bare pytest run can otherwise exercise a stale
  extension.
- `uv run ty check src` is the supported type-checking scope. Checking the
  whole repository also visits benchmark implementations with their own
  environment, intentionally invalid test inputs, and fixture generators with
  optional dependencies.
- `make test-docs` builds Sphinx with warnings treated as errors and then runs
  executable examples in `README.md` and `docs/`.
- `make clean` removes build products and caches. It is not a routine
  prerequisite and should not be run when a narrower rebuild is sufficient.

Useful targeted commands:

```console
make rust-tests
make pytest
uv run pytest -q tests/test_arrays.py
uv run pytest -q tests/protocols/test_dns.py
cargo test -p rustruct-core
cargo test -p rustruct-core arrays
make build-python
make docs
```

## Repository map

### Python package

- `src/rustruct/__init__.py` is the public top-level API.
- `src/rustruct/struct.py` implements `StructMeta`, lazy schema resolution,
  field inheritance, typed object conversion, and generated frontend methods.
- `src/rustruct/fields.py` contains field descriptors such as `slice`,
  `array`, `switch`, `sized`, `when`, `digest`, and `convert`.
- `src/rustruct/expr.py` turns frontend expression lambdas and field
  references into the low-level expression form.
- `src/rustruct/scalars.py` defines scalar annotation sentinels.
- `src/rustruct/vocab.py` mirrors closed vocabularies owned by the Rust core.
- `src/rustruct/errors.py` defines the public exception hierarchy and typed
  error attributes.
- `src/rustruct/core.pyi` is generated from PyO3 introspection. Do not edit it
  by hand; update the Rust declarations/docstrings and rebuild the extension.
- `src/rustruct/protocols/` contains IPv4, TCP, UDP, and DNS worked models.
- `src/rustruct/formats/` contains PNG and MessagePack worked models.

### Rust crates

- `crates/rustruct` is the pure Rust core. It has no Python dependency.
  - `schema.rs` defines compiler input types.
  - `compile.rs` validates schemas and lowers them to a `Program`.
  - `program.rs` defines the native instruction set.
  - `pack.rs` and `unpack.rs` execute programs.
  - `model.rs` defines the generic `Source` and `Builder` boundaries.
  - `expr.rs` evaluates compiled expressions.
  - `digest.rs` implements checksums and hashes.
  - `error.rs` defines machine-readable failure kinds.
  - `value.rs` is the reference Rust value model used by core tests and
    standalone Rust callers.
- `crates/rustruct-py` is the PyO3 binding.
  - `parse.rs` parses Python field tuples into core schema types.
  - `lib.rs` exposes `compile`, `Codec`, and `Incomplete`, reads directly from
    Python objects through `PySource`, and builds Python objects directly
    through `PyBuilder`.

### Tests, documentation, and benchmarks

- `crates/rustruct/tests/` tests compilation and execution without Python.
- `tests/` tests the low-level Python API, typed frontend, schema boundary,
  errors, streaming, formats, and protocols.
- `README.md` and Markdown files under `docs/` contain executable examples
  collected by pytest.
- `docs/explanation/` is the source of truth for architecture and design
  rationale.
- `docs/reference/` documents public API behavior.
- `docs/how-to/` and `docs/tutorials/` contain task-oriented examples.
- `benchmark/` is a separate uv project so competitor dependencies do not
  enter the library environment.

Ignore generated artifacts such as `target/`, `docs/_build/`, caches,
`__pycache__/`, and the compiled extension unless the task is explicitly
about build output.

## Architecture and data flow

There are four distinct layers:

1. A `Struct` declaration or low-level tuple schema describes the wire
   layout.
2. The frontend resolves annotations/descriptors and the binding parses the
   low-level schema.
3. The Rust compiler validates it and produces a flat reusable `Program`.
4. Pack/unpack executes that program without re-walking the Python schema.

The typed frontend is deliberately thin:

- `StructMeta` records fields and class options.
- Resolution happens lazily on first construction, pack, unpack, parse, or
  explicit codec access.
- Each class receives specialized `__init__`, `to_mapping`, and
  `from_mapping` functions generated with Python AST nodes.
- One compiled `Codec` is cached per concrete `Struct` class.

The native boundary avoids generic intermediate trees on Python calls:

- pack reads mappings and values through `PySource` as execution reaches
  them;
- unpack builds `dict`, `list`, `bytes`, and scalar Python objects through
  `PyBuilder` during the native walk;
- the pure Rust API still uses `ValueSource`/`ValueBuilder`, keeping the core
  independently testable and usable without PyO3.

Preserve this separation. Protocol conveniences belong in Python. Generic
wire execution and validation belong in the core. Python-specific conversion
must remain in the binding/frontend rather than leaking into
`rustruct-core`.

## Public schema model

### Two frontends, one instruction set

The typed frontend:

```python
from rustruct import Struct, U8, U16, slice


class Blob(Struct):
    kind: U8
    length: U16
    data: bytes = slice(len="length")
```

The low-level frontend:

```python
from rustruct import Field, compile

codec = compile(
    (
        Field("kind", "u8", {}),
        Field("length", "u16", {}),
        Field("data", "bytes", {"len": ("ref", "length")}),
    )
)
```

Both lower to the same closed native vocabulary. `compile()` is not an
escape hatch from native schema limitations.

### Field kinds and descriptors

The main wire shapes are:

- fixed scalars: signed/unsigned integers, floats, and bool;
- `raw` for fixed-size bytes and constants;
- `slice`, `string`, and `cstring` for dynamic bytes/text;
- consecutive `bits` fields and low-level `flags`;
- nested `Struct` values and exact `sized` windows;
- counted or region-greedy `array` values;
- exact-tag `switch` unions;
- conditional `when` fields;
- derived and verified `digest` fields;
- value-level `convert` wrappers.

Use the narrowest descriptor that expresses the wire layout. Prefer bounded
fields (`max=`, exact windows, finite counts) for untrusted input.

### Derived fields and backpatching

When a preceding integer is referenced by a later `len=`, `count=`, or
`size=`, pack normally derives and backpatches that integer from the actual
encoded value. Callers omit derived fields.

The compiler can invert a relationship only when it is linear in one
reference (`a * ref + b`). A product or sum of multiple independent fields
cannot be derived. Reshape the schema with a nested size window or use a
small Python convenience constructor, as the IPv4 model does.

A field used as a `switch` discriminant remains explicit even if a branch
also refers to it as a length. Do not assume the runtime can infer a unique
tag from the selected Python value.

### Scope and windows

Expressions can refer to already decoded fields in the current or an
enclosing scope. They cannot:

- read a field that appears later on the wire;
- reach into a nested scope that has already closed;
- inspect the current absolute cursor;
- inspect the root input buffer;
- maintain arbitrary mutable message-wide state.

`Struct.unpack` and `Codec.unpack` require exact top-level consumption.
`unpack_from` allows a tail and returns the new offset. `sized()` creates an
exact nested region; its child must consume exactly that region. Greedy
bytes and `array(..., until_eof=True)` consume the remainder of their current
region, not necessarily the entire outer buffer.

### Inheritance and registries

`Struct` field inheritance is layout inheritance:

- inherited fields retain their original order;
- subclass fields are appended;
- redeclaring a field replaces it in place;
- subclass fields may reference inherited fields.

Use inheritance when concrete types genuinely share a wire prefix or complete
wire algorithm. Do not use it merely because two methods contain one similar
helper call.

Registries provide runtime dispatch:

```python
from rustruct import Struct, U32


class Payload(Struct, registry=True):
    pass


class Ping(Payload, kind=1):
    nonce: U32
```

Connect `Payload.dispatch_registry` to `switch()`. A registry freezes the
first time a consuming schema compiles, so import/register every variant
before first use. Late registration must fail loudly rather than creating a
stale compiled switch.

### Deliberate schema limits

The compiled program is a finite, fully validated instruction tree. It does
not support:

- self-referential or mutually recursive schema types;
- arbitrary Python callbacks that own or move the native cursor;
- general seek/dereference operations;
- dispatch over numeric ranges (only exact switch tags);
- sentinel-controlled arrays other than consuming a known region;
- arbitrary message-wide mutable state.

Keep algorithms outside the schema when they require those facilities.
MessagePack keeps recursive container traversal in an iterative Python loop
while using `Struct` for one tag. DNS keeps compression traversal in Python
while using `Struct` for fixed layouts.

## Runtime behavior

`Codec` works with mappings; `Struct` wraps the same operations with typed
instances.

- `pack(value)` returns a new `bytes`.
- `pack_into(buffer, offset, value)` writes to a writable contiguous buffer
  and returns the new offset.
- `unpack(buffer)` requires exact consumption.
- `unpack_from(buffer, offset)` returns `(value, new_offset)` and permits a
  trailing outer buffer.
- `parse(buffer, offset)` returns `(value, new_offset)` when complete or a
  falsy `Incomplete` with a lower-bound `needed` count when more input may
  complete the value.
- `min_size` is a lower bound; `static_size` is an exact size only for fully
  static schemas.

Error categories are part of the API:

- `SchemaError` means the declaration cannot compile.
- `PackError` has `kind` and `path`.
- `InvalidDataError` has `kind`, `path`, and byte `offset`.

Keep field paths and machine-readable error kinds stable when changing
validation. A shortage in `parse()` is `Incomplete`; malformed input is an
error. `unpack()` additionally treats a tail as invalid data.

Native byte order is intentionally unsupported because it would make a wire
format machine-dependent. Use `"big"`, `"network"`, or `"little"`.

## How to make changes

### Python frontend changes

When changing descriptors, expressions, inheritance, registries, or typed
conversion:

1. Update the implementation under `src/rustruct/`.
2. Add frontend tests and, where relevant, equivalent low-level schema tests.
3. Preserve lazy compilation and per-class caches.
4. Check nested arrays, switches, conditional absence, defaults, and
   cross-scope references where applicable.
5. Update reference docs and at least one worked example for public behavior.
6. Run Ruff, `ty check src`, pytest, and docs tests.

`convert()` is a value boundary, not a custom codec. Its callback transforms a
completed base value and cannot change how many bytes the base field consumes.

### Rust core or schema vocabulary changes

A new field kind or option is cross-layer work. Review all of:

- `crates/rustruct/src/schema.rs`;
- `crates/rustruct/src/program.rs`;
- `crates/rustruct/src/compile.rs`;
- `crates/rustruct/src/pack.rs`;
- `crates/rustruct/src/unpack.rs`;
- `crates/rustruct-py/src/parse.rs`;
- Python descriptors and `vocab.py`;
- generated typing information;
- low-level and frontend documentation;
- Rust and Python tests.

`parse.rs` uses one macro declaration to generate each kind's accepted option
list and extraction logic. Keep it single-source; do not reintroduce a second
hand-maintained allowlist.

The Rust/Python vocabulary drift tests are intentionally bidirectional:
Python names must compile in Rust, and names published by Rust must exist in
Python. Extend those tests whenever a closed set changes.

Maintain these safety properties:

- schema nesting and expression depth remain bounded;
- dynamic byte allocations and array element counts remain bounded;
- arithmetic uses checked operations;
- windows cannot be over-consumed or under-consumed;
- pack-time derived values are checked/backpatched consistently;
- parsing never panics on caller-controlled schema or input.

Run Rust formatting, clippy, core tests, rebuilt Python tests, and docs after
cross-layer changes.

### PyO3 binding changes

The binding should translate between Python buffers/mappings and the generic
core traits without duplicating wire semantics. Preserve structural coercion
rules between `ValueSource` and `PySource`, and preserve equivalent output
between `ValueBuilder` and `PyBuilder`.

The extension is abi3. Avoid APIs that require a CPython-version-specific
wheel unless the packaging contract is intentionally being changed.

`src/rustruct/core.pyi` is generated. Rebuild with `make build-python` after
changing exported PyO3 items or docstrings, then review the generated diff.

### Documentation changes

Documentation is executable and is part of the test suite. Keep the four
documentation layers distinct:

- tutorials teach a complete first workflow;
- how-to guides solve a task;
- explanations describe design and tradeoffs;
- reference pages specify public behavior.

Use MyST fenced examples with the existing `<!-- name: ... -->` markers when
the example should be collected by `markdown-pytest`. Run `make test-docs`;
Sphinx warnings are failures.

### Performance changes

Do not optimize based only on intuition or a tiny scalar microbenchmark.
The benchmark suite measures:

- schema construction;
- scalar field-count scaling;
- counted nested vectors;
- mixed telemetry messages;
- mapping/typed-object materialization;
- buffer I/O overhead;
- shared-codec thread-pool scaling.

Run it from its isolated project:

```console
cd benchmark
uv sync
uv run python run.py
```

For a quick smoke run:

```console
uv run python run.py --budget 0.001 --rounds 1 --min-iterations 1
```

Compare `rustruct-core` with typed `rustruct` to distinguish native execution
cost from object-conversion cost. Preserve benchmark input/output equivalence
across implementations.

## Test strategy

Choose tests according to the layer changed.

### Rust core tests

Use `crates/rustruct/tests/` for compiler and runtime invariants independent
of Python:

- static and dynamic fields;
- arrays and bits;
- windows and conditions;
- switches;
- digests;
- randomized round trips;
- streaming outcomes;
- schema errors and limits.

Prefer asserting both directions: expected wire bytes and
`pack(unpack(wire)) == wire`. For errors, assert machine-readable kind and
path, not only message prose.

### Python low-level tests

The root `tests/test_*.py` files cover:

- tuple-schema parsing and vocabulary drift;
- scalar and dynamic field behavior;
- byte order, strings, bits, flags, arrays, switches, and windows;
- pack/unpack buffer APIs;
- `Incomplete` streaming behavior;
- schema limits and malformed declarations;
- typed frontend construction, nesting, inheritance, dispatch, and
  conditionals.

When a frontend helper emits a low-level option, ensure the schema-boundary
tests prove that Rust accepts that option.

### Format and protocol tests

- PNG tests validate declarative chunks, sizes, and CRC behavior.
- MessagePack tests compare supported values with the reference `msgpack`
  package and cover deeply nested iterative traversal.
- IP tests cover nested IPv4/TCP/UDP registry dispatch and framing.
- DNS tests cover normal behavior, malformed compression, extra RDATA types,
  and independent fixture compatibility.

Protocol tests should include known wire vectors from an independent
implementation where practical. Round-tripping only against the same codec can
hide symmetric bugs.

### Documentation tests

Pytest collects `README.md` and `docs/` in addition to `tests/`. If a public
example changes, run both its focused pytest case and `make test-docs`.

## Bundled format models

These modules are examples of where the declarative boundary should sit.

### IPv4, TCP, and UDP

`IPv4` uses bit fields, an exact total-length window, cross-scope references,
and registry dispatch to TCP/UDP. `IPv4.build()` computes relationships that
cannot be inverted from one field, such as a total over options plus payload.

TCP and UDP inherit from the `IPPayload` registry base. Their protocol numbers
remain explicit switch discriminants at the IPv4 layer.

### PNG

PNG is almost entirely declarative: signature constants, until-EOF chunk
arrays, a type switch, exact chunk data lengths, and CRC derivation. Unknown
chunk types round-trip as raw bytes.

### MessagePack

One MessagePack tag is declarative. Recursive arrays/maps are not: the module
uses explicit iterative Python stacks around `Value.unpack_from` and
`Value.pack` so unbounded nesting does not require a recursive schema or
Python recursion.

### DNS

Import DNS types from `rustruct.protocols.dns`; they are not re-exported by
the general `rustruct.protocols` package.

DNS is intentionally hybrid. Fixed-width layouts are declarative, while
domain-name compression is an algorithmic outer codec.

## DNS design boundary

### What is declarative

Keep these portions as real `Struct` classes:

- `MessageHeader` and nested `DNSFlags`;
- fixed question and resource-record tails;
- RDATA without domain names, such as address, DNSSEC scalar/blob, CAA, and
  LOC records;
- independent fixed runs inside mixed RDATA, such as MX, SOA, SRV, NAPTR,
  RRSIG, and HTTPS prefixes.

Use `convert()` for value-level representations such as IP addresses and LOC
coordinates.

### Why compressed names stay manual

Reading a DNS compression pointer requires three things the schema VM does
not expose:

1. the pointer consumes two bytes at the current record cursor;
2. label decoding jumps to an absolute offset from the start of the complete
   DNS message;
3. after following the pointer chain, the enclosing record resumes after the
   original pointer, not after the referenced labels.

A pointer inside RDATA may target bytes outside its `RDLENGTH` window, so an
exact `sized()` child cannot own this traversal.

Writing compressed names requires:

- the current absolute output position;
- a suffix table shared by questions and every record section;
- knowledge of every name suffix emitted earlier;
- writing RDATA directly into the final message buffer.

Name-bearing RDATA cannot be packed into an independent temporary buffer:
temporary offsets would change pointer targets and compression decisions.
`RDLENGTH` is therefore patched only after the body has been written into the
final message.

### Alternatives that are not improvements

- `cstring(encoding="latin-1")` is not a DNS name codec. Labels are
  length-prefixed octet strings and may contain zero; a pointer byte may also
  be zero. It cannot dereference absolute pointers.
- Supporting only uncompressed names is not a complete DNS decoder and still
  lacks a sentinel-controlled label array.
- Exposing labels plus unresolved pointer offsets as a wire AST moves name
  resolution to a manual post-pass, degrades the public API, and does not
  generate compression on pack.
- Wrapping the entire packet in `slice("*") + convert()` is only cosmetically
  declarative; the callback would contain the whole real codec and lose native
  layout validation and structured errors.
- More tiny `Struct` wrappers for character strings, TXT fragments, service
  parameters, or NSEC bitmap windows can be locally valid, but are worthwhile
  only when they make the public model clearer. They do not make
  `ResourceRecord` or `DNS` declarative.
- A custom cursor callback, generic seek/state instruction, or DNS-specific
  opcode could change the boundary, but that is a core architecture proposal,
  not a better use of the current API. It must define ownership, windows,
  streaming, errors, safety, and pack-time backpatching first.

### DNS inheritance guidance

Inheritance is useful only for identical complete wire algorithms.
`SingleName` is the good example: `NS`, `CNAME`, `PTR`, and `DNAME` all contain
exactly one compressible domain name and differ only by `RTYPE`. Inherited
dataclass construction, representation, type-sensitive equality, pack, and
unpack preserve their public behavior while removing duplicate codecs.

Do not generalize the rest merely because several records contain names:

- `RP` has two names, but introducing dynamic field-name metadata for one
  additional class adds indirection with little reduction.
- MX, SRV, SOA, NAPTR, RRSIG, NSEC, and HTTPS differ in fixed prefixes and
  suffixes, name positions, tail transformations, framing, and whether
  compression is allowed.
- `Question` and `ResourceRecord` have different return shapes and
  backpatching responsibilities.
- automatic registration through a shared `__init_subclass__` would mix
  `Struct` and dataclass hierarchies and alter the public `RDataKind`
  contract.

Revisit a shared base only when multiple concrete types have the same field
order, cursor behavior, framing, and compression policy, with subclasses
mostly supplying a tag.

### Adding or changing DNS records

- Use a full `Struct` for RDATA that contains no domain name.
- For name-bearing RDATA, keep a small dataclass with
  `pack_into(out, ctx)` and `unpack(buf, off, length, ctx)`.
- Express independent fixed runs inside that dataclass as nested `Struct`
  helpers.
- Use `write_name(..., None)` for fields whose RFC forbids compression.
  Continue tolerant decoding unless the relevant specification requires
  rejection.
- Preserve backward-only pointer validation, the 14-bit pointer range,
  63-byte label limit, and 255-byte wire-name limit.
- Preserve unknown RDATA bytes and unknown numeric enum values.
- Update `RRType`, `RDataKind`, exports, documentation, direct wire tests,
  malformed-input tests where relevant, and independent fixtures.
- Run all DNS protocol test modules, then the complete Python suite.

## Completion checklist

Before declaring a change complete:

- inspect and preserve unrelated user changes;
- keep the implementation in the correct architectural layer;
- add focused regression tests;
- test both pack and unpack, including malformed input when parsing changes;
- rebuild the extension after Rust changes;
- run formatting, lint, typing, and documentation checks appropriate to the
  touched layers;
- update public docs and exports when behavior changes;
- avoid committing generated caches/build directories;
- report any checks not run and why.
