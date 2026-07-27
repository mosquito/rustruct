# 2. Build an IPv4 packet with UDP

Decoding is only half the workflow -- sending a packet means constructing
one instead. Filling in every IPv4 header field by hand for each outgoing
packet would be repetitive and error-prone, so
{py:meth}`IPv4.build() <rustruct.protocols.IPv4.build>` derives the
protocol number and IPv4 length fields from the payload:

<!-- name: test_tutorial_build_ipv4_udp -->
```python
import ipaddress

from rustruct.protocols import IPProtocol, IPv4
from rustruct.protocols.udp import UDP

udp = UDP(source_port=53, dest_port=5353, length=8, payload=b"")
packet = IPv4.build(
    source=ipaddress.IPv4Address("192.0.2.1"),
    destination=ipaddress.IPv4Address("198.51.100.1"),
    payload=udp,
)

wire = packet.pack()
decoded = IPv4.unpack(wire)

assert decoded.protocol == IPProtocol.UDP
assert isinstance(decoded.body.payload, UDP)
assert decoded.body.payload.dest_port == 5353
```

The convenience model does not recompute transport or IP pseudo-header
checksums. Applications constructing packets for transmission must supply
those values where required.

Previous: {doc}`decode-an-ipv4-packet`. Next: {doc}`build-and-decode-a-dns-query`.
