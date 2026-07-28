"""Fixed-width fields: sizes, endianness, const/magic, padding."""

import pytest

from helpers import nest_arrays, nest_expr, nest_structs, u
from rustruct import Codec, InvalidDataError, PackError, SchemaError, compile


def test_static_roundtrip():
    codec = compile(
        (
            u("a", "u8"),
            u("b", "u16"),
            u("c", "i32"),
            u("d", "f64"),
            u("e", "bool"),
        )
    )
    assert isinstance(codec, Codec)
    buf = bytes([1, 0x12, 0x34, 0xFF, 0xFF, 0xFF, 0xFE]) + bytes(8) + bytes([1])
    values = codec.unpack(buf)
    assert values == {"a": 1, "b": 0x1234, "c": -2, "d": 0.0, "e": True}
    assert codec.pack(values) == buf


def test_byteorder_little_and_field_override():
    codec = compile((u("x", "u32"), u("y", "u16", byteorder="big")), byteorder="little")
    values = codec.unpack(bytes([0x78, 0x56, 0x34, 0x12, 0xAB, 0xCD]))
    assert values == {"x": 0x12345678, "y": 0xABCD}


def test_negative_and_wide_ints():
    codec = compile((u("a", "i8"), u("b", "i64")))
    values = codec.unpack(bytes([0x80]) + (-1).to_bytes(8, "big", signed=True))
    assert values == {"a": -128, "b": -1}
    assert codec.pack(values) == bytes([0x80]) + (-1).to_bytes(8, "big", signed=True)


def test_float_roundtrip_both_endians():
    import struct as pystruct

    codec = compile((u("be", "f32"), u("le", "f64", byteorder="little")))
    buf = pystruct.pack(">f", 1.5) + pystruct.pack("<d", -2.5)
    values = codec.unpack(buf)
    assert values == {"be": 1.5, "le": -2.5}
    assert codec.pack(values) == buf


def test_sizes():
    codec = compile((u("a", "u32"), u("b", "u8")))
    assert codec.static_size == 5
    assert codec.min_size == 5
    dyn = compile((u("n", "u8"), u("data", "bytes", len=("ref", "n"))))
    assert dyn.static_size is None
    assert dyn.min_size == 1


def test_unpack_accepts_any_buffer():
    codec = compile((u("x", "u16"),))
    assert codec.unpack(b"\x01\x02") == {"x": 0x0102}
    assert codec.unpack(bytearray(b"\x01\x02")) == {"x": 0x0102}
    assert codec.unpack(memoryview(b"\x01\x02")) == {"x": 0x0102}


def test_unpack_from_allows_tail():
    codec = compile((u("x", "u8"),))
    values, pos = codec.unpack_from(b"\x07tail", 0)
    assert values == {"x": 7}
    assert pos == 1
    values, pos = codec.unpack_from(b"junk\x09", 4)
    assert values == {"x": 9}
    assert pos == 5


def test_trailing_error():
    codec = compile((u("x", "u8"),))
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x01\x02")
    assert ei.value.kind == "trailing"
    assert ei.value.offset == 1


def test_truncated_error():
    codec = compile((u("x", "u32"),))
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x01")
    assert ei.value.kind == "truncated"


def test_const_magic_and_padding():
    codec = compile(
        (
            (None, "raw", {"const": b"MAGI"}),
            u("ver", "u8", const=2),
            (None, "raw", {"len": 2}),
            u("x", "u8"),
        )
    )
    values = codec.unpack(b"MAGI\x02\xaa\xbb\x2a")
    assert values == {"ver": 2, "x": 0x2A}
    # pack: the magic comes from the schema, padding is zeros, const ignores input
    assert codec.pack({"x": 0x2A, "ver": 99}) == b"MAGI\x02\x00\x00\x2a"

    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"MAGJ\x02\xaa\xbb\x2a")
    assert ei.value.kind == "const"


def test_const_bool():
    codec = compile((u("flag", "bool", const=True), u("x", "u8")))
    assert codec.unpack(b"\x01\x07") == {"flag": True, "x": 7}
    with pytest.raises(InvalidDataError) as ei:
        codec.unpack(b"\x00\x07")
    assert ei.value.kind == "const"


def test_bool_pack_is_truthiness():
    codec = compile((u("b", "bool"),))
    assert codec.pack({"b": 5}) == b"\x01"
    assert codec.pack({"b": ""}) == b"\x00"
    assert codec.unpack(b"\x02") == {"b": True}


def test_float_const_is_forbidden_by_ir():
    # f32/f64 don't accept a `const` option at all (bit-pattern consts aren't
    # meaningful without picking u32/u64 instead).
    with pytest.raises(SchemaError):
        compile((u("f", "f32", const=1),))


def test_unnamed_dynamic_is_schema_error():
    with pytest.raises(SchemaError):
        compile(((None, "bytes", {"len": 4}),))


def test_raw_const_len_mismatch_is_schema_error():
    with pytest.raises(SchemaError):
        compile((u("magic", "raw", len=4, const=b"AB"),))


def test_pack_missing_key():
    codec = compile((u("x", "u8"), u("y", "u8")))
    with pytest.raises(PackError) as ei:
        codec.pack({"x": 1})
    assert ei.value.kind == "missing"
    assert ei.value.path == "y"


def test_pack_range():
    codec = compile((u("x", "i8"),))
    with pytest.raises(PackError) as ei:
        codec.pack({"x": 128})
    assert ei.value.kind == "range"
    assert ei.value.path == "x"
    assert codec.pack({"x": -128}) == b"\x80"


def test_extra_keys_ignored_on_pack():
    codec = compile((u("x", "u8"),))
    assert codec.pack({"x": 1, "junk": object(), "more": {1: 2}}) == b"\x01"

    class MyMapping:
        def items(self):
            return [("x", 5), ("extra", object())]

    assert codec.pack(MyMapping()) == b"\x05"


def test_schema_errors():
    with pytest.raises(SchemaError):
        compile((u("x", "u8"), u("x", "u16")))  # duplicate
    with pytest.raises(SchemaError):
        compile((u("data", "bytes", len=("ref", "n")), u("n", "u8")))  # forward ref
    with pytest.raises(SchemaError):
        compile((u("x", "u8", byteorder="native"),))
    with pytest.raises(SchemaError):
        compile((u("x", "u8", const=256),))  # const out of range
    with pytest.raises(SchemaError):
        compile((u("x", "u8", typo=1),))  # unknown option
    with pytest.raises(SchemaError):
        compile((u("x", "wat"),))  # unknown kind


def test_deep_schema_is_rejected_not_a_crash():
    # parse_type/parse_type_spec/parse_fields are mutually recursive over
    # caller-supplied data, so an unbounded schema used to overflow the C
    # stack and kill the interpreter outright (SIGSEGV, nothing to catch).
    compile(nest_structs(64))  # comfortably inside the limit
    with pytest.raises(SchemaError, match="nests deeper"):
        compile(nest_structs(4000))


def test_deep_array_nesting_is_rejected_not_a_crash():
    # A nested `elem` recurses through the type spec, not through a field
    # list the way struct-in-struct does -- a separate way down into the
    # same parser, so it needs its own proof that the cap is on the path.
    # Uncapped this one survives further than struct nesting does: it took
    # ~5k levels to segfault on a default 8 MB stack, hence 8000 here.
    compile(nest_arrays(60))
    with pytest.raises(SchemaError, match="nests deeper"):
        compile(nest_arrays(8000))


def test_deep_expression_is_rejected_not_a_crash():
    # Expressions recurse on a counter of their own, reset per option, so
    # the schema cap says nothing about them: uncapped, one flat field with
    # a deep enough `len` segfaults on its own (~12k levels on a default
    # 8 MB stack, hence 20000 here).
    codec = compile((u("b", "bytes", len=nest_expr(60)),))
    assert codec.unpack(b"\x07") == {"b": b"\x07"}
    with pytest.raises(SchemaError, match="nests deeper"):
        compile((u("b", "bytes", len=nest_expr(20000)),))


def test_depth_caps_are_exactly_where_they_claim():
    # Everything else about depth is tested thousands of levels past the
    # cap, so raising it tenfold would leave all of it green. The number
    # is the whole safety margin against a stack overflow, so it gets a
    # test that fails the moment it moves.
    #
    # Two caps, counted separately: one for how deep types nest, one for
    # how deep a single expression nests (reset per option, so a schema
    # full of 128-deep lengths is fine).
    compile(nest_structs(128))
    with pytest.raises(SchemaError, match="deeper than 128"):
        compile(nest_structs(129))

    compile((u("b", "bytes", len=nest_expr(128)),))
    with pytest.raises(SchemaError, match="deeper than 128"):
        compile((u("b", "bytes", len=nest_expr(129)),))


def test_absurdly_deep_schema_is_rejected_cheaply():
    # The cap has to bite at the cap, not after walking whatever it was
    # handed. A million levels is refused in well under a millisecond
    # because the parser never descends past 128 -- an implementation that
    # converted the tuple tree first and checked afterwards would sit here
    # chewing through a million frames instead. Both recursions get a turn.
    deep = nest_structs(1_000_000)
    with pytest.raises(SchemaError, match="nests deeper"):
        compile(deep)
    del deep  # ~330 MB; don't hold it while the next one is built
    with pytest.raises(SchemaError, match="nests deeper"):
        compile((u("b", "bytes", len=nest_expr(1_000_000)),))


def test_deep_schema_limit_is_above_anything_usable():
    # Two different limits, and neither number here is the parser's 128:
    # both schemas below compile fine. Unpacking caps live frames at 64
    # (MAX_DEPTH, program.rs), and a struct is what costs one -- including
    # the schema itself, which is the top frame. So 63 levels of nesting
    # is the last that decodes and 64 is one too many, which is why these
    # numbers look off by one against the cap they are testing.
    #
    # That is the gap this pins, in the right direction: a too-deep schema
    # has to get as far as compiling and fail on data. Drop the parser's
    # limit below 64 and the failure would arrive earlier, at compile
    # time, as a SchemaError instead.
    #
    # This bounds the parser's limit from below only. It says nothing
    # about other nesting: arrays cost no frame and decode far deeper,
    # see test_deep_array_nesting_decodes_past_the_struct_frame_limit.
    codec = compile(nest_structs(63))
    assert codec.unpack(b"\x01")
    codec = compile(nest_structs(64))
    with pytest.raises(InvalidDataError) as excinfo:
        codec.unpack(b"\x01")
    assert excinfo.value.kind == "depth"


def test_exception_hierarchy():
    import rustruct

    assert issubclass(SchemaError, rustruct.RustructError)
    assert issubclass(InvalidDataError, rustruct.RustructError)
    assert issubclass(PackError, rustruct.RustructError)


def test_serialization_not_implemented():
    codec = compile((u("x", "u8"),))
    with pytest.raises(NotImplementedError):
        codec.to_bytes()
    with pytest.raises(NotImplementedError):
        Codec.from_bytes(b"RSTR")


def test_struct_parity_semantics():
    import struct as pystruct

    codec = compile(
        (
            u("a", "u8"),
            u("b", "u16"),
            u("c", "u32"),
            u("d", "u64"),
            u("e", "i8"),
            u("f", "i16"),
            u("g", "i32"),
            u("h", "i64"),
        )
    )
    buf = bytes(range(30))
    names = ["a", "b", "c", "d", "e", "f", "g", "h"]
    expected = dict(zip(names, pystruct.unpack(">BHIQbhiq", buf), strict=True))
    assert codec.unpack(buf) == expected


def test_schema_errors_say_where_they_came_from():
    # The same complaint at three different depths used to produce three
    # byte-identical messages, so finding the offending field meant
    # re-reading the whole schema.
    def typo_at(fields):
        with pytest.raises(SchemaError) as excinfo:
            compile(fields)
        return str(excinfo.value)

    assert typo_at((u("x", "u8", typo=1),)).endswith("(at x)")
    assert typo_at((u("frame", "struct", fields=(u("x", "u8", typo=1),)),)).endswith("(at frame.x)")
    nested = (u("a", "struct", fields=(u("b", "struct", fields=(u("x", "u8", typo=1),)),)),)
    assert typo_at(nested).endswith("(at a.b.x)")


def test_schema_error_paths_name_the_container_that_led_there():
    def typo_at(fields):
        with pytest.raises(SchemaError) as excinfo:
            compile(fields)
        return str(excinfo.value)

    elem = ("struct", {"fields": (u("x", "u8", typo=1),)})
    assert typo_at((u("rows", "array", elem=elem, count=1),)).endswith("(at rows[].x)")

    switch_fields = (
        u("t", "u8"),
        u("b", "switch", on=("ref", "t"), cases=((7, elem),)),
    )
    assert typo_at(switch_fields).endswith("(at b?7.x)")

    default_fields = (
        u("t", "u8"),
        u("b", "switch", on=("ref", "t"), cases=((1, ("u8", {})),), default=elem),
    )
    assert typo_at(default_fields).endswith("(at b?default.x)")

    assert typo_at(((None, "raw", {"len": 1, "typo": 1}),)).endswith("(at <unnamed>)")


def test_schema_error_is_located_once_not_at_every_level():
    with pytest.raises(SchemaError) as excinfo:
        compile((u("a", "struct", fields=(u("b", "struct", fields=(u("x", "wat"),)),)),))
    assert str(excinfo.value).count("(at ") == 1
