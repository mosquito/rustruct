"""switch + open registry dispatch (the `registry=True`/`key=` pattern),
and the derived/const field-exemption rules those depend on."""

import pytest

from rustruct import U8, U16, U32, Struct, described, sized, slice, switch


def make_ip_payload_hierarchy():
    class IPPayload(Struct, registry=True):
        pass

    class TCP(IPPayload, proto=6):
        src_port: U16
        dst_port: U16
        seq: U32

    class UDP(IPPayload, proto=17):
        src_port: U16
        dst_port: U16
        length: U16

    class Body(Struct):
        proto: U8
        payload: object = switch(on="proto", cases=IPPayload.dispatch_registry, default=slice(len="*"))

    class Packet(Struct):
        size: U8
        body: object = sized(Body, size="size")

    return IPPayload, TCP, UDP, Body, Packet


def test_registered_subclasses_dispatch_to_distinct_typed_instances():
    IPPayload, TCP, UDP, Body, Packet = make_ip_payload_hierarchy()

    pkt = Packet(body=Body(proto=6, payload=TCP(src_port=80, dst_port=22, seq=1)))
    wire = pkt.pack()
    back = Packet.unpack(wire)
    assert isinstance(back.body.payload, TCP)
    assert back.body.payload == TCP(src_port=80, dst_port=22, seq=1)

    udp_pkt = Packet(body=Body(proto=17, payload=UDP(src_port=53, dst_port=53, length=8)))
    back2 = Packet.unpack(udp_pkt.pack())
    assert isinstance(back2.body.payload, UDP)
    assert back2.body.payload == UDP(src_port=53, dst_port=53, length=8)


def test_unregistered_tag_falls_back_to_raw_bytes():
    IPPayload, TCP, UDP, Body, Packet = make_ip_payload_hierarchy()

    pkt = Packet(body=Body(proto=253, payload=b"\xaa\xbb\xcc\xdd"))
    wire = pkt.pack()
    back = Packet.unpack(wire)
    assert back.body.proto == 253
    assert back.body.payload == b"\xaa\xbb\xcc\xdd"


def test_registration_is_eager_at_class_definition_time():
    IPPayload, TCP, UDP, Body, Packet = make_ip_payload_hierarchy()
    assert dict(IPPayload.dispatch_registry.items()) == {6: TCP, 17: UDP}
    assert TCP.registry_key == 6
    assert UDP.registry_key == 17


def test_registry_without_base_marker_raises():
    class Plain(Struct):
        pass

    with pytest.raises(TypeError, match="no registry found"):

        class Sub(Plain, key=1):
            pass


def test_registry_rejects_multiple_kwargs():
    class Base(Struct, registry=True):
        pass

    with pytest.raises(TypeError, match="exactly one"):

        class Sub(Base, a=1, b=2):
            pass


def test_switch_discriminant_is_not_exempted_as_derived():
    """Regression test: a switch's `on=` field must stay an ordinary,
    required, caller-supplied field -- referencing it from `on=` must NOT
    make it "derived" the way referencing it from a bytes/array `len=`/
    `count=` would. Confusing the two caused `to_mapping()` to silently drop
    the discriminant field, and Codec.pack() to fail with
    PackError(kind="missing", path="proto")."""

    class Body(Struct):
        proto: U8
        payload: object = switch(on="proto", cases={1: U8}, default=slice(len="*"))

    # `proto` must be required in __init__ -- NOT silently defaulted.
    with pytest.raises(TypeError, match="proto"):
        Body(payload=b"\x01")

    b = Body(proto=1, payload=7)
    mapping = b.to_mapping()
    assert "proto" in mapping, "the discriminant must not be dropped from the packed mapping"
    assert mapping["proto"] == 1

    wire = b.pack()
    assert wire == b"\x01\x07"
    assert Body.unpack(wire) == b


def test_derived_and_const_fields_are_not_required():
    class Rec(Struct):
        magic: U8 = described(default=0)
        n: U8
        data: bytes = slice(len="n")

    # `n` is derived (referenced by data's len=) and must not be required.
    r = Rec(data=b"xyz")
    assert r.pack() == b"\x00\x03xyz"
    assert "n" not in r.to_mapping()


def test_sized_struct_size_field_not_required():
    class Body(Struct):
        payload: bytes = slice(len="*")

    class Packet(Struct):
        size: U8
        body: object = sized(Body, size="size")

    pkt = Packet(body=Body(payload=b"ab"))
    assert "size" not in pkt.to_mapping()
    assert pkt.pack() == b"\x02ab"


def test_roundtrip_of_roundtrip_is_stable():
    """Derived-field placeholders (None before the first real pack) make a
    freshly-constructed instance compare unequal to its own round-trip; two
    independently unpacked instances of the same bytes must still compare
    equal to each other."""
    IPPayload, TCP, UDP, Body, Packet = make_ip_payload_hierarchy()
    pkt = Packet(body=Body(proto=6, payload=TCP(src_port=80, dst_port=22, seq=1)))
    once = Packet.unpack(pkt.pack())
    twice = Packet.unpack(once.pack())
    assert once == twice
    assert once.body == pkt.body
