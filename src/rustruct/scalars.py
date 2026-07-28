"""Sentinel classes for fixed-width scalar wire types, used directly as
class-body annotations: `count: U16`. Being real classes (not instances)
means they can later be mixed into an enum wrapper, e.g.
`class IPProtocol(U8, OpenIntEnum): ...`.
"""

from dataclasses import dataclass
from typing import ClassVar

from .vocab import Kind


@dataclass(frozen=True, slots=True)
class ScalarType:
    """Base of every scalar sentinel; `kind` is the rustruct.compile() kind
    this sentinel stands for.

    Annotated ClassVar so it stays a class attribute rather than becoming a
    dataclass field -- these types are used as sentinels, never instantiated."""

    kind: ClassVar[Kind]


class U8(ScalarType):
    kind = Kind.U8


class I8(ScalarType):
    kind = Kind.I8


class U16(ScalarType):
    kind = Kind.U16


class I16(ScalarType):
    kind = Kind.I16


class U32(ScalarType):
    kind = Kind.U32


class I32(ScalarType):
    kind = Kind.I32


class U64(ScalarType):
    kind = Kind.U64


class I64(ScalarType):
    kind = Kind.I64


class F32(ScalarType):
    kind = Kind.F32


class F64(ScalarType):
    kind = Kind.F64


class Bool(ScalarType):
    kind = Kind.BOOL
