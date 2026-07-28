"""Every name in `rustruct.vocab` is one the core actually accepts.

The vocabulary is a hand-written mirror of closed sets that live in Rust
(`parse_type`, `Algo::preset`, `parse_enc`, the rest-policy match, `BinOp`).
Nothing mechanically ties the two together, so each member here is fed
through a real `compile()`: a name that stops being accepted -- or was never
accepted, i.e. a typo in the mirror -- fails a test instead of surfacing as
a `SchemaError` in somebody's schema.

The check is one-directional by construction: it proves no member is
rejected, not that no kind exists in Rust without a member here. Closing
that direction needs the core to publish its own tables.
"""

import pytest

from helpers import u
from rustruct import (
    Algo,
    BinOp,
    ByteOrder,
    Encoding,
    ErrorKind,
    InvalidDataError,
    Kind,
    RestPolicy,
    SchemaError,
    compile,
)
from rustruct.vocab import REST_KEY

# A minimal schema exercising each kind, keyed by the member it covers.
# Several kinds are only legal as a struct field or need a companion field,
# hence whole field tuples rather than one field each.
MINIMAL_SCHEMAS = {
    Kind.U8: (u("x", "u8"),),
    Kind.I8: (u("x", "i8"),),
    Kind.U16: (u("x", "u16"),),
    Kind.I16: (u("x", "i16"),),
    Kind.U32: (u("x", "u32"),),
    Kind.I32: (u("x", "i32"),),
    Kind.U64: (u("x", "u64"),),
    Kind.I64: (u("x", "i64"),),
    Kind.F32: (u("x", "f32"),),
    Kind.F64: (u("x", "f64"),),
    Kind.BOOL: (u("x", "bool"),),
    Kind.RAW: (u("x", "raw", len=2),),
    Kind.BYTES: (u("n", "u8"), u("x", "bytes", len=("ref", "n"))),
    Kind.STR: (u("n", "u8"), u("x", "str", len=("ref", "n"))),
    Kind.CSTR: (u("x", "cstr", max=8),),
    Kind.BITS: (u("x", "bits", width=8),),
    Kind.FLAGS: (u("x", "flags", base="u8", names=(("a", 1),)),),
    Kind.STRUCT: (u("x", "struct", fields=(u("y", "u8"),)),),
    Kind.ARRAY: (u("x", "array", elem=("u8", {}), count=2),),
    Kind.SWITCH: (u("t", "u8"), u("x", "switch", on=("ref", "t"), cases=((1, ("u8", {})),))),
    Kind.COND: (u("t", "u8"), u("x", "cond", pred=("ref", "t"), then=("u8", {}))),
    Kind.DIGEST: (u("x", "u8"), u("d", "digest", algo="crc32", over="*")),
}


def test_every_kind_is_covered_by_a_minimal_schema():
    assert set(MINIMAL_SCHEMAS) == set(Kind)


@pytest.mark.parametrize("kind", list(Kind), ids=str)
def test_every_kind_compiles(kind):
    # The member itself is substituted into the field, so this checks the
    # enum's *value*, not just that some hand-written string works.
    fields = tuple((name, kind if k == str(kind) else k, opts) for name, k, opts in MINIMAL_SCHEMAS[kind])
    assert compile(fields).min_size >= 0


@pytest.mark.parametrize("algo", list(Algo), ids=str)
def test_every_digest_algo_compiles(algo):
    codec = compile((u("x", "u8"), u("d", "digest", algo=algo, over="*")))
    # Round-trips, so the algorithm is really implemented and not merely
    # accepted by the name parser.
    assert codec.unpack(codec.pack({"x": 1}))["x"] == 1


@pytest.mark.parametrize("byteorder", list(ByteOrder), ids=str)
def test_every_byteorder_compiles(byteorder):
    assert compile((u("x", "u16"),), byteorder=byteorder).static_size == 2
    assert compile((u("x", "u16", byteorder=byteorder),)).static_size == 2


def test_byteorder_network_is_big_endian():
    big = compile((u("x", "u16"),), byteorder=ByteOrder.BIG).pack({"x": 1})
    assert compile((u("x", "u16"),), byteorder=ByteOrder.NETWORK).pack({"x": 1}) == big
    assert compile((u("x", "u16"),), byteorder=ByteOrder.LITTLE).pack({"x": 1}) != big


def test_byteorder_has_no_native_member():
    # Not an oversight: the core refuses it, because it would make the wire
    # format depend on the machine doing the encoding.
    assert not hasattr(ByteOrder, "NATIVE")
    with pytest.raises(SchemaError):
        compile((u("x", "u16"),), byteorder="native")


@pytest.mark.parametrize("encoding", list(Encoding), ids=str)
def test_every_encoding_compiles(encoding):
    codec = compile((u("n", "u8"), u("s", "str", len=("ref", "n"), encoding=encoding)))
    assert codec.unpack(codec.pack({"s": "ok"}))["s"] == "ok"


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("utf-8", Encoding.UTF8),
        ("UTF-8", Encoding.UTF8),
        ("utf8", Encoding.UTF8),
        ("utf_8", Encoding.UTF8),
        ("ascii", Encoding.ASCII),
        ("US-ASCII", Encoding.ASCII),
        ("usascii", Encoding.ASCII),
        ("latin-1", Encoding.LATIN1),
        ("latin1", Encoding.LATIN1),
        ("ISO-8859-1", Encoding.LATIN1),
        ("iso8859_1", Encoding.LATIN1),
    ],
)
def test_encoding_accepts_every_spelling_the_core_does(spelling, expected):
    # Encoding._missing_ mirrors parse_enc's normalizer; if the two drift,
    # the enum would start refusing something compile() still accepts.
    assert Encoding(spelling) is expected
    codec = compile((u("n", "u8"), u("s", "str", len=("ref", "n"), encoding=spelling)))
    assert codec.unpack(codec.pack({"s": "ok"}))["s"] == "ok"


def test_encoding_rejects_what_the_core_rejects():
    with pytest.raises(ValueError):
        Encoding("cp1251")
    with pytest.raises(SchemaError):
        compile((u("n", "u8"), u("s", "str", len=("ref", "n"), encoding="cp1251")))


@pytest.mark.parametrize("rest", list(RestPolicy), ids=str)
def test_every_rest_policy_compiles(rest):
    codec = compile((u("fl", "flags", base="u8", names=(("a", 1),), rest=rest),))
    assert codec.unpack(b"\x01")["fl"]["a"] is True


def test_rest_key_is_what_keep_reports_under():
    codec = compile((u("fl", "flags", base="u8", names=(("a", 1),), rest=RestPolicy.KEEP),))
    assert REST_KEY in codec.unpack(b"\x03")["fl"]


@pytest.mark.parametrize("op", list(BinOp), ids=str)
def test_every_binop_parses(op):
    # `pred` takes an arbitrary expression, so it accepts the comparison
    # operators too -- unlike `len`, which has to stay linearly invertible.
    fields = (u("t", "u8"), u("x", "cond", pred=(op, ("ref", "t"), 1), then=("u8", {})))
    assert compile(fields).min_size == 1


def test_error_kinds_match_what_the_core_raises():
    # A sample across both directions; the full set is Rust's, and this only
    # pins that the spellings here are the spellings that arrive.
    with pytest.raises(InvalidDataError) as truncated:
        compile((u("x", "u32"),)).unpack(b"\x00")
    assert truncated.value.kind == ErrorKind.TRUNCATED

    with pytest.raises(InvalidDataError) as trailing:
        compile((u("x", "u8"),)).unpack(b"\x00\x00")
    assert trailing.value.kind == ErrorKind.TRAILING

    with pytest.raises(InvalidDataError) as const:
        compile((u("x", "u8", const=1),)).unpack(b"\x02")
    assert const.value.kind == ErrorKind.CONST

    with pytest.raises(InvalidDataError) as no_case:
        compile((u("t", "u8"), u("x", "switch", on=("ref", "t"), cases=((1, ("u8", {})),)))).unpack(b"\x09\x00")
    assert no_case.value.kind == ErrorKind.NO_CASE


def test_error_kind_members_compare_equal_to_the_plain_strings():
    # The Rust side sets a plain str, and existing user code compares
    # against plain strings; both spellings have to keep working.
    assert ErrorKind.TRUNCATED == "truncated"
    assert "truncated" == ErrorKind.TRUNCATED
    assert f"{ErrorKind.TRUNCATED}" == "truncated"


def test_members_are_str_subclasses_end_to_end():
    # The whole design rests on this: a StrEnum member crosses the FFI
    # unchanged, so naming a value never changes what gets compiled.
    plain = compile((u("tag", "u8"), u("d", "digest", algo="crc32", over="*")), byteorder="big")
    named = compile((u("tag", Kind.U8), u("d", Kind.DIGEST, algo=Algo.CRC32, over="*")), byteorder=ByteOrder.BIG)
    assert plain._program_debug() == named._program_debug()
    assert plain.pack({"tag": 7}) == named.pack({"tag": 7})


def test_kindstr_literal_matches_the_kind_enum():
    # KindStr is spelled out by hand (a Literal built by unpacking is not a
    # valid type form, and a checker that cannot evaluate it silently
    # accepts everything). This is what stops the duplication from drifting.
    from typing import get_args

    from rustruct.vocab import KindStr

    assert set(get_args(KindStr)) == {k.value for k in Kind}


def test_flags_base_takes_the_unsigned_kinds_and_refuses_the_signed_ones():
    # There is no `UnsignedKind` alias for this: nothing in `src/` annotates
    # a flags base (`fields.py` has no `flags()` helper, and the untyped
    # form is `KindArg`-typed at most), so the alias only ever described a
    # rule without enforcing it anywhere. The rule itself is a runtime one --
    # the boundary admits all eight integer kinds and the core rejects the
    # signed half -- so assert that directly.
    for base in (Kind.U8, Kind.U16, Kind.U32, Kind.U64):
        assert compile((u("fl", "flags", base=base, names=(("a", 1),)),)).static_size is not None
    for base in (Kind.I8, Kind.I16, Kind.I32, Kind.I64):
        with pytest.raises(SchemaError, match="unsigned"):
            compile((u("fl", "flags", base=base, names=(("a", 1),)),))


# ---------- the drift lock, in both directions ----------
#
# Every test above proves that a name Python knows is one the core accepts.
# That leaves the other direction open: a name added in Rust and forgotten
# here would simply go unused, with nothing failing. `core.__vocabulary__`
# publishes the Rust tables -- each one the same table the Rust code
# actually matches on -- so the two sets can be asserted equal.


def rust_vocabulary():
    from rustruct.core import vocabulary

    return {k: set(v) for k, v in vocabulary().items()}


def test_byteorders_match_rust():
    assert {b.value for b in ByteOrder} == rust_vocabulary()["byteorders"]


def test_binops_match_rust():
    assert {o.value for o in BinOp} == rust_vocabulary()["binops"]


def test_error_kinds_match_rust():
    assert {e.value for e in ErrorKind} == rust_vocabulary()["error_kinds"]


def test_encodings_match_rust():
    # The spelling tests above pin the aliases in both directions, one
    # spelling at a time. This is the other half: a fourth encoding added
    # to the core would otherwise stay invisible here, exactly the way
    # crc32c and crc64_xz did before they were named.
    assert {e.value for e in Encoding} == rust_vocabulary()["encodings"]


def test_errors_policy_is_still_the_single_one():
    # Asserted through a real compile() rather than against a published
    # table: a table listing the accepted policies would be a second copy of
    # the match that parses them, with nothing keeping the two in step.
    assert compile((u("n", "u8"), u("s", "str", len=("ref", "n"), errors="strict"))).min_size == 1
    with pytest.raises(SchemaError):
        compile((u("n", "u8"), u("s", "str", len=("ref", "n"), errors="replace")))


def test_integer_kinds_match_rust():
    prims = set(rust_vocabulary()["int_prims"])
    assert {k.value for k in Kind if k.value in prims} == prims
    # flags' base is the unsigned half of exactly that set; the previous
    # test is what proves the signed half is refused.
    assert {p for p in prims if p.startswith("u")} == {"u8", "u16", "u32", "u64"}
