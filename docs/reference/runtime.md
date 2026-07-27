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

`PackError` and `InvalidDataError` set `kind`/`path`/`offset` on the raised
instance rather than exposing them as class attributes, so they're
documented by hand below rather than through autodoc introspection.

```{eval-rst}
.. autoexception:: rustruct.RustructError
.. autoexception:: rustruct.SchemaError

.. autoexception:: rustruct.PackError

   .. py:attribute:: kind
      :type: str

      Machine-readable failure category, e.g. ``"range"`` or
      ``"inconsistent"``.

   .. py:attribute:: path
      :type: str

      Dotted field path where the failure occurred.

.. autoexception:: rustruct.InvalidDataError

   .. py:attribute:: kind
      :type: str

      Machine-readable failure category, e.g. ``"checksum"`` or
      ``"tag"``.

   .. py:attribute:: path
      :type: str

      Dotted field path where the failure occurred.

   .. py:attribute:: offset
      :type: int

      Byte offset in the input where the failure was detected.
```
