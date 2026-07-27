"""A tight, sustained pack/unpack loop for perf record -- not a benchmark
(no timing here, run.py already does that), just enough sustained work for
a sampling profiler to get a useful number of samples inside the Rust
extension. Run under `perf record -g -- python profile_hot_loop.py <mode>`.
"""

import sys

import rustruct

SCALAR_N = 32
VECTOR_M = 256


def make_scalars_codec():
    names = [f"f{i}" for i in range(SCALAR_N)]
    fields = tuple((name, "u16", {}) for name in names)
    codec = rustruct.compile(fields, byteorder="network")
    values = dict(zip(names, range(SCALAR_N), strict=True))
    return codec, values, names


def make_vector_codec():
    item_fields = (("a", "u16", {}), ("b", "u16", {}))
    fields = (
        ("n", "u16", {}),
        ("items", "array", {"elem": ("struct", {"fields": item_fields}), "count": ("ref", "n")}),
    )
    codec = rustruct.compile(fields, byteorder="network")
    items = [{"a": i, "b": i + 1} for i in range(VECTOR_M)]
    values = {"items": items}
    return codec, values


def run_scalars_pack(iterations):
    codec, values, _ = make_scalars_codec()
    for _ in range(iterations):
        codec.pack(values)


def run_scalars_unpack(iterations):
    codec, values, names = make_scalars_codec()
    wire = codec.pack(values)
    for _ in range(iterations):
        codec.unpack(wire)


def run_vector_pack(iterations):
    codec, values = make_vector_codec()
    for _ in range(iterations):
        codec.pack(values)


def run_vector_unpack(iterations):
    codec, values = make_vector_codec()
    wire = codec.pack(values)
    for _ in range(iterations):
        codec.unpack(wire)


MODES = {
    "scalars-pack": run_scalars_pack,
    "scalars-unpack": run_scalars_unpack,
    "vector-pack": run_vector_pack,
    "vector-unpack": run_vector_unpack,
}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "vector-unpack"
    iterations = int(sys.argv[2]) if len(sys.argv) > 2 else 2_000_000
    MODES[mode](iterations)
