"""`switch`: tagged unions, explicit discriminant at pack time."""

import pytest

from helpers import u
from rustruct import InvalidDataError, SchemaError, compile


def test_switch():
    codec = compile(
        (
            u("kind", "u8"),
            u(
                "body",
                "switch",
                on=("ref", "kind"),
                cases=((1, ("u16", {})), (2, ("bytes", {"len": 3}))),
                default=("u8", {}),
            ),
        )
    )
    assert codec.unpack(b"\x01\xab\xcd") == {"kind": 1, "body": 0xABCD}
    assert codec.unpack(b"\x02xyz") == {"kind": 2, "body": b"xyz"}
    assert codec.unpack(b"\x09\x7f") == {"kind": 9, "body": 0x7F}
    # pack: an explicit discriminant in the mapping
    assert codec.pack({"kind": 1, "body": 0xABCD}) == b"\x01\xab\xcd"
    assert codec.pack({"kind": 2, "body": b"xyz"}) == b"\x02xyz"


def test_switch_default_only():
    codec = compile(
        (
            u("kind", "u8"),
            u("body", "switch", on=("ref", "kind"), cases=(), default=("u8", {})),
        )
    )
    assert codec.unpack(b"\x05\xaa") == {"kind": 5, "body": 0xAA}


def test_switch_negative_tag():
    codec = compile(
        (
            u("kind", "i8"),
            u("body", "switch", on=("ref", "kind"), cases=((-1, ("u8", {})),)),
        )
    )
    assert codec.unpack(b"\xff\x42") == {"kind": -1, "body": 0x42}


def test_switch_no_case():
    codec = compile(
        (
            u("kind", "u8"),
            u("body", "switch", on=("ref", "kind"), cases=((1, ("u8", {})),)),
        )
    )
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x05\x01")
    assert ei.value.kind == "no_case"
    assert ei.value.path == "body"


def test_switch_no_branches_is_schema_error():
    with pytest.raises(SchemaError):
        compile((u("kind", "u8"), u("body", "switch", on=("ref", "kind"), cases=())))


def test_switch_duplicate_branch_is_schema_error():
    with pytest.raises(SchemaError):
        compile(
            (
                u("kind", "u8"),
                u("body", "switch", on=("ref", "kind"), cases=((1, ("u8", {})), (1, ("u16", {})))),
            )
        )


def test_array_of_switch_elements():
    codec = compile(
        (
            u("tag", "u8"),
            u(
                "items",
                "array",
                elem=("switch", {"on": ("ref", "tag"), "cases": ((0, ("u8", {})), (1, ("u16", {})))}),
                count=2,
            ),
        )
    )
    values = codec.unpack(bytes([1, 0x00, 0x11, 0x22, 0x33]))
    assert values["items"] == [0x0011, 0x2233]


def test_switch_branches_are_differently_shaped_structs():
    """The realistic dispatch shape: each branch parses into a struct with
    its own, unrelated set of named fields -- "depending on the field's
    value, parse one way or another, with different fields and values in
    the result dict"."""
    tcp_like = (
        "struct",
        {
            "fields": (
                u("src_port", "u16"),
                u("dst_port", "u16"),
                u("seq", "u32"),
            )
        },
    )
    udp_like = (
        "struct",
        {
            "fields": (
                u("src_port", "u16"),
                u("dst_port", "u16"),
                u("length", "u16"),
            )
        },
    )
    unknown = ("bytes", {"len": "*"})

    codec = compile(
        (
            u("size", "u8"),
            u(
                "body",
                "struct",
                fields=(
                    u("proto", "u8"),
                    u(
                        "payload",
                        "switch",
                        on=("ref", "proto"),
                        cases=((6, tcp_like), (17, udp_like)),
                        default=unknown,
                    ),
                ),
                size=("ref", "size"),
            ),
        )
    )

    # proto=6 (TCP-like): payload is a dict with src_port/dst_port/seq
    tcp_buf = bytes([9, 6, 0, 80, 0, 22, 0, 0, 0, 1])
    values = codec.unpack(tcp_buf)
    payload = values["body"]["payload"]
    assert payload == {"src_port": 80, "dst_port": 22, "seq": 1}
    assert codec.pack(values) == tcp_buf

    # proto=17 (UDP-like): entirely different fields and values
    udp_buf = bytes([7, 17, 0, 53, 0, 53, 0, 8])
    values = codec.unpack(udp_buf)
    payload = values["body"]["payload"]
    assert payload == {"src_port": 53, "dst_port": 53, "length": 8}
    assert codec.pack(values) == udp_buf

    # an unrecognized protocol number falls back to raw bytes (still
    # round-trippable without knowing the real shape)
    raw_buf = bytes([5, 253, 0xAA, 0xBB, 0xCC, 0xDD])
    values = codec.unpack(raw_buf)
    assert values["body"]["payload"] == bytes([0xAA, 0xBB, 0xCC, 0xDD])
    assert codec.pack(values) == raw_buf
