//! Abstracts *what unpack() actually constructs* while it decodes, so a
//! consumer can build something other than the reference `Value` tree
//! directly during the walk -- this leaves room to eliminate the
//! intermediate `Value` tree entirely if a benchmark ever justifies it.
//! `unpack::run_with` is generic over this trait; `unpack::run` is a thin
//! convenience wrapper defaulting to `ValueBuilder` (the reference
//! implementation below, used by this crate's own tests/fuzzing and by
//! anything wanting the plain, Python-agnostic `Value` API). Other crates
//! (e.g. the pyo3 bindings) can supply their own `Builder` -- writing
//! straight into their own object model -- without rustruct-core ever
//! depending on them: the core knows nothing about Python.
//!
//! Methods are infallible: every implementation we actually have builds
//! from already-decoded, well-typed Rust values (a valid i128/f64/String/
//! Vec<u8>) with no failure mode of its own (a pyo3 implementation's
//! PyObject construction from these inputs cannot fail while the GIL is
//! held) -- there's nothing for the trait to propagate.

use std::collections::HashMap;
use std::marker::PhantomData;
use std::sync::Arc;

use crate::value::Value;

pub trait Builder {
    type Val;
    type Map;
    type List;

    fn int(&mut self, v: i128) -> Self::Val;
    fn float(&mut self, v: f64) -> Self::Val;
    fn bool_(&mut self, v: bool) -> Self::Val;
    fn bytes(&mut self, v: Vec<u8>) -> Self::Val;
    fn string(&mut self, v: String) -> Self::Val;

    fn map_new(&mut self, hint: usize) -> Self::Map;
    fn map_set(&mut self, m: &mut Self::Map, key: &Arc<str>, v: Self::Val);
    fn map_finish(&mut self, m: Self::Map) -> Self::Val;

    fn list_new(&mut self, hint: usize) -> Self::List;
    fn list_push(&mut self, l: &mut Self::List, v: Self::Val);
    fn list_finish(&mut self, l: Self::List) -> Self::Val;
}

/// The reference implementation: builds the same `Value` tree unpack
/// always built directly.
pub struct ValueBuilder;

impl Builder for ValueBuilder {
    type Val = Value;
    type Map = Value;
    type List = Vec<Value>;

    fn int(&mut self, v: i128) -> Value {
        Value::Int(v)
    }
    fn float(&mut self, v: f64) -> Value {
        Value::Float(v)
    }
    fn bool_(&mut self, v: bool) -> Value {
        Value::Bool(v)
    }
    fn bytes(&mut self, v: Vec<u8>) -> Value {
        Value::Bytes(v)
    }
    fn string(&mut self, v: String) -> Value {
        Value::Str(v)
    }

    fn map_new(&mut self, _hint: usize) -> Value {
        Value::map()
    }
    fn map_set(&mut self, m: &mut Value, key: &Arc<str>, v: Value) {
        m.insert(key.clone(), v);
    }
    fn map_finish(&mut self, m: Value) -> Value {
        m
    }

    fn list_new(&mut self, hint: usize) -> Vec<Value> {
        Vec::with_capacity(hint)
    }
    fn list_push(&mut self, l: &mut Vec<Value>, v: Value) {
        l.push(v);
    }
    fn list_finish(&mut self, l: Vec<Value>) -> Value {
        Value::List(l)
    }
}

/// The pack-direction counterpart of `Builder`: abstracts *what pack()
/// actually reads* from the caller's input, so `pack::run_with` can read
/// straight from a foreign object model (e.g. PyObjects) during the walk
/// instead of first materializing the whole input into a `Value` tree.
///
/// Methods are infallible for the same reason as `Builder`'s: they mirror
/// `Value`'s own *structural* coercion rules (an int-or-bool for an int
/// field, a float-or-int for a float field, non-recursive truthiness for a
/// bool field) rather than delegating to a foreign object's own dynamic
/// protocols (e.g. Python's `__bool__`) -- a pyo3 implementation only ever
/// does type-check-style downcasts, which cannot raise while the GIL is
/// held, so there's nothing to propagate. A value that doesn't
/// structurally match what a field expects returns `None`, which callers
/// turn into the same `Kind::Type` PackError a mismatched `Value` variant
/// already produced.
pub trait Source {
    type Val;
    type MapView;

    /// `None` if `v` isn't map/mapping-shaped at all (a schema-error at
    /// the call site: `Kind::Type`, or a top-level `TypeError` for
    /// `pack()`'s own root argument). Built *once* per struct scope, not
    /// once per field -- a naive per-field-lookup index build would
    /// reintroduce the O(n^2) cost fixed for the reference implementation
    /// below (see `FieldIndex`).
    fn map_view(&self, v: &Self::Val) -> Option<Self::MapView>;
    fn view_get(&self, view: &Self::MapView, key: &str) -> Option<Self::Val>;
    /// Every key the caller actually supplied in this map -- used only by
    /// the flags closed key-set check, the one place an
    /// *unknown* key is itself an error rather than silently ignored
    /// (every other field position ignores extra keys).
    fn view_keys(&self, view: &Self::MapView) -> Vec<Arc<str>>;

    /// A list/tuple-shaped value's elements, in order; `None` if `v` isn't
    /// list/tuple-shaped (matching `Value::List`'s own construction, which
    /// likewise only ever comes from a Python list or tuple).
    fn as_list(&self, v: &Self::Val) -> Option<Vec<Self::Val>>;

    /// int coercion: an int OR a bool (bool-as-int), matching `Value`'s
    /// own existing pack-time coercion (`Value::Bool` is accepted
    /// wherever `Value::Int` is).
    fn as_int(&self, v: &Self::Val) -> Option<i128>;
    /// float coercion: a float OR an int (int-as-float) -- deliberately
    /// *not* bool-as-float, matching `float_of`'s existing behavior.
    fn as_float(&self, v: &Self::Val) -> Option<f64>;
    /// Exact bytes match, no coercion.
    fn as_bytes(&self, v: &Self::Val) -> Option<Vec<u8>>;
    /// Exact str match, no coercion.
    fn as_str(&self, v: &Self::Val) -> Option<String>;
    /// `Value`'s own structural truthiness (bool as-is; int/float
    /// non-zero; str/bytes/list/map non-empty); `None` for anything that
    /// doesn't structurally match one of those shapes.
    fn truthy(&self, v: &Self::Val) -> Option<bool>;
}

/// Below this field count a linear scan beats a HashMap: building the map
/// costs an allocation plus per-entry hashing, which only pays for itself
/// once repeated lookups amortize it -- small structs (a typical array
/// element) are the common case and must not regress just to fix the
/// large-struct case.
const HASH_INDEX_THRESHOLD: usize = 16;

/// `ValueSource`'s per-scope field-name index: linear scan for small
/// structs (no allocation, cache-friendly), a real hash map once a struct
/// has enough fields that scanning all of them by name per field would
/// otherwise be O(n^2) for the whole struct.
pub enum FieldIndex<'a> {
    Small(&'a [(Arc<str>, Value)]),
    Hashed(HashMap<&'a str, &'a Value>),
}

impl<'a> FieldIndex<'a> {
    fn get(&self, name: &str) -> Option<&'a Value> {
        match self {
            FieldIndex::Small(pairs) => pairs.iter().find(|(k, _)| &**k == name).map(|(_, v)| v),
            FieldIndex::Hashed(map) => map.get(name).copied(),
        }
    }
}

/// The reference implementation: reads straight from the same `Value`
/// tree the core has always used. `'a` is carried by the impl (not the
/// trait), mirroring how a pyo3 `Source` carries its own `Python<'py>`.
pub struct ValueSource<'a> {
    _marker: PhantomData<&'a Value>,
}

impl<'a> ValueSource<'a> {
    pub fn new() -> Self {
        ValueSource {
            _marker: PhantomData,
        }
    }
}

impl<'a> Default for ValueSource<'a> {
    fn default() -> Self {
        Self::new()
    }
}

impl<'a> Source for ValueSource<'a> {
    type Val = &'a Value;
    type MapView = FieldIndex<'a>;

    fn map_view(&self, v: &&'a Value) -> Option<FieldIndex<'a>> {
        let Value::Map(pairs) = *v else { return None };
        Some(if pairs.len() > HASH_INDEX_THRESHOLD {
            FieldIndex::Hashed(pairs.iter().map(|(k, val)| (&**k, val)).collect())
        } else {
            FieldIndex::Small(pairs)
        })
    }
    fn view_get(&self, view: &FieldIndex<'a>, key: &str) -> Option<&'a Value> {
        view.get(key)
    }
    fn view_keys(&self, view: &FieldIndex<'a>) -> Vec<Arc<str>> {
        match view {
            FieldIndex::Small(pairs) => pairs.iter().map(|(k, _)| k.clone()).collect(),
            FieldIndex::Hashed(map) => map.keys().map(|k| Arc::from(*k)).collect(),
        }
    }

    fn as_list(&self, v: &&'a Value) -> Option<Vec<&'a Value>> {
        let Value::List(items) = *v else { return None };
        Some(items.iter().collect())
    }

    fn as_int(&self, v: &&'a Value) -> Option<i128> {
        match *v {
            Value::Int(i) => Some(*i),
            Value::Bool(b) => Some(i128::from(*b)),
            _ => None,
        }
    }
    fn as_float(&self, v: &&'a Value) -> Option<f64> {
        match *v {
            Value::Float(f) => Some(*f),
            Value::Int(i) => Some(*i as f64),
            _ => None,
        }
    }
    fn as_bytes(&self, v: &&'a Value) -> Option<Vec<u8>> {
        match *v {
            Value::Bytes(b) => Some(b.clone()),
            _ => None,
        }
    }
    fn as_str(&self, v: &&'a Value) -> Option<String> {
        match *v {
            Value::Str(s) => Some(s.clone()),
            _ => None,
        }
    }
    fn truthy(&self, v: &&'a Value) -> Option<bool> {
        Some(match *v {
            Value::Bool(b) => *b,
            Value::Int(i) => *i != 0,
            Value::Float(f) => *f != 0.0,
            Value::Str(s) => !s.is_empty(),
            Value::Bytes(b) => !b.is_empty(),
            Value::List(l) => !l.is_empty(),
            Value::Map(m) => !m.is_empty(),
            Value::Unsupported => return None,
        })
    }
}
