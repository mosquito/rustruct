"""
The compiled core: the compiler entry point and `Codec`.

`src/rustruct/core.pyi` is generated from this module by
`make stubs` -- edit here and regenerate, never the stub.

Declared as an inline Rust module rather than a function because only
this form is introspectable: pyo3 emits the member list at compile
time, and it cannot know what a function body adds.
"""

from collections.abc import Iterable, Mapping
from typing import Any, Final, final
from typing_extensions import Buffer

__abi__: Final[int]
"""
Bumped on any change to the IR or the serialization format.
"""

@final
class Codec:
    def _program_debug(self, /) -> str:
        """
        The compiled `Program`, as Rust's own `Debug` rendering.
        
        Not an API: it lets a test assert that two schemas compile to the
        *same program*, which comparing pack/unpack behaviour can only
        approximate. Deterministic -- `Program` holds only `Vec`s,
        `Arc<str>`/`Arc<[u8]>` and plain enums, all of which `Debug` by value
        rather than by address.
        """
    @staticmethod
    def from_bytes(_data: bytes) -> Codec: ...
    @property
    def min_size(self, /) -> int:
        """
        Lower bound of the size.
        """
    def pack(self, /, values: Mapping[str, Any]) -> bytes: ...
    def pack_into(self, /, buf: Buffer, offset: int, values: Mapping[str, Any]) -> int:
        """
        Writes into an existing writable buffer, returns the new position.
        """
    def parse(self, /, buf: Buffer, offset: int = 0) -> tuple[dict[str, Any], int] |Incomplete:
        """
        Streaming parse: a data shortage yields Incomplete, not an exception.
        """
    @property
    def static_size(self, /) -> int |None:
        """
        Exact size, if the schema is static.
        """
    def to_bytes(self, /) -> bytes: ...
    def unpack(self, /, buf: Buffer) -> dict[str, Any]:
        """
        Requires the buffer to be fully consumed; a tail raises
        InvalidDataError with kind="trailing".
        """
    def unpack_from(self, /, buf: Buffer, offset: int = 0) -> tuple[dict[str, Any], int]:
        """
        A trailing tail is allowed; returns (dict, new position).
        """

@final
class Incomplete:
    """
    The result of parse() when data is missing: falsy.
    """
    def __bool__(self, /) -> bool: ...
    def __repr__(self, /) -> str: ...
    @property
    def needed(self, /) -> int:
        """
        Minimum bytes missing beyond the end of the buffer (a lower bound).
        """

def compile(fields: Iterable[tuple[str |None, str, dict[str, Any]]], *, byteorder: str = "big", max_default: int = 67108864, max_count: int = 16777216) -> Codec:
    """
    Compile a schema in the documented `(name, kind, opts)` form.
    
    This is `rustruct.compile`. The parsing it does is generated from the
    kind table in `parse.rs`, so the set of options a kind accepts and the
    code that reads them come out of one declaration.
    """

def vocabulary() -> dict[str, list[str]]:
    """
    Every closed set the Rust side owns, keyed by what it names.
    
    Without this the drift check only runs one way: `tests/test_vocabulary.py`
    can prove that every name Python knows is accepted, but a name that
    exists only in Rust stays invisible to Python and simply goes unused.
    """
