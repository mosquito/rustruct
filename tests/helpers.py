"""Shared helpers for the rustruct test suite."""


def u(name, kind, **opts):
    """Build a Field tuple: (name, kind, opts)."""
    return (name, kind, opts)


def nest_structs(depth):
    """A schema `depth` levels of struct-in-struct deep, around one u8."""
    field = u("leaf", "u8")
    for _ in range(depth):
        field = u("n", "struct", fields=(field,))
    return (field,)


def nest_arrays(depth):
    """A schema `depth` levels of array-of-array deep, around one u8."""
    spec = ("u8", {})
    for _ in range(depth):
        spec = ("array", {"elem": spec, "count": 1})
    return (u("n", spec[0], **spec[1]),)


def nest_expr(depth):
    """A length expression `depth` operators deep, worth 1 either way."""
    e = 1
    for _ in range(depth):
        e = ("add", e, 0)
    return e
