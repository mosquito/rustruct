"""dataclasses-struct: a dataclass over a single ``struct`` format (fixed only)."""

import common
import dataclasses_struct as dcs

SUPPORTS = {"scalars"}


def build_scalars(n):
    plain = type(f"Scalars{n}", (), {"__annotations__": {f"f{i}": dcs.U16 for i in range(n)}})
    return dcs.dataclass_struct(size="std", byteorder="big")(plain)


def make_scalars(n):
    names = [f"f{i}" for i in range(n)]
    struct_cls = build_scalars(n)
    obj = struct_cls(*common.scalars_values(n))

    def pack():
        return obj.pack()

    def unpack(data):
        decoded = struct_cls.from_packed(data)
        return sum(getattr(decoded, name) for name in names)

    return pack, unpack
