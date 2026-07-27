//! `bits`: MSB-first bit runs, signed extension, bit-alignment, bits as a
//! length source.

mod common;

use common::*;
use rustruct_core::compile::{compile, Options};
use rustruct_core::error::Kind;
use rustruct_core::program::{IntPrim, Program};
use rustruct_core::schema::TypeIn;
use rustruct_core::value::Value;

fn dns_header() -> Program {
    build(vec![
        f("id", int(IntPrim::U16)),
        f(
            "qr",
            TypeIn::Bits {
                width: 1,
                signed: false,
            },
        ),
        f(
            "opcode",
            TypeIn::Bits {
                width: 4,
                signed: false,
            },
        ),
        f(
            "aa",
            TypeIn::Bits {
                width: 1,
                signed: false,
            },
        ),
        f(
            "tc",
            TypeIn::Bits {
                width: 1,
                signed: false,
            },
        ),
        f(
            "rd",
            TypeIn::Bits {
                width: 1,
                signed: false,
            },
        ),
        f(
            "ra",
            TypeIn::Bits {
                width: 1,
                signed: false,
            },
        ),
        f(
            "z",
            TypeIn::Bits {
                width: 3,
                signed: false,
            },
        ),
        f(
            "rcode",
            TypeIn::Bits {
                width: 4,
                signed: false,
            },
        ),
        f("qdcount", int(IntPrim::U16)),
        f("ancount", int(IntPrim::U16)),
        f("nscount", int(IntPrim::U16)),
        f("arcount", int(IntPrim::U16)),
    ])
}

#[test]
fn dns_header_bits() {
    let prog = dns_header();
    assert_eq!(prog.static_size, Some(12));
    // id=0x1234, QR=1 opcode=0 AA=0 TC=0 RD=1; RA=1 Z=0 RCODE=0; counts 1,2,0,0
    let buf = [0x12, 0x34, 0x81, 0x80, 0, 1, 0, 2, 0, 0, 0, 0];
    let v = roundtrip(&prog, &buf);
    assert_eq!(v.get("qr"), Some(&Value::Int(1)));
    assert_eq!(v.get("opcode"), Some(&Value::Int(0)));
    assert_eq!(v.get("rd"), Some(&Value::Int(1)));
    assert_eq!(v.get("ra"), Some(&Value::Int(1)));
    assert_eq!(v.get("rcode"), Some(&Value::Int(0)));
    assert_eq!(v.get("ancount"), Some(&Value::Int(2)));
}

#[test]
fn bits_misaligned_run_is_schema_error() {
    let err = compile(
        &[
            f(
                "a",
                TypeIn::Bits {
                    width: 3,
                    signed: false,
                },
            ),
            f("x", int(IntPrim::U8)),
        ],
        &Options::default(),
    )
    .unwrap_err();
    assert!(err.msg.contains("bit_alignment"), "{}", err.msg);
}

#[test]
fn bits_width_out_of_range_is_schema_error() {
    let e = compile(
        &[f(
            "a",
            TypeIn::Bits {
                width: 0,
                signed: false,
            },
        )],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("1..64"), "{}", e.msg);
    let e = compile(
        &[f(
            "a",
            TypeIn::Bits {
                width: 65,
                signed: false,
            },
        )],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("1..64"), "{}", e.msg);
}

#[test]
fn signed_bits() {
    let prog = build(vec![
        f(
            "a",
            TypeIn::Bits {
                width: 4,
                signed: true,
            },
        ),
        f(
            "b",
            TypeIn::Bits {
                width: 4,
                signed: false,
            },
        ),
    ]);
    let v = roundtrip(&prog, &[0xF5]);
    assert_eq!(v.get("a"), Some(&Value::Int(-1)));
    assert_eq!(v.get("b"), Some(&Value::Int(5)));
}

#[test]
fn signed_bits_range_check_on_pack() {
    let prog = build(vec![
        f(
            "a",
            TypeIn::Bits {
                width: 4,
                signed: true,
            },
        ),
        f(
            "b",
            TypeIn::Bits {
                width: 4,
                signed: false,
            },
        ),
    ]);
    // signed 4-bit range is -8..7
    let packed = pack_ok(
        &prog,
        &map(vec![("a", Value::Int(-8)), ("b", Value::Int(0))]),
    );
    assert_eq!(packed, [0x80]);
    let (kind, _) = pack_err(
        &prog,
        &map(vec![("a", Value::Int(-9)), ("b", Value::Int(0))]),
    );
    assert_eq!(kind, Kind::Range);
    let (kind, _) = pack_err(
        &prog,
        &map(vec![("a", Value::Int(0)), ("b", Value::Int(16))]),
    );
    assert_eq!(kind, Kind::Range);
}

#[test]
fn full_byte_bits_field() {
    // A single 8-bit bits field behaves like a u8 (an edge case for the
    // byte-boundary math in read_bits/or_bits).
    let prog = build(vec![f(
        "a",
        TypeIn::Bits {
            width: 8,
            signed: false,
        },
    )]);
    let v = roundtrip(&prog, &[0xAB]);
    assert_eq!(v.get("a"), Some(&Value::Int(0xAB)));
}

#[test]
fn wide_bits_field_spanning_multiple_bytes() {
    // A 20-bit field spanning more than two bytes, followed by a 4-bit tail
    // to keep the run byte-aligned.
    let prog = build(vec![
        f(
            "a",
            TypeIn::Bits {
                width: 20,
                signed: false,
            },
        ),
        f(
            "b",
            TypeIn::Bits {
                width: 4,
                signed: false,
            },
        ),
    ]);
    let v = roundtrip(&prog, &[0x12, 0x34, 0x5F]);
    assert_eq!(v.get("a"), Some(&Value::Int(0x12345)));
    assert_eq!(v.get("b"), Some(&Value::Int(0xF)));
}

#[test]
fn bits_as_length_ref() {
    let prog = build(vec![
        f(
            "hi",
            TypeIn::Bits {
                width: 4,
                signed: false,
            },
        ),
        f(
            "n",
            TypeIn::Bits {
                width: 4,
                signed: false,
            },
        ),
        f(
            "data",
            TypeIn::Bytes {
                len: r("n"),
                max: None,
            },
        ),
    ]);
    let v = roundtrip(&prog, &[0x03, b'a', b'b', b'c']);
    assert_eq!(v.get("data"), Some(&Value::Bytes(b"abc".to_vec())));
    // a derived bits field gets patched during pack
    let packed = pack_ok(
        &prog,
        &map(vec![
            ("hi", Value::Int(0xA)),
            ("data", Value::Bytes(b"xy".to_vec())),
        ]),
    );
    assert_eq!(packed, [0xA2, b'x', b'y']);
}
