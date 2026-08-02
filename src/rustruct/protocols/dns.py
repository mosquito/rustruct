"""DNS message (RFC 1035, plus common extensions).

Domain-name compression is the one thing here that doesn't fit rustruct's
declarative model: a pointer's target is an absolute offset into the whole
message, and writing one requires remembering every name suffix already
emitted anywhere earlier in the message -- state that outlives any single
field or nested scope. So the record types that carry a domain name
(`Question`, `ResourceRecord`, and the NS/CNAME/DNAME/SOA/PTR/MX/SRV/NAPTR/
RRSIG/NSEC/RP/HTTPS record types) are small hand-written classes operating
on a shared `bytearray` + a `dict` context, calling `write_name`/
`decode_name` for each name and `encode_labels` to prepare one for the
wire.

Everything else about those same record types -- every fixed-width field
or run of them, both before and after the domain name(s) -- is still a
real, nested `rustruct.Struct` (`SOAFixed`, `SRVFixed`, `RRSIGFixed`, and
so on): there is no hand-rolled `struct.pack()` format string anywhere in
this module, right up through the 12-byte message header itself
(`MessageHeader`, nesting `DNSFlags`). Record types with no domain name at
all (`A`/`AAAA`/`DS`/`DNSKEY`/`TLSA`/`SSHFP`/`CAA`/`LOC`) need no escape
hatch whatsoever and are plain top-level `Struct` classes.

Domain names are plain strings (`"example.com"`; the root is `""`; a
trailing dot is accepted and dropped). Labels are transcoded byte-for-byte
as latin-1. Compression pointers are followed on read (backwards only, so
hostile messages cannot loop) and emitted on write whenever a name suffix
was already written; pack with `compress=False` to disable that.

A handful of record types carry a domain name that RFC 4034/2782 forbid
compressing (RRSIG's signer, NSEC's next-domain, SRV's/HTTPS's target):
those call `write_name(out, labels, None)` regardless of the message's own
`compress=` setting, matching what real resolvers expect on the wire.
"""

import enum
from dataclasses import dataclass, field
from typing import Any, Protocol

from rustruct import U8, U16, U32, Struct, bits, convert, described, slice, string

from .inet import ipv4_address_field, ipv6_address_field

__all__ = [
    "OpenIntEnum",
    "RRType",
    "RCODE",
    "DNSClass",
    "DNSFlags",
    "A",
    "AAAA",
    "NS",
    "CNAME",
    "DNAME",
    "PTR",
    "MX",
    "SOA",
    "SRV",
    "NAPTR",
    "DS",
    "DNSKEY",
    "RRSIG",
    "NSEC",
    "CAA",
    "HTTPS",
    "LOC",
    "RP",
    "TLSA",
    "SSHFP",
    "TXT",
    "RDataKind",
    "UnknownRData",
    "Question",
    "ResourceRecord",
    "DNS",
    "reply",
    "edns0",
    "edns_udp_size",
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
    def _missing_(cls, value: object) -> "OpenIntEnum | None":
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
    RP = 17
    MX = 15
    TXT = 16
    AAAA = 28
    LOC = 29
    SRV = 33
    NAPTR = 35
    DNAME = 39
    OPT = 41
    DS = 43
    SSHFP = 44
    RRSIG = 46
    NSEC = 47
    DNSKEY = 48
    TLSA = 52
    HTTPS = 65
    CAA = 257
    ANY = 255


class DNSClass(OpenIntEnum):
    IN = 1
    CH = 3
    HS = 4
    ANY = 255


class RCODE(OpenIntEnum):
    """RFC 1035 section 4.1.1 plus the extensions RFC 2136/2671 added; the
    header's own `rcode` field is only 4 bits wide (0-15), the rest need an
    EDNS0 extended-RCODE to appear on the wire at all."""

    NOERROR = 0
    FORMERR = 1
    SERVFAIL = 2
    NXDOMAIN = 3
    NOTIMP = 4
    REFUSED = 5
    YXDOMAIN = 6
    YXRRSET = 7
    NXRRSET = 8
    NOTAUTH = 9
    NOTZONE = 10


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


class DS(Struct, byteorder="big"):
    """Delegation Signer (RFC 4034 section 5); no domain name involved."""

    RTYPE = RRType.DS
    key_tag: U16
    algorithm: U8
    digest_type: U8
    digest: bytes = slice(len="*")


class DNSKEY(Struct, byteorder="big"):
    """RFC 4034 section 2; no domain name involved."""

    RTYPE = RRType.DNSKEY
    flags: U16
    protocol: U8
    algorithm: U8
    key: bytes = slice(len="*")


class TLSA(Struct, byteorder="big"):
    """RFC 6698; no domain name involved."""

    RTYPE = RRType.TLSA
    usage: U8
    selector: U8
    matching_type: U8
    cert_data: bytes = slice(len="*")


class SSHFP(Struct, byteorder="big"):
    """RFC 4255; no domain name involved."""

    RTYPE = RRType.SSHFP
    algorithm: U8
    fp_type: U8
    fingerprint: bytes = slice(len="*")


class CAA(Struct, byteorder="big"):
    """RFC 6844; no domain name involved. `tag_length` is derived from
    `len(tag)` on pack, like a length field -- the caller never supplies it
    (see `described()`'s docstring)."""

    RTYPE = RRType.CAA
    flags: U8 = described(default=0, help="critical flag; bit 0 set means non-issuer-critical")
    tag_length: U8 = described(help="derived from len(tag) on pack")
    tag: str = string(len="tag_length", encoding="ascii")
    value: str = string(len="*", encoding="utf-8")


def loc_precision_to_wire(value: float) -> int:
    """RFC 1876's SIZE/HORIZ_PRE/VERT_PRE nibble-exponent encoding: value ==
    0 packs as the single byte 0, otherwise `(mantissa << 4) | exponent`
    with `mantissa * 10**exponent == round(value * 100)` and mantissa a
    single digit 0-9."""
    if value == 0:
        return 0
    exponent = 0
    scaled = value * 100
    while scaled >= 10 and exponent < 9:
        scaled /= 10
        exponent += 1
    mantissa = int(round(scaled))
    if mantissa >= 10:
        raise ValueError(f"LOC precision value out of range: {value!r}")
    return (mantissa << 4) | exponent


def loc_precision_from_wire(value: int) -> float:
    mantissa, exponent = value >> 4, value & 0x0F
    return mantissa * (10**exponent) / 100


def loc_coord_to_wire(degrees: float) -> int:
    return int(round(degrees * 3600000)) + (1 << 31)


def loc_coord_from_wire(value: int) -> float:
    return (value - (1 << 31)) / 3600000


def loc_altitude_to_wire(meters: float) -> int:
    return int(round((meters + 100000) * 100))


def loc_altitude_from_wire(value: int) -> float:
    return value / 100 - 100000


def loc_precision_field(**kwargs: Any) -> Any:
    return convert(U8, decode=loc_precision_from_wire, encode=loc_precision_to_wire, **kwargs)


class LOC(Struct, byteorder="big"):
    """RFC 1876; no domain name involved. Latitude/longitude are degrees
    (negative is south/west), altitude is meters above the WGS 84 reference
    ellipsoid, and size/*_precision are meters -- all four wire fixed-point
    encodings (see the `loc_*` helpers above) round-trip through plain
    Python floats via `convert()`."""

    RTYPE = RRType.LOC
    version: U8 = described(default=0, help="always 0 (RFC 1876 has no other version)")
    size: float = loc_precision_field(default=1.0, help="diameter of the enclosing sphere, in meters")
    h_precision: float = loc_precision_field(default=10000.0, help="horizontal precision, in meters")
    v_precision: float = loc_precision_field(default=10.0, help="vertical precision, in meters")
    latitude: float = convert(U32, decode=loc_coord_from_wire, encode=loc_coord_to_wire, help="degrees north")
    longitude: float = convert(U32, decode=loc_coord_from_wire, encode=loc_coord_to_wire, help="degrees east")
    altitude: float = convert(U32, decode=loc_altitude_from_wire, encode=loc_altitude_to_wire)


# ---------- domain names: the hand-written escape hatch ----------


def pointer_target(buf: bytes | bytearray, off: int) -> int:
    if off + 1 >= len(buf):
        raise ValueError(f"cut compression pointer at offset {off}")
    target = ((buf[off] & 0x3F) << 8) | buf[off + 1]
    if target >= off:
        raise ValueError(f"compression pointer at offset {off} does not point backwards")
    return target


def read_label(buf: bytes | bytearray, off: int, length: int) -> tuple[str, int]:
    stop = off + 1 + length
    label = bytes(buf[off + 1 : stop])
    if len(label) != length:
        raise ValueError(f"cut label at offset {off}")
    return label.decode("latin-1"), stop


def decode_name(buf: bytes | bytearray, start: int) -> tuple[str, int]:
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


def encode_labels(value: str) -> list[bytes]:
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


def write_name(out: bytearray, labels: list[bytes], ctx: dict[str, Any] | None) -> None:
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
class SingleName:
    """Common shape shared by NS/CNAME/PTR/DNAME (RFC 1035/6672): a
    compressible domain name and nothing else. Concrete subclasses add
    only their own `RTYPE`; `pack_into`/`unpack` and equality/repr are
    inherited as-is, with `cls(...)` in `unpack` dispatching to whichever
    subclass it's called on."""

    target: str

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        write_name(out, encode_labels(self.target), ctx)

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, length: int, ctx: dict[str, Any]) -> "SingleName":
        name, _ = decode_name(buf, off)
        return cls(name)


class NS(SingleName):
    RTYPE = RRType.NS


class CNAME(SingleName):
    RTYPE = RRType.CNAME


class PTR(SingleName):
    RTYPE = RRType.PTR


class DNAME(SingleName):
    """RFC 6672; a name-compressible domain name, same as CNAME/NS/PTR."""

    RTYPE = RRType.DNAME


class MXPrefix(Struct, byteorder="big"):
    """MX's fixed-width part, ahead of the `exchange` domain name."""

    preference: U16


@dataclass
class MX:
    RTYPE = RRType.MX
    preference: int
    exchange: str

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        out += MXPrefix(preference=self.preference).pack()
        write_name(out, encode_labels(self.exchange), ctx)

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, length: int, ctx: dict[str, Any]) -> "MX":
        prefix, pos = MXPrefix.unpack_from(buf, off)
        exchange, _ = decode_name(buf, pos)
        return cls(prefix.preference, exchange)


class SOAFixed(Struct, byteorder="big"):
    """SOA's five fixed-width fields, after `mname`/`rname`."""

    serial: U32
    refresh: U32
    retry: U32
    expire: U32
    minimum: U32


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

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        write_name(out, encode_labels(self.mname), ctx)
        write_name(out, encode_labels(self.rname), ctx)
        out += SOAFixed(
            serial=self.serial, refresh=self.refresh, retry=self.retry, expire=self.expire, minimum=self.minimum
        ).pack()

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, length: int, ctx: dict[str, Any]) -> "SOA":
        mname, off = decode_name(buf, off)
        rname, off = decode_name(buf, off)
        fixed, _ = SOAFixed.unpack_from(buf, off)
        return cls(mname, rname, fixed.serial, fixed.refresh, fixed.retry, fixed.expire, fixed.minimum)


@dataclass
class TXT:
    """A sequence of length-prefixed opaque text chunks filling the record."""

    RTYPE = RRType.TXT
    strings: list[bytes] = field(default_factory=list)

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        for s in self.strings:
            out.append(len(s))
            out += s

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, length: int, ctx: dict[str, Any]) -> "TXT":
        strings = []
        end = off + length
        pos = off
        while pos < end:
            n = buf[pos]
            pos += 1
            strings.append(bytes(buf[pos : pos + n]))
            pos += n
        return cls(strings)


class SRVFixed(Struct, byteorder="big"):
    """SRV's three fixed-width fields, ahead of `target`."""

    priority: U16
    weight: U16
    port: U16


@dataclass
class SRV:
    """RFC 2782; `target` must not be compressed."""

    RTYPE = RRType.SRV
    priority: int
    weight: int
    port: int
    target: str

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        out += SRVFixed(priority=self.priority, weight=self.weight, port=self.port).pack()
        write_name(out, encode_labels(self.target), None)

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, length: int, ctx: dict[str, Any]) -> "SRV":
        fixed, pos = SRVFixed.unpack_from(buf, off)
        target, _ = decode_name(buf, pos)
        return cls(fixed.priority, fixed.weight, fixed.port, target)


def read_char_string(buf: bytes | bytearray, off: int) -> tuple[str, int]:
    """A DNS "character-string" (RFC 1035 section 3.3): one length octet
    followed by that many bytes, decoded as ASCII. Returns (text, offset to
    resume at)."""
    n = buf[off]
    stop = off + 1 + n
    return bytes(buf[off + 1 : stop]).decode("ascii"), stop


def write_char_string(out: bytearray, value: str) -> None:
    encoded = value.encode("ascii")
    out.append(len(encoded))
    out += encoded


class NAPTRPrefix(Struct, byteorder="big"):
    """NAPTR's two fixed-width fields, ahead of its character-strings."""

    order: U16
    preference: U16


@dataclass
class NAPTR:
    """RFC 3403; `replacement` is name-compressible, same as CNAME/NS/PTR."""

    RTYPE = RRType.NAPTR
    order: int
    preference: int
    flags: str
    service: str
    regexp: str
    replacement: str

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        out += NAPTRPrefix(order=self.order, preference=self.preference).pack()
        write_char_string(out, self.flags)
        write_char_string(out, self.service)
        write_char_string(out, self.regexp)
        write_name(out, encode_labels(self.replacement), ctx)

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, length: int, ctx: dict[str, Any]) -> "NAPTR":
        prefix, pos = NAPTRPrefix.unpack_from(buf, off)
        flags, pos = read_char_string(buf, pos)
        service, pos = read_char_string(buf, pos)
        regexp, pos = read_char_string(buf, pos)
        replacement, _ = decode_name(buf, pos)
        return cls(prefix.order, prefix.preference, flags, service, regexp, replacement)


class RRSIGFixed(Struct, byteorder="big"):
    """RRSIG's seven fixed-width fields, ahead of `signer`."""

    type_covered: U16
    algorithm: U8
    labels: U8
    original_ttl: U32
    expiration: U32
    inception: U32
    key_tag: U16


@dataclass
class RRSIG:
    """RFC 4034 section 3; `signer` must not be compressed."""

    RTYPE = RRType.RRSIG
    type_covered: int
    algorithm: int
    labels: int
    original_ttl: int
    expiration: int
    inception: int
    key_tag: int
    signer: str
    signature: bytes

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        out += RRSIGFixed(
            type_covered=self.type_covered,
            algorithm=self.algorithm,
            labels=self.labels,
            original_ttl=self.original_ttl,
            expiration=self.expiration,
            inception=self.inception,
            key_tag=self.key_tag,
        ).pack()
        write_name(out, encode_labels(self.signer), None)
        out += self.signature

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, length: int, ctx: dict[str, Any]) -> "RRSIG":
        end = off + length
        fixed, pos = RRSIGFixed.unpack_from(buf, off)
        signer, pos = decode_name(buf, pos)
        signature = bytes(buf[pos:end])
        return cls(
            fixed.type_covered,
            fixed.algorithm,
            fixed.labels,
            fixed.original_ttl,
            fixed.expiration,
            fixed.inception,
            fixed.key_tag,
            signer,
            signature,
        )


def encode_type_bitmap(rrtypes: list[int]) -> bytes:
    """RFC 4034 section 4.1.2: types are grouped into 256-wide windows, each
    written as (window number, bitmap length in bytes, bitmap) with trailing
    all-zero bytes dropped from that window's bitmap."""
    windows: dict[int, bytearray] = {}
    for rrtype in rrtypes:
        window, bit = divmod(int(rrtype), 256)
        windows.setdefault(window, bytearray(32))[bit // 8] |= 0x80 >> (bit % 8)
    out = bytearray()
    for window in sorted(windows):
        bitmap = windows[window]
        length = max(i for i, b in enumerate(bitmap) if b) + 1
        out.append(window)
        out.append(length)
        out += bitmap[:length]
    return bytes(out)


def decode_type_bitmap(buf: bytes | bytearray, off: int, end: int) -> list[int]:
    rrtypes = []
    pos = off
    while pos < end:
        window = buf[pos]
        length = buf[pos + 1]
        pos += 2
        for i, byte in enumerate(buf[pos : pos + length]):
            for bit in range(8):
                if byte & (0x80 >> bit):
                    rrtypes.append(window * 256 + i * 8 + bit)
        pos += length
    return rrtypes


@dataclass
class NSEC:
    """RFC 4034 section 4; `next_domain` must not be compressed."""

    RTYPE = RRType.NSEC
    next_domain: str
    rrtypes: list[int] = field(default_factory=list)

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        write_name(out, encode_labels(self.next_domain), None)
        out += encode_type_bitmap(self.rrtypes)

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, length: int, ctx: dict[str, Any]) -> "NSEC":
        end = off + length
        next_domain, pos = decode_name(buf, off)
        return cls(next_domain, decode_type_bitmap(buf, pos, end))


@dataclass
class RP:
    """RFC 1183 section 2.2; both names are name-compressible, same as
    CNAME/NS/PTR."""

    RTYPE = RRType.RP
    mbox: str
    txt: str

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        write_name(out, encode_labels(self.mbox), ctx)
        write_name(out, encode_labels(self.txt), ctx)

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, length: int, ctx: dict[str, Any]) -> "RP":
        mbox, pos = decode_name(buf, off)
        txt, _ = decode_name(buf, pos)
        return cls(mbox, txt)


class HTTPSPrefix(Struct, byteorder="big"):
    """HTTPS's one fixed-width field, ahead of `target`."""

    priority: U16


class SvcParamHeader(Struct, byteorder="big"):
    """One SvcParam's (SvcParamKey, SvcParamValue length) pair, ahead of
    that many bytes of raw value."""

    key: U16
    length: U16


@dataclass
class HTTPS:
    """RFC 9460; `target` (the SVCB TargetName) must not be compressed.
    `params` is the raw (SvcParamKey, SvcParamValue) list -- key-specific
    formatting (alpn/ipv4hint/echconfig/...) is a presentation-format
    concern this module does not implement, same scope as the rest of this
    hand-written RDATA layer."""

    RTYPE = RRType.HTTPS
    priority: int
    target: str
    params: list[tuple[int, bytes]] = field(default_factory=list)

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        out += HTTPSPrefix(priority=self.priority).pack()
        write_name(out, encode_labels(self.target), None)
        for key, value in self.params:
            out += SvcParamHeader(key=key, length=len(value)).pack()
            out += value

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, length: int, ctx: dict[str, Any]) -> "HTTPS":
        end = off + length
        prefix, pos = HTTPSPrefix.unpack_from(buf, off)
        target, pos = decode_name(buf, pos)
        params = []
        while pos < end:
            header, pos = SvcParamHeader.unpack_from(buf, pos)
            params.append((header.key, bytes(buf[pos : pos + header.length])))
            pos += header.length
        return cls(prefix.priority, target, params)


@dataclass
class UnknownRData:
    """Raw RDATA of a record type the registry does not know."""

    rtype: RRType
    data: bytes

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        out += self.data


class RawRDataClass(Protocol):
    """The non-`Struct` RDATA classes (NS/CNAME/PTR/MX/SOA/TXT): each needs
    the wire's byte offset/record length/name-compression ctx to decode
    (for compression-pointer resolution), unlike `Struct`'s own
    self-contained `unpack(buf)` -- so they implement this 4-arg classmethod
    instead of inheriting from `Struct`."""

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, length: int, ctx: dict[str, Any]) -> Any: ...


class RDataKind(enum.Enum):
    """The RDATA type registry: an enum member per implemented record type,
    keyed by its wire `RRType`, so `RDataKind(rtype)` is itself the lookup
    (the usual `ValueError`-on-unknown-value enum contract, no hand-rolled
    dict needed). A wire rtype with no member here falls back to
    `UnknownRData`."""

    rdata_cls: "type[Struct] | type[RawRDataClass]"

    def __new__(cls, rtype: int, rdata_cls: "type[Struct] | type[RawRDataClass]") -> "RDataKind":
        obj = object.__new__(cls)
        obj._value_ = rtype
        obj.rdata_cls = rdata_cls
        return obj

    A = (RRType.A, A)
    AAAA = (RRType.AAAA, AAAA)
    NS = (RRType.NS, NS)
    CNAME = (RRType.CNAME, CNAME)
    DNAME = (RRType.DNAME, DNAME)
    PTR = (RRType.PTR, PTR)
    MX = (RRType.MX, MX)
    SOA = (RRType.SOA, SOA)
    SRV = (RRType.SRV, SRV)
    NAPTR = (RRType.NAPTR, NAPTR)
    DS = (RRType.DS, DS)
    DNSKEY = (RRType.DNSKEY, DNSKEY)
    RRSIG = (RRType.RRSIG, RRSIG)
    NSEC = (RRType.NSEC, NSEC)
    CAA = (RRType.CAA, CAA)
    HTTPS = (RRType.HTTPS, HTTPS)
    LOC = (RRType.LOC, LOC)
    RP = (RRType.RP, RP)
    TLSA = (RRType.TLSA, TLSA)
    SSHFP = (RRType.SSHFP, SSHFP)
    TXT = (RRType.TXT, TXT)


def pack_rdata(data: Any, out: bytearray, ctx: dict[str, Any] | None) -> None:
    if isinstance(data, Struct):
        out += data.pack()
    else:
        data.pack_into(out, ctx)


def unpack_rdata(rtype: int, buf: bytes | bytearray, off: int, length: int, ctx: dict[str, Any]) -> Any:
    try:
        rdata_cls = RDataKind(rtype).rdata_cls
    except ValueError:
        return UnknownRData(RRType(rtype), bytes(buf[off : off + length]))
    if issubclass(rdata_cls, Struct):
        return rdata_cls.unpack(bytes(buf[off : off + length]))
    return rdata_cls.unpack(buf, off, length, ctx)


# ---------- Question / ResourceRecord / DNS: hand-written orchestration ----------


class QuestionTail(Struct, byteorder="big"):
    """A question's fixed-width part, after its (possibly compressed) name."""

    qtype: U16
    qclass: U16


@dataclass
class Question:
    name: str
    qtype: RRType = RRType.A
    qclass: DNSClass = DNSClass.IN

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        write_name(out, encode_labels(self.name), ctx)
        out += QuestionTail(qtype=self.qtype, qclass=self.qclass).pack()

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, ctx: dict[str, Any]) -> tuple["Question", int]:
        name, off = decode_name(buf, off)
        tail, pos = QuestionTail.unpack_from(buf, off)
        return cls(name, RRType(tail.qtype), DNSClass(tail.qclass)), pos


class ResourceRecordHeader(Struct, byteorder="big"):
    """TYPE/CLASS/TTL, ahead of RDLENGTH and the RDATA body. Packed with
    `rdlength=0` as a placeholder (see `ResourceRecord.pack_into`'s
    docstring for why RDLENGTH can't be known yet at this point);
    `unpack_from()` instead reads it for real, since on read the whole
    record -- RDATA included -- is already in `buf`."""

    rtype: U16
    rclass: U16
    ttl: U32
    rdlength: U16 = described(default=0, help="always 0 on pack; patched in afterwards")


@dataclass
class ResourceRecord:
    """`rtype` is never stored here: a known record type carries its own
    class-level `RTYPE`, and `UnknownRData` already carries the wire tag it
    was decoded with -- storing a third, possibly-inconsistent copy on the
    record itself would just invite them drifting apart.

    `rclass` is `DNSClass | int`, not just `DNSClass`: an EDNS0 OPT
    pseudo-record (see `edns0()`) repurposes this field to carry the
    requestor's/responder's UDP payload size instead of an actual class."""

    name: str
    rclass: DNSClass | int = DNSClass.IN
    ttl: int = 0
    data: object = None

    def pack_into(self, out: bytearray, ctx: dict[str, Any] | None) -> None:
        write_name(out, encode_labels(self.name), ctx)
        rtype = getattr(self.data, "RTYPE", None)
        if rtype is None:
            # No class-level RTYPE means this wasn't a registered rdata_cls,
            # so unpack_rdata() must have produced an UnknownRData instead --
            # the only other shape `data` can take (see the class docstring).
            assert isinstance(self.data, UnknownRData)
            rtype = self.data.rtype
        # RDLENGTH isn't known until the RDATA body itself is packed (its
        # own length can depend on name compression state further down the
        # message), so the whole header is written once as a 0-rdlength
        # placeholder and then overwritten in place, rdlength and all.
        rdlen_pos = len(out)
        out += ResourceRecordHeader(rtype=rtype, rclass=self.rclass, ttl=self.ttl).pack()
        body_start = len(out)
        pack_rdata(self.data, out, ctx)
        rdlength = len(out) - body_start
        out[rdlen_pos:body_start] = ResourceRecordHeader(
            rtype=rtype, rclass=self.rclass, ttl=self.ttl, rdlength=rdlength
        ).pack()

    @classmethod
    def unpack(cls, buf: bytes | bytearray, off: int, ctx: dict[str, Any]) -> tuple["ResourceRecord", int]:
        name, off = decode_name(buf, off)
        header, off = ResourceRecordHeader.unpack_from(buf, off)
        data = unpack_rdata(header.rtype, buf, off, header.rdlength, ctx)
        off += header.rdlength
        return cls(name=name, rclass=DNSClass(header.rclass), ttl=header.ttl, data=data), off


class MessageHeader(Struct, byteorder="big"):
    """The whole 12-byte DNS header (RFC 1035 section 4.1.1): ID, the
    FLAGS word (nested -- see `DNSFlags`) and the four section counts."""

    id: U16
    flags: DNSFlags
    qdcount: U16
    ancount: U16
    nscount: U16
    arcount: U16


@dataclass
class DNS:
    """A whole DNS message; the section counts are computed on pack()."""

    id: int = 0
    flags: DNSFlags = field(default_factory=DNSFlags)
    questions: list[Question] = field(default_factory=list)
    answers: list[ResourceRecord] = field(default_factory=list)
    authorities: list[ResourceRecord] = field(default_factory=list)
    additionals: list[ResourceRecord] = field(default_factory=list)

    def pack(self, compress: bool = True) -> bytes:
        out = bytearray(
            MessageHeader(
                id=self.id,
                flags=self.flags,
                qdcount=len(self.questions),
                ancount=len(self.answers),
                nscount=len(self.authorities),
                arcount=len(self.additionals),
            ).pack()
        )
        ctx: dict[str, Any] | None = {} if compress else None
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
    def unpack(cls, buf: bytes | bytearray) -> "DNS":
        header, off = MessageHeader.unpack_from(buf, 0)
        ctx: dict[str, Any] = {}

        def read_n(n: int, reader: Any) -> list[Any]:
            nonlocal off
            items = []
            for _ in range(n):
                item, off = reader(buf, off, ctx)
                items.append(item)
            return items

        questions = read_n(header.qdcount, Question.unpack)
        answers = read_n(header.ancount, ResourceRecord.unpack)
        authorities = read_n(header.nscount, ResourceRecord.unpack)
        additionals = read_n(header.arcount, ResourceRecord.unpack)
        return cls(header.id, header.flags, questions, answers, authorities, additionals)


def reply(request: DNS, *, aa: bool = True) -> DNS:
    """A ready-to-answer `DNS` for `request`: same id and questions, `qr`
    set, `rd` echoed back, `ra` left False (this module has no resolver of
    its own to promise recursion from) and `rcode` NOERROR -- the caller
    fills in answers/authorities/additionals and overwrites
    `.flags.rcode`/`.flags.tc` on the way out, same as any other mutable
    `Struct`/dataclass field."""
    return DNS(
        id=request.id,
        flags=DNSFlags(
            qr=True, opcode=request.flags.opcode, aa=aa, rd=request.flags.rd, ra=False, rcode=RCODE.NOERROR
        ),
        questions=list(request.questions),
    )


def edns0(
    udp_payload_size: int,
    *,
    extended_rcode: int = 0,
    version: int = 0,
    do: bool = False,
    options: bytes = b"",
) -> ResourceRecord:
    """An EDNS0 OPT pseudo-record (RFC 6891) ready to append to a message's
    `additionals`. The requestor's/responder's UDP payload size is smuggled
    in the ordinary `rclass` field (that's the whole point of `DNSClass`
    being an `OpenIntEnum` rather than a closed set); `ttl` carries the
    extended RCODE, version and the DO bit instead of an actual TTL."""
    ttl = (extended_rcode << 24) | (version << 16) | (0x8000 if do else 0)
    return ResourceRecord(name="", rclass=udp_payload_size, ttl=ttl, data=UnknownRData(RRType.OPT, bytes(options)))


def edns_udp_size(msg: DNS) -> int | None:
    """The requestor's/responder's advertised UDP payload size from `msg`'s
    first OPT pseudo-record in `additionals`, or None if it has none."""
    for rr in msg.additionals:
        rtype = getattr(rr.data, "RTYPE", None)
        if rtype is None and isinstance(rr.data, UnknownRData):
            rtype = rr.data.rtype
        if rtype == RRType.OPT:
            return int(rr.rclass)
    return None
