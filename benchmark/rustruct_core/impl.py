"""rustruct-core: the raw compile()/Codec API, no Struct frontend at all --
isolates how much of rustruct's overhead is the frontend versus the core +
Python<->Rust FFI crossing itself."""

import common

import rustruct

SUPPORTS = {"scalars", "vector", "telemetry"}


def make_scalars(n):
    names = [f"f{i}" for i in range(n)]
    fields = tuple((name, "u16", {}) for name in names)
    codec = rustruct.compile(fields, byteorder="network")
    values = dict(zip(names, common.scalars_values(n), strict=True))

    def pack():
        return codec.pack(values)

    def unpack(data):
        decoded = codec.unpack(data)
        return sum(decoded[name] for name in names)

    return pack, unpack


def make_vector(m):
    item_fields = (("a", "u16", {}), ("b", "u16", {}))
    fields = (
        ("n", "u16", {}),
        (
            "items",
            "array",
            {"elem": ("struct", {"fields": item_fields}), "count": ("ref", "n")},
        ),
    )
    codec = rustruct.compile(fields, byteorder="network")
    items = [{"a": a, "b": b} for a, b in common.vector_items(m)]
    values = {"items": items}

    def pack():
        return codec.pack(values)

    def unpack(data):
        decoded = codec.unpack(data)
        return sum(it["a"] + it["b"] for it in decoded["items"])

    return pack, unpack


def make_telemetry(m):
    record_fields = (
        ("record_id", "u32", {}),
        ("kind", "u8", {}),
        ("status", "u8", {}),
        ("code", "u16", {}),
        ("x", "i32", {}),
        ("y", "i32", {}),
        ("reading", "u64", {}),
        ("payload_len", "u16", {}),
        ("payload", "bytes", {"len": ("ref", "payload_len")}),
    )
    frame_fields = (
        ("version", "u8", {}),
        ("flags", "u8", {}),
        ("message_type", "u16", {}),
        ("sequence", "u32", {}),
        ("timestamp", "u64", {}),
        ("session", "raw", {"len": 16}),
        ("source_id", "u32", {}),
        ("source_len", "u8", {}),
        ("source", "str", {"len": ("ref", "source_len"), "encoding": "utf-8"}),
        ("record_count", "u16", {}),
        (
            "records",
            "array",
            {"elem": ("struct", {"fields": record_fields}), "count": ("ref", "record_count")},
        ),
    )
    fields = (
        ("frame_size", "u32", {}),
        ("frame", "struct", {"fields": frame_fields, "size": ("ref", "frame_size")}),
    )
    codec = rustruct.compile(fields, byteorder="network")
    values = {"frame": common.telemetry_values(m)}

    def pack():
        return codec.pack(values)

    def unpack(data):
        frame = codec.unpack(data)["frame"]
        total = (
            frame["version"]
            + frame["flags"]
            + frame["message_type"]
            + frame["sequence"]
            + frame["timestamp"]
            + sum(frame["session"])
            + frame["source_id"]
            + sum(frame["source"].encode("utf-8"))
        )
        for record in frame["records"]:
            total += (
                record["record_id"]
                + record["kind"]
                + record["status"]
                + record["code"]
                + record["x"]
                + record["y"]
                + record["reading"]
                + sum(record["payload"])
            )
        return total

    return pack, unpack
