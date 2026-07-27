# Extension API

The supported Python extension points are declarative:

- {py:func}`rustruct.convert` transforms one decoded value;
- {py:func}`rustruct.switch` selects a declared wire shape;
- {py:func}`rustruct.registry` creates a reusable case registry;
- {py:func}`rustruct.compile` builds generated mapping-based schemas.

There is no public cursor or custom field-codec base in this release. See
{doc}`/how-to/extend-schema` for practical extension
patterns and {doc}`/explanation/extension-model` for the design boundary.
