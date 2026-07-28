"""rustruct: the declarative Struct frontend over the Rust core, built
dynamically per size."""

import common

from rustruct import I32, U8, U16, U32, U64, Struct, array, raw, sized, slice, string
from rustruct.struct import get_codec


def compiled(struct_cls):
    """Return `struct_cls` with its codec already built.

    The frontend compiles lazily and caches per class, so timing the class
    statement alone would leave rustruct's compile out of the comparison
    entirely and report a cost nobody actually pays.
    """
    get_codec(struct_cls)
    return struct_cls


SUPPORTS = {"scalars", "vector", "telemetry"}


def build_scalars(n):
    names = [f"f{i}" for i in range(n)]
    namespace = {"__annotations__": {name: U16 for name in names}}
    namespace.update(dict.fromkeys(names, 0))  # defaults
    return compiled(type(f"Scalars{n}", (Struct,), namespace, byteorder="network"))


def make_scalars(n):
    names = [f"f{i}" for i in range(n)]
    struct_cls = build_scalars(n)
    obj = struct_cls(**dict(zip(names, common.scalars_values(n), strict=True)))

    def pack():
        return obj.pack()

    def unpack(data):
        decoded = struct_cls.unpack(data)
        return sum(getattr(decoded, name) for name in names)

    return pack, unpack


def build_vector(_m):
    class Item(Struct, byteorder="network"):
        a: U16
        b: U16

    class Vec(Struct, byteorder="network"):
        n: U16  # derived: refreshed from len(items) on pack
        items: list = array(elem=Item, count="n")

    return compiled(Vec), Item


def make_vector(m):
    Vec, Item = build_vector(m)
    obj = Vec(items=[Item(a=a, b=b) for a, b in common.vector_items(m)])

    def pack():
        return obj.pack()

    def unpack(data):
        decoded = Vec.unpack(data)
        return sum(it.a + it.b for it in decoded.items)

    return pack, unpack


def build_telemetry(_m):
    class Record(Struct, byteorder="network"):
        record_id: U32
        kind: U8
        status: U8
        code: U16
        x: I32
        y: I32
        reading: U64
        payload_len: U16
        payload: bytes = slice(len="payload_len")

    class Frame(Struct, byteorder="network"):
        version: U8
        flags: U8
        message_type: U16
        sequence: U32
        timestamp: U64
        session: bytes = raw(16)
        source_id: U32
        source_len: U8
        source: str = string(len="source_len")
        record_count: U16
        records: list = array(elem=Record, count="record_count")

    class Envelope(Struct, byteorder="network"):
        frame_size: U32
        frame: Frame = sized(Frame, size="frame_size")

    return compiled(Envelope), Frame, Record


def make_telemetry(m):
    Envelope, Frame, Record = build_telemetry(m)
    values = common.telemetry_values(m)
    records = [Record(**record) for record in values["records"]]
    frame_values = dict(values)
    frame_values["records"] = records
    obj = Envelope(frame=Frame(**frame_values))

    def pack():
        return obj.pack()

    def unpack(data):
        frame = Envelope.unpack(data).frame
        total = (
            frame.version
            + frame.flags
            + frame.message_type
            + frame.sequence
            + frame.timestamp
            + sum(frame.session)
            + frame.source_id
            + sum(frame.source.encode("utf-8"))
        )
        for record in frame.records:
            total += (
                record.record_id
                + record.kind
                + record.status
                + record.code
                + record.x
                + record.y
                + record.reading
                + sum(record.payload)
            )
        return total

    return pack, unpack
