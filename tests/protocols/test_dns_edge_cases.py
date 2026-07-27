"""DNS edge cases: compression details, malformed-input rejection, and
name-length limits -- a deeper pass than tests/test_protocols_dns.py's
happy-path coverage. All exceptions here are plain `ValueError` (the
hand-written domain-name codec in src/rustruct/protocols/dns.py doesn't
distinguish a `FormatError`/`Truncated` exception hierarchy; the `kind`/
`offset`-style structured errors are a rustruct.Codec feature, and domain
names deliberately bypass Codec, per that module's own docstring)."""

import pytest

from rustruct.protocols.dns import (
    CNAME,
    DNS,
    MX,
    DNSClass,
    DNSFlags,
    Question,
    ResourceRecord,
    RRType,
)

# dig example.com A, id 0x1234, recursion desired
QUERY = bytes.fromhex("123401000001000000000000") + b"\x07example\x03com\x00" + bytes.fromhex("00010001")

# the matching answer: name compressed to a pointer at offset 12, A 93.184.216.34
RESPONSE = (
    bytes.fromhex("123481800001000100000000")
    + b"\x07example\x03com\x00"
    + bytes.fromhex("00010001")
    + bytes.fromhex("c00c0001000100000e1000045db8d822")
)


def test_flags_word_layout():
    msg = DNS(id=0, flags=DNSFlags(qr=True, opcode=4, rd=True, rcode=3))
    assert msg.pack()[2:4] == b"\xa1\x03"  # 0x8000 | 4<<11 | 0x0100 | 3


def test_query_pack_matches_wire():
    msg = DNS(id=0x1234, flags=DNSFlags(rd=True), questions=[Question("example.com")])
    assert msg.pack() == QUERY


def test_query_unpack():
    msg = DNS.unpack(QUERY)
    assert msg.id == 0x1234
    assert msg.flags.rd and not msg.flags.qr
    assert len(msg.questions) == 1 and not msg.answers
    q = msg.questions[0]
    assert q.name == "example.com"
    assert q.qtype is RRType.A and q.qclass is DNSClass.IN


def test_response_repack_is_byte_identical():
    # packing compresses the answer name back into the same c00c pointer
    assert DNS.unpack(RESPONSE).pack() == RESPONSE


def test_suffix_compression_on_pack():
    msg = DNS(
        id=1,
        questions=[Question("example.com", RRType.MX)],
        answers=[ResourceRecord(name="example.com", ttl=60, data=MX(10, "mail.example.com"))],
    )
    wire = msg.pack()
    assert wire.count(b"\x07example") == 1  # question spells it out, the rest point
    back = DNS.unpack(wire)
    assert back.answers[0].name == "example.com"
    assert back.answers[0].data.exchange == "mail.example.com"


def test_compression_disabled_via_pack_kwarg():
    msg = DNS(
        id=1,
        questions=[Question("example.com")],
        answers=[ResourceRecord(name="example.com", ttl=60, data=CNAME("example.com"))],
    )
    wire = msg.pack(compress=False)
    assert wire.count(b"\x07example\x03com\x00") == 3  # every name spelled out
    assert DNS.unpack(wire) == DNS.unpack(msg.pack())


def test_unknown_rdata_rtype_survives_roundtrip():
    from rustruct.protocols.dns import UnknownRData

    msg = DNS(
        id=2,
        answers=[ResourceRecord(name="x", ttl=0, data=UnknownRData(RRType(999), b"\xde\xad"))],
    )
    back = DNS.unpack(msg.pack())
    rr = back.answers[0]
    assert rr.data.rtype == 999
    assert isinstance(rr.data, UnknownRData)
    assert rr.data.data == b"\xde\xad"


def test_root_name_and_trailing_dot():
    assert DNS.unpack(DNS(questions=[Question("")]).pack()).questions[0].name == ""
    assert DNS(questions=[Question(".")]).pack() == DNS(questions=[Question("")]).pack()
    with_dot = DNS(id=3, questions=[Question("example.com.")]).pack()
    without = DNS(id=3, questions=[Question("example.com")]).pack()
    assert with_dot == without


@pytest.mark.parametrize("name", ["example.com..", ".."])
def test_multiple_trailing_dots_are_rejected(name):
    with pytest.raises(ValueError, match="bad label length"):
        DNS(questions=[Question(name)]).pack()


def test_forward_pointer_rejected():
    # the question name is a pointer to itself
    bad = bytes.fromhex("000001000001000000000000") + b"\xc0\x0c" + bytes.fromhex("00010001")
    with pytest.raises(ValueError, match="does not point backwards"):
        DNS.unpack(bad)


def test_truncated_name():
    bad = bytes.fromhex("000001000001000000000000") + b"\x07exam"
    with pytest.raises(ValueError, match="cut label"):
        DNS.unpack(bad)


def test_truncated_compression_pointer():
    bad = bytes.fromhex("000001000001000000000000") + b"\xc0"
    with pytest.raises(ValueError, match="cut compression pointer"):
        DNS.unpack(bad)


@pytest.mark.parametrize("first", [0x40, 0x80])
def test_reserved_label_kinds_are_rejected(first):
    bad = bytes.fromhex("000001000001000000000000") + bytes([first])
    with pytest.raises(ValueError, match="unsupported label type"):
        DNS.unpack(bad)


def test_oversized_label_rejected_on_pack():
    with pytest.raises(ValueError, match="bad label length"):
        DNS(questions=[Question("a" * 64 + ".com")]).pack()


def test_oversized_domain_name_rejected_on_pack():
    name = ".".join(["a" * 63] * 4)
    with pytest.raises(ValueError, match="longer than 255"):
        DNS(questions=[Question(name)]).pack()
