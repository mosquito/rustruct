"""PNG (Portable Network Graphics), ISO/IEC 15948 / RFC 2083.

A PNG file is an 8-byte signature followed by chunks running to end of file.
Each chunk is `{length, type, data, crc}`; the CRC-32 covers exactly
`type` + `data`, never `length`.

The chunk type is 4 ASCII bytes (e.g. `b"IHDR"`), kept as a plain `int`
(one big-endian u32) rather than converted to `str`: `switch()`'s `on=`
reads the field's raw value before any converter runs, so the discriminant
must already match `ChunkType`'s representation. `decode_chunk_type()`/
`encode_chunk_type()` convert to/from the readable 4-character form on
demand.

Only `IHDR` gets a further-decoded body. `IDAT`/`IEND` are registered as
plain raw bytes -- decompressing `IDAT` is an image-codec concern, not a
wire-layout one. Any other chunk type (`PLTE`, `tEXt`, `gAMA`, ...)
round-trips as raw bytes via the switch's `default` case.
"""

import enum

from rustruct import (
    U8,
    U32,
    Struct,
    array,
    convert,
    described,
    digest,
    raw,
    sized,
    slice,
    switch,
)

__all__ = ["SIGNATURE", "ColorType", "ChunkType", "IHDR", "Chunk", "PNG"]

SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ColorType(enum.IntEnum):
    GRAYSCALE = 0
    RGB = 2
    PALETTE = 3
    GRAYSCALE_ALPHA = 4
    RGBA = 6


class ChunkType(enum.IntEnum):
    """A chunk's 4-byte ASCII tag, read as one big-endian u32. RFC 2083's
    ancillary/private/safe-to-copy bits live in this same integer but
    aren't decoded separately -- this module only needs the tag for
    dispatch."""

    IHDR = int.from_bytes(b"IHDR", "big")
    PLTE = int.from_bytes(b"PLTE", "big")
    IDAT = int.from_bytes(b"IDAT", "big")
    IEND = int.from_bytes(b"IEND", "big")


def decode_chunk_type(value):
    """The 4-character ASCII form of a wire chunk-type int, for display or
    for matching an ancillary tag this module doesn't name in `ChunkType`."""
    return value.to_bytes(4, "big").decode("ascii")


def encode_chunk_type(value):
    return int.from_bytes(value.encode("ascii"), "big")


class IHDR(Struct, byteorder="big"):
    """The mandatory, always-first chunk: dimensions and pixel format."""

    width: U32 = described(help="image width in pixels")
    height: U32 = described(help="image height in pixels")
    bit_depth: U8 = described(help="bits per sample; meaning depends on color_type")
    color_type: object = convert(U8, decode=ColorType, encode=int, help="pixel format")
    compression_method: U8 = described(0, help="always 0 (deflate)")
    filter_method: U8 = described(0, help="always 0")
    interlace_method: U8 = described(0, help="0 = none, 1 = Adam7")


class Chunk(Struct, byteorder="big"):
    # Not directly switch-derived (the frontend doesn't look inside switch
    # cases when deciding what's derived), but each branch of `data` below
    # derives it via its own size=/len=; pack() always recomputes it.
    length: U32 = described(0, help="byte length of `data` alone")
    # Plain int, not converted to `str`: switch's `on=` reads the raw value
    # before any converter runs. Use ChunkType or decode_chunk_type() for
    # the readable 4-character form.
    type: U32 = described(help="4-byte ASCII chunk tag, as one big-endian u32")
    # IDAT/IEND are registered explicitly (same raw-bytes shape the
    # `default` case would give them) so they're a visible part of the
    # schema rather than indistinguishable from a genuinely unknown tag.
    data: object = switch(
        on="type",
        cases={
            ChunkType.IHDR: sized(IHDR, size="length"),
            ChunkType.IDAT: slice(len="length"),
            ChunkType.IEND: slice(len="length"),
        },
        default=slice(len="length"),
    )
    crc: U32 = digest("crc32", over=("type", "data"), help="CRC-32 over type+data")


class PNG(Struct, byteorder="big"):
    signature: bytes = raw(8, const=SIGNATURE)
    chunks: list = array(elem=Chunk, until_eof=True)
