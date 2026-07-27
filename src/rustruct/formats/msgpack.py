"""MessagePack (https://github.com/msgpack/msgpack/blob/master/spec.md).

MessagePack's tag byte packs the type discriminant and, for several ranges
(`fixmap`/`fixarray`/`fixstr`, positive/negative fixint), the value itself
into the same bits: `hi4` selects the range, `lo4` is either a sub-tag or
the count/value directly. `Value` decodes exactly one tag's worth of wire
content -- for a container, that's just the element count, not the
elements.

A MessagePack value is recursive to unbounded depth, which a single
rustruct schema cannot express (see docs/explanation/schema-model.md,
"What a schema cannot express"). `decode`/`encode` supply that recursion
in plain Python instead, walking an explicit stack rather than recursing,
so neither risks `RecursionError` regardless of nesting depth.

Deliberately out of scope: ext types (`ext8`/`16`/`32`, `fixext1`-`16`) and
`float32` on encode (`Value` always encodes floats as `float64`; both
widths decode on read).
"""

from rustruct import (
    F64,
    I8,
    I16,
    I32,
    I64,
    U8,
    U16,
    U32,
    U64,
    Struct,
    bits,
    raw,
    switch,
)
from rustruct import slice as mslice

__all__ = ["Value", "decode", "decode_from", "encode"]


class LenPrefixed8(Struct, byteorder="big"):
    """`n` bytes of `data`, `n` itself an explicit leading count -- the
    shared wire shape of bin8/16/32 and str8/16/32 (only the *meaning* of
    `data` differs, decided by which tag routed here)."""

    n: U8
    data: bytes = mslice(len="n")


class LenPrefixed16(Struct, byteorder="big"):
    n: U16
    data: bytes = mslice(len="n")


class LenPrefixed32(Struct, byteorder="big"):
    n: U32
    data: bytes = mslice(len="n")


# hi4 == 0xC (0xc0-0xcf), keyed by lo4. 0x1 (0xc1) is reserved/unused by the
# format itself and deliberately has no case: decoding it raises an ordinary
# "no matching case" InvalidDataError. 0x7-0x9 (ext8/16/32) are the
# deliberate ext omission described in the module docstring.
BLOCK_A_CASES = {
    0x0: raw(0),  # nil
    0x2: raw(0),  # false
    0x3: raw(0),  # true
    0x4: LenPrefixed8,  # bin8
    0x5: LenPrefixed16,  # bin16
    0x6: LenPrefixed32,  # bin32
    0xB: F64,  # float64 (float32, tag 0xa, is the other float-width omission)
    0xC: U8,  # uint8
    0xD: U16,  # uint16
    0xE: U32,  # uint32
    0xF: U64,  # uint64
}

# hi4 == 0xD (0xd0-0xdf), keyed by lo4. 0x4-0x8 (fixext1/2/4/8/16) are the
# deliberate ext omission.
BLOCK_B_CASES = {
    0x0: I8,  # int8
    0x1: I16,  # int16
    0x2: I32,  # int32
    0x3: I64,  # int64
    0x9: LenPrefixed8,  # str8
    0xA: LenPrefixed16,  # str16
    0xB: LenPrefixed32,  # str32
    0xC: U16,  # array16 -- just the element count; see the module docstring
    0xD: U32,  # array32
    0xE: U16,  # map16 -- element count, i.e. pair count
    0xF: U32,  # map32
}

# hi4 == 0-7: positive fixint (value = hi4*16 + lo4, nothing more to read).
# hi4 == 8: fixmap (lo4 pairs to follow). hi4 == 9: fixarray (lo4 elements).
# hi4 == 14/15: negative fixint (value = lo4-32 / lo4-16).
# None of these need anything beyond the tag byte itself.
TAG_CASES = {h: raw(0) for h in (*range(8), 8, 9, 14, 15)}
TAG_CASES[10] = mslice(len="lo4")  # fixstr, length 0-15
TAG_CASES[11] = mslice(len=lambda f: f.lo4 + 16)  # fixstr, length 16-31
TAG_CASES[12] = switch(on="lo4", cases=BLOCK_A_CASES)
TAG_CASES[13] = switch(on="lo4", cases=BLOCK_B_CASES)


class Value(Struct, byteorder="big"):
    """One MessagePack tag's worth of wire content -- a container's
    elements are not part of this schema (see the module docstring);
    `decode`/`encode` drive `Value.unpack_from`/`Value(...).pack()` in a
    loop to build/consume them."""

    hi4: int = bits(4)
    lo4: int = bits(4)
    payload: object = switch(on="hi4", cases=TAG_CASES)


def classify(mv):
    """("leaf", python_value) with the value already fully decoded, or
    ("array"/"map", element_count) for a container header."""
    h, lo4, p = mv.hi4, mv.lo4, mv.payload
    if h <= 7:
        return "leaf", h * 16 + lo4
    if h == 8:
        return "map", lo4
    if h == 9:
        return "array", lo4
    if h in (10, 11):
        return "leaf", p.decode("utf-8")
    if h == 14:
        return "leaf", lo4 - 32
    if h == 15:
        return "leaf", lo4 - 16
    if h == 12:
        if lo4 == 0x0:
            return "leaf", None
        if lo4 == 0x2:
            return "leaf", False
        if lo4 == 0x3:
            return "leaf", True
        if lo4 in (0x4, 0x5, 0x6):
            return "leaf", p.data
        if lo4 in (0xB, 0xC, 0xD, 0xE, 0xF):
            return "leaf", p
        raise ValueError(f"unsupported or reserved tag 0xc{lo4:x}")
    if h == 13:
        if lo4 in (0x0, 0x1, 0x2, 0x3):
            return "leaf", p
        if lo4 in (0x9, 0xA, 0xB):
            return "leaf", p.data.decode("utf-8")
        if lo4 in (0xC, 0xD):
            return "array", p
        if lo4 in (0xE, 0xF):
            return "map", p
        raise ValueError(f"unsupported ext tag 0xd{lo4:x}")
    raise AssertionError("hi4 is a 4-bit field; every value 0-15 is handled above")


class Frame:
    """One in-progress container on `decode_from`'s work stack."""

    __slots__ = ("kind", "need", "items")

    def __init__(self, kind, need):
        self.kind = kind
        self.need = need
        self.items = []


def finish_frame(frame):
    if frame.kind == "array":
        return frame.items
    return dict(zip(frame.items[0::2], frame.items[1::2], strict=True))


def decode_from(buf, offset=0):
    """Decode one MessagePack value starting at `offset`, returning
    `(value, new_offset)` -- the streaming-friendly building block `decode`
    itself is written in terms of. Iterative: a work stack of in-progress
    containers stands in for the call stack a recursive-descent decoder
    would use, so nesting depth is bounded only by available memory, not by
    Python's own recursion limit."""
    root = Frame("root", 1)
    stack = [root]
    while True:
        frame = stack[-1]
        if frame.need == 0:
            stack.pop()
            if frame.kind == "root":
                return frame.items[0], offset
            parent = stack[-1]
            parent.items.append(finish_frame(frame))
            parent.need -= 1
            continue
        mv, offset = Value.unpack_from(buf, offset)
        kind, extra = classify(mv)
        if kind == "leaf":
            frame.items.append(extra)
            frame.need -= 1
        else:
            frame_need = extra if kind == "array" else extra * 2
            stack.append(Frame(kind, frame_need))


def decode(buf):
    """Decode a buffer holding exactly one MessagePack value."""
    value, offset = decode_from(buf, 0)
    if offset != len(buf):
        raise ValueError(f"trailing data at offset {offset}")
    return value


def encode_int(v):
    if 0 <= v <= 127:
        return Value(hi4=v >> 4, lo4=v & 0xF, payload=b"").pack()
    if -32 <= v < 0:
        b = v & 0xFF
        return Value(hi4=b >> 4, lo4=b & 0xF, payload=b"").pack()
    if v > 127:
        for lo4, width in ((0xC, 0xFF), (0xD, 0xFFFF), (0xE, 0xFFFFFFFF), (0xF, (1 << 64) - 1)):
            if v <= width:
                return Value(hi4=12, lo4=lo4, payload=v).pack()
        raise ValueError(f"int too large for msgpack: {v}")
    for lo4, prim_min, prim_max in (
        (0x0, -128, 127),
        (0x1, -32768, 32767),
        (0x2, -(2**31), 2**31 - 1),
        (0x3, -(2**63), 2**63 - 1),
    ):
        if prim_min <= v <= prim_max:
            return Value(hi4=13, lo4=lo4, payload=v).pack()
    raise ValueError(f"int too small for msgpack: {v}")


def encode_bin(data):
    n = len(data)
    if n <= 0xFF:
        return Value(hi4=12, lo4=4, payload=LenPrefixed8(n=n, data=data)).pack()
    if n <= 0xFFFF:
        return Value(hi4=12, lo4=5, payload=LenPrefixed16(n=n, data=data)).pack()
    if n <= 0xFFFFFFFF:
        return Value(hi4=12, lo4=6, payload=LenPrefixed32(n=n, data=data)).pack()
    raise ValueError("bin too large for msgpack")


def encode_str(s):
    data = s.encode("utf-8")
    n = len(data)
    if n <= 15:
        return Value(hi4=10, lo4=n, payload=data).pack()
    if n <= 31:
        return Value(hi4=11, lo4=n - 16, payload=data).pack()
    if n <= 0xFF:
        return Value(hi4=13, lo4=9, payload=LenPrefixed8(n=n, data=data)).pack()
    if n <= 0xFFFF:
        return Value(hi4=13, lo4=0xA, payload=LenPrefixed16(n=n, data=data)).pack()
    if n <= 0xFFFFFFFF:
        return Value(hi4=13, lo4=0xB, payload=LenPrefixed32(n=n, data=data)).pack()
    raise ValueError("str too large for msgpack")


def encode_array_header(n):
    if n <= 15:
        return Value(hi4=9, lo4=n, payload=b"").pack()
    if n <= 0xFFFF:
        return Value(hi4=13, lo4=0xC, payload=n).pack()
    if n <= 0xFFFFFFFF:
        return Value(hi4=13, lo4=0xD, payload=n).pack()
    raise ValueError("array too large for msgpack")


def encode_map_header(n):
    if n <= 15:
        return Value(hi4=8, lo4=n, payload=b"").pack()
    if n <= 0xFFFF:
        return Value(hi4=13, lo4=0xE, payload=n).pack()
    if n <= 0xFFFFFFFF:
        return Value(hi4=13, lo4=0xF, payload=n).pack()
    raise ValueError("map too large for msgpack")


def encode(value):
    """Encode a Python value (`None`/`bool`/`int`/`float`/`bytes`/`str`/
    `list`/`dict`, with `dict` keys and values themselves encodable) as
    MessagePack, choosing the most compact tag that fits -- the same
    preference order the reference implementation uses, so the two
    interoperate byte-for-byte on the common cases (see
    `tests/formats/test_msgpack.py`).

    Iterative, like `decode_from`: a preorder walk over an explicit work
    stack, so encoding a value does not risk `RecursionError` regardless of
    how deeply it nests."""
    out = bytearray()
    stack = [value]
    while stack:
        v = stack.pop()
        if v is None:
            out += Value(hi4=12, lo4=0, payload=b"").pack()
        elif v is False:
            out += Value(hi4=12, lo4=2, payload=b"").pack()
        elif v is True:
            out += Value(hi4=12, lo4=3, payload=b"").pack()
        elif isinstance(v, int):
            out += encode_int(v)
        elif isinstance(v, float):
            out += Value(hi4=12, lo4=0xB, payload=v).pack()
        elif isinstance(v, (bytes, bytearray)):
            out += encode_bin(bytes(v))
        elif isinstance(v, str):
            out += encode_str(v)
        elif isinstance(v, list):
            out += encode_array_header(len(v))
            stack.extend(reversed(v))
        elif isinstance(v, dict):
            out += encode_map_header(len(v))
            for k, item in reversed(list(v.items())):
                stack.append(item)
                stack.append(k)
        else:
            raise TypeError(f"cannot encode {type(v)!r} as MessagePack")
    return bytes(out)
