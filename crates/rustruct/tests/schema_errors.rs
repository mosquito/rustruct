//! Compile-time limits and structural validation that don't fit neatly into
//! a single feature file: register/span/expression-depth caps, nesting
//! depth, and error-path assembly.

mod common;

use common::*;
use rustruct_core::compile::{compile, Options};
use rustruct_core::error::Kind;
use rustruct_core::program::IntPrim;
use rustruct_core::schema::{BinOp, ExprIn, TypeIn};

#[test]
fn nested_error_path() {
    let inner = TypeIn::StructT {
        fields: vec![f(
            "items",
            TypeIn::ArrayT {
                elem: Box::new(TypeIn::StructT {
                    fields: vec![f(
                        "name",
                        TypeIn::StrT {
                            len: ExprIn::Imm(2),
                            max: None,
                            encoding: "utf-8".into(),
                            errors: "strict".into(),
                        },
                    )],
                    byteorder: None,
                    size: None,
                }),
                count: Some(ExprIn::Imm(2)),
                until_eof: false,
            },
        )],
        byteorder: None,
        size: None,
    };
    let prog = build(vec![f("outer", inner)]);
    let buf = [b'o', b'k', 0xC3, 0x28];
    let (kind, path) = unpack_err(&prog, &buf);
    assert_eq!(kind, Kind::Decode);
    assert_eq!(path, "outer.items[1].name");
}

/// A schema `n` structs deep, counting the one this returns.
fn nested(n: usize) -> TypeIn {
    let mut ty = TypeIn::StructT {
        fields: vec![f("x", int(IntPrim::U8))],
        byteorder: None,
        size: None,
    };
    for _ in 1..n {
        ty = TypeIn::StructT {
            fields: vec![f("s", ty)],
            byteorder: None,
            size: None,
        };
    }
    ty
}

#[test]
fn depth_limit_is_a_compile_error() {
    // Unpacking allows 64 frames and a struct costs one, so a schema past
    // that could never decode anything. It used to compile and pack
    // regardless, and only fail on the first unpack; now it is refused
    // where the problem actually is.
    let e = compile(&[f("root", nested(70))], &Options::default()).unwrap_err();
    assert!(e.msg.contains("structs deep"), "{}", e.msg);
}

#[test]
fn depth_limit_boundary() {
    // The outermost frame is the schema itself, so 63 nested structs fit
    // in the 64 and the 64th is one too many.
    let prog = build(vec![f("root", nested(63))]);
    unpack_ok(&prog, &[0]);
    let e = compile(&[f("root", nested(64))], &Options::default()).unwrap_err();
    assert!(e.msg.contains("65 structs deep"), "{}", e.msg);
}

#[test]
fn register_limit_exceeded() {
    // 17 distinct referenced integer fields exceed the 16-registers-per-scope
    // cap.
    let mut fields = Vec::new();
    for i in 0..17 {
        fields.push(f(&format!("n{i}"), int(IntPrim::U8)));
    }
    for i in 0..17 {
        fields.push(f(
            &format!("d{i}"),
            TypeIn::Bytes {
                len: r(&format!("n{i}")),
                max: None,
            },
        ));
    }
    let e = compile(&fields, &Options::default()).unwrap_err();
    assert!(e.msg.contains("16 registers"), "{}", e.msg);
}

#[test]
fn span_register_limit_exceeded() {
    // 17 distinct fields named in one digest's `over` exceed the 16
    // span-registers-per-scope cap.
    let mut fields = Vec::new();
    let mut over_names = Vec::new();
    for i in 0..17 {
        let name = format!("r{i}");
        fields.push(f(
            &name,
            TypeIn::Raw {
                len: Some(1),
                const_: None,
            },
        ));
        over_names.push(name);
    }
    fields.push(f(
        "crc",
        TypeIn::DigestT {
            algo: "crc32".into(),
            overrides: rustruct_core::schema::CrcOverrides::default(),
            over: rustruct_core::schema::OverIn::Names(over_names),
            verify: true,
        },
    ));
    let e = compile(&fields, &Options::default()).unwrap_err();
    assert!(e.msg.contains("16 span registers"), "{}", e.msg);
}

#[test]
fn expr_stack_depth_exceeded() {
    // A right-nested chain of 8 Adds around a ref pushes 9 values onto the
    // 8-deep Expr stack before any reduction happens.
    fn deep_add(n: usize, inner: ExprIn) -> ExprIn {
        if n == 0 {
            inner
        } else {
            bin(BinOp::Add, ExprIn::Imm(1), deep_add(n - 1, inner))
        }
    }
    let len_expr = deep_add(8, r("n"));
    let e = compile(
        &[
            f("n", int(IntPrim::U8)),
            f(
                "data",
                TypeIn::Bytes {
                    len: len_expr,
                    max: None,
                },
            ),
        ],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("too deep"), "{}", e.msg);
}

#[test]
fn expr_stack_depth_within_limit_compiles() {
    fn deep_add(n: usize, inner: ExprIn) -> ExprIn {
        if n == 0 {
            inner
        } else {
            bin(BinOp::Add, ExprIn::Imm(0), deep_add(n - 1, inner))
        }
    }
    // 6 Adds -> max depth 7, within the 8-deep stack.
    let len_expr = deep_add(6, r("n"));
    let prog = compile(
        &[
            f("n", int(IntPrim::U8)),
            f(
                "data",
                TypeIn::Bytes {
                    len: len_expr,
                    max: None,
                },
            ),
        ],
        &Options::default(),
    )
    .unwrap();
    let v = unpack_ok(&prog, &[3, b'a', b'b', b'c']);
    assert_eq!(
        v.get("data"),
        Some(&rustruct_core::value::Value::Bytes(b"abc".to_vec()))
    );
}
