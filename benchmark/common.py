"""Parametric sample data: build the same wire bytes at any complexity.

The two small workloads are useful for isolating one scaling factor.  The
telemetry workload is intentionally closer to an application message: it has
an outer size window, a mixed-width header, fixed and dynamic byte strings,
UTF-8 text, and an array of nested records with per-record dynamic payloads.

* ``scalars(n)``  -- a fixed struct of ``n`` u16 fields (field-count scaling).
* ``vector(m)``   -- a u16 count followed by ``m`` (u16, u16) items (length scaling).
* ``telemetry(m)`` -- a framed message containing ``m`` non-trivial records.

Every implementation encodes exactly these bytes, so the runner can cross-check
correctness and the numbers are comparable field-for-field.
"""

import struct


def scalars_values(n: int) -> "list[int]":
    return [(i * 7 + 3) & 0xFFFF for i in range(n)]


def scalars_wire(n: int) -> bytes:
    return struct.pack("!" + "H" * n, *scalars_values(n))


def scalars_checksum(n: int) -> int:
    return sum(scalars_values(n))


def vector_items(m: int) -> "list[tuple[int, int]]":
    return [((i * 3 + 1) & 0xFFFF, (i * 5 + 2) & 0xFFFF) for i in range(m)]


def vector_wire(m: int) -> bytes:
    items = vector_items(m)
    return struct.pack("!H", m) + b"".join(struct.pack("!HH", a, b) for a, b in items)


def vector_checksum(m: int) -> int:
    return sum(a + b for a, b in vector_items(m))


TELEMETRY_SESSION = bytes.fromhex("00112233445566778899aabbccddeeff")
TELEMETRY_SOURCE = "edge-gateway-eu"
_FRAME_FIXED = struct.Struct("!BBHIQ16sIB")
_RECORD_COUNT = struct.Struct("!H")
_RECORD_FIXED = struct.Struct("!IBBHiiQH")
_FRAME_SIZE = struct.Struct("!I")


def telemetry_records(m: int) -> "list[dict[str, int | bytes]]":
    records = []
    for i in range(m):
        payload_len = 12 + i % 20
        payload = bytes((i * 13 + j * 17 + 5) & 0xFF for j in range(payload_len))
        records.append(
            {
                "record_id": 10_000 + i,
                "kind": (i * 3 + 1) & 0xFF,
                "status": (i * 5 + 2) & 0xFF,
                "code": (i * 97 + 11) & 0xFFFF,
                "x": -1_000_000 + i * 101,
                "y": 2_000_000 - i * 103,
                "reading": 1_000_000_000_000 + i * 1_000_003,
                "payload": payload,
            }
        )
    return records


def telemetry_values(m: int) -> dict:
    return {
        "version": 3,
        "flags": 0b1010_0101,
        "message_type": 0x1201,
        "sequence": 0x1020_3040,
        "timestamp": 1_750_000_000_123_456,
        "session": TELEMETRY_SESSION,
        "source_id": 0xA0B0_C0D0,
        "source": TELEMETRY_SOURCE,
        "records": telemetry_records(m),
    }


def telemetry_wire(m: int) -> bytes:
    values = telemetry_values(m)
    source = values["source"].encode("utf-8")
    parts = [
        _FRAME_FIXED.pack(
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
        _RECORD_COUNT.pack(m),
    ]
    for record in values["records"]:
        payload = record["payload"]
        parts.append(
            _RECORD_FIXED.pack(
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
        parts.append(payload)
    frame = b"".join(parts)
    return _FRAME_SIZE.pack(len(frame)) + frame


def telemetry_checksum(m: int) -> int:
    values = telemetry_values(m)
    total = (
        values["version"]
        + values["flags"]
        + values["message_type"]
        + values["sequence"]
        + values["timestamp"]
        + sum(values["session"])
        + values["source_id"]
        + sum(values["source"].encode("utf-8"))
    )
    for record in values["records"]:
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
