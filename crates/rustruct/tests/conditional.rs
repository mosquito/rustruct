//! `when(pred)`: a conditionally-present struct field. Absent means zero wire
//! bytes and no key in the decoded map -- not `None`/null on the wire,
//! there's nothing to write at all.

mod common;

use common::*;
use rustruct_core::compile::{compile, Options};
use rustruct_core::error::Kind;
use rustruct_core::program::IntPrim;
use rustruct_core::schema::{BinOp, ExprIn, FieldIn, TypeIn};
use rustruct_core::value::Value;

fn when(pred: ExprIn, then: TypeIn) -> TypeIn {
    TypeIn::CondT {
        pred,
        then: Box::new(then),
    }
}

#[test]
fn present_when_pred_is_true() {
    let prog = build(vec![
        f("has_extra", int(IntPrim::U8)),
        f("extra", when(r("has_extra"), int(IntPrim::U16))),
    ]);
    let v = roundtrip(&prog, &[1, 0xAB, 0xCD]);
    assert_eq!(v.get("extra"), Some(&Value::Int(0xABCD)));
}

#[test]
fn absent_when_pred_is_false_no_bytes_no_key() {
    let prog = build(vec![
        f("has_extra", int(IntPrim::U8)),
        f("extra", when(r("has_extra"), int(IntPrim::U16))),
    ]);
    let v = roundtrip(&prog, &[0]);
    assert_eq!(v.get("extra"), None);
}

#[test]
fn pred_is_a_comparison_not_just_a_bare_ref() {
    // this.optionalheader_size > 0 -- exactly the PE32/ELF-style condition
    // that motivated this feature.
    let prog = build(vec![
        f("optionalheader_size", int(IntPrim::U16)),
        f(
            "optionalheader",
            when(
                bin(BinOp::Gt, r("optionalheader_size"), ExprIn::Imm(0)),
                int(IntPrim::U32),
            ),
        ),
    ]);
    let v = roundtrip(&prog, &[0, 0]);
    assert_eq!(v.get("optionalheader"), None);

    let v = roundtrip(&prog, &[0, 28, 0x01, 0x02, 0x03, 0x04]);
    assert_eq!(v.get("optionalheader"), Some(&Value::Int(0x01020304)));
}

#[test]
fn pack_present_requires_the_value() {
    let prog = build(vec![
        f("has_extra", int(IntPrim::U8)),
        f("extra", when(r("has_extra"), int(IntPrim::U16))),
    ]);
    let (kind, path) = pack_err(&prog, &map(vec![("has_extra", Value::Int(1))]));
    assert_eq!(kind, Kind::Missing);
    assert_eq!(path, "extra");
}

#[test]
fn pack_absent_never_looks_up_the_value_even_if_supplied() {
    let prog = build(vec![
        f("has_extra", int(IntPrim::U8)),
        f("extra", when(r("has_extra"), int(IntPrim::U16))),
    ]);
    // has_extra=0 (false): `extra` isn't required...
    let wire = pack_ok(&prog, &map(vec![("has_extra", Value::Int(0))]));
    assert_eq!(wire, vec![0]);
    // ...and a stray value for it is simply ignored (extra keys
    // are always ignored), not a schema-mismatch error.
    let wire = pack_ok(
        &prog,
        &map(vec![
            ("has_extra", Value::Int(0)),
            ("extra", Value::Bool(true)),
        ]),
    );
    assert_eq!(wire, vec![0]);
}

#[test]
fn unnamed_when_is_a_schema_error() {
    let err = compile(
        &[FieldIn::anon(when(ExprIn::Imm(1), int(IntPrim::U8)))],
        &Options::default(),
    )
    .expect_err("unnamed when must be rejected");
    assert!(err.msg.contains("when"), "unexpected message: {}", err.msg);
}

#[test]
fn when_is_forbidden_as_an_array_element() {
    let err = compile(
        &[f(
            "items",
            TypeIn::ArrayT {
                elem: Box::new(when(ExprIn::Imm(1), int(IntPrim::U8))),
                count: Some(ExprIn::Imm(2)),
                until_eof: false,
            },
        )],
        &Options::default(),
    )
    .expect_err("when must be rejected as an array element");
    assert!(
        err.msg.contains("struct field"),
        "unexpected message: {}",
        err.msg
    );
}

#[test]
fn when_is_forbidden_as_a_switch_branch() {
    let err = compile(
        &[
            f("tag", int(IntPrim::U8)),
            f(
                "body",
                TypeIn::SwitchT {
                    on: r("tag"),
                    cases: vec![(1, when(ExprIn::Imm(1), int(IntPrim::U8)))],
                    default: None,
                },
            ),
        ],
        &Options::default(),
    )
    .expect_err("when must be rejected as a switch branch");
    assert!(
        err.msg.contains("struct field"),
        "unexpected message: {}",
        err.msg
    );
}

#[test]
fn nested_when_is_forbidden() {
    let err = compile(
        &[f(
            "outer",
            when(ExprIn::Imm(1), when(ExprIn::Imm(1), int(IntPrim::U8))),
        )],
        &Options::default(),
    )
    .expect_err("when cannot nest inside another when's then");
    assert!(
        err.msg.contains("struct field"),
        "unexpected message: {}",
        err.msg
    );
}

#[test]
fn when_field_cannot_be_referenced_elsewhere() {
    // No register is allocated for a cond-wrapped field (v1 restriction) --
    // referencing it must be an ordinary backward-ref schema error, not a
    // panic.
    let err = compile(
        &[
            f("has_extra", int(IntPrim::U8)),
            f("extra", when(r("has_extra"), int(IntPrim::U8))),
            f(
                "payload",
                TypeIn::Bytes {
                    len: ExprIn::Ref("extra".to_string()),
                    max: None,
                },
            ),
        ],
        &Options::default(),
    )
    .expect_err("a cond field has no register to reference");
    assert!(err.msg.contains("extra"), "unexpected message: {}", err.msg);
}

#[test]
fn a_conditional_field_makes_the_struct_size_non_static() {
    let prog = build(vec![
        f("has_extra", int(IntPrim::U8)),
        f("extra", when(r("has_extra"), int(IntPrim::U16))),
    ]);
    assert_eq!(prog.static_size, None);
    assert_eq!(prog.min_size, 1); // has_extra alone; extra contributes 0 at minimum
}

#[test]
fn present_case_can_be_any_type_not_just_a_scalar() {
    let prog = build(vec![
        f("has_name", int(IntPrim::U8)),
        f(
            "name",
            when(
                r("has_name"),
                TypeIn::Bytes {
                    len: ExprIn::Imm(3),
                    max: None,
                },
            ),
        ),
    ]);
    let v = roundtrip(&prog, &[1, b'a', b'b', b'c']);
    assert_eq!(v.get("name"), Some(&Value::Bytes(b"abc".to_vec())));
    let v = roundtrip(&prog, &[0]);
    assert_eq!(v.get("name"), None);
}
