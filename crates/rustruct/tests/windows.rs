//! `struct` with `size` (a window): TLV framing, ref-up out of the window,
//! trailing-data checks, nested windows.

mod common;

use common::*;
use rustruct_core::compile::{compile, Options};
use rustruct_core::error::Kind;
use rustruct_core::program::IntPrim;
use rustruct_core::schema::{ExprIn, TypeIn};
use rustruct_core::unpack::{run as unpack, Outcome};
use rustruct_core::value::Value;

#[test]
fn tlv_window_ref_up() {
    // header: tag u8, size u8; body is a `size`-byte window with bytes
    // consuming the rest of the window
    let body = TypeIn::StructT {
        fields: vec![f(
            "payload",
            TypeIn::Bytes {
                len: ExprIn::Greedy,
                max: None,
            },
        )],
        byteorder: None,
        size: Some(r("size")),
    };
    let prog = build(vec![
        f("tag", int(IntPrim::U8)),
        f("size", int(IntPrim::U8)),
        f("value", body),
    ]);
    let v = roundtrip(&prog, &[7, 3, b'a', b'b', b'c']);
    assert_eq!(v.get("tag"), Some(&Value::Int(7)));
    let value = v.get("value").unwrap();
    assert_eq!(value.get("payload"), Some(&Value::Bytes(b"abc".to_vec())));

    // pack: size is derived from the actual body
    let packed = pack_ok(
        &prog,
        &map(vec![
            ("tag", Value::Int(1)),
            (
                "value",
                map(vec![("payload", Value::Bytes(b"xyzw".to_vec()))]),
            ),
        ]),
    );
    assert_eq!(packed, [1, 4, b'x', b'y', b'z', b'w']);
}

#[test]
fn window_trailing_error() {
    // a fixed body smaller than the window -> trailing with the nested struct's path
    let body = TypeIn::StructT {
        fields: vec![f("x", int(IntPrim::U8))],
        byteorder: None,
        size: Some(r("size")),
    };
    let prog = build(vec![f("size", int(IntPrim::U8)), f("body", body)]);
    let (kind, path) = unpack_err(&prog, &[2, 1, 2]);
    assert_eq!(kind, Kind::Trailing);
    assert_eq!(path, "body");
}

#[test]
fn window_overrun_is_invalid_not_incomplete() {
    // a 1-byte window with a u16 field: hitting the window (not the buffer
    // end) is Invalid even in parse/stream mode
    let body = TypeIn::StructT {
        fields: vec![f("x", int(IntPrim::U16))],
        byteorder: None,
        size: Some(r("size")),
    };
    let prog = build(vec![f("size", int(IntPrim::U8)), f("body", body)]);
    match unpack(&prog, &[1, 0xAA, 0xBB], 0, false, true) {
        Outcome::Invalid { kind, .. } => assert_eq!(kind, Kind::Truncated),
        other => panic!("{other:?}"),
    }
}

#[test]
fn nested_windows_roundtrip() {
    let inner = TypeIn::StructT {
        fields: vec![f(
            "payload",
            TypeIn::Bytes {
                len: ExprIn::Greedy,
                max: None,
            },
        )],
        byteorder: None,
        size: Some(r("inner_size")),
    };
    let outer = TypeIn::StructT {
        fields: vec![
            f("inner_size", int(IntPrim::U8)),
            f("inner", inner),
            f("after", int(IntPrim::U8)),
        ],
        byteorder: None,
        size: Some(r("outer_size")),
    };
    let prog = build(vec![f("outer_size", int(IntPrim::U8)), f("outer", outer)]);
    // outer window: inner_size(1) + inner(2 bytes "ab") + after(1) = 4
    let buf = [4, 2, b'a', b'b', 0x2A];
    let v = roundtrip(&prog, &buf);
    let outer_v = v.get("outer").unwrap();
    assert_eq!(
        outer_v.get("inner").unwrap().get("payload"),
        Some(&Value::Bytes(b"ab".to_vec()))
    );
    assert_eq!(outer_v.get("after"), Some(&Value::Int(0x2A)));
}

#[test]
fn struct_size_matching_static_body_compiles() {
    let body = TypeIn::StructT {
        fields: vec![f("a", int(IntPrim::U8)), f("b", int(IntPrim::U8))],
        byteorder: None,
        size: Some(ExprIn::Imm(2)),
    };
    let prog = build(vec![f("body", body)]);
    let v = roundtrip(&prog, &[1, 2]);
    assert_eq!(v.get("body").unwrap().get("a"), Some(&Value::Int(1)));
}

#[test]
fn struct_size_mismatching_static_body_is_schema_error() {
    let body = TypeIn::StructT {
        fields: vec![f("a", int(IntPrim::U8)), f("b", int(IntPrim::U8))],
        byteorder: None,
        size: Some(ExprIn::Imm(3)),
    };
    let e = compile(&[f("body", body)], &Options::default()).unwrap_err();
    assert!(e.msg.contains("static body size"), "{}", e.msg);
}

#[test]
fn struct_size_greedy_is_schema_error() {
    let body = TypeIn::StructT {
        fields: vec![f("a", int(IntPrim::U8))],
        byteorder: None,
        size: Some(ExprIn::Greedy),
    };
    let e = compile(&[f("body", body)], &Options::default()).unwrap_err();
    assert!(e.msg.contains("\"*\""), "{}", e.msg);
}
