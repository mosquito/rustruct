//! Shared helpers for the integration tests. Living under `tests/common/`
//! (not `tests/common.rs`) keeps cargo from treating this as its own test
//! binary.

#![allow(dead_code)]

use std::sync::Arc;

use rustruct_core::compile::{compile, Options};
use rustruct_core::error::Kind;
use rustruct_core::pack::{run as pack, PackOutcome};
use rustruct_core::program::{IntPrim, Program};
use rustruct_core::schema::{BinOp, ExprIn, FieldIn, TypeIn};
use rustruct_core::unpack::{run as unpack, Outcome};
use rustruct_core::value::Value;

pub fn int(prim: IntPrim) -> TypeIn {
    TypeIn::Int {
        prim,
        byteorder: None,
        const_: None,
    }
}

pub fn f(name: &str, ty: TypeIn) -> FieldIn {
    FieldIn::named(name, ty)
}

pub fn r(name: &str) -> ExprIn {
    ExprIn::Ref(name.to_string())
}

pub fn bin(op: BinOp, l: ExprIn, r: ExprIn) -> ExprIn {
    ExprIn::Bin(op, Box::new(l), Box::new(r))
}

pub fn build(fields: Vec<FieldIn>) -> Program {
    compile(&fields, &Options::default()).expect("compile")
}

pub fn build_with(fields: Vec<FieldIn>, opts: Options) -> Program {
    compile(&fields, &opts).expect("compile")
}

pub fn map(pairs: Vec<(&str, Value)>) -> Value {
    Value::Map(pairs.into_iter().map(|(k, v)| (Arc::from(k), v)).collect())
}

pub fn unpack_ok(prog: &Program, buf: &[u8]) -> Value {
    match unpack(prog, buf, 0, true, false) {
        Outcome::Ok { value, .. } => value,
        other => panic!("expected Ok, got {other:?}"),
    }
}

pub fn unpack_err(prog: &Program, buf: &[u8]) -> (Kind, String) {
    match unpack(prog, buf, 0, true, false) {
        Outcome::Invalid { kind, path, .. } => (kind, path),
        other => panic!("expected Invalid, got {other:?}"),
    }
}

pub fn pack_ok(prog: &Program, v: &Value) -> Vec<u8> {
    match pack(prog, v) {
        PackOutcome::Ok(b) => b,
        PackOutcome::Err { kind, path } => panic!("pack failed: {kind:?} at {path:?}"),
    }
}

pub fn pack_err(prog: &Program, v: &Value) -> (Kind, String) {
    match pack(prog, v) {
        PackOutcome::Err { kind, path } => (kind, path),
        PackOutcome::Ok(_) => panic!("expected a pack error"),
    }
}

pub fn roundtrip(prog: &Program, buf: &[u8]) -> Value {
    let v = unpack_ok(prog, buf);
    let packed = pack_ok(prog, &v);
    assert_eq!(packed, buf, "pack(unpack(buf)) != buf");
    v
}
