use std::sync::Arc;

/// Machine names for data/value errors (closed list, v1).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Truncated,
    Trailing,
    Range,
    NegativeLen,
    Overflow,
    DivZero,
    Unterminated,
    NulInCstr,
    NoCase,
    Decode,
    Limit,
    Depth,
    Checksum,
    Const,
    ReservedBits,
    // pack-specific
    Missing,
    Length,
    Indivisible,
    Inconsistent,
    Buffer,
    UnknownFlag,
    Type,
    Encode,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Truncated => "truncated",
            Kind::Trailing => "trailing",
            Kind::Range => "range",
            Kind::NegativeLen => "negative_len",
            Kind::Overflow => "overflow",
            Kind::DivZero => "div_zero",
            Kind::Unterminated => "unterminated",
            Kind::NulInCstr => "nul_in_cstr",
            Kind::NoCase => "no_case",
            Kind::Decode => "decode",
            Kind::Limit => "limit",
            Kind::Depth => "depth",
            Kind::Checksum => "checksum",
            Kind::Const => "const",
            Kind::ReservedBits => "reserved_bits",
            Kind::Missing => "missing",
            Kind::Length => "length",
            Kind::Indivisible => "indivisible",
            Kind::Inconsistent => "inconsistent",
            Kind::Buffer => "buffer",
            Kind::UnknownFlag => "unknown_flag",
            Kind::Type => "type",
            Kind::Encode => "encode",
        }
    }
}

/// A segment of the path to the field that failed. The path is assembled
/// only while unwinding: segments are pushed from innermost to outermost,
/// `format_path` reverses them.
#[derive(Debug, Clone)]
pub enum Seg {
    Field(Arc<str>),
    Index(usize),
}

pub fn format_path(path: &[Seg]) -> String {
    let mut out = String::new();
    for seg in path.iter().rev() {
        match seg {
            Seg::Field(name) => {
                if !out.is_empty() {
                    out.push('.');
                }
                out.push_str(name);
            }
            Seg::Index(i) => {
                out.push('[');
                out.push_str(&i.to_string());
                out.push(']');
            }
        }
    }
    out
}

#[derive(Debug, Clone)]
pub struct Invalid {
    pub kind: Kind,
    pub offset: usize,
    pub path: Vec<Seg>,
}

impl Invalid {
    pub fn new(kind: Kind, offset: usize) -> Self {
        Invalid {
            kind,
            offset,
            path: Vec::new(),
        }
    }
}

/// Internal outcome of a failed unpack.
#[derive(Debug, Clone)]
pub enum UFail {
    Incomplete { needed: usize },
    Invalid(Invalid),
}

impl UFail {
    pub fn invalid(kind: Kind, offset: usize) -> Self {
        UFail::Invalid(Invalid::new(kind, offset))
    }
    pub fn seg(mut self, seg: Seg) -> Self {
        if let UFail::Invalid(inv) = &mut self {
            inv.path.push(seg);
        }
        self
    }
}

pub type URes<T> = Result<T, UFail>;

#[derive(Debug, Clone)]
pub struct PackFail {
    pub kind: Kind,
    pub path: Vec<Seg>,
}

impl PackFail {
    pub fn new(kind: Kind) -> Self {
        PackFail {
            kind,
            path: Vec::new(),
        }
    }
    pub fn seg(mut self, seg: Seg) -> Self {
        self.path.push(seg);
        self
    }
}

pub type PRes<T> = Result<T, PackFail>;

/// compile() error.
#[derive(Debug, Clone)]
pub struct SchemaError {
    pub msg: String,
}

impl SchemaError {
    pub fn new(msg: impl Into<String>) -> Self {
        SchemaError { msg: msg.into() }
    }
}

pub type SRes<T> = Result<T, SchemaError>;
