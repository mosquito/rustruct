//! `flags`: bit masks over a decoded integer, the `rest` policy, and the
//! closed key set on pack.

mod common;

use common::*;
use rustruct_core::compile::{compile, Options};
use rustruct_core::error::Kind;
use rustruct_core::program::IntPrim;
use rustruct_core::schema::{ByteOrder, TypeIn};
use rustruct_core::value::Value;

#[test]
fn flags_roundtrip() {
    let prog = build(vec![f(
        "fl",
        TypeIn::FlagsT {
            base: IntPrim::U16,
            byteorder: Some(ByteOrder::Little),
            names: vec![
                ("ack".into(), 0x0001),
                ("syn".into(), 0x0002),
                ("kind".into(), 0x00F0),
            ],
            rest: "keep".into(),
        },
    )]);
    let v = roundtrip(&prog, &[0x53, 0x01]);
    let fl = v.get("fl").unwrap();
    assert_eq!(fl.get("ack"), Some(&Value::Bool(true)));
    assert_eq!(fl.get("syn"), Some(&Value::Bool(true)));
    assert_eq!(fl.get("kind"), Some(&Value::Int(5)));
    assert_eq!(fl.get("_rest"), Some(&Value::Int(0x0100)));

    // missing keys default to 0/false; an unknown key is unknown_flag
    let packed = pack_ok(
        &prog,
        &map(vec![("fl", map(vec![("ack", Value::Bool(true))]))]),
    );
    assert_eq!(packed, [0x01, 0x00]);
    let (kind, path) = pack_err(
        &prog,
        &map(vec![("fl", map(vec![("typo", Value::Bool(true))]))]),
    );
    assert_eq!(kind, Kind::UnknownFlag);
    assert_eq!(path, "fl");
}

#[test]
fn flags_u32_little_endian() {
    let prog = build(vec![f(
        "fl",
        TypeIn::FlagsT {
            base: IntPrim::U32,
            byteorder: Some(ByteOrder::Little),
            names: vec![("low".into(), 0x0000_00FF), ("high".into(), 0xFF00_0000)],
            rest: "ignore".into(),
        },
    )]);
    let v = roundtrip(&prog, &[0x42, 0x00, 0x00, 0x80]);
    let fl = v.get("fl").unwrap();
    assert_eq!(fl.get("low"), Some(&Value::Int(0x42)));
    assert_eq!(fl.get("high"), Some(&Value::Int(0x80)));
}

#[test]
fn flags_strict_reserved_bits() {
    let prog = build(vec![f(
        "fl",
        TypeIn::FlagsT {
            base: IntPrim::U8,
            byteorder: None,
            names: vec![("a".into(), 0x01)],
            rest: "strict".into(),
        },
    )]);
    let (kind, _) = unpack_err(&prog, &[0x81]);
    assert_eq!(kind, Kind::ReservedBits);
    assert_eq!(
        unpack_ok(&prog, &[0x01]).get("fl").unwrap().get("a"),
        Some(&Value::Bool(true))
    );
}

#[test]
fn flags_ignore_policy_drops_leftover_both_ways() {
    let prog = build(vec![f(
        "fl",
        TypeIn::FlagsT {
            base: IntPrim::U8,
            byteorder: None,
            names: vec![("a".into(), 0x01)],
            rest: "ignore".into(),
        },
    )]);
    let v = unpack_ok(&prog, &[0xFF]);
    let fl = v.get("fl").unwrap();
    assert_eq!(fl.get("a"), Some(&Value::Bool(true)));
    assert_eq!(
        fl.get("_rest"),
        None,
        "ignore policy must not surface a _rest key"
    );
    // pack always writes zero for the ignored bits, regardless of the source byte
    assert_eq!(pack_ok(&prog, &v), [0x01]);
}

#[test]
fn flags_validation_errors() {
    // a non-contiguous mask
    let e = compile(
        &[f(
            "fl",
            TypeIn::FlagsT {
                base: IntPrim::U8,
                byteorder: None,
                names: vec![("a".into(), 0b101)],
                rest: "keep".into(),
            },
        )],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("non-contiguous"), "{}", e.msg);
    // overlap
    let e = compile(
        &[f(
            "fl",
            TypeIn::FlagsT {
                base: IntPrim::U8,
                byteorder: None,
                names: vec![("a".into(), 0b11), ("b".into(), 0b10)],
                rest: "keep".into(),
            },
        )],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("overlaps"), "{}", e.msg);
    // ref to flags
    let e = compile(
        &[
            f(
                "fl",
                TypeIn::FlagsT {
                    base: IntPrim::U8,
                    byteorder: None,
                    names: vec![("a".into(), 1)],
                    rest: "keep".into(),
                },
            ),
            f(
                "data",
                TypeIn::Bytes {
                    len: r("fl"),
                    max: None,
                },
            ),
        ],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("flags"), "{}", e.msg);
}

#[test]
fn flags_mask_out_of_base_range_is_schema_error() {
    let e = compile(
        &[f(
            "fl",
            TypeIn::FlagsT {
                base: IntPrim::U8,
                byteorder: None,
                names: vec![("a".into(), 0x100)],
                rest: "keep".into(),
            },
        )],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("does not fit"), "{}", e.msg);
}

#[test]
fn flags_reserved_rest_name_is_schema_error() {
    let e = compile(
        &[f(
            "fl",
            TypeIn::FlagsT {
                base: IntPrim::U8,
                byteorder: None,
                names: vec![("_rest".into(), 1)],
                rest: "keep".into(),
            },
        )],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("_rest"), "{}", e.msg);
}
