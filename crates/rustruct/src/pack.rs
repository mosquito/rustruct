use std::sync::Arc;

use crate::digest::{Algo, DigestVal, Hasher};
use crate::error::{format_path, Kind, PRes, PackFail, Seg};
use crate::expr::Expr;
use crate::model::{Source, ValueSource};
use crate::program::{
    BitItem, CountSrc, Enc, FixKind, FixedItem, FlagItem, IntPrim, Inv, Key, LenSrc, Op, Over,
    Program, Reg, RestPolicy, MAX_REGS, MAX_SPANS,
};
use crate::value::Value;

/// Top-level pack outcome.
#[derive(Debug)]
pub enum PackOutcome {
    Ok(Vec<u8>),
    Err { kind: Kind, path: String },
}

/// Convenience wrapper over `run_with` for the reference `Value` tree --
/// see the module-level `Source` trait for reading straight from
/// something else (e.g. rustruct-py reads straight from PyObjects,
/// skipping this tree entirely).
pub fn run(prog: &Program, values: &Value) -> PackOutcome {
    run_with(prog, &ValueSource::new(), &values)
}

pub fn run_with<S: Source>(prog: &Program, source: &S, values: &S::Val) -> PackOutcome {
    match pack_root(prog, source, values) {
        Ok(out) => PackOutcome::Ok(out),
        Err(e) => PackOutcome::Err {
            kind: e.kind,
            path: format_path(&e.path),
        },
    }
}

fn pack_root<S: Source>(prog: &Program, source: &S, values: &S::Val) -> PRes<Vec<u8>> {
    let mut ctx = PCtx {
        out: Vec::with_capacity(prog.min_size),
        frames: Vec::new(),
        source,
    };
    pack_struct(&mut ctx, prog, values)?;
    Ok(ctx.out)
}

/// The reserved slot of a derived field.
enum Slot {
    Int {
        pos: usize,
        prim: IntPrim,
        be: bool,
    },
    Bits {
        base: usize,
        bit_off: u32,
        width: u8,
        signed: bool,
    },
}

struct Resv {
    slot: Slot,
    value: Option<i64>,
}

struct PDigest {
    algo: Algo,
    over: Over,
    pos: usize,
    width: usize,
    be: bool,
    #[allow(dead_code)]
    key: Arc<str>,
}

struct PFrame {
    regs: [i64; MAX_REGS],
    known: u16,
    res: Vec<Option<Resv>>,
    spans: [(usize, usize); MAX_SPANS],
    digests: Vec<PDigest>,
}

impl PFrame {
    fn new() -> PFrame {
        PFrame {
            regs: [0; MAX_REGS],
            known: 0,
            res: (0..MAX_REGS).map(|_| None).collect(),
            spans: [(0, 0); MAX_SPANS],
            digests: Vec::new(),
        }
    }
}

struct PCtx<'a, S: Source> {
    out: Vec<u8>,
    frames: Vec<PFrame>,
    source: &'a S,
}

impl<S: Source> PCtx<'_, S> {
    fn set_reg(&mut self, idx: Option<u8>, v: i64) {
        if let Some(idx) = idx {
            let f = self.frames.last_mut().expect("frame");
            f.regs[idx as usize] = v;
            f.known |= 1 << idx;
        }
    }

    fn set_span(&mut self, idx: Option<u8>, start: usize, end: usize) {
        if let Some(idx) = idx {
            self.frames.last_mut().expect("frame").spans[idx as usize] = (start, end);
        }
    }

    fn get_reg(&self, r: Reg) -> PRes<i64> {
        let f = &self.frames[self.frames.len() - 1 - r.up as usize];
        if f.known & (1 << r.idx) == 0 {
            // The register value isn't known yet (its derived field hasn't been patched).
            return Err(PackFail::new(Kind::Missing));
        }
        Ok(f.regs[r.idx as usize])
    }

    fn eval(&self, expr: &Expr) -> PRes<i64> {
        let mut failed: Option<PackFail> = None;
        let r = expr.eval(&mut |reg| match self.get_reg(reg) {
            Ok(v) => Ok(v),
            Err(e) => {
                failed = Some(e);
                Err(Kind::Missing)
            }
        });
        match r {
            Ok(v) => Ok(v),
            Err(k) => Err(failed.unwrap_or_else(|| PackFail::new(k))),
        }
    }

    /// Inversion: the derived register gets x = (actual - b) / a.
    fn apply_inv(&mut self, inv: &Inv, actual: i64) -> PRes<()> {
        let d = actual
            .checked_sub(inv.b)
            .ok_or_else(|| PackFail::new(Kind::Range))?;
        if d % inv.a != 0 {
            return Err(PackFail::new(Kind::Indivisible));
        }
        let x = d / inv.a;
        let fi = self.frames.len() - 1 - inv.reg.up as usize;
        let idx = inv.reg.idx as usize;
        let Some(resv) = self.frames[fi].res[idx].as_mut() else {
            // A non-derived target (a const field): the values must match.
            if self.frames[fi].known & (1 << idx) != 0 && self.frames[fi].regs[idx] == x {
                return Ok(());
            }
            return Err(PackFail::new(Kind::Inconsistent));
        };
        if let Some(prev) = resv.value {
            // Multiple consumers must agree.
            if prev != x {
                return Err(PackFail::new(Kind::Inconsistent));
            }
            return Ok(());
        }
        resv.value = Some(x);
        let slot = match resv.slot {
            Slot::Int { pos, prim, be } => Slot::Int { pos, prim, be },
            Slot::Bits {
                base,
                bit_off,
                width,
                signed,
            } => Slot::Bits {
                base,
                bit_off,
                width,
                signed,
            },
        };
        match slot {
            Slot::Int { pos, prim, be } => {
                write_int_at(&mut self.out, pos, prim, be, x as i128)?;
            }
            Slot::Bits {
                base,
                bit_off,
                width,
                signed,
            } => {
                check_bits_range(x as i128, width, signed)?;
                let mask = if width >= 64 {
                    u64::MAX
                } else {
                    (1u64 << width) - 1
                };
                or_bits(&mut self.out, base, bit_off, width, (x as u64) & mask);
            }
        }
        self.frames[fi].regs[idx] = x;
        self.frames[fi].known |= 1 << idx;
        Ok(())
    }

    fn finish_len(&mut self, len: &LenSrc, actual: usize) -> PRes<()> {
        match len {
            LenSrc::Greedy => Ok(()),
            LenSrc::Expr { inv: Some(inv), .. } => {
                let actual = i64::try_from(actual).map_err(|_| PackFail::new(Kind::Range))?;
                self.apply_inv(inv, actual)
            }
            LenSrc::Expr { expr, inv: None } => {
                let n = expr
                    .as_const()
                    .expect("no ref means a constant after lower");
                if actual as i128 != n as i128 {
                    return Err(PackFail::new(Kind::Length));
                }
                Ok(())
            }
        }
    }
}

fn pack_struct<S: Source>(ctx: &mut PCtx<S>, prog: &Program, values: &S::Val) -> PRes<()> {
    let view = ctx
        .source
        .map_view(values)
        .ok_or_else(|| PackFail::new(Kind::Type))?;
    ctx.frames.push(PFrame::new());
    let scope_start = ctx.out.len();
    let r = pack_struct_inner(ctx, prog, &view);
    let frame = ctx.frames.pop().expect("frame");
    r?;
    // This scope's digests run after all length patches, innermost first.
    let scope_end = ctx.out.len();
    for d in &frame.digests {
        let mut h = Hasher::new(&d.algo);
        match &d.over {
            Over::Star => {
                // The zero reservation gives self-as-zeros for free.
                h.update(&ctx.out[scope_start..scope_end]);
            }
            Over::Spans(idxs) => {
                for &i in idxs {
                    let (s, e) = frame.spans[i as usize];
                    h.update(&ctx.out[s..e]);
                }
            }
        }
        match h.finalize() {
            DigestVal::Int(v) => {
                write_uint_at(&mut ctx.out, d.pos, d.width, d.be, v);
            }
            DigestVal::Bytes(b) => {
                ctx.out[d.pos..d.pos + d.width].copy_from_slice(&b);
            }
        }
    }
    Ok(())
}

fn pack_struct_inner<S: Source>(ctx: &mut PCtx<S>, prog: &Program, view: &S::MapView) -> PRes<()> {
    for op in &prog.ops {
        pack_op(ctx, op, view)?;
    }
    Ok(())
}

fn seg_key(e: PackFail, key: &Key) -> PackFail {
    match key.name() {
        Some(n) => e.seg(Seg::Field(n.clone())),
        None => e,
    }
}

fn pack_op<S: Source>(ctx: &mut PCtx<S>, op: &Op, view: &S::MapView) -> PRes<()> {
    match op {
        Op::Fixed { width, items } => {
            let base = ctx.out.len();
            ctx.out.resize(base + width, 0);
            for item in items {
                let v = lookup(ctx.source, view, &item.key);
                pack_fixed_item(ctx, base, item, v.as_ref()).map_err(|e| seg_key(e, &item.key))?;
            }
            Ok(())
        }
        Op::BitRun { nbytes, items } => {
            let base = ctx.out.len();
            ctx.out.resize(base + nbytes, 0);
            let mut bit_off = 0u32;
            for item in items {
                let v = lookup(ctx.source, view, &item.key);
                pack_bit_item(ctx, base, bit_off, item, v.as_ref())
                    .map_err(|e| seg_key(e, &item.key))?;
                bit_off += u32::from(item.width);
            }
            Ok(())
        }
        Op::Digest {
            algo, over, be, c, ..
        } => {
            // Input from the mapping is always ignored: reserve with zeros.
            let w = algo.width_bytes();
            let pos = ctx.out.len();
            ctx.out.resize(pos + w, 0);
            ctx.set_span(c.span, pos, pos + w);
            let key = c.key.name().expect("digest is always named").clone();
            ctx.frames.last_mut().expect("frame").digests.push(PDigest {
                algo: algo.clone(),
                over: over.clone(),
                pos,
                width: w,
                be: *be,
                key,
            });
            Ok(())
        }
        Op::Cond { pred, then, c } => {
            // Absent (pred == 0): no bytes written, no lookup at all --
            // the caller doesn't need to supply anything for this field.
            // Present: exactly the ordinary named-field flow, just deferred
            // behind the predicate check.
            if ctx.eval(pred)? != 0 {
                let name = c.key.name().expect("cond is always named");
                let v = ctx
                    .source
                    .view_get(view, name)
                    .ok_or_else(|| PackFail::new(Kind::Missing).seg(Seg::Field(name.clone())))?;
                let start = ctx.out.len();
                pack_value(ctx, then, &v).map_err(|e| seg_key(e, &c.key))?;
                ctx.set_span(c.span, start, ctx.out.len());
            }
            Ok(())
        }
        _ => {
            let c = op.common().expect("all remaining opcodes have Common");
            let name = c
                .key
                .name()
                .expect("unnamed dynamic fields are rejected by the compiler");
            let v = ctx
                .source
                .view_get(view, name)
                .ok_or_else(|| PackFail::new(Kind::Missing).seg(Seg::Field(name.clone())))?;
            let start = ctx.out.len();
            pack_value(ctx, op, &v).map_err(|e| seg_key(e, &c.key))?;
            ctx.set_span(c.span, start, ctx.out.len());
            Ok(())
        }
    }
}

/// Value -> bytes for a single opcode (shared between struct fields and the
/// position of an array element/branch).
fn pack_value<S: Source>(ctx: &mut PCtx<S>, op: &Op, v: &S::Val) -> PRes<()> {
    match op {
        Op::Fixed { width, items } => {
            debug_assert_eq!(items.len(), 1);
            let base = ctx.out.len();
            ctx.out.resize(base + width, 0);
            pack_fixed_item(ctx, base, &items[0], Some(v))
        }
        Op::Bytes { len, max, .. } => {
            let b = ctx
                .source
                .as_bytes(v)
                .ok_or_else(|| PackFail::new(Kind::Type))?;
            if b.len() > *max {
                return Err(PackFail::new(Kind::Limit));
            }
            ctx.out.extend_from_slice(&b);
            ctx.finish_len(len, b.len())
        }
        Op::Str { len, max, enc, .. } => {
            let s = ctx
                .source
                .as_str(v)
                .ok_or_else(|| PackFail::new(Kind::Type))?;
            let b = encode(&s, *enc).ok_or_else(|| PackFail::new(Kind::Encode))?;
            if b.len() > *max {
                return Err(PackFail::new(Kind::Limit));
            }
            // Encode happens before writing the length; the length
            // comes from the actual encoded byte count.
            ctx.out.extend_from_slice(&b);
            ctx.finish_len(len, b.len())
        }
        Op::CStr { max, enc, .. } => {
            let s = ctx
                .source
                .as_str(v)
                .ok_or_else(|| PackFail::new(Kind::Type))?;
            let b = encode(&s, *enc).ok_or_else(|| PackFail::new(Kind::Encode))?;
            if b.contains(&0) {
                return Err(PackFail::new(Kind::NulInCstr));
            }
            if b.len() > *max {
                return Err(PackFail::new(Kind::Limit));
            }
            ctx.out.extend_from_slice(&b);
            ctx.out.push(0);
            Ok(())
        }
        Op::Flags {
            prim,
            be,
            items,
            rest,
            rest_mask,
            ..
        } => pack_flags(ctx, *prim, *be, items, *rest, *rest_mask, v),
        Op::Nest { prog, size, .. } => {
            let start = ctx.out.len();
            pack_struct(ctx, prog, v)?;
            let sz = ctx.out.len() - start;
            match size {
                None => Ok(()),
                Some(len) => ctx.finish_len(len, sz),
            }
        }
        Op::Array { count, elem, .. } => {
            let list = ctx
                .source
                .as_list(v)
                .ok_or_else(|| PackFail::new(Kind::Type))?;
            for (i, item) in list.iter().enumerate() {
                pack_value(ctx, elem, item).map_err(|e| e.seg(Seg::Index(i)))?;
            }
            match count {
                CountSrc::UntilEof => Ok(()),
                CountSrc::Expr { expr, inv } => {
                    let len = LenSrc::Expr {
                        expr: expr.clone(),
                        inv: inv.clone(),
                    };
                    ctx.finish_len(&len, list.len())
                }
            }
        }
        Op::Switch {
            on, cases, default, ..
        } => {
            let tag = ctx.eval(on)?;
            let branch = cases
                .iter()
                .find(|(t, _)| *t == tag)
                .map(|(_, op)| op.as_ref())
                .or(default.as_deref());
            match branch {
                Some(b) => pack_value(ctx, b, v),
                None => Err(PackFail::new(Kind::NoCase)),
            }
        }
        Op::BitRun { .. } | Op::Digest { .. } | Op::Cond { .. } => {
            unreachable!("bits/digest/cond in a value position are forbidden by the compiler")
        }
    }
}

fn lookup<S: Source>(source: &S, view: &S::MapView, key: &Key) -> Option<S::Val> {
    key.name().and_then(|n| source.view_get(view, n))
}

fn pack_fixed_item<S: Source>(
    ctx: &mut PCtx<S>,
    base: usize,
    item: &FixedItem,
    v: Option<&S::Val>,
) -> PRes<()> {
    let pos = base + item.off;
    match &item.kind {
        FixKind::Int {
            prim,
            be,
            expected,
            derived,
        } => {
            let w = prim.width();
            if let Some(exp) = expected {
                // A degenerate derived value: written from the
                // schema, input is ignored.
                write_int_at(&mut ctx.out, pos, *prim, *be, *exp)?;
                ctx.set_reg(item.reg, *exp as i64);
            } else if *derived {
                let reg = item.reg.expect("derived implies ref implies a register");
                ctx.frames.last_mut().expect("frame").res[reg as usize] = Some(Resv {
                    slot: Slot::Int {
                        pos,
                        prim: *prim,
                        be: *be,
                    },
                    value: None,
                });
            } else {
                let val = match v {
                    Some(val) => ctx
                        .source
                        .as_int(val)
                        .ok_or_else(|| PackFail::new(Kind::Type))?,
                    // Unnamed field: writes 0.
                    None => {
                        if item.key.name().is_some() {
                            return Err(PackFail::new(Kind::Missing));
                        }
                        0
                    }
                };
                write_int_at(&mut ctx.out, pos, *prim, *be, val)?;
                ctx.set_reg(item.reg, val as i64);
            }
            ctx.set_span(item.span, pos, pos + w);
            Ok(())
        }
        FixKind::F32 { be } => {
            let f = float_of(ctx.source, v, &item.key)?;
            let b = (f as f32).to_be_bytes();
            let b = if *be {
                b
            } else {
                {
                    let mut r = b;
                    r.reverse();
                    r
                }
            };
            ctx.out[pos..pos + 4].copy_from_slice(&b);
            ctx.set_span(item.span, pos, pos + 4);
            Ok(())
        }
        FixKind::F64 { be } => {
            let f = float_of(ctx.source, v, &item.key)?;
            let b = f.to_be_bytes();
            let b = if *be {
                b
            } else {
                {
                    let mut r = b;
                    r.reverse();
                    r
                }
            };
            ctx.out[pos..pos + 8].copy_from_slice(&b);
            ctx.set_span(item.span, pos, pos + 8);
            Ok(())
        }
        FixKind::Bool { expected } => {
            let val = if let Some(exp) = expected {
                *exp
            } else {
                match v {
                    Some(x) => ctx
                        .source
                        .truthy(x)
                        .ok_or_else(|| PackFail::new(Kind::Type))?,
                    None => {
                        if item.key.name().is_some() {
                            return Err(PackFail::new(Kind::Missing));
                        }
                        false
                    }
                }
            };
            ctx.out[pos] = u8::from(val);
            ctx.set_span(item.span, pos, pos + 1);
            Ok(())
        }
        FixKind::Raw { len, expected } => {
            if let Some(exp) = expected {
                ctx.out[pos..pos + len].copy_from_slice(exp);
            } else {
                match v {
                    Some(val) => {
                        let b = ctx
                            .source
                            .as_bytes(val)
                            .ok_or_else(|| PackFail::new(Kind::Type))?;
                        if b.len() != *len {
                            return Err(PackFail::new(Kind::Length));
                        }
                        ctx.out[pos..pos + len].copy_from_slice(&b);
                    }
                    None => {
                        if item.key.name().is_some() {
                            return Err(PackFail::new(Kind::Missing));
                        }
                        // Unnamed raw: zeros — already there.
                    }
                }
            }
            ctx.set_span(item.span, pos, pos + len);
            Ok(())
        }
    }
}

fn pack_bit_item<S: Source>(
    ctx: &mut PCtx<S>,
    base: usize,
    bit_off: u32,
    item: &BitItem,
    v: Option<&S::Val>,
) -> PRes<()> {
    if item.derived {
        let reg = item.reg.expect("derived implies a register");
        ctx.frames.last_mut().expect("frame").res[reg as usize] = Some(Resv {
            slot: Slot::Bits {
                base,
                bit_off,
                width: item.width,
                signed: item.signed,
            },
            value: None,
        });
        return Ok(());
    }
    let val: i128 = match v {
        Some(val) => ctx
            .source
            .as_int(val)
            .ok_or_else(|| PackFail::new(Kind::Type))?,
        None => {
            if item.key.name().is_some() {
                return Err(PackFail::new(Kind::Missing));
            }
            0
        }
    };
    check_bits_range(val, item.width, item.signed)?;
    let mask = if item.width >= 64 {
        u64::MAX
    } else {
        (1u64 << item.width) - 1
    };
    or_bits(&mut ctx.out, base, bit_off, item.width, (val as u64) & mask);
    ctx.set_reg(item.reg, val as i64);
    Ok(())
}

fn pack_flags<S: Source>(
    ctx: &mut PCtx<S>,
    prim: IntPrim,
    be: bool,
    items: &[FlagItem],
    rest: RestPolicy,
    rest_mask: u64,
    v: &S::Val,
) -> PRes<()> {
    let view = ctx
        .source
        .map_view(v)
        .ok_or_else(|| PackFail::new(Kind::Type))?;
    // Closed key set: an unknown key is a PackError -- the one
    // place all supplied keys matter, not just the schema's known ones.
    for k in ctx.source.view_keys(&view) {
        let known = items.iter().any(|i| i.key == k)
            || (matches!(rest, RestPolicy::Keep) && &*k == "_rest");
        if !known {
            return Err(PackFail::new(Kind::UnknownFlag));
        }
    }
    let mut raw = 0u64;
    for item in items {
        let field = ctx.source.view_get(&view, &item.key);
        if item.is_bool {
            let b = match &field {
                None => false,
                Some(x) => ctx
                    .source
                    .truthy(x)
                    .ok_or_else(|| PackFail::new(Kind::Type))?,
            };
            if b {
                raw |= item.mask;
            }
        } else {
            let val = match &field {
                None => 0i128,
                Some(x) => ctx
                    .source
                    .as_int(x)
                    .ok_or_else(|| PackFail::new(Kind::Type))?,
            };
            let width = (item.mask >> item.shift).count_ones();
            if val < 0 || val > i128::from((1u64 << width) - 1) {
                return Err(PackFail::new(Kind::Range));
            }
            raw |= (val as u64) << item.shift;
        }
    }
    if matches!(rest, RestPolicy::Keep) {
        let leftover = match ctx.source.view_get(&view, "_rest") {
            None => 0i128, // a missing key means 0
            Some(x) => ctx
                .source
                .as_int(&x)
                .ok_or_else(|| PackFail::new(Kind::Type))?,
        };
        if leftover < 0
            || leftover as u128 > u128::from(u64::MAX)
            || (leftover as u64) & !rest_mask != 0
        {
            return Err(PackFail::new(Kind::Range));
        }
        raw |= (leftover as u64) & rest_mask;
    }
    let w = prim.width();
    let pos = ctx.out.len();
    ctx.out.resize(pos + w, 0);
    write_uint_at(&mut ctx.out, pos, w, be, raw);
    Ok(())
}

fn float_of<S: Source>(source: &S, v: Option<&S::Val>, key: &Key) -> PRes<f64> {
    match v {
        Some(val) => source
            .as_float(val)
            .ok_or_else(|| PackFail::new(Kind::Type)),
        None => {
            if key.name().is_some() {
                Err(PackFail::new(Kind::Missing))
            } else {
                Ok(0.0)
            }
        }
    }
}

fn encode(s: &str, enc: Enc) -> Option<Vec<u8>> {
    match enc {
        Enc::Utf8 => Some(s.as_bytes().to_vec()),
        Enc::Ascii => {
            if s.is_ascii() {
                Some(s.as_bytes().to_vec())
            } else {
                None
            }
        }
        Enc::Latin1 => {
            let mut out = Vec::with_capacity(s.len());
            for ch in s.chars() {
                let c = ch as u32;
                if c > 0xFF {
                    return None;
                }
                out.push(c as u8);
            }
            Some(out)
        }
    }
}

fn write_int_at(out: &mut [u8], pos: usize, prim: IntPrim, be: bool, v: i128) -> PRes<()> {
    if v < prim.min() || v > prim.max() {
        return Err(PackFail::new(Kind::Range));
    }
    let w = prim.width();
    let raw = (v as u64) & mask_bytes(w);
    write_uint_slice(&mut out[pos..pos + w], be, raw);
    Ok(())
}

fn write_uint_at(out: &mut [u8], pos: usize, width: usize, be: bool, v: u64) {
    write_uint_slice(&mut out[pos..pos + width], be, v);
}

fn write_uint_slice(raw: &mut [u8], be: bool, v: u64) {
    let w = raw.len();
    for (i, byte) in raw.iter_mut().enumerate() {
        let shift = if be { (w - 1 - i) * 8 } else { i * 8 };
        *byte = (v >> shift) as u8;
    }
}

fn mask_bytes(w: usize) -> u64 {
    if w >= 8 {
        u64::MAX
    } else {
        (1u64 << (w * 8)) - 1
    }
}

fn check_bits_range(v: i128, width: u8, signed: bool) -> PRes<()> {
    let ok = if signed {
        let half = 1i128 << (width - 1);
        v >= -half && v < half
    } else {
        v >= 0 && v < (1i128 << width)
    };
    if ok {
        Ok(())
    } else {
        Err(PackFail::new(Kind::Range))
    }
}

/// OR a value (width bits, already masked) into an MSB-first bit stream.
fn or_bits(out: &mut [u8], base: usize, bit_off: u32, width: u8, v: u64) {
    for i in 0..u32::from(width) {
        let bit = (v >> (u32::from(width) - 1 - i)) & 1;
        if bit != 0 {
            let idx = bit_off + i;
            out[base + (idx / 8) as usize] |= 1 << (7 - idx % 8);
        }
    }
}

/// pack_into: render into a temporary buffer, then copy at offset.
pub fn run_into(prog: &Program, values: &Value, dst: &mut [u8], offset: usize) -> PackOutcome {
    run_into_with(prog, &ValueSource::new(), &values, dst, offset)
}

pub fn run_into_with<S: Source>(
    prog: &Program,
    source: &S,
    values: &S::Val,
    dst: &mut [u8],
    offset: usize,
) -> PackOutcome {
    match pack_root(prog, source, values) {
        Ok(bytes) => {
            if offset > dst.len() || dst.len() - offset < bytes.len() {
                return PackOutcome::Err {
                    kind: Kind::Buffer,
                    path: String::new(),
                };
            }
            dst[offset..offset + bytes.len()].copy_from_slice(&bytes);
            PackOutcome::Ok(bytes)
        }
        Err(e) => PackOutcome::Err {
            kind: e.kind,
            path: format_path(&e.path),
        },
    }
}
