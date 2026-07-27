"""UDP header (RFC 768) -- the simplest protocol here, fitting rustruct's
frontend with zero adaptation."""

from rustruct import U16, described, slice

from .inet import IPPayload, IPProtocol

__all__ = ["UDP"]


class UDP(IPPayload, proto=IPProtocol.UDP):
    source_port: U16 = described(help="sending application's port")
    dest_port: U16 = described(help="receiving application's port")
    length: U16 = described(0, help="UDP header + payload length in bytes")
    checksum: U16 = described(0, help="header + pseudo-header + data checksum")
    payload: bytes = slice(len="*", default=b"")
