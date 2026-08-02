"""The record types added on top of tests/protocols/test_dns.py's original
eight: SRV/NAPTR/DNAME/RP (name-compressible, hand-written), RRSIG/NSEC
(hand-written, non-compressible names), and DS/DNSKEY/TLSA/SSHFP/CAA/LOC
(no domain name -- real `rustruct.Struct` classes). Every wire-format
assertion below is cross-checked against dnslib (github.com/paulc/dnslib)
0.9.26's own `pack()` output, the same correctness signal
test_dns_fixtures.py uses for the original eight record types."""

from rustruct.protocols.dns import (
    CAA,
    DNAME,
    DNS,
    DNSKEY,
    DS,
    HTTPS,
    LOC,
    NAPTR,
    NSEC,
    RCODE,
    RP,
    RRSIG,
    SRV,
    SSHFP,
    TLSA,
    DNSFlags,
    Question,
    ResourceRecord,
    RRType,
    edns0,
    edns_udp_size,
    pack_rdata,
    reply,
)


def pack(rdata):
    out = bytearray()
    pack_rdata(rdata, out, {})
    return bytes(out)


def test_srv_matches_dnslib_and_does_not_compress():
    rdata = SRV(priority=10, weight=20, port=80, target="target.example.com")
    assert pack(rdata) == bytes.fromhex("000a0014005006746172676574076578616d706c6503636f6d00")


def test_naptr_matches_dnslib():
    rdata = NAPTR(order=100, preference=10, flags="S", service="SIP+D2U", regexp="", replacement=".")
    assert pack(rdata) == bytes.fromhex("0064000a0153075349502b4432550000")


def test_rrsig_matches_dnslib_and_does_not_compress():
    rdata = RRSIG(
        type_covered=RRType.A,
        algorithm=5,
        labels=2,
        original_ttl=300,
        expiration=1234567890,
        inception=1234560000,
        key_tag=12345,
        signer="example.com",
        signature=b"abcdefg",
    )
    assert pack(rdata) == bytes.fromhex("000105020000012c499602d24995e4003039076578616d706c6503636f6d0061626364656667")


def test_nsec_matches_dnslib_and_does_not_compress():
    rdata = NSEC(next_domain="next.example.com", rrtypes=[RRType.A, RRType.AAAA])
    assert pack(rdata) == bytes.fromhex("046e657874076578616d706c6503636f6d00000440000008")


def test_ds_matches_dnslib():
    rdata = DS(key_tag=12345, algorithm=5, digest_type=1, digest=b"abc123")
    assert rdata.pack() == bytes.fromhex("30390501616263313233")


def test_dnskey_matches_dnslib():
    rdata = DNSKEY(flags=256, protocol=3, algorithm=5, key=b"abcdefg")
    assert rdata.pack() == bytes.fromhex("0100030561626364656667")


def test_tlsa_matches_dnslib():
    rdata = TLSA(usage=1, selector=1, matching_type=1, cert_data=b"abcdefg")
    assert rdata.pack() == bytes.fromhex("01010161626364656667")


def test_sshfp_matches_dnslib():
    rdata = SSHFP(algorithm=1, fp_type=1, fingerprint=b"abcdefg")
    assert rdata.pack() == bytes.fromhex("010161626364656667")


def test_caa_matches_dnslib():
    rdata = CAA(flags=0, tag="issue", value="letsencrypt.org")
    wire = rdata.pack()
    assert wire == bytes.fromhex("000569737375656c657473656e63727970742e6f7267")
    back = CAA.unpack(wire)
    assert (back.flags, back.tag, back.value) == (rdata.flags, rdata.tag, rdata.value)


def test_rp_matches_dnslib():
    rdata = RP(mbox="hostmaster.example.com", txt="contact.example.com")
    assert pack(rdata) == bytes.fromhex("0a686f73746d6173746572076578616d706c6503636f6d0007636f6e74616374c00b")


def test_dname_matches_dnslib():
    rdata = DNAME(target="target.example.com")
    assert pack(rdata) == bytes.fromhex("06746172676574076578616d706c6503636f6d00")


def test_https_matches_dnslib_and_does_not_compress():
    rdata = HTTPS(priority=1, target="target.example.com", params=[(1, b"h2")])
    assert pack(rdata) == bytes.fromhex("000106746172676574076578616d706c6503636f6d00000100026832")


def test_loc_matches_dnslib():
    rdata = LOC(latitude=12.34, longitude=56.78, altitude=90.0, size=1.0, h_precision=2.0, v_precision=3.0)
    assert rdata.pack() == bytes.fromhex("0012223282a5db408c2f04c00098b9a8")


def test_loc_roundtrips_defaults():
    rdata = LOC(latitude=-33.8688, longitude=151.2093, altitude=25.0)
    back = LOC.unpack(rdata.pack())
    assert round(back.latitude, 4) == -33.8688
    assert round(back.longitude, 4) == 151.2093
    assert round(back.altitude, 2) == 25.0
    assert back.size == 1.0
    assert back.h_precision == 10000.0
    assert back.v_precision == 10.0


def test_all_extra_rdata_types_roundtrip_through_a_message():
    msg = DNS(
        id=1,
        flags=DNSFlags(qr=True),
        answers=[
            ResourceRecord(name="srv.test", data=SRV(10, 20, 80, "target.test")),
            ResourceRecord(name="naptr.test", data=NAPTR(100, 10, "S", "SIP+D2U", "", "naptr.test")),
            ResourceRecord(name="dname.test", data=DNAME("target.test")),
            ResourceRecord(name="rp.test", data=RP("mbox.test", "rp.test")),
            ResourceRecord(name="rrsig.test", data=RRSIG(1, 5, 2, 300, 2, 1, 1, "signer.test", b"sig")),
            ResourceRecord(name="nsec.test", data=NSEC("next.test", [RRType.A, RRType.MX, RRType.AAAA])),
            ResourceRecord(name="ds.test", data=DS(key_tag=1, algorithm=5, digest_type=1, digest=b"xy")),
            ResourceRecord(name="dnskey.test", data=DNSKEY(flags=0, protocol=3, algorithm=5, key=b"xy")),
            ResourceRecord(name="tlsa.test", data=TLSA(usage=1, selector=1, matching_type=1, cert_data=b"xy")),
            ResourceRecord(name="sshfp.test", data=SSHFP(algorithm=1, fp_type=1, fingerprint=b"xy")),
            ResourceRecord(name="caa.test", data=CAA(flags=0, tag="issue", value="ca.test")),
            ResourceRecord(name="https.test", data=HTTPS(1, "target.test", [(1, b"h2")])),
            ResourceRecord(name="loc.test", data=LOC(latitude=1.0, longitude=2.0, altitude=3.0)),
        ],
    )
    wire = msg.pack()
    back = DNS.unpack(wire)
    assert back.pack() == wire
    assert len(back.answers) == len(msg.answers)
    assert back.answers[1].data.replacement == "naptr.test"
    assert back.answers[5].data.rrtypes == [RRType.A, RRType.MX, RRType.AAAA]


def test_reply_copies_id_and_questions_and_echoes_rd():
    request = DNS(id=0x1234, flags=DNSFlags(rd=True), questions=[Question("example.com", RRType.A)])
    response = reply(request)
    assert response.id == request.id
    assert response.questions == request.questions
    assert response.flags.qr is True
    assert response.flags.aa is True
    assert response.flags.rd is True
    assert response.flags.ra is False
    assert response.flags.rcode == RCODE.NOERROR


def test_reply_rcode_is_mutable_after_construction():
    response = reply(DNS(id=1, questions=[Question("example.com")]))
    response.flags.rcode = RCODE.NXDOMAIN
    back = DNS.unpack(response.pack())
    assert back.flags.rcode == RCODE.NXDOMAIN


def test_edns0_round_trips_udp_payload_size():
    request = DNS(id=1, questions=[Question("example.com")], additionals=[edns0(4096)])
    assert edns_udp_size(request) == 4096
    back = DNS.unpack(request.pack())
    assert edns_udp_size(back) == 4096


def test_edns_udp_size_is_none_without_opt_record():
    assert edns_udp_size(DNS(id=1, questions=[Question("example.com")])) is None
