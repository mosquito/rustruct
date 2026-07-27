"""TCP header (RFC 793).

The data-offset/flag bits are flat sibling fields rather than grouped into a
nested sub-object (see inet.py's module docstring for why: ``options``
needs to `ref` ``data_offset``, and refs can't reach into an already-closed
nested scope). The control-flag bit conventionally called "ack" is named
``ack_flag`` here to avoid colliding with the u32 acknowledgment-number
field ``ack``.
"""

from rustruct import U16, U32, bits, described, slice

from .inet import IPPayload, IPProtocol

__all__ = ["TCP"]


class TCP(IPPayload, proto=IPProtocol.TCP):
    source_port: U16 = described(help="sending application's port")
    dest_port: U16 = described(help="receiving application's port")
    seq: U32 = described(help="sequence number of the first data byte in this segment")
    ack: U32 = described(0, help="next sequence number expected, when ack_flag is set")
    data_offset: int = bits(4, default=5, help="header length in 32-bit words, including options")
    reserved: int = bits(3, default=0)
    ns: bool = bits(1, default=False, help="ECN-nonce concealment protection")
    cwr: bool = bits(1, default=False, help="congestion window reduced")
    ece: bool = bits(1, default=False, help="ECN-Echo")
    urg: bool = bits(1, default=False, help="urgent pointer field is significant")
    ack_flag: bool = bits(1, default=False, help="acknowledgment field is significant")
    psh: bool = bits(1, default=False, help="push buffered data to the application")
    rst: bool = bits(1, default=False, help="reset the connection")
    syn: bool = bits(1, default=False, help="synchronize sequence numbers")
    fin: bool = bits(1, default=False, help="no more data from the sender")
    window: U16 = described(0, help="receive window size in bytes")
    checksum: U16 = described(0, help="header + pseudo-header + data checksum")
    urgent: U16 = described(0, help="offset to the last urgent data byte, when urg is set")
    options: bytes = slice(len=lambda f: (f.data_offset - 5) * 4, default=b"")
    payload: bytes = slice(len="*", default=b"")
