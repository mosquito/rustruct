"""IPv4 header (RFC 791).

Bit groups (`version`/`ihl`, `dscp`/`ecn`, flags+fragment-offset) are flat
sibling fields, not nested sub-objects: a `ref` can only reach the current
or an enclosing scope, and `options`' length depends on `ihl`, so `ihl`
must live in the same scope as `options`.

`total_length` is `HEADER_LEN + len(options) + len(payload)` -- a sum of
two independently-sized regions, which rustruct's derived-length mechanism
(linear in one ref) can't express directly. `options` and `payload` are
grouped instead under one nested, `total_length`-sized window (`body`):
within it, `options` is sized from `ihl` (a ref up into the enclosing
scope) and `payload` greedily consumes the rest.

`protocol` stays an ordinary, explicit field rather than derived from
`payload`'s registered tag, since `switch()` requires its discriminant to
be explicit input. `IPv4.build()` is a plain-Python convenience
constructor that computes `ihl`/`total_length`/`protocol` before pack().
"""

import enum
import ipaddress

from rustruct import (
    U8,
    U16,
    U32,
    Struct,
    bits,
    convert,
    described,
    raw,
    sized,
    slice,
    switch,
)

__all__ = ["IPProtocol", "IPv4Address", "IPv6Address", "IPPayload", "Body", "IPv4", "HEADER_LEN"]

HEADER_LEN = 20  # fixed part, before `options`


class IPProtocol(enum.IntEnum):
    ICMP = 1
    TCP = 6
    UDP = 17
    GRE = 47


def ipv4_address_field(**kwargs):
    return convert(U32, decode=ipaddress.IPv4Address, encode=int, **kwargs)


def ipv6_address_field(**kwargs):
    return convert(raw(16), decode=ipaddress.IPv6Address, encode=lambda a: a.packed, **kwargs)


# Exposed as plain names too, for anything that wants the Python type rather
# than a field descriptor.
IPv4Address = ipaddress.IPv4Address
IPv6Address = ipaddress.IPv6Address


class IPPayload(Struct, registry=True):
    """The upper-layer registry base; concrete protocols key in via `proto=`."""


class Body(Struct):
    """``options`` + ``payload``, windowed to exactly `total_length -
    HEADER_LEN` bytes (see the module docstring)."""

    options: bytes = slice(len=lambda f: (f.ihl - 5) * 4, default=b"")
    payload: object = switch(on="protocol", cases=IPPayload.dispatch_registry, default=slice(len="*"))


class IPv4(Struct):
    version: int = bits(4, default=4, help="IP version; 4 for IPv4")
    ihl: int = bits(4, default=5, help="header length in 32-bit words, including options")
    dscp: int = bits(6, default=0, help="differentiated services code point")
    ecn: int = bits(2, default=0, help="explicit congestion notification")
    total_length: U16 = described(help="total datagram length in bytes")
    identification: U16 = described(0, help="identifies fragments of the same datagram")
    reserved: bool = bits(1, default=False)
    df: bool = bits(1, default=False, help="don't fragment")
    mf: bool = bits(1, default=False, help="more fragments follow")
    fragment_offset: int = bits(13, default=0, help="fragment offset in 8-byte units")
    ttl: U8 = described(64, help="hop limit; each router decrements it, drops at zero")
    protocol: U8 = described(help="next-header protocol; see IPProtocol")
    checksum: U16 = described(0, help="header checksum")
    source: object = ipv4_address_field(help="sender's IPv4 address")
    destination: object = ipv4_address_field(help="recipient's IPv4 address")
    body: object = sized(Body, size=lambda f: f.total_length - HEADER_LEN)

    @classmethod
    def build(
        cls,
        *,
        source,
        destination,
        payload,
        protocol=None,
        options=b"",
        ttl=64,
        version=4,
        dscp=0,
        ecn=0,
        identification=0,
        reserved=False,
        df=False,
        mf=False,
        fragment_offset=0,
        checksum=0,
    ):
        """Compute ihl/total_length/protocol, then build one ready-to-pack
        instance. `protocol` is inferred from a registered `payload` (e.g.
        TCP/UDP); it must be given explicitly when `payload` is raw bytes
        for an unregistered protocol number."""
        payload_bytes = payload.pack() if isinstance(payload, Struct) else bytes(payload)
        if protocol is None:
            if not isinstance(payload, Struct):
                raise TypeError("IPv4.build(): protocol= is required when payload is raw bytes")
            protocol = payload.registry_key
        return cls(
            version=version,
            ihl=5 + len(options) // 4,
            dscp=dscp,
            ecn=ecn,
            total_length=HEADER_LEN + len(options) + len(payload_bytes),
            identification=identification,
            reserved=reserved,
            df=df,
            mf=mf,
            fragment_offset=fragment_offset,
            ttl=ttl,
            protocol=protocol,
            checksum=checksum,
            source=source,
            destination=destination,
            body=Body(options=options, payload=payload),
        )
