"""Complexity sweep plus a realistic nested-message and IO interop case.

Workloads:

* **scalars(n)** -- a fixed struct of n u16 fields (field-count scaling).
* **vector(m)** -- a u16 count + m (u16, u16) items (length scaling).
* **telemetry(m)** -- a length-framed, mixed-width message with a dynamic
  string and m nested records, each with a dynamic bytes payload.

For each library and size we time pack and unpack, then least-squares fit
``ns = base + slope * size`` so the per-field / per-item cost (``slope``) and the
fixed per-call overhead (``base``) are visible -- that is the runtime complexity.

The telemetry message also has an IO interop case.  It repeatedly reads one
complete message from an ``IO[bytes]`` (``BytesIO``) before decoding it and
reports both the isolated read cost and the complete read/decode pipeline.
Finally, ``ThreadPool.imap_unordered`` submits many telemetry pack/unpack jobs
through 1, 2, 4, and 8 workers to make thread scaling (or its absence) visible.

Progress ("what is running now") goes to **stderr**; the result tables go to
**stdout** via ``rich`` at the end of each case.

Implementations are loaded by file path (never via ``sys.path``), so the
``struct`` / ``ctypes`` / ``construct`` directory names cannot shadow the real
modules those impls import.
"""

import argparse
import importlib.util
import io
import itertools
import pathlib
import statistics
import sys
import time
from collections import deque
from multiprocessing.pool import ThreadPool

from rich.console import Console
from rich.table import Table

HERE = pathlib.Path(__file__).parent
OUT = Console(width=118)  # keep tables readable even when stdout is piped
LOG = Console(stderr=True)

IMPLEMENTATIONS = [
    ("struct", "struct"),
    ("ctypes", "ctypes"),
    ("dataclasses-struct", "dataclasses_struct"),
    ("construct", "construct"),
    ("rustruct-core", "rustruct_core"),
    ("rustruct", "rustruct"),
]

SCALAR_SIZES = [1, 2, 4, 8, 16, 32, 64]
VECTOR_SIZES = [1, 4, 16, 64, 256, 1024]
TELEMETRY_SIZES = [1, 4, 16, 64, 256]
INTEROP_SIZE = 64
THREADPOOL_SIZE = 16
THREADPOOL_WORKERS = (1, 2, 4, 8)
DEFAULT_THREAD_JOBS = 1_000
DEFAULT_THREAD_CHUNKSIZE = 8

DEFAULT_BUDGET = 0.02
DEFAULT_ROUNDS = 3
DEFAULT_MIN_ITERATIONS = 10


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # so impls can ``import common`` without sys.path
    spec.loader.exec_module(module)
    return module


def measure(fn, *, budget, rounds, min_iterations):
    """Best-of-``rounds`` ns/op with a minimum sample size and time budget."""
    fn()  # warm up
    iterations = min_iterations
    while True:
        start = time.perf_counter()
        for _ in range(iterations):
            fn()
        elapsed = time.perf_counter() - start
        if elapsed >= budget:
            break
        iterations = max(iterations * 2, int(iterations * budget / elapsed) + 1)
    best = elapsed / iterations
    for _ in range(rounds - 1):
        start = time.perf_counter()
        for _ in range(iterations):
            fn()
        best = min(best, (time.perf_counter() - start) / iterations)
    return best * 1e9


def fit_line(xs, ys):
    """Least-squares ns = base + slope * size -> (slope, base)."""
    if len(xs) < 2:
        return 0.0, ys[0]
    slope, base = statistics.linear_regression(xs, ys)
    return slope, base


def run_task(mods, task, sizes, wire_of, checksum_of, unit, measure_fn):
    LOG.rule(f"[bold]{task}[/]  ({unit} = {sizes[0]}..{sizes[-1]})")
    packs, unpacks = {}, {}
    for label, _ in IMPLEMENTATIONS:
        mod = mods[label]
        if task not in mod.SUPPORTS:
            LOG.log(f"[dim]{label}: skips {task}[/]")
            continue
        make = getattr(mod, f"make_{task}")
        packs[label], unpacks[label] = {}, {}
        for size in sizes:
            wire, checksum = wire_of(size), checksum_of(size)
            LOG.log(f"{task}  [cyan]{label}[/]  {unit}={size}")
            pack, unpack = make(size)
            if pack() != wire or unpack(wire) != checksum:
                LOG.log(f"[red]!! {label} {task}({size}) incorrect[/]")
                continue
            packs[label][size] = measure_fn(pack)
            unpacks[label][size] = measure_fn(lambda u=unpack, w=wire: u(w))
    return packs, unpacks


def make_io_reader(wire):
    """Build a repeated fixed-record ``IO[bytes].read(size)`` call.

    A roughly 1 MiB stream contains many complete frames, so seek-to-start is
    rare and its amortized cost does not dominate the measurement.  ``read``
    still allocates an exact-size ``bytes`` object.
    """
    copies = max(2, min(1024, (1 << 20) // len(wire)))
    source = io.BytesIO(wire * copies)
    size = len(wire)

    def read_one():
        data = source.read(size)
        if len(data) != size:
            source.seek(0)
            data = source.read(size)
        return data

    return read_one


def make_io_unpack(unpack, wire):
    """Build the measured ``IO[bytes].read(size) -> unpack(bytes)`` pipeline."""
    read_one = make_io_reader(wire)

    def unpack_from_io():
        return unpack(read_one())

    return unpack_from_io


def run_interop(mods, size, wire, checksum, measure_fn):
    LOG.rule(f"[bold]IO[bytes] interop[/]  (telemetry records={size}, wire={len(wire):,} B)")
    read_ns = measure_fn(make_io_reader(wire))
    streamed = {}
    for label, _ in IMPLEMENTATIONS:
        mod = mods[label]
        if "telemetry" not in mod.SUPPORTS:
            continue
        LOG.log(f"interop  [cyan]{label}[/]")
        _, unpack = mod.make_telemetry(size)
        unpack_from_io = make_io_unpack(unpack, wire)
        if unpack(wire) != checksum or unpack_from_io() != checksum:
            LOG.log(f"[red]!! {label} telemetry IO incorrect[/]")
            continue
        streamed[label] = measure_fn(unpack_from_io)
    return read_ns, streamed


def measure_threadpool(fn, arg, *, workers, jobs, chunksize, rounds):
    """Best ns/job through one reused ``ThreadPool.imap_unordered``.

    Pool startup and shutdown are outside the timed section. Queueing,
    scheduling, result transfer, result destruction, and the codec call itself
    are inside it.
    """
    fn(arg)
    best = float("inf")
    with ThreadPool(workers) as pool:
        for _ in range(rounds):
            args = itertools.repeat(arg, jobs)
            start = time.perf_counter()
            deque(pool.imap_unordered(fn, args, chunksize), maxlen=0)
            best = min(best, time.perf_counter() - start)
    return best / jobs * 1e9


def run_threadpool(mods, size, jobs, chunksize, rounds, wire, checksum):
    LOG.rule(f"[bold]ThreadPool.imap_unordered[/]  (telemetry records={size}, jobs={jobs:,}, chunksize={chunksize})")
    packs, unpacks = {}, {}
    for label, _ in IMPLEMENTATIONS:
        mod = mods[label]
        if "telemetry" not in mod.SUPPORTS:
            continue
        pack, unpack = mod.make_telemetry(size)
        if pack() != wire or unpack(wire) != checksum:
            LOG.log(f"[red]!! {label} telemetry threadpool incorrect[/]")
            continue

        def pack_one(_arg, p=pack):
            return p()

        def unpack_one(data, u=unpack):
            return u(data)

        packs[label], unpacks[label] = {}, {}
        for workers in THREADPOOL_WORKERS:
            LOG.log(f"threadpool  [cyan]{label}[/]  workers={workers}")
            options = {
                "workers": workers,
                "jobs": jobs,
                "chunksize": chunksize,
                "rounds": rounds,
            }
            packs[label][workers] = measure_threadpool(pack_one, None, **options)
            unpacks[label][workers] = measure_threadpool(unpack_one, wire, **options)
    return packs, unpacks


def render(title, sizes, data, unit):
    """One table: absolute ns per size, the fitted per-unit cost, that cost
    relative to hand-written struct (1.00x), and the fitted per-call overhead
    (the intercept -- in ns, may dip below zero on fit noise)."""
    table = Table(title=title, title_justify="left", title_style="bold")
    table.add_column("impl")
    for s in sizes:
        table.add_column(f"{unit}={s}", justify="right")
    table.add_column(f"ns/{unit}", justify="right", style="bold")
    table.add_column("vs struct", justify="right")
    table.add_column("call ns", justify="right", style="dim")

    def last(row):
        return row.get(sizes[-1], next(iter(row.values())))

    fits = {}
    for label, row in data.items():
        if row:
            pts = [(s, row[s]) for s in sizes if s in row]
            fits[label] = fit_line([x for x, _ in pts], [y for _, y in pts])
    baseline = fits.get("struct", (None, None))[0]

    order = sorted(fits, key=lambda k: last(data[k]))
    for label in order:
        row = data[label]
        slope, base = fits[label]
        cells = [f"{row[s]:,.0f}" if s in row else "-" for s in sizes]
        ratio = f"{max(slope, 0.0) / baseline:,.2f}x" if baseline else "-"
        style = "bold cyan" if label == "rustruct" else ("dim" if label == "struct" else "")
        table.add_row(label, *cells, f"{slope:,.1f}", ratio, f"{base:,.0f}", style=style)
    OUT.print(table)
    OUT.print()


def render_interop(size, wire_size, read_ns, streamed):
    """Show the isolated read cost and the complete IO decoding pipeline."""
    OUT.print(f"[dim]isolated BytesIO.read({wire_size:,}) allocation: {read_ns:,.0f} ns/op[/]")
    table = Table(
        title=f"telemetry: IO[bytes].read + unpack ({size} records, {wire_size:,} B)",
        title_justify="left",
        title_style="bold",
    )
    table.add_column("impl")
    table.add_column("IO total", justify="right", style="bold")
    table.add_column("IO throughput", justify="right")

    for label in sorted(streamed, key=streamed.get):
        total = streamed[label]
        mib_s = wire_size / (total / 1e9) / (1 << 20)
        style = "bold cyan" if label == "rustruct" else ("dim" if label == "struct" else "")
        table.add_row(
            label,
            f"{total:,.0f} ns",
            f"{mib_s:,.1f} MiB/s",
            style=style,
        )
    OUT.print(table)
    OUT.print()


def render_threadpool(title, data, jobs, chunksize):
    table = Table(
        title=f"{title} ({jobs:,} jobs, chunksize={chunksize}; ns/job)",
        title_justify="left",
        title_style="bold",
    )
    table.add_column("impl")
    for workers in THREADPOOL_WORKERS:
        suffix = "s" if workers != 1 else ""
        table.add_column(f"{workers} worker{suffix}", justify="right")
    table.add_column("best speedup", justify="right", style="bold")
    table.add_column("best rate", justify="right")

    for label in sorted(data, key=lambda key: min(data[key].values())):
        row = data[label]
        best_workers = min(row, key=row.get)
        speedup = row[1] / row[best_workers]
        rate = 1e9 / row[best_workers]
        cells = [f"{row[workers]:,.0f}" for workers in THREADPOOL_WORKERS]
        style = "bold cyan" if label == "rustruct" else ("dim" if label == "struct" else "")
        table.add_row(
            label,
            *cells,
            f"{speedup:.2f}x @ {best_workers}",
            f"{rate:,.0f} jobs/s",
            style=style,
        )
    OUT.print(table)
    OUT.print()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--budget",
        type=float,
        default=DEFAULT_BUDGET,
        help=f"minimum seconds per timing round (default: {DEFAULT_BUDGET})",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=DEFAULT_ROUNDS,
        help=f"timing rounds; the best is reported (default: {DEFAULT_ROUNDS})",
    )
    parser.add_argument(
        "--min-iterations",
        type=int,
        default=DEFAULT_MIN_ITERATIONS,
        help=f"minimum calls in every round (default: {DEFAULT_MIN_ITERATIONS})",
    )
    parser.add_argument(
        "--thread-jobs",
        type=int,
        default=DEFAULT_THREAD_JOBS,
        help=f"jobs per ThreadPool timing round (default: {DEFAULT_THREAD_JOBS})",
    )
    parser.add_argument(
        "--thread-chunksize",
        type=int,
        default=DEFAULT_THREAD_CHUNKSIZE,
        help=f"imap_unordered chunksize (default: {DEFAULT_THREAD_CHUNKSIZE})",
    )
    args = parser.parse_args(argv)
    positive = (
        args.budget,
        args.rounds,
        args.min_iterations,
        args.thread_jobs,
        args.thread_chunksize,
    )
    if any(value <= 0 for value in positive):
        parser.error("all numeric benchmark options must be positive")
    return args


def main(argv=None):
    args = parse_args(argv)
    load_module("common", HERE / "common.py")
    common = sys.modules["common"]
    mods = {label: load_module(f"impl_{d}", HERE / d / "impl.py") for label, d in IMPLEMENTATIONS}

    def measure_fn(fn):
        return measure(
            fn,
            budget=args.budget,
            rounds=args.rounds,
            min_iterations=args.min_iterations,
        )

    OUT.print(
        f"[bold]python {sys.version.split()[0]}[/]  best of {args.rounds}, ns/op; "
        f">={args.min_iterations} calls/round, >={args.budget:g}s/round; "
        f"[bold]ns/unit[/] = fitted cost per field/item"
    )

    sp, su = run_task(mods, "scalars", SCALAR_SIZES, common.scalars_wire, common.scalars_checksum, "n", measure_fn)
    render("scalars: pack   (n u16 fields)", SCALAR_SIZES, sp, "n")
    render("scalars: unpack (n u16 fields)", SCALAR_SIZES, su, "n")

    vp, vu = run_task(mods, "vector", VECTOR_SIZES, common.vector_wire, common.vector_checksum, "m", measure_fn)
    render("vector: pack   (m items)", VECTOR_SIZES, vp, "m")
    render("vector: unpack (m items)", VECTOR_SIZES, vu, "m")

    tp, tu = run_task(
        mods,
        "telemetry",
        TELEMETRY_SIZES,
        common.telemetry_wire,
        common.telemetry_checksum,
        "m",
        measure_fn,
    )
    render("telemetry: pack   (m nested records)", TELEMETRY_SIZES, tp, "m")
    render("telemetry: unpack (m nested records)", TELEMETRY_SIZES, tu, "m")

    wire = common.telemetry_wire(INTEROP_SIZE)
    checksum = common.telemetry_checksum(INTEROP_SIZE)
    read_ns, streamed = run_interop(mods, INTEROP_SIZE, wire, checksum, measure_fn)
    render_interop(INTEROP_SIZE, len(wire), read_ns, streamed)

    thread_wire = common.telemetry_wire(THREADPOOL_SIZE)
    thread_checksum = common.telemetry_checksum(THREADPOOL_SIZE)
    thread_packs, thread_unpacks = run_threadpool(
        mods,
        THREADPOOL_SIZE,
        args.thread_jobs,
        args.thread_chunksize,
        args.rounds,
        thread_wire,
        thread_checksum,
    )
    render_threadpool(
        f"telemetry: ThreadPool pack ({THREADPOOL_SIZE} records)",
        thread_packs,
        args.thread_jobs,
        args.thread_chunksize,
    )
    render_threadpool(
        f"telemetry: ThreadPool unpack ({THREADPOOL_SIZE} records)",
        thread_unpacks,
        args.thread_jobs,
        args.thread_chunksize,
    )


if __name__ == "__main__":
    main()
