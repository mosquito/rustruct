"""dataclasses-struct: a dataclass over a single ``struct`` format (fixed only)."""

import common
import dataclasses_struct as dcs

SUPPORTS = {"scalars"}


def make_scalars(n):
    names = [f"f{i}" for i in range(n)]
    plain = type(f"Scalars{n}", (), {"__annotations__": {name: dcs.U16 for name in names}})
    struct_cls = dcs.dataclass_struct(size="std", byteorder="big")(plain)
    obj = struct_cls(*common.scalars_values(n))

    def pack():
        return obj.pack()

    def unpack(data):
        decoded = struct_cls.from_packed(data)
        return sum(getattr(decoded, name) for name in names)

    return pack, unpack
