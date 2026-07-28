"""Translate Python-ish expressions into the Expr tuples rustruct.compile()
expects: prefix tuples like
("mul", ("sub", ("ref", "ihl"), 5), 4), or a bare int/"*"/("ref", name).

A field descriptor position (len=/count=/on=/size=) accepts, uniformly:
  - an int literal                      -> a constant
  - "*"                                 -> Greedy
  - a plain field-name string           -> shorthand for ("ref", name)
  - a raw Expr tuple                    -> passed through verbatim (escape hatch)
  - a callable taking a FieldNamespace  -> evaluated once, at schema-resolution
                                           time, building the expression via
                                           operator overloading on FieldExpr
"""

from .vocab import BinOp


class FieldExpr:
    """A symbolic placeholder for a sibling field's value, built up via
    ordinary Python arithmetic/comparison operators into an Expr AST. Only
    meant to live for the duration of a single descriptor-building call --
    never stored on an instance or compared for real equality."""

    __slots__ = ("node",)

    def __init__(self, node):
        self.node = node

    def operand(self, other):
        return other.node if isinstance(other, FieldExpr) else other

    def binop(self, op, other):
        return FieldExpr((op, self.node, self.operand(other)))

    def rbinop(self, op, other):
        return FieldExpr((op, self.operand(other), self.node))

    def __add__(self, other):
        return self.binop(BinOp.ADD, other)

    def __radd__(self, other):
        return self.rbinop(BinOp.ADD, other)

    def __sub__(self, other):
        return self.binop(BinOp.SUB, other)

    def __rsub__(self, other):
        return self.rbinop(BinOp.SUB, other)

    def __mul__(self, other):
        return self.binop(BinOp.MUL, other)

    def __rmul__(self, other):
        return self.rbinop(BinOp.MUL, other)

    def __floordiv__(self, other):
        return self.binop(BinOp.DIV, other)

    def __rfloordiv__(self, other):
        return self.rbinop(BinOp.DIV, other)

    def __lshift__(self, other):
        return self.binop(BinOp.SHL, other)

    def __rlshift__(self, other):
        return self.rbinop(BinOp.SHL, other)

    def __rshift__(self, other):
        return self.binop(BinOp.SHR, other)

    def __rrshift__(self, other):
        return self.rbinop(BinOp.SHR, other)

    def __and__(self, other):
        return self.binop(BinOp.AND, other)

    def __rand__(self, other):
        return self.rbinop(BinOp.AND, other)

    def __or__(self, other):
        return self.binop(BinOp.OR, other)

    def __ror__(self, other):
        return self.rbinop(BinOp.OR, other)

    def __xor__(self, other):
        return self.binop(BinOp.XOR, other)

    def __rxor__(self, other):
        return self.rbinop(BinOp.XOR, other)

    def __eq__(self, other):
        return self.binop(BinOp.EQ, other)

    def __ne__(self, other):
        return self.binop(BinOp.NE, other)

    def __lt__(self, other):
        return self.binop(BinOp.LT, other)

    def __le__(self, other):
        return self.binop(BinOp.LE, other)

    def __gt__(self, other):
        return self.binop(BinOp.GT, other)

    def __ge__(self, other):
        return self.binop(BinOp.GE, other)

    __hash__ = None


class FieldNamespace:
    """Attribute access returns a FieldExpr referencing that sibling field.
    Passed as the sole argument to len=/count=/on=/size= callables."""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return FieldExpr(("ref", name))


def resolve_expr_arg(value):
    """Normalize a len=/count=/on=/size= argument into the int/"*"/tuple
    shape rustruct.compile() itself accepts."""
    if callable(value) and not isinstance(value, FieldExpr):
        value = value(FieldNamespace())
    if isinstance(value, FieldExpr):
        return value.node
    if isinstance(value, str):
        return value if value == "*" else ("ref", value)
    if isinstance(value, (int, tuple)):
        return value
    raise TypeError(f"unsupported expression argument: {value!r}")
