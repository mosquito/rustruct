use std::sync::Arc;

/// Universal core value. The py-crate converts it to/from PyObject;
/// core tests work with it directly (the core has no Python).
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Int(i128),
    Float(f64),
    Bool(bool),
    Bytes(Vec<u8>),
    Str(String),
    /// Ordered map: insertion order matches field order.
    Map(Vec<(Arc<str>, Value)>),
    List(Vec<Value>),
    /// A value from the bindings whose type the core doesn't know. Only an
    /// error if pack actually tries to use it (extra keys are ignored).
    Unsupported,
}

impl Value {
    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Map(m) => m.iter().find(|(k, _)| &**k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn map() -> Value {
        Value::Map(Vec::new())
    }

    pub fn insert(&mut self, key: Arc<str>, value: Value) {
        if let Value::Map(m) = self {
            m.push((key, value));
        }
    }
}
