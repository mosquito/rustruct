//! `switch`: tagged unions with an explicit discriminant in the mapping at
//! pack time.

mod common;

use common::*;
use rustruct_core::compile::{compile, Options};
use rustruct_core::error::Kind;
use rustruct_core::program::IntPrim;
use rustruct_core::schema::{ExprIn, TypeIn};
use rustruct_core::value::Value;

#[test]
fn switch_roundtrip() {
    let prog = build(vec![
        f("kind", int(IntPrim::U8)),
        f(
            "body",
            TypeIn::SwitchT {
                on: r("kind"),
                cases: vec![
                    (1, int(IntPrim::U16)),
                    (
                        2,
                        TypeIn::Bytes {
                            len: ExprIn::Imm(3),
                            max: None,
                        },
                    ),
                ],
                default: Some(Box::new(int(IntPrim::U8))),
            },
        ),
    ]);
    let v = roundtrip(&prog, &[1, 0xAB, 0xCD]);
    assert_eq!(v.get("body"), Some(&Value::Int(0xABCD)));
    let v = roundtrip(&prog, &[2, b'x', b'y', b'z']);
    assert_eq!(v.get("body"), Some(&Value::Bytes(b"xyz".to_vec())));
    let v = roundtrip(&prog, &[9, 0x7F]);
    assert_eq!(v.get("body"), Some(&Value::Int(0x7F)));
}

#[test]
fn switch_default_only() {
    let prog = build(vec![
        f("kind", int(IntPrim::U8)),
        f(
            "body",
            TypeIn::SwitchT {
                on: r("kind"),
                cases: vec![],
                default: Some(Box::new(int(IntPrim::U8))),
            },
        ),
    ]);
    let v = roundtrip(&prog, &[5, 0xAA]);
    assert_eq!(v.get("body"), Some(&Value::Int(0xAA)));
}

#[test]
fn switch_negative_tag() {
    let prog = build(vec![
        f("kind", int(IntPrim::I8)),
        f(
            "body",
            TypeIn::SwitchT {
                on: r("kind"),
                cases: vec![(-1, int(IntPrim::U8))],
                default: None,
            },
        ),
    ]);
    let v = roundtrip(&prog, &[0xFF, 0x42]);
    assert_eq!(v.get("body"), Some(&Value::Int(0x42)));
}

#[test]
fn switch_no_case() {
    let prog = build(vec![
        f("kind", int(IntPrim::U8)),
        f(
            "body",
            TypeIn::SwitchT {
                on: r("kind"),
                cases: vec![(1, int(IntPrim::U8))],
                default: None,
            },
        ),
    ]);
    let (kind, path) = unpack_err(&prog, &[5, 1]);
    assert_eq!(kind, Kind::NoCase);
    assert_eq!(path, "body");
}

#[test]
fn field_used_as_both_a_length_and_a_switch_discriminant_stays_explicit() {
    // "n" drives both data's length and body's own tag -- a case a bit-packed
    // format like MessagePack hits often (the same bits are simultaneously a
    // count and a type selector across different branches). It can't become
    // a derived field (that would make it impossible to know which switch
    // branch to write before the switch itself has run), so it stays an
    // ordinary, caller-supplied field; a length/count/size reference to it
    // just checks consistency against the given value instead.
    let prog = build(vec![
        f("n", int(IntPrim::U8)),
        f(
            "data",
            TypeIn::Bytes {
                len: r("n"),
                max: None,
            },
        ),
        f(
            "body",
            TypeIn::SwitchT {
                on: r("n"),
                cases: vec![(1, int(IntPrim::U8))],
                default: None,
            },
        ),
    ]);

    let consistent = map(vec![
        ("n", Value::Int(1)),
        ("data", Value::Bytes(vec![0xAB])),
        ("body", Value::Int(7)),
    ]);
    let buf = pack_ok(&prog, &consistent);
    assert_eq!(buf, vec![1, 0xAB, 7]);
    let back = unpack_ok(&prog, &buf);
    assert_eq!(back, consistent);

    let inconsistent = map(vec![
        ("n", Value::Int(1)),
        ("data", Value::Bytes(vec![0xAB, 0xCD])),
        ("body", Value::Int(7)),
    ]);
    let (kind, _) = pack_err(&prog, &inconsistent);
    assert_eq!(kind, Kind::Inconsistent);
}

#[test]
fn switch_no_branches_is_schema_error() {
    let e = compile(
        &[
            f("kind", int(IntPrim::U8)),
            f(
                "body",
                TypeIn::SwitchT {
                    on: r("kind"),
                    cases: vec![],
                    default: None,
                },
            ),
        ],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("no branches"), "{}", e.msg);
}

#[test]
fn switch_duplicate_branch_is_schema_error() {
    let e = compile(
        &[
            f("kind", int(IntPrim::U8)),
            f(
                "body",
                TypeIn::SwitchT {
                    on: r("kind"),
                    cases: vec![(1, int(IntPrim::U8)), (1, int(IntPrim::U16))],
                    default: None,
                },
            ),
        ],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("duplicate branch"), "{}", e.msg);
}

#[test]
fn array_of_switch_elements() {
    let elem = TypeIn::SwitchT {
        on: r("tag"),
        cases: vec![(0, int(IntPrim::U8)), (1, int(IntPrim::U16))],
        default: None,
    };
    let prog = build(vec![
        f("tag", int(IntPrim::U8)),
        f(
            "items",
            TypeIn::ArrayT {
                elem: Box::new(elem),
                count: Some(ExprIn::Imm(2)),
                until_eof: false,
            },
        ),
    ]);
    // tag=1 is a fixed outer register, so both elements take the u16 branch
    let v = roundtrip(&prog, &[1, 0x00, 0x11, 0x22, 0x33]);
    assert_eq!(
        v.get("items"),
        Some(&Value::List(vec![Value::Int(0x0011), Value::Int(0x2233)]))
    );
}

/// The realistic dispatch shape: each branch is a struct with its own,
/// unrelated set of named fields (not just a different primitive type).
/// This is the "parse one way or another depending on a field's value,
/// with different fields and values in the result dict" scenario.
#[test]
fn switch_branches_are_differently_shaped_structs() {
    let tcp_like = TypeIn::StructT {
        fields: vec![
            f("src_port", int(IntPrim::U16)),
            f("dst_port", int(IntPrim::U16)),
            f("seq", int(IntPrim::U32)),
        ],
        byteorder: None,
        size: None,
    };
    let udp_like = TypeIn::StructT {
        fields: vec![
            f("src_port", int(IntPrim::U16)),
            f("dst_port", int(IntPrim::U16)),
            f("length", int(IntPrim::U16)),
        ],
        byteorder: None,
        size: None,
    };
    let unknown = TypeIn::Bytes {
        len: ExprIn::Greedy,
        max: None,
    };

    let body = TypeIn::StructT {
        fields: vec![
            f("proto", int(IntPrim::U8)),
            f(
                "payload",
                TypeIn::SwitchT {
                    on: r("proto"),
                    cases: vec![(6, tcp_like), (17, udp_like)],
                    default: Some(Box::new(unknown)),
                },
            ),
        ],
        byteorder: None,
        size: Some(r("size")),
    };
    let prog = build(vec![f("size", int(IntPrim::U8)), f("body", body)]);

    // proto=6 (TCP-like): payload is a dict with src_port/dst_port/seq
    // body window = proto(1) + payload(2+2+4=8) = 9 bytes
    let tcp_buf = [9, 6, 0, 80, 0, 22, 0, 0, 0, 1];
    let v = roundtrip(&prog, &tcp_buf);
    let payload = v.get("body").unwrap().get("payload").unwrap();
    assert_eq!(payload.get("src_port"), Some(&Value::Int(80)));
    assert_eq!(payload.get("dst_port"), Some(&Value::Int(22)));
    assert_eq!(payload.get("seq"), Some(&Value::Int(1)));
    assert_eq!(
        payload.get("length"),
        None,
        "UDP-only field must not leak into a TCP-shaped result"
    );

    // proto=17 (UDP-like): entirely different fields and values
    let udp_buf = [7, 17, 0, 53, 0, 53, 0, 8];
    let v = roundtrip(&prog, &udp_buf);
    let payload = v.get("body").unwrap().get("payload").unwrap();
    assert_eq!(payload.get("src_port"), Some(&Value::Int(53)));
    assert_eq!(payload.get("length"), Some(&Value::Int(8)));
    assert_eq!(
        payload.get("seq"),
        None,
        "TCP-only field must not leak into a UDP-shaped result"
    );

    // an unrecognized protocol number falls back to raw bytes (round-trippable
    // without knowing the real shape)
    let raw_buf = [5, 253, 0xAA, 0xBB, 0xCC, 0xDD];
    let v = roundtrip(&prog, &raw_buf);
    assert_eq!(
        v.get("body").unwrap().get("payload"),
        Some(&Value::Bytes(vec![0xAA, 0xBB, 0xCC, 0xDD]))
    );
}
