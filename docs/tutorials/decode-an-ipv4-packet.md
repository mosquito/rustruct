# 1. Decode an IPv4 packet

The chat relay tutorial designed a wire format from scratch. This second
sequence applies the same `Struct`/`pack`/`unpack` workflow to a handful of
formats rustruct already models for you -- formats you did not design and
cannot change, which is the more common case once code has to talk to the
outside world. Complete {doc}`/tutorials/install-rustruct` first if
{py:meth}`rustruct.Struct.pack` and {py:meth}`rustruct.Struct.unpack` are
unfamiliar.

Start with a complete packet containing an IPv4 header and a TCP header:

<!-- name: test_tutorial_decode_ipv4_packet -->
```python
import ipaddress

from rustruct.protocols import IPProtocol, IPv4
from rustruct.protocols.tcp import TCP

wire = bytes.fromhex(
    "45 00 00 28 1c 46 40 00 40 06 b1 e6 c0 a8 00 01 c0 a8 00 02"
    "00 50 c3 50 00 00 00 01 00 00 00 00 50 02 72 10 e6 f2 00 00"
)
packet = IPv4.unpack(wire)
```

The first 20 bytes are the IPv4 header itself:

| Offset | Bytes | Field                   | Value                         |
| -----: | ----: | ----------------------- | ------------------------------ |
|      0 |     1 | version + IHL           | `0x45` (version 4, 5×4 = 20-byte header) |
|      1 |     1 | DSCP / ECN              | `0x00`                         |
|    2-3 |     2 | total length             | `0x0028` (40: this header + the TCP header) |
|    4-5 |     2 | identification            | `0x1c46`                       |
|    6-7 |     2 | flags + fragment offset   | `0x4000` (don't-fragment, offset 0) |
|      8 |     1 | TTL                      | `0x40` (64 hops)               |
|      9 |     1 | protocol                 | `0x06` (TCP)                   |
|  10-11 |     2 | header checksum           | `0xb1e6`                       |
|  12-15 |     4 | source address            | `192.168.0.1`                  |
|  16-19 |     4 | destination address       | `192.168.0.2`                  |

Bytes 20-39 are the TCP header `packet.body.payload` decodes into. The
model converts addresses to `IPv4Address` and dispatches the payload from
the IP protocol number:

<!-- name: test_tutorial_decode_ipv4_packet -->
```python
assert packet.source == ipaddress.IPv4Address("192.168.0.1")
assert packet.destination == ipaddress.IPv4Address("192.168.0.2")
assert packet.protocol == IPProtocol.TCP
assert isinstance(packet.body.payload, TCP)
assert packet.body.payload.source_port == 80
assert packet.body.payload.dest_port == 50000
```

Packing the typed packet preserves the original bytes:

<!-- name: test_tutorial_decode_ipv4_packet -->
```python
assert packet.pack() == wire
```

Next: {doc}`build-an-ipv4-packet-with-udp`.
