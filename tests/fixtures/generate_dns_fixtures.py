"""Regenerate dnslib_dns_packets.json from scratch: every family here is
either a full cartesian product (flag-bits, opcode-rcode, question, rdata)
or, for "scenario", one hand-built edge case, built directly against
dnslib (github.com/paulc/dnslib) and written out fresh -- nothing here
reads the previous fixture, so this script is the single source of truth
for it, and test_dns_fixtures.py's byte-for-byte comparisons are only as
good as dnslib's own wire output.

dnslib is not one of rustruct's own dependencies (this generator is a
one-off tool, not part of the test suite), so run it with a separate
interpreter that has dnslib installed:

    uv run --with dnslib python tests/fixtures/generate_dns_fixtures.py

Deterministic by construction (no wall-clock or random data anywhere --
the SOA serials below are fixed constants, not a generation date), so
running this script twice in a row produces byte-identical output.
"""

import json
from pathlib import Path

import dnslib

FIXTURE_PATH = Path(__file__).parent / "dnslib_dns_packets.json"

SCHEMA = 1
GENERATED_BY = {"library": "dnslib", "repository": "https://github.com/paulc/dnslib", "version": dnslib.version}

QUESTION_TYPES = [1, 2, 5, 6, 12, 15, 16, 28, 33, 41, 255, 65000]
DNS_CLASSES = [1, 3, 4, 255, 65000]
SECTIONS = ["answers", "authorities", "additionals"]
RCLASS_NAME = {1: "in", 3: "ch", 4: "hs", 255: "any", 65000: "unknown"}
TYPE_NAME = {
    1: "a",
    2: "ns",
    5: "cname",
    6: "soa",
    12: "ptr",
    15: "mx",
    16: "txt",
    28: "aaaa",
    33: "srv",
    41: "opt",
    255: "any",
    65000: "unknown",
    17: "rp",
    29: "loc",
    35: "naptr",
    39: "dname",
    43: "ds",
    44: "sshfp",
    46: "rrsig",
    47: "nsec",
    48: "dnskey",
    52: "tlsa",
    65: "https",
    257: "caa",
}
# Every RDATA type this fixture exercises via the "rdata" family: the
# original eight rustruct.protocols.dns started with, plus the thirteen
# added afterwards, plus 65000 standing in for a type nobody registers
# (UnknownRData).
ALL_RDATA_TYPES = [1, 2, 5, 6, 12, 15, 16, 28, 17, 29, 33, 35, 39, 43, 44, 46, 47, 48, 52, 65, 257, 65000]

SOA_SERIAL = 2024010100  # a fixed constant, not a generation date -- see module docstring


def hexs(value):
    return bytes(value).hex()


def add_to_section(record, section, rr):
    adder = {"answers": record.add_answer, "authorities": record.add_auth, "additionals": record.add_ar}[section]
    adder(rr)


def flags_dict(*, qr=False, opcode=0, aa=False, tc=False, rd=False, ra=False, z=0, rcode=0):
    return {"qr": qr, "opcode": opcode, "aa": aa, "tc": tc, "rd": rd, "ra": ra, "z": z, "rcode": rcode}


def empty_packet(id_, flags, *, questions=(), answers=(), authorities=(), additionals=()):
    return {
        "id": id_,
        "flags": flags,
        "qdcount": len(questions),
        "ancount": len(answers),
        "nscount": len(authorities),
        "arcount": len(additionals),
        "questions": list(questions),
        "answers": list(answers),
        "authorities": list(authorities),
        "additionals": list(additionals),
    }


# ---------- rdata: one (dnslib factory, JSON "data" dict) pair per type ----------


def rdata_spec(rtype):
    match rtype:
        case 1:
            return dnslib.A("192.0.2.1"), {"kind": "A", "address": "192.0.2.1"}
        case 2:
            return dnslib.NS("ns1.example.test"), {"kind": "NS", "target": "ns1.example.test"}
        case 5:
            return dnslib.CNAME("alias.example.test"), {"kind": "CNAME", "target": "alias.example.test"}
        case 6:
            return (
                dnslib.SOA(
                    "ns1.example.test", "hostmaster.example.test", (SOA_SERIAL, 3600, 600, 86400, 300)
                ),
                {
                    "kind": "SOA",
                    "mname": "ns1.example.test",
                    "rname": "hostmaster.example.test",
                    "serial": SOA_SERIAL,
                    "refresh": 3600,
                    "retry": 600,
                    "expire": 86400,
                    "minimum": 300,
                },
            )
        case 12:
            return dnslib.PTR("host.example.test"), {"kind": "PTR", "target": "host.example.test"}
        case 15:
            return (
                dnslib.MX("mail.example.test", 65535),
                {"kind": "MX", "preference": 65535, "exchange": "mail.example.test"},
            )
        case 16:
            strings = [b"", b"hello", b"binary\x00value"]
            return dnslib.TXT(strings), {"kind": "TXT", "strings": [hexs(s) for s in strings]}
        case 28:
            return dnslib.AAAA("2001:db8::1"), {"kind": "AAAA", "address": "2001:db8::1"}
        case 17:
            return (
                dnslib.RP("mbox1.example.test", "txt1.example.test"),
                {"kind": "RP", "mbox": "mbox1.example.test", "txt": "txt1.example.test"},
            )
        case 29:
            return (
                dnslib.LOC(51.5074, -0.1278, 25.0, 2.0, 3000.0, 5.0),
                {
                    "kind": "LOC",
                    "latitude": 51.5074,
                    "longitude": -0.1278,
                    "altitude": 25.0,
                    "size": 2.0,
                    "h_precision": 3000.0,
                    "v_precision": 5.0,
                },
            )
        case 33:
            return (
                dnslib.SRV(65535, 65535, 65535, "target1.example.test"),
                {"kind": "SRV", "priority": 65535, "weight": 65535, "port": 65535, "target": "target1.example.test"},
            )
        case 35:
            return (
                dnslib.NAPTR(65535, 65535, b"S", b"SIP+D2U", b"", b"replacement1.example.test"),
                {
                    "kind": "NAPTR",
                    "order": 65535,
                    "preference": 65535,
                    "flags": "S",
                    "service": "SIP+D2U",
                    "regexp": "",
                    "replacement": "replacement1.example.test",
                },
            )
        case 39:
            return dnslib.DNAME("target1.example.test"), {"kind": "DNAME", "target": "target1.example.test"}
        case 43:
            return (
                dnslib.DS(65535, 5, 1, b"digest-bytes"),
                {"kind": "DS", "key_tag": 65535, "algorithm": 5, "digest_type": 1, "digest": hexs(b"digest-bytes")},
            )
        case 44:
            return (
                dnslib.SSHFP(4, 2, b"fingerprint-bytes"),
                {"kind": "SSHFP", "algorithm": 4, "fp_type": 2, "fingerprint": hexs(b"fingerprint-bytes")},
            )
        case 46:
            return (
                dnslib.RRSIG(
                    1, 5, 2, 4294967295, 4294967295, 0, 65535, "signer1.example.test", b"signature-bytes"
                ),
                {
                    "kind": "RRSIG",
                    "type_covered": 1,
                    "algorithm": 5,
                    "labels": 2,
                    "original_ttl": 4294967295,
                    "expiration": 4294967295,
                    "inception": 0,
                    "key_tag": 65535,
                    "signer": "signer1.example.test",
                    "signature": hexs(b"signature-bytes"),
                },
            )
        case 47:
            return (
                dnslib.NSEC("next1.example.test", ["A", "MX", "AAAA"]),
                {"kind": "NSEC", "next_domain": "next1.example.test", "rrtypes": [1, 15, 28]},
            )
        case 48:
            return (
                dnslib.DNSKEY(257, 3, 5, b"key-bytes"),
                {"kind": "DNSKEY", "flags": 257, "protocol": 3, "algorithm": 5, "key": hexs(b"key-bytes")},
            )
        case 52:
            return (
                dnslib.TLSA(1, 1, 1, b"cert-data-bytes"),
                {"kind": "TLSA", "usage": 1, "selector": 1, "matching_type": 1, "cert_data": hexs(b"cert-data-bytes")},
            )
        case 65:
            return (
                dnslib.HTTPS(
                    1, [bytearray(b"target1"), bytearray(b"example"), bytearray(b"test")], [(1, bytearray(b"h2"))]
                ),
                {"kind": "HTTPS", "priority": 1, "target": "target1.example.test", "params": [[1, hexs(b"h2")]]},
            )
        case 257:
            return (
                dnslib.CAA(128, "issue", "letsencrypt.org"),
                {"kind": "CAA", "flags": 128, "tag": "issue", "value": "letsencrypt.org"},
            )
        case 65000:
            return dnslib.RD(bytes.fromhex("00deadff")), {"kind": "UnknownRData", "data": "00deadff"}
        case _:
            raise AssertionError(f"no rdata_spec() for rtype {rtype}")


def build_rr(rtype, rclass, ttl):
    rdata, data = rdata_spec(rtype)
    name = f"{TYPE_NAME[rtype]}.example.test"
    rr = dnslib.RR(rname=name, rtype=rtype, rclass=rclass, ttl=ttl, rdata=rdata)
    rr_json = {"name": name, "rtype": rtype, "rclass": rclass, "ttl": ttl, "data": data}
    return rr, rr_json


# ---------- the five families ----------


def gen_flag_bits():
    cases = []
    for mask in range(32):
        for z in range(8):
            qr, aa, tc, rd, ra = (bool(mask & bit) for bit in (1, 2, 4, 8, 16))
            z_bit, ad_bit, cd_bit = (z >> 2) & 1, (z >> 1) & 1, z & 1
            id_ = mask * 8 + z
            header = dnslib.DNSHeader(
                id=id_, bitmap=0, qr=qr, opcode=0, aa=aa, tc=tc, rd=rd, ra=ra, z=z_bit, ad=ad_bit, cd=cd_bit, rcode=0
            )
            wire = dnslib.DNSRecord(header).pack()
            cases.append(
                {
                    "name": f"flags-{mask:02d}-z-{z}",
                    "coverage": {"family": "flag-bits", "boolean_mask": mask, "z": z},
                    "packet": empty_packet(id_, flags_dict(qr=qr, aa=aa, tc=tc, rd=rd, ra=ra, z=z)),
                    "wire": wire.hex(),
                }
            )
    return cases


def gen_opcode_rcode():
    cases = []
    for opcode in range(16):
        for rcode in range(16):
            id_ = 16384 + opcode * 16 + rcode
            header = dnslib.DNSHeader(id=id_, bitmap=0, opcode=opcode, rcode=rcode)
            wire = dnslib.DNSRecord(header).pack()
            cases.append(
                {
                    "name": f"opcode-{opcode}-rcode-{rcode}",
                    "coverage": {"family": "opcode-rcode", "opcode": opcode, "rcode": rcode},
                    "packet": empty_packet(id_, flags_dict(opcode=opcode, rcode=rcode)),
                    "wire": wire.hex(),
                }
            )
    return cases


def gen_question():
    cases = []
    for qtype in QUESTION_TYPES:
        for qclass in DNS_CLASSES:
            id_ = (qclass + qtype) & 0xFFFF
            name = f"q-{qtype}-{qclass}.example.test"
            header = dnslib.DNSHeader(id=id_, bitmap=0, rd=True)
            record = dnslib.DNSRecord(header, q=dnslib.DNSQuestion(name, qtype=qtype, qclass=qclass))
            cases.append(
                {
                    "name": f"question-{TYPE_NAME[qtype]}-{RCLASS_NAME[qclass]}",
                    "coverage": {"family": "question", "qtype": qtype, "qclass": qclass},
                    "packet": empty_packet(
                        id_, flags_dict(rd=True), questions=[{"name": name, "qtype": qtype, "qclass": qclass}]
                    ),
                    "wire": record.pack().hex(),
                }
            )
    return cases


def gen_rdata():
    cases = []
    for rtype in ALL_RDATA_TYPES:
        for section in SECTIONS:
            for rclass in DNS_CLASSES:
                ttl = 4294967295
                id_ = (rclass + rtype) & 0xFFFF
                rr, rr_json = build_rr(rtype, rclass, ttl)
                header = dnslib.DNSHeader(id=id_, bitmap=0, qr=True, aa=True, rd=True, ra=True)
                record = dnslib.DNSRecord(header)
                add_to_section(record, section, rr)
                sections = {section: [rr_json]}
                cases.append(
                    {
                        "name": f"rdata-{TYPE_NAME[rtype]}-{section}-{RCLASS_NAME[rclass]}",
                        "coverage": {"family": "rdata", "rtype": rtype, "section": section, "rclass": rclass},
                        "packet": empty_packet(id_, flags_dict(qr=True, aa=True, rd=True, ra=True), **sections),
                        "wire": record.pack().hex(),
                    }
                )
    return cases


def gen_scenarios():
    cases = []

    # root-names: the RR name and its NS target are both the DNS root ("").
    header = dnslib.DNSHeader(id=40961, bitmap=0, qr=True, aa=True, rd=True, ra=True)
    record = dnslib.DNSRecord(header, q=dnslib.DNSQuestion("", qtype=2))
    record.add_answer(dnslib.RR(rname="", rtype=2, rclass=1, ttl=0, rdata=dnslib.NS("")))
    cases.append(
        {
            "name": "scenario-root-names",
            "coverage": {"family": "scenario", "scenario": "root-names"},
            "packet": empty_packet(
                40961,
                flags_dict(qr=True, aa=True, rd=True, ra=True),
                questions=[{"name": "", "qtype": 2, "qclass": 1}],
                answers=[{"name": "", "rtype": 2, "rclass": 1, "ttl": 0, "data": {"kind": "NS", "target": ""}}],
            ),
            "wire": record.pack().hex(),
        }
    )

    # maximum-domain-name: a question name at exactly RFC 1035's 255-wire-byte limit.
    labels = ["a" * 63, "b" * 63, "c" * 63, "d" * 61]
    assert sum(len(label) + 1 for label in labels) + 1 == 255
    name = ".".join(labels)
    header = dnslib.DNSHeader(id=40962, bitmap=0, rd=True)
    record = dnslib.DNSRecord(header, q=dnslib.DNSQuestion(name, qtype=1))
    cases.append(
        {
            "name": "scenario-maximum-domain-name",
            "coverage": {"family": "scenario", "scenario": "maximum-domain-name"},
            "packet": empty_packet(
                40962, flags_dict(rd=True), questions=[{"name": name, "qtype": 1, "qclass": 1}]
            ),
            "wire": record.pack().hex(),
        }
    )

    # maximum-txt-segment: one 255-byte chunk (the character-string length
    # limit), one empty chunk, and one short binary chunk.
    strings = [b"x" * 255, b"", b"\x00\xff"]
    header = dnslib.DNSHeader(id=40963, bitmap=0, qr=True, aa=True)
    record = dnslib.DNSRecord(header)
    record.add_answer(dnslib.RR(rname="txt.example.test", rtype=16, rclass=1, ttl=300, rdata=dnslib.TXT(strings)))
    cases.append(
        {
            "name": "scenario-maximum-txt-segment",
            "coverage": {"family": "scenario", "scenario": "maximum-txt-segment"},
            "packet": empty_packet(
                40963,
                flags_dict(qr=True, aa=True),
                answers=[
                    {
                        "name": "txt.example.test",
                        "rtype": 16,
                        "rclass": 1,
                        "ttl": 300,
                        "data": {"kind": "TXT", "strings": [hexs(s) for s in strings]},
                    }
                ],
            ),
            "wire": record.pack().hex(),
        }
    )

    # soa-boundaries: every numeric SOA field at its wire minimum or maximum,
    # in the authority section (a SOA's usual home in a real response).
    header = dnslib.DNSHeader(id=40964, bitmap=0, qr=True, aa=True)
    record = dnslib.DNSRecord(header)
    record.add_auth(
        dnslib.RR(
            rname="example.test",
            rtype=6,
            rclass=1,
            ttl=4294967295,
            rdata=dnslib.SOA("ns.example.test", "hostmaster.example.test", (0, 4294967295, 0, 4294967295, 0)),
        )
    )
    cases.append(
        {
            "name": "scenario-soa-boundaries",
            "coverage": {"family": "scenario", "scenario": "soa-boundaries"},
            "packet": empty_packet(
                40964,
                flags_dict(qr=True, aa=True),
                authorities=[
                    {
                        "name": "example.test",
                        "rtype": 6,
                        "rclass": 1,
                        "ttl": 4294967295,
                        "data": {
                            "kind": "SOA",
                            "mname": "ns.example.test",
                            "rname": "hostmaster.example.test",
                            "serial": 0,
                            "refresh": 4294967295,
                            "retry": 0,
                            "expire": 4294967295,
                            "minimum": 0,
                        },
                    }
                ],
            ),
            "wire": record.pack().hex(),
        }
    )

    # scalar-boundaries: every other scalar (TTL, IPv4/IPv6 address, MX
    # preference) at its wire minimum or maximum, alongside a header with
    # every flag/opcode/rcode/z field maxed out too.
    header = dnslib.DNSHeader(id=65535, bitmap=0, qr=True, opcode=15, aa=True, tc=True, rd=True, ra=True, rcode=15)
    header.z, header.ad, header.cd = 1, 1, 1
    record = dnslib.DNSRecord(header)
    boundary_rrs = [
        ("zero.example.test", 1, 0, dnslib.A("0.0.0.0"), {"kind": "A", "address": "0.0.0.0"}),
        ("max.example.test", 1, 4294967295, dnslib.A("255.255.255.255"), {"kind": "A", "address": "255.255.255.255"}),
        ("v6-zero.example.test", 28, 0, dnslib.AAAA("::"), {"kind": "AAAA", "address": "::"}),
        (
            "v6-max.example.test",
            28,
            4294967295,
            dnslib.AAAA("ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"),
            {"kind": "AAAA", "address": "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"},
        ),
        ("mx-zero.example.test", 15, 0, dnslib.MX("", 0), {"kind": "MX", "preference": 0, "exchange": ""}),
    ]
    answers_json = []
    for name, rtype, ttl, rdata, data in boundary_rrs:
        record.add_answer(dnslib.RR(rname=name, rtype=rtype, rclass=1, ttl=ttl, rdata=rdata))
        answers_json.append({"name": name, "rtype": rtype, "rclass": 1, "ttl": ttl, "data": data})
    cases.append(
        {
            "name": "scenario-scalar-boundaries",
            "coverage": {"family": "scenario", "scenario": "scalar-boundaries"},
            "packet": empty_packet(
                65535,
                flags_dict(qr=True, opcode=15, aa=True, tc=True, rd=True, ra=True, z=7, rcode=15),
                answers=answers_json,
            ),
            "wire": record.pack().hex(),
        }
    )

    # all-questions-compressed: every (qtype, qclass) combination as its own
    # question in one message, so the shared ".example.test" suffix compresses.
    header = dnslib.DNSHeader(id=40965, bitmap=0, rd=True)
    questions = [
        (f"all-{qtype}-{qclass}.example.test", qtype, qclass) for qtype in QUESTION_TYPES for qclass in DNS_CLASSES
    ]
    record = dnslib.DNSRecord(
        header, questions=[dnslib.DNSQuestion(name, qtype=qtype, qclass=qclass) for name, qtype, qclass in questions]
    )
    cases.append(
        {
            "name": "scenario-all-questions-compressed",
            "coverage": {"family": "scenario", "scenario": "all-questions-compressed"},
            "packet": empty_packet(
                40965,
                flags_dict(rd=True),
                questions=[{"name": name, "qtype": qtype, "qclass": qclass} for name, qtype, qclass in questions],
            ),
            "wire": record.pack().hex(),
        }
    )

    # all-rdata-all-sections: every implemented RDATA type, spread across
    # all three sections, in one fully-populated message.
    header = dnslib.DNSHeader(id=40966, bitmap=0, qr=True, aa=True)
    record = dnslib.DNSRecord(header)
    section_json = {"answers": [], "authorities": [], "additionals": []}
    for i, rtype in enumerate(ALL_RDATA_TYPES):
        section = SECTIONS[i % 3]
        rr, rr_json = build_rr(rtype, 1, 300)
        add_to_section(record, section, rr)
        section_json[section].append(rr_json)
    cases.append(
        {
            "name": "scenario-all-rdata-all-sections",
            "coverage": {"family": "scenario", "scenario": "all-rdata-all-sections"},
            "packet": empty_packet(40966, flags_dict(qr=True, aa=True), **section_json),
            "wire": record.pack().hex(),
        }
    )

    return cases


def main():
    cases = gen_flag_bits() + gen_opcode_rcode() + gen_question() + gen_rdata() + gen_scenarios()
    names = [case["name"] for case in cases]
    assert len(names) == len(set(names)), "duplicate fixture case name"

    fixture = {"schema": SCHEMA, "generated_by": GENERATED_BY, "cases": cases}
    FIXTURE_PATH.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(cases)} cases to {FIXTURE_PATH}")


if __name__ == "__main__":
    main()
