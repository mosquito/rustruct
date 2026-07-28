use std::sync::Arc;

use crate::names::{loose_eq, ClosedSet};

use crate::digest::Algo;
use crate::expr::Expr;

pub const MAX_REGS: usize = 16;
pub const MAX_SPANS: usize = 16;
pub const MAX_DEPTH: usize = 64;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IntPrim {
    U8,
    I8,
    U16,
    I16,
    U32,
    I32,
    U64,
    I64,
}

impl IntPrim {
    pub fn width(self) -> usize {
        match self {
            IntPrim::U8 | IntPrim::I8 => 1,
            IntPrim::U16 | IntPrim::I16 => 2,
            IntPrim::U32 | IntPrim::I32 => 4,
            IntPrim::U64 | IntPrim::I64 => 8,
        }
    }

    pub fn signed(self) -> bool {
        matches!(
            self,
            IntPrim::I8 | IntPrim::I16 | IntPrim::I32 | IntPrim::I64
        )
    }

    pub fn min(self) -> i128 {
        if self.signed() {
            -(1i128 << (self.width() * 8 - 1))
        } else {
            0
        }
    }

    pub fn max(self) -> i128 {
        if self.signed() {
            (1i128 << (self.width() * 8 - 1)) - 1
        } else {
            (1i128 << (self.width() * 8)) - 1
        }
    }
}

/// A register: `up` frames above the current one, `idx` — register number.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Reg {
    pub up: u8,
    pub idx: u8,
}

#[derive(Debug, Clone)]
pub enum Key {
    Named(Arc<str>),
    Skip,
}

impl Key {
    pub fn name(&self) -> Option<&Arc<str>> {
        match self {
            Key::Named(n) => Some(n),
            Key::Skip => None,
        }
    }
}

/// The common part of dynamic opcodes: the result key and the span register
/// (written when the field is covered by some digest).
#[derive(Debug, Clone)]
pub struct Common {
    pub key: Key,
    pub span: Option<u8>,
}

impl Common {
    pub fn named(name: Arc<str>) -> Common {
        Common {
            key: Key::Named(name),
            span: None,
        }
    }
    pub fn skip() -> Common {
        Common {
            key: Key::Skip,
            span: None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Enc {
    Utf8,
    Ascii,
    Latin1,
}

/// Text encodings a `str`/`cstr` field can use.
///
/// Matched loosely, so `"UTF-8"`, `"utf8"` and `"utf_8"` all land on the
/// same member -- which is why the spellings are written the way a person
/// would rather than in some normalized form.
pub static ENCODINGS: ClosedSet<Enc> = ClosedSet {
    what: "encoding",
    allowed: "utf-8/ascii/latin-1",
    eq: loose_eq,
    names: &[
        ("utf-8", Enc::Utf8),
        ("ascii", Enc::Ascii),
        ("latin-1", Enc::Latin1),
    ],
    aliases: &[("us-ascii", Enc::Ascii), ("iso-8859-1", Enc::Latin1)],
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RestPolicy {
    Keep,
    Strict,
    Ignore,
}

/// What a `flags` field does with bits no name in it covers.
pub static REST_POLICIES: ClosedSet<RestPolicy> = ClosedSet {
    what: "rest policy",
    allowed: "keep/strict/ignore",
    eq: str::eq,
    names: &[
        ("keep", RestPolicy::Keep),
        ("strict", RestPolicy::Strict),
        ("ignore", RestPolicy::Ignore),
    ],
    aliases: &[],
};

/// Inversion of the linear expression `value = a*x + b`: during
/// pack the derived register `reg` receives `x = (value - b) / a`.
#[derive(Debug, Clone)]
pub struct Inv {
    pub reg: Reg,
    pub a: i64,
    pub b: i64,
}

#[derive(Debug, Clone)]
pub enum LenSrc {
    Expr { expr: Expr, inv: Option<Inv> },
    Greedy,
}

#[derive(Debug, Clone)]
pub enum CountSrc {
    Expr { expr: Expr, inv: Option<Inv> },
    UntilEof,
}

#[derive(Debug, Clone)]
pub enum FixKind {
    Int {
        prim: IntPrim,
        be: bool,
        expected: Option<i128>,
        derived: bool,
    },
    F32 {
        be: bool,
    },
    F64 {
        be: bool,
    },
    Bool {
        expected: Option<bool>,
    },
    Raw {
        len: usize,
        expected: Option<Arc<[u8]>>,
    },
}

impl FixKind {
    pub fn width(&self) -> usize {
        match self {
            FixKind::Int { prim, .. } => prim.width(),
            FixKind::F32 { .. } => 4,
            FixKind::F64 { .. } => 8,
            FixKind::Bool { .. } => 1,
            FixKind::Raw { len, .. } => *len,
        }
    }
}

#[derive(Debug, Clone)]
pub struct FixedItem {
    pub off: usize,
    pub kind: FixKind,
    /// Index of the value register in the field's own frame (always written
    /// at up=0).
    pub reg: Option<u8>,
    pub span: Option<u8>,
    pub key: Key,
}

#[derive(Debug, Clone)]
pub struct BitItem {
    pub width: u8,
    pub signed: bool,
    pub reg: Option<u8>,
    pub derived: bool,
    pub key: Key,
}

#[derive(Debug, Clone)]
pub struct FlagItem {
    pub key: Arc<str>,
    pub mask: u64,
    pub shift: u32,
    /// true → single-bit mask, value is bool.
    pub is_bool: bool,
}

#[derive(Debug, Clone)]
pub enum Over {
    Star,
    /// Span registers in the order they appear in `over`.
    Spans(Vec<u8>),
}

#[derive(Debug, Clone)]
pub enum Op {
    /// A coalesced run of fixed fields: a single bounds check.
    Fixed {
        width: usize,
        items: Vec<FixedItem>,
    },
    Bytes {
        len: LenSrc,
        max: usize,
        c: Common,
    },
    Str {
        len: LenSrc,
        max: usize,
        enc: Enc,
        c: Common,
    },
    CStr {
        max: usize,
        enc: Enc,
        c: Common,
    },
    /// Merged bits fields; nbytes * 8 bits, MSB-first.
    BitRun {
        nbytes: usize,
        items: Vec<BitItem>,
    },
    Flags {
        prim: IntPrim,
        be: bool,
        items: Vec<FlagItem>,
        rest: RestPolicy,
        rest_mask: u64,
        c: Common,
    },
    Digest {
        algo: Algo,
        over: Over,
        verify: bool,
        be: bool,
        c: Common,
    },
    Nest {
        prog: Arc<Program>,
        size: Option<LenSrc>,
        c: Common,
    },
    Array {
        count: CountSrc,
        max_count: usize,
        elem: Box<Op>,
        elem_min: usize,
        c: Common,
    },
    Switch {
        on: Expr,
        cases: Vec<(i64, Box<Op>)>,
        default: Option<Box<Op>>,
        c: Common,
    },
    /// A conditionally-present field (`when`): absent (contributes zero
    /// wire bytes, no key in the decoded map) when `pred` evaluates to 0
    /// against already-known registers, present (decoded/encoded as
    /// `then`) otherwise. Only valid as a struct field, never as an array
    /// element or switch branch.
    Cond {
        pred: Expr,
        then: Box<Op>,
        c: Common,
    },
}

impl Op {
    pub fn common(&self) -> Option<&Common> {
        match self {
            Op::Fixed { .. } | Op::BitRun { .. } => None,
            Op::Bytes { c, .. }
            | Op::Str { c, .. }
            | Op::CStr { c, .. }
            | Op::Flags { c, .. }
            | Op::Digest { c, .. }
            | Op::Nest { c, .. }
            | Op::Array { c, .. }
            | Op::Switch { c, .. }
            | Op::Cond { c, .. } => Some(c),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Program {
    pub ops: Vec<Op>,
    pub nregs: u8,
    pub nspans: u8,
    pub min_size: usize,
    pub static_size: Option<usize>,
}
