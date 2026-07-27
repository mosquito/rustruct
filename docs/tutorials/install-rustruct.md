# 1. Install rustruct

This tutorial builds the frame format for a tiny chat relay -- a small TCP
server that forwards messages between connected clients. Create or activate
a Python 3.11 or newer environment, then install the package:

```console
python -m pip install rustruct
```

## Set up a development environment instead

Working on rustruct itself needs the Rust toolchain (`cargo`) and
[`uv`](https://docs.astral.sh/uv/) in addition to Python, since the core is a
Rust crate compiled into a `rustruct.core` extension module via
[`maturin`](https://www.maturin.rs/). From a checkout of the repository,
build the extension and run everything through the project `Makefile`:

```console
make build
```

`make build` compiles the Rust workspace and installs the extension in
editable mode (`uv sync --reinstall-package rustruct` under the hood), so
edits to the Rust sources take effect the next time a target that depends on
it runs.

Run the tests:

```console
make rust-tests   # cargo test --workspace: rustruct-core's own test suite
make pytest       # rebuilds the extension if needed, then runs the Python suite
make test         # both of the above (this is what plain `make` runs)
```

Build and check the documentation (the same code examples embedded in these
pages run as part of the test suite; `test-docs` additionally treats Sphinx
warnings as build failures):

```console
make docs         # build the HTML docs
make test-docs    # build docs, then run their embedded examples
```

`make fmt` runs `cargo fmt --all` and `make lint` runs `cargo clippy
--workspace --all-targets -- -D warnings`. `make clean` removes build
artifacts (`target/`, the compiled extension, and caches) if you need a
clean rebuild. `make help` lists all of this from the command line.

Next: {doc}`define-and-pack-a-structure`.
