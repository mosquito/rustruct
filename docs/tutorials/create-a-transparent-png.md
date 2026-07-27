# 5. Create a transparent 1×1 PNG

The PNG model works at the wire-format layer. Supply an RGBA IHDR, one
transparent pixel in the IDAT scanline, and an IEND chunk. The IDAT payload is
compressed with zlib because PNG image decompression is intentionally outside
the schema model:

<!-- name: test_tutorial_transparent_png -->
```python
import zlib

from rustruct.formats.image.png import Chunk, ChunkType, ColorType, IHDR, PNG


transparent_scanline = zlib.compress(b"\x00\x00\x00\x00\x00")
image = PNG(
    chunks=[
        Chunk(
            type=ChunkType.IHDR,
            data=IHDR(width=1, height=1, bit_depth=8, color_type=ColorType.RGBA),
        ),
        Chunk(type=ChunkType.IDAT, data=transparent_scanline),
        Chunk(type=ChunkType.IEND, data=b""),
    ]
)
wire = image.pack()
decoded = PNG.unpack(wire)

assert decoded.chunks[0].data.width == 1
assert decoded.chunks[0].data.height == 1
assert decoded.chunks[0].data.color_type == ColorType.RGBA
assert decoded.chunks[1].type == ChunkType.IDAT
assert decoded.pack() == wire
```

The first scanline byte is the PNG filter byte (`0`); the following four zero
bytes are the transparent RGBA pixel. {py:meth}`rustruct.Struct.pack` derives each length and
CRC-32, and {py:meth}`rustruct.Struct.unpack` verifies them on the way back.

Previous: {doc}`create-a-minimal-png-container`.

You have used the same typed workflow across a network packet, a
pointer-oriented protocol, and a chunked file format. The focused how-to
guides start at {doc}`/how-to/use-ipv4`,
{doc}`/how-to/use-dns`, and {doc}`/how-to/use-png`;
the complete class listings are in {doc}`/reference/protocols`.
