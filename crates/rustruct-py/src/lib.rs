//! pyo3 bindings for the core: the `rustruct.core` module.

mod parse;

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
use rustruct_core::program::{Key, Op, Program, RestPolicy};
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

pub(crate) fn schema_err(msg: impl Into<String>) -> PyErr {
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
        if let Ok(d) = v.cast::<PyDict>() {
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
        if let Ok(l) = v.cast::<PyList>() {
            return Some(l.iter().collect());
        }
        if let Ok(t) = v.cast::<PyTuple>() {
            return Some(t.iter().collect());
        }
        None
    }

    fn as_int(&self, v: &Bound<'py, PyAny>) -> Option<i128> {
        // PyBool first: bool is a Python int subclass, so downcast::<PyInt>
        // would also match True/False -- checking Bool first (and coercing
        // it to 0/1) matches Value's own Int-accepts-Bool coercion exactly.
        if let Ok(b) = v.cast::<PyBool>() {
            return Some(i128::from(b.is_true()));
        }
        if v.cast::<PyInt>().is_ok() {
            return extract_int(v);
        }
        None
    }

    fn as_float(&self, v: &Bound<'py, PyAny>) -> Option<f64> {
        if let Ok(f) = v.cast::<PyFloat>() {
            return Some(f.value());
        }
        // int-as-float, but deliberately *not* bool-as-float (float_of's
        // existing behavior never coerced Value::Bool either).
        if v.cast::<PyInt>().is_ok() && v.cast::<PyBool>().is_err() {
            return Some(extract_int(v)? as f64);
        }
        None
    }

    fn as_bytes(&self, v: &Bound<'py, PyAny>) -> Option<Vec<u8>> {
        if let Ok(b) = v.cast::<PyBytes>() {
            return Some(b.as_bytes().to_vec());
        }
        if let Ok(b) = v.cast::<PyByteArray>() {
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
        v.cast::<PyString>().ok().and_then(|s| s.extract().ok())
    }

    fn truthy(&self, v: &Bound<'py, PyAny>) -> Option<bool> {
        if let Ok(b) = v.cast::<PyBool>() {
            return Some(b.is_true());
        }
        if let Ok(i) = v.cast::<PyInt>() {
            return i.extract::<i128>().ok().map(|x| x != 0);
        }
        if let Ok(f) = v.cast::<PyFloat>() {
            return Some(f.value() != 0.0);
        }
        if let Ok(s) = v.cast::<PyString>() {
            return Some(s.len().unwrap_or(0) != 0);
        }
        if let Ok(b) = v.cast::<PyBytes>() {
            return Some(!b.as_bytes().is_empty());
        }
        if let Ok(b) = v.cast::<PyByteArray>() {
            return Some(b.len() != 0);
        }
        if let Ok(l) = v.cast::<PyList>() {
            return Some(l.len() != 0);
        }
        if let Ok(t) = v.cast::<PyTuple>() {
            return Some(t.len() != 0);
        }
        if let Ok(d) = v.cast::<PyDict>() {
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

    /// The compiled `Program`, as Rust's own `Debug` rendering.
    ///
    /// Not an API: it lets a test assert that two schemas compile to the
    /// *same program*, which comparing pack/unpack behaviour can only
    /// approximate. Deterministic -- `Program` holds only `Vec`s,
    /// `Arc<str>`/`Arc<[u8]>` and plain enums, all of which `Debug` by value
    /// rather than by address.
    fn _program_debug(&self) -> String {
        format!("{:#?}", self.prog)
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

/// Compile a schema in the documented `(name, kind, opts)` form.
///
/// This is `rustruct.compile`. The parsing it does is generated from the
/// kind table in `parse.rs`, so the set of options a kind accepts and the
/// code that reads them come out of one declaration.
#[pyfunction]
#[pyo3(signature = (fields, *, byteorder = "big", max_default = 67_108_864, max_count = 16_777_216))]
fn compile(
    py: Python<'_>,
    fields: &Bound<'_, PyAny>,
    byteorder: &str,
    max_default: usize,
    max_count: usize,
) -> PyResult<Codec> {
    let parsed = parse::parse_fields(fields, &parse::Ctx::root())?;
    let opts = Options {
        byteorder: parse::Bo::parse(byteorder)?,
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

/// The compiled core: the compiler entry point and `Codec`.
///
/// `src/rustruct/core.pyi` is generated from this module by
/// `make stubs` -- edit here and regenerate, never the stub.
///
/// Declared as an inline Rust module rather than a function because only
/// this form is introspectable: pyo3 emits the member list at compile
/// time, and it cannot know what a function body adds.
#[pymodule(name = "core")]
mod core_module {
    #[pymodule_export]
    use super::{compile, Codec, Incomplete};

    #[pymodule_export]
    use super::{InvalidDataError, PackError, RustructError, SchemaError};

    /// Bumped on any change to the IR or the serialization format.
    #[pymodule_export]
    #[allow(non_upper_case_globals)]
    const __abi__: u32 = super::ABI;

    #[pymodule_export]
    use super::parse::vocabulary;

    // Deliberately no `#[pymodule_init]`: its presence would make pyo3 mark
    // the module incomplete and append `def __getattr__(name: str) ->
    // Incomplete: ...` to the generated stub, which tells a type checker to
    // accept *any* attribute here -- including ones that no longer exist.
}
