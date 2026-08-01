# rustruct

[![Tests](https://github.com/mosquito/rustruct/workflows/tests/badge.svg)](https://github.com/mosquito/rustruct/actions?query=workflow%3Atests)
[![Latest Version](https://img.shields.io/pypi/v/rustruct.svg)](https://pypi.python.org/pypi/rustruct/)
[![Python Versions](https://img.shields.io/pypi/pyversions/rustruct.svg)](https://pypi.python.org/pypi/rustruct/)
[![License](https://img.shields.io/pypi/l/rustruct.svg)](https://pypi.python.org/pypi/rustruct/)
[![Stars](https://img.shields.io/github/stars/mosquito/rustruct.svg)](https://github.com/mosquito/rustruct)

A Rust core for parsing and building binary wire formats from Python --
[`construct`](https://pypi.org/project/construct/)'s declarative
ergonomics at `struct`'s speed.

Like `construct`, you describe a layout once -- as a `Struct` subclass,
or via `compile()` for schemas built at runtime -- with lengths, nested
regions, and derived fields tracked and validated by the schema itself,
not by hand. Unlike `construct`, that schema isn't walked field-by-field
in Python on every call: it's compiled once into a program that Rust
runs in a single Python -> Rust call for the whole structure, so parsing
and building cost about what the equivalent hand-written `struct` code
would.

## Example

`construct`'s own quick-start is a toy bitmap: a signature, a `width` and
`height`, and `width * height` pixels. The same schema in rustruct, with
one difference noted below:

<!-- name: test_readme_core -->
```python
from rustruct import U8, Struct, array, described, raw


class Bitmap(Struct):
    signature: bytes = raw(3, const=b"BMP")
    width: U8
    height: U8
    count: U8 = described(help="derived from len(pixels) on pack, like a length field")
    pixels: object = array(U8, count="count")


bmp = Bitmap(width=3, height=2, pixels=[7, 8, 9, 11, 12, 13])
assert bmp.pack() == b"BMP\x03\x02\x06\x07\x08\t\x0b\x0c\r"
```

The `count` field is the one difference from `construct`, which needs
none: its schema is walked by a Python interpreter on every call, so
`width * height` can be evaluated directly. rustruct compiles a schema
once, and a `count`/`len`/`size` it derives has to be invertible back to
one sibling field (`a*ref + b`) -- a product of two fields doesn't fit
that shape, on either side. `count` above is that one field: like
`file_size` in a real BMP header, the caller never computes it, it's
derived from `pixels` at pack time.

## Documentation

The full documentation is at
[mosquito.github.io/rustruct](https://mosquito.github.io/rustruct/): a
guided tutorial, task-oriented how-to guides, explanations of the schema
and execution model, and an API reference.

## Development

```bash
# Rust core (no Python)
cargo test -p rustruct-core

# Python package (maturin backend) + pytest
uv sync
uv run pytest

# Sphinx documentation (warnings are errors)
make test-docs
```
