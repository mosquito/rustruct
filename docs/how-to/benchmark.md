# How to benchmark

## Run the built-in suite

Use this guide to reproduce the published measurements or measure a schema
that represents your application. The performance model and current results
are described in {doc}`/explanation/performance`.

The benchmark is an independent uv project so comparison libraries do not
enter the main package environment:

```console
cd benchmark
uv sync
uv run python run.py
```

The default profile runs three rounds, spends at least 20 ms per round, and
executes at least 10 calls per round. It covers:

- scalar field-count scaling;
- arrays of nested records;
- a framed telemetry message with mixed fields and dynamic payloads;
- `IO[bytes]` read-plus-unpack interop;
- `ThreadPool.imap_unordered` pack and unpack throughput.

## Choose a timing profile

Use a longer profile before publishing release comparisons:

```console
uv run python run.py --budget 0.1 --rounds 5 --min-iterations 50
```

Use a smoke profile while changing the runner or an implementation:

```console
uv run python run.py --budget 0.001 --rounds 1 --min-iterations 1
```

Increase the job count or change batching when investigating thread-pool
behaviour:

```console
uv run python run.py --thread-jobs 5000 --thread-chunksize 16
```

## Measure an application schema

Keep schema construction outside the timed loop, then measure the steady-state
operations used by the application. Match all of these properties:

1. Use the exact schema and payload-size distribution.
2. Start pack from the same mapping or typed object used in production.
3. Materialize and consume unpacked fields; do not time an aggregate-only
   parser against libraries that construct records.
4. Use the real buffer source, such as `bytes`, `memoryview`, or an
   `IO[bytes]` read.
5. Include the intended API layer: {py:class}`rustruct.Struct` for typed
   objects or {py:class}`rustruct.Codec` for mappings.
6. Test concurrency only with the actual job size and batching policy.

Record the CPU, operating system, Python and dependency versions, runner
options, and whether the machine was isolated. Small differences without that
context should not drive a design decision.
