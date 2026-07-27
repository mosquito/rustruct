# How to use the IPv4 model

## Decode an existing packet

<!-- name: test_use_ipv4 -->
```python
import ipaddress

from rustruct.protocols import IPProtocol, IPv4
from rustruct.protocols.tcp import TCP

wire = bytes.fromhex(
    "45 00 00 28 1c 46 40 00 40 06 b1 e6 c0 a8 00 01 c0 a8 00 02"
    "00 50 c3 50 00 00 00 01 00 00 00 00 50 02 72 10 e6 f2 00 00"
)

packet = IPv4.unpack(wire)

assert packet.source == ipaddress.IPv4Address("192.168.0.1")
assert packet.destination == ipaddress.IPv4Address("192.168.0.2")
assert packet.protocol == IPProtocol.TCP
assert isinstance(packet.body.payload, TCP)
assert packet.body.payload.dest_port == 50000
assert packet.pack() == wire
```

Unknown protocol numbers preserve the payload as raw bytes, so decoding and
repacking does not discard an unsupported upper-layer protocol.

## Build a UDP packet

Use {py:meth}`IPv4.build() <rustruct.protocols.IPv4.build>` to derive the
header length, total length, and IP protocol number from the supplied
payload:

<!-- name: test_use_ipv4 -->
```python
from rustruct.protocols.udp import UDP

udp = UDP(source_port=53, dest_port=5353, length=8, payload=b"")
packet = IPv4.build(
    source=ipaddress.IPv4Address("192.0.2.1"),
    destination=ipaddress.IPv4Address("198.51.100.1"),
    payload=udp,
)
assert packet.protocol == IPProtocol.UDP
```

The convenience models preserve existing checksum fields but do not currently
recompute transport or IP pseudo-header checksums. Compute those values in the
calling application when constructing packets for transmission.

See {doc}`/reference/protocols` for the complete protocol API.
