# rustruct benchmarks

A separate uv project (its own `pyproject.toml`), so the competitor libraries
never touch the `rustruct` package itself. `rustruct` is pulled in as an
editable path dependency from the sibling checkout.

```bash
cd benchmark
uv sync
uv run python run.py
```

The default run balances stability with iteration time: three rounds, at least
20 ms and at least 10 calls per round. Use a longer profile when collecting
release numbers, or a shorter one for a smoke test:

```bash
uv run python run.py --budget 0.1 --rounds 5 --min-iterations 50
uv run python run.py --budget 0.001 --rounds 1 --min-iterations 1
```

`--markdown` prints the same tables as markdown instead of boxes, ready to
paste into a pull request.

## Workloads

* **scalars(n)** -- a fixed struct of `n` u16 fields (field-count scaling).
* **vector(m)**  -- a u16 count followed by `m` (u16, u16) items (length scaling).
* **telemetry(m)** -- a length-framed application message with a mixed-width
  header, fixed bytes, a dynamic UTF-8 string, and `m` nested records.  Each
  record mixes signed and unsigned integers and owns a variable-length bytes
  payload.  This exercises nested structs, size windows, derived lengths,
  arrays, text decoding, and bytes materialization together.

Each workload is measured three ways: **build** (construct the schema --
`struct.Struct(fmt)`, a `construct` object graph, a `ctypes`/dataclass class,
`rustruct.compile()`), then **pack** and **unpack**. Building is paid once per
schema rather than once per message, so it never shows up in the pack/unpack
numbers -- which is exactly why a change landing entirely on the compile path
can look free there. The `rustruct` frontend compiles lazily and caches per
class, so its build case forces the codec rather than timing an empty class
statement. Build is swept over the size only for `scalars`, whose schema grows
a field per unit; a `vector` or `telemetry` schema is the same object at every
`m`, so it is reported as one number rather than a fit through a flat line.

Every pack starts from the implementation's normal named mapping or typed
object. Every unpack materializes the implementation's normal named result
before summing every field, so neither lazy decoders nor a specialized
aggregate-only parser can cheat. In particular, the hand-written `struct`
baseline converts mappings to positional arguments and materializes `dict` and
nested `list[dict]` results inside the timed path.

That conversion is not synthetic busywork added to slow down `struct`. It
emulates the adapter code a user cannot avoid when the application works with
named fields: `struct` accepts and returns positional values, while the
application needs mappings and nested records. Construct and rustruct perform
the corresponding lookup and materialization inside their public APIs. An
application designed around positional tuples can omit this adapter, but then
it is measuring a different, less ergonomic API contract.

`scalars` is the smallest workload here, and its numbers are correspondingly
sensitive to where the compiler happened to place code: two builds of *identical*
source, differing only in the order two declarations appear in a file, have been
measured 5% apart on `scalars: pack`. Treat a difference of that size on that row
as unattributable unless something in the packing path actually changed; the
`vector` and `telemetry` rows do far more work per call and do not wobble like
that.

Iteration counts auto-scale to a fixed time budget. For each library we
least-squares fit `ns = base + slope * size`: `slope` (ns/field, ns/item) is
the per-unit complexity constant; `call ns` is the fitted per-call overhead
(an extrapolated intercept, which can dip below zero).

The telemetry workload is byte-for-byte identical in the hand-written
`struct`, `construct`, raw `rustruct` codec, and typed `rustruct.Struct`
implementations. `ctypes` and `dataclasses-struct` remain in the fixed-layout
scalar test, but are skipped for dynamic nested layouts they do not model.

`rustruct-core` uses the raw `compile()`/`Codec` API directly (plain dicts,
no `Struct` frontend at all) -- it isolates how much of `rustruct`'s cost is
the core + the Python<->Rust FFI crossing versus the frontend built on top.

## `IO[bytes]` interop

After the in-memory workloads, the runner benchmarks a 64-record telemetry
message through this pipeline:

```python
data = source.read(message_size)  # source is an IO[bytes]
record = unpack(data)
```

The source is a `BytesIO` containing many consecutive complete messages, so
rewinding is rare and amortized. The runner measures the `BytesIO.read`
allocation in isolation, then reports total `IO[bytes].read + unpack` time and
end-to-end throughput. Direct `bytes` decoding is already present at `m=64` in
the telemetry unpack table. The isolated read number is the readable price of
the I/O boundary; the runner deliberately does not subtract two independently
timed decoder runs because that small difference is easily hidden by allocator
and GC noise. This is the best-case in-memory I/O boundary; filesystem or
socket latency is intentionally outside the codec benchmark.

## ThreadPool scaling

The final case submits 1,000 pack jobs and 1,000 unpack jobs for the
16-record telemetry message through
`multiprocessing.pool.ThreadPool.imap_unordered`, using 1, 2, 4, and 8 worker
threads. The pool is reused across timing rounds, so startup and shutdown are
excluded; task queueing, scheduling, result transfer/destruction, and codec
work are included. `chunksize=8` keeps the test focused on codec parallelism
without making one queue operation dominate every small job.

The tables report nanoseconds per completed job, the best speedup relative to
one worker, and the best throughput. The workload intentionally uses the same
compiled codec/object concurrently, matching a shared-codec service. Job count
and chunk size are configurable:

```bash
uv run python run.py --thread-jobs 5000 --thread-chunksize 16
```

## Previous micro-workload results

The captured numbers below predate the telemetry and `IO[bytes]` workloads and
the rule that the hand-written baseline must convert to and from named
mappings. They are retained as optimization history, not as output from the
current runner. In particular, the historical `struct` rows use positional
inputs and do not materialize named results, so they must not be compared with
current output. Run `run.py` for current results on the local machine.

Captured on this machine, CPython 3.14.2, best of 3, nanoseconds/op.

### Complexity constants

| task                    | struct | ctypes | dataclasses-struct | construct | **rustruct-core** | **rustruct** |
|-------------------------|-------:|-------:|--------------------:|----------:|-------------------:|-------------:|
| scalar pack, ns/field   |    8.8 |    0.0 |               333.3 |     636.1 |          **89.2** |     **95.2** |
| scalar unpack, ns/field |   12.6 |   73.6 |             1,353.3 |     716.5 |         **135.5** |    **148.4** |
| vector pack, ns/item    |   91.3 |      - |                    - |   2,687.9 |         **184.3** |    **405.5** |
| vector unpack, ns/item  |  116.0 |      - |                    - |   3,056.8 |         **205.0** |    **397.6** |

## Reading the results honestly

**The core is now where the time goes.** Compare `rustruct-core` to
`rustruct` directly: the `Struct` frontend adds ~3% on scalar pack, ~6% on
scalar unpack, and ~60% on the array-of-nested-struct cases. That last gap
is the irreducible price of what the frontend actually delivers there -- a
real typed Python object per element (one allocation plus one method call
per item) instead of the core's plain dict. If plain dicts are acceptable,
the `rustruct-core` row *is* the frontend-free API and costs nothing extra.

How the frontend got this cheap (src/rustruct/struct.py): each class gets
specialized `__init__`/`to_mapping`/`from_mapping` generated as an AST (the
`ast` module, no source-string templating) and compiled on first use.
`from_mapping` builds instances via `object.__new__` plus a direct
`__dict__` assignment (no `__init__` parameter binding), and for all-scalar
classes it *adopts* the dict the core just produced as the instance
`__dict__` outright -- zero per-field work. Arrays of nested structs and
converts are inlined as list comprehensions with the element class's
compiled converter bound directly, so no Shape-object dispatch survives on
the per-element path; a static `needs_ctx` analysis skips building the
ancestor-scope ctx tuple unless a cross-scope switch actually needs it.

A real core bug was found and fixed this round, not just frontend work:
`scalar pack` used to grow *super-linearly* with field count (measured up
to ~325 ns/field at n=128, ~757 ns/field at n=512, versus a flat ~140-180
ns/field now at every size up to n=2048). The cause was
`crates/rustruct/src/pack.rs`: a flat scalar struct compiles its n fields
into a single `Op::Fixed` opcode, and looking up each field's value by
name in the input mapping was a linear scan over the whole `Value::Map`
Vec (`Value::get`) -- O(n) per field times n fields is O(n^2) for the
struct as a whole. `pack_struct_inner` now builds a `HashMap` index once
per scope for structs above a small size threshold (16 fields), and keeps
the original linear scan below it (a real hash map costs more to build
than it saves for e.g. a 2-field array element, which is the common case
and must not regress to fix the large-struct case). `unpack` never had
this bug -- it writes fields in schema order as it decodes, no by-name
lookup involved.

A second round of work went into the pyo3 binding layer only
(`crates/rustruct-py/src/lib.rs`) -- `rustruct-core` itself (the `Value`
type, `pack::run`/`unpack::run`, the `Program` IR) is completely
untouched, still pure Rust with zero pyo3 dependency, still fuzzable and
unit-testable standalone. The old `py_to_value()` is a *total* conversion:
given pack()'s input Mapping, it eagerly walks and converts *every* key,
including ones the schema will never look up (extra keys are allowed and
ignored, spec §2), and allocates a fresh `Arc<str>` per key even though the
identical `Arc<str>` already lives in the compiled `Program`'s
`Key::Named`. A new `value_for_program()` instead walks `Program::ops` and
looks up only the fields the schema actually needs via one direct
`PyDict::get_item(name)` per field (a native C-level dict lookup, not a
scan), reusing the schema's own `Arc<str>` for the key and recursing the
same way into nested structs/array elements. Fields the packer is
guaranteed to ignore regardless of input (const/derived fields, digests)
are skipped without even being looked up. Two cases still fall back to the
original generic conversion for a field's own value: flags (its closed
key-set validation needs to see every key the caller supplied, not just
the schema's known names) and switch (which branch fired depends on
register state that only exists inside `pack::run` itself). This shows up
almost entirely on `vector pack` (each of up to 1024 array elements is a
2-field nested struct, previously re-converted and re-keyed from scratch
every time): 371.2 -> 284.2 ns/item for `rustruct-core` (-23%), 590.7 ->
509.9 for `rustruct` (-14%). Scalars are unaffected (a flat struct with no
extra keys had nothing for this pass to save).

A third round did the same for the *unpack* direction, and it is where
the intermediate `Value`-tree elimination that design/spec.md section 10
defers actually turned out to matter most. The old path built a `Value`
tree while decoding, then walked that whole tree a *second* time
(`value_to_py`) to produce the PyDict/PyBytes/etc the caller gets back --
two full passes with two full sets of allocations. `unpack::run` (in
`crates/rustruct/src/unpack.rs`) is now generic over a `Builder` trait
(`crates/rustruct/src/model.rs`, pure Rust, no pyo3) that abstracts *what
a decode actually constructs*; the reference `ValueBuilder` impl (still in
rustruct-core, used by all its own tests/fuzzing) builds the same `Value`
tree as before, byte-for-byte unchanged, while a new `PyBuilder` (living
entirely in `crates/rustruct-py/src/lib.rs`) builds real PyObjects
directly during the walk, skipping the tree entirely. `PyBuilder::int`
also takes the machine-word fast path (try `i64` first, fall back to
`i128` only if it doesn't fit) instead of always going through i128's
generic byte-array conversion, for the same reason flagged earlier this
session. Net effect: 178.5 -> 136.5 ns/field for scalar unpack
(`rustruct-core`, -24%), 345.8 -> 210.3 ns/item for vector unpack (-39%,
the two-pass cost was proportionally worse the more nested the structure).
`rustruct-core` itself gained a public generic entry point
(`unpack::run_with`) alongside the unchanged `unpack::run` convenience
wrapper -- every existing Rust test kept compiling and passing with zero
changes, since `run` still returns the same `Outcome<Value>` it always did.

A fourth round finished the job by mirroring `Builder` in the pack
direction: a `Source` trait (added to `crates/rustruct/src/model.rs`,
right alongside `Builder`) abstracts *what pack() actually reads*.
`pack::run`/`pack::run_into` (`crates/rustruct/src/pack.rs`) are now
generic over `S: Source`, reading a field's value via `map_view`/
`view_get`/`as_int`/`as_bytes`/etc instead of pattern-matching a
pre-built `Value`; the reference `ValueSource` (in `model.rs`) reads from
the same `Value` tree as always (its `map_view` reuses the exact
small-vs-hashed `FieldIndex` logic the second round introduced, so the
O(n^2) fix from round two carries over unchanged), while a new `PySource`
(`crates/rustruct-py/src/lib.rs`) reads straight from PyObjects during the
walk. The second round's `value_for_program`/`py_to_value`-based guided
conversion is gone entirely -- `Source` replaces it outright rather than
sitting on top of it.

This turned out to remove more than the second round's own guided
conversion did, because reading is now *lazy*, interleaved with the walk
itself rather than happening eagerly ahead of time -- which incidentally
dissolved the two cases round two had to special-case:
- **Flags**: `pack_flags`'s closed key-set check (spec §3.2, an unknown
  key is a PackError) now runs directly via `Source::view_keys` against
  whatever the caller actually supplied -- no separate fallback needed.
- **Switch**: which branch fired is only known once `pack::run`'s own
  register state exists; since `Source` reads a value exactly when
  `pack_value` reaches it (not before), there's no "eagerly pick a shape
  before knowing the tag" problem to work around anymore.
- For a plain `PyDict` (what `Struct.to_mapping()` always produces),
  `map_view` is just a cheap `Bound` clone -- CPython's dict is *already*
  a hash table, so unlike `ValueSource` there's no separate index to
  build per scope at all, on top of no `Value` tree to build either.

Net effect, on top of round two's already-improved numbers: 141.1 -> 89.2
ns/field for scalar pack (`rustruct-core`, -37%: scalars have no nested
structure, so round two's own gain there was small, but eliminating the
`Value`-tree-plus-`FieldIndex` construction *entirely* helps even a flat
struct) and 281.5 -> 184.3 ns/item for vector pack (-35%, the array of
nested 2-field structs no longer gets materialized into a `Value` tree at
all before being walked). Unpack numbers are unchanged this round (nothing
here touches `unpack.rs`/`PyBuilder`). Pack is now the *cheaper* direction
for scalars specifically (89.2 vs 135.5 ns/field) -- reading an existing
PyObject is less work than allocating a new one, which is what unpack must
still do for every field.

What remains is the core's other cost: every `pack()`/`unpack()` call
still crosses the Python<->Rust FFI boundary once, and design/spec.md
section 10's original target -- writing PyObject slots directly without
any Rust-side abstraction in between at all -- is a further step past what
`Builder`/`Source` already buy (they eliminate the *tree*, not the
downcast/extract cost of reading or the allocation cost of building each
individual PyObject). Against the historical positional `struct` baseline
shown above, `rustruct` already beats `construct` by 7-28x and
`dataclasses-struct` at every size on both axes.

### Fifth round: actual sampling profiling, not just reasoning from code

Everything above was reasoned from reading the code and targeted
micro-benchmarks -- never an actual sampling profiler, because macOS
without a full Xcode install has no usable one (Instruments/`xctrace`
needs Xcode, not just the command-line tools). An OrbStack Ubuntu VM (with
the project directory shared in directly, so no repo copy was needed) gave
a real `perf record -g --call-graph dwarf` against the same release build.
Two concrete, previously-invisible costs turned up:

- **`PySource::as_list` used the general Python iterator protocol**
  (`try_iter()` / `__iter__` + `__next__`) for arrays even though it had
  already downcast the value to a concrete `PyList`/`PyTuple` -- the
  profile showed `PyObject_SelfIter` + `PyIter_Next` +
  `Flatten<PyIterator>::next` as real, avoidable cost. Fixed by iterating
  a downcast `PyList`/`PyTuple` directly (`.iter()` on the concrete type,
  direct indexed access, no protocol dispatch).
- **The same fixed field-name strings were rebuilt as a fresh PyUnicode
  object on every single field, every call** -- `PyUnicode_FromStringAndSize`
  showed up on *both* `PyBuilder::map_set` (unpack, inserting a dict key)
  and `PySource::view_get` (pack, looking one up), because pyo3's
  `set_item`/`get_item` take a plain `&str` and build a throwaway PyUnicode
  from it internally every time -- for the exact same fixed set of names a
  schema will ever use. Fixed by building one `Py<PyString>` per field name
  *once*, in a `Codec::key_cache` populated by walking `Program::ops`
  recursively at `compile()` time, and having `PySource`/`PyBuilder` look
  the cached object up instead of handing pyo3 a raw `&str`.

The key-cache fix needed a second pass once measured: a first attempt used
`std::collections::HashMap`'s default hasher (SipHash, deliberately
DoS-resistant, which costs real per-hash overhead) and *regressed*
`vector`'s pack/unpack (each of up to 1024 array elements repeats the same
2 short field names, so the fix traded "build a fresh 1-byte PyUnicode" for
"SipHash a 1-byte string", a bad trade at that repetition count and string
length) while still helping `scalars`/`telemetry` (many distinct names,
each looked up once per call, so the win is amortized *across calls*
instead). Re-run with a small hand-rolled FNV-1a hasher (`FnvHasher` in
lib.rs -- schema field names are short, fixed at compile time, and never
attacker-controlled, so SipHash's DoS-resistance is buying nothing here)
recovered `vector` back to its pre-regression numbers while keeping the
`scalars`/`telemetry` win. This is why the earlier hasher choice for this
kind of cache matters more than it looks: *measure* the exact repetition
shape (few keys many times vs many keys once) before picking one, not just
its Big-O.

Net effect, on top of every round above: 141.1 -> ~89 (round four) -> **~49
ns/field** for scalar pack (`rustruct-core`, another ~45% off round four's
already-improved number), 135.5 -> **~105 ns/field** for scalar unpack
(~22%), and on the composite `telemetry` workload (mixed header + nested
records, the closest thing here to a real protocol message): pack **~678
-> ~544 ns/record** (-20%), unpack **~1,098 -> ~1,025 ns/record** (-7%).
`vector` pack/unpack are unchanged from round four (the fix was neutral
there once the hasher was fixed, not a further win) -- run `run.py` for
current numbers on all axes, they now include telemetry and ThreadPool
scaling too.

Re-run this benchmark after any core or frontend hot-path work lands; the
numbers here are the honest current state, not a target already hit.

## Not ported (yet)

A DNS-versus-`dnslib` throughput comparison (using captured real DNS packet
fixtures) wasn't built here: it would need a fixture file generated from
`dnslib` plus the `dnslib` dependency, neither of which exist in this repo
yet. `src/rustruct/protocols/dns.py` itself is fully implemented and tested
(tests/protocols/test_dns.py) -- only that head-to-head comparison is
missing.
