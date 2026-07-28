"""construct: the declarative binary-parsing library rustruct most resembles."""

import common
import construct as C

SUPPORTS = {"scalars", "vector", "telemetry"}


def build_scalars(n):
    return C.Struct(*[f"f{i}" / C.Int16ub for i in range(n)])


def make_scalars(n):
    names = [f"f{i}" for i in range(n)]
    struct_def = build_scalars(n)
    values = dict(zip(names, common.scalars_values(n), strict=True))

    def pack():
        return struct_def.build(values)

    def unpack(data):
        o = struct_def.parse(data)
        return sum(o[name] for name in names)

    return pack, unpack


def build_vector(_m):
    item = C.Struct("a" / C.Int16ub, "b" / C.Int16ub)
    return C.Struct("count" / C.Int16ub, "records" / C.Array(C.this.count, item))


def make_vector(m):
    vec = build_vector(m)
    values = {"count": m, "records": [{"a": a, "b": b} for a, b in common.vector_items(m)]}

    def pack():
        return vec.build(values)

    def unpack(data):
        o = vec.parse(data)
        return sum(rec.a + rec.b for rec in o.records)

    return pack, unpack


def build_telemetry(_m):
    record = C.Struct(
        "record_id" / C.Int32ub,
        "kind" / C.Int8ub,
        "status" / C.Int8ub,
        "code" / C.Int16ub,
        "x" / C.Int32sb,
        "y" / C.Int32sb,
        "reading" / C.Int64ub,
        "payload" / C.Prefixed(C.Int16ub, C.GreedyBytes),
    )
    frame = C.Struct(
        "version" / C.Int8ub,
        "flags" / C.Int8ub,
        "message_type" / C.Int16ub,
        "sequence" / C.Int32ub,
        "timestamp" / C.Int64ub,
        "session" / C.Bytes(16),
        "source_id" / C.Int32ub,
        "source" / C.PascalString(C.Int8ub, "utf-8"),
        "records" / C.PrefixedArray(C.Int16ub, record),
    )
    envelope = C.Prefixed(C.Int32ub, frame)
    return envelope


def make_telemetry(m):
    envelope = build_telemetry(m)
    values = common.telemetry_values(m)

    def pack():
        return envelope.build(values)

    def unpack(data):
        decoded = envelope.parse(data)
        total = (
            decoded.version
            + decoded.flags
            + decoded.message_type
            + decoded.sequence
            + decoded.timestamp
            + sum(decoded.session)
            + decoded.source_id
            + sum(decoded.source.encode("utf-8"))
        )
        for record in decoded.records:
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
