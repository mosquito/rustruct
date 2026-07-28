"""rustruct: a Rust core for parsing and building binary wire formats.

The low-level API is re-exported from the compiled ``rustruct.core`` module
(cdylib), built into the same wheel by the same maturin invocation -- there
is no scenario where they ship out of sync with each other.

The declarative ``Struct`` frontend is a plain Python layer on top of that:
a metaclass that lazily compiles a Codec per class and converts to/from
typed instances.
"""

from rustruct.core import Codec, Incomplete, compile
from rustruct.errors import (
    InvalidDataError,
    PackError,
    RustructError,
    SchemaError,
)
from rustruct.fields import (
    MISSING,
    Registry,
    array,
    bits,
    convert,
    cstring,
    described,
    digest,
    raw,
    registry,
    sized,
    slice,
    string,
    switch,
    when,
)
from rustruct.scalars import F32, F64, I8, I16, I32, I64, U8, U16, U32, U64, Bool
from rustruct.struct import Field, Struct
from rustruct.vocab import (
    REST_KEY,
    Algo,
    BinOp,
    ByteOrder,
    Encoding,
    ErrorKind,
    Kind,
    RestPolicy,
)

__all__ = [
    "Algo",
    "BinOp",
    "ByteOrder",
    "Codec",
    "Encoding",
    "ErrorKind",
    "Kind",
    "REST_KEY",
    "RestPolicy",
    "Field",
    "Incomplete",
    "InvalidDataError",
    "PackError",
    "RustructError",
    "SchemaError",
    "compile",
    "F32",
    "F64",
    "I8",
    "I16",
    "I32",
    "I64",
    "U8",
    "U16",
    "U32",
    "U64",
    "Bool",
    "MISSING",
    "Registry",
    "Struct",
    "array",
    "bits",
    "slice",
    "convert",
    "cstring",
    "described",
    "digest",
    "raw",
    "registry",
    "sized",
    "string",
    "switch",
    "when",
]
