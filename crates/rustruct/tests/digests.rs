//! `digest`: deferred scope-exit verification, `over` as a name tuple or
//! `"*"` with self-zeroing, CRC/hash presets.

mod common;

use common::*;
use rustruct_core::compile::{compile, Options};
use rustruct_core::digest::{Algo, DigestVal, Hasher};
use rustruct_core::error::Kind;
use rustruct_core::program::IntPrim;
use rustruct_core::schema::{CrcOverrides, OverIn, TypeIn};
use rustruct_core::unpack::{run as unpack, Outcome};
use rustruct_core::value::Value;

fn digest_field(
    name: &str,
    algo: &str,
    over: OverIn,
    verify: bool,
) -> rustruct_core::schema::FieldIn {
    f(
        name,
        TypeIn::DigestT {
            algo: algo.to_string(),
            overrides: CrcOverrides::default(),
            over,
            verify,
        },
    )
}

/// A PNG-like chunk: length (derived), type, data, CRC32 over type+data
/// (forward coverage: `over` is declared before the fields it names, the
/// one exception to the backward-only rule).
#[test]
fn png_chunk_crc32() {
    let prog = build(vec![
        f("length", int(IntPrim::U32)),
        f(
            "ctype",
            TypeIn::Raw {
                len: Some(4),
                const_: None,
            },
        ),
        f(
            "data",
            TypeIn::Bytes {
                len: r("length"),
                max: None,
            },
        ),
        digest_field(
            "crc",
            "crc32",
            OverIn::Names(vec!["ctype".into(), "data".into()]),
            true,
        ),
    ]);
    let packed = pack_ok(
        &prog,
        &map(vec![
            ("ctype", Value::Bytes(b"IDAT".to_vec())),
            ("data", Value::Bytes(b"payload".to_vec())),
        ]),
    );
    assert_eq!(&packed[..4], &[0, 0, 0, 7]);
    let v = roundtrip(&prog, &packed);
    let Value::Int(crc) = v.get("crc").unwrap() else {
        panic!()
    };
    // cross-check against an independent computation
    let mut h = Hasher::new(&Algo::preset("crc32").unwrap());
    h.update(b"IDATpayload");
    assert_eq!(DigestVal::Int(*crc as u64), h.finalize());

    // corrupting a byte -> checksum
    let mut bad = packed.clone();
    bad[6] ^= 0xFF;
    let (kind, path) = unpack_err(&prog, &bad);
    assert_eq!(kind, Kind::Checksum);
    assert_eq!(path, "crc");
}

#[test]
fn digest_verify_false_allows_mismatch() {
    let prog = build(vec![
        f("length", int(IntPrim::U32)),
        f(
            "ctype",
            TypeIn::Raw {
                len: Some(4),
                const_: None,
            },
        ),
        f(
            "data",
            TypeIn::Bytes {
                len: r("length"),
                max: None,
            },
        ),
        digest_field(
            "crc",
            "crc32",
            OverIn::Names(vec!["ctype".into(), "data".into()]),
            false,
        ),
    ]);
    let mut bad = pack_ok(
        &prog,
        &map(vec![
            ("ctype", Value::Bytes(b"IDAT".to_vec())),
            ("data", Value::Bytes(b"payload".to_vec())),
        ]),
    );
    bad[6] ^= 0xFF;
    match unpack(&prog, &bad, 0, true, false) {
        Outcome::Ok { .. } => {}
        other => panic!("verify=false must not raise on mismatch: {other:?}"),
    }
}

/// IPv4 header: bits + checksum with `over="*"` and self-zeroing.
#[test]
fn ipv4_header_checksum() {
    let prog = build(vec![
        f(
            "version",
            TypeIn::Bits {
                width: 4,
                signed: false,
            },
        ),
        f(
            "ihl",
            TypeIn::Bits {
                width: 4,
                signed: false,
            },
        ),
        f("tos", int(IntPrim::U8)),
        f("total_length", int(IntPrim::U16)),
        f("ident", int(IntPrim::U16)),
        f("frag", int(IntPrim::U16)),
        f("ttl", int(IntPrim::U8)),
        f("proto", int(IntPrim::U8)),
        digest_field("checksum", "ip", OverIn::Star, true),
        f("src", int(IntPrim::U32)),
        f("dst", int(IntPrim::U32)),
    ]);
    // a reference header with a valid checksum of 0xB861
    let buf: [u8; 20] = [
        0x45, 0x00, 0x00, 0x73, 0x00, 0x00, 0x40, 0x00, 0x40, 0x11, 0xb8, 0x61, 0xc0, 0xa8, 0x00,
        0x01, 0xc0, 0xa8, 0x00, 0xc7,
    ];
    let v = roundtrip(&prog, &buf);
    assert_eq!(v.get("checksum"), Some(&Value::Int(0xB861)));
    assert_eq!(v.get("ttl"), Some(&Value::Int(0x40)));

    let mut bad = buf;
    bad[8] = 0x41;
    let (kind, path) = unpack_err(&prog, &bad);
    assert_eq!(kind, Kind::Checksum);
    assert_eq!(path, "checksum");
}

#[test]
fn sha256_digest_bytes_value() {
    let prog = build(vec![
        f(
            "data",
            TypeIn::Bytes {
                len: rustruct_core::schema::ExprIn::Imm(3),
                max: None,
            },
        ),
        digest_field("hash", "sha256", OverIn::Names(vec!["data".into()]), true),
    ]);
    let packed = pack_ok(&prog, &map(vec![("data", Value::Bytes(b"abc".to_vec()))]));
    assert_eq!(packed.len(), 3 + 32);
    let v = roundtrip(&prog, &packed);
    let Value::Bytes(h) = v.get("hash").unwrap() else {
        panic!()
    };
    // sha256("abc") starts with ba7816bf
    assert_eq!(&h[..4], &[0xba, 0x78, 0x16, 0xbf]);
}

#[test]
fn md5_and_sha1_digest_values() {
    let prog_md5 = build(vec![
        f(
            "data",
            TypeIn::Bytes {
                len: rustruct_core::schema::ExprIn::Imm(3),
                max: None,
            },
        ),
        digest_field("hash", "md5", OverIn::Names(vec!["data".into()]), true),
    ]);
    let packed = pack_ok(
        &prog_md5,
        &map(vec![("data", Value::Bytes(b"abc".to_vec()))]),
    );
    let v = roundtrip(&prog_md5, &packed);
    let Value::Bytes(h) = v.get("hash").unwrap() else {
        panic!()
    };
    // md5("abc") = 900150983cd24fb0 d6963f7d28e17f72
    assert_eq!(&h[..4], &[0x90, 0x01, 0x50, 0x98]);

    let prog_sha1 = build(vec![
        f(
            "data",
            TypeIn::Bytes {
                len: rustruct_core::schema::ExprIn::Imm(3),
                max: None,
            },
        ),
        digest_field("hash", "sha1", OverIn::Names(vec!["data".into()]), true),
    ]);
    let packed = pack_ok(
        &prog_sha1,
        &map(vec![("data", Value::Bytes(b"abc".to_vec()))]),
    );
    let v = roundtrip(&prog_sha1, &packed);
    let Value::Bytes(h) = v.get("hash").unwrap() else {
        panic!()
    };
    // sha1("abc") = a9993e364706816aba3e25717850c26c9cd0d89
    assert_eq!(&h[..4], &[0xa9, 0x99, 0x3e, 0x36]);
}

#[test]
fn digest_over_unknown_name_is_schema_error() {
    let e = compile(
        &[digest_field(
            "crc",
            "crc32",
            OverIn::Names(vec!["nope".into()]),
            true,
        )],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("nope"), "{}", e.msg);
}

#[test]
fn digest_self_coverage_in_tuple_form_is_schema_error() {
    let e = compile(
        &[digest_field(
            "crc",
            "crc32",
            OverIn::Names(vec!["crc".into()]),
            true,
        )],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("cannot list itself"), "{}", e.msg);
}

#[test]
fn crc_overrides_on_hash_is_schema_error() {
    let e = compile(
        &[
            f(
                "data",
                TypeIn::Bytes {
                    len: rustruct_core::schema::ExprIn::Imm(1),
                    max: None,
                },
            ),
            f(
                "h",
                TypeIn::DigestT {
                    algo: "sha256".into(),
                    overrides: CrcOverrides {
                        poly: Some(7),
                        ..CrcOverrides::default()
                    },
                    over: OverIn::Names(vec!["data".into()]),
                    verify: true,
                },
            ),
        ],
        &Options::default(),
    )
    .unwrap_err();
    assert!(e.msg.contains("overrides"), "{}", e.msg);
}

#[test]
fn nested_digest_innermost_patched_before_outer() {
    // The inner struct has its own CRC over its own body; the outer digest
    // covers the whole outer scope with over="*", including the
    // already-patched inner CRC bytes (innermost patches first).
    let inner = TypeIn::StructT {
        fields: vec![
            f(
                "data",
                TypeIn::Bytes {
                    len: rustruct_core::schema::ExprIn::Imm(2),
                    max: None,
                },
            ),
            digest_field(
                "inner_crc",
                "crc16_ccitt",
                OverIn::Names(vec!["data".into()]),
                true,
            ),
        ],
        byteorder: None,
        size: None,
    };
    let prog = build(vec![
        f("inner", inner),
        digest_field("outer_crc", "crc32", OverIn::Star, true),
    ]);
    let packed = pack_ok(
        &prog,
        &map(vec![(
            "inner",
            map(vec![("data", Value::Bytes(b"ab".to_vec()))]),
        )]),
    );
    roundtrip(&prog, &packed);

    // corrupting a data byte must invalidate both digests, inner first
    let mut bad = packed.clone();
    bad[0] ^= 0xFF;
    let (kind, path) = unpack_err(&prog, &bad);
    assert_eq!(kind, Kind::Checksum);
    assert_eq!(path, "inner.inner_crc");
}
