//! `bytes`/`str`/`cstr` with dynamic length: derived fields, linear
//! inversion, greedy reads, and the `max` limit.

mod common;

use common::*;
use rustruct_core::compile::{compile, Options};
use rustruct_core::error::Kind;
use rustruct_core::program::IntPrim;
use rustruct_core::schema::{BinOp, ExprIn, TypeIn};
use rustruct_core::unpack::{run as unpack, Outcome};
use rustruct_core::value::Value;

#[test]
fn length_prefixed_bytes_roundtrip() {
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
    let v = roundtrip(&prog, b"\x03abc");
    assert_eq!(v.get("data"), Some(&Value::Bytes(b"abc".to_vec())));

    // derived: n is ignored on input and recomputed
    let packed = pack_ok(&prog, &map(vec![("data", Value::Bytes(b"hello".to_vec()))]));
    assert_eq!(packed, b"\x05hello");
    let packed = pack_ok(
        &prog,
        &map(vec![
            ("n", Value::Int(99)),
            ("data", Value::Bytes(b"xy".to_vec())),
        ]),
    );
    assert_eq!(packed, b"\x02xy");
}

#[test]
fn linear_inversion_with_offset_and_scale() {
    // size = len + 4  =>  len = size - 4
    let prog = build(vec![
        f("size", int(IntPrim::U8)),
        f(
            "data",
            TypeIn::Bytes {
                len: bin(BinOp::Sub, r("size"), ExprIn::Imm(4)),
                max: None,
            },
        ),
    ]);
    let packed = pack_ok(&prog, &map(vec![("data", Value::Bytes(b"ab".to_vec()))]));
    assert_eq!(packed, b"\x06ab");
    roundtrip(&prog, &packed);

    // n*2 = len => n = len/2; an odd length is indivisible
    let prog2 = build(vec![
        f("n", int(IntPrim::U8)),
        f(
            "data",
            TypeIn::Bytes {
                len: bin(BinOp::Mul, r("n"), ExprIn::Imm(2)),
                max: None,
            },
        ),
    ]);
    let packed = pack_ok(&prog2, &map(vec![("data", Value::Bytes(b"abcd".to_vec()))]));
    assert_eq!(packed, b"\x02abcd");
    let (kind, _) = pack_err(&prog2, &map(vec![("data", Value::Bytes(b"abc".to_vec()))]));
    assert_eq!(kind, Kind::Indivisible);
}

#[test]
fn multiple_consumers_of_one_register_must_agree() {
    // Two bytes fields sharing the same length register: pack must produce
    // a consistent value for both, or fail.
    let prog = build(vec![
        f("n", int(IntPrim::U8)),
        f(
            "a",
            TypeIn::Bytes {
                len: r("n"),
                max: None,
            },
        ),
        f(
            "b",
            TypeIn::Bytes {
                len: r("n"),
                max: None,
            },
        ),
    ]);
    let packed = pack_ok(
        &prog,
        &map(vec![
            ("a", Value::Bytes(b"xy".to_vec())),
            ("b", Value::Bytes(b"zw".to_vec())),
        ]),
    );
    assert_eq!(packed, b"\x02xyzw");

    let (kind, _) = pack_err(
        &prog,
        &map(vec![
            ("a", Value::Bytes(b"xy".to_vec())),
            ("b", Value::Bytes(b"zzz".to_vec())),
        ]),
    );
    assert_eq!(kind, Kind::Inconsistent);
}

#[test]
fn nonlinear_len_is_schema_error() {
    let err = compile(
        &[
            f("a", int(IntPrim::U8)),
            f("b", int(IntPrim::U8)),
            f(
                "data",
                TypeIn::Bytes {
                    len: bin(BinOp::Add, r("a"), r("b")),
                    max: None,
                },
            ),
        ],
        &Options::default(),
    )
    .unwrap_err();
    assert!(err.msg.contains("invertible"), "{}", err.msg);
}

#[test]
fn forward_ref_is_schema_error() {
    let err = compile(
        &[
            f(
                "data",
                TypeIn::Bytes {
                    len: r("n"),
                    max: None,
                },
            ),
            f("n", int(IntPrim::U8)),
        ],
        &Options::default(),
    )
    .unwrap_err();
    assert!(err.msg.contains("backward"), "{}", err.msg);
}

#[test]
fn range_check_on_pack() {
    let prog = build(vec![f("x", int(IntPrim::U8))]);
    let (kind, path) = pack_err(&prog, &map(vec![("x", Value::Int(256))]));
    assert_eq!(kind, Kind::Range);
    assert_eq!(path, "x");
    let (kind, _) = pack_err(&prog, &map(vec![("x", Value::Int(-1))]));
    assert_eq!(kind, Kind::Range);
}

#[test]
fn missing_key_on_pack() {
    let prog = build(vec![f("x", int(IntPrim::U8))]);
    let (kind, path) = pack_err(&prog, &map(vec![]));
    assert_eq!(kind, Kind::Missing);
    assert_eq!(path, "x");
}

#[test]
fn derived_length_exceeds_prim_range() {
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
    let (kind, _) = pack_err(&prog, &map(vec![("data", Value::Bytes(vec![0u8; 300]))]));
    assert_eq!(kind, Kind::Range, "the body doesn't fit an u8 length");
}

#[test]
fn str_utf8_len_in_bytes() {
    let prog = build(vec![
        f("n", int(IntPrim::U8)),
        f(
            "s",
            TypeIn::StrT {
                len: r("n"),
                max: None,
                encoding: "utf-8".into(),
                errors: "strict".into(),
            },
        ),
    ]);
    let payload = "\u{4f60}\u{597d}\u{4e16}\u{754c}"; // multi-byte UTF-8 sample
    let mut buf = vec![payload.len() as u8];
    buf.extend_from_slice(payload.as_bytes());
    let v = roundtrip(&prog, &buf);
    assert_eq!(v.get("s"), Some(&Value::Str(payload.to_string())));

    // invalid utf-8 -> decode
    let (kind, path) = unpack_err(&prog, &[2, 0xC3, 0x28]);
    assert_eq!(kind, Kind::Decode);
    assert_eq!(path, "s");
}

#[test]
fn str_ascii_and_latin1() {
    let prog = build(vec![f(
        "s",
        TypeIn::StrT {
            len: ExprIn::Imm(3),
            max: None,
            encoding: "ascii".into(),
            errors: "strict".into(),
        },
    )]);
    let v = roundtrip(&prog, b"abc");
    assert_eq!(v.get("s"), Some(&Value::Str("abc".to_string())));
    let (kind, _) = unpack_err(&prog, &[0x61, 0x80, 0x63]);
    assert_eq!(kind, Kind::Decode);

    let prog2 = build(vec![f(
        "s",
        TypeIn::StrT {
            len: ExprIn::Imm(2),
            max: None,
            encoding: "latin-1".into(),
            errors: "strict".into(),
        },
    )]);
    // 0xE9 is 'é' in latin-1, invalid as a utf-8 lead byte on its own
    let v = roundtrip(&prog2, &[0x61, 0xE9]);
    assert_eq!(v.get("s"), Some(&Value::Str("a\u{e9}".to_string())));
}

#[test]
fn unsupported_encoding_is_schema_error() {
    let e = compile(
        &[f(
            "s",
            TypeIn::StrT {
                len: ExprIn::Imm(4),
                max: None,
                encoding: "cp1251".into(),
                errors: "strict".into(),
            },
        )],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("cp1251"), "{}", e.msg);
}

#[test]
fn cstr_roundtrip_and_errors() {
    let prog = build(vec![
        f(
            "s",
            TypeIn::CStrT {
                max: Some(8),
                encoding: "utf-8".into(),
                errors: "strict".into(),
            },
        ),
        f("x", int(IntPrim::U8)),
    ]);
    let v = roundtrip(&prog, b"hi\x00\x2A");
    assert_eq!(v.get("s"), Some(&Value::Str("hi".into())));

    // no terminator found within max -> limit
    let (kind, _) = unpack_err(&prog, b"aaaaaaaaaaaa\x00\x2A");
    assert_eq!(kind, Kind::Limit);

    // \0 in the value at pack time
    let (kind, _) = pack_err(
        &prog,
        &map(vec![("s", Value::Str("a\0b".into())), ("x", Value::Int(0))]),
    );
    assert_eq!(kind, Kind::NulInCstr);

    // longer than max at pack time -- also checked before writing, not just
    // on unpack of an already-produced buffer
    let (kind, _) = pack_err(
        &prog,
        &map(vec![("s", Value::Str("a".repeat(9))), ("x", Value::Int(0))]),
    );
    assert_eq!(kind, Kind::Limit);
}

#[test]
fn cstr_unterminated_within_window_is_invalid() {
    // A window smaller than max, with no terminator before the window ends:
    // not Incomplete (that would only apply at the end of the whole buffer).
    let body = TypeIn::StructT {
        fields: vec![f(
            "s",
            TypeIn::CStrT {
                max: Some(32),
                encoding: "utf-8".into(),
                errors: "strict".into(),
            },
        )],
        byteorder: None,
        size: Some(r("size")),
    };
    let prog = build(vec![f("size", int(IntPrim::U8)), f("body", body)]);
    // a trailing byte after the window ensures the window boundary is hit
    // before the buffer end, so this must be Unterminated, not Incomplete.
    let (kind, _) = unpack_err(&prog, &[4, b'a', b'b', b'c', b'd', 0xFF]);
    assert_eq!(kind, Kind::Unterminated);
}

#[test]
fn greedy_bytes() {
    let prog = build(vec![
        f("head", int(IntPrim::U8)),
        f(
            "rest",
            TypeIn::Bytes {
                len: ExprIn::Greedy,
                max: None,
            },
        ),
    ]);
    let v = roundtrip(&prog, b"\x01tail-data");
    assert_eq!(v.get("rest"), Some(&Value::Bytes(b"tail-data".to_vec())));
}

#[test]
fn bytes_max_limit() {
    let prog = compile(
        &[
            f("n", int(IntPrim::U32)),
            f(
                "data",
                TypeIn::Bytes {
                    len: r("n"),
                    max: Some(16),
                },
            ),
        ],
        &Options::default(),
    )
    .unwrap();
    let (kind, _) = unpack_err(&prog, &[0, 0, 1, 0, 1, 2, 3]);
    assert_eq!(kind, Kind::Limit);
}

#[test]
fn bytes_max_limit_on_pack() {
    // max= also bounds pack(), not just unpack(): a value that violates it
    // is rejected up front instead of producing wire bytes this same codec
    // could never unpack again.
    let prog = compile(
        &[
            f("n", int(IntPrim::U32)),
            f(
                "data",
                TypeIn::Bytes {
                    len: r("n"),
                    max: Some(4),
                },
            ),
        ],
        &Options::default(),
    )
    .unwrap();
    let (kind, _) = pack_err(&prog, &map(vec![("data", Value::Bytes(vec![0; 8]))]));
    assert_eq!(kind, Kind::Limit);
    let _ = pack_ok(&prog, &map(vec![("data", Value::Bytes(vec![0; 4]))]));
}

#[test]
fn negative_len_at_runtime() {
    let prog = build(vec![
        f("n", int(IntPrim::I8)),
        f(
            "data",
            TypeIn::Bytes {
                len: r("n"),
                max: None,
            },
        ),
    ]);
    let (kind, _) = unpack_err(&prog, &[0xFF]);
    assert_eq!(kind, Kind::NegativeLen);
}

#[test]
fn div_zero_at_runtime() {
    let prog = build(vec![
        f("n", int(IntPrim::U8)),
        f("m", int(IntPrim::U8)),
        f(
            "body",
            TypeIn::SwitchT {
                on: bin(BinOp::Div, ExprIn::Imm(10), r("m")),
                cases: vec![(5, int(IntPrim::U8))],
                default: None,
            },
        ),
    ]);
    let (kind, _) = unpack_err(&prog, &[1, 0, 7]);
    assert_eq!(kind, Kind::DivZero);
    let v = unpack_ok(&prog, &[1, 2, 7]);
    assert_eq!(v.get("body"), Some(&Value::Int(7)));
}

#[test]
fn unnamed_dynamic_is_schema_error() {
    let e = compile(
        &[rustruct_core::schema::FieldIn::anon(TypeIn::Bytes {
            len: ExprIn::Imm(4),
            max: None,
        })],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("unnamed"), "{}", e.msg);
}

#[test]
fn parse_offset_within_buffer() {
    let prog = build(vec![f("x", int(IntPrim::U16))]);
    match unpack(&prog, b"junk\x01\x02", 4, false, false) {
        Outcome::Ok { value, pos } => {
            assert_eq!(value.get("x"), Some(&Value::Int(0x0102)));
            assert_eq!(pos, 6);
        }
        other => panic!("{other:?}"),
    }
}
