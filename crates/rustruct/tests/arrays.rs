//! `array`: `count`/`until_eof`, limits, nested elements, and lexical
//! up-references from inside an element.

mod common;

use common::*;
use rustruct_core::compile::{compile, Options};
use rustruct_core::error::Kind;
use rustruct_core::program::IntPrim;
use rustruct_core::schema::{ExprIn, FieldIn, TypeIn};
use rustruct_core::value::Value;

#[test]
fn array_count_roundtrip() {
    let prog = build(vec![
        f("n", int(IntPrim::U8)),
        f(
            "items",
            TypeIn::ArrayT {
                elem: Box::new(int(IntPrim::U16)),
                count: Some(r("n")),
                until_eof: false,
            },
        ),
    ]);
    let v = roundtrip(&prog, &[2, 0x01, 0x02, 0x03, 0x04]);
    assert_eq!(
        v.get("items"),
        Some(&Value::List(vec![Value::Int(0x0102), Value::Int(0x0304)]))
    );
    // derived count
    let packed = pack_ok(
        &prog,
        &map(vec![(
            "items",
            Value::List(vec![Value::Int(7), Value::Int(8), Value::Int(9)]),
        )]),
    );
    assert_eq!(packed, [3, 0, 7, 0, 8, 0, 9]);
}

#[test]
fn empty_array_roundtrip() {
    let prog = build(vec![
        f("n", int(IntPrim::U8)),
        f(
            "items",
            TypeIn::ArrayT {
                elem: Box::new(int(IntPrim::U8)),
                count: Some(r("n")),
                until_eof: false,
            },
        ),
    ]);
    let v = roundtrip(&prog, &[0]);
    assert_eq!(v.get("items"), Some(&Value::List(vec![])));
}

#[test]
fn array_until_eof_within_window() {
    let inner = TypeIn::StructT {
        fields: vec![f(
            "items",
            TypeIn::ArrayT {
                elem: Box::new(int(IntPrim::U16)),
                count: None,
                until_eof: true,
            },
        )],
        byteorder: None,
        size: Some(r("len")),
    };
    let prog = build(vec![f("len", int(IntPrim::U8)), f("body", inner)]);
    let v = roundtrip(&prog, &[4, 0, 1, 0, 2]);
    let body = v.get("body").unwrap();
    assert_eq!(
        body.get("items"),
        Some(&Value::List(vec![Value::Int(1), Value::Int(2)]))
    );

    // an element doesn't end exactly on the window boundary -> truncated inside the window
    let (kind, _) = unpack_err(&prog, &[3, 0, 1, 0, 9]);
    assert_eq!(kind, Kind::Truncated);
}

#[test]
fn array_greedy_count_is_until_eof() {
    // count="*" is sugar for until_eof=true (compile.rs treats ExprIn::Greedy
    // in the count position the same way).
    let inner = TypeIn::StructT {
        fields: vec![f(
            "items",
            TypeIn::ArrayT {
                elem: Box::new(int(IntPrim::U8)),
                count: Some(ExprIn::Greedy),
                until_eof: false,
            },
        )],
        byteorder: None,
        size: Some(r("len")),
    };
    let prog = build(vec![f("len", int(IntPrim::U8)), f("body", inner)]);
    let v = roundtrip(&prog, &[3, 7, 8, 9]);
    assert_eq!(
        v.get("body").unwrap().get("items"),
        Some(&Value::List(vec![
            Value::Int(7),
            Value::Int(8),
            Value::Int(9)
        ]))
    );
}

#[test]
fn array_count_limit_checked_before_alloc() {
    let prog = compile(
        &[
            f("n", int(IntPrim::U32)),
            f(
                "items",
                TypeIn::ArrayT {
                    elem: Box::new(int(IntPrim::U8)),
                    count: Some(r("n")),
                    until_eof: false,
                },
            ),
        ],
        &Options {
            max_count: 1000,
            ..Options::default()
        },
    )
    .unwrap();
    let (kind, path) = unpack_err(&prog, &[0xFF, 0xFF, 0xFF, 0xFF, 1, 2, 3]);
    assert_eq!(kind, Kind::Limit);
    assert_eq!(path, "items");
}

#[test]
fn array_until_eof_limit() {
    let inner = TypeIn::StructT {
        fields: vec![f(
            "items",
            TypeIn::ArrayT {
                elem: Box::new(int(IntPrim::U8)),
                count: None,
                until_eof: true,
            },
        )],
        byteorder: None,
        size: Some(r("len")),
    };
    let prog = compile(
        &[f("len", int(IntPrim::U8)), f("body", inner)],
        &Options {
            max_count: 4,
            ..Options::default()
        },
    )
    .unwrap();
    let (kind, _) = unpack_err(&prog, &[5, 1, 2, 3, 4, 5]);
    assert_eq!(kind, Kind::Limit);
}

#[test]
fn nested_arrays_roundtrip() {
    let row = TypeIn::ArrayT {
        elem: Box::new(int(IntPrim::U8)),
        count: Some(ExprIn::Imm(2)),
        until_eof: false,
    };
    let prog = build(vec![f(
        "rows",
        TypeIn::ArrayT {
            elem: Box::new(row),
            count: Some(ExprIn::Imm(2)),
            until_eof: false,
        },
    )]);
    let v = roundtrip(&prog, &[1, 2, 3, 4]);
    assert_eq!(
        v.get("rows"),
        Some(&Value::List(vec![
            Value::List(vec![Value::Int(1), Value::Int(2)]),
            Value::List(vec![Value::Int(3), Value::Int(4)]),
        ]))
    );
}

#[test]
fn array_element_const_mismatch_reports_index() {
    let elem = TypeIn::Int {
        prim: IntPrim::U8,
        byteorder: None,
        const_: Some(0xAB),
    };
    let prog = build(vec![f(
        "items",
        TypeIn::ArrayT {
            elem: Box::new(elem),
            count: Some(ExprIn::Imm(3)),
            until_eof: false,
        },
    )]);
    let (kind, path) = unpack_err(&prog, &[0xAB, 0xAB, 0xFF]);
    assert_eq!(kind, Kind::Const);
    assert_eq!(path, "items[2]");
}

/// A ref from inside an array element (a nested struct) may
/// reach a field of an enclosing scope; the up-count crosses the element's
/// own struct frame plus the array's enclosing frame.
#[test]
fn array_element_ref_reaches_enclosing_scope() {
    let elem = TypeIn::StructT {
        fields: vec![f(
            "value",
            TypeIn::Bytes {
                len: r("n"),
                max: None,
            },
        )],
        byteorder: None,
        size: None,
    };
    let prog = build(vec![
        f("n", int(IntPrim::U8)),
        f(
            "items",
            TypeIn::ArrayT {
                elem: Box::new(elem),
                count: Some(ExprIn::Imm(2)),
                until_eof: false,
            },
        ),
    ]);
    let buf = [2, b'a', b'b', b'c', b'd'];
    let v = roundtrip(&prog, &buf);
    let items = v.get("items").unwrap();
    let Value::List(rows) = items else { panic!() };
    assert_eq!(rows[0].get("value"), Some(&Value::Bytes(b"ab".to_vec())));
    assert_eq!(rows[1].get("value"), Some(&Value::Bytes(b"cd".to_vec())));
}

#[test]
fn array_count_and_until_eof_mutually_exclusive_is_schema_error() {
    let e = compile(
        &[FieldIn::named(
            "items",
            TypeIn::ArrayT {
                elem: Box::new(int(IntPrim::U8)),
                count: Some(ExprIn::Imm(1)),
                until_eof: true,
            },
        )],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("mutually exclusive"), "{}", e.msg);
}

#[test]
fn array_missing_count_and_until_eof_is_schema_error() {
    let e = compile(
        &[FieldIn::named(
            "items",
            TypeIn::ArrayT {
                elem: Box::new(int(IntPrim::U8)),
                count: None,
                until_eof: false,
            },
        )],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("count or until_eof"), "{}", e.msg);
}
