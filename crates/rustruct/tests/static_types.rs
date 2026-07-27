//! Fixed-width fields: coalescing into a single `Op::Fixed`, endianness,
//! const/magic fields, unnamed padding.

mod common;

use common::*;
use rustruct_core::compile::{compile, Options};
use rustruct_core::error::Kind;
use rustruct_core::program::{IntPrim, Op};
use rustruct_core::schema::{ByteOrder, FieldIn, TypeIn};
use rustruct_core::value::Value;

/// A schema with no dynamic parts must lower to exactly one Fixed op.
#[test]
fn static_schema_is_single_fixed() {
    let prog = build(vec![
        f("a", int(IntPrim::U8)),
        f("b", int(IntPrim::U16)),
        f("c", int(IntPrim::I32)),
        f("d", int(IntPrim::U64)),
        f(
            "e",
            TypeIn::Float {
                is64: false,
                byteorder: None,
            },
        ),
        f(
            "g",
            TypeIn::Float {
                is64: true,
                byteorder: None,
            },
        ),
        f("h", TypeIn::Bool { const_: None }),
        f("i", int(IntPrim::I8)),
    ]);
    assert_eq!(
        prog.ops.len(),
        1,
        "a static schema must produce a single Fixed op"
    );
    assert!(matches!(prog.ops[0], Op::Fixed { .. }));
    assert_eq!(prog.static_size, Some(1 + 2 + 4 + 8 + 4 + 8 + 1 + 1));
    assert_eq!(prog.min_size, 29);
}

#[test]
fn static_roundtrip_and_endianness() {
    let fields = vec![
        f("be16", int(IntPrim::U16)),
        f(
            "le16",
            TypeIn::Int {
                prim: IntPrim::U16,
                byteorder: Some(ByteOrder::Little),
                const_: None,
            },
        ),
        f("i8", int(IntPrim::I8)),
    ];
    let prog = build(fields);
    let buf = [0x12, 0x34, 0x34, 0x12, 0xFF];
    let v = roundtrip(&prog, &buf);
    assert_eq!(v.get("be16"), Some(&Value::Int(0x1234)));
    assert_eq!(v.get("le16"), Some(&Value::Int(0x1234)));
    assert_eq!(v.get("i8"), Some(&Value::Int(-1)));
}

#[test]
fn little_endian_context_inherited() {
    let prog = compile(
        &[f("x", int(IntPrim::U32))],
        &Options {
            byteorder: ByteOrder::Little,
            ..Options::default()
        },
    )
    .unwrap();
    let v = unpack_ok(&prog, &[0x78, 0x56, 0x34, 0x12]);
    assert_eq!(v.get("x"), Some(&Value::Int(0x12345678)));
}

#[test]
fn field_byteorder_overrides_scope_default() {
    // Field-level byteorder beats the compile()-level default.
    let prog = compile(
        &[f(
            "x",
            TypeIn::Int {
                prim: IntPrim::U16,
                byteorder: Some(ByteOrder::Big),
                const_: None,
            },
        )],
        &Options {
            byteorder: ByteOrder::Little,
            ..Options::default()
        },
    )
    .unwrap();
    let v = unpack_ok(&prog, &[0x12, 0x34]);
    assert_eq!(v.get("x"), Some(&Value::Int(0x1234)));
}

#[test]
fn negative_ints_roundtrip() {
    let prog = build(vec![f("a", int(IntPrim::I8)), f("b", int(IntPrim::I64))]);
    let v = roundtrip(&prog, &[0x80, 0x80, 0, 0, 0, 0, 0, 0, 0]);
    assert_eq!(v.get("a"), Some(&Value::Int(-128)));
    assert_eq!(v.get("b"), Some(&Value::Int(i128::from(i64::MIN))));
}

#[test]
fn float_roundtrip_both_endians() {
    let prog = build(vec![
        f(
            "be",
            TypeIn::Float {
                is64: false,
                byteorder: None,
            },
        ),
        f(
            "le",
            TypeIn::Float {
                is64: true,
                byteorder: Some(ByteOrder::Little),
            },
        ),
    ]);
    let mut buf = 1.5f32.to_be_bytes().to_vec();
    buf.extend_from_slice(&(-2.5f64).to_le_bytes());
    let v = roundtrip(&prog, &buf);
    assert_eq!(v.get("be"), Some(&Value::Float(1.5)));
    assert_eq!(v.get("le"), Some(&Value::Float(-2.5)));
}

#[test]
fn trailing_is_error_for_unpack() {
    let prog = build(vec![f("a", int(IntPrim::U8))]);
    let (kind, _) = unpack_err(&prog, &[1, 2]);
    assert_eq!(kind, Kind::Trailing);
    // unpack_from-style call (exact=false) allows a tail.
    match rustruct_core::unpack::run(&prog, &[1, 2], 0, false, false) {
        rustruct_core::unpack::Outcome::Ok { pos, .. } => assert_eq!(pos, 1),
        other => panic!("{other:?}"),
    }
}

#[test]
fn const_magic() {
    let prog = build(vec![
        FieldIn::anon(TypeIn::Raw {
            len: None,
            const_: Some(b"PNG".to_vec()),
        }),
        f(
            "ver",
            TypeIn::Int {
                prim: IntPrim::U8,
                byteorder: None,
                const_: Some(1),
            },
        ),
        f("x", int(IntPrim::U8)),
    ]);
    let v = roundtrip(&prog, b"PNG\x01\x2A");
    // a named const field is still surfaced in the dict
    assert_eq!(v.get("ver"), Some(&Value::Int(1)));
    assert_eq!(v.get("magic"), None);

    let (kind, _) = unpack_err(&prog, b"PNQ\x01\x2A");
    assert_eq!(kind, Kind::Const);
    let (kind, path) = unpack_err(&prog, b"PNG\x02\x2A");
    assert_eq!(kind, Kind::Const);
    assert_eq!(path, "ver");

    // pack writes the const from the schema; input is ignored
    let packed = pack_ok(
        &prog,
        &map(vec![("x", Value::Int(0x2A)), ("ver", Value::Int(99))]),
    );
    assert_eq!(packed, b"PNG\x01\x2A");
}

#[test]
fn const_bool_roundtrip() {
    let prog = build(vec![
        f("flag", TypeIn::Bool { const_: Some(true) }),
        f("x", int(IntPrim::U8)),
    ]);
    let v = roundtrip(&prog, &[0x01, 7]);
    assert_eq!(v.get("flag"), Some(&Value::Bool(true)));
    let (kind, _) = unpack_err(&prog, &[0x00, 7]);
    assert_eq!(kind, Kind::Const);
}

#[test]
fn unnamed_padding_roundtrip() {
    let prog = build(vec![
        f("a", int(IntPrim::U8)),
        FieldIn::anon(TypeIn::Raw {
            len: Some(3),
            const_: None,
        }),
        f("b", int(IntPrim::U8)),
    ]);
    let v = unpack_ok(&prog, &[1, 9, 9, 9, 2]);
    assert_eq!(v, map(vec![("a", Value::Int(1)), ("b", Value::Int(2))]));
    // pack: padding is written as zeros
    let packed = pack_ok(&prog, &v);
    assert_eq!(packed, [1, 0, 0, 0, 2]);
}

#[test]
fn raw_const_and_len_mismatch_is_schema_error() {
    let e = compile(
        &[f(
            "magic",
            TypeIn::Raw {
                len: Some(4),
                const_: Some(b"AB".to_vec()),
            },
        )],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("const"), "{}", e.msg);
}

#[test]
fn extra_keys_ignored_on_pack() {
    let prog = build(vec![f("x", int(IntPrim::U8))]);
    let packed = pack_ok(
        &prog,
        &map(vec![("x", Value::Int(1)), ("junk", Value::Unsupported)]),
    );
    assert_eq!(packed, [1]);
}

#[test]
fn bool_pack_missing_named_field_is_error() {
    let prog = build(vec![f("b", TypeIn::Bool { const_: None })]);
    let (kind, path) = pack_err(&prog, &map(vec![]));
    assert_eq!(kind, Kind::Missing);
    assert_eq!(path, "b");
}

#[test]
fn duplicate_name_is_schema_error() {
    let e = compile(
        &[f("x", int(IntPrim::U8)), f("x", int(IntPrim::U8))],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("duplicate"), "{}", e.msg);
}

#[test]
fn min_size_lower_bound() {
    let prog = build(vec![
        f("n", int(IntPrim::U8)),
        f(
            "data",
            TypeIn::Bytes {
                len: r("n"),
                max: None,
            },
        ),
        f("tail", int(IntPrim::U16)),
    ]);
    assert_eq!(prog.min_size, 3);
    assert_eq!(prog.static_size, None);
}
