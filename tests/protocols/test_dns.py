"""DNS message port: header/flags stay a real rustruct.Struct, domain-name
compression is the hand-written escape hatch (see the module docstring in
src/rustruct/protocols/dns.py for why)."""

import ipaddress

from rustruct.protocols.dns import (
    AAAA,
    CNAME,
    DNS,
    MX,
    NS,
    PTR,
    SOA,
    TXT,
    A,
    DNSClass,
    DNSFlags,
    Question,
    ResourceRecord,
    RRType,
    UnknownRData,
)


def test_dns_flags_is_a_real_struct():
    flags = DNSFlags(qr=True, opcode=0, aa=False, tc=False, rd=True, ra=False, z=0, rcode=0)
    wire = flags.pack()
    assert wire == b"\x81\x00"
    back = DNSFlags.unpack(wire)
    assert back == flags


def test_simple_query_roundtrip():
    msg = DNS(id=0x1234, flags=DNSFlags(rd=True), questions=[Question("example.com")])
    wire = msg.pack()
    back = DNS.unpack(wire)
    assert back.id == 0x1234
    assert back.flags.rd == 1
    assert back.questions[0].name == "example.com"
    assert back.questions[0].qtype == RRType.A
    assert back.questions[0].qclass == DNSClass.IN
    assert back.pack() == wire


def test_root_name_roundtrip():
    msg = DNS(questions=[Question("")])
    back = DNS.unpack(msg.pack())
    assert back.questions[0].name == ""


def test_trailing_dot_is_dropped():
    msg = DNS(questions=[Question("example.com.")])
    back = DNS.unpack(msg.pack())
    assert back.questions[0].name == "example.com"


def test_a_response_uses_compression_and_roundtrips():
    resp = DNS(
        id=0x1234,
        flags=DNSFlags(qr=True, aa=True),
        questions=[Question("example.com")],
        answers=[ResourceRecord(name="example.com", ttl=300, data=A(address=ipaddress.IPv4Address("192.0.2.1")))],
    )
    compressed = resp.pack()
    uncompressed = resp.pack(compress=False)
    assert len(compressed) < len(uncompressed), "the repeated name should be a 2-byte pointer"

    back = DNS.unpack(compressed)
    assert back.pack() == compressed
    assert back.answers[0].name == "example.com"
    assert back.answers[0].data.address == ipaddress.IPv4Address("192.0.2.1")

    back_u = DNS.unpack(uncompressed)
    assert back_u.pack(compress=False) == uncompressed
    assert back_u.answers[0].name == "example.com"


def test_all_rdata_types_roundtrip():
    msg = DNS(
        id=1,
        flags=DNSFlags(qr=True),
        answers=[
            ResourceRecord(name="a.test", data=A(address=ipaddress.IPv4Address("10.0.0.1"))),
            ResourceRecord(name="aaaa.test", data=AAAA(address=ipaddress.IPv6Address("::1"))),
            ResourceRecord(name="ns.test", data=NS("ns1.test")),
            ResourceRecord(name="cname.test", data=CNAME("target.test")),
            ResourceRecord(name="ptr.test", data=PTR("host.test")),
            ResourceRecord(name="mx.test", data=MX(10, "mail.test")),
            ResourceRecord(name="soa.test", data=SOA("ns1.test", "admin.test", 1, 2, 3, 4, 5)),
            ResourceRecord(name="txt.test", data=TXT([b"hello", b"world"])),
        ],
    )
    wire = msg.pack()
    back = DNS.unpack(wire)
    assert back.pack() == wire
    assert len(back.answers) == 8
    assert back.answers[2].data == NS("ns1.test")
    assert back.answers[5].data == MX(10, "mail.test")
    assert back.answers[6].data == SOA("ns1.test", "admin.test", 1, 2, 3, 4, 5)
    assert back.answers[7].data == TXT([b"hello", b"world"])


def test_unknown_rdata_type_roundtrips_as_raw_bytes():
    msg = DNS(answers=[ResourceRecord(name="x.test", ttl=1, data=UnknownRData(999, b"\xaa\xbb"))])
    wire = msg.pack()
    back = DNS.unpack(wire)
    assert back.pack() == wire
    assert back.answers[0].data == UnknownRData(999, b"\xaa\xbb")


def test_section_counts_are_computed_on_pack():
    msg = DNS(
        questions=[Question("a"), Question("b")],
        answers=[ResourceRecord(name="a", data=A(address=ipaddress.IPv4Address("1.2.3.4")))],
    )
    wire = msg.pack()
    import struct as pystruct

    _, _, qdcount, ancount, nscount, arcount = pystruct.unpack_from("!HHHHHH", wire, 0)
    assert (qdcount, ancount, nscount, arcount) == (2, 1, 0, 0)
