# Extension model

The native program supports a fixed set of field kinds. Python code cannot
subclass a codec and inject callbacks into the Rust execution loop.

This is a deliberate boundary between layouts and algorithms. A closed native
instruction set lets schemas be validated before execution, keeps the hot walk
inside the Rust core, and prevents arbitrary Python callbacks from acquiring a
cursor with unclear ownership.

## Value-level conversion

{py:func}`rustruct.convert` keeps wire parsing in the core and runs Python only after the base
field has been decoded, or before it is encoded. Because a converter never
owns the cursor, it cannot invalidate region accounting or consume a different
number of bytes than the declared base field.

## Algorithmic formats

Some formats are algorithms rather than layouts. DNS compressed names can
jump to absolute offsets in the complete message and packing them needs a
message-wide suffix table. `rustruct.protocols.dns` handles those portions in
Python while reusing declarative {py:class}`rustruct.Struct` classes for DNS flags and fixed
RDATA types.

This boundary is deliberate: declarative components remain fast and
independently testable, while the exceptional algorithm stays explicit.

Practical choices and examples are in {doc}`/how-to/extend-schema`; the
supported public entry points are listed in {doc}`/reference/extensions`.
