//! A lightweight round-trip fuzz check on a static schema — a dependency-free
//! stand-in for a proper proptest-based fuzz suite.

mod common;

use common::*;
use rustruct_core::program::IntPrim;
use rustruct_core::schema::ByteOrder;
use rustruct_core::schema::TypeIn;

#[test]
fn randomized_static_roundtrip() {
    let prog = build(vec![
        f("a", int(IntPrim::U8)),
        f("b", int(IntPrim::I16)),
        f(
            "c",
            TypeIn::Int {
                prim: IntPrim::U32,
                byteorder: Some(ByteOrder::Little),
                const_: None,
            },
        ),
        f("d", int(IntPrim::I64)),
    ]);
    let mut state = 0x1234_5678_u64;
    let mut next = move || {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        state
    };
    for _ in 0..500 {
        let mut buf = Vec::new();
        for _ in 0..15 {
            buf.push(next() as u8);
        }
        let v = unpack_ok(&prog, &buf);
        assert_eq!(pack_ok(&prog, &v), buf);
    }
}

#[test]
fn randomized_dynamic_roundtrip() {
    // A length-prefixed bytes field plus a trailing checksum: exercises
    // derived-length backpatching across many random payload lengths.
    let prog = build(vec![
        f("n", int(IntPrim::U8)),
        f(
            "data",
            TypeIn::Bytes {
                len: r("n"),
                max: Some(64),
            },
        ),
        f(
            "crc",
            TypeIn::DigestT {
                algo: "crc32".into(),
                overrides: rustruct_core::schema::CrcOverrides::default(),
                over: rustruct_core::schema::OverIn::Names(vec!["data".into()]),
                verify: true,
            },
        ),
    ]);
    let mut state = 0x2463_1af9_u64;
    let mut next = move || {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        state
    };
    for _ in 0..200 {
        let len = (next() % 40) as usize;
        let data: Vec<u8> = (0..len).map(|_| next() as u8).collect();
        let values = map(vec![("data", rustruct_core::value::Value::Bytes(data))]);
        let packed = pack_ok(&prog, &values);
        roundtrip(&prog, &packed);
    }
}
