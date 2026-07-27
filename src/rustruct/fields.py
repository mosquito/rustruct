"""Field-descriptor factories: the metaclass reads these off the class body
value (if any) to learn a field's wire kind/opts plus its Python-side
construction default and doc string. A field with no descriptor at all just
gets its kind from the annotation (a scalar sentinel or a Struct subclass);
a descriptor is only required for kinds the annotation alone can't express
(dynamic bytes/str/cstr, bits, array, switch) or to attach a default/help.
"""

from types import MappingProxyType

from .expr import resolve_expr_arg

MISSING = object()


class FieldSpec:
    """kind is None for described()-only specs (defaults/help layered onto
    a plain scalar/nested-struct annotation); otherwise the rustruct.compile()
    kind string this field resolves to, with opts is the raw options for it
    (possibly still containing unresolved sub-specs for array/switch)."""

    __slots__ = ("kind", "opts", "default", "default_factory", "help")

    def __init__(self, *, kind=None, opts=None, default=MISSING, default_factory=MISSING, help=None):
        self.kind = kind
        self.opts = MappingProxyType(opts or {})
        self.default = default
        self.default_factory = default_factory
        self.help = help


def described(default=MISSING, *, default_factory=MISSING, help=None):
    """A plain field: wire kind comes from the annotation, this only carries
    a construction default and/or documentation.

    .. code-block:: python

        class Header(Struct):
            version: U8 = described(1, help="wire format version")

        assert Header().pack() == bytes.fromhex("01")
    """
    return FieldSpec(default=default, default_factory=default_factory, help=help)


def convert(base, *, decode, encode, default=MISSING, default_factory=MISSING, help=None):
    """Wrap `base` (a scalar type, or another field spec like raw(4))
    with a value-level transcoder, e.g. u32 <-> ipaddress.IPv4Address.

    .. code-block:: python

        import ipaddress

        class Endpoint(Struct):
            address: object = convert(U32, decode=ipaddress.IPv4Address, encode=int)

        addr = ipaddress.IPv4Address("192.0.2.1")
        assert Endpoint(address=addr).pack() == bytes.fromhex("c0000201")
    """
    opts = {"base": base, "decode": decode, "encode": encode}
    return FieldSpec(kind="convert", opts=opts, default=default, default_factory=default_factory, help=help)


def raw(length, *, const=None, default=MISSING, help=None):
    """A fixed-length `bytes` value: exactly `length` bytes, every time, no
    length prefix and no expression. `const` fixes the value (checked on
    unpack, written on pack) for signatures and magic numbers.

    .. code-block:: python

        class Magic(Struct):
            signature: bytes = raw(4, const=b"RSTC")

        assert Magic().pack() == b"RSTC"
    """
    opts = {"len": length}
    if const is not None:
        opts["const"] = const
    return FieldSpec(kind="raw", opts=opts, default=default, help=help)


def slice(len=None, *, max=None, default=MISSING, help=None):  # noqa: A002
    """A runtime-sized `bytes` value. `len` is an integer constant, `"*"`
    for the rest of the current region, a sibling field name, or an
    expression lambda; referencing a sibling by name derives that sibling
    from the actual data on pack. `max` bounds the allocation regardless of
    what `len` evaluates to."""
    opts = {}
    if len is not None:
        opts["len"] = resolve_expr_arg(len)
    if max is not None:
        opts["max"] = max
    return FieldSpec(kind="bytes", opts=opts, default=default, help=help)


def string(len=None, *, max=None, encoding="utf-8", errors="strict", default=MISSING, help=None):  # noqa: A002
    """A runtime-sized `str` value: the same length rules as `slice()`, but
    the wire bytes are decoded to text on unpack and encoded on pack.
    `len` and `max` count encoded bytes, not Unicode code points."""
    opts = {"encoding": encoding, "errors": errors}
    if len is not None:
        opts["len"] = resolve_expr_arg(len)
    if max is not None:
        opts["max"] = max
    return FieldSpec(kind="str", opts=opts, default=default, help=help)


def cstring(*, max=None, encoding="utf-8", errors="strict", default=MISSING, help=None):
    """A NUL-terminated `str` value: no length field at all, the extent is a
    `\\x00` byte on unpack, and pack writes one after the encoded text.
    Always give `max` when reading untrusted input -- it's the only thing
    stopping a missing terminator from consuming the rest of the buffer."""
    opts = {"encoding": encoding, "errors": errors}
    if max is not None:
        opts["max"] = max
    return FieldSpec(kind="cstr", opts=opts, default=default, help=help)


def sized(struct_cls, size, *, default_factory=MISSING, help=None):
    """A nested struct framed as a window: only `size` bytes are visible to
    it, and it must consume exactly that many. Use this instead of a bare
    Struct-subclass annotation whenever the outer schema needs TLV-style
    framing (a length field bounding the body)."""
    opts = {"struct": struct_cls, "size": resolve_expr_arg(size)}
    return FieldSpec(kind="struct", opts=opts, default_factory=default_factory, help=help)


def bits(width, *, signed=False, const=None, default=MISSING, help=None):
    """One field inside a run of consecutive bit fields, packed
    most-significant-bit-first into whole bytes. Each run (however many
    `bits()` fields appear back to back) must add up to a whole number of
    bytes. `signed=True` reads the field as two's-complement; `const=`
    fixes the value the same way it does for `raw()`."""
    opts = {"width": width, "signed": signed}
    if const is not None:
        opts["const"] = const
    return FieldSpec(kind="bits", opts=opts, default=default, help=help)


def array(elem, *, count=None, until_eof=False, default_factory=list, help=None):
    """A repeated `elem` (a scalar type, another field descriptor, or a
    Struct subclass). `count` is an integer constant, a sibling field
    name, or an expression lambda; referencing a sibling by name derives
    that sibling from `len(...)` on pack. `until_eof=True` instead repeats
    until the current region ends, with a partial trailing element treated
    as invalid rather than dropped."""
    opts = {"elem": elem}
    if until_eof:
        opts["until_eof"] = True
    elif count is not None:
        opts["count"] = resolve_expr_arg(count)
    return FieldSpec(kind="array", opts=opts, default_factory=default_factory, help=help)


def digest(algo, over, *, verify=True, poly=None, init=None, xorout=None, refin=None, refout=None, help=None):  # noqa: A002
    """A checksum/hash computed over `over` (`"*"` for the whole enclosing
    scope, self-zeroing its own bytes, or a tuple of sibling field names)
    and written on pack; verified on unpack unless `verify=False`. The
    caller never supplies this field -- pack() always recomputes it and
    ignores whatever's given, the same as a derived length."""
    opts = {"algo": algo, "over": over, "verify": verify}
    for name, value in (("poly", poly), ("init", init), ("xorout", xorout), ("refin", refin), ("refout", refout)):
        if value is not None:
            opts[name] = value
    return FieldSpec(kind="digest", opts=opts, help=help)


def when(pred, then, *, default=None, help=None):
    """A conditionally-present field: `then` (any type_spec -- a scalar
    type, another field descriptor, a Struct subclass, ...) is decoded/encoded
    only when `pred` (evaluated against already-decoded sibling fields) is
    truthy. When it isn't, the field is entirely absent from the wire --
    zero bytes, not a null -- and absent from the decoded mapping;
    `default` (`None` unless given) is its Python-side value in that case."""
    opts = {"pred": resolve_expr_arg(pred), "then": then}
    return FieldSpec(kind="cond", opts=opts, default=default, help=help)


def switch(on, cases, *, default=None, help=None):
    """`cases`: a dict {tag: type_spec}, a Registry, or an iterable of
    (tag, type_spec) pairs. `default`, if given, is a type_spec applied to
    unmatched tags; a common choice is slice(len="*") for a raw,
    round-trippable fallback."""
    opts = {"on": resolve_expr_arg(on), "cases": cases, "default": default}
    return FieldSpec(kind="switch", opts=opts, help=help)


class Registry:
    """An open, mutable dispatch table: a registry-base Struct subclass owns
    one of these, and concrete subclasses register into it via a class kwarg
    (`class TCP(IPPayload, key=IPProtocol.TCP): ...`). Reading `items()` is
    deferred to first compile, so subclasses imported after the base -- but
    before the schema is first used -- are still picked up.

    Freezes the moment anything reads it, since a schema that already
    switched on this registry captured a fixed snapshot a later `add()`
    could never retroactively update. `add()` after that point is a loud,
    immediate error instead of a silent no-op; `entries` itself becomes a
    frozen `tuple` at the same moment."""

    __slots__ = ("entries", "frozen")

    def __init__(self):
        self.entries = []
        self.frozen = False

    @property
    def is_frozen(self):
        return self.frozen

    def add(self, tag, value):
        if self.frozen:
            raise TypeError(
                "cannot register into this Registry: it is already frozen "
                "(some schema switching on it was already compiled -- every "
                "subclass must be registered before the first pack()/unpack()/"
                "construction of anything that uses this registry)"
            )
        # self.frozen guarantees entries is still the original list at this
        # point (it only ever becomes a tuple in items(), together with
        # setting frozen=True) -- spelled out as an isinstance check so a
        # static type checker can see it too, not just at runtime.
        entries = self.entries
        assert isinstance(entries, list)
        entries.append((tag, value))

    def items(self):
        if not self.frozen:
            self.entries = tuple(self.entries)
            self.frozen = True
        return self.entries


def registry():
    """Create a new, empty `Registry` for a `switch()`'s `cases`. Concrete
    subclasses of a `class Base(Struct, registry=True)` add themselves to
    `Base.dispatch_registry` automatically -- this factory exists for the
    rare case of building or extending a dispatch table by hand instead."""
    return Registry()
