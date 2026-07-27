# Understanding performance

This page explains where pack and unpack time comes from, which comparisons
perform equivalent work, and how to interpret the current benchmark snapshot.
For instructions rather than analysis, use {doc}`/how-to/benchmark`.

## Benchmark environment

The result tables on this page were captured in the following environment on
2026-07-26:

| Component | Value |
| --- | --- |
| Machine | Apple M4, 10 CPU cores, 24 GiB RAM, arm64 |
| Operating system | macOS 26.2 (build 25C56) |
| Python | 64-bit CPython 3.14.2 |
| Standard-library baselines | `struct` and `ctypes` from that CPython build |
| Third-party comparisons | Construct 2.10.70 and dataclasses-struct 1.5.1 |
| rustruct | current local editable checkout |
| Sampling | best of 3 rounds; at least 20 ms and 10 calls per round |
| Complexity sweep | scalars 1–64 fields; vectors 1–1,024 items; telemetry 1–256 records |
| I/O case | `BytesIO`, one 3,066-byte frame with 64 telemetry records |
| Thread case | 1,000 jobs, 16 records/job, `chunksize=8`, 1/2/4/8 workers |

The runner does not pin CPU cores, change process priority, or isolate the
machine from other user processes. The environment is therefore context for a
local snapshot, not a claim that the exact numbers are universally
reproducible. Use the same runner on the deployment hardware for decisions
based on small differences.

## What is compiled

The frontend resolves each class once. A cached native {py:class}`rustruct.Codec` handles the
whole structure in one Python-to-Rust call. Specialized Python methods convert
between typed instances and mappings without interpreting field descriptors
on every operation.

The compiler coalesces adjacent fixed fields into one operation and stores
static size information in the program. Unpack builds Python dictionaries,
lists, integers, and bytes directly during the native walk.

## Cost of typed results

{py:class}`rustruct.Struct`
: Returns typed Python instances and nested typed objects.

{py:func}`rustruct.compile` / {py:class}`rustruct.Codec`
: Returns dictionaries and avoids frontend object conversion.

Hand-written `struct`
: Useful as a low-level baseline for simple, fixed layouts, but does not provide
  schema validation, windows, derived fields, switches, or streaming state.

For arrays of nested structures, typed object construction is intentionally
visible in the cost. The mapping API avoids that conversion when an
application does not require typed objects.

## Capability matrix

A timing result is only useful when the implementations perform comparable
work. This matrix is scoped to protocol features used by the benchmark and by
this project; it is not intended as an exhaustive catalogue of every library
extension.

**Legend:** ✅ built in, ⚠️ possible with manual code or a restricted layout,
❌ not supported by the tested schema model.

| Capability | `struct` | `ctypes` | `dataclasses-struct` | `construct` | `rustruct-core` | `rustruct` |
| --- | --- | --- | --- | --- | --- | --- |
| Fixed numeric and byte fields | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Named unpacked record | ⚠️ manual conversion | ✅ `Structure` | ✅ dataclass | ✅ `Container` | ✅ `dict` | ✅ typed {py:class}`rustruct.Struct` |
| Nested fixed records | ⚠️ manual composition | ✅ | ✅ | ✅ | ✅ | ✅ |
| Runtime-sized bytes and text | ⚠️ manual slicing | ⚠️ type per size | ❌ fixed length only | ✅ | ✅ | ✅ |
| Counted runtime-sized arrays | ⚠️ manual loop | ⚠️ type per count | ❌ fixed length only | ✅ | ✅ | ✅ |
| Derive lengths and counts on pack | ⚠️ manual | ❌ | ❌ | ✅ | ✅ | ✅ |
| Length-bounded nested frame | ⚠️ manual offsets and checks | ❌ | ❌ | ✅ | ✅ | ✅ |
| Conditional or tagged fields | ⚠️ manual branch | ⚠️ manual layout selection | ❌ | ✅ | ✅ | ✅ |
| Short-buffer result distinct from invalid data | ⚠️ manual | ❌ | ❌ | ⚠️ stream API/error | ✅ {py:class}`rustruct.Incomplete` | ✅ {py:class}`rustruct.Incomplete` |
| Main execution model | compiled fixed-format operations plus Python glue | native memory layout | Python dataclass wrapper over `struct` | Python schema interpreter | compiled native program | native program plus typed conversion |

Here, ⚠️ **manual** means that the format can be implemented with user-written
loops, offsets, slices, or layout selection, but the library does not express
that relationship in the tested schema. For example, `struct` can implement
the complete telemetry frame, but the framing, dynamic arrays, and validation
are application code. A `ctypes` type can be generated after a size is known,
but one static `Structure` does not model a count read from the same message.

The hand-written conversions are deliberately inside the timed path. They
emulate adapter code that a user must write to cross between `struct`'s
positional tuples and the named mappings normally used by application code.
The schema libraries perform the equivalent field lookup and result
construction internally, so timing only the `struct.pack`/`unpack` calls would
hide work rather than eliminate it. If an application genuinely consumes and
produces positional tuples directly, a raw `struct` microbenchmark is relevant
to that application, but it is a different API contract from this comparison.

The benchmark therefore has two coverage levels:

**Legend:** ✅ measured, ⚠️ measured using hand-written protocol logic,
❌ skipped because the workload is not represented by that implementation.

| Workload | `struct` | `ctypes` | `dataclasses-struct` | `construct` | `rustruct-core` | `rustruct` |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Fixed scalar fields | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Counted nested vector | ⚠️ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Dynamic telemetry frame | ⚠️ | ❌ | ❌ | ✅ | ✅ | ✅ |
| `IO[bytes]` telemetry interop | ⚠️ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Thread-pool telemetry jobs | ⚠️ | ❌ | ❌ | ✅ | ✅ | ✅ |

## Performance snapshot

The following snapshot was collected in the environment described above.
Lower values are better.

| Workload and fitted cost | `struct` | `ctypes` | `dataclasses-struct` | `construct` | `rustruct-core` | `rustruct` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| scalar pack, ns/field | 72.8 | <0.1 | 328.4 | 543.0 | 47.4 | 53.2 |
| scalar unpack, ns/field | 41.8 | 73.5 | 1,359.7 | 595.4 | 104.3 | 117.9 |
| vector pack, ns/item | 124.5 | — | — | 2,660.0 | 155.2 | 383.2 |
| vector unpack, ns/item | 270.2 | — | — | 3,081.4 | 205.9 | 393.3 |
| telemetry pack, ns/record | 343.6 | — | — | 7,102.1 | 573.3 | 1,024.7 |
| telemetry unpack, ns/record | 1,010.0 | — | — | 9,632.8 | 1,065.6 | 1,193.7 |

These are least-squares fitted incremental costs across a range of message
sizes, not the latency of a single call. Fixed call overhead is represented by
a separate fitted intercept in the runner. A slope close to zero can occur
when the changing part is too cheap to resolve reliably; it does not mean an
operation is free.

## Why the results differ

| Implementation | Work performed in the timed path | How to interpret the result |
| --- | --- | --- |
| `struct` | Converts named mappings to positional arguments, calls compiled C format operations, and implements dynamic fields with hand-written Python loops and slices. Unpack materializes `dict` and nested `list[dict]` results before the checksum reads them. | A low-level comparison baseline with an optimized hand-written mapping adapter. Schema relationships, validation, and result construction are still bespoke application code. |
| `ctypes` | Pack copies an already-populated contiguous `Structure` to `bytes`; unpack copies bytes into a `Structure`, then reads every attribute for the checksum. | The near-zero scalar pack slope measures a small memory copy. Object population is outside the timed loop, and the dynamic workloads are absent. |
| `dataclasses-struct` | Flattens dataclass attributes into one `struct.pack` call; unpack expands the tuple and constructs a dataclass, including nested fixed dataclasses. | Typed-object and Python flattening/construction costs dominate. Its fixed-length design cannot express the vector or telemetry cases. |
| `construct` | Interprets a general declarative schema field by field in Python and constructs `Container` and list objects. | It provides the closest feature comparison in this runner, but pays Python dispatch and allocation costs for that generality. |
| `rustruct-core` | Runs one compiled native program per message and creates Python dictionaries, lists, integers, strings, and bytes during the native walk. | This isolates the codec and Python-to-Rust boundary without typed frontend conversion. |
| `rustruct` | Runs the same native codec, then converts mappings and nested records to typed {py:class}`rustruct.Struct` instances. | The difference from `rustruct-core` is the visible price of the typed API, especially for arrays of nested objects. |

The telemetry workload is the primary end-to-end comparison. It combines a
size window, mixed integer widths, a fixed byte field, dynamic UTF-8 text,
nested records, and dynamic payloads. Pack starts from named mappings or typed
objects, and unpack materializes each implementation's normal named result
before every decoded field contributes to a checksum. All implementations
produce identical wire bytes. The checksum prevents lazy or incomplete
decoding from looking fast, while the materialization rule prevents the manual
baseline from acting as a specialized parser that returns only an aggregate.

## Buffers and I/O

Direct decoding from an existing contiguous buffer avoids an intermediate
read allocation. Reading from `IO[bytes]` first allocates a `bytes` object
before the buffer crosses the extension boundary. The benchmark suite measures
both this isolated read cost and the full read-plus-unpack pipeline.

For a 3,066-byte telemetry frame containing 64 records, the same run measured:

| Implementation | `IO[bytes]` read + unpack | Throughput |
| --- | ---: | ---: |
| `struct` | 65,652 ns | 44.5 MiB/s |
| `rustruct-core` | 66,267 ns | 44.1 MiB/s |
| `rustruct` | 84,640 ns | 34.5 MiB/s |
| `construct` | 634,434 ns | 4.6 MiB/s |

An isolated `BytesIO.read(3066)` allocation took 218 ns in this run. Comparing
that number with the complete pipeline separates the price of obtaining
`bytes` from the price of decoding and constructing the result.

{py:meth}`rustruct.Struct.pack_into` avoids allocating the final destination buffer, although the
current native implementation still constructs the encoded bytes before
copying them into that destination.

## Threads

The benchmark includes `ThreadPool.imap_unordered` pack and unpack workloads
with 1, 2, 4, and 8 workers. Native implementation alone does not imply
scaling across Python threads: scheduling overhead and GIL-held portions can
outweigh any parallel work.

The default thread workload processes 1,000 independent jobs through
`ThreadPool.imap_unordered`; every job packs or unpacks 16 telemetry records
and the pool uses a chunksize of 8.

| Operation | Implementation | 1 worker | 2 workers | 4 workers | 8 workers | Best speedup |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| pack | `struct` | 7.60 µs/job | 7.08 µs/job | 7.11 µs/job | 7.02 µs/job | 1.08x |
| pack | `rustruct-core` | 11.01 µs/job | 10.93 µs/job | 11.02 µs/job | 11.38 µs/job | 1.01x |
| pack | `rustruct` | 19.68 µs/job | 19.64 µs/job | 19.79 µs/job | 19.81 µs/job | 1.00x |
| pack | `construct` | 138.08 µs/job | 131.94 µs/job | 136.08 µs/job | 130.54 µs/job | 1.06x |
| unpack | `struct` | 18.86 µs/job | 18.66 µs/job | 18.83 µs/job | 18.94 µs/job | 1.01x |
| unpack | `rustruct-core` | 18.39 µs/job | 18.47 µs/job | 18.70 µs/job | 18.86 µs/job | 1.00x |
| unpack | `rustruct` | 22.78 µs/job | 22.75 µs/job | 23.25 µs/job | 23.19 µs/job | 1.00x |
| unpack | `construct` | 174.55 µs/job | 172.89 µs/job | 176.29 µs/job | 181.00 µs/job | 1.01x |

This run shows no meaningful scaling from extra threads. Treat that as a
property of this CPU-bound workload and current implementation, not as a
universal thread-pool result: real applications can still benefit when jobs
also spend time in I/O or in code that releases the GIL.

## Reproduce or extend the measurements

Follow {doc}`/how-to/benchmark` to construct an
application-specific comparison without omitting mapping and object
materialization costs.
