"""stdlib :mod:`ctypes` big-endian Structure (fixed layouts only)."""

import ctypes

import common

SUPPORTS = {"scalars"}


def make_scalars(n):
    names = [f"f{i}" for i in range(n)]
    struct_cls = type(
        f"Scalars{n}",
        (ctypes.BigEndianStructure,),
        {"_pack_": 1, "_fields_": [(name, ctypes.c_uint16) for name in names]},
    )
    obj = struct_cls(*common.scalars_values(n))

    def pack():
        return bytes(obj)

    def unpack(data):
        decoded = struct_cls.from_buffer_copy(data)
        return sum(getattr(decoded, name) for name in names)

    return pack, unpack
