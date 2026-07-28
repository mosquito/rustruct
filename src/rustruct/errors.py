"""The exception types the core raises.

Defined here rather than in Rust with `create_exception!` for two reasons.

pyo3 emits no introspection data for a `create_exception!` class, so a
module exporting one is described with a fallback
`def __getattr__(name: str) -> Incomplete: ...` -- which tells a type
checker to accept *any* attribute of `rustruct.core`, including ones that
do not exist. The alternative, `#[pyclass(extends = PyException)]`, cannot
be used: `PyException` is only subclassable outside the limited API, and
this package ships abi3 so one wheel covers every CPython from 3.11.

And the attributes the core attaches to these -- `kind`, `offset`, `path`
-- were set with `setattr` from Rust, so nothing could see them: not a type
checker, not an editor, not `help()`. Here they are ordinary annotated
attributes.

The Rust side looks these classes up by name and raises them, so an
`except rustruct.SchemaError` still catches what it always caught.

Each sets `__module__` to `rustruct`, which is where they are imported
from and how they were spelled when Rust defined them -- so a traceback
still reads `rustruct.SchemaError`, not `rustruct.errors.SchemaError`.
"""

__all__ = ["InvalidDataError", "PackError", "RustructError", "SchemaError"]


class RustructError(Exception):
    """Base of every error this package raises."""

    __module__ = "rustruct"


class SchemaError(RustructError):
    """A schema `compile()` cannot accept.

    Raised while the schema is being read, before any data is touched --
    an unknown kind or option, a reference to a field that is not in scope,
    a byte order the core refuses.
    """

    __module__ = "rustruct"


class InvalidDataError(RustructError):
    """Wire data that does not match the schema.

    Raised by `unpack`, `unpack_from` and `parse`.
    """

    __module__ = "rustruct"

    kind: str
    """Machine-readable failure category, e.g. ``"checksum"`` or ``"tag"``
    -- one of the :class:`rustruct.ErrorKind` spellings."""

    offset: int
    """Byte offset in the input where the failure was detected."""

    path: str
    """Dotted field path where the failure occurred, empty at the top
    level."""


class PackError(RustructError):
    """Values that cannot be written as the schema describes them.

    Raised by `pack` and `pack_into`. There is no `offset`: the failure is
    found before anything is written.
    """

    __module__ = "rustruct"

    kind: str
    """Machine-readable failure category, e.g. ``"range"`` or
    ``"inconsistent"`` -- one of the :class:`rustruct.ErrorKind`
    spellings."""

    path: str
    """Dotted field path where the failure occurred, empty at the top
    level."""
