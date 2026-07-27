# How to build and decode PNG files

## Build a transparent 1×1 image

<!-- name: test_use_png -->
```python
import zlib

from rustruct.formats.image.png import (
    Chunk,
    ChunkType,
    ColorType,
    IHDR,
    PNG,
)

image = PNG(
    chunks=[
        Chunk(
            type=ChunkType.IHDR,
            data=IHDR(
                width=1,
                height=1,
                bit_depth=8,
                color_type=ColorType.RGBA,
            ),
        ),
        Chunk(type=ChunkType.IDAT, data=zlib.compress(b"\x00\x00\x00\x00\x00")),
        Chunk(type=ChunkType.IEND, data=b""),
    ]
)
wire = image.pack()
```

## Decode it

<!-- name: test_use_png -->
```python
png = PNG.unpack(wire)
assert png.signature == b"\x89PNG\r\n\x1a\n"
assert png.chunks[0].type == ChunkType.IHDR
assert png.chunks[0].data.color_type == ColorType.RGBA
assert png.chunks[1].type == ChunkType.IDAT
assert png.pack() == wire
```

IHDR is decoded to a typed structure. IDAT, IEND, and unknown ancillary chunks
retain raw bytes. Chunk lengths and CRC-32 values are derived and verified by
the schema.

See {doc}`/reference/protocols` for the PNG classes and enums.
