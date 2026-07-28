"""The schema boundary: what it accepts, and what it says when it doesn't.

`rustruct.compile` takes the documented `(name, kind, opts)` form and parses
it in Rust. The option set each kind accepts is generated from the same
declaration the extraction reads (the `kinds!` table in
`crates/rustruct-py/src/parse.rs`), so these cover the behaviour that used to
depend on a hand-written allowlist staying in step with the code below it.
"""

import pytest

from helpers import u
from rustruct import SchemaError, compile
from rustruct.core import vocabulary
from rustruct.vocab import Kind


def test_an_unknown_option_is_rejected():
    # The old boundary kept a hand-written allowlist per kind alongside the
    # code reading those keys. Now the declaration generates both, so the
    # two cannot disagree.
    with pytest.raises(SchemaError, match="unknown option .ln."):
        compile((u("d", "bytes", len=4, ln=2),))
    with pytest.raises(SchemaError, match="unknown option .typo."):
        compile((u("x", "u8", typo=1),))


def test_an_unknown_option_set_to_none_is_still_rejected():
    # None means "not given", so an option can be passed unset. That read
    # is about the value, not the name: a misspelling used to slip through
    # whenever it happened to carry None, quietly disabling the very guard
    # the caller was relying on.
    compile((u("x", "u8", const=None),))
    with pytest.raises(SchemaError, match="unknown option .cnst."):
        compile((u("x", "u8", cnst=None),))


def test_a_missing_required_option_says_which():
    with pytest.raises(SchemaError, match="len is required"):
        compile((u("d", "bytes"),))
    with pytest.raises(SchemaError, match="width is required"):
        compile((u("b", "bits"),))


def test_array_extent_is_one_value_not_two_flags():
    # `count` and `until_eof` were independent options and setting both was
    # accepted, with one silently winning. They now fold into the single
    # count expression the core reads -- `until_eof=True` is not a separate
    # mode, it is a greedy count, which the core has always read as "until
    # the region ends".
    greedy = compile((u("a", "array", elem=("u8", {}), until_eof=True),))
    assert greedy._program_debug() == compile((u("a", "array", elem=("u8", {}), count="*"),))._program_debug()
    with pytest.raises(SchemaError, match="mutually exclusive"):
        compile((u("a", "array", elem=("u8", {}), count=3, until_eof=True),))
    with pytest.raises(SchemaError, match="count or until_eof is required"):
        compile((u("a", "array", elem=("u8", {})),))


def test_bad_values_still_raise_schema_error_not_typeerror():
    with pytest.raises(SchemaError, match="native"):
        compile((u("x", "u16", byteorder="native"),))
    with pytest.raises(SchemaError, match="outside 1..64"):
        compile((u("b", "bits", width=0),))
    with pytest.raises(SchemaError, match="unknown kind"):
        compile((u("x", "wat"),))


def test_a_bool_is_not_a_length():
    # bool is an int subclass, so `len=True` used to compile to len=1 and
    # pack a one-byte field with no complaint anywhere.
    with pytest.raises(SchemaError, match="bool is not a length"):
        compile((u("d", "bytes", len=True),))


def test_compile_still_accepts_every_iterable_it_used_to():
    # pyo3's Vec<T> extraction goes through the sequence protocol, which
    # would have quietly stopped accepting these; the boundary iterates.
    fields = [u("a", "u8"), u("b", "u8")]
    expected = compile(tuple(fields)).static_size
    assert compile(iter(fields)).static_size == expected
    assert compile(f for f in fields).static_size == expected
    assert compile(map(lambda f: f, fields)).static_size == expected
    assert compile({i: f for i, f in enumerate(fields)}.values()).static_size == expected


def test_the_published_option_table_is_the_one_the_parser_reads():
    # The macro emits the allowlist and the extraction from one declaration,
    # so there is no second list to compare against -- but it also publishes
    # that declaration, which is what lets Python check *its* option-emitting
    # helpers against what the core will really take.
    options = vocabulary()["options"]
    assert set(options) == {k.value for k in Kind}
    assert set(options["bytes"]) == {"len", "max"}
    assert set(options["bits"]) == {"width", "signed"}
    assert set(options["array"]) == {"elem", "count", "until_eof"}


@pytest.mark.parametrize("kind", sorted(vocabulary()["options"]))
def test_every_published_option_is_actually_accepted(kind):
    # The other direction: a name in the table that the arm below it does not
    # read would be a lie. An accepted-but-wrongly-typed value fails on its
    # value, never with "unknown option".
    for name in vocabulary()["options"][kind]:
        with pytest.raises(SchemaError) as excinfo:
            compile((u("x", kind, **{name: object()}),))
        assert "unknown option" not in str(excinfo.value), (kind, name)


def test_options_emitted_by_the_field_helpers_are_ones_the_core_takes():
    # `bits` once built a `const` option the boundary's allowlist rejected,
    # because the two were written separately. Anything `rustruct.fields` can
    # put in an opts dict is checked against the table the parser reads.
    from rustruct import fields as f

    options = vocabulary()["options"]
    specs = [
        f.raw(4, const=b"MAGI"),
        f.slice(4, max=8),
        f.string(4, max=8, encoding="ascii"),
        f.cstring(max=8),
        f.bits(4, signed=True),
        f.array(("u8", {}), count=2),
        f.array(("u8", {}), until_eof=True),
        f.digest("crc32", "*", verify=False, poly=1, init=0, xorout=0, refin=True, refout=True),
        f.when(("ref", "t"), ("u8", {})),
    ]
    for spec in specs:
        assert set(spec.opts) <= set(options[spec.kind]), spec.kind
