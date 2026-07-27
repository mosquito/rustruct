use std::sync::Arc;

use crate::digest::{Algo, DigestVal, Hasher};
use crate::error::{format_path, Kind, Seg, UFail, URes};
use crate::expr::Expr;
use crate::model::{Builder, ValueBuilder};
use crate::program::{
    BitItem, Common, CountSrc, Enc, FixKind, FixedItem, FlagItem, IntPrim, Key, LenSrc, Op, Over,
    Program, Reg, RestPolicy, MAX_DEPTH, MAX_REGS, MAX_SPANS,
};
use crate::value::Value;

/// Top-level unpack outcome (RawResult). Generic over what a
/// successful decode actually produces (`V`); defaults to `Value` so
/// existing code matching on `Outcome::Ok { value, .. }` without spelling
/// out `Outcome<Value>` keeps compiling unchanged.
#[derive(Debug)]
pub enum Outcome<V = Value> {
    Ok {
        value: V,
        pos: usize,
    },
    Incomplete {
        needed: usize,
    },
    Invalid {
        kind: Kind,
        offset: usize,
        path: String,
    },
}

/// `exact` — require the buffer to be fully consumed (unpack);
/// `stream` — a data shortage yields Incomplete (parse), otherwise Invalid truncated.
///
/// Convenience wrapper over `run_with` for the reference `Value` tree --
/// see the module-level `Builder` trait for building something else
/// directly (e.g. rustruct-py builds PyObjects, skipping this tree
/// entirely).
pub fn run(prog: &Program, buf: &[u8], offset: usize, exact: bool, stream: bool) -> Outcome<Value> {
    run_with(prog, &mut ValueBuilder, buf, offset, exact, stream)
}

pub fn run_with<B: Builder>(
    prog: &Program,
    builder: &mut B,
    buf: &[u8],
    offset: usize,
    exact: bool,
    stream: bool,
) -> Outcome<B::Val> {
    let mut ctx = Ctx {
        buf,
        frames: Vec::new(),
        builder,
    };
    let offset = offset.min(buf.len());
    match run_struct(&mut ctx, prog, offset, buf.len()) {
        Ok((value, pos)) => {
            if exact && pos != buf.len() {
                Outcome::Invalid {
                    kind: Kind::Trailing,
                    offset: pos,
                    path: String::new(),
                }
            } else {
                Outcome::Ok { value, pos }
            }
        }
        Err(UFail::Incomplete { needed }) => {
            if stream {
                Outcome::Incomplete { needed }
            } else {
                Outcome::Invalid {
                    kind: Kind::Truncated,
                    offset: buf.len(),
                    path: String::new(),
                }
            }
        }
        Err(UFail::Invalid(inv)) => Outcome::Invalid {
            kind: inv.kind,
            offset: inv.offset,
            path: format_path(&inv.path),
        },
    }
}

struct Frame {
    regs: [i64; MAX_REGS],
    spans: [(usize, usize); MAX_SPANS],
}

struct Ctx<'a, B: Builder> {
    buf: &'a [u8],
    frames: Vec<Frame>,
    builder: &'a mut B,
}

impl<B: Builder> Ctx<'_, B> {
    fn get_reg(&self, r: Reg) -> i64 {
        let i = self.frames.len() - 1 - r.up as usize;
        self.frames[i].regs[r.idx as usize]
    }

    fn set_reg(&mut self, idx: u8, v: i64) {
        self.frames.last_mut().expect("frame").regs[idx as usize] = v;
    }

    fn set_span(&mut self, idx: Option<u8>, start: usize, end: usize) {
        if let Some(idx) = idx {
            self.frames.last_mut().expect("frame").spans[idx as usize] = (start, end);
        }
    }

    /// Bounds check: hitting the end of the buffer → Incomplete,
    /// hitting a window boundary → Invalid.
    fn need(&self, pos: usize, limit: usize, n: usize) -> URes<()> {
        if pos.saturating_add(n) <= limit {
            return Ok(());
        }
        if limit == self.buf.len() {
            Err(UFail::Incomplete {
                needed: pos + n - self.buf.len(),
            })
        } else {
            Err(UFail::invalid(Kind::Truncated, pos))
        }
    }

    fn eval(&self, expr: &Expr, pos: usize) -> URes<i64> {
        expr.eval(&mut |r| Ok(self.get_reg(r)))
            .map_err(|k| UFail::invalid(k, pos))
    }

    fn eval_len(&self, expr: &Expr, pos: usize) -> URes<usize> {
        let v = self.eval(expr, pos)?;
        if v < 0 {
            return Err(UFail::invalid(Kind::NegativeLen, pos));
        }
        Ok(v as usize)
    }
}

/// Deferred digest verification.
struct Deferred {
    algo: Algo,
    over: Over,
    verify: bool,
    read: DigestVal,
    /// Bytes of the digest field itself — used for self-zeroing when over="*".
    field: (usize, usize),
    key: Arc<str>,
}

impl Deferred {
    fn compute(
        &self,
        buf: &[u8],
        scope_start: usize,
        scope_end: usize,
        spans: &[(usize, usize); MAX_SPANS],
    ) -> DigestVal {
        let mut h = Hasher::new(&self.algo);
        match &self.over {
            Over::Star => {
                h.update(&buf[scope_start..self.field.0]);
                h.update_zeros(self.field.1 - self.field.0);
                h.update(&buf[self.field.1..scope_end]);
            }
            Over::Spans(idxs) => {
                for &i in idxs {
                    let (s, e) = spans[i as usize];
                    h.update(&buf[s..e]);
                }
            }
        }
        h.finalize()
    }
}

fn run_struct<B: Builder>(
    ctx: &mut Ctx<B>,
    prog: &Program,
    pos: usize,
    limit: usize,
) -> URes<(B::Val, usize)> {
    if ctx.frames.len() >= MAX_DEPTH {
        return Err(UFail::invalid(Kind::Depth, pos));
    }
    ctx.frames.push(Frame {
        regs: [0; MAX_REGS],
        spans: [(0, 0); MAX_SPANS],
    });
    let r = run_struct_inner(ctx, prog, pos, limit);
    ctx.frames.pop();
    r
}

fn run_struct_inner<B: Builder>(
    ctx: &mut Ctx<B>,
    prog: &Program,
    mut pos: usize,
    limit: usize,
) -> URes<(B::Val, usize)> {
    let scope_start = pos;
    let mut map = ctx.builder.map_new(prog.ops.len());
    let mut deferred: Vec<Deferred> = Vec::new();
    for op in &prog.ops {
        pos = run_op(ctx, op, pos, limit, &mut map, &mut deferred)?;
    }
    // Digests run on scope exit, in declaration order.
    let spans = ctx.frames.last().expect("frame").spans;
    for d in &deferred {
        if !d.verify {
            continue;
        }
        let computed = d.compute(ctx.buf, scope_start, pos, &spans);
        if computed != d.read {
            return Err(UFail::invalid(Kind::Checksum, d.field.0).seg(Seg::Field(d.key.clone())));
        }
    }
    Ok((ctx.builder.map_finish(map), pos))
}

fn run_op<B: Builder>(
    ctx: &mut Ctx<B>,
    op: &Op,
    pos: usize,
    limit: usize,
    map: &mut B::Map,
    deferred: &mut Vec<Deferred>,
) -> URes<usize> {
    match op {
        Op::Fixed { width, items } => {
            ctx.need(pos, limit, *width)?;
            for item in items {
                let v = read_fixed_item(ctx, item, pos).map_err(|e| push_key(e, &item.key))?;
                if let Some(name) = item.key.name() {
                    ctx.builder.map_set(map, name, v);
                }
            }
            Ok(pos + width)
        }
        Op::BitRun { nbytes, items } => {
            ctx.need(pos, limit, *nbytes)?;
            let mut bitpos = 0u32;
            for item in items {
                let v = read_bits(ctx.buf, pos, bitpos, item);
                bitpos += u32::from(item.width);
                if let Some(reg) = item.reg {
                    let as64 = i64::try_from(v)
                        .map_err(|_| push_key(UFail::invalid(Kind::Overflow, pos), &item.key))?;
                    ctx.set_reg(reg, as64);
                }
                if let Some(name) = item.key.name() {
                    let bv = ctx.builder.int(v);
                    ctx.builder.map_set(map, name, bv);
                }
            }
            Ok(pos + nbytes)
        }
        Op::Digest {
            algo,
            over,
            verify,
            be,
            c,
        } => {
            let w = algo.width_bytes();
            ctx.need(pos, limit, w).map_err(|e| push_key(e, &c.key))?;
            let raw = &ctx.buf[pos..pos + w];
            let (value, read) = if algo.is_int() {
                let u = read_uint(raw, *be);
                (ctx.builder.int(u as i128), DigestVal::Int(u))
            } else {
                (
                    ctx.builder.bytes(raw.to_vec()),
                    DigestVal::Bytes(raw.to_vec()),
                )
            };
            let key = c.key.name().expect("digest is always named").clone();
            deferred.push(Deferred {
                algo: algo.clone(),
                over: over.clone(),
                verify: *verify,
                read,
                field: (pos, pos + w),
                key,
            });
            ctx.set_span(c.span, pos, pos + w);
            insert(ctx, map, c, value);
            Ok(pos + w)
        }
        Op::Cond { pred, then, c } => {
            // Absent (pred == 0): no bytes consumed, no key in the
            // decoded map at all -- the caller's compiled from_mapping
            // sees exactly the same shape as "this field was never
            // supplied", which it already handles for a normal optional
            // field.
            if ctx.eval(pred, pos)? != 0 {
                let (v, end) = op_value(ctx, then, pos, limit).map_err(|e| push_key(e, &c.key))?;
                ctx.set_span(c.span, pos, end);
                insert(ctx, map, c, v);
                Ok(end)
            } else {
                Ok(pos)
            }
        }
        _ => {
            let c = op.common().expect("all remaining opcodes have Common");
            let (v, end) = op_value(ctx, op, pos, limit).map_err(|e| push_key(e, &c.key))?;
            ctx.set_span(c.span, pos, end);
            insert(ctx, map, c, v);
            Ok(end)
        }
    }
}

/// The value of a single opcode — shared between struct fields and the
/// position of an array element / switch branch.
fn op_value<B: Builder>(
    ctx: &mut Ctx<B>,
    op: &Op,
    pos: usize,
    limit: usize,
) -> URes<(B::Val, usize)> {
    match op {
        Op::Fixed { width, items } => {
            // Element position only: exactly one item (compiler invariant).
            ctx.need(pos, limit, *width)?;
            debug_assert_eq!(items.len(), 1);
            let v = read_fixed_item(ctx, &items[0], pos)?;
            Ok((v, pos + width))
        }
        Op::Bytes { len, max, .. } => {
            let n = resolve_len(ctx, len, *max, pos, limit)?;
            ctx.need(pos, limit, n)?;
            let v = ctx.builder.bytes(ctx.buf[pos..pos + n].to_vec());
            Ok((v, pos + n))
        }
        Op::Str { len, max, enc, .. } => {
            let n = resolve_len(ctx, len, *max, pos, limit)?;
            ctx.need(pos, limit, n)?;
            let s = decode(&ctx.buf[pos..pos + n], *enc)
                .map_err(|_| UFail::invalid(Kind::Decode, pos))?;
            let v = ctx.builder.string(s);
            Ok((v, pos + n))
        }
        Op::CStr { max, enc, .. } => do_cstr(ctx, *max, *enc, pos, limit),
        Op::Flags {
            prim,
            be,
            items,
            rest,
            rest_mask,
            ..
        } => do_flags(ctx, *prim, *be, items, *rest, *rest_mask, pos, limit),
        Op::Nest { prog, size, .. } => do_nest(ctx, prog, size, pos, limit),
        Op::Array {
            count,
            max_count,
            elem,
            elem_min,
            ..
        } => do_array(ctx, count, *max_count, elem, *elem_min, pos, limit),
        Op::Switch {
            on, cases, default, ..
        } => do_switch(ctx, on, cases, default.as_deref(), pos, limit),
        Op::BitRun { .. } | Op::Digest { .. } | Op::Cond { .. } => {
            unreachable!("bits/digest/cond in a value position are forbidden by the compiler")
        }
    }
}

fn insert<B: Builder>(ctx: &mut Ctx<B>, map: &mut B::Map, c: &Common, v: B::Val) {
    if let Some(name) = c.key.name() {
        ctx.builder.map_set(map, name, v);
    }
}

fn push_key(e: UFail, key: &Key) -> UFail {
    match key.name() {
        Some(n) => e.seg(Seg::Field(n.clone())),
        None => e,
    }
}

fn read_fixed_item<B: Builder>(ctx: &mut Ctx<B>, item: &FixedItem, base: usize) -> URes<B::Val> {
    let pos = base + item.off;
    let buf = ctx.buf;
    match &item.kind {
        FixKind::Int {
            prim, be, expected, ..
        } => {
            let w = prim.width();
            let raw = &buf[pos..pos + w];
            let v: i128 = if prim.signed() {
                read_sint(raw, *be)
            } else {
                read_uint(raw, *be) as i128
            };
            if let Some(exp) = expected {
                if v != *exp {
                    return Err(UFail::invalid(Kind::Const, pos));
                }
            }
            if let Some(reg) = item.reg {
                let as64 = i64::try_from(v).map_err(|_| UFail::invalid(Kind::Overflow, pos))?;
                ctx.set_reg(reg, as64);
            }
            ctx.set_span(item.span, pos, pos + w);
            Ok(ctx.builder.int(v))
        }
        FixKind::F32 { be } => {
            let b: [u8; 4] = buf[pos..pos + 4].try_into().unwrap();
            let v = if *be {
                f32::from_be_bytes(b)
            } else {
                f32::from_le_bytes(b)
            };
            ctx.set_span(item.span, pos, pos + 4);
            Ok(ctx.builder.float(f64::from(v)))
        }
        FixKind::F64 { be } => {
            let b: [u8; 8] = buf[pos..pos + 8].try_into().unwrap();
            let v = if *be {
                f64::from_be_bytes(b)
            } else {
                f64::from_le_bytes(b)
            };
            ctx.set_span(item.span, pos, pos + 8);
            Ok(ctx.builder.float(v))
        }
        FixKind::Bool { expected } => {
            let v = buf[pos] != 0;
            if let Some(exp) = expected {
                if v != *exp {
                    return Err(UFail::invalid(Kind::Const, pos));
                }
            }
            ctx.set_span(item.span, pos, pos + 1);
            Ok(ctx.builder.bool_(v))
        }
        FixKind::Raw { len, expected } => {
            let raw = &buf[pos..pos + len];
            if let Some(exp) = expected {
                if raw != &exp[..] {
                    return Err(UFail::invalid(Kind::Const, pos));
                }
            }
            ctx.set_span(item.span, pos, pos + len);
            Ok(ctx.builder.bytes(raw.to_vec()))
        }
    }
}

/// MSB-first within a byte and across byte boundaries.
fn read_bits(buf: &[u8], base: usize, bitpos: u32, item: &BitItem) -> i128 {
    let mut v: u64 = 0;
    for i in 0..u32::from(item.width) {
        let bit_index = bitpos + i;
        let byte = buf[base + (bit_index / 8) as usize];
        let bit = (byte >> (7 - bit_index % 8)) & 1;
        v = (v << 1) | u64::from(bit);
    }
    if item.signed {
        if item.width < 64 {
            let sign = 1u64 << (item.width - 1);
            if v & sign != 0 {
                return (v as i128) - (1i128 << item.width);
            }
        } else {
            return v as i64 as i128;
        }
    }
    v as i128
}

#[allow(clippy::too_many_arguments)]
fn do_flags<B: Builder>(
    ctx: &mut Ctx<B>,
    prim: IntPrim,
    be: bool,
    items: &[FlagItem],
    rest: RestPolicy,
    rest_mask: u64,
    pos: usize,
    limit: usize,
) -> URes<(B::Val, usize)> {
    let w = prim.width();
    ctx.need(pos, limit, w)?;
    let raw = read_uint(&ctx.buf[pos..pos + w], be);
    let mut fmap = ctx.builder.map_new(items.len() + 1);
    for item in items {
        let extracted = (raw & item.mask) >> item.shift;
        let v = if item.is_bool {
            ctx.builder.bool_(extracted != 0)
        } else {
            ctx.builder.int(extracted as i128)
        };
        ctx.builder.map_set(&mut fmap, &item.key, v);
    }
    let leftover = raw & rest_mask;
    match rest {
        RestPolicy::Keep => {
            let rest_key: Arc<str> = Arc::from("_rest");
            let v = ctx.builder.int(leftover as i128);
            ctx.builder.map_set(&mut fmap, &rest_key, v);
        }
        RestPolicy::Strict => {
            if leftover != 0 {
                return Err(UFail::invalid(Kind::ReservedBits, pos));
            }
        }
        RestPolicy::Ignore => {}
    }
    Ok((ctx.builder.map_finish(fmap), pos + w))
}

fn do_cstr<B: Builder>(
    ctx: &mut Ctx<B>,
    max: usize,
    enc: Enc,
    pos: usize,
    limit: usize,
) -> URes<(B::Val, usize)> {
    let window = limit - pos;
    let scan = window.min(max.saturating_add(1));
    match ctx.buf[pos..pos + scan].iter().position(|&b| b == 0) {
        Some(i) => {
            let s = decode(&ctx.buf[pos..pos + i], enc)
                .map_err(|_| UFail::invalid(Kind::Decode, pos))?;
            Ok((ctx.builder.string(s), pos + i + 1))
        }
        None => {
            if window > max {
                Err(UFail::invalid(Kind::Limit, pos))
            } else if limit == ctx.buf.len() {
                Err(UFail::Incomplete { needed: 1 })
            } else {
                Err(UFail::invalid(Kind::Unterminated, pos))
            }
        }
    }
}

fn do_nest<B: Builder>(
    ctx: &mut Ctx<B>,
    prog: &Program,
    size: &Option<LenSrc>,
    pos: usize,
    limit: usize,
) -> URes<(B::Val, usize)> {
    match size {
        None => run_struct(ctx, prog, pos, limit),
        Some(LenSrc::Greedy) => unreachable!("forbidden by the compiler"),
        Some(LenSrc::Expr { expr, .. }) => {
            let sz = ctx.eval_len(expr, pos)?;
            // Window: bounds check, then narrow limit.
            ctx.need(pos, limit, sz)?;
            let wlimit = pos + sz;
            let (v, end) = run_struct(ctx, prog, pos, wlimit)?;
            if end != wlimit {
                return Err(UFail::invalid(Kind::Trailing, end));
            }
            Ok((v, wlimit))
        }
    }
}

fn do_array<B: Builder>(
    ctx: &mut Ctx<B>,
    count: &CountSrc,
    max_count: usize,
    elem: &Op,
    elem_min: usize,
    pos: usize,
    limit: usize,
) -> URes<(B::Val, usize)> {
    let mut pos = pos;
    match count {
        CountSrc::Expr { expr, .. } => {
            let n = ctx.eval(expr, pos)?;
            if n < 0 {
                return Err(UFail::invalid(Kind::NegativeLen, pos));
            }
            let n = n as usize;
            if n > max_count {
                return Err(UFail::invalid(Kind::Limit, pos));
            }
            // Preallocate no more than what could physically fit.
            let fit = (limit - pos) / elem_min.max(1) + 1;
            let mut out = ctx.builder.list_new(n.min(fit));
            for i in 0..n {
                let (v, np) = op_value(ctx, elem, pos, limit).map_err(|e| e.seg(Seg::Index(i)))?;
                ctx.builder.list_push(&mut out, v);
                pos = np;
            }
            Ok((ctx.builder.list_finish(out), pos))
        }
        CountSrc::UntilEof => {
            let mut out = ctx.builder.list_new(0);
            let mut i = 0usize;
            while pos < limit {
                if i >= max_count {
                    return Err(UFail::invalid(Kind::Limit, pos));
                }
                let (v, np) = op_value(ctx, elem, pos, limit).map_err(|e| e.seg(Seg::Index(i)))?;
                ctx.builder.list_push(&mut out, v);
                pos = np;
                i += 1;
            }
            Ok((ctx.builder.list_finish(out), pos))
        }
    }
}

fn do_switch<B: Builder>(
    ctx: &mut Ctx<B>,
    on: &Expr,
    cases: &[(i64, Box<Op>)],
    default: Option<&Op>,
    pos: usize,
    limit: usize,
) -> URes<(B::Val, usize)> {
    let tag = ctx.eval(on, pos)?;
    let branch = cases
        .iter()
        .find(|(t, _)| *t == tag)
        .map(|(_, op)| op.as_ref())
        .or(default);
    match branch {
        Some(op) => op_value(ctx, op, pos, limit),
        None => Err(UFail::invalid(Kind::NoCase, pos)),
    }
}

fn resolve_len<B: Builder>(
    ctx: &Ctx<B>,
    len: &LenSrc,
    max: usize,
    pos: usize,
    limit: usize,
) -> URes<usize> {
    let n = match len {
        LenSrc::Greedy => limit - pos,
        LenSrc::Expr { expr, .. } => ctx.eval_len(expr, pos)?,
    };
    if n > max {
        return Err(UFail::invalid(Kind::Limit, pos));
    }
    Ok(n)
}

pub(crate) fn read_uint(raw: &[u8], be: bool) -> u64 {
    let mut v = 0u64;
    if be {
        for &b in raw {
            v = (v << 8) | u64::from(b);
        }
    } else {
        for &b in raw.iter().rev() {
            v = (v << 8) | u64::from(b);
        }
    }
    v
}

fn read_sint(raw: &[u8], be: bool) -> i128 {
    let u = read_uint(raw, be);
    let bits = raw.len() * 8;
    if bits < 64 {
        let sign = 1u64 << (bits - 1);
        if u & sign != 0 {
            return (u as i128) - (1i128 << bits);
        }
    } else if u > i64::MAX as u64 {
        return u as i64 as i128;
    }
    u as i128
}

fn decode(raw: &[u8], enc: Enc) -> Result<String, ()> {
    match enc {
        Enc::Utf8 => String::from_utf8(raw.to_vec()).map_err(|_| ()),
        Enc::Ascii => {
            if raw.iter().all(|&b| b < 0x80) {
                Ok(raw.iter().map(|&b| b as char).collect())
            } else {
                Err(())
            }
        }
        Enc::Latin1 => Ok(raw.iter().map(|&b| b as char).collect()),
    }
}
