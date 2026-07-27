# Native program reference

This page documents the Rust core's internal instruction set: the exact
`Op` variants {py:func}`rustruct.compile` lowers a schema into. Using the
Python API does not require reading it -- {doc}`schema-model` covers
derived fields, discriminants, and what a schema cannot express in terms of
the schema itself, with no Rust types involved. Read this page when
modifying `rustruct-core`, or when you want to know exactly what
instructions a particular schema compiles to and why.

A `Program` is a flat `Vec<Op>` the Rust core walks directly for both pack
and unpack, with no re-interpretation of the original declaration.

A `Program` is a fixed, fully unrolled tree: every nested `struct`, every
`array` element, every `switch` branch is inlined into the parent op at
compile time. There is no call/return or loop-back instruction -- a schema
that would require one (a type referencing itself, directly or through a
cycle of other types) cannot be compiled, because the resulting `Program`
would have to be infinite.

## Common

Every opcode except `Fixed` and `BitRun` carries a `Common { key, span }`:

- `key` -- `Key::Named(name)` if the field has a name and appears in the
  decoded mapping, or `Key::Skip` for a field that's part of the wire layout
  but never surfaces as its own key (an array element, a switch branch, a
  `when` payload -- these get their name, if any, from the field that wraps
  them).
- `span` -- set when a digest's `over=` covers this field, so pack/unpack can
  record where its bytes start and end.

`Fixed` and `BitRun` don't need a shared `Common` because they hold several
named sub-items directly (`FixedItem`/`BitItem`, each with its own `key`).

## Fixed

```
Fixed { width: usize, items: Vec<FixedItem> }
```

A coalesced run of same-region fixed-width fields, checked and read/written
as one contiguous span with a single bounds check -- not one op per scalar.
Each `FixedItem` has an `off` (byte offset within the run) and a `FixKind`:

- `Int { prim, be, expected, derived }` -- `u8`..`u64`/`i8`..`i64`.
  `expected` is set for `const=`; `derived` is set once a `len=`/`count=`/
  `size=` reference claims this field as a backpatch target (see
  [Derived fields and discriminants, in program terms](#derived-fields-and-discriminants-in-program-terms)).
- `F32 { be }` / `F64 { be }`.
- `Bool { expected }` -- one byte, nonzero is `True` on unpack; `expected`
  is set for `const=`.
- `Raw { len, expected }` -- a fixed-length byte string; `expected` is set
  for `const=` (checked on unpack, written on pack, e.g. a magic number).

Adjacent fixed fields (including across a struct's whole run of them) always
coalesce into one `Fixed` op -- a fully static schema lowers to exactly one.

## BitRun

```
BitRun { nbytes: usize, items: Vec<BitItem> }
```

Consecutive `bits()` fields packed most-significant-bit-first into whole
bytes (`nbytes = total_bits / 8`; compilation rejects a run that isn't a
whole number of bytes). Each `BitItem` has a `width`, `signed`, and its own
`derived` flag (the same backpatch mechanism as `FixKind::Int`, just scoped
to one item's bits instead of a whole byte-aligned field).

## Bytes / Str / CStr

```
Bytes { len: LenSrc, max: usize, c: Common }
Str   { len: LenSrc, max: usize, enc: Enc, c: Common }
CStr  { max: usize, enc: Enc, c: Common }
```

Dynamic-length byte/text fields. `LenSrc` is either `Expr { expr, inv }` (a
length expression, with `inv` set when it's linearly invertible for pack-time
derivation) or `Greedy` (`len="*"`/until-region-end). `Str` additionally
carries an `Enc` (`Utf8`/`Ascii`/`Latin1`); `CStr` has no `len` at all --
its extent is a NUL terminator on unpack, and pack writes one after the
string's bytes. `max` bounds the allocation regardless of what the length
expression evaluates to.

## Flags

```
Flags { prim, be, items: Vec<FlagItem>, rest: RestPolicy, rest_mask: u64, c }
```

A fixed-width integer decoded into named boolean/masked sub-fields. Each
`FlagItem` has a `mask`, a `shift`, and `is_bool` (a single-bit mask exposed
as `bool` rather than an unshifted int). `rest` (`Keep`/`Strict`/`Ignore`)
governs the bits no `FlagItem` names: keep them and round-trip verbatim,
reject a nonzero value at that mask (`Strict`), or silently zero them
(`Ignore`).

## Digest

```
Digest { algo: Algo, over: Over, verify: bool, be: bool, c: Common }
```

A checksum or hash. `over` is `Star` (the whole enclosing scope, treating the
digest field's own bytes as zero while computing) or `Spans(Vec<u8>)` (a list
of span-register indices naming specific sibling fields). Pack always
computes and writes the digest, ignoring any caller-supplied value; unpack
verifies it unless `verify=false`, and always exposes the wire value either
way.

## Nest

```
Nest { prog: Arc<Program>, size: Option<LenSrc>, c: Common }
```

A nested structure: `prog` is that structure's own, independently-compiled
`Program` (a whole `Vec<Op>` inlined here, not a reference to a shared one --
even a nested struct's *own* recursive-looking self-reference would need this
same `prog` field to hold an infinitely nested `Program`, which is why
self-referential schemas can't compile). `size`, if present, makes this an
exact byte window (`sized()`): the nested program must consume exactly that
many bytes, no more, no less.

## Array

```
Array { count: CountSrc, max_count: usize, elem: Box<Op>, elem_min: usize, c }
```

A repeated element. `elem` is one compiled `Op` reused for every element (the
same restriction as `Nest` applies transitively: an array of a
self-referential type can't compile either). `count` is `Expr{..}` or
`UntilEof` (`until_eof=True`/greedy). `max_count` bounds the element count
regardless of what `count`'s expression evaluates to; `elem_min` (the
element's own minimum size) lets streaming {py:meth}`rustruct.Codec.parse` compute how many more
bytes are needed without decoding partial elements.

## Switch

```
Switch { on: Expr, cases: Vec<(i64, Box<Op>)>, default: Option<Box<Op>>, c }
```

A tagged union: `on` selects a branch by exact integer match against `cases`
(no range matching -- see below), falling back to `default` when present or
raising otherwise. Cases compile *inline into the enclosing scope*, not into
a scope of their own -- a case's own field references (a nested `struct`
does get its own scope, but the case wrapper itself does not) resolve
against the same symbol table as the switch's own siblings. This is what
makes it possible for one field to be both a switch discriminant in some
branches and a length/count/size source in others (see next section).

## Cond

```
Cond { pred: Expr, then: Box<Op>, c: Common }
```

A conditionally-present field (`when(pred, then=...)`): absent (zero wire
bytes, no key in the decoded mapping -- not `null`) when `pred` evaluates to
0 against already-known registers, otherwise decoded/encoded as `then`.
Valid only as an ordinary struct field -- never as an array element, a
switch branch, or nested inside another `Cond` -- and, like an array element
or switch branch, gets no register of its own: nothing else can `ref` it,
and it can't be named in a digest's `over=`.

## Derived fields and discriminants, in program terms

{doc}`schema-model` explains why a field can be a derived length in some
switch branches and an explicit discriminant in others. Mechanically: a
`FixKind::Int`/`BitItem`'s `derived` flag and `Switch`'s use of a symbol as
`on` are tracked separately per symbol while a scope compiles, and only
resolved once the whole scope (all branches included) has been seen --
never at the first reference. A symbol used as `on` anywhere in its scope
never gets `derived` set, regardless of how many `len=`/`count=`/`size=`
expressions reference it elsewhere; those references compile to the same
"check against the actual data" path a `const=` field already uses.

## Why self-reference and range dispatch don't compile

Both limits in {doc}`schema-model`'s "What a schema cannot express" follow
directly from `Program` having no recursion opcode and `Switch` matching by
exact value: a self-referential type would need `Nest`/`Array`'s `prog`/
`elem` to hold an infinitely large `Program`, and `Switch`'s `cases` is a
`Vec<(i64, Box<Op>)>` matched by equality, with no range representation at
all.
