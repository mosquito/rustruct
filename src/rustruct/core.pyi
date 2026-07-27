from collections.abc import Mapping
from typing import Any, Literal, NamedTuple, Protocol

class Buffer(Protocol):
    """`collections.abc.Buffer` is 3.12+ only; this project's floor is 3.11
    (see requires-python), so a locally-defined structural match for the
    same PEP 688 protocol stands in for it here."""

    def __buffer__(self, flags: int, /) -> memoryview: ...

__abi__: int

# Every kind string parse_type (crates/rustruct-py/src/lib.rs) recognizes --
# a closed set, mirrored in rustruct.struct's own Kind.
Kind = Literal[
    "u8",
    "i8",
    "u16",
    "i16",
    "u32",
    "i32",
    "u64",
    "i64",
    "f32",
    "f64",
    "bool",
    "raw",
    "bytes",
    "str",
    "cstr",
    "bits",
    "flags",
    "struct",
    "array",
    "switch",
    "cond",
    "digest",
]

class Field(NamedTuple):
    """(name, kind, opts) -- a NamedTuple is still a plain `tuple` on the
    Rust side (pyo3 downcasts structurally), so struct.py's own Field
    (or any equivalent 3-tuple) satisfies this positionally."""

    name: str | None
    kind: Kind
    opts: dict[str, Any]

class RustructError(Exception): ...
class SchemaError(RustructError): ...

class InvalidDataError(RustructError):
    path: str
    offset: int
    kind: str

class PackError(RustructError):
    path: str
    kind: str

class Incomplete:
    needed: int
    def __bool__(self) -> bool: ...

class Codec:
    def unpack(self, buf: Buffer, /) -> dict[str, Any]: ...
    def unpack_from(self, buf: Buffer, offset: int = 0, /) -> tuple[dict[str, Any], int]: ...
    def parse(self, buf: Buffer, offset: int = 0, /) -> tuple[dict[str, Any], int] | Incomplete: ...
    def pack(self, values: Mapping[str, Any], /) -> bytes: ...
    def pack_into(self, buf: Buffer, offset: int, values: Mapping[str, Any], /) -> int: ...
    @property
    def min_size(self) -> int: ...
    @property
    def static_size(self) -> int | None: ...
    def to_bytes(self) -> bytes: ...
    @staticmethod
    def from_bytes(data: bytes, /) -> Codec: ...

def compile(
    fields: tuple[Field, ...],
    *,
    byteorder: str = "big",
    max_default: int = 67_108_864,
    max_count: int = 16_777_216,
) -> Codec: ...
