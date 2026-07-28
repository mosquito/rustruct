"""Hand-written :mod:`struct` with an ergonomic mapping boundary.

The fixed-format operations remain the low-level baseline, but the timed path
also converts named mappings to positional values on pack and materializes
named mappings on unpack. Otherwise this implementation would benchmark a
specialized checksum parser rather than something an application can use.
These conversions emulate the adapter code users must otherwise write around
the positional :mod:`struct` API; competing schema libraries perform that work
inside their own public APIs.
"""

import struct

import common

SUPPORTS = {"scalars", "vector", "telemetry"}


def build_scalars(n):
    return struct.Struct("!" + "H" * n)


def make_scalars(n):
    st = build_scalars(n)
    names = [f"f{i}" for i in range(n)]
    values = dict(zip(names, common.scalars_values(n), strict=True))

    def pack():
        return st.pack(*(values[name] for name in names))

    def unpack(data):
        decoded = dict(zip(names, st.unpack(data), strict=True))
        return sum(decoded.values())

    return pack, unpack


def build_vector(_m):
    return struct.Struct("!HH"), struct.Struct("!H")


def make_vector(m):
    values = {
        "items": [{"a": a, "b": b} for a, b in common.vector_items(m)],
    }
    item, count = build_vector(m)

    def pack():
        records = values["items"]
        parts = [count.pack(len(records))]
        append = parts.append
        pk = item.pack
        for record in records:
            append(pk(record["a"], record["b"]))
        return b"".join(parts)

    def unpack(data):
        (n,) = count.unpack_from(data)
        off = 2
        records = []
        append = records.append
        up = item.unpack_from
        for _ in range(n):
            a, b = up(data, off)
            off += 4
            append({"a": a, "b": b})
        decoded = {"n": n, "items": records}
        return sum(record["a"] + record["b"] for record in decoded["items"])

    return pack, unpack


def build_telemetry(_m):
    return (
        struct.Struct("!BBHIQ16sIB"),
        struct.Struct("!I"),
        struct.Struct("!H"),
        struct.Struct("!IBBHiiQH"),
    )


def make_telemetry(m):
    values = common.telemetry_values(m)
    frame_fixed, frame_size, record_count, record_fixed = build_telemetry(m)

    def pack():
        source = values["source"].encode("utf-8")
        records = values["records"]
        parts = [
            frame_fixed.pack(
                values["version"],
                values["flags"],
                values["message_type"],
                values["sequence"],
                values["timestamp"],
                values["session"],
                values["source_id"],
                len(source),
            ),
            source,
            record_count.pack(len(records)),
        ]
        append = parts.append
        for record in records:
            payload = record["payload"]
            append(
                record_fixed.pack(
                    record["record_id"],
                    record["kind"],
                    record["status"],
                    record["code"],
                    record["x"],
                    record["y"],
                    record["reading"],
                    len(payload),
                )
            )
            append(payload)
        frame = b"".join(parts)
        return frame_size.pack(len(frame)) + frame

    def unpack(data):
        (size,) = frame_size.unpack_from(data, 0)
        end = frame_size.size + size
        (
            version,
            flags,
            message_type,
            sequence,
            timestamp,
            session,
            source_id,
            source_len,
        ) = frame_fixed.unpack_from(data, frame_size.size)
        pos = frame_size.size + frame_fixed.size
        decoded_source = data[pos : pos + source_len].decode("utf-8")
        pos += source_len
        (count,) = record_count.unpack_from(data, pos)
        pos += record_count.size
        records = []
        append = records.append
        for _ in range(count):
            record_id, kind, status, code, x, y, reading, payload_len = record_fixed.unpack_from(data, pos)
            pos += record_fixed.size
            payload = data[pos : pos + payload_len]
            pos += payload_len
            append(
                {
                    "record_id": record_id,
                    "kind": kind,
                    "status": status,
                    "code": code,
                    "x": x,
                    "y": y,
                    "reading": reading,
                    "payload_len": payload_len,
                    "payload": payload,
                }
            )
        if pos != end:
            raise ValueError(f"telemetry frame ended at {pos}, expected {end}")

        decoded = {
            "frame_size": size,
            "frame": {
                "version": version,
                "flags": flags,
                "message_type": message_type,
                "sequence": sequence,
                "timestamp": timestamp,
                "session": session,
                "source_id": source_id,
                "source_len": source_len,
                "source": decoded_source,
                "record_count": count,
                "records": records,
            },
        }
        frame = decoded["frame"]
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
