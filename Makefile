.PHONY: all test rust-tests pytest build build-rust build-python docs test-docs fmt lint clean help

CARGO ?= cargo
UV ?= uv

# The compiled extension uv/maturin place directly in the source tree for an
# editable install (src/rustruct/rustruct.pth points straight at src/, so
# `import rustruct.core` needs this file to physically exist here).
EXT := src/rustruct/core.abi3.so
RUST_SRC := $(shell find crates -type f \( -name '*.rs' -o -name 'Cargo.toml' \)) Cargo.toml

all: test

## Build the Rust workspace (core + pyo3 crate), no Python involved. Does NOT
## produce $(EXT) -- that's maturin's job, not plain `cargo build` (see below).
build-rust:
	$(CARGO) build --workspace

## Build/install the Python extension, and regenerate its type stub from the
## type information pyo3 embeds in the built library. A real file-based
## dependency: only rebuilds when Rust sources actually changed (or $(EXT) is
## missing, e.g. right after `make clean`), instead of paying for it on every
## `make test`/`make pytest`. `uv sync` is for the dev tooling; maturin is
## what produces $(EXT), and `--generate-stubs` is a CLI flag with no
## [tool.maturin] equivalent, so it cannot ride along on the sync.
$(EXT): $(RUST_SRC) pyproject.toml
	$(UV) sync
	$(UV) run maturin develop --quiet --generate-stubs
	@test -f $(EXT) || (echo "error: $(EXT) was not produced by maturin" >&2; exit 1)

build-python: $(EXT)

build: build-rust build-python

## rustruct-core unit + integration tests, plus a compile check of rustruct-py
## (a pyo3 extension-module crate has nothing to run as its own test binary).
rust-tests:
	$(CARGO) test --workspace

## Python test suite. Depends on build-python so it always exercises the
## current Rust code, not a cached wheel.
pytest: build-python
	$(UV) run --no-sync pytest

test: rust-tests pytest

docs: build-python
	$(MAKE) -C docs html

test-docs: build-python
	$(MAKE) -C docs test-docs

fmt:
	$(CARGO) fmt --all

lint:
	$(CARGO) clippy --workspace --all-targets -- -D warnings

clean:
	$(CARGO) clean
	rm -rf .pytest_cache dist tests/__pycache__ src/rustruct/__pycache__
	rm -f $(EXT)

help:
	@echo "Targets:"
	@echo "  make test        - rust-tests + pytest (default: bare 'make' runs this)"
	@echo "  make rust-tests  - cargo test --workspace"
	@echo "  make pytest      - rebuild the extension, then run the Python test suite"
	@echo "  make build       - build the Rust workspace and the Python extension"
	@echo "  make docs        - build the Sphinx HTML documentation"
	@echo "  make test-docs   - build docs with warnings treated as errors"
	@echo "  make fmt         - cargo fmt --all"
	@echo "  make lint        - cargo clippy --workspace --all-targets -- -D warnings"
	@echo "  make clean       - remove build artifacts (target/, .venv/, caches)"
