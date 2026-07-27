"""PNG (ISO/IEC 15948 / RFC 2083): the first real exercise of rustruct's
digest field through the declarative frontend (IPv4/TCP/UDP/DNS don't
compute a checksum). Every CRC is cross-checked against Python's own
zlib.crc32 -- an independent implementation, not just self-roundtripping --
matching this repo's existing "verify against something else" standard for
protocol ports (tests/protocols/test_dns_fixtures.py does the same against
dnslib)."""

import struct as pystruct
import zlib

import pytest

from rustruct import InvalidDataError
from rustruct.formats.image.png import (
    IHDR,
    PNG,
    SIGNATURE,
    Chunk,
    ChunkType,
    ColorType,
    decode_chunk_type,
    encode_chunk_type,
)


def make_idat(width, height):
    """One uncompressed-per-scanline (filter type 0) grayscale IDAT body:
    each pixel is its own (row, col) value, no need for a matching-length
    fixture per call site."""
    raw = b""
    for row in range(height):
        raw += b"\x00" + bytes((row * width + col) % 256 for col in range(width))
    return zlib.compress(raw, 9)


def make_png(width=2, height=2):
    idat = make_idat(width, height)
    return PNG(
        chunks=[
            Chunk(
                type=ChunkType.IHDR,
                data=IHDR(width=width, height=height, bit_depth=8, color_type=ColorType.GRAYSCALE),
            ),
            Chunk(type=ChunkType.IDAT, data=idat),
            Chunk(type=ChunkType.IEND, data=b""),
        ]
    ), idat


def iter_chunks_from_wire(wire):
    """A from-scratch reader (struct + zlib only) used to cross-check our
    own encoder without depending on any of our own decode logic."""
    off = 8
    while off < len(wire):
        (length,) = pystruct.unpack_from("!I", wire, off)
        ctype = wire[off + 4 : off + 8]
        data = wire[off + 8 : off + 8 + length]
        (crc,) = pystruct.unpack_from("!I", wire, off + 8 + length)
        yield ctype, data, crc
        off += 8 + length + 4
    assert off == len(wire), "chunk lengths must exactly tile the buffer"


def test_signature_is_written():
    png, _ = make_png()
    assert png.pack()[:8] == SIGNATURE


def test_every_crc_matches_an_independent_zlib_computation():
    png, _ = make_png()
    wire = png.pack()
    chunks = list(iter_chunks_from_wire(wire))
    assert len(chunks) == 3
    for ctype, data, crc in chunks:
        assert crc == zlib.crc32(ctype + data) & 0xFFFFFFFF


def test_length_covers_data_only_not_type_or_crc():
    png, idat = make_png()
    wire = png.pack()
    chunks = list(iter_chunks_from_wire(wire))
    assert chunks[1][0] == b"IDAT"
    assert chunks[1][1] == idat


def test_roundtrip_is_byte_identical():
    png, _ = make_png()
    wire = png.pack()
    assert PNG.unpack(wire).pack() == wire


def test_ihdr_decodes_as_a_typed_struct():
    png, _ = make_png(width=3, height=5)
    back = PNG.unpack(png.pack())
    ihdr = back.chunks[0].data
    assert isinstance(ihdr, IHDR)
    assert (ihdr.width, ihdr.height) == (3, 5)
    assert ihdr.bit_depth == 8
    assert ihdr.color_type is ColorType.GRAYSCALE
    assert ihdr.compression_method == 0
    assert ihdr.filter_method == 0


def test_idat_is_registered_but_stays_raw_bytes():
    # IDAT is a deliberate switch case (not a fallthrough to `default`),
    # but its Python-facing shape is still plain bytes: decompressing it
    # is a separate, image-codec-level concern, not a wire-layout one.
    png, idat = make_png()
    back = PNG.unpack(png.pack())
    assert back.chunks[1].type == ChunkType.IDAT
    assert back.chunks[1].data == idat
    assert not isinstance(back.chunks[1].data, IHDR)


def test_unknown_ancillary_chunk_roundtrips_as_raw_bytes():
    # A chunk type this module has never heard of (not in ChunkType at
    # all) must still round-trip -- the same "unknown tag survives" con-
    # tract protocols/inet.py and protocols/dns.py both already guarantee.
    png = PNG(
        chunks=[
            Chunk(type=ChunkType.IHDR, data=IHDR(width=1, height=1, bit_depth=8, color_type=ColorType.RGB)),
            Chunk(type=encode_chunk_type("tEXt"), data=b"Comment\x00hello"),
            Chunk(type=ChunkType.IEND, data=b""),
        ]
    )
    back = PNG.unpack(png.pack())
    assert decode_chunk_type(back.chunks[1].type) == "tEXt"
    assert back.chunks[1].data == b"Comment\x00hello"


def test_chunk_type_helpers_round_trip():
    assert decode_chunk_type(encode_chunk_type("IHDR")) == "IHDR"
    assert encode_chunk_type(decode_chunk_type(ChunkType.PLTE)) == ChunkType.PLTE


@pytest.mark.parametrize("color_type", list(ColorType))
def test_all_color_types_round_trip(color_type):
    ihdr = IHDR(width=1, height=1, bit_depth=8, color_type=color_type)
    assert IHDR.unpack(ihdr.pack()).color_type is color_type


def test_bad_signature_is_rejected_on_unpack():
    # SIGNATURE is baked in as a const, so a mismatched signature can only
    # be exercised by corrupting an already-packed wire, not by
    # constructing a PNG() with the wrong one in the first place.
    png, _ = make_png()
    corrupted = b"\x00" * 8 + png.pack()[8:]
    with pytest.raises(InvalidDataError) as excinfo:
        PNG.unpack(corrupted)
    assert excinfo.value.kind == "const"


def test_corrupted_crc_is_rejected_on_unpack():
    png, _ = make_png()
    wire = bytearray(png.pack())
    wire[-1] ^= 0xFF  # flip a bit in IEND's crc
    with pytest.raises(InvalidDataError) as excinfo:
        PNG.unpack(bytes(wire))
    assert excinfo.value.kind == "checksum"


def test_chunk_length_field_is_not_a_required_constructor_argument():
    # length is derived (from whichever branch of `data` actually fires),
    # matching a normal derived-length field's ergonomics -- the caller
    # never has to compute or pass it.
    Chunk(type=ChunkType.IEND, data=b"")
