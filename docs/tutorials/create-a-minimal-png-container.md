# 4. Create a minimal PNG container

PNG is a chunked container format, not just an image codec: a fixed
8-byte signature followed by a sequence of typed, length-prefixed, CRC-
protected chunks. Building one exercises the same tagged-union and
computed-field ideas from the chat relay tutorial, applied to a format with
a real published specification and a large ecosystem of tools that will
happily open whatever bytes come out the other end. Construct a typed IHDR
chunk and an empty IEND chunk:

<!-- name: test_tutorial_minimal_png -->
```python
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
                color_type=ColorType.RGB,
            ),
        ),
        Chunk(type=ChunkType.IEND, data=b""),
    ]
)
wire = image.pack()
decoded = PNG.unpack(wire)

assert decoded.signature == b"\x89PNG\r\n\x1a\n"
assert decoded.chunks[0].type == ChunkType.IHDR
assert decoded.chunks[0].data.width == 1
assert decoded.pack() == wire
```

The PNG model derives chunk lengths and CRC-32 values. Unknown ancillary
chunks retain their data as bytes.

Previous: {doc}`build-and-decode-a-dns-query`. Next: {doc}`create-a-transparent-png`.
