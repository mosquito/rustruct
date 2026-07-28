//! The schema boundary: the untyped `(name, kind, opts)` form -> `TypeIn`.
//!
//! What used to sit here was a 22-arm match on the kind string in which each
//! arm opened with a hand-written `check_keys(opts, &["len", "max"], kind)`
//! allowlist and then, below it, read those same keys back out one at a time.
//! Two spellings of one option set, three lines apart, with nothing keeping
//! them in step -- and they did drift: `bits(width=4, const=3)` was public,
//! documented API that the allowlist rejected because `const` was never added
//! to it.
//!
//! The `kinds!` macro below is that same table written once. Each option is
//! declared as `name: req|opt parser`, and the expansion emits, from that one
//! token:
//!
//! * the entry in the allowlist `check_keys` is handed,
//! * the `opts["name"]` lookup,
//! * the required-ness check, and
//! * the local the constructor expression below reads.
//!
//! An option that is not declared cannot be read, and one that is declared is
//! accepted -- there is no second list to forget.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyInt, PyString, PyTuple};

use rustruct_core::program::IntPrim;
use rustruct_core::schema::{BinOp, ByteOrder, CrcOverrides, ExprIn, FieldIn, OverIn, TypeIn};

use crate::schema_err;

/// How deeply a schema may nest before parsing gives up.
///
/// `parse_type`/`parse_type_spec`/`parse_fields` are mutually recursive over
/// caller-supplied data, so without a cap a deep enough schema overflows the
/// C stack and kills the interpreter outright. The core caps struct nesting
/// at 64 frames when unpacking, so anything past that already fails every
/// decode; this sits comfortably above it.
pub const MAX_SCHEMA_DEPTH: usize = 128;

/// The same cap for expression tuples, which nest independently of types.
const MAX_EXPR_DEPTH: usize = 128;

// ---------- closed sets ----------

use rustruct_core::names::ClosedSet;

/// Byte orders `compile()` takes. `network` is a struct-module-style
/// spelling of `!`, i.e. big -- an entry of its own rather than a spelling
/// of `"big"`, because it is published as a `rustruct.ByteOrder` member.
static BYTEORDERS: ClosedSet<ByteOrder> = ClosedSet {
    what: "byteorder",
    allowed: "only \"big\"/\"little\"/\"network\"; \"native\" is forbidden, since it makes \
              the wire format depend on the running machine",
    eq: str::eq,
    names: &[
        ("big", ByteOrder::Big),
        ("network", ByteOrder::Big),
        ("little", ByteOrder::Little),
    ],
    aliases: &[],
};

/// A byte order named at the top level of `compile()`.
pub fn byteorder(s: &str) -> PyResult<ByteOrder> {
    BYTEORDERS.parse(s).map_err(schema_err)
}

/// The fixed-width integer kinds.
static INT_PRIMS: ClosedSet<IntPrim> = ClosedSet {
    what: "an integer kind",
    allowed: "only \"u8\"/\"i8\" through \"u64\"/\"i64\"",
    eq: str::eq,
    names: &[
        ("u8", IntPrim::U8),
        ("i8", IntPrim::I8),
        ("u16", IntPrim::U16),
        ("i16", IntPrim::I16),
        ("u32", IntPrim::U32),
        ("i32", IntPrim::I32),
        ("u64", IntPrim::U64),
        ("i64", IntPrim::I64),
    ],
    aliases: &[],
};

/// The heads an expression tuple can carry.
static BINOPS: ClosedSet<BinOp> = ClosedSet {
    what: "an operator",
    allowed: "one of add/sub/mul/div/shl/shr/and/or/xor/eq/ne/lt/le/gt/ge",
    eq: str::eq,
    names: &[
        ("add", BinOp::Add),
        ("sub", BinOp::Sub),
        ("mul", BinOp::Mul),
        ("div", BinOp::Div),
        ("shl", BinOp::Shl),
        ("shr", BinOp::Shr),
        ("and", BinOp::And),
        ("or", BinOp::Or),
        ("xor", BinOp::Xor),
        ("eq", BinOp::Eq),
        ("ne", BinOp::Ne),
        ("lt", BinOp::Lt),
        ("le", BinOp::Le),
        ("gt", BinOp::Gt),
        ("ge", BinOp::Ge),
    ],
    aliases: &[],
};

// ---------- where parsing is ----------

/// How deep (for the cap) and where (for messages).
///
/// The path is a chain of borrowed segments rather than a string grown as
/// parsing descends. It is read in one place -- to say which field a
/// failure came from -- so building it eagerly bought nothing and cost an
/// allocation per field and per level, which on a nested schema was most
/// of what compiling one cost.
pub struct Ctx<'a> {
    depth: usize,
    parent: Option<&'a Ctx<'a>>,
    seg: Seg<'a>,
}

/// How one level spells itself in a path.
enum Seg<'a> {
    /// Adds nothing: the root, or a nested field list, which is already
    /// under its own field's name.
    Nothing,
    /// A field: `.name`, or bare with nothing above it.
    Field(&'a str),
    /// Joined with no separator: `[]`, `?default`.
    Suffix(&'a str),
    /// A switch branch, spelled `?tag`.
    Case(i64),
}

impl<'a> Ctx<'a> {
    pub fn root() -> Ctx<'static> {
        Ctx {
            depth: 0,
            parent: None,
            seg: Seg::Nothing,
        }
    }

    fn nest(&'a self, seg: Seg<'a>, deeper: bool) -> Ctx<'a> {
        Ctx {
            depth: self.depth + usize::from(deeper),
            parent: Some(self),
            seg,
        }
    }

    /// A nested field list: deeper, but named by the field it belongs to.
    fn deeper(&'a self) -> Ctx<'a> {
        self.nest(Seg::Nothing, true)
    }
    /// An array's element type.
    fn elem(&'a self) -> Ctx<'a> {
        self.nest(Seg::Suffix("[]"), true)
    }
    /// A `when`'s `then`, which shares its field's name.
    fn then(&'a self) -> Ctx<'a> {
        self.nest(Seg::Nothing, true)
    }
    /// A switch's fallback branch.
    fn default_branch(&'a self) -> Ctx<'a> {
        self.nest(Seg::Suffix("?default"), true)
    }
    /// One switch branch.
    fn case(&'a self, tag: i64) -> Ctx<'a> {
        self.nest(Seg::Case(tag), true)
    }
    /// One named (or deliberately unnamed) field inside this scope.
    fn field(&'a self, leaf: &'a str) -> Ctx<'a> {
        self.nest(Seg::Field(leaf), false)
    }

    /// Where this is, as `frame.rows[].x` -- built only to put in a message.
    fn path(&self) -> String {
        let mut chain = Vec::new();
        let mut here = Some(self);
        while let Some(ctx) = here {
            chain.push(&ctx.seg);
            here = ctx.parent;
        }
        let mut out = String::new();
        for seg in chain.iter().rev() {
            match seg {
                Seg::Nothing => {}
                Seg::Field(name) => {
                    if !out.is_empty() {
                        out.push('.');
                    }
                    out.push_str(name);
                }
                Seg::Suffix(text) => out.push_str(text),
                Seg::Case(tag) => {
                    out.push('?');
                    out.push_str(&tag.to_string());
                }
            }
        }
        out
    }
}

/// Say where a schema error came from, once. An error raised deeper already
/// carries the whole path, so it passes straight through; anything that is
/// not a `SchemaError` is not ours to annotate.
fn locate(py: Python<'_>, err: PyErr, path: &str) -> PyErr {
    if !err.is_instance_of::<crate::SchemaError>(py) {
        return err;
    }
    let msg = err.value(py).to_string();
    if msg.contains(" (at ") {
        return err;
    }
    schema_err(format!("{msg} (at {path})"))
}

// ---------- options ----------

/// `stringify!(r#const)` keeps the `r#`; the option is spelled `const` in
/// Python. The one place this is undone, so the allowlist entry and the
/// lookup below it still come from the same token.
fn opt_key(s: &'static str) -> &'static str {
    match s.strip_prefix("r#") {
        Some(k) => k,
        None => s,
    }
}

/// `None` means "not given", so an unset option can be passed explicitly.
fn opt_get<'py>(opts: &Bound<'py, PyDict>, key: &str) -> PyResult<Option<Bound<'py, PyAny>>> {
    match opts.get_item(key)? {
        Some(v) if !v.is_none() => Ok(Some(v)),
        _ => Ok(None),
    }
}

/// Edit distance for the near-miss hint below -- `difflib`'s job on the
/// Python side, in twenty lines and with no dependency.
///
/// Optimal string alignment, not plain Levenshtein: a swapped pair of
/// adjacent letters costs one edit, not two. That is the single commonest
/// typo (`lne`, `alog`, `siez`), and counting it as two puts it outside the
/// 0.6 cutoff, which is exactly where a hint is most wanted.
fn edits(a: &str, b: &str) -> usize {
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    let mut d = vec![vec![0usize; b.len() + 1]; a.len() + 1];
    for (i, row) in d.iter_mut().enumerate() {
        row[0] = i;
        for (j, cell) in row.iter_mut().enumerate() {
            if i == 0 {
                *cell = j;
            }
        }
    }
    for i in 1..=a.len() {
        for j in 1..=b.len() {
            let mut best = (d[i - 1][j] + 1)
                .min(d[i][j - 1] + 1)
                .min(d[i - 1][j - 1] + usize::from(a[i - 1] != b[j - 1]));
            if i > 1 && j > 1 && a[i - 1] == b[j - 2] && a[i - 2] == b[j - 1] {
                best = best.min(d[i - 2][j - 2] + 1);
            }
            d[i][j] = best;
        }
    }
    d[a.len()][b.len()]
}

/// The closest accepted option to a misspelling, if anything is close
/// enough. Declaration order breaks ties, so the message is deterministic.
///
/// Scored and cut off the way `difflib.get_close_matches` does -- twice the
/// characters the two share over their combined length, accepted at 0.6 --
/// so a one-letter option like `on` still gets a hint from `n`, which a
/// distance-over-longest ratio would score at 0.5 and drop.
fn nearest<'a>(word: &str, allowed: &[&'a str]) -> Option<&'a str> {
    let mut best: Option<&'a str> = None;
    let mut best_score = 0.0f64;
    let n = word.chars().count();
    for &candidate in allowed {
        let m = candidate.chars().count();
        let shared = n.max(m).saturating_sub(edits(word, candidate));
        let score = 2.0 * shared as f64 / (n + m).max(1) as f64;
        if score >= 0.6 && score > best_score {
            best_score = score;
            best = Some(candidate);
        }
    }
    best
}

fn check_keys(opts: &Bound<'_, PyDict>, allowed: &[&str], kind: &str) -> PyResult<()> {
    // Every key is checked, including one explicitly set to None: `opt_get`
    // reads that as "not given", and a misspelling would otherwise sail
    // through as long as it happened to carry None.
    for key in opts.keys() {
        // Borrowed: this runs for every option of every field, and the name
        // is only read.
        let k = key
            .cast::<PyString>()
            .map_err(|_| schema_err(format!("{kind}: opts keys must be str")))?
            .to_str()?;
        if !allowed.contains(&k) {
            return Err(schema_err(match nearest(k, allowed) {
                Some(hint) => format!("{kind}: unknown option '{k}' (did you mean '{hint}'?)"),
                None => format!("{kind}: unknown option '{k}'"),
            }));
        }
    }
    Ok(())
}

// ---------- option parsers ----------
//
// One shape for all of them -- (value, kind, key, ctx) -- so the macro can
// name any of them for any option without knowing what it needs.

macro_rules! simple {
    ($name:ident, $ty:ty, $what:literal) => {
        fn $name(v: &Bound<'_, PyAny>, kind: &str, key: &str, _ctx: &Ctx<'_>) -> PyResult<$ty> {
            v.extract()
                .map_err(|_| schema_err(format!("{kind}: {key} must be {}", $what)))
        }
    };
}

simple!(p_bool, bool, "a bool");
simple!(p_usize, usize, "a non-negative int");
simple!(p_u64, u64, "a non-negative int");
simple!(p_i128, i128, "an int");
simple!(p_bytes, Vec<u8>, "bytes");
simple!(p_string, String, "a str");

fn p_byteorder(v: &Bound<'_, PyAny>, kind: &str, key: &str, _ctx: &Ctx<'_>) -> PyResult<ByteOrder> {
    BYTEORDERS
        .parse(&p_string(v, kind, key, _ctx)?)
        .map_err(schema_err)
}

fn p_prim(v: &Bound<'_, PyAny>, kind: &str, key: &str, _ctx: &Ctx<'_>) -> PyResult<IntPrim> {
    INT_PRIMS
        .parse(&p_string(v, kind, key, _ctx)?)
        .map_err(schema_err)
}

/// Rejected here rather than at compile time, so the message names the
/// field that wrote it.
fn p_width(v: &Bound<'_, PyAny>, kind: &str, key: &str, _ctx: &Ctx<'_>) -> PyResult<u8> {
    let n: i64 = v
        .extract()
        .map_err(|_| schema_err(format!("{kind}: {key} must be an int in 1..64")))?;
    if !(1..=64).contains(&n) {
        return Err(schema_err(format!("{kind}: {key} {n} is outside 1..64")));
    }
    Ok(n as u8)
}

/// `int | "*" | ("ref", name) | (op, a, b)`.
fn expr(v: &Bound<'_, PyAny>, kind: &str, key: &str, depth: usize) -> PyResult<ExprIn> {
    // `kind`/`key` rather than a formatted label: this runs once per node
    // of every expression in the schema, and the label is only ever needed
    // to build a message, so it is put together in the error paths.
    let expected = || {
        schema_err(format!(
            "{kind}: {key}: expected an int, '*', or a tuple starting with an operator"
        ))
    };
    if depth > MAX_EXPR_DEPTH {
        return Err(schema_err(format!(
            "{kind}: {key} nests deeper than {MAX_EXPR_DEPTH} levels"
        )));
    }
    // bool is an int subclass, so an unguarded `len=True` sails through as
    // len=1 and packs a one-byte field with no complaint anywhere.
    if v.cast::<PyBool>().is_ok() {
        return Err(schema_err(format!(
            "{kind}: {key}: a bool is not a length; use an int, '*', or a reference"
        )));
    }
    if let Ok(s) = v.cast::<PyString>() {
        return if s.to_str()? == "*" {
            Ok(ExprIn::Greedy)
        } else {
            Err(expected())
        };
    }
    if v.cast::<PyInt>().is_ok() {
        return v
            .extract()
            .map(ExprIn::Imm)
            .map_err(|_| schema_err(format!("{kind}: {key} literal does not fit in i64")));
    }
    let t = v.cast::<PyTuple>().map_err(|_| expected())?;
    let head_obj = t.get_item(0).map_err(|_| expected())?;
    // Borrowed, not extracted: a `String` per operator is an allocation
    // per node, and the name is only read.
    let head = head_obj
        .cast::<PyString>()
        .map_err(|_| expected())?
        .to_str()?;
    if head == "ref" {
        if t.len() != 2 {
            return Err(schema_err(
                "(\"ref\", name): exactly two elements, name a str",
            ));
        }
        return t
            .get_item(1)?
            .extract()
            .map(ExprIn::Ref)
            .map_err(|_| schema_err("(\"ref\", name): exactly two elements, name a str"));
    }
    if t.len() != 3 {
        return Err(schema_err(format!(
            "(\"{head}\", a, b): exactly three elements"
        )));
    }
    Ok(ExprIn::Bin(
        BINOPS.parse(head).map_err(schema_err)?,
        Box::new(expr(&t.get_item(1)?, kind, key, depth + 1)?),
        Box::new(expr(&t.get_item(2)?, kind, key, depth + 1)?),
    ))
}

fn p_expr(v: &Bound<'_, PyAny>, kind: &str, key: &str, _ctx: &Ctx<'_>) -> PyResult<ExprIn> {
    expr(v, kind, key, 0)
}

fn p_over(v: &Bound<'_, PyAny>, _kind: &str, _key: &str, _ctx: &Ctx<'_>) -> PyResult<OverIn> {
    let shape = || schema_err("digest: over is either \"*\" or a tuple of names");
    // A str is iterable, so "*" has to be taken before the name list is.
    if let Ok(s) = v.cast::<PyString>() {
        return if s.to_str()? == "*" {
            Ok(OverIn::Star)
        } else {
            Err(shape())
        };
    }
    let mut names = Vec::new();
    for n in v.try_iter().map_err(|_| shape())? {
        names.push(
            n?.extract::<String>()
                .map_err(|_| schema_err("digest: names in over must be str"))?,
        );
    }
    Ok(OverIn::Names(names))
}

fn p_names(
    v: &Bound<'_, PyAny>,
    _kind: &str,
    _key: &str,
    _ctx: &Ctx<'_>,
) -> PyResult<Vec<(String, u64)>> {
    let shape = || schema_err("flags: names must be a sequence of (str, int) pairs");
    let mut names = Vec::new();
    for pair in v.try_iter().map_err(|_| shape())? {
        names.push(pair?.extract::<(String, u64)>().map_err(|_| shape())?);
    }
    Ok(names)
}

fn p_fields(
    v: &Bound<'_, PyAny>,
    _kind: &str,
    _key: &str,
    ctx: &Ctx<'_>,
) -> PyResult<Vec<FieldIn>> {
    parse_fields(v, &ctx.deeper())
}

fn p_elem(v: &Bound<'_, PyAny>, _kind: &str, _key: &str, ctx: &Ctx<'_>) -> PyResult<TypeIn> {
    parse_type_spec(v, &ctx.elem())
}

fn p_then(v: &Bound<'_, PyAny>, _kind: &str, _key: &str, ctx: &Ctx<'_>) -> PyResult<TypeIn> {
    parse_type_spec(v, &ctx.then())
}

fn p_default(v: &Bound<'_, PyAny>, _kind: &str, _key: &str, ctx: &Ctx<'_>) -> PyResult<TypeIn> {
    parse_type_spec(v, &ctx.default_branch())
}

fn p_cases(
    v: &Bound<'_, PyAny>,
    _kind: &str,
    _key: &str,
    ctx: &Ctx<'_>,
) -> PyResult<Vec<(i64, TypeIn)>> {
    let shape = || schema_err("switch: a cases element must be a (int, (kind, opts)) tuple");
    let mut cases = Vec::new();
    for case in v.try_iter().map_err(|_| shape())? {
        let case = case?;
        let pair = case.cast::<PyTuple>().map_err(|_| shape())?;
        if pair.len() != 2 {
            return Err(shape());
        }
        let tag_obj = pair.get_item(0)?;
        let bad_tag = || schema_err("switch: a branch tag must be an int (i64)");
        if tag_obj.cast::<PyBool>().is_ok() {
            return Err(bad_tag());
        }
        let tag: i64 = tag_obj.extract().map_err(|_| bad_tag())?;
        cases.push((tag, parse_type_spec(&pair.get_item(1)?, &ctx.case(tag))?));
    }
    Ok(cases)
}

/// `count=`/`until_eof=` -> the one count expression the core reads. Two
/// spellings in the public form, one value: a greedy count already means
/// "until the region ends" (`(Some(ExprIn::Greedy), false) => UntilEof`).
fn extent(count: Option<ExprIn>, until_eof: bool) -> PyResult<ExprIn> {
    match (count, until_eof) {
        (Some(_), true) => Err(schema_err(
            "array: count and until_eof are mutually exclusive",
        )),
        (None, false) => Err(schema_err("array: count or until_eof is required")),
        (None, true) => Ok(ExprIn::Greedy),
        (Some(c), false) => Ok(c),
    }
}

// ---------- the kind table ----------

/// One option: its allowlist entry, its lookup and its local, from one token.
macro_rules! bind {
    (req, $opts:expr, $kind:expr, $key:expr, $ctx:expr, $parse:expr) => {
        match opt_get($opts, $key)? {
            Some(v) => ($parse)(&v, $kind, $key, $ctx)?,
            None => return Err(schema_err(format!("{}: {} is required", $kind, $key))),
        }
    };
    (opt, $opts:expr, $kind:expr, $key:expr, $ctx:expr, $parse:expr) => {
        match opt_get($opts, $key)? {
            Some(v) => Some(($parse)(&v, $kind, $key, $ctx)?),
            None => None,
        }
    };
}

/// The whole boundary, as a table: which kind names reach which arm, what
/// options that arm takes, and what it builds. Each option is spelled once.
///
/// The leading identifier names the kind string inside the arms -- macro
/// hygiene means a binding the macro introduces is invisible to the
/// constructor expressions, which come from here.
macro_rules! kinds {
    ($kind:ident: $(
        [$($k:literal),+ $(,)?] { $($opt:ident : $mode:tt $parse:expr),* $(,)? } => $build:expr
    );+ $(;)?) => {
        fn parse_type($kind: &str, opts: &Bound<'_, PyDict>, ctx: &Ctx<'_>) -> PyResult<TypeIn> {
            if ctx.depth > MAX_SCHEMA_DEPTH {
                return Err(schema_err(format!(
                    "schema nests deeper than {MAX_SCHEMA_DEPTH} levels"
                )));
            }
            match $kind {
                $($($k)|+ => {
                    let allowed: &[&str] = &[$(opt_key(stringify!($opt))),*];
                    check_keys(opts, allowed, $kind)?;
                    $(
                        let $opt = bind!(
                            $mode, opts, $kind, opt_key(stringify!($opt)), ctx, $parse
                        );
                    )*
                    Ok($build)
                })+
                other => Err(schema_err(format!("unknown kind '{other}'"))),
            }
        }

        /// Every arm: the kinds that reach it, and the options it reads --
        /// the same tokens. Published by `vocabulary()` so Python can check
        /// its own option-emitting helpers against what the core accepts.
        const OPTIONS: &[(&[&str], &[&str])] = &[$((
            &[$($k),+] as &[&str],
            &[$(stringify!($opt)),*] as &[&str],
        )),+];
    };
}

kinds! {
    kind:

    ["u8", "i8", "u16", "i16", "u32", "i32", "u64", "i64"] {
        byteorder: opt p_byteorder,
        r#const:   opt p_i128,
    } => TypeIn::Int { prim: INT_PRIMS.parse(kind).map_err(schema_err)?, byteorder, const_: r#const };

    ["f32", "f64"] {
        byteorder: opt p_byteorder,
    } => TypeIn::Float { is64: kind == "f64", byteorder };

    ["bool"] {
        r#const: opt p_bool,
    } => TypeIn::Bool { const_: r#const };

    ["raw"] {
        len:     opt p_usize,
        r#const: opt p_bytes,
    } => TypeIn::Raw { len, const_: r#const };

    ["bytes"] {
        len: req p_expr,
        max: opt p_usize,
    } => TypeIn::Bytes { len, max };

    ["str"] {
        len:      req p_expr,
        max:      opt p_usize,
        encoding: opt p_string,
        errors:   opt p_string,
    } => TypeIn::StrT {
        len,
        max,
        encoding: encoding.unwrap_or_else(|| "utf-8".to_string()),
        errors: errors.unwrap_or_else(|| "strict".to_string()),
    };

    ["cstr"] {
        max:      opt p_usize,
        encoding: opt p_string,
        errors:   opt p_string,
    } => TypeIn::CStrT {
        max,
        encoding: encoding.unwrap_or_else(|| "utf-8".to_string()),
        errors: errors.unwrap_or_else(|| "strict".to_string()),
    };

    ["bits"] {
        width:  req p_width,
        signed: opt p_bool,
    } => TypeIn::Bits {
        width,
        signed: signed.unwrap_or(false),
    };

    ["flags"] {
        base:      req p_prim,
        names:     req p_names,
        rest:      opt p_string,
        byteorder: opt p_byteorder,
    } => TypeIn::FlagsT {
        base,
        byteorder,
        names,
        rest: rest.unwrap_or_else(|| "keep".to_string()),
    };

    ["digest"] {
        algo:   req p_string,
        over:   req p_over,
        verify: opt p_bool,
        poly:   opt p_u64,
        init:   opt p_u64,
        xorout: opt p_u64,
        refin:  opt p_bool,
        refout: opt p_bool,
    } => TypeIn::DigestT {
        algo,
        overrides: CrcOverrides { poly, init, xorout, refin, refout },
        over,
        verify: verify.unwrap_or(true),
    };

    ["struct"] {
        fields:    req p_fields,
        byteorder: opt p_byteorder,
        size:      opt p_expr,
    } => TypeIn::StructT { fields, byteorder, size };

    ["array"] {
        elem:      req p_elem,
        count:     opt p_expr,
        until_eof: opt p_bool,
    } => TypeIn::ArrayT {
        elem: Box::new(elem),
        count: Some(extent(count, until_eof.unwrap_or(false))?),
        until_eof: false,
    };

    ["switch"] {
        on:      req p_expr,
        cases:   req p_cases,
        default: opt p_default,
    } => TypeIn::SwitchT { on, cases, default: default.map(Box::new) };

    ["cond"] {
        pred: req p_expr,
        then: req p_then,
    } => TypeIn::CondT { pred, then: Box::new(then) };
}

// ---------- the tuple forms around it ----------

/// Borrowed from the caller's tuple: the name is matched, never kept.
fn kind_str<'a>(obj: &'a Bound<'_, PyAny>) -> PyResult<&'a str> {
    let bad = || {
        let shown = obj.repr().map(|r| r.to_string()).unwrap_or_default();
        schema_err(format!("unknown kind {shown}"))
    };
    obj.cast::<PyString>().map_err(|_| bad())?.to_str()
}

fn opts_dict<'py>(obj: &Bound<'py, PyAny>, kind: &str) -> PyResult<Bound<'py, PyDict>> {
    obj.cast::<PyDict>()
        .cloned()
        .map_err(|_| schema_err(format!("{kind}: opts must be a dict")))
}

/// `(kind, opts)` -- a type in the elem/case/default/then position.
fn parse_type_spec(obj: &Bound<'_, PyAny>, ctx: &Ctx<'_>) -> PyResult<TypeIn> {
    let shape = || schema_err("a type spec must be a (kind, opts) tuple");
    let t = obj.cast::<PyTuple>().map_err(|_| shape())?;
    if t.len() != 2 {
        return Err(shape());
    }
    let kind_obj = t.get_item(0)?;
    let kind = kind_str(&kind_obj)?;
    let opts = opts_dict(&t.get_item(1)?, kind)?;
    parse_type(kind, &opts, ctx)
}

/// `(name, kind, opts)` -- one field, located on failure.
fn parse_field(item: &Bound<'_, PyAny>, ctx: &Ctx<'_>) -> PyResult<FieldIn> {
    let shape = || schema_err("a field must be a (name, kind, opts) tuple");
    let t = item.cast::<PyTuple>().map_err(|_| shape())?;
    if t.len() != 3 {
        return Err(shape());
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
    let here = ctx.field(name.as_deref().unwrap_or("<unnamed>"));
    let ty = (|| {
        let kind_obj = t.get_item(1)?;
        let kind = kind_str(&kind_obj)?;
        let opts = opts_dict(&t.get_item(2)?, kind)?;
        parse_type(kind, &opts, &here)
    })()
    .map_err(|e| locate(item.py(), e, &here.path()))?;
    Ok(FieldIn { name, ty })
}

/// Deliberately `try_iter` rather than a `Vec<T>` parameter: pyo3's `Vec<T>`
/// extraction goes through the sequence protocol, which would quietly stop
/// accepting the generators, `map()` results and `dict.values()` views that
/// work today.
pub fn parse_fields(obj: &Bound<'_, PyAny>, ctx: &Ctx<'_>) -> PyResult<Vec<FieldIn>> {
    let mut out = Vec::new();
    for item in obj
        .try_iter()
        .map_err(|_| schema_err("fields must be an iterable of (name, kind, opts) tuples"))?
    {
        out.push(parse_field(&item?, ctx)?);
    }
    Ok(out)
}

/// Every closed set the Rust side owns, keyed by what it names.
///
/// Without this the drift check only runs one way: `tests/test_vocabulary.py`
/// can prove that every name Python knows is accepted, but a name that
/// exists only in Rust stays invisible to Python and simply goes unused.
#[pyfunction]
pub fn vocabulary(py: Python<'_>) -> PyResult<crate::Vocabulary> {
    use rustruct_core::digest::ALGOS;
    use rustruct_core::error::Kind as ErrKind;
    use rustruct_core::program::{ENCODINGS, REST_POLICIES};

    let d = PyDict::new(py);
    d.set_item("byteorders", BYTEORDERS.names().collect::<Vec<_>>())?;
    d.set_item("int_prims", INT_PRIMS.names().collect::<Vec<_>>())?;
    d.set_item("binops", BINOPS.names().collect::<Vec<_>>())?;
    d.set_item("error_kinds", ErrKind::ALL)?;
    d.set_item("encodings", ENCODINGS.names().collect::<Vec<_>>())?;
    d.set_item("algos", ALGOS.names().collect::<Vec<_>>())?;
    d.set_item("rest_policies", REST_POLICIES.names().collect::<Vec<_>>())?;

    // Spellings a set takes without publishing as a name of its own -- for
    // most sets the same list, for encodings the aliases too. Published so
    // the alias coverage in `tests/test_vocabulary.py` is read off the core
    // rather than retyped.
    let accepted = PyDict::new(py);
    accepted.set_item("byteorders", BYTEORDERS.accepted().collect::<Vec<_>>())?;
    accepted.set_item("int_prims", INT_PRIMS.accepted().collect::<Vec<_>>())?;
    accepted.set_item("binops", BINOPS.accepted().collect::<Vec<_>>())?;
    accepted.set_item("encodings", ENCODINGS.accepted().collect::<Vec<_>>())?;
    accepted.set_item("algos", ALGOS.accepted().collect::<Vec<_>>())?;
    accepted.set_item(
        "rest_policies",
        REST_POLICIES.accepted().collect::<Vec<_>>(),
    )?;
    d.set_item("accepted", accepted)?;

    let options = PyDict::new(py);
    for (kinds, opts) in OPTIONS {
        let names: Vec<&str> = opts.iter().copied().map(opt_key).collect();
        for kind in *kinds {
            options.set_item(kind, &names)?;
        }
    }
    d.set_item("options", options)?;
    Ok(crate::Vocabulary(d.into_any().unbind()))
}
