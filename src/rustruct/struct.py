"""The declarative frontend: a metaclass that reads class-body annotations
and field descriptors (rustruct.fields), lazily compiles a rustruct.Codec on
first use, and converts between typed Struct instances and the plain dict
Codec.pack()/unpack() deal with.

byteorder is an inherited class kwarg. An open dispatch registry is a class
kwarg pair: `registry=True` on the base, one arbitrary-named kwarg (its
value is the tag) on each concrete subclass. Registration is eager, but a
registry only freezes into a fixed cases tuple on first schema resolution,
so subclasses imported after the base -- but before first use -- are still
visible.

Resolution (building fields_tuple/Shapes, the Codec, and specialized
__init__/to_mapping/from_mapping methods compiled as AST, never
source-string templating) is per-class and lazy, triggered by first use.
`StructMeta.__new__` installs a small trampoline into every class's own
`__dict__` so resolving an ancestor class never leaks its compiled methods
onto an unresolved subclass.
"""

import ast
import inspect
import typing
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast

from .core import compile as compile_codec
from .errors import SchemaError
from .fields import MISSING, FieldSpec, Registry
from .scalars import ScalarType
from .vocab import ByteOrder, KindArg


class FromMapping(Protocol):
    """The compiled/trampoline from_mapping signature: `mapping` always,
    `ctx` only when a nested Shape needs it. A plain `Callable[[dict, tuple],
    Struct]` can't express that second parameter's default, hence this
    Protocol instead."""

    def __call__(self, mapping: dict[str, Any], ctx: tuple = ()) -> "Struct": ...


class Shape:
    """Converts a decoded wire value to/from its Python-facing value for one
    field. `ctx` is a tuple of enclosing frames, outermost first: a raw wire
    dict per frame during unpack, the Struct instance per frame during pack.
    Only SwitchShape looks past the innermost frame, since a switch's `on=`
    can live in an enclosing scope (e.g. IPv4's `protocol` driving dispatch
    inside its `body` window)."""

    def to_python(self, value, ctx):
        return value

    def to_wire(self, value, ctx):
        return value


SCALAR_SHAPE = Shape()


class StructShape(Shape):
    __slots__ = ("cls",)

    def __init__(self, cls):
        self.cls = cls

    def to_python(self, value, ctx):
        return self.cls.from_mapping(value, ctx)

    def to_wire(self, value, ctx):
        return value.to_mapping(ctx) if isinstance(value, Struct) else value


class ArrayShape(Shape):
    __slots__ = ("elem",)

    def __init__(self, elem):
        self.elem = elem

    def to_python(self, value, ctx):
        return [self.elem.to_python(v, ctx) for v in value]

    def to_wire(self, value, ctx):
        return [self.elem.to_wire(v, ctx) for v in value]


class ConvertShape(Shape):
    """Wraps a plain scalar/bytes shape with a value-level transcoder, e.g.
    u32 <-> ipaddress.IPv4Address."""

    __slots__ = ("decode", "encode")

    def __init__(self, decode, encode):
        self.decode = decode
        self.encode = encode

    def to_python(self, value, ctx):
        return self.decode(value)

    def to_wire(self, value, ctx):
        return self.encode(value)


def lookup_in_chain(ctx, name):
    for frame in reversed(ctx):
        if isinstance(frame, dict):
            if name in frame:
                return frame[name]
        elif hasattr(frame, name):
            return getattr(frame, name)
    raise KeyError(name)


class SwitchShape(Shape):
    """`on_field` is the sibling/ancestor field name driving dispatch, when
    `on=` is a bare ref. A non-ref `on=` expression can't be re-evaluated
    here, so values pass through unconverted (SCALAR_SHAPE)."""

    __slots__ = ("on_field", "cases", "default")

    def __init__(self, on_field, cases, default):
        self.on_field = on_field
        # Read-only: this dispatch table is fixed once the class resolves,
        # so nothing can add/remove a case through this Shape afterward.
        self.cases = MappingProxyType(dict(cases))
        self.default = default

    def pick(self, ctx):
        if self.on_field is None:
            return self.default or SCALAR_SHAPE
        try:
            tag = lookup_in_chain(ctx, self.on_field)
        except KeyError:
            return self.default or SCALAR_SHAPE
        return self.cases.get(tag, self.default or SCALAR_SHAPE)

    def to_python(self, value, ctx):
        return self.pick(ctx).to_python(value, ctx)

    def to_wire(self, value, ctx):
        return self.pick(ctx).to_wire(value, ctx)


def type_spec(value):
    """Resolve any of {ScalarType subclass, Struct subclass, FieldSpec, raw
    (kind, opts) tuple} to a (kind, opts, Shape) triple. Struct subclasses
    resolve through the target class's own lazy, cached resolution, so
    reused nested types are only ever resolved once."""
    if isinstance(value, FieldSpec):
        return resolve_field_spec(value)
    if isinstance(value, type) and issubclass(value, ScalarType):
        return value.kind, {}, SCALAR_SHAPE
    if isinstance(value, type) and issubclass(value, Struct):
        resolved = ensure_resolved(value)
        opts = {"fields": resolved.fields_tuple, "byteorder": value.byteorder}
        return "struct", opts, StructShape(value)
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        return value[0], dict(value[1]), SCALAR_SHAPE
    raise TypeError(f"cannot resolve a wire type for {value!r}")


def resolve_cases(cases_src):
    if isinstance(cases_src, Registry):
        pairs = cases_src.items()
    elif isinstance(cases_src, dict):
        pairs = cases_src.items()
    else:
        pairs = list(cases_src)
    cases_opts = []
    cases_shapes = {}
    for tag, val in pairs:
        kind, opts, shape = type_spec(val)
        cases_opts.append((tag, (kind, opts)))
        cases_shapes[tag] = shape
    return tuple(cases_opts), cases_shapes


def resolve_field_spec(spec):
    if spec.kind is None:
        raise TypeError(
            "a kind-less FieldSpec (described()) can't be resolved on its own; it must pair with a typed annotation"
        )
    match spec.kind:
        case "array":
            elem_kind, elem_opts, elem_shape = type_spec(spec.opts["elem"])
            opts = dict(spec.opts)
            opts["elem"] = (elem_kind, elem_opts)
            # An array of plain scalars needs no per-element conversion;
            # collapsing to SCALAR_SHAPE lets compiled methods pass the
            # core's list straight through instead of copying element-wise.
            shape = SCALAR_SHAPE if elem_shape is SCALAR_SHAPE else ArrayShape(elem_shape)
            return "array", opts, shape
        case "switch":
            cases_opts, cases_shapes = resolve_cases(spec.opts["cases"])
            opts = {"on": spec.opts["on"], "cases": cases_opts}
            default_shape = None
            if spec.opts.get("default") is not None:
                default_kind, default_opts, default_shape = type_spec(spec.opts["default"])
                opts["default"] = (default_kind, default_opts)
            on = spec.opts["on"]
            on_field = on[1] if isinstance(on, tuple) and on[0] == "ref" else None
            return "switch", opts, SwitchShape(on_field, cases_shapes, default_shape)
        case "struct":
            struct_cls = spec.opts["struct"]
            resolved = ensure_resolved(struct_cls)
            opts = {k: v for k, v in spec.opts.items() if k != "struct"}
            opts["fields"] = resolved.fields_tuple
            opts["byteorder"] = struct_cls.byteorder
            return "struct", opts, StructShape(struct_cls)
        case "convert":
            base_kind, base_opts, _base_shape = type_spec(spec.opts["base"])
            return base_kind, base_opts, ConvertShape(spec.opts["decode"], spec.opts["encode"])
        case "cond":
            # then_shape is reused as-is: to_python/to_wire for this field
            # only run when the key is present, guarded by from_mapping's
            # "if name in mapping" check and to_mapping's own None-check.
            then_kind, then_opts, then_shape = type_spec(spec.opts["then"])
            opts = {"pred": spec.opts["pred"], "then": (then_kind, then_opts)}
            return "cond", opts, then_shape
        case _:
            return spec.kind, dict(spec.opts), SCALAR_SHAPE


class FieldDecl:
    """One class-body declaration -- name plus its raw annotation/FieldSpec,
    default, and help -- before `resolve_field_spec` turns it into a wire
    kind/opts/Shape. Not to be confused with `Field`, the (name, kind, opts)
    NamedTuple this eventually compiles down to."""

    __slots__ = ("name", "value_spec", "default", "default_factory", "help")

    def __init__(self, name, value_spec, default, default_factory, help):  # noqa: A002
        self.name = name
        self.value_spec = value_spec
        self.default = default
        self.default_factory = default_factory
        self.help = help


class Field(typing.NamedTuple):
    """One entry of `resolved.fields_tuple`, matching rustruct.compile()'s
    expected shape (core.pyi's `Field`) -- a real NamedTuple so a call site
    reads as `Field(name=..., kind=..., opts=...)`. Still a plain tuple at
    the C level (pyo3's downcast is structural), so the core side needs no
    changes to accept it.

    `kind` is `KindArg`, not bare `Kind`: the low-level form is documented
    as taking plain strings and the whole test suite, the doc snippets and
    the benchmark all pass them, so the annotation has to admit both."""

    name: str | None
    kind: KindArg
    opts: dict


def build_wire_fields(cls, namespace, bases):
    # inspect.get_annotations(), not namespace["__annotations__"]: PEP 649
    # (default since 3.14) computes annotations lazily via a hidden
    # __annotate__, absent from the class-body namespace at __new__ time.
    # get_annotations() handles both eager (<3.14) and lazy (3.14+) cases
    # uniformly, and returns only this class's own annotations.
    own = {}
    for name, annotation in inspect.get_annotations(cls).items():
        if typing.get_origin(annotation) is typing.ClassVar:
            continue
        if name.startswith("__") and name.endswith("__"):
            continue
        raw = namespace.get(name, MISSING)
        if isinstance(raw, FieldSpec):
            spec = raw
            value_spec = annotation if spec.kind is None else spec
            default, default_factory, help_ = spec.default, spec.default_factory, spec.help
        else:
            value_spec = annotation
            default = raw
            default_factory = MISSING
            help_ = None
        own[name] = FieldDecl(name, value_spec, default, default_factory, help_)

    # Each base already carries its own fully-accumulated wire_fields (this
    # same merge ran when it was built), so a flat walk of direct bases --
    # not the whole MRO -- is enough. A name inherited from a base and then
    # redeclared here overrides that base's FieldDecl but keeps its
    # original wire position, exactly like dataclass field inheritance.
    fields = {}
    for base in bases:
        for f in getattr(base, "wire_fields", ()):
            fields[f.name] = f
    fields.update(own)
    return tuple(fields.values())


class Resolved:
    """A class's own compiled schema, cached once on `cls.resolved_cache`
    (see `ensure_resolved`) and never touched again -- every container
    here is a read-only snapshot (`MappingProxyType`/`frozenset`/`tuple`),
    not a live mutable structure some other code could reach through the
    class and corrupt out from under every future pack()/unpack() call."""

    __slots__ = ("fields_tuple", "shapes", "no_input_names", "needs_ctx")

    def __init__(self, fields_tuple, shapes, no_input_names, needs_ctx):
        self.fields_tuple = fields_tuple
        self.shapes = MappingProxyType(dict(shapes))
        self.no_input_names = frozenset(no_input_names)
        self.needs_ctx = needs_ctx


def shape_needs_ctx(shape):
    """Whether converting a value through `shape` could ever need to look
    past its immediate frame (only a bare-ref SwitchShape does, or something
    nested that does). Lets the common switch-free case skip building the
    ctx tuple, avoiding a per-call allocation."""
    if isinstance(shape, SwitchShape):
        return shape.on_field is not None
    if isinstance(shape, StructShape):
        return ensure_resolved(shape.cls).needs_ctx
    if isinstance(shape, ArrayShape):
        return shape_needs_ctx(shape.elem)
    return False


def collect_refs(value, into):
    """Walk an Expr-shaped tuple ("*"/int/("ref", name)/("add", a, b)/...)
    and collect every ("ref", name) target it mentions."""
    if isinstance(value, tuple):
        if len(value) == 2 and value[0] == "ref" and isinstance(value[1], str):
            into.add(value[1])
        else:
            for item in value:
                collect_refs(item, into)


def ast_load(name):
    return ast.Name(id=name, ctx=LOAD, **POSITION)


def ast_call(func, args):
    return ast.Call(func=func, args=args, keywords=[], **POSITION)


def ast_bind(namespace, prefix, value):
    """Put `value` into the compiled function's globals under a fresh name
    and return a Name node loading it -- how generated code references
    runtime objects (shapes, defaults, other classes' compiled methods).

    Fresh because the counter is the namespace's own size, so every class
    must build all of its methods against one dict. Handing each method its
    own and merging afterwards silently aliased a field's default onto
    another's, since the counters restarted."""
    name = f"{prefix}{len(namespace)}"
    assert name not in namespace, f"generated name {name!r} bound twice"
    namespace[name] = value
    return ast_load(name)


def ast_subscript(owner, key):
    return ast.Subscript(value=ast_load(owner), slice=ast.Constant(value=key, **POSITION), ctx=LOAD, **POSITION)


def ast_subscript_store(owner, key):
    return ast.Subscript(value=ast_load(owner), slice=ast.Constant(value=key, **POSITION), ctx=STORE, **POSITION)


def ast_self_dict(ctx):
    return ast.Attribute(value=ast_load("self"), attr="__dict__", ctx=ctx, **POSITION)


def ast_ctx_push(frame):
    """ctx = (*ctx, <frame>)"""
    return ast.Assign(
        targets=[ast.Name(id="ctx", ctx=STORE, **POSITION)],
        value=ast.Tuple(
            elts=[ast.Starred(value=ast_load("ctx"), ctx=LOAD, **POSITION), ast_load(frame)],
            ctx=LOAD,
            **POSITION,
        ),
        **POSITION,
    )


def ast_signature(posargs, kwonlyargs=(), kw_defaults=(), defaults=()):
    return ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg=a, **POSITION) for a in posargs],
        vararg=None,
        kwonlyargs=[ast.arg(arg=a, **POSITION) for a in kwonlyargs],
        kw_defaults=list(kw_defaults),
        kwarg=None,
        defaults=list(defaults),
    )


# Every generated node shares one position, because the code it stands for
# has no source to point at. `compile()` insists on having one, and the
# alternative -- `ast.fix_missing_locations` -- walks the whole tree in
# Python afterwards to fill them in, which costs more than compiling it.
#
# Start only: `compile()` does not ask for `end_lineno`/`end_col_offset`,
# and carrying them made every node a third more expensive to build. They
# would only ever have underlined a caret in a traceback, and everything
# here already claims to be at line 1.
POSITION: dict[str, Any] = {"lineno": 1, "col_offset": 0}

# One each, shared by every node in every tree. A context is a marker: the
# AST grammar gives `expr_context` no fields at all, so `_fields` and
# `_attributes` are both empty and there is nothing in one to write, and
# nothing to race on -- `compile()` only ever reads the tree.
#
# This is also what CPython itself does: every tree `ast.parse` returns
# shares a single `Load` across all of its nodes, and the same one between
# trees. Building a fresh context per node cost a hundred allocations a
# class for nothing.
LOAD = ast.Load()
STORE = ast.Store()


def ast_function(name, args, body):
    """One FunctionDef built as an AST (never via source-string templating)."""
    fn = ast.FunctionDef(name=name, args=args, body=body, decorator_list=[], **POSITION)
    # type_params: a FunctionDef field from PEP 695 (3.12+), required by
    # compile() on 3.12+ and simply unused before that. setattr(), since
    # typeshed's FunctionDef stub only declares this field for a 3.12+
    # target and this project's floor is 3.11.
    setattr(fn, "type_params", [])  # noqa: B010
    return fn


def compile_functions(cls, defs, namespace):
    """Compile a class's generated methods together, and return them.

    One `compile()` call rather than one per method: the call has a fixed
    cost that dwarfs bodies this small, and every class needs three. They
    share one globals dict, which is also the one `ast_bind` counted
    against while building them -- see there for why that matters.
    """
    module = ast.Module(body=defs, type_ignores=[])
    exec(compile(module, f"<rustruct:{cls.__name__}>", "exec"), namespace)
    return [namespace[fn.name] for fn in defs]


def compile_init(fields, no_input_names, namespace):
    """Generate a real __init__ with a genuine keyword-only signature, so
    CPython's own call-binding machinery (fast, C-level) handles required-
    vs-optional and rejects unknown keywords, instead of a Python-level loop
    popping from **kwargs and branching on default/default_factory/
    no_input_names per field per call."""
    if not fields:
        return ast_function("__init__", ast_signature(["self"]), [ast.Pass(**POSITION)])

    namespace["_MISSING"] = MISSING
    kwonly, kw_defaults, body = [], [], []
    for f in fields:
        kwonly.append(f.name)
        if f.default_factory is not MISSING:
            factory = ast_bind(namespace, "_factory", f.default_factory)
            kw_defaults.append(ast_load("_MISSING"))
            value = ast.IfExp(
                test=ast.Compare(
                    left=ast_load(f.name), ops=[ast.Is()], comparators=[ast_load("_MISSING")], **POSITION
                ),
                body=ast_call(factory, []),
                orelse=ast_load(f.name),
                **POSITION,
            )
        elif f.default is not MISSING:
            kw_defaults.append(ast_bind(namespace, "_default", f.default))
            value = ast_load(f.name)
        elif f.name in no_input_names:
            # derived/const: pack() always recomputes/overwrites this, so
            # there's nothing meaningful to require here.
            kw_defaults.append(ast.Constant(value=None, **POSITION))
            value = ast_load(f.name)
        else:
            kw_defaults.append(None)  # keyword-only with no default: required
            value = ast_load(f.name)
        body.append(
            ast.Assign(
                targets=[ast.Attribute(value=ast_load("self"), attr=f.name, ctx=STORE, **POSITION)],
                value=value,
                **POSITION,
            )
        )
    args = ast_signature(["self"], kwonlyargs=kwonly, kw_defaults=kw_defaults)
    return ast_function("__init__", args, body)


def emit_to_python(shape, expr, namespace, depth=0):
    """Return an AST expression converting wire-value `expr` to its Python
    value, inlining the common shape combinations so the per-element hot
    path (arrays of nested structs, arrays of converts) runs with zero
    Shape-object dispatch. Falls back to a generic `.to_python()` call for
    anything else (switches). `expr` is a zero-arg factory producing a
    fresh node per use -- AST nodes must not be shared between positions."""
    match shape:
        case _ if shape is SCALAR_SHAPE:
            return expr()
        case StructShape(cls=cls):
            # The target class is fixed by the schema (no polymorphism on
            # unpack) and is already resolved -- ensure_resolved() ran on it
            # while this class's own fields were being resolved -- so this
            # binds its *compiled* from_mapping directly.
            fm = ast_bind(namespace, "_fm", cls.from_mapping)
            return ast_call(fm, [expr(), ast_load("ctx")])
        case ConvertShape(decode=decode):
            return ast_call(ast_bind(namespace, "_dec", decode), [expr()])
        case ArrayShape(elem=elem):
            var = f"v{depth}"
            inner = emit_to_python(elem, lambda: ast_load(var), namespace, depth + 1)
            return ast.ListComp(
                elt=inner,
                generators=[
                    ast.comprehension(target=ast.Name(id=var, ctx=STORE, **POSITION), iter=expr(), ifs=[], is_async=0)
                ],
                **POSITION,
            )
        case _:
            sh = ast_bind(namespace, "_sh", shape)
            return ast_call(ast.Attribute(value=sh, attr="to_python", ctx=LOAD, **POSITION), [expr(), ast_load("ctx")])


def emit_to_wire(shape, expr, namespace, depth=0):
    """The pack-direction twin of emit_to_python. Nested struct values
    dispatch through the value's own to_mapping (not a schema-bound
    function): the runtime value may be a *subclass* of the declared elem
    class, and raw dicts are passed through unconverted, exactly as
    StructShape.to_wire always did."""
    match shape:
        case _ if shape is SCALAR_SHAPE:
            return expr()
        case StructShape():
            # <expr>.to_mapping(ctx) if isinstance(<expr>, Struct) else <expr>
            return ast.IfExp(
                test=ast_call(ast_load("isinstance"), [expr(), ast_load("_Struct")]),
                body=ast_call(ast.Attribute(value=expr(), attr="to_mapping", ctx=LOAD, **POSITION), [ast_load("ctx")]),
                orelse=expr(),
                **POSITION,
            )
        case ConvertShape(encode=encode):
            return ast_call(ast_bind(namespace, "_enc", encode), [expr()])
        case ArrayShape(elem=elem):
            var = f"v{depth}"
            inner = emit_to_wire(elem, lambda: ast_load(var), namespace, depth + 1)
            return ast.ListComp(
                elt=inner,
                generators=[
                    ast.comprehension(target=ast.Name(id=var, ctx=STORE, **POSITION), iter=expr(), ifs=[], is_async=0)
                ],
                **POSITION,
            )
        case _:
            sh = ast_bind(namespace, "_sh", shape)
            return ast_call(ast.Attribute(value=sh, attr="to_wire", ctx=LOAD, **POSITION), [expr(), ast_load("ctx")])


def compile_to_mapping(fields, no_input_names, shapes, needs_ctx, cond_names, namespace):
    namespace["_Struct"] = Struct
    args = ast_signature(["self", "ctx"], defaults=[ast.Tuple(elts=[], ctx=LOAD, **POSITION)])
    if not no_input_names and not cond_names and all(shapes[f.name] is SCALAR_SHAPE for f in fields):
        # All-scalar, nothing omitted: one C-level dict copy of the
        # instance dict beats a per-key literal (the core ignores any
        # stray extra keys on pack, verified).
        body = [ast.Return(value=ast_call(ast_load("dict"), [ast_self_dict(ast.Load())]), **POSITION)]
        return ast_function("to_mapping", args, body)
    body = []
    if needs_ctx:
        # Skipping the ctx append when nothing below could ever look past
        # this frame avoids a tuple allocation on every single call -- the
        # common case for e.g. an array of plain-scalar structs (see
        # shape_needs_ctx).
        body.append(ast_ctx_push("self"))
    body.append(
        ast.Assign(targets=[ast.Name(id="d", ctx=STORE, **POSITION)], value=ast_self_dict(ast.Load()), **POSITION)
    )
    if not cond_names:
        # No conditionally-present field: still one dict literal, just
        # built from statement-free key/value lists (the common case).
        keys, values = [], []
        for f in fields:
            if f.name in no_input_names:
                # Derived/const/digest: pack() always recomputes/overwrites
                # it, so it is omitted.
                continue
            keys.append(ast.Constant(value=f.name, **POSITION))
            values.append(emit_to_wire(shapes[f.name], lambda name=f.name: ast_subscript("d", name), namespace))
        body.append(ast.Return(value=ast.Dict(keys=keys, values=values, **POSITION), **POSITION))
        return ast_function("to_mapping", args, body)
    # At least one `when()` field: its Python value is None exactly when
    # wire-absent (a Shape like ArrayShape/StructShape would crash trying to
    # convert a bare None), so its key is only added when not None.
    body.append(
        ast.Assign(
            targets=[ast.Name(id="out", ctx=STORE, **POSITION)],
            value=ast.Dict(keys=[], values=[], **POSITION),
            **POSITION,
        )
    )
    for f in fields:
        if f.name in no_input_names:
            continue
        value_expr = emit_to_wire(shapes[f.name], lambda name=f.name: ast_subscript("d", name), namespace)
        assign = ast.Assign(targets=[ast_subscript_store("out", f.name)], value=value_expr, **POSITION)
        match f.name in cond_names:
            case True:
                body.append(
                    ast.If(
                        test=ast.Compare(
                            left=ast_subscript("d", f.name),
                            ops=[ast.IsNot()],
                            comparators=[ast.Constant(value=None, **POSITION)],
                            **POSITION,
                        ),
                        body=[assign],
                        orelse=[],
                        **POSITION,
                    )
                )
            case False:
                body.append(assign)
    body.append(ast.Return(value=ast_load("out"), **POSITION))
    return ast_function("to_mapping", args, body)


def compile_from_mapping(cls, fields, shapes, needs_ctx, cond_names, namespace):
    """The mapping is expected to be complete for every non-`when()` field;
    a missing key raises KeyError rather than falling back to a default.
    Built via object.__new__ plus a direct __dict__ assignment, since
    __init__'s parameter binding is pure overhead when every value is
    already known by name. A `when()` field is the one exception: the core
    omits its key when the predicate was false, so it gets its own presence
    check and default."""
    namespace["_cls"] = cls
    namespace["_new"] = object.__new__
    args = ast_signature(["mapping", "ctx"], defaults=[ast.Tuple(elts=[], ctx=LOAD, **POSITION)])
    new_self = ast.Assign(
        targets=[ast.Name(id="self", ctx=STORE, **POSITION)],
        value=ast_call(ast_load("_new"), [ast_load("_cls")]),
        **POSITION,
    )
    if not cond_names and all(shapes[f.name] is SCALAR_SHAPE for f in fields):
        # All-scalar, nothing conditionally absent: adopt the dict the core
        # just built as the instance dict outright. Safe because the core
        # hands back a fresh dict per struct; unsound once a field can be
        # missing on purpose, hence the cond_names branch below.
        body = [
            new_self,
            ast.Assign(targets=[ast_self_dict(ast.Store())], value=ast_load("mapping"), **POSITION),
            ast.Return(value=ast_load("self"), **POSITION),
        ]
        return ast_function("from_mapping", args, body)
    body = []
    if needs_ctx:
        body.append(ast_ctx_push("mapping"))
    body.append(new_self)
    if not cond_names:
        keys, values = [], []
        for f in fields:
            keys.append(ast.Constant(value=f.name, **POSITION))
            values.append(
                emit_to_python(shapes[f.name], lambda name=f.name: ast_subscript("mapping", name), namespace)
            )
        body.append(
            ast.Assign(
                targets=[ast_self_dict(ast.Store())], value=ast.Dict(keys=keys, values=values, **POSITION), **POSITION
            )
        )
        body.append(ast.Return(value=ast_load("self"), **POSITION))
        return ast_function("from_mapping", args, body)
    # At least one `when()` field: build the instance dict incrementally so
    # each one can fall back to its own default when the core's mapping
    # doesn't have that key at all (predicate was false at pack time).
    body.append(
        ast.Assign(
            targets=[ast.Name(id="d", ctx=STORE, **POSITION)],
            value=ast.Dict(keys=[], values=[], **POSITION),
            **POSITION,
        )
    )
    for f in fields:
        value_expr = emit_to_python(shapes[f.name], lambda name=f.name: ast_subscript("mapping", name), namespace)
        present = ast.Assign(targets=[ast_subscript_store("d", f.name)], value=value_expr, **POSITION)
        if f.name in cond_names:
            default_node = ast_bind(namespace, "_default", f.default)
            absent = ast.Assign(targets=[ast_subscript_store("d", f.name)], value=default_node, **POSITION)
            body.append(
                ast.If(
                    test=ast.Compare(
                        left=ast.Constant(value=f.name, **POSITION),
                        ops=[ast.In()],
                        comparators=[ast_load("mapping")],
                        **POSITION,
                    ),
                    body=[present],
                    orelse=[absent],
                    **POSITION,
                )
            )
        else:
            body.append(present)
    body.append(ast.Assign(targets=[ast_self_dict(ast.Store())], value=ast_load("d"), **POSITION))
    body.append(ast.Return(value=ast_load("self"), **POSITION))
    return ast_function("from_mapping", args, body)


def ensure_resolved(cls):
    resolved = cls.__dict__.get("resolved_cache")
    if resolved is not None:
        return resolved
    fields = cls.wire_fields
    fields_tuple = []
    shapes = {}
    referenced = set()
    const_names = set()
    digest_names = set()
    cond_names = set()
    for f in fields:
        kind, opts, shape = type_spec(f.value_spec)
        fields_tuple.append(Field(f.name, kind, opts))
        shapes[f.name] = shape
        if "const" in opts:
            const_names.add(f.name)
        match kind:
            case "digest":
                digest_names.add(f.name)
            case "cond":
                cond_names.add(f.name)
            case "bytes" | "str":
                collect_refs(opts.get("len"), referenced)
            case "array":
                collect_refs(opts.get("count"), referenced)
            case "struct":
                collect_refs(opts.get("size"), referenced)
        # switch's "on" is deliberately NOT collected here: unlike len/count/
        # size, referencing a field from `on=` does not make it derived --
        # the discriminant stays an ordinary, caller-supplied field.
    own_names = {f.name for f in fields}
    # Fields the caller never has to supply: derived (a sibling's len/
    # count/size references them), const (pack() always writes the literal
    # value), or digest (pack() always recomputes it) -- pack() ignores
    # whatever's given for any of these.
    no_input_names = (referenced & own_names) | const_names | digest_names
    # Whether this class's own fields ever need the ancestor-scope ctx chain
    # -- computed after the shapes above are all built, so any nested
    # struct's own needs_ctx (already resolved, recursively) is available.
    needs_ctx = any(shape_needs_ctx(s) for s in shapes.values())
    resolved = Resolved(tuple(fields_tuple), shapes, no_input_names, needs_ctx)
    cls.resolved_cache = resolved

    # Compile specialized methods for this *exact* class and install them
    # directly into cls.__dict__, replacing the per-class trampolines
    # StructMeta.__new__ installed at creation time. Every call after this
    # one, for this class, skips the wire_fields/Shape interpretive loop
    # entirely -- see the module docstring for why this can't just be
    # inherited from an ancestor.
    namespace = {}
    init, to_mapping, from_mapping = compile_functions(
        cls,
        [
            compile_init(fields, no_input_names, namespace),
            compile_to_mapping(fields, no_input_names, shapes, needs_ctx, cond_names, namespace),
            compile_from_mapping(cls, fields, shapes, needs_ctx, cond_names, namespace),
        ],
        namespace,
    )
    cls.__init__ = init
    cls.to_mapping = to_mapping
    cls.from_mapping = staticmethod(from_mapping)

    return resolved


def get_codec(cls):
    codec = cls.__dict__.get("codec_cache")
    if codec is None:
        resolved = ensure_resolved(cls)
        codec = compile_codec(resolved.fields_tuple, byteorder=cls.byteorder)
        cls.codec_cache = codec
    return codec


def make_trampolines(cls):
    """A per-class __init__/to_mapping/from_mapping that resolves+compiles
    (idempotent, cached) on first call and immediately delegates this same
    call to the freshly-compiled version. Installed directly into every
    class's OWN __dict__ at creation time -- see the module docstring."""

    def trampoline_init(self, **kwargs):
        ensure_resolved(cls)
        return cls.__dict__["__init__"](self, **kwargs)

    def trampoline_to_mapping(self, ctx=()):
        ensure_resolved(cls)
        return cls.__dict__["to_mapping"](self, ctx)

    def trampoline_from_mapping(mapping, ctx=()):
        ensure_resolved(cls)
        return cls.__dict__["from_mapping"](mapping, ctx)

    return trampoline_init, trampoline_to_mapping, trampoline_from_mapping


class StructMeta(type):
    def __new__(mcls, name, bases, namespace, *, byteorder=None, registry=False, **kwargs):
        # A metaclass's __new__ can't express "every class this builds is a
        # Struct subclass" in its own signature -- cast once so every
        # attribute set below resolves against Struct's shape, not `type`.
        cls = cast("type[Struct]", super().__new__(mcls, name, bases, namespace))
        if byteorder is not None:
            # Coerced here rather than left for compile(): a bad byteorder
            # is then a SchemaError naming this class, at the `class`
            # statement, instead of one naming nothing at all on some later
            # first pack(). ByteOrder has no NATIVE member, so the core's
            # refusal of it is reproduced by construction -- but keep the
            # core's own wording, which explains why.
            try:
                cls.byteorder = ByteOrder(byteorder)
            except ValueError:
                raise SchemaError(
                    f"{name}: byteorder {byteorder!r} is not supported (only "
                    f'"big"/"little"/"network"; "native" is forbidden, since it '
                    f"makes the wire format depend on the running machine)"
                ) from None
        if registry:
            cls.dispatch_registry = Registry()
            cls.registry_key = None
        elif kwargs:
            if len(kwargs) != 1:
                raise TypeError(f"{name}: expected exactly one registration keyword, got {sorted(kwargs)}")
            ((_, key),) = kwargs.items()
            target = getattr(cls, "dispatch_registry", None)
            if target is None:
                raise TypeError(f"{name}: no registry found on any base class (mark the base with registry=True)")
            target.add(key, cls)
            cls.registry_key = key
        cls.wire_fields = build_wire_fields(cls, namespace, bases)
        cls.resolved_cache = None
        cls.codec_cache = None
        init, to_mapping, from_mapping = make_trampolines(cls)
        cls.__init__ = init
        cls.to_mapping = to_mapping
        cls.from_mapping = staticmethod(from_mapping)
        return cls


class Struct(metaclass=StructMeta):
    byteorder: ClassVar[ByteOrder] = ByteOrder.BIG
    dispatch_registry: ClassVar[Registry | None] = None
    registry_key: ClassVar[Any] = None

    if TYPE_CHECKING:
        # StructMeta.__new__ sets these three unconditionally on every class
        # it builds. Declaring them here never actually executes -- it just
        # gives type checkers a real shape to check subclass constructor
        # calls and pack()/unpack() usage against.
        wire_fields: ClassVar[tuple[FieldDecl, ...]] = ()
        resolved_cache: ClassVar["Resolved | None"] = None
        codec_cache: ClassVar[Any] = None

        def __init__(self, **kwargs: Any) -> None: ...
        def to_mapping(self, ctx: tuple = ()) -> dict[str, Any]: ...

        # ClassVar[FromMapping], not `@staticmethod def`: it's assigned a
        # real `staticmethod(...)` object at runtime (see StructMeta.__new__
        # and ensure_resolved), so its declared type must match that value
        # rather than the auto-unwrapped shape a literal def would get.
        from_mapping: ClassVar[FromMapping]

    def __repr__(self):
        parts = ", ".join(f"{f.name}={getattr(self, f.name)!r}" for f in type(self).wire_fields)
        return f"{type(self).__name__}({parts})"

    def __eq__(self, other):
        if type(self) is not type(other):
            return NotImplemented
        return all(getattr(self, f.name) == getattr(other, f.name) for f in type(self).wire_fields)

    __hash__ = None

    def pack(self):
        return get_codec(type(self)).pack(self.to_mapping())

    def pack_into(self, buf, offset=0):
        return get_codec(type(self)).pack_into(buf, offset, self.to_mapping())

    @classmethod
    def unpack(cls, buf):
        return cls.from_mapping(get_codec(cls).unpack(buf))

    @classmethod
    def unpack_from(cls, buf, offset=0):
        mapping, pos = get_codec(cls).unpack_from(buf, offset)
        return cls.from_mapping(mapping), pos

    @classmethod
    def parse(cls, buf, offset=0):
        result = get_codec(cls).parse(buf, offset)
        if not result:
            return result
        mapping, pos = result
        return cls.from_mapping(mapping), pos
