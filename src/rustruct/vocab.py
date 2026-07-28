"""Names for the closed sets the core recognises.

Every one of these was a bare string literal at some point in the pipeline,
and several -- the digest presets especially -- existed nowhere in Python at
all: the only way to learn that ``crc32c`` shipped was to read
``crates/rustruct/src/digest.rs``.

They are :class:`enum.StrEnum`, deliberately. A ``StrEnum`` member *is* a
``str``, so it crosses the FFI boundary unchanged, compares equal to the
spelling it replaces, and formats as that spelling: ``kind="u8"`` and
``kind=Kind.U8`` are the same call. ``class X(str, Enum)`` is *not* a
substitute -- it formats as ``X.U8`` in an f-string while still comparing
equal to ``"u8"``, so it would corrupt interpolated output while every
equality test kept passing.

Every value is written out rather than left to ``enum.auto()``, even where
``StrEnum`` would derive exactly the right one from the member name. The
wire spelling is the whole point of these classes, so it belongs next to
the name where it can be read -- ``CRC16_CCITT = "crc16_ccitt"`` says what
goes over the wire, ``CRC16_CCITT = auto()`` makes you know the rule to
find out. :class:`Encoding` could not use ``auto()`` anyway.

Members have to be spelled out in a class body, not built at runtime.
``ty`` reads a written-out member and types ``Kind.U8`` as
``Literal[Kind.U8]``; generate the same class from
:func:`rustruct.core.vocabulary` instead and every member degrades to
``Unknown``, so even ``Kind.NOSUCH`` stops being an error.

Neither the names nor the values are trusted to stay in step with the core
on their own: ``tests/test_vocabulary.py`` asserts each enum equals what
``vocabulary()`` publishes, so a typo on either side fails a test rather
than reaching a schema.

This module imports nothing from the rest of the package, so it can be used
from anywhere in it without an import cycle.
"""

import enum
from typing import Literal

__all__ = [
    "Algo",
    "BinOp",
    "ByteOrder",
    "Encoding",
    "ErrorKind",
    "Kind",
    "KindArg",
    "KindStr",
    "REST_KEY",
    "RestPolicy",
]


class Kind(enum.StrEnum):
    """Every field kind :func:`rustruct.compile` recognises.

    ``tests/test_vocabulary.py`` compiles a minimal schema for each member,
    so a kind that stops being accepted fails a test rather than drifting
    silently.
    """

    U8 = "u8"
    I8 = "i8"
    U16 = "u16"
    I16 = "i16"
    U32 = "u32"
    I32 = "i32"
    U64 = "u64"
    I64 = "i64"
    F32 = "f32"
    F64 = "f64"
    BOOL = "bool"
    RAW = "raw"
    BYTES = "bytes"
    STR = "str"
    CSTR = "cstr"
    BITS = "bits"
    FLAGS = "flags"
    STRUCT = "struct"
    ARRAY = "array"
    SWITCH = "switch"
    COND = "cond"
    DIGEST = "digest"


class ByteOrder(enum.StrEnum):
    """Wire byte order.

    ``NETWORK`` compiles to exactly what ``BIG`` does -- same program, same
    bytes -- and exists as its own spelling because it documents intent the
    way the ``struct`` module's ``!`` does. It is not an alias in the
    ``enum`` sense: it is a distinct member with the value ``"network"``,
    so ``ByteOrder.NETWORK == ByteOrder.BIG`` is false. Compare what a
    codec produces, not the member you passed.

    There is deliberately no ``NATIVE``: the core refuses it, since it
    would make the wire format depend on the machine doing the encoding.
    """

    BIG = "big"
    LITTLE = "little"
    NETWORK = "network"


class Algo(enum.StrEnum):
    """Digest algorithms :func:`rustruct.digest` can compute.

    The CRC members name Rocksoft models; pass ``poly=``/``init=``/
    ``xorout=``/``refin=``/``refout=`` to vary one. CRCs and ``IP`` produce
    an integer field; the hashes produce ``bytes`` of their natural width
    (16, 20 and 32 bytes respectively).
    """

    CRC16_CCITT = "crc16_ccitt"
    """CRC-16/IBM-3740, also known as CCITT-FALSE."""
    CRC16_IBM = "crc16_ibm"
    """CRC-16/ARC."""
    CRC32 = "crc32"
    CRC32C = "crc32c"
    """Castagnoli."""
    CRC64_XZ = "crc64_xz"
    IP = "ip"
    """RFC 1071 Internet checksum (IPv4 headers)."""
    MD5 = "md5"
    SHA1 = "sha1"
    SHA256 = "sha256"


class RestPolicy(enum.StrEnum):
    """What a ``flags`` field does with bits no name in it covers."""

    KEEP = "keep"
    """Report the leftover under :data:`REST_KEY`, and accept one back on pack."""
    STRICT = "strict"
    """Reject any set bit that no name covers."""
    IGNORE = "ignore"
    """Drop them on read, write zeros on pack."""


class BinOp(enum.StrEnum):
    """Operations an expression tuple's head can name.

    Ordinary Python operators on a field reference build these, so this is
    mostly of interest when writing expression tuples by hand.
    """

    ADD = "add"
    SUB = "sub"
    MUL = "mul"
    DIV = "div"
    SHL = "shl"
    SHR = "shr"
    AND = "and"
    OR = "or"
    XOR = "xor"
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"


class ErrorKind(enum.StrEnum):
    """The ``kind`` attribute of :class:`rustruct.InvalidDataError` and
    :class:`rustruct.PackError`.

    A closed set (``crates/rustruct/src/error.rs``, "closed list, v1") that
    until now reached Python as an unnamed string, so ``if exc.kind ==
    "truncated"`` was a typo away from a branch that could never be taken.
    Members compare equal to those strings, so existing comparisons keep
    working unchanged.
    """

    # unpack
    TRUNCATED = "truncated"
    """The buffer ended before the schema did."""
    TRAILING = "trailing"
    """Bytes left over after a full :meth:`~rustruct.Codec.unpack`."""
    RANGE = "range"
    NEGATIVE_LEN = "negative_len"
    OVERFLOW = "overflow"
    DIV_ZERO = "div_zero"
    UNTERMINATED = "unterminated"
    """A ``cstr`` ran to the end of its region without a NUL."""
    NUL_IN_CSTR = "nul_in_cstr"
    NO_CASE = "no_case"
    """A ``switch`` tag matched no case and there was no default."""
    DECODE = "decode"
    """Bytes that are not valid text in the field's encoding."""
    LIMIT = "limit"
    """A length or count exceeded its ``max``."""
    DEPTH = "depth"
    """Nesting exceeded the 64-frame limit.

    Not reachable from :func:`rustruct.compile`, which refuses a schema
    that deep rather than letting it fail on every input.
    """
    CHECKSUM = "checksum"
    CONST = "const"
    RESERVED_BITS = "reserved_bits"
    # pack
    MISSING = "missing"
    """No value supplied for a field that needs one."""
    LENGTH = "length"
    INDIVISIBLE = "indivisible"
    INCONSISTENT = "inconsistent"
    """A supplied value contradicts what the schema derives."""
    BUFFER = "buffer"
    """The destination buffer is too small or not writable."""
    UNKNOWN_FLAG = "unknown_flag"
    TYPE = "type"
    """A value of the wrong Python type for the field."""
    ENCODE = "encode"


REST_KEY = "_rest"
"""The key ``RestPolicy.KEEP`` reports uncovered flag bits under."""


class Encoding(enum.StrEnum):
    """Text encodings ``str``/``cstr`` fields can use.

    A much smaller set than Python's own codec registry -- the core
    implements exactly these three. The only set here whose wire spellings
    are not ``name.lower()``, and the only one that accepts aliases, so it
    stays a written-out class: ``"UTF-8"``, ``"utf8"`` and ``"utf_8"`` all
    resolve to :attr:`UTF8`, and ``"us-ascii"``/``"iso-8859-1"`` resolve to
    :attr:`ASCII`/:attr:`LATIN1`.
    """

    UTF8 = "utf-8"
    ASCII = "ascii"
    LATIN1 = "latin-1"

    @classmethod
    def _missing_(cls, value):
        # Mirrors parse_enc in crates/rustruct/src/compile.rs: lowercase,
        # drop "-" and "_", then match. Kept in step with it by
        # tests/test_vocabulary.py, which feeds every spelling here through
        # an actual compile().
        if not isinstance(value, str):
            return None
        match value.lower().replace("-", "").replace("_", ""):
            case "utf8":
                return cls.UTF8
            case "ascii" | "usascii":
                return cls.ASCII
            case "latin1" | "iso88591":
                return cls.LATIN1
        return None


KindStr = Literal[
    "u8", "i8", "u16", "i16", "u32", "i32", "u64", "i64", "f32", "f64", "bool",
    "raw", "bytes", "str", "cstr", "bits", "flags", "struct", "array", "switch", "cond", "digest",
]  # fmt: skip
"""The same closed set as :class:`Kind`, spelled as literal strings.

The one duplication in this module that cannot be derived away: a ``Literal``
built by unpacking is not a valid type form (:pep:`586`), and a checker that
cannot evaluate it falls back to accepting *anything* -- defeating the one
thing this alias exists for. It pays for itself: ``ty`` rejects
``Field("x", "bogus", {})`` because of it.
``tests/test_vocabulary.py`` asserts the two stay in step.
"""

KindArg = Kind | KindStr
"""What any kind-accepting position takes: a :class:`Kind` or its spelling.

Annotate accepting positions as this, never as bare ``Kind`` -- the low-level
``(name, kind, opts)`` form is documented as taking plain strings, and the
tests, docs and benchmark all pass them.
"""
