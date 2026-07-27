"""IPv4/TCP/UDP declarative protocol schemas, verified against real
captured header bytes."""

import ipaddress

import pytest

from rustruct import InvalidDataError
from rustruct.protocols import IPProtocol, IPv4
from rustruct.protocols.tcp import TCP
from rustruct.protocols.udp import UDP

# 192.168.0.1 -> 192.168.0.2, TCP, DF set, no options, total_length=40 (20 + TCP_HDR)
IP_HDR = bytes.fromhex("450000281c4640004006b1e6c0a80001c0a80002")
# port 80 -> 50000, SYN, no options, no payload -- exactly the 20 header bytes
TCP_HDR = bytes.fromhex("0050c350000000010000000050027210e6f20000")


def test_ipv4_parse_with_declarative_tcp_payload():
    ip = IPv4.unpack(IP_HDR + TCP_HDR)
    assert ip.version == 4
    assert ip.ihl == 5
    assert ip.total_length == 40
    assert ip.df == 1
    assert ip.mf == 0
    assert ip.fragment_offset == 0
    assert ip.ttl == 64
    assert ip.protocol == IPProtocol.TCP
    assert ip.checksum == 0xB1E6
    assert ip.source == ipaddress.IPv4Address("192.168.0.1")
    assert ip.destination == ipaddress.IPv4Address("192.168.0.2")
    assert isinstance(ip.source, ipaddress.IPv4Address)
    assert ip.body.options == b""
    # no separate decode step: .body.payload is already the parsed TCP struct
    assert isinstance(ip.body.payload, TCP)
    assert ip.body.payload.dest_port == 50000


def test_ipv4_roundtrip():
    wire = IP_HDR + TCP_HDR
    assert IPv4.unpack(wire).pack() == wire


def test_ipv4_total_length_bounds_switched_payload():
    with pytest.raises(InvalidDataError) as ei:
        IPv4.unpack(IP_HDR + TCP_HDR + b"\xde\xad")
    assert ei.value.kind == "trailing"


def test_ipv4_rejects_payload_past_declared_total_length():
    header = bytearray(IP_HDR)
    header[2:4] = (20).to_bytes(2, "big")
    with pytest.raises(InvalidDataError) as ei:
        IPv4.unpack(bytes(header) + TCP_HDR)
    assert ei.value.kind == "truncated"


def test_tcp_parse():
    tcp = TCP.unpack(TCP_HDR)
    assert tcp.source_port == 80
    assert tcp.dest_port == 50000
    assert tcp.seq == 1
    assert tcp.data_offset == 5
    assert tcp.syn == 1
    assert tcp.ack_flag == 0
    assert tcp.window == 0x7210
    assert tcp.options == b""
    assert tcp.payload == b""


def test_tcp_roundtrip():
    assert TCP.unpack(TCP_HDR).pack() == TCP_HDR


def test_registry_contents():
    from rustruct.protocols.inet import IPPayload

    registry = dict(IPPayload.dispatch_registry.items())
    assert registry == {IPProtocol.TCP: TCP, IPProtocol.UDP: UDP}


def test_ipv4_build_udp_payload_roundtrip():
    udp = UDP(source_port=53, dest_port=5353, length=8, checksum=0, payload=b"")
    source = ipaddress.IPv4Address("1.1.1.1")
    destination = ipaddress.IPv4Address("2.2.2.2")
    ip = IPv4.build(source=source, destination=destination, payload=udp)
    wire = ip.pack()
    back = IPv4.unpack(wire)
    assert back.source == source
    assert back.destination == destination
    assert isinstance(back.body.payload, UDP)
    assert back.body.payload.source_port == 53
    assert back.protocol == IPProtocol.UDP  # computed by build(), not set by hand
    assert back.pack() == wire


def test_ipv4_unknown_protocol_falls_back_to_raw():
    # protocol 1 (ICMP) has no registered payload class
    data = b"\x08\x00\xf7\xff\x00\x00\x00\x00"
    ip = IPv4.build(
        source=ipaddress.IPv4Address("1.1.1.1"),
        destination=ipaddress.IPv4Address("2.2.2.2"),
        payload=data,
        protocol=int(IPProtocol.ICMP),
    )
    wire = ip.pack()
    back = IPv4.unpack(wire)
    assert back.body.payload == data
    assert back.protocol == IPProtocol.ICMP
    assert back.pack() == wire


def test_ipv4_build_requires_explicit_protocol_for_raw_payload():
    with pytest.raises(TypeError, match="protocol"):
        IPv4.build(
            source=ipaddress.IPv4Address("1.1.1.1"),
            destination=ipaddress.IPv4Address("2.2.2.2"),
            payload=b"\x00",
        )


def test_ipv4_options_are_declaratively_sized():
    # ihl=6 -> 4 bytes of options
    ip = IPv4.build(
        source=ipaddress.IPv4Address("1.1.1.1"),
        destination=ipaddress.IPv4Address("2.2.2.2"),
        payload=UDP(source_port=1, dest_port=2, length=0, checksum=0, payload=b""),
        options=b"\x01\x02\x03\x04",
    )
    wire = ip.pack()
    back = IPv4.unpack(wire)
    assert back.ihl == 6
    assert back.body.options == b"\x01\x02\x03\x04"
    assert back.pack() == wire


def test_tcp_options_are_declaratively_sized():
    tcp = TCP(
        source_port=1,
        dest_port=2,
        seq=0,
        data_offset=6,
        window=0,
        checksum=0,
        urgent=0,
        options=b"\x00\x00\x00\x00",
    )
    wire = tcp.pack()
    back = TCP.unpack(wire)
    assert back.options == b"\x00\x00\x00\x00"
    assert back.pack() == wire
