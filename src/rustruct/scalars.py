"""Sentinel classes for fixed-width scalar wire types, used directly as
class-body annotations: `count: U16`. Being real classes (not instances)
means they can later be mixed into an enum wrapper, e.g.
`class IPProtocol(U8, OpenIntEnum): ...`.
"""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ScalarType:
    """Base of every scalar sentinel; `kind` is the rustruct.compile() kind
    string ("u8", "f32", "bool", ...)."""

    kind = None


class U8(ScalarType):
    kind = "u8"


class I8(ScalarType):
    kind = "i8"


class U16(ScalarType):
    kind = "u16"


class I16(ScalarType):
    kind = "i16"


class U32(ScalarType):
    kind = "u32"


class I32(ScalarType):
    kind = "i32"


class U64(ScalarType):
    kind = "u64"


class I64(ScalarType):
    kind = "i64"


class F32(ScalarType):
    kind = "f32"


class F64(ScalarType):
    kind = "f64"


class Bool(ScalarType):
    kind = "bool"


SCALARS_BY_KIND = MappingProxyType({t.kind: t for t in (U8, I8, U16, I16, U32, I32, U64, I64, F32, F64, Bool)})
