# Runtime API

For incremental parsing in context, see {doc}`/tutorials/try-partial-input`.
For mapping-based operation examples, see {doc}`/how-to/compile-mapping-schemas`.
{py:func}`rustruct.compile` produces a {py:class}`rustruct.Codec`; a
{py:class}`rustruct.Struct` subclass compiles and holds one internally.

## Compilation and codec

```{eval-rst}
.. autofunction:: rustruct.compile

.. autoclass:: rustruct.Codec
   :members: pack, pack_into, unpack, unpack_from, parse, min_size, static_size
```

## Streaming result

```{eval-rst}
.. autoclass:: rustruct.Incomplete
   :members:
```

`Incomplete` is falsy and exposes `needed`.

## Errors

`PackError` and `InvalidDataError` carry `kind` and `path`, and
`InvalidDataError` also carries `offset`.

```{eval-rst}
.. autoexception:: rustruct.RustructError
.. autoexception:: rustruct.SchemaError
.. autoexception:: rustruct.PackError
   :members:
.. autoexception:: rustruct.InvalidDataError
   :members:
```
