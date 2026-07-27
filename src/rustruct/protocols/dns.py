"""DNS message (RFC 1035).

Domain-name compression doesn't fit rustruct's declarative model: a
pointer's target is an absolute offset into the whole message, and writing
one requires remembering every name suffix already emitted anywhere earlier
in the message -- state that outlives any single field or nested scope. So
this module is not one rustruct.Codec for the whole message: `DNSFlags`/
`A`/`AAAA` (no domain name involved) are real `rustruct.Struct` classes,
while everything that touches a domain name (`Question`, `ResourceRecord`,
and the NS/CNAME/SOA/PTR/MX/TXT record types) is a small hand-written class
operating on a shared `bytearray` + a `dict` context.

Domain names are plain strings (`"example.com"`; the root is `""`; a
trailing dot is accepted and dropped). Labels are transcoded byte-for-byte
as latin-1. Compression pointers are followed on read (backwards only, so
hostile messages cannot loop) and emitted on write whenever a name suffix
was already written; pack with `compress=False` to disable that.
"""

import enum
import struct as pystruct
from dataclasses import dataclass, field
from typing import Any, Protocol

from rustruct import Struct, bits

from .inet import ipv4_address_field, ipv6_address_field

__all__ = [
    "OpenIntEnum",
    "RRType",
    "DNSClass",
    "DNSFlags",
    "A",
    "AAAA",
    "NS",
    "CNAME",
    "PTR",
    "MX",
    "SOA",
    "TXT",
    "RDataKind",
    "UnknownRData",
    "Question",
    "ResourceRecord",
    "DNS",
]

MAX_NAME_WIRE = 255  # labels + length bytes + the final zero (RFC 1035 2.3.4)
MAX_LABEL = 63
POINTER_LIMIT = 0x3FFF  # compression offsets are 14 bits


class OpenIntEnum(enum.IntEnum):
    """An `IntEnum` that tolerates values with no named member: DNS wire
    rtype/qtype/qclass values aren't restricted to well-known ones, so
    decoding one must not raise -- it behaves as a plain int with no name.
    Unknown values are cached on `PSEUDO` so repeated decodes of the same
    value return the identical object."""

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, int) or not 0 <= value < 0x10000:
            return None
        cache = cls.__dict__.get("PSEUDO")
        if cache is None:
            cache = cls.PSEUDO = {}
        member = cache.get(value)
        if member is None:
            member = int.__new__(cls, value)
            member._name_ = f"{cls.__name__}_{value}"
            member._value_ = value
            cache[value] = member
        return member


class RRType(OpenIntEnum):
    A = 1
    NS = 2
    CNAME = 5
    SOA = 6
    PTR = 12
    MX = 15
    TXT = 16
    AAAA = 28
    SRV = 33
    OPT = 41
    ANY = 255


class DNSClass(OpenIntEnum):
    IN = 1
    CH = 3
    HS = 4
    ANY = 255


class DNSFlags(Struct, byteorder="big"):
    """The second header word: QR, opcode, AA/TC/RD/RA, Z and RCODE."""

    qr: bool = bits(1, default=False, help="query (False) or response (True)")
    opcode: int = bits(4, default=0, help="kind of query; 0 = standard query")
    aa: bool = bits(1, default=False, help="responder is authoritative")
    tc: bool = bits(1, default=False, help="message was truncated")
    rd: bool = bits(1, default=False, help="recursion desired")
    ra: bool = bits(1, default=False, help="recursion available")
    z: int = bits(3, default=0, help="reserved; must be zero")
    rcode: int = bits(4, default=0, help="response code; 0 = no error")


class A(Struct, byteorder="big"):
    """No domain name -- a plain 4-byte address, fully declarative."""

    RTYPE = RRType.A
    address: object = ipv4_address_field(help="the host's IPv4 address")


class AAAA(Struct, byteorder="big"):
    RTYPE = RRType.AAAA
    address: object = ipv6_address_field(help="the host's IPv6 address")


# ---------- domain names: the hand-written escape hatch ----------


def pointer_target(buf, off):
    if off + 1 >= len(buf):
        raise ValueError(f"cut compression pointer at offset {off}")
    target = ((buf[off] & 0x3F) << 8) | buf[off + 1]
    if target >= off:
        raise ValueError(f"compression pointer at offset {off} does not point backwards")
    return target


def read_label(buf, off, length):
    stop = off + 1 + length
    label = bytes(buf[off + 1 : stop])
    if len(label) != length:
        raise ValueError(f"cut label at offset {off}")
    return label.decode("latin-1"), stop


def decode_name(buf, start):
    """Returns (name, offset to resume the *enclosing* record at) -- the
    resume point is right after the first pointer followed, not wherever the
    pointer chain eventually bottoms out."""
    off = start
    resume = None
    labels = []
    wire_len = 1  # the terminating zero

    while True:
        if off >= len(buf):
            raise ValueError(f"domain name runs past the buffer at offset {off}")
        first = buf[off]
        if first == 0:
            off += 1
            break

        kind = first & 0xC0
        if kind == 0xC0:
            target = pointer_target(buf, off)
            if resume is None:
                resume = off + 2
            off = target
            continue
        if kind:
            raise ValueError(f"unsupported label type 0x{first:02X} at offset {off}")

        label, off = read_label(buf, off, first)
        wire_len += first + 1
        if wire_len > MAX_NAME_WIRE:
            raise ValueError("domain name longer than 255 wire bytes")
        labels.append(label)

    return ".".join(labels), (resume if resume is not None else off)


def encode_labels(value):
    stripped = value[:-1] if value.endswith(".") else value
    labels = [part.encode("latin-1") for part in stripped.split(".")] if stripped else []
    wire_len = 1
    for label in labels:
        if not 1 <= len(label) <= MAX_LABEL:
            raise ValueError(f"bad label length {len(label)} in {value!r}")
        wire_len += len(label) + 1
    if wire_len > MAX_NAME_WIRE:
        raise ValueError(f"domain name {value!r} longer than 255 wire bytes")
    return labels


def write_name(out, labels, ctx):
    """`ctx`, if not None, is the whole message's shared compression table
    (`{"dns_names": {label_suffix_tuple: offset}}`); pass None to disable
    compression on write (`DNS.pack(compress=False)`)."""
    offsets = ctx.setdefault("dns_names", {}) if ctx is not None else None
    for i, label in enumerate(labels):
        if offsets is not None:
            suffix = tuple(labels[i:])
            target = offsets.get(suffix)
            if target is not None:
                out += bytes((0xC0 | (target >> 8), target & 0xFF))
                return
            if len(out) <= POINTER_LIMIT:
                offsets[suffix] = len(out)
        out.append(len(label))
        out += label
    out.append(0)


# ---------- RDATA types that contain a domain name: hand-written ----------


@dataclass
class NS:
    RTYPE = RRType.NS
    target: str

    def pack_into(self, out, ctx):
        write_name(out, encode_labels(self.target), ctx)

    @classmethod
    def unpack(cls, buf, off, length, ctx):
        name, _ = decode_name(buf, off)
        return cls(name)


@dataclass
class CNAME:
    RTYPE = RRType.CNAME
    target: str

    def pack_into(self, out, ctx):
        write_name(out, encode_labels(self.target), ctx)

    @classmethod
    def unpack(cls, buf, off, length, ctx):
        name, _ = decode_name(buf, off)
        return cls(name)


@dataclass
class PTR:
    RTYPE = RRType.PTR
    target: str

    def pack_into(self, out, ctx):
        write_name(out, encode_labels(self.target), ctx)

    @classmethod
    def unpack(cls, buf, off, length, ctx):
        name, _ = decode_name(buf, off)
        return cls(name)


@dataclass
class MX:
    RTYPE = RRType.MX
    preference: int
    exchange: str

    def pack_into(self, out, ctx):
        out += pystruct.pack("!H", self.preference)
        write_name(out, encode_labels(self.exchange), ctx)

    @classmethod
    def unpack(cls, buf, off, length, ctx):
        (preference,) = pystruct.unpack_from("!H", buf, off)
        exchange, _ = decode_name(buf, off + 2)
        return cls(preference, exchange)


@dataclass
class SOA:
    RTYPE = RRType.SOA
    mname: str
    rname: str
    serial: int
    refresh: int
    retry: int
    expire: int
    minimum: int

    def pack_into(self, out, ctx):
        write_name(out, encode_labels(self.mname), ctx)
        write_name(out, encode_labels(self.rname), ctx)
        out += pystruct.pack("!IIIII", self.serial, self.refresh, self.retry, self.expire, self.minimum)

    @classmethod
    def unpack(cls, buf, off, length, ctx):
        mname, off = decode_name(buf, off)
        rname, off = decode_name(buf, off)
        serial, refresh, retry, expire, minimum = pystruct.unpack_from("!IIIII", buf, off)
        return cls(mname, rname, serial, refresh, retry, expire, minimum)


@dataclass
class TXT:
    """A sequence of length-prefixed opaque text chunks filling the record."""

    RTYPE = RRType.TXT
    strings: list = field(default_factory=list)

    def pack_into(self, out, ctx):
        for s in self.strings:
            out.append(len(s))
            out += s

    @classmethod
    def unpack(cls, buf, off, length, ctx):
        strings = []
        end = off + length
        pos = off
        while pos < end:
            n = buf[pos]
            pos += 1
            strings.append(bytes(buf[pos : pos + n]))
            pos += n
        return cls(strings)


@dataclass
class UnknownRData:
    """Raw RDATA of a record type the registry does not know."""

    rtype: RRType
    data: bytes

    def pack_into(self, out, ctx):
        out += self.data


class RawRDataClass(Protocol):
    """The non-`Struct` RDATA classes (NS/CNAME/PTR/MX/SOA/TXT): each needs
    the wire's byte offset/record length/name-compression ctx to decode
    (for compression-pointer resolution), unlike `Struct`'s own
    self-contained `unpack(buf)` -- so they implement this 4-arg classmethod
    instead of inheriting from `Struct`."""

    @classmethod
    def unpack(cls, buf: bytes, off: int, length: int, ctx: dict) -> Any: ...


class RDataKind(enum.Enum):
    """The RDATA type registry: an enum member per implemented record type,
    keyed by its wire `RRType`, so `RDataKind(rtype)` is itself the lookup
    (the usual `ValueError`-on-unknown-value enum contract, no hand-rolled
    dict needed). A wire rtype with no member here falls back to
    `UnknownRData`."""

    rdata_cls: "type[Struct] | type[RawRDataClass]"

    def __new__(cls, rtype, rdata_cls):
        obj = object.__new__(cls)
        obj._value_ = rtype
        obj.rdata_cls = rdata_cls
        return obj

    A = (RRType.A, A)
    AAAA = (RRType.AAAA, AAAA)
    NS = (RRType.NS, NS)
    CNAME = (RRType.CNAME, CNAME)
    PTR = (RRType.PTR, PTR)
    MX = (RRType.MX, MX)
    SOA = (RRType.SOA, SOA)
    TXT = (RRType.TXT, TXT)


def pack_rdata(data, out, ctx):
    if isinstance(data, Struct):
        out += data.pack()
    else:
        data.pack_into(out, ctx)


def unpack_rdata(rtype, buf, off, length, ctx):
    try:
        rdata_cls = RDataKind(rtype).rdata_cls
    except ValueError:
        return UnknownRData(RRType(rtype), bytes(buf[off : off + length]))
    if issubclass(rdata_cls, Struct):
        return rdata_cls.unpack(bytes(buf[off : off + length]))
    return rdata_cls.unpack(buf, off, length, ctx)


# ---------- Question / ResourceRecord / DNS: hand-written orchestration ----------


@dataclass
class Question:
    name: str
    qtype: RRType = RRType.A
    qclass: DNSClass = DNSClass.IN

    def pack_into(self, out, ctx):
        write_name(out, encode_labels(self.name), ctx)
        out += pystruct.pack("!HH", self.qtype, self.qclass)

    @classmethod
    def unpack(cls, buf, off, ctx):
        name, off = decode_name(buf, off)
        qtype, qclass = pystruct.unpack_from("!HH", buf, off)
        return cls(name, RRType(qtype), DNSClass(qclass)), off + 4


@dataclass
class ResourceRecord:
    """`rtype` is never stored here: a known record type carries its own
    class-level `RTYPE`, and `UnknownRData` already carries the wire tag it
    was decoded with -- storing a third, possibly-inconsistent copy on the
    record itself would just invite them drifting apart."""

    name: str
    rclass: DNSClass = DNSClass.IN
    ttl: int = 0
    data: object = None

    def pack_into(self, out, ctx):
        write_name(out, encode_labels(self.name), ctx)
        rtype = getattr(self.data, "RTYPE", None)
        if rtype is None:
            # No class-level RTYPE means this wasn't a registered rdata_cls,
            # so unpack_rdata() must have produced an UnknownRData instead --
            # the only other shape `data` can take (see the class docstring).
            assert isinstance(self.data, UnknownRData)
            rtype = self.data.rtype
        out += pystruct.pack("!HHI", rtype, self.rclass, self.ttl)
        rdlen_pos = len(out)
        out += b"\x00\x00"
        body_start = len(out)
        pack_rdata(self.data, out, ctx)
        rdlength = len(out) - body_start
        out[rdlen_pos : rdlen_pos + 2] = pystruct.pack("!H", rdlength)

    @classmethod
    def unpack(cls, buf, off, ctx):
        name, off = decode_name(buf, off)
        rtype, rclass, ttl, rdlength = pystruct.unpack_from("!HHIH", buf, off)
        off += 10
        data = unpack_rdata(rtype, buf, off, rdlength, ctx)
        off += rdlength
        return cls(name=name, rclass=DNSClass(rclass), ttl=ttl, data=data), off


@dataclass
class DNS:
    """A whole DNS message; the section counts are computed on pack()."""

    id: int = 0
    flags: DNSFlags = field(default_factory=DNSFlags)
    questions: list = field(default_factory=list)
    answers: list = field(default_factory=list)
    authorities: list = field(default_factory=list)
    additionals: list = field(default_factory=list)

    def pack(self, compress=True):
        out = bytearray()
        out += pystruct.pack("!H", self.id)
        out += self.flags.pack()
        out += pystruct.pack(
            "!HHHH",
            len(self.questions),
            len(self.answers),
            len(self.authorities),
            len(self.additionals),
        )
        ctx = {} if compress else None
        for q in self.questions:
            q.pack_into(out, ctx)
        for r in self.answers:
            r.pack_into(out, ctx)
        for r in self.authorities:
            r.pack_into(out, ctx)
        for r in self.additionals:
            r.pack_into(out, ctx)
        return bytes(out)

    @classmethod
    def unpack(cls, buf):
        id_, flags_raw, qdcount, ancount, nscount, arcount = pystruct.unpack_from("!HHHHHH", buf, 0)
        flags = DNSFlags.unpack(flags_raw.to_bytes(2, "big"))
        ctx = {}
        off = 12

        def read_n(n, reader):
            nonlocal off
            items = []
            for _ in range(n):
                item, off = reader(buf, off, ctx)
                items.append(item)
            return items

        questions = read_n(qdcount, Question.unpack)
        answers = read_n(ancount, ResourceRecord.unpack)
        authorities = read_n(nscount, ResourceRecord.unpack)
        additionals = read_n(arcount, ResourceRecord.unpack)
        return cls(id_, flags, questions, answers, authorities, additionals)
