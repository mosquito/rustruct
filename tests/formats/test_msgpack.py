"""`rustruct.formats.msgpack`: cross-checked against the reference `msgpack`
package (a real, independent implementation, not a self-roundtrip) and
tested for iterative (non-recursive) decode/encode at a nesting depth the
reference C implementation cannot itself construct recursively."""

import msgpack as ref
import pytest

from rustruct.formats.msgpack import decode, decode_from, encode

SCALAR_CASES = [
    None,
    True,
    False,
    0,
    1,
    15,
    16,
    31,
    32,
    127,
    128,
    255,
    256,
    65535,
    65536,
    2**32 - 1,
    2**32,
    2**64 - 1,
    -1,
    -32,
    -33,
    -128,
    -129,
    -32768,
    -32769,
    -(2**31),
    -(2**31) - 1,
    -(2**63),
    3.25,
    -1.5,
    "",
    "a",
    "x" * 15,
    "x" * 16,
    "x" * 31,
    "x" * 32,
    "x" * 300,
    "héllo wörld",
    b"",
    b"\x01" * 15,
    b"\x01" * 16,
    b"\x01" * 300,
]

CONTAINER_CASES = [
    [],
    [1, 2, 3],
    list(range(20)),
    list(range(70_000)),
    {},
    {"a": 1},
    {f"k{i}": i for i in range(20)},
    {"nested": [1, {"x": [2, 3, None, True, b"y"]}]},
]

ALL_CASES = SCALAR_CASES + CONTAINER_CASES


@pytest.mark.parametrize("value", ALL_CASES)
def test_decode_matches_reference_encoding(value):
    """The reference implementation encodes; we decode -- an independent
    check that our tag/bit layout matches the real format, not just our own
    encoder's conventions."""
    assert decode(ref.packb(value, use_bin_type=True)) == value


@pytest.mark.parametrize("value", ALL_CASES)
def test_reference_decodes_our_encoding(value):
    """We encode; the reference implementation decodes -- the other
    direction of the same independence check."""
    assert ref.unpackb(encode(value), raw=False) == value


@pytest.mark.parametrize("value", ALL_CASES)
def test_roundtrip(value):
    assert decode(encode(value)) == value


@pytest.mark.parametrize(
    "value",
    [None, True, False, 0, 1, 127, -1, -32, "", "a", "x" * 20, b"\x01\x02", [1, 2, 3], {"a": 1}],
)
def test_byte_exact_against_reference_packer(value):
    """For representative cases, we don't just interoperate with the
    reference packer -- we choose the exact same compact tag it does."""
    assert encode(value) == ref.packb(value, use_bin_type=True)


def test_reserved_tag_is_rejected():
    from rustruct import InvalidDataError

    with pytest.raises(InvalidDataError):
        decode(b"\xc1")


def test_decode_from_supports_streaming_multiple_values():
    wire = encode(1) + encode("two") + encode([3, 3, 3])
    first, offset = decode_from(wire, 0)
    second, offset = decode_from(wire, offset)
    third, offset = decode_from(wire, offset)
    assert (first, second, third) == (1, "two", [3, 3, 3])
    assert offset == len(wire)


def test_decode_rejects_trailing_data():
    with pytest.raises(ValueError, match="trailing"):
        decode(encode(1) + encode(2))


def test_decode_deeply_nested_array_without_recursion_error():
    # N nested fixarray-of-1, ending in nil -- constructed by hand, since
    # the reference C packer itself cannot recurse this deep to build it.
    depth = 100_000
    wire = bytes([0x91]) * depth + b"\xc0"
    value, offset = decode_from(wire, 0)
    assert offset == len(wire)
    seen = 0
    while isinstance(value, list):
        seen += 1
        value = value[0] if value else None
    assert seen == depth
    assert value is None


def test_encode_deeply_nested_list_without_recursion_error():
    depth = 100_000
    value = None
    for _ in range(depth):
        value = [value]
    wire = encode(value)
    assert wire == bytes([0x91]) * depth + b"\xc0"


def test_map_key_order_is_insertion_order():
    # Python dicts preserve insertion order; msgpack has no canonical order
    # requirement, so a straight round-trip through our own decode is the
    # right check here (an independent decoder is free to return the pairs
    # in the same wire order too, but isn't obligated to reconstruct the
    # exact same dict object identity/order guarantee we rely on).
    value = {"z": 1, "a": 2, "m": 3}
    assert list(decode(encode(value)).items()) == list(value.items())
