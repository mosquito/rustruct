# Architecture and execution model

The library separates schema declaration, compilation, native execution, and
typed object conversion into distinct stages precisely so that the
expensive parts -- validating a schema, deciding how each field's bytes are
produced or consumed -- happen once per class, not once per message.
Annotations or low-level field tuples are validated and compiled to a
reusable program up front. Every later pack or unpack call reuses that
already-validated program; it does not re-walk the Python declaration or
re-check the schema's own consistency. The Rust core executes that program
and returns either a mapping or, through the frontend, a typed
{py:class}`rustruct.Struct`.

## Python frontend

`StructMeta` records annotated fields and class options. Resolution is lazy so
registry subclasses imported before first use are visible. During resolution,
the frontend:

1. turns scalar annotations and descriptors into core field tuples;
2. resolves nested structures and switch cases;
3. compiles specialized `__init__`, `to_mapping`, and `from_mapping` methods;
4. creates and caches one low-level {py:class}`rustruct.Codec`.

Generated methods are AST-compiled Python functions. They avoid a generic
descriptor loop on every call and convert arrays of nested structures with
specialized list comprehensions.

## Rust core

The compiler validates fields, expressions, byte order, windows, limits, and
backpatch relationships, then lowers the schema to a compact `Program`.
Adjacent fixed-width operations are coalesced. Runtime execution walks that
program without reinterpreting the Python declaration.

The core crate contains no Python dependency. The PyO3 crate exposes buffers
and mappings to it and builds Python dictionaries directly during unpack.

## Pack path

1. {py:meth}`rustruct.Struct.pack` calls the generated `to_mapping()` method.
2. {py:meth}`rustruct.Codec.pack` crosses into the Rust core once.
3. The compiled program writes fields and backpatches derived lengths and
   digests.
4. The binding returns the completed `bytes`.

{py:meth}`rustruct.Struct.pack_into` runs the same packer and copies the result into a writable,
contiguous buffer at the requested offset.

## Unpack path

1. {py:meth}`rustruct.Codec.unpack` borrows a contiguous input through the buffer protocol.
2. The Rust program validates and decodes the structure.
3. The binding builds the Python mapping during that native walk.
4. The generated `from_mapping()` method constructs the typed {py:class}`rustruct.Struct`.

{py:meth}`rustruct.Codec.unpack` builds Python objects directly during the native walk rather
than first constructing an intermediate generic value tree. The {py:class}`rustruct.Struct`
frontend then turns nested mappings into the declared Python classes.

## Boundaries and limits

Every nested size window has an exclusive endpoint. Arrays and greedy fields
inherit that endpoint. Compilation and runtime enforce depth, expression
stack, allocation, and element-count limits so malformed lengths cannot cause
unbounded work by default -- a corrupt or adversarial length field can only
make decoding fail, never allocate memory or recurse without bound.
