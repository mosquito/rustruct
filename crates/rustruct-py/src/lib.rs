//! pyo3 bindings for the core: the `rustruct.core` module.

use std::collections::HashMap;
use std::sync::Arc;

use pyo3::buffer::PyBuffer;
use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyNotImplementedError, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::{
    PyBool, PyByteArray, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple,
};

use rustruct_core::compile::{compile as core_compile, Options};
use rustruct_core::model::{Builder, Source};
use rustruct_core::pack::{run_with as core_pack, PackOutcome};
use rustruct_core::program::{IntPrim, Key, Op, Program, RestPolicy};
use rustruct_core::schema::{BinOp, ByteOrder, CrcOverrides, ExprIn, FieldIn, OverIn, TypeIn};
use rustruct_core::unpack::{run_with as core_unpack, Outcome};

/// FNV-1a: `Codec::key_cache`'s field names are short, fixed at compile
/// time, and never attacker-controlled, so std's default SipHash (built
/// deliberately DoS-resistant, at a real per-hash cost) is the wrong
/// tradeoff here -- a profiling pass (see benchmark/README.md) showed a
/// plain SipHash-backed cache actually *regressing* the high-repetition
/// case (many lookups of the same couple of short field names, e.g. an
/// array of 2-field structs) versus not caching at all.
struct FnvHasher(u64);

impl Default for FnvHasher {
    fn default() -> Self {
        FnvHasher(0xcbf29ce484222325)
    }
}

impl std::hash::Hasher for FnvHasher {
    fn finish(&self) -> u64 {
        self.0
    }
    fn write(&mut self, bytes: &[u8]) {
        for &b in bytes {
            self.0 ^= u64::from(b);
            self.0 = self.0.wrapping_mul(0x100000001b3);
        }
    }
}

type FnvBuildHasher = std::hash::BuildHasherDefault<FnvHasher>;
type KeyCache = HashMap<Arc<str>, Py<PyString>, FnvBuildHasher>;

create_exception!(rustruct, RustructError, PyException, "Base rustruct error.");
create_exception!(rustruct, SchemaError, RustructError, "compile() error.");
create_exception!(
    rustruct,
    InvalidDataError,
    RustructError,
    "Corrupt data (unpack)."
);
create_exception!(
    rustruct,
    PackError,
    RustructError,
    "Values don't fit the schema (pack)."
);

/// Bumped on any change to the IR or the serialization format.
const ABI: u32 = 1;

fn schema_err(msg: impl Into<String>) -> PyErr {
    SchemaError::new_err(msg.into())
}

fn invalid_err(py: Python<'_>, kind: &str, offset: usize, path: &str) -> PyErr {
    let msg = if path.is_empty() {
        format!("invalid data: {kind} at offset {offset}")
    } else {
        format!("invalid data: {kind} at offset {offset} (path {path:?})")
    };
    let err = InvalidDataError::new_err(msg);
    let v = err.value(py);
    let _ = v.setattr("kind", kind);
    let _ = v.setattr("offset", offset);
    let _ = v.setattr("path", path);
    err
}

fn pack_err(py: Python<'_>, kind: &str, path: &str) -> PyErr {
    let msg = if path.is_empty() {
        format!("cannot pack: {kind}")
    } else {
        format!("cannot pack: {kind} (path {path:?})")
    };
    let err = PackError::new_err(msg);
    let v = err.value(py);
    let _ = v.setattr("kind", kind);
    let _ = v.setattr("path", path);
    err
}

// ---------- parsing the input IR ----------

fn parse_byteorder(v: &Bound<'_, PyAny>) -> PyResult<ByteOrder> {
    let s: String = v
        .extract()
        .map_err(|_| schema_err("byteorder must be a str"))?;
    match s.as_str() {
        "big" => Ok(ByteOrder::Big),
        // "network" is a struct-module-style alias for big-endian (format '!').
        "network" => Ok(ByteOrder::Big),
        "little" => Ok(ByteOrder::Little),
        other => Err(schema_err(format!(
            "byteorder {other:?} is not supported (only \"big\"/\"little\"/\"network\"; \"native\" is forbidden, since it makes the wire format depend on the running machine)"
        ))),
    }
}

fn check_keys(opts: &Bound<'_, PyDict>, allowed: &[&str], kind: &str) -> PyResult<()> {
    for key in opts.keys() {
        let k: String = key
            .extract()
            .map_err(|_| schema_err(format!("{kind}: opts keys must be str")))?;
        if !allowed.contains(&k.as_str()) {
            return Err(schema_err(format!("{kind}: unknown option {k:?}")));
        }
    }
    Ok(())
}

fn get<'py>(opts: &Bound<'py, PyDict>, key: &str) -> PyResult<Option<Bound<'py, PyAny>>> {
    match opts.get_item(key)? {
        Some(v) if !v.is_none() => Ok(Some(v)),
        _ => Ok(None),
    }
}

fn parse_expr(v: &Bound<'_, PyAny>) -> PyResult<ExprIn> {
    if let Ok(s) = v.downcast::<PyString>() {
        let s: String = s.extract()?;
        if s == "*" {
            return Ok(ExprIn::Greedy);
        }
        return Err(schema_err(format!(
            "expression: unexpected string {s:?} (only \"*\" is valid)"
        )));
    }
    if v.downcast::<PyInt>().is_ok() {
        let n: i64 = v
            .extract()
            .map_err(|_| schema_err("expression literal does not fit in i64"))?;
        return Ok(ExprIn::Imm(n));
    }
    let t = v
        .downcast::<PyTuple>()
        .map_err(|_| schema_err("expression: expected an int, \"*\", or a tuple"))?;
    if t.len() == 0 {
        return Err(schema_err("expression: empty tuple"));
    }
    let head: String = t
        .get_item(0)?
        .extract()
        .map_err(|_| schema_err("expression: the first tuple element must be a str"))?;
    if head == "ref" {
        if t.len() != 2 {
            return Err(schema_err("(\"ref\", name): exactly two elements"));
        }
        let name: String = t
            .get_item(1)?
            .extract()
            .map_err(|_| schema_err("(\"ref\", name): name must be a str"))?;
        return Ok(ExprIn::Ref(name));
    }
    let op = match head.as_str() {
        "add" => BinOp::Add,
        "sub" => BinOp::Sub,
        "mul" => BinOp::Mul,
        "div" => BinOp::Div,
        "shl" => BinOp::Shl,
        "shr" => BinOp::Shr,
        "and" => BinOp::And,
        "or" => BinOp::Or,
        "xor" => BinOp::Xor,
        "eq" => BinOp::Eq,
        "ne" => BinOp::Ne,
        "lt" => BinOp::Lt,
        "le" => BinOp::Le,
        "gt" => BinOp::Gt,
        "ge" => BinOp::Ge,
        other => {
            return Err(schema_err(format!(
                "expression: unknown operation {other:?}"
            )))
        }
    };
    if t.len() != 3 {
        return Err(schema_err(format!(
            "(\"{head}\", a, b): exactly three elements"
        )));
    }
    Ok(ExprIn::Bin(
        op,
        Box::new(parse_expr(&t.get_item(1)?)?),
        Box::new(parse_expr(&t.get_item(2)?)?),
    ))
}

fn int_prim(kind: &str) -> Option<IntPrim> {
    Some(match kind {
        "u8" => IntPrim::U8,
        "i8" => IntPrim::I8,
        "u16" => IntPrim::U16,
        "i16" => IntPrim::I16,
        "u32" => IntPrim::U32,
        "i32" => IntPrim::I32,
        "u64" => IntPrim::U64,
        "i64" => IntPrim::I64,
        _ => return None,
    })
}

fn parse_type(kind: &str, opts: &Bound<'_, PyDict>) -> PyResult<TypeIn> {
    if let Some(prim) = int_prim(kind) {
        check_keys(opts, &["byteorder", "const"], kind)?;
        let byteorder = get(opts, "byteorder")?
            .map(|v| parse_byteorder(&v))
            .transpose()?;
        let const_ = get(opts, "const")?
            .map(|v| {
                v.extract::<i128>()
                    .map_err(|_| schema_err(format!("{kind}: const must be an int")))
            })
            .transpose()?;
        return Ok(TypeIn::Int {
            prim,
            byteorder,
            const_,
        });
    }
    Ok(match kind {
        "f32" | "f64" => {
            check_keys(opts, &["byteorder"], kind)?;
            let byteorder = get(opts, "byteorder")?
                .map(|v| parse_byteorder(&v))
                .transpose()?;
            TypeIn::Float {
                is64: kind == "f64",
                byteorder,
            }
        }
        "bool" => {
            check_keys(opts, &["const"], kind)?;
            let const_ = get(opts, "const")?
                .map(|v| {
                    v.extract::<bool>()
                        .map_err(|_| schema_err("bool: const must be a bool"))
                })
                .transpose()?;
            TypeIn::Bool { const_ }
        }
        "raw" => {
            check_keys(opts, &["len", "const"], kind)?;
            let len = get(opts, "len")?
                .map(|v| {
                    v.extract::<usize>()
                        .map_err(|_| schema_err("raw: len must be a non-negative int"))
                })
                .transpose()?;
            let const_ = get(opts, "const")?
                .map(|v| {
                    v.extract::<Vec<u8>>()
                        .map_err(|_| schema_err("raw: const must be bytes"))
                })
                .transpose()?;
            TypeIn::Raw { len, const_ }
        }
        "bytes" => {
            check_keys(opts, &["len", "max"], kind)?;
            let len = get(opts, "len")?.ok_or_else(|| schema_err("bytes: len is required"))?;
            TypeIn::Bytes {
                len: parse_expr(&len)?,
                max: get(opts, "max")?
                    .map(|v| {
                        v.extract::<usize>()
                            .map_err(|_| schema_err("bytes: max must be an int"))
                    })
                    .transpose()?,
            }
        }
        "str" => {
            check_keys(opts, &["len", "max", "encoding", "errors"], kind)?;
            let len = get(opts, "len")?.ok_or_else(|| schema_err("str: len is required"))?;
            TypeIn::StrT {
                len: parse_expr(&len)?,
                max: get(opts, "max")?
                    .map(|v| v.extract::<usize>())
                    .transpose()
                    .map_err(|_| schema_err("str: max must be an int"))?,
                encoding: get(opts, "encoding")?
                    .map(|v| v.extract::<String>())
                    .transpose()
                    .map_err(|_| schema_err("str: encoding must be a str"))?
                    .unwrap_or_else(|| "utf-8".to_string()),
                errors: get(opts, "errors")?
                    .map(|v| v.extract::<String>())
                    .transpose()
                    .map_err(|_| schema_err("str: errors must be a str"))?
                    .unwrap_or_else(|| "strict".to_string()),
            }
        }
        "cstr" => {
            check_keys(opts, &["max", "encoding", "errors"], kind)?;
            TypeIn::CStrT {
                max: get(opts, "max")?
                    .map(|v| v.extract::<usize>())
                    .transpose()
                    .map_err(|_| schema_err("cstr: max must be an int"))?,
                encoding: get(opts, "encoding")?
                    .map(|v| v.extract::<String>())
                    .transpose()
                    .map_err(|_| schema_err("cstr: encoding must be a str"))?
                    .unwrap_or_else(|| "utf-8".to_string()),
                errors: get(opts, "errors")?
                    .map(|v| v.extract::<String>())
                    .transpose()
                    .map_err(|_| schema_err("cstr: errors must be a str"))?
                    .unwrap_or_else(|| "strict".to_string()),
            }
        }
        "bits" => {
            check_keys(opts, &["width", "signed"], kind)?;
            let width = get(opts, "width")?
                .ok_or_else(|| schema_err("bits: width is required"))?
                .extract::<u8>()
                .map_err(|_| schema_err("bits: width must be an int in 1..64"))?;
            let signed = get(opts, "signed")?
                .map(|v| v.extract::<bool>())
                .transpose()
                .map_err(|_| schema_err("bits: signed must be a bool"))?
                .unwrap_or(false);
            TypeIn::Bits { width, signed }
        }
        "flags" => {
            check_keys(opts, &["base", "names", "rest", "byteorder"], kind)?;
            let base_s: String = get(opts, "base")?
                .ok_or_else(|| schema_err("flags: base is required"))?
                .extract()
                .map_err(|_| schema_err("flags: base must be a str (\"u8\"..\"u64\")"))?;
            let base = int_prim(&base_s).ok_or_else(|| {
                schema_err(format!("flags: base {base_s:?} is not an integer type"))
            })?;
            let names_obj =
                get(opts, "names")?.ok_or_else(|| schema_err("flags: names is required"))?;
            let mut names = Vec::new();
            for pair in names_obj
                .try_iter()
                .map_err(|_| schema_err("flags: names must be a sequence of pairs"))?
            {
                let (n, mask): (String, u64) = pair?
                    .extract()
                    .map_err(|_| schema_err("flags: names entries are (str, int) pairs"))?;
                names.push((n, mask));
            }
            TypeIn::FlagsT {
                base,
                byteorder: get(opts, "byteorder")?
                    .map(|v| parse_byteorder(&v))
                    .transpose()?,
                names,
                rest: get(opts, "rest")?
                    .map(|v| v.extract::<String>())
                    .transpose()
                    .map_err(|_| schema_err("flags: rest must be a str"))?
                    .unwrap_or_else(|| "keep".to_string()),
            }
        }
        "digest" => {
            check_keys(
                opts,
                &[
                    "algo", "over", "verify", "poly", "init", "xorout", "refin", "refout",
                ],
                kind,
            )?;
            let algo: String = get(opts, "algo")?
                .ok_or_else(|| schema_err("digest: algo is required"))?
                .extract()
                .map_err(|_| schema_err("digest: algo must be a str"))?;
            let over_obj =
                get(opts, "over")?.ok_or_else(|| schema_err("digest: over is required"))?;
            let over = if let Ok(s) = over_obj.extract::<String>() {
                if s == "*" {
                    OverIn::Star
                } else {
                    return Err(schema_err(
                        "digest: over is either \"*\" or a tuple of names",
                    ));
                }
            } else {
                let mut names = Vec::new();
                for n in over_obj
                    .try_iter()
                    .map_err(|_| schema_err("digest: over is either \"*\" or a tuple of names"))?
                {
                    names.push(
                        n?.extract::<String>()
                            .map_err(|_| schema_err("digest: names in over must be str"))?,
                    );
                }
                OverIn::Names(names)
            };
            let overrides = CrcOverrides {
                poly: get(opts, "poly")?
                    .map(|v| v.extract::<u64>())
                    .transpose()
                    .map_err(|_| schema_err("digest: poly must be an int"))?,
                init: get(opts, "init")?
                    .map(|v| v.extract::<u64>())
                    .transpose()
                    .map_err(|_| schema_err("digest: init must be an int"))?,
                xorout: get(opts, "xorout")?
                    .map(|v| v.extract::<u64>())
                    .transpose()
                    .map_err(|_| schema_err("digest: xorout must be an int"))?,
                refin: get(opts, "refin")?
                    .map(|v| v.extract::<bool>())
                    .transpose()
                    .map_err(|_| schema_err("digest: refin must be a bool"))?,
                refout: get(opts, "refout")?
                    .map(|v| v.extract::<bool>())
                    .transpose()
                    .map_err(|_| schema_err("digest: refout must be a bool"))?,
            };
            let verify = get(opts, "verify")?
                .map(|v| v.extract::<bool>())
                .transpose()
                .map_err(|_| schema_err("digest: verify must be a bool"))?
                .unwrap_or(true);
            TypeIn::DigestT {
                algo,
                overrides,
                over,
                verify,
            }
        }
        "struct" => {
            check_keys(opts, &["fields", "byteorder", "size"], kind)?;
            let fields_obj =
                get(opts, "fields")?.ok_or_else(|| schema_err("struct: fields is required"))?;
            TypeIn::StructT {
                fields: parse_fields(&fields_obj)?,
                byteorder: get(opts, "byteorder")?
                    .map(|v| parse_byteorder(&v))
                    .transpose()?,
                size: get(opts, "size")?.map(|v| parse_expr(&v)).transpose()?,
            }
        }
        "array" => {
            check_keys(opts, &["elem", "count", "until_eof"], kind)?;
            let elem_obj =
                get(opts, "elem")?.ok_or_else(|| schema_err("array: elem is required"))?;
            TypeIn::ArrayT {
                elem: Box::new(parse_type_spec(&elem_obj)?),
                count: get(opts, "count")?.map(|v| parse_expr(&v)).transpose()?,
                until_eof: get(opts, "until_eof")?
                    .map(|v| v.extract::<bool>())
                    .transpose()
                    .map_err(|_| schema_err("array: until_eof must be a bool"))?
                    .unwrap_or(false),
            }
        }
        "switch" => {
            check_keys(opts, &["on", "cases", "default"], kind)?;
            let on = get(opts, "on")?.ok_or_else(|| schema_err("switch: on is required"))?;
            let cases_obj =
                get(opts, "cases")?.ok_or_else(|| schema_err("switch: cases is required"))?;
            let mut cases = Vec::new();
            for case in cases_obj.try_iter().map_err(|_| {
                schema_err("switch: cases must be a sequence of (int, (kind, opts)) pairs")
            })? {
                let case = case?;
                let pair = case.downcast::<PyTuple>().map_err(|_| {
                    schema_err("switch: a cases element must be a (int, (kind, opts)) tuple")
                })?;
                if pair.len() != 2 {
                    return Err(schema_err(
                        "switch: a cases element must be a (int, (kind, opts)) tuple",
                    ));
                }
                let tag: i64 = pair
                    .get_item(0)?
                    .extract()
                    .map_err(|_| schema_err("switch: a branch tag must be an int (i64)"))?;
                cases.push((tag, parse_type_spec(&pair.get_item(1)?)?));
            }
            TypeIn::SwitchT {
                on: parse_expr(&on)?,
                cases,
                default: get(opts, "default")?
                    .map(|v| parse_type_spec(&v).map(Box::new))
                    .transpose()?,
            }
        }
        "cond" => {
            check_keys(opts, &["pred", "then"], kind)?;
            let pred = get(opts, "pred")?.ok_or_else(|| schema_err("cond: pred is required"))?;
            let then = get(opts, "then")?.ok_or_else(|| schema_err("cond: then is required"))?;
            TypeIn::CondT {
                pred: parse_expr(&pred)?,
                then: Box::new(parse_type_spec(&then)?),
            }
        }
        other => return Err(schema_err(format!("unknown kind {other:?}"))),
    })
}

/// (kind, opts) — a type in the elem/case/default position.
fn parse_type_spec(obj: &Bound<'_, PyAny>) -> PyResult<TypeIn> {
    let t = obj
        .downcast::<PyTuple>()
        .map_err(|_| schema_err("a type spec must be a (kind, opts) tuple"))?;
    if t.len() != 2 {
        return Err(schema_err("a type spec must be a (kind, opts) tuple"));
    }
    let kind: String = t
        .get_item(0)?
        .extract()
        .map_err(|_| schema_err("kind must be a str"))?;
    let opts_obj = t.get_item(1)?;
    let opts = opts_obj
        .downcast::<PyDict>()
        .map_err(|_| schema_err("opts must be a dict"))?;
    parse_type(&kind, opts)
}

fn parse_fields(obj: &Bound<'_, PyAny>) -> PyResult<Vec<FieldIn>> {
    let mut out = Vec::new();
    for item in obj
        .try_iter()
        .map_err(|_| schema_err("fields must be a tuple of fields"))?
    {
        let item = item?;
        let t = item
            .downcast::<PyTuple>()
            .map_err(|_| schema_err("a field must be a (name, kind, opts) tuple"))?;
        if t.len() != 3 {
            return Err(schema_err("a field must be a (name, kind, opts) tuple"));
        }
        let name_obj = t.get_item(0)?;
        let name: Option<String> = if name_obj.is_none() {
            None
        } else {
            Some(
                name_obj
                    .extract()
                    .map_err(|_| schema_err("field name must be a str or None"))?,
            )
        };
        let kind: String = t
            .get_item(1)?
            .extract()
            .map_err(|_| schema_err("kind must be a str"))?;
        let opts_obj = t.get_item(2)?;
        let opts = opts_obj
            .downcast::<PyDict>()
            .map_err(|_| schema_err("opts must be a dict"))?;
        out.push(FieldIn {
            name,
            ty: parse_type(&kind, opts)?,
        });
    }
    Ok(out)
}

// ---------- unpack: building straight into PyObjects, no Value tree ----------
//
// unpack::run_with() (crates/rustruct/src/model.rs) is generic over what a
// decode actually constructs. PyBuilder implements Builder directly over
// pyo3 types, so unpack constructs the real PyDict/PyList/etc PyObjects as
// it decodes, with no intermediate `Value` tree and no second walk to
// convert one. rustruct-core itself is untouched: this impl lives entirely
// here, in the pyo3 crate.
//
// Builder's methods are infallible by design (see model.rs's doc comment);
// the `.expect()`s below reflect that these specific pyo3 operations
// (constructing a PyLong/PyBytes/PyString from a valid Rust value, or
// inserting a plain str key into a dict/appending to a list) cannot
// actually fail while the GIL is held -- there's no real error to recover
// from, just an infallible-in-practice PyResult signature to satisfy.
struct PyBuilder<'py, 'c> {
    py: Python<'py>,
    key_cache: &'c KeyCache,
}

impl<'py, 'c> Builder for PyBuilder<'py, 'c> {
    type Val = Py<PyAny>;
    type Map = Bound<'py, PyDict>;
    type List = Vec<Py<PyAny>>;

    fn int(&mut self, v: i128) -> Py<PyAny> {
        // The fast machine-word path for the overwhelming majority of
        // real fields (anything fitting u32/i32 and then some), instead
        // of always going through i128's generic byte-array conversion.
        match i64::try_from(v) {
            Ok(x) => x
                .into_pyobject(self.py)
                .expect("int conversion")
                .into_any()
                .unbind(),
            Err(_) => v
                .into_pyobject(self.py)
                .expect("int128 conversion")
                .into_any()
                .unbind(),
        }
    }
    fn float(&mut self, v: f64) -> Py<PyAny> {
        v.into_pyobject(self.py)
            .expect("float conversion")
            .into_any()
            .unbind()
    }
    fn bool_(&mut self, v: bool) -> Py<PyAny> {
        PyBool::new(self.py, v).to_owned().into_any().unbind()
    }
    fn bytes(&mut self, v: Vec<u8>) -> Py<PyAny> {
        PyBytes::new(self.py, &v).into_any().unbind()
    }
    fn string(&mut self, v: String) -> Py<PyAny> {
        PyString::new(self.py, &v).into_any().unbind()
    }

    fn map_new(&mut self, _hint: usize) -> Bound<'py, PyDict> {
        PyDict::new(self.py)
    }
    fn map_set(&mut self, m: &mut Bound<'py, PyDict>, key: &Arc<str>, v: Py<PyAny>) {
        let py_key = self
            .key_cache
            .get(key)
            .expect("every schema key is pre-cached in Codec::key_cache at compile time");
        m.set_item(py_key.bind(self.py), v)
            .expect("dict insertion with a cached str key");
    }
    fn map_finish(&mut self, m: Bound<'py, PyDict>) -> Py<PyAny> {
        m.into_any().unbind()
    }

    fn list_new(&mut self, hint: usize) -> Vec<Py<PyAny>> {
        Vec::with_capacity(hint)
    }
    fn list_push(&mut self, l: &mut Vec<Py<PyAny>>, v: Py<PyAny>) {
        l.push(v);
    }
    fn list_finish(&mut self, l: Vec<Py<PyAny>>) -> Py<PyAny> {
        PyList::new(self.py, l)
            .expect("list construction from an owned Vec")
            .into_any()
            .unbind()
    }
}

// ---------- pack: reading straight from PyObjects, no Value tree ----------
//
// pack::run_with() (crates/rustruct/src/pack.rs) is generic over what
// pack() actually reads from the caller's input. PySource implements
// Source directly over pyo3 types, so pack() reads a field's value the
// moment pack_op/pack_value actually needs it and never builds a `Value`
// tree at all -- including for flags (its closed key-set check runs
// directly against the caller's own dict/Mapping keys) and
// switch (whose branch is only known once register state exists inside
// pack::run itself). rustruct-core itself is untouched: this impl lives
// entirely here.
//
// Source's methods are infallible by design (see model.rs's doc comment):
// they mirror Value's own *structural* coercion rules via type-check-style
// downcasts only (PyBool checked before PyInt so a bool never
// misclassifies as a plain int; PyBytes/PyByteArray/buffer-protocol for
// bytes; a *closed* PyDict-or-generic-Mapping check for map-shaped
// values) -- nothing here calls into arbitrary Python code (`__bool__`,
// `__eq__`, ...) that could raise, so there's no error to propagate.
enum PyMapView<'py> {
    Dict(Bound<'py, PyDict>),
    /// An arbitrary Mapping (has `.items()` but isn't a dict): collected
    /// once per scope, exactly like `Dict`'s O(1) per-field lookup, just
    /// backed by our own hash map instead of CPython's.
    Generic(HashMap<String, Bound<'py, PyAny>>),
}

/// `v.extract::<i128>()` unconditionally takes pyo3's own
/// `slow_128bit_int_conversion` path (a generic byte-array algorithm --
/// confirmed by symbol name in the compiled extension), regardless of how
/// small the actual value is, since that slowness is a property of the
/// *type* asked for, not the runtime value. Extracting as i64 first hits
/// the direct `PyLong_AsLongLong`-style fast path instead, covering every
/// real field width (u32/i32 and below) and then some; only a value that
/// doesn't fit falls back to the slow i128 path. Mirrors `PyBuilder::int`'s
/// same fast-path choice in the opposite (construction) direction.
fn extract_int(v: &Bound<'_, PyAny>) -> Option<i128> {
    match v.extract::<i64>() {
        Ok(x) => Some(i128::from(x)),
        Err(_) => v.extract::<i128>().ok(),
    }
}

/// Every method reads from the `Bound` it's handed, which already carries
/// its own Python attachment (`.py()`) -- `'py` is only here to fix
/// `Self::Val`'s lifetime, mirroring `ValueSource`'s own `PhantomData` in
/// model.rs. `key_cache` is the one real field: `Codec::key_cache`,
/// borrowed so `view_get` can look a field up using the same cached
/// `Py<PyString>` every call instead of building a fresh one per lookup.
struct PySource<'py, 'c> {
    key_cache: &'c KeyCache,
    _marker: std::marker::PhantomData<Python<'py>>,
}

impl<'py, 'c> Source for PySource<'py, 'c> {
    type Val = Bound<'py, PyAny>;
    type MapView = PyMapView<'py>;

    fn map_view(&self, v: &Bound<'py, PyAny>) -> Option<PyMapView<'py>> {
        if let Ok(d) = v.downcast::<PyDict>() {
            return Some(PyMapView::Dict(d.clone()));
        }
        // An arbitrary Mapping: same fallback py_to_value always had.
        let items = v.call_method0("items").ok()?;
        let mut map = HashMap::new();
        for pair in items.try_iter().ok()?.flatten() {
            if let Ok((k, val)) = pair.extract::<(Bound<'py, PyAny>, Bound<'py, PyAny>)>() {
                if let Ok(key) = k.extract::<String>() {
                    map.insert(key, val);
                }
            }
        }
        Some(PyMapView::Generic(map))
    }

    fn view_get(&self, view: &PyMapView<'py>, key: &str) -> Option<Bound<'py, PyAny>> {
        match view {
            PyMapView::Dict(d) => {
                let py_key = self
                    .key_cache
                    .get(key)
                    .expect("every schema key is pre-cached in Codec::key_cache at compile time");
                d.get_item(py_key.bind(d.py())).ok().flatten()
            }
            PyMapView::Generic(m) => m.get(key).cloned(),
        }
    }

    fn view_keys(&self, view: &PyMapView<'py>) -> Vec<Arc<str>> {
        match view {
            PyMapView::Dict(d) => d
                .keys()
                .iter()
                .filter_map(|k| k.extract::<String>().ok())
                .map(|s| Arc::from(s.as_str()))
                .collect(),
            PyMapView::Generic(m) => m.keys().map(|s| Arc::from(s.as_str())).collect(),
        }
    }

    fn as_list(&self, v: &Bound<'py, PyAny>) -> Option<Vec<Bound<'py, PyAny>>> {
        // Direct indexed iteration, not the general __iter__/__next__
        // protocol (`try_iter`): a perf profile of this exact call site
        // showed PyObject_SelfIter/PyIter_Next/Flatten::next as real,
        // avoidable cost once we already know (via the downcast) that
        // this is a concrete list/tuple, not an arbitrary iterable.
        if let Ok(l) = v.downcast::<PyList>() {
            return Some(l.iter().collect());
        }
        if let Ok(t) = v.downcast::<PyTuple>() {
            return Some(t.iter().collect());
        }
        None
    }

    fn as_int(&self, v: &Bound<'py, PyAny>) -> Option<i128> {
        // PyBool first: bool is a Python int subclass, so downcast::<PyInt>
        // would also match True/False -- checking Bool first (and coercing
        // it to 0/1) matches Value's own Int-accepts-Bool coercion exactly.
        if let Ok(b) = v.downcast::<PyBool>() {
            return Some(i128::from(b.is_true()));
        }
        if v.downcast::<PyInt>().is_ok() {
            return extract_int(v);
        }
        None
    }

    fn as_float(&self, v: &Bound<'py, PyAny>) -> Option<f64> {
        if let Ok(f) = v.downcast::<PyFloat>() {
            return Some(f.value());
        }
        // int-as-float, but deliberately *not* bool-as-float (float_of's
        // existing behavior never coerced Value::Bool either).
        if v.downcast::<PyInt>().is_ok() && v.downcast::<PyBool>().is_err() {
            return Some(extract_int(v)? as f64);
        }
        None
    }

    fn as_bytes(&self, v: &Bound<'py, PyAny>) -> Option<Vec<u8>> {
        if let Ok(b) = v.downcast::<PyBytes>() {
            return Some(b.as_bytes().to_vec());
        }
        if let Ok(b) = v.downcast::<PyByteArray>() {
            return Some(b.to_vec());
        }
        // buffer protocol (memoryview, mmap, array, ...)
        if let Ok(buffer) = PyBuffer::<u8>::get(v) {
            if buffer.is_c_contiguous() {
                let data = unsafe {
                    std::slice::from_raw_parts(buffer.buf_ptr() as *const u8, buffer.len_bytes())
                };
                return Some(data.to_vec());
            }
        }
        None
    }

    fn as_str(&self, v: &Bound<'py, PyAny>) -> Option<String> {
        v.downcast::<PyString>().ok().and_then(|s| s.extract().ok())
    }

    fn truthy(&self, v: &Bound<'py, PyAny>) -> Option<bool> {
        if let Ok(b) = v.downcast::<PyBool>() {
            return Some(b.is_true());
        }
        if let Ok(i) = v.downcast::<PyInt>() {
            return i.extract::<i128>().ok().map(|x| x != 0);
        }
        if let Ok(f) = v.downcast::<PyFloat>() {
            return Some(f.value() != 0.0);
        }
        if let Ok(s) = v.downcast::<PyString>() {
            return Some(s.len().unwrap_or(0) != 0);
        }
        if let Ok(b) = v.downcast::<PyBytes>() {
            return Some(!b.as_bytes().is_empty());
        }
        if let Ok(b) = v.downcast::<PyByteArray>() {
            return Some(b.len() != 0);
        }
        if let Ok(l) = v.downcast::<PyList>() {
            return Some(l.len() != 0);
        }
        if let Ok(t) = v.downcast::<PyTuple>() {
            return Some(t.len() != 0);
        }
        if let Ok(d) = v.downcast::<PyDict>() {
            return Some(d.len() != 0);
        }
        None
    }
}

// ---------- buffers ----------

fn with_buffer<R>(
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
    f: impl FnOnce(&[u8]) -> PyResult<R>,
) -> PyResult<R> {
    // A PyBUF_SIMPLE view held only for the duration of the call, not
    // retained after return.
    let buffer = PyBuffer::<u8>::get(obj)?;
    if !buffer.is_c_contiguous() {
        return Err(PyTypeError::new_err("expected a C-contiguous buffer"));
    }
    let data =
        unsafe { std::slice::from_raw_parts(buffer.buf_ptr() as *const u8, buffer.len_bytes()) };
    let r = f(data);
    drop(buffer);
    let _ = py;
    r
}

// ---------- public classes ----------

/// The result of parse() when data is missing: falsy.
#[pyclass(module = "rustruct.core", frozen)]
pub struct Incomplete {
    /// Minimum bytes missing beyond the end of the buffer (a lower bound).
    #[pyo3(get)]
    needed: usize,
}

#[pymethods]
impl Incomplete {
    fn __bool__(&self) -> bool {
        false
    }
    fn __repr__(&self) -> String {
        format!("Incomplete(needed={})", self.needed)
    }
}

/// Every schema field name gets exactly one `Py<PyString>`, built once
/// here (at `compile()` time) and reused for every `pack()`/`unpack()`
/// call on this `Codec` -- a perf profile (`perf record` under Linux,
/// since macOS without Xcode has no usable sampling profiler) showed
/// `PyUnicode_FromStringAndSize` as real, repeated cost on both
/// `PySource::view_get` (pack, looking a key up in the caller's dict) and
/// `PyBuilder::map_set` (unpack, inserting one into a freshly-built
/// dict): a fresh PyUnicode object for the exact same fixed field name,
/// every field, every call. Reusing one cached object also lets CPython's
/// own per-string hash cache pay off across calls, not just within one.
fn build_key_cache(py: Python<'_>, prog: &Program, cache: &mut KeyCache) {
    for op in &prog.ops {
        collect_op_keys(py, op, cache);
    }
}

fn intern_key(py: Python<'_>, name: &Arc<str>, cache: &mut KeyCache) {
    cache
        .entry(name.clone())
        .or_insert_with(|| PyString::new(py, name).unbind());
}

fn collect_op_keys(py: Python<'_>, op: &Op, cache: &mut KeyCache) {
    match op {
        Op::Fixed { items, .. } => {
            for item in items {
                if let Key::Named(name) = &item.key {
                    intern_key(py, name, cache);
                }
            }
        }
        Op::BitRun { items, .. } => {
            for item in items {
                if let Key::Named(name) = &item.key {
                    intern_key(py, name, cache);
                }
            }
        }
        Op::Flags { items, rest, c, .. } => {
            if let Some(name) = c.key.name() {
                intern_key(py, name, cache);
            }
            for item in items {
                intern_key(py, &item.key, cache);
            }
            if matches!(rest, RestPolicy::Keep) {
                intern_key(py, &Arc::from("_rest"), cache);
            }
        }
        Op::Digest { c, .. } | Op::Bytes { c, .. } | Op::Str { c, .. } | Op::CStr { c, .. } => {
            if let Some(name) = c.key.name() {
                intern_key(py, name, cache);
            }
        }
        Op::Nest { prog: inner, c, .. } => {
            if let Some(name) = c.key.name() {
                intern_key(py, name, cache);
            }
            build_key_cache(py, inner, cache);
        }
        Op::Array { elem, c, .. } => {
            if let Some(name) = c.key.name() {
                intern_key(py, name, cache);
            }
            collect_op_keys(py, elem, cache);
        }
        Op::Switch {
            cases, default, c, ..
        } => {
            if let Some(name) = c.key.name() {
                intern_key(py, name, cache);
            }
            for (_, case_op) in cases {
                collect_op_keys(py, case_op, cache);
            }
            if let Some(d) = default {
                collect_op_keys(py, d, cache);
            }
        }
        Op::Cond { then, c, .. } => {
            if let Some(name) = c.key.name() {
                intern_key(py, name, cache);
            }
            collect_op_keys(py, then, cache);
        }
    }
}

#[pyclass(module = "rustruct.core", frozen)]
pub struct Codec {
    prog: Arc<Program>,
    key_cache: KeyCache,
}

#[pymethods]
impl Codec {
    /// Requires the buffer to be fully consumed; a tail raises
    /// InvalidDataError with kind="trailing".
    fn unpack(&self, py: Python<'_>, buf: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        with_buffer(py, buf, |data| {
            let mut builder = PyBuilder {
                py,
                key_cache: &self.key_cache,
            };
            match core_unpack(&self.prog, &mut builder, data, 0, true, false) {
                Outcome::Ok { value, .. } => Ok(value),
                Outcome::Incomplete { .. } => unreachable!("stream=false"),
                Outcome::Invalid { kind, offset, path } => {
                    Err(invalid_err(py, kind.as_str(), offset, &path))
                }
            }
        })
    }

    /// A trailing tail is allowed; returns (dict, new position).
    #[pyo3(signature = (buf, offset = 0))]
    fn unpack_from(
        &self,
        py: Python<'_>,
        buf: &Bound<'_, PyAny>,
        offset: usize,
    ) -> PyResult<(Py<PyAny>, usize)> {
        with_buffer(py, buf, |data| {
            let mut builder = PyBuilder {
                py,
                key_cache: &self.key_cache,
            };
            match core_unpack(&self.prog, &mut builder, data, offset, false, false) {
                Outcome::Ok { value, pos } => Ok((value, pos)),
                Outcome::Incomplete { .. } => unreachable!("stream=false"),
                Outcome::Invalid { kind, offset, path } => {
                    Err(invalid_err(py, kind.as_str(), offset, &path))
                }
            }
        })
    }

    /// Streaming parse: a data shortage yields Incomplete, not an exception.
    #[pyo3(signature = (buf, offset = 0))]
    fn parse(&self, py: Python<'_>, buf: &Bound<'_, PyAny>, offset: usize) -> PyResult<Py<PyAny>> {
        with_buffer(py, buf, |data| {
            let mut builder = PyBuilder {
                py,
                key_cache: &self.key_cache,
            };
            match core_unpack(&self.prog, &mut builder, data, offset, false, true) {
                Outcome::Ok { value, pos } => {
                    let t = PyTuple::new(py, [value, pos.into_pyobject(py)?.into_any().unbind()])?;
                    Ok(t.into_any().unbind())
                }
                Outcome::Incomplete { needed } => {
                    Ok(Py::new(py, Incomplete { needed })?.into_any())
                }
                Outcome::Invalid { kind, offset, path } => {
                    Err(invalid_err(py, kind.as_str(), offset, &path))
                }
            }
        })
    }

    fn pack(&self, py: Python<'_>, values: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let source = PySource {
            key_cache: &self.key_cache,
            _marker: std::marker::PhantomData,
        };
        if source.map_view(values).is_none() {
            return Err(PyTypeError::new_err("pack: expected a Mapping"));
        }
        match core_pack(&self.prog, &source, values) {
            PackOutcome::Ok(bytes) => Ok(PyBytes::new(py, &bytes).into_any().unbind()),
            PackOutcome::Err { kind, path } => Err(pack_err(py, kind.as_str(), &path)),
        }
    }

    /// Writes into an existing writable buffer, returns the new position.
    fn pack_into(
        &self,
        py: Python<'_>,
        buf: &Bound<'_, PyAny>,
        offset: usize,
        values: &Bound<'_, PyAny>,
    ) -> PyResult<usize> {
        let source = PySource {
            key_cache: &self.key_cache,
            _marker: std::marker::PhantomData,
        };
        if source.map_view(values).is_none() {
            return Err(PyTypeError::new_err("pack_into: expected a Mapping"));
        }
        let bytes = match core_pack(&self.prog, &source, values) {
            PackOutcome::Ok(b) => b,
            PackOutcome::Err { kind, path } => return Err(pack_err(py, kind.as_str(), &path)),
        };
        let buffer = PyBuffer::<u8>::get(buf)?;
        if buffer.readonly() {
            return Err(PyTypeError::new_err("pack_into: the buffer is read-only"));
        }
        if !buffer.is_c_contiguous() {
            return Err(PyTypeError::new_err("expected a C-contiguous buffer"));
        }
        if offset > buffer.len_bytes() || buffer.len_bytes() - offset < bytes.len() {
            return Err(pack_err(py, "buffer", ""));
        }
        let dst = unsafe {
            std::slice::from_raw_parts_mut(buffer.buf_ptr() as *mut u8, buffer.len_bytes())
        };
        dst[offset..offset + bytes.len()].copy_from_slice(&bytes);
        Ok(offset + bytes.len())
    }

    /// Lower bound of the size.
    #[getter]
    fn min_size(&self) -> usize {
        self.prog.min_size
    }

    /// Exact size, if the schema is static.
    #[getter]
    fn static_size(&self) -> Option<usize> {
        self.prog.static_size
    }

    fn to_bytes(&self) -> PyResult<Py<PyAny>> {
        Err(PyNotImplementedError::new_err(
            "Program serialization is not implemented yet",
        ))
    }

    #[staticmethod]
    fn from_bytes(_data: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        Err(PyNotImplementedError::new_err(
            "Program serialization is not implemented yet",
        ))
    }
}

#[pyfunction]
#[pyo3(signature = (fields, *, byteorder = "big", max_default = 67_108_864, max_count = 16_777_216))]
fn compile(
    py: Python<'_>,
    fields: &Bound<'_, PyAny>,
    byteorder: &str,
    max_default: usize,
    max_count: usize,
) -> PyResult<Codec> {
    let bo = match byteorder {
        "big" | "network" => ByteOrder::Big,
        "little" => ByteOrder::Little,
        other => {
            return Err(schema_err(format!(
                "byteorder {other:?} is not supported (only \"big\"/\"little\"/\"network\")"
            )))
        }
    };
    let parsed = parse_fields(fields)?;
    let opts = Options {
        byteorder: bo,
        max_default,
        max_count,
    };
    let prog = core_compile(&parsed, &opts).map_err(|e| schema_err(e.msg))?;
    let mut key_cache = KeyCache::default();
    build_key_cache(py, &prog, &mut key_cache);
    Ok(Codec {
        prog: Arc::new(prog),
        key_cache,
    })
}

#[pymodule(name = "core")]
fn core_module(py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Codec>()?;
    m.add_class::<Incomplete>()?;
    m.add_function(wrap_pyfunction!(compile, m)?)?;
    m.add("RustructError", py.get_type::<RustructError>())?;
    m.add("SchemaError", py.get_type::<SchemaError>())?;
    m.add("InvalidDataError", py.get_type::<InvalidDataError>())?;
    m.add("PackError", py.get_type::<PackError>())?;
    m.add("__abi__", ABI)?;
    Ok(())
}
