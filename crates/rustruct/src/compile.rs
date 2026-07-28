use std::sync::Arc;

use crate::digest::Algo;
use crate::error::{SRes, SchemaError};
use crate::expr::{Expr, Ins, EXPR_STACK};
use crate::program::{
    BitItem, Common, CountSrc, Enc, FixKind, FixedItem, FlagItem, Inv, Key, LenSrc, Op, Over,
    Program, Reg, RestPolicy, MAX_DEPTH, MAX_REGS, MAX_SPANS,
};
use crate::schema::{BinOp, ByteOrder, CrcOverrides, ExprIn, FieldIn, OverIn, TypeIn};

#[derive(Debug, Clone)]
pub struct Options {
    pub byteorder: ByteOrder,
    pub max_default: usize,
    pub max_count: usize,
}

impl Default for Options {
    fn default() -> Self {
        Options {
            byteorder: ByteOrder::Big,
            max_default: 67_108_864,
            max_count: 16_777_216,
        }
    }
}

pub fn compile(fields: &[FieldIn], opts: &Options) -> SRes<Program> {
    let mut c = C {
        opts,
        scopes: Vec::new(),
    };
    let prog = c.compile_scope(fields, opts.byteorder)?;
    let frames = frame_depth(&prog);
    if frames > MAX_DEPTH {
        return Err(err(format!(
            "schema nests {frames} structs deep, more than the {MAX_DEPTH} \
             unpacking allows (the schema itself is the outermost one)"
        )));
    }
    Ok(prog)
}

/// How many frames unpacking this program needs at its deepest.
///
/// `run_struct` pushes one frame per `Op::Nest` and one for the program it
/// is handed, and refuses to go past `MAX_DEPTH`. Knowing that here means a
/// schema too deep to ever decode is refused at compile time, rather than
/// compiling, packing happily, and failing every single unpack.
fn frame_depth(prog: &Program) -> usize {
    fn in_op(op: &Op) -> usize {
        match op {
            // The only op that costs a frame, windowed or not.
            Op::Nest { prog, .. } => frame_depth(prog),
            // Pass-through: these decode their child in the same frame.
            Op::Array { elem, .. } | Op::Cond { then: elem, .. } => in_op(elem),
            Op::Switch { cases, default, .. } => cases
                .iter()
                .map(|(_, op)| in_op(op))
                .chain(default.iter().map(|op| in_op(op)))
                .max()
                .unwrap_or(0),
            _ => 0,
        }
    }
    1 + prog.ops.iter().map(in_op).max().unwrap_or(0)
}

fn err(msg: impl Into<String>) -> SchemaError {
    SchemaError::new(msg)
}

/// Where a field physically lives — for deferred reg/derived/span patching.
enum Loc {
    Fix { op: usize, item: usize },
    Bit { op: usize, item: usize },
    Op(usize),
}

struct Sym {
    name: Arc<str>,
    loc: Loc,
    refable: bool,
    is_flags: bool,
    is_const: bool,
    spannable: bool,
    reg: Option<u8>,
    span: Option<u8>,
    /// Set once, at the end of the scope (see `resolve_deferred_derives`):
    /// whether this field ends up backpatched from a later consumer, or
    /// stays an ordinary field the caller supplies (and any len/count/size
    /// reference to it just checks consistency instead) -- see
    /// `len_referenced`.
    derived: bool,
    /// A field referenced by at least one len/count/size expression is a
    /// *candidate* for becoming derived -- but not if it's also a switch
    /// discriminant (`used_in_on`) anywhere in this same scope: a switch
    /// case's cases are compiled inline into the enclosing scope (not a
    /// separate one, unlike a nested struct), so every len/count/size and
    /// every switch `on=` reference to a field in an ancestor struct
    /// resolves to the exact same Sym regardless of which case it came
    /// from, and both roles are only fully known once the whole scope is
    /// done.
    len_referenced: bool,
    used_in_on: bool,
}

struct Scope {
    ops: Vec<Op>,
    syms: Vec<Sym>,
    nregs: u8,
    nspans: u8,
    /// (Digest op index, over, digest field name) — resolved at end of scope.
    digests: Vec<(usize, OverIn, String)>,
}

#[derive(Clone, Copy, PartialEq)]
enum RefCtx {
    /// len/count/size: a candidate to become derived, the expression must
    /// be linearly invertible. Whether the target actually ends up derived
    /// is decided once the whole scope is known, by `resolve_deferred_derives`
    /// -- not here, since the same field may also be a switch discriminant
    /// elsewhere in the very same scope (a switch's cases compile inline,
    /// not into a scope of their own).
    Len,
    /// switch on: an explicit discriminant in the mapping. If the target
    /// also collects a len/count/size reference elsewhere in this scope, it
    /// stays an ordinary explicit field instead of becoming derived (any
    /// len/count/size consumer just checks consistency against it, exactly
    /// like a const field already does).
    On,
    /// when's pred: purely a read of an already-known sibling register, no
    /// side effect on the referenced field at all (not derived, not a
    /// discriminant -- it can be any of those things too, independently).
    Pred,
}

struct C<'a> {
    opts: &'a Options,
    scopes: Vec<Scope>,
}

impl C<'_> {
    fn compile_scope(&mut self, fields: &[FieldIn], bo: ByteOrder) -> SRes<Program> {
        self.scopes.push(Scope {
            ops: Vec::new(),
            syms: Vec::new(),
            nregs: 0,
            nspans: 0,
            digests: Vec::new(),
        });
        let r = self.compile_scope_inner(fields, bo);
        let mut scope = self.scopes.pop().expect("scope stack");
        r?;
        resolve_deferred_derives(&mut scope);
        finish_scope(scope)
    }

    fn compile_scope_inner(&mut self, fields: &[FieldIn], bo: ByteOrder) -> SRes<()> {
        for f in fields {
            self.compile_field(f, bo)?;
        }
        Ok(())
    }

    fn top(&mut self) -> &mut Scope {
        self.scopes.last_mut().expect("scope stack")
    }

    fn check_name(&mut self, name: &Option<String>) -> SRes<Option<Arc<str>>> {
        match name {
            None => Ok(None),
            Some(n) => {
                if n.is_empty() {
                    return Err(err("field name must not be empty"));
                }
                if self.top().syms.iter().any(|s| &*s.name == n.as_str()) {
                    return Err(err(format!("duplicate field name {n:?} in scope")));
                }
                Ok(Some(Arc::from(n.as_str())))
            }
        }
    }

    fn add_sym(
        &mut self,
        name: Option<Arc<str>>,
        loc: Loc,
        refable: bool,
        is_flags: bool,
        is_const: bool,
        spannable: bool,
    ) {
        if let Some(name) = name {
            self.top().syms.push(Sym {
                name,
                loc,
                refable,
                is_flags,
                is_const,
                spannable,
                reg: None,
                span: None,
                derived: false,
                len_referenced: false,
                used_in_on: false,
            });
        }
    }

    fn push_fixed(&mut self, kind: FixKind, key: Key) -> Loc {
        let w = kind.width();
        let scope = self.top();
        let last_idx = scope.ops.len().wrapping_sub(1);
        if let Some(Op::Fixed { width, items }) = scope.ops.last_mut() {
            let off = *width;
            *width += w;
            items.push(FixedItem {
                off,
                kind,
                reg: None,
                span: None,
                key,
            });
            Loc::Fix {
                op: last_idx,
                item: items.len() - 1,
            }
        } else {
            scope.ops.push(Op::Fixed {
                width: w,
                items: vec![FixedItem {
                    off: 0,
                    kind,
                    reg: None,
                    span: None,
                    key,
                }],
            });
            Loc::Fix {
                op: scope.ops.len() - 1,
                item: 0,
            }
        }
    }

    fn resolve_ref(&mut self, name: &str, ctx: RefCtx) -> SRes<Reg> {
        for (up, scope) in self.scopes.iter_mut().rev().enumerate() {
            let Some(pos) = scope.syms.iter().position(|s| &*s.name == name) else {
                continue;
            };
            {
                let sym = &scope.syms[pos];
                if sym.is_flags {
                    return Err(err(format!(
                        "ref to flags field {name:?} is forbidden; use bits instead"
                    )));
                }
                if !sym.refable {
                    return Err(err(format!(
                        "ref {name:?}: only integer fields can be referenced"
                    )));
                }
            }
            if scope.syms[pos].reg.is_none() {
                if scope.nregs as usize >= MAX_REGS {
                    return Err(err("exceeded the limit of 16 registers per scope"));
                }
                let idx = scope.nregs;
                scope.nregs += 1;
                scope.syms[pos].reg = Some(idx);
                patch_reg(scope, pos, idx);
            }
            let sym = &mut scope.syms[pos];
            match ctx {
                RefCtx::Len => {
                    if !sym.is_const {
                        sym.len_referenced = true;
                    }
                }
                RefCtx::On => {
                    sym.used_in_on = true;
                }
                RefCtx::Pred => {}
            }
            let idx = scope.syms[pos].reg.expect("reg set above");
            return Ok(Reg { up: up as u8, idx });
        }
        Err(err(format!(
            "unknown name {name:?} in ref (references are backward-only: a field can only ref an already-decoded sibling or ancestor)"
        )))
    }

    /// len/count/size: constant folding, linear analysis, inversion, postfix emission.
    fn build_len(&mut self, e: &ExprIn, what: &str) -> SRes<LenSrc> {
        if let ExprIn::Greedy = e {
            return Ok(LenSrc::Greedy);
        }
        let folded = fold(e)?;
        if let ExprIn::Imm(n) = folded {
            if n < 0 {
                return Err(err(format!("{what}: negative constant length {n}")));
            }
            return Ok(LenSrc::Expr {
                expr: Expr::imm(n),
                inv: None,
            });
        }
        let lin = linear(&folded).map_err(|_| {
            err(format!(
                "{what}: expression is not invertible (must have the form a*ref + b)"
            ))
        })?;
        let Some((ref_name, a)) = lin.ref_ else {
            unreachable!("non-Imm without a ref after folding");
        };
        if a == 0 {
            return Err(err(format!("{what}: coefficient on ref is zero")));
        }
        let reg = self.resolve_ref(&ref_name, RefCtx::Len)?;
        let expr = self.emit_expr(&folded, RefCtx::Len)?;
        Ok(LenSrc::Expr {
            expr,
            inv: Some(Inv { reg, a, b: lin.b }),
        })
    }

    fn emit_expr(&mut self, e: &ExprIn, ctx: RefCtx) -> SRes<Expr> {
        let mut out = Vec::new();
        self.emit_into(e, ctx, &mut out)?;
        if postfix_depth(&out) > EXPR_STACK {
            return Err(err(
                "expression too deep (the expression evaluation stack holds at most 8 values)",
            ));
        }
        Ok(Expr(out))
    }

    fn emit_into(&mut self, e: &ExprIn, ctx: RefCtx, out: &mut Vec<Ins>) -> SRes<()> {
        match e {
            ExprIn::Imm(n) => out.push(Ins::Imm(*n)),
            ExprIn::Greedy => {
                return Err(err(
                    "\"*\" is only allowed as the whole of len/count, not inside an expression",
                ))
            }
            ExprIn::Ref(name) => out.push(Ins::Reg(self.resolve_ref(name, ctx)?)),
            ExprIn::Bin(op, l, r) => {
                self.emit_into(l, ctx, out)?;
                self.emit_into(r, ctx, out)?;
                out.push(match op {
                    BinOp::Add => Ins::Add,
                    BinOp::Sub => Ins::Sub,
                    BinOp::Mul => Ins::Mul,
                    BinOp::Div => Ins::Div,
                    BinOp::Shl => Ins::Shl,
                    BinOp::Shr => Ins::Shr,
                    BinOp::And => Ins::And,
                    BinOp::Or => Ins::Or,
                    BinOp::Xor => Ins::Xor,
                    BinOp::Eq => Ins::Eq,
                    BinOp::Ne => Ins::Ne,
                    BinOp::Lt => Ins::Lt,
                    BinOp::Le => Ins::Le,
                    BinOp::Gt => Ins::Gt,
                    BinOp::Ge => Ins::Ge,
                });
            }
        }
        Ok(())
    }

    fn compile_field(&mut self, f: &FieldIn, bo: ByteOrder) -> SRes<()> {
        let name = self.check_name(&f.name)?;
        let key = match &name {
            Some(n) => Key::Named(n.clone()),
            None => Key::Skip,
        };
        match &f.ty {
            TypeIn::Int {
                prim,
                byteorder,
                const_,
            } => {
                if let Some(c) = const_ {
                    if *c < prim.min() || *c > prim.max() {
                        return Err(err(format!("const {c} is out of range for {prim:?}")));
                    }
                }
                let be = byteorder.unwrap_or(bo) == ByteOrder::Big;
                let loc = self.push_fixed(
                    FixKind::Int {
                        prim: *prim,
                        be,
                        expected: *const_,
                        derived: false,
                    },
                    key,
                );
                self.add_sym(name, loc, true, false, const_.is_some(), true);
            }
            TypeIn::Float { is64, byteorder } => {
                let be = byteorder.unwrap_or(bo) == ByteOrder::Big;
                let kind = if *is64 {
                    FixKind::F64 { be }
                } else {
                    FixKind::F32 { be }
                };
                let loc = self.push_fixed(kind, key);
                self.add_sym(name, loc, false, false, false, true);
            }
            TypeIn::Bool { const_ } => {
                let loc = self.push_fixed(FixKind::Bool { expected: *const_ }, key);
                self.add_sym(name, loc, false, false, const_.is_some(), true);
            }
            TypeIn::Raw { len, const_ } => {
                let len = match (len, const_) {
                    (Some(l), Some(c)) if *l != c.len() => {
                        return Err(err(format!(
                            "raw: len={l} does not match the length of const ({})",
                            c.len()
                        )))
                    }
                    (Some(l), _) => *l,
                    (None, Some(c)) => c.len(),
                    (None, None) => return Err(err("raw: len or const is required")),
                };
                let expected = const_.as_ref().map(|c| Arc::from(c.as_slice()));
                let is_const = expected.is_some();
                let loc = self.push_fixed(FixKind::Raw { len, expected }, key);
                self.add_sym(name, loc, false, false, is_const, true);
            }
            TypeIn::Bytes { len, max } => {
                let name = named_only(name, "bytes")?;
                let len = self.build_len(len, "bytes.len")?;
                let op = Op::Bytes {
                    len,
                    max: max.unwrap_or(self.opts.max_default),
                    c: Common::named(name.clone()),
                };
                let loc = self.push_op(op);
                self.add_sym(Some(name), loc, false, false, false, true);
            }
            TypeIn::StrT {
                len,
                max,
                encoding,
                errors,
            } => {
                let name = named_only(name, "str")?;
                let enc = parse_enc(encoding, errors)?;
                let len = self.build_len(len, "str.len")?;
                let op = Op::Str {
                    len,
                    max: max.unwrap_or(self.opts.max_default),
                    enc,
                    c: Common::named(name.clone()),
                };
                let loc = self.push_op(op);
                self.add_sym(Some(name), loc, false, false, false, true);
            }
            TypeIn::CStrT {
                max,
                encoding,
                errors,
            } => {
                let name = named_only(name, "cstr")?;
                let enc = parse_enc(encoding, errors)?;
                let op = Op::CStr {
                    max: max.unwrap_or(self.opts.max_default),
                    enc,
                    c: Common::named(name.clone()),
                };
                let loc = self.push_op(op);
                self.add_sym(Some(name), loc, false, false, false, true);
            }
            TypeIn::Bits { width, signed } => {
                if !(1..=64).contains(width) {
                    return Err(err(format!("bits: width {width} is outside 1..64")));
                }
                let item = BitItem {
                    width: *width,
                    signed: *signed,
                    reg: None,
                    derived: false,
                    key,
                };
                let scope = self.top();
                let last_idx = scope.ops.len().wrapping_sub(1);
                let loc = if let Some(Op::BitRun { items, .. }) = scope.ops.last_mut() {
                    items.push(item);
                    Loc::Bit {
                        op: last_idx,
                        item: items.len() - 1,
                    }
                } else {
                    scope.ops.push(Op::BitRun {
                        nbytes: 0,
                        items: vec![item],
                    });
                    Loc::Bit {
                        op: scope.ops.len() - 1,
                        item: 0,
                    }
                };
                self.add_sym(name, loc, true, false, false, false);
            }
            TypeIn::FlagsT {
                base,
                byteorder,
                names,
                rest,
            } => {
                let name = named_only(name, "flags")?;
                if base.signed() {
                    return Err(err("flags: base must be unsigned (u8..u64)"));
                }
                let be = byteorder.unwrap_or(bo) == ByteOrder::Big;
                let base_mask = if base.width() == 8 {
                    u64::MAX
                } else {
                    (1u64 << (base.width() * 8)) - 1
                };
                let mut union = 0u64;
                let mut items = Vec::with_capacity(names.len());
                for (fname, mask) in names {
                    if fname.is_empty() || fname == "_rest" {
                        return Err(err(format!("flags: invalid mask name {fname:?}")));
                    }
                    if items.iter().any(|i: &FlagItem| &*i.key == fname.as_str()) {
                        return Err(err(format!("flags: duplicate name {fname:?}")));
                    }
                    if *mask == 0 {
                        return Err(err(format!("flags: zero mask {fname:?}")));
                    }
                    if mask & !base_mask != 0 {
                        return Err(err(format!(
                            "flags: mask {fname:?} does not fit in {base:?}"
                        )));
                    }
                    if mask & union != 0 {
                        return Err(err(format!("flags: mask {fname:?} overlaps another mask")));
                    }
                    let shift = mask.trailing_zeros();
                    let m = mask >> shift;
                    if m & (m + 1) != 0 {
                        return Err(err(format!("flags: non-contiguous mask {fname:?}")));
                    }
                    union |= mask;
                    items.push(FlagItem {
                        key: Arc::from(fname.as_str()),
                        mask: *mask,
                        shift,
                        is_bool: mask.count_ones() == 1,
                    });
                }
                let rest = match rest.as_str() {
                    "keep" => RestPolicy::Keep,
                    "strict" => RestPolicy::Strict,
                    "ignore" => RestPolicy::Ignore,
                    other => return Err(err(format!("flags: unknown rest policy {other:?}"))),
                };
                let op = Op::Flags {
                    prim: *base,
                    be,
                    items,
                    rest,
                    rest_mask: base_mask & !union,
                    c: Common::named(name.clone()),
                };
                let loc = self.push_op(op);
                self.add_sym(Some(name), loc, false, true, false, true);
            }
            TypeIn::DigestT {
                algo,
                overrides,
                over,
                verify,
            } => {
                let name = named_only(name, "digest")?;
                let algo = parse_algo(algo, overrides)?;
                if let OverIn::Names(names) = over {
                    if names.is_empty() {
                        return Err(err("digest: empty over"));
                    }
                    let mut seen = Vec::new();
                    for n in names {
                        if seen.contains(&n) {
                            return Err(err(format!("digest: duplicate {n:?} in over")));
                        }
                        if n.as_str() == &*name {
                            return Err(err(
                                "digest: a digest field cannot list itself by name in over",
                            ));
                        }
                        seen.push(n);
                    }
                }
                let be = bo == ByteOrder::Big;
                let placeholder = Over::Spans(Vec::new());
                let op = Op::Digest {
                    algo,
                    over: placeholder,
                    verify: *verify,
                    be,
                    c: Common::named(name.clone()),
                };
                let loc = self.push_op(op);
                let Loc::Op(op_idx) = loc else { unreachable!() };
                self.top()
                    .digests
                    .push((op_idx, over.clone(), name.to_string()));
                self.add_sym(Some(name), Loc::Op(op_idx), false, false, false, true);
            }
            TypeIn::StructT {
                fields,
                byteorder,
                size,
            } => {
                let name = named_only(name, "struct")?;
                let inner_bo = byteorder.unwrap_or(bo);
                let size = match size {
                    None => None,
                    Some(e) => {
                        let src = self.build_len(e, "struct.size")?;
                        if matches!(src, LenSrc::Greedy) {
                            return Err(err("struct.size: \"*\" is not supported"));
                        }
                        Some(src)
                    }
                };
                let prog = self.compile_scope(fields, inner_bo)?;
                if let (Some(LenSrc::Expr { expr, inv: None }), Some(st)) =
                    (size.as_ref(), prog.static_size)
                {
                    if let Some(sz) = expr.as_const() {
                        if sz as i128 != st as i128 {
                            return Err(err(format!(
                                "struct.size={sz} does not match the static body size {st}"
                            )));
                        }
                    }
                }
                let op = Op::Nest {
                    prog: Arc::new(prog),
                    size,
                    c: Common::named(name.clone()),
                };
                let loc = self.push_op(op);
                self.add_sym(Some(name), loc, false, false, false, true);
            }
            TypeIn::ArrayT {
                elem,
                count,
                until_eof,
            } => {
                let name = named_only(name, "array")?;
                let count_src = match (count, until_eof) {
                    (Some(_), true) => {
                        return Err(err("array: count and until_eof are mutually exclusive"))
                    }
                    (None, false) => return Err(err("array: count or until_eof is required")),
                    (None, true) => CountSrc::UntilEof,
                    (Some(ExprIn::Greedy), false) => CountSrc::UntilEof,
                    (Some(e), false) => match self.build_len(e, "array.count")? {
                        LenSrc::Expr { expr, inv } => CountSrc::Expr { expr, inv },
                        LenSrc::Greedy => CountSrc::UntilEof,
                    },
                };
                let elem_op = self.compile_elem(elem, bo)?;
                let elem_min = op_min(&elem_op);
                let op = Op::Array {
                    count: count_src,
                    max_count: self.opts.max_count,
                    elem: Box::new(elem_op),
                    elem_min,
                    c: Common::named(name.clone()),
                };
                let loc = self.push_op(op);
                self.add_sym(Some(name), loc, false, false, false, true);
            }
            TypeIn::SwitchT { on, cases, default } => {
                let name = named_only(name, "switch")?;
                if cases.is_empty() && default.is_none() {
                    return Err(err("switch: no branches at all"));
                }
                let folded = fold(on)?;
                let on_expr = self.emit_expr(&folded, RefCtx::On)?;
                let mut compiled = Vec::with_capacity(cases.len());
                let mut tags: Vec<i64> = Vec::new();
                for (tag, ty) in cases {
                    if tags.contains(tag) {
                        return Err(err(format!("switch: duplicate branch {tag}")));
                    }
                    tags.push(*tag);
                    compiled.push((*tag, Box::new(self.compile_elem(ty, bo)?)));
                }
                let default = match default {
                    Some(ty) => Some(Box::new(self.compile_elem(ty, bo)?)),
                    None => None,
                };
                let op = Op::Switch {
                    on: on_expr,
                    cases: compiled,
                    default,
                    c: Common::named(name.clone()),
                };
                let loc = self.push_op(op);
                self.add_sym(Some(name), loc, false, false, false, true);
            }
            TypeIn::CondT { pred, then } => {
                let name = named_only(name, "when")?;
                let folded = fold(pred)?;
                let pred_expr = self.emit_expr(&folded, RefCtx::Pred)?;
                // Self-contained, like an array element or switch branch
                // (compile_elem): a Cond-wrapped field can never be `ref`d
                // by anything else (no register of its own is allocated),
                // and can never be named in a digest's `over` (not
                // spannable) -- deliberately restricted for v1.
                let then_op = self.compile_elem(then, bo)?;
                let op = Op::Cond {
                    pred: pred_expr,
                    then: Box::new(then_op),
                    c: Common::named(name.clone()),
                };
                let loc = self.push_op(op);
                self.add_sym(Some(name), loc, false, false, false, false);
            }
        }
        Ok(())
    }

    fn push_op(&mut self, op: Op) -> Loc {
        let scope = self.top();
        scope.ops.push(op);
        Loc::Op(scope.ops.len() - 1)
    }

    /// A type in the position of an array element / switch branch: a
    /// single op with a Skip key.
    fn compile_elem(&mut self, ty: &TypeIn, bo: ByteOrder) -> SRes<Op> {
        Ok(match ty {
            TypeIn::Int {
                prim,
                byteorder,
                const_,
            } => {
                if let Some(c) = const_ {
                    if *c < prim.min() || *c > prim.max() {
                        return Err(err(format!("const {c} is out of range for {prim:?}")));
                    }
                }
                let be = byteorder.unwrap_or(bo) == ByteOrder::Big;
                fixed1(FixKind::Int {
                    prim: *prim,
                    be,
                    expected: *const_,
                    derived: false,
                })
            }
            TypeIn::Float { is64, byteorder } => {
                let be = byteorder.unwrap_or(bo) == ByteOrder::Big;
                fixed1(if *is64 {
                    FixKind::F64 { be }
                } else {
                    FixKind::F32 { be }
                })
            }
            TypeIn::Bool { const_ } => fixed1(FixKind::Bool { expected: *const_ }),
            TypeIn::Raw { len, const_ } => {
                let len = match (len, const_) {
                    (Some(l), Some(c)) if *l != c.len() => {
                        return Err(err("raw: len does not match the length of const"))
                    }
                    (Some(l), _) => *l,
                    (None, Some(c)) => c.len(),
                    (None, None) => return Err(err("raw: len or const is required")),
                };
                fixed1(FixKind::Raw {
                    len,
                    expected: const_.as_ref().map(|c| Arc::from(c.as_slice())),
                })
            }
            TypeIn::Bytes { len, max } => Op::Bytes {
                len: self.build_len(len, "bytes.len")?,
                max: max.unwrap_or(self.opts.max_default),
                c: Common::skip(),
            },
            TypeIn::StrT {
                len,
                max,
                encoding,
                errors,
            } => Op::Str {
                len: self.build_len(len, "str.len")?,
                max: max.unwrap_or(self.opts.max_default),
                enc: parse_enc(encoding, errors)?,
                c: Common::skip(),
            },
            TypeIn::CStrT {
                max,
                encoding,
                errors,
            } => Op::CStr {
                max: max.unwrap_or(self.opts.max_default),
                enc: parse_enc(encoding, errors)?,
                c: Common::skip(),
            },
            TypeIn::FlagsT { .. } => {
                // flags is rare as an element type but valid.
                // compile_field can't be reused on a temporary scope directly
                // (it deals with names), so route through a disposable scope.
                return self.elem_via_field(ty, bo);
            }
            TypeIn::StructT {
                fields,
                byteorder,
                size,
            } => {
                let inner_bo = byteorder.unwrap_or(bo);
                let size = match size {
                    None => None,
                    Some(e) => {
                        let src = self.build_len(e, "struct.size")?;
                        if matches!(src, LenSrc::Greedy) {
                            return Err(err("struct.size: \"*\" is not supported"));
                        }
                        Some(src)
                    }
                };
                let prog = self.compile_scope(fields, inner_bo)?;
                Op::Nest {
                    prog: Arc::new(prog),
                    size,
                    c: Common::skip(),
                }
            }
            TypeIn::ArrayT {
                elem,
                count,
                until_eof,
            } => {
                let count_src = match (count, until_eof) {
                    (Some(_), true) => {
                        return Err(err("array: count and until_eof are mutually exclusive"))
                    }
                    (None, false) => return Err(err("array: count or until_eof is required")),
                    (None, true) => CountSrc::UntilEof,
                    (Some(ExprIn::Greedy), false) => CountSrc::UntilEof,
                    (Some(e), false) => match self.build_len(e, "array.count")? {
                        LenSrc::Expr { expr, inv } => CountSrc::Expr { expr, inv },
                        LenSrc::Greedy => CountSrc::UntilEof,
                    },
                };
                let elem_op = self.compile_elem(elem, bo)?;
                let elem_min = op_min(&elem_op);
                Op::Array {
                    count: count_src,
                    max_count: self.opts.max_count,
                    elem: Box::new(elem_op),
                    elem_min,
                    c: Common::skip(),
                }
            }
            TypeIn::SwitchT { on, cases, default } => {
                if cases.is_empty() && default.is_none() {
                    return Err(err("switch: no branches at all"));
                }
                let folded = fold(on)?;
                let on_expr = self.emit_expr(&folded, RefCtx::On)?;
                let mut compiled = Vec::with_capacity(cases.len());
                let mut tags: Vec<i64> = Vec::new();
                for (tag, ty) in cases {
                    if tags.contains(tag) {
                        return Err(err(format!("switch: duplicate branch {tag}")));
                    }
                    tags.push(*tag);
                    compiled.push((*tag, Box::new(self.compile_elem(ty, bo)?)));
                }
                let default = match default {
                    Some(ty) => Some(Box::new(self.compile_elem(ty, bo)?)),
                    None => None,
                };
                Op::Switch {
                    on: on_expr,
                    cases: compiled,
                    default,
                    c: Common::skip(),
                }
            }
            TypeIn::Bits { .. } => return Err(err("bits is only allowed as a struct field")),
            TypeIn::DigestT { .. } => return Err(err("digest is only allowed as a struct field")),
            TypeIn::CondT { .. } => return Err(err("when is only allowed as a struct field")),
        })
    }

    /// flags as an element type: compiled through compile_field on a
    /// throwaway single-use scope, then the op is extracted.
    fn elem_via_field(&mut self, ty: &TypeIn, bo: ByteOrder) -> SRes<Op> {
        self.scopes.push(Scope {
            ops: Vec::new(),
            syms: Vec::new(),
            nregs: 0,
            nspans: 0,
            digests: Vec::new(),
        });
        let r = self.compile_field(
            &FieldIn {
                name: Some("_elem".into()),
                ty: ty.clone(),
            },
            bo,
        );
        let mut scope = self.scopes.pop().expect("scope stack");
        r?;
        let mut op = scope.ops.pop().expect("one field produces exactly one op");
        if let Some(c) = op_common_mut(&mut op) {
            c.key = Key::Skip;
        }
        Ok(op)
    }
}

fn op_common_mut(op: &mut Op) -> Option<&mut Common> {
    match op {
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

fn fixed1(kind: FixKind) -> Op {
    let w = kind.width();
    Op::Fixed {
        width: w,
        items: vec![FixedItem {
            off: 0,
            kind,
            reg: None,
            span: None,
            key: Key::Skip,
        }],
    }
}

fn named_only(name: Option<Arc<str>>, what: &str) -> SRes<Arc<str>> {
    name.ok_or_else(|| {
        err(format!(
            "{what} cannot be unnamed (only fixed-width types, bits and raw may be unnamed)"
        ))
    })
}

/// The encodings, as one table: the canonical spelling `Enc::ALL`
/// publishes and the normalized forms `parse_enc` matches come out of the
/// same declaration, so one cannot gain a spelling the other has never
/// heard of.
///
/// The bracketed names are matched *after* normalization -- lowercased with
/// `-` and `_` removed -- which is why they are written without separators
/// and why the canonical name is listed separately rather than reused.
macro_rules! encodings {
    ($($variant:path => $canon:literal [$($norm:literal),+ $(,)?]),+ $(,)?) => {
        impl Enc {
            /// The canonical spelling of each encoding: what `vocabulary()`
            /// publishes, and what `rustruct.Encoding`'s members are.
            pub const ALL: &'static [&'static str] = &[$($canon),+];
        }

        fn enc_from_normalized(norm: &str) -> Option<Enc> {
            match norm {
                $($($norm)|+ => Some($variant),)+
                _ => None,
            }
        }
    };
}

encodings! {
    Enc::Utf8 => "utf-8" ["utf8"],
    Enc::Ascii => "ascii" ["ascii", "usascii"],
    Enc::Latin1 => "latin-1" ["latin1", "iso88591"],
}

fn parse_enc(encoding: &str, errors: &str) -> SRes<Enc> {
    if errors != "strict" {
        return Err(err(format!(
            "errors={errors:?} is not supported by the v1 core (only \"strict\")"
        )));
    }
    let norm = encoding.to_ascii_lowercase().replace(['-', '_'], "");
    enc_from_normalized(&norm).ok_or_else(|| {
        err(format!(
            "encoding {encoding:?} is not supported by the v1 core ({})",
            Enc::ALL.join("/")
        ))
    })
}

fn parse_algo(name: &str, ov: &CrcOverrides) -> SRes<Algo> {
    let algo = Algo::preset(name).ok_or_else(|| err(format!("digest: unknown algo {name:?}")))?;
    match algo {
        Algo::Crc(mut spec) => {
            if let Some(p) = ov.poly {
                spec.poly = p;
            }
            if let Some(i) = ov.init {
                spec.init = i;
            }
            if let Some(x) = ov.xorout {
                spec.xorout = x;
            }
            if let Some(r) = ov.refin {
                spec.refin = r;
            }
            if let Some(r) = ov.refout {
                spec.refout = r;
            }
            Ok(Algo::Crc(spec))
        }
        other => {
            if ov.any() {
                return Err(err("digest: poly/init/xorout/refin/refout overrides only apply to a CRC algo, not a hash"));
            }
            Ok(other)
        }
    }
}

fn patch_reg(scope: &mut Scope, sym_pos: usize, idx: u8) {
    match scope.syms[sym_pos].loc {
        Loc::Fix { op, item } => {
            if let Op::Fixed { items, .. } = &mut scope.ops[op] {
                items[item].reg = Some(idx);
            }
        }
        Loc::Bit { op, item } => {
            if let Op::BitRun { items, .. } = &mut scope.ops[op] {
                items[item].reg = Some(idx);
            }
        }
        Loc::Op(_) => unreachable!("registers only exist on fixed/bits fields"),
    }
}

fn patch_derived(scope: &mut Scope, sym_pos: usize) {
    match scope.syms[sym_pos].loc {
        Loc::Fix { op, item } => {
            if let Op::Fixed { items, .. } = &mut scope.ops[op] {
                if let FixKind::Int { derived, .. } = &mut items[item].kind {
                    *derived = true;
                }
            }
        }
        Loc::Bit { op, item } => {
            if let Op::BitRun { items, .. } = &mut scope.ops[op] {
                items[item].derived = true;
            }
        }
        Loc::Op(_) => unreachable!(),
    }
}

/// The whole scope is compiled now, so every field's full set of roles
/// (len/count/size-referenced, switch-discriminant) is known: a field
/// collects `len_referenced` and `used_in_on` independently as they're
/// found (in any order, from any switch case, since cases compile inline
/// into this same scope), and only now do we decide whether it actually
/// becomes derived. A field that's also a switch discriminant somewhere
/// stays an ordinary, caller-supplied field instead -- its len/count/size
/// consumers still work, just as a consistency check against the given
/// value rather than a backward computation of it, the same as a const
/// field already gets.
fn resolve_deferred_derives(scope: &mut Scope) {
    for pos in 0..scope.syms.len() {
        let sym = &scope.syms[pos];
        if sym.len_referenced && !sym.used_in_on && !sym.is_const && !sym.derived {
            patch_derived(scope, pos);
            scope.syms[pos].derived = true;
        }
    }
}

/// Finalizes a scope: bit alignment, digest spans, sizes.
fn finish_scope(mut scope: Scope) -> SRes<Program> {
    for op in &mut scope.ops {
        if let Op::BitRun { nbytes, items } = op {
            let total: u32 = items.iter().map(|i| u32::from(i.width)).sum();
            if !total.is_multiple_of(8) {
                return Err(err(format!(
                    "bit_alignment: bit run occupies {total} bits, not a whole number of bytes"
                )));
            }
            *nbytes = (total / 8) as usize;
        }
    }

    for (op_idx, over_in, dname) in std::mem::take(&mut scope.digests) {
        let over = match over_in {
            OverIn::Star => Over::Star,
            OverIn::Names(names) => {
                let mut spans = Vec::with_capacity(names.len());
                for n in &names {
                    let Some(pos) = scope.syms.iter().position(|s| &*s.name == n.as_str()) else {
                        return Err(err(format!(
                            "digest {dname:?}: unknown name {n:?} in over (only the current scope is visible)"
                        )));
                    };
                    if !scope.syms[pos].spannable {
                        return Err(err(format!(
                            "digest {dname:?}: field {n:?} cannot be covered (bits fields cannot)"
                        )));
                    }
                    if scope.syms[pos].span.is_none() {
                        if scope.nspans as usize >= MAX_SPANS {
                            return Err(err("exceeded the limit of 16 span registers per scope"));
                        }
                        let idx = scope.nspans;
                        scope.nspans += 1;
                        scope.syms[pos].span = Some(idx);
                        match scope.syms[pos].loc {
                            Loc::Fix { op, item } => {
                                if let Op::Fixed { items, .. } = &mut scope.ops[op] {
                                    items[item].span = Some(idx);
                                }
                            }
                            Loc::Op(op) => {
                                if let Some(c) = op_common_mut(&mut scope.ops[op]) {
                                    c.span = Some(idx);
                                }
                            }
                            Loc::Bit { .. } => unreachable!("checked via spannable"),
                        }
                    }
                    spans.push(scope.syms[pos].span.expect("span set"));
                }
                Over::Spans(spans)
            }
        };
        if let Op::Digest { over: o, .. } = &mut scope.ops[op_idx] {
            *o = over;
        }
    }

    let mut min_size = 0usize;
    let mut static_size = Some(0usize);
    for op in &scope.ops {
        min_size = min_size.saturating_add(op_min(op));
        static_size = match (static_size, op_static(op)) {
            (Some(a), Some(b)) => Some(a + b),
            _ => None,
        };
    }

    Ok(Program {
        ops: scope.ops,
        nregs: scope.nregs,
        nspans: scope.nspans,
        min_size,
        static_size,
    })
}

pub(crate) fn op_min(op: &Op) -> usize {
    match op {
        Op::Fixed { width, .. } => *width,
        Op::Bytes { len, .. } | Op::Str { len, .. } => len_const(len).unwrap_or(0),
        Op::CStr { .. } => 1,
        Op::BitRun { nbytes, .. } => *nbytes,
        Op::Flags { prim, .. } => prim.width(),
        Op::Digest { algo, .. } => algo.width_bytes(),
        Op::Nest { prog, size, .. } => match size {
            Some(LenSrc::Expr { expr, inv: None }) => {
                expr.as_const().map(|n| n as usize).unwrap_or(prog.min_size)
            }
            Some(_) => prog.min_size,
            None => prog.min_size,
        },
        Op::Array {
            count, elem_min, ..
        } => match count {
            CountSrc::Expr { expr, inv: None } => expr
                .as_const()
                .map(|n| (n as usize).saturating_mul(*elem_min))
                .unwrap_or(0),
            _ => 0,
        },
        Op::Switch { cases, default, .. } => {
            let mut m = usize::MAX;
            for (_, op) in cases {
                m = m.min(op_min(op));
            }
            if let Some(d) = default {
                m = m.min(op_min(d));
            }
            if m == usize::MAX {
                0
            } else {
                m
            }
        }
        // Absent (predicate false) contributes zero bytes -- 0 is the
        // only sound lower bound.
        Op::Cond { .. } => 0,
    }
}

fn op_static(op: &Op) -> Option<usize> {
    match op {
        Op::Fixed { width, .. } => Some(*width),
        Op::Bytes { len, .. } | Op::Str { len, .. } => len_const(len),
        Op::CStr { .. } => None,
        Op::BitRun { nbytes, .. } => Some(*nbytes),
        Op::Flags { prim, .. } => Some(prim.width()),
        Op::Digest { algo, .. } => Some(algo.width_bytes()),
        Op::Nest { prog, size, .. } => match size {
            Some(LenSrc::Expr { expr, inv: None }) => expr.as_const().map(|n| n as usize),
            Some(_) => None,
            None => prog.static_size,
        },
        Op::Array { count, elem, .. } => match count {
            CountSrc::Expr { expr, inv: None } => {
                let n = expr.as_const()? as usize;
                Some(n.checked_mul(op_static(elem)?)?)
            }
            _ => None,
        },
        Op::Switch { cases, default, .. } => {
            let mut sizes = cases.iter().map(|(_, op)| op_static(op));
            let first = sizes.next()??;
            for s in sizes {
                if s? != first {
                    return None;
                }
            }
            if let Some(d) = default {
                if op_static(d)? != first {
                    return None;
                }
            }
            Some(first)
        }
        // Presence is runtime-conditional, so there's never a single
        // static size.
        Op::Cond { .. } => None,
    }
}

fn len_const(len: &LenSrc) -> Option<usize> {
    match len {
        LenSrc::Expr { expr, inv: None } => expr.as_const().map(|n| n as usize),
        _ => None,
    }
}

/// Constant subexpression folding: overflow and div-by-0 on
/// constants become a SchemaError at compile time.
fn fold(e: &ExprIn) -> SRes<ExprIn> {
    Ok(match e {
        ExprIn::Imm(_) | ExprIn::Ref(_) => e.clone(),
        ExprIn::Greedy => {
            return Err(err(
                "\"*\" is only allowed as the whole of len/count, not inside an expression",
            ))
        }
        ExprIn::Bin(op, l, r) => {
            let l = fold(l)?;
            let r = fold(r)?;
            if let (ExprIn::Imm(a), ExprIn::Imm(b)) = (&l, &r) {
                ExprIn::Imm(apply_const(*op, *a, *b)?)
            } else {
                ExprIn::Bin(*op, Box::new(l), Box::new(r))
            }
        }
    })
}

fn apply_const(op: BinOp, a: i64, b: i64) -> SRes<i64> {
    let ov = || err("i64 overflow in a constant subexpression");
    Ok(match op {
        BinOp::Add => a.checked_add(b).ok_or_else(ov)?,
        BinOp::Sub => a.checked_sub(b).ok_or_else(ov)?,
        BinOp::Mul => a.checked_mul(b).ok_or_else(ov)?,
        BinOp::Div => {
            if b == 0 {
                return Err(err("division by a constant zero"));
            }
            a.checked_div(b).ok_or_else(ov)?
        }
        BinOp::Shl => {
            if !(0..64).contains(&b) {
                return Err(err(
                    "shift amount outside 0..63 in a constant subexpression",
                ));
            }
            a.checked_shl(b as u32).ok_or_else(ov)?
        }
        BinOp::Shr => {
            if !(0..64).contains(&b) {
                return Err(err(
                    "shift amount outside 0..63 in a constant subexpression",
                ));
            }
            a >> b
        }
        BinOp::And => a & b,
        BinOp::Or => a | b,
        BinOp::Xor => a ^ b,
        BinOp::Eq => (a == b) as i64,
        BinOp::Ne => (a != b) as i64,
        BinOp::Lt => (a < b) as i64,
        BinOp::Le => (a <= b) as i64,
        BinOp::Gt => (a > b) as i64,
        BinOp::Ge => (a >= b) as i64,
    })
}

struct Lin {
    /// (ref name, coefficient a) — present if the expression contains a ref.
    ref_: Option<(String, i64)>,
    b: i64,
}

struct NonLinear;

/// value = a*ref + b — the only invertible form in v1.
fn linear(e: &ExprIn) -> Result<Lin, NonLinear> {
    match e {
        ExprIn::Imm(n) => Ok(Lin { ref_: None, b: *n }),
        ExprIn::Ref(name) => Ok(Lin {
            ref_: Some((name.clone(), 1)),
            b: 0,
        }),
        ExprIn::Greedy => Err(NonLinear),
        ExprIn::Bin(op, l, r) => {
            let l = linear(l)?;
            let r = linear(r)?;
            match op {
                BinOp::Add => {
                    let ref_ = match (l.ref_, r.ref_) {
                        (Some(_), Some(_)) => return Err(NonLinear),
                        (a, b) => a.or(b),
                    };
                    Ok(Lin {
                        ref_,
                        b: l.b.checked_add(r.b).ok_or(NonLinear)?,
                    })
                }
                BinOp::Sub => {
                    let ref_ = match (l.ref_, r.ref_) {
                        (Some(_), Some(_)) => return Err(NonLinear),
                        (Some(x), None) => Some(x),
                        (None, Some((n, a))) => Some((n, a.checked_neg().ok_or(NonLinear)?)),
                        (None, None) => None,
                    };
                    Ok(Lin {
                        ref_,
                        b: l.b.checked_sub(r.b).ok_or(NonLinear)?,
                    })
                }
                BinOp::Mul => {
                    let (with_ref, konst) = match (&l.ref_, &r.ref_) {
                        (Some(_), Some(_)) => return Err(NonLinear),
                        (Some(_), None) => (&l, r.b),
                        (None, _) => (&r, l.b),
                    };
                    let ref_ = match &with_ref.ref_ {
                        Some((n, a)) => Some((n.clone(), a.checked_mul(konst).ok_or(NonLinear)?)),
                        None => None,
                    };
                    Ok(Lin {
                        ref_,
                        b: with_ref.b.checked_mul(konst).ok_or(NonLinear)?,
                    })
                }
                _ => {
                    if l.ref_.is_some() || r.ref_.is_some() {
                        Err(NonLinear)
                    } else {
                        // Constants of this shape are already folded by fold().
                        Err(NonLinear)
                    }
                }
            }
        }
    }
}

fn postfix_depth(ins: &[Ins]) -> usize {
    let mut depth = 0usize;
    let mut max = 0usize;
    for i in ins {
        match i {
            Ins::Imm(_) | Ins::Reg(_) => {
                depth += 1;
                max = max.max(depth);
            }
            _ => depth -= 1,
        }
    }
    max
}
