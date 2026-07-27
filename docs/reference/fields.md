# Fields and declarations

Signatures and docstrings only -- for a table view of the same vocabulary
alongside the value it produces and its wire extent, see
{doc}`schema-language`. For selection and composition examples, see the
how-to guides on {doc}`/how-to/choose-field-types` and choosing among field
types, {doc}`/how-to/model-collections` and modeling collections, and
{doc}`/how-to/model-computed-fields` and other computed fields.

## Scalars

```{eval-rst}
.. py:class:: rustruct.I8
.. py:class:: rustruct.I16
.. py:class:: rustruct.I32
.. py:class:: rustruct.I64

   Signed integers of 1, 2, 4, and 8 bytes.

.. py:class:: rustruct.U8
.. py:class:: rustruct.U16
.. py:class:: rustruct.U32
.. py:class:: rustruct.U64

   Unsigned integers of 1, 2, 4, and 8 bytes.

.. py:class:: rustruct.F32
.. py:class:: rustruct.F64

   Four- and eight-byte floating-point values.

.. py:class:: rustruct.Bool

   A one-byte boolean.
```

## Descriptors

```{eval-rst}
.. autofunction:: rustruct.described
.. autofunction:: rustruct.raw
.. autofunction:: rustruct.slice
.. autofunction:: rustruct.string
.. autofunction:: rustruct.cstring
.. autofunction:: rustruct.convert
.. autofunction:: rustruct.bits
.. autofunction:: rustruct.array
.. autofunction:: rustruct.sized
.. autofunction:: rustruct.when
.. autofunction:: rustruct.switch
.. autofunction:: rustruct.digest
.. autofunction:: rustruct.registry
```
