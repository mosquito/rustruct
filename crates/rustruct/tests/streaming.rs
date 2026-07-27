//! The streaming contract: `parse` on a truncated buffer must always yield
//! `Incomplete` or `Ok`, never `Invalid`, with monotonic `needed`.

mod common;

use common::*;
use rustruct_core::error::Kind;
use rustruct_core::program::IntPrim;
use rustruct_core::schema::{CrcOverrides, OverIn, TypeIn};
use rustruct_core::unpack::{run as unpack, Outcome};

#[test]
fn incomplete_invariant() {
    let body = TypeIn::StructT {
        fields: vec![f(
            "items",
            TypeIn::ArrayT {
                elem: Box::new(int(IntPrim::U16)),
                count: None,
                until_eof: true,
            },
        )],
        byteorder: None,
        size: Some(r("blen")),
    };
    let prog = build(vec![
        f(
            "magic",
            TypeIn::Int {
                prim: IntPrim::U8,
                byteorder: None,
                const_: Some(0x7F),
            },
        ),
        f(
            "name",
            TypeIn::CStrT {
                max: Some(32),
                encoding: "utf-8".into(),
                errors: "strict".into(),
            },
        ),
        f("blen", int(IntPrim::U8)),
        f("body", body),
        f(
            "crc",
            TypeIn::DigestT {
                algo: "crc16_ccitt".into(),
                overrides: CrcOverrides::default(),
                over: OverIn::Star,
                verify: true,
            },
        ),
    ]);
    let full = pack_ok(
        &prog,
        &map(vec![
            ("name", rustruct_core::value::Value::Str("stream".into())),
            (
                "body",
                map(vec![(
                    "items",
                    rustruct_core::value::Value::List(vec![
                        rustruct_core::value::Value::Int(10),
                        rustruct_core::value::Value::Int(20),
                        rustruct_core::value::Value::Int(30),
                    ]),
                )]),
            ),
        ]),
    );
    // the full buffer is valid
    roundtrip(&prog, &full);

    let mut i = 0usize;
    let mut steps = 0usize;
    while i < full.len() {
        match unpack(&prog, &full[..i], 0, false, true) {
            Outcome::Incomplete { needed } => {
                assert!(needed > 0, "needed must be positive");
                assert!(
                    i + needed <= full.len(),
                    "monotonic progress, not an overshoot"
                );
                i += needed;
            }
            Outcome::Ok { .. } => break,
            Outcome::Invalid { kind, offset, path } => {
                panic!("a {i}-byte prefix yielded Invalid {kind:?} @{offset} {path:?}")
            }
        }
        steps += 1;
        assert!(steps < full.len() * 2, "no progress");
    }
    match unpack(&prog, &full, 0, false, true) {
        Outcome::Ok { pos, .. } => assert_eq!(pos, full.len()),
        other => panic!("{other:?}"),
    }
}

#[test]
fn empty_buffer_is_incomplete_not_invalid() {
    let prog = build(vec![f("x", int(IntPrim::U32))]);
    match unpack(&prog, &[], 0, false, true) {
        Outcome::Incomplete { needed } => assert_eq!(needed, 4),
        other => panic!("{other:?}"),
    }
}

#[test]
fn truncated_is_invalid_for_unpack() {
    let prog = build(vec![f("x", int(IntPrim::U32))]);
    let (kind, _) = unpack_err(&prog, &[1, 2]);
    assert_eq!(kind, Kind::Truncated);
    match unpack(&prog, &[1, 2], 0, false, true) {
        Outcome::Incomplete { needed } => assert_eq!(needed, 2),
        other => panic!("{other:?}"),
    }
}

#[test]
fn incomplete_inside_dynamic_bytes_field() {
    let prog = build(vec![
        f("n", int(IntPrim::U8)),
        f(
            "data",
            TypeIn::Bytes {
                len: r("n"),
                max: None,
            },
        ),
    ]);
    match unpack(&prog, &[5, b'a', b'b'], 0, false, true) {
        Outcome::Incomplete { needed } => assert_eq!(needed, 3),
        other => panic!("{other:?}"),
    }
}
