//! rustruct-core: parsing and building binary wire formats.
//! The core knows nothing about Python: the py-crate converts Value <-> PyObject.

/// A closed set of names, as one table.
///
/// Generates a namespace holding three things that cannot disagree: `ALL`,
/// the names `vocabulary()` publishes and a `rustruct.*` enum mirrors;
/// `ACCEPTED`, every spelling `parse` takes; and `parse` itself. Without
/// this each set grew a match and a separate list, and the two drifted --
/// `crc32c` and `crc64_xz` were implemented and reachable while being
/// named nowhere a Python reader would look.
///
/// A name after `|` is accepted but not published: a spelling of a value
/// rather than a value of its own, so it gets no enum member. `"network"`
/// is written as an entry of its own instead, because it *is* a member of
/// `rustruct.ByteOrder` -- one member per value, except where a second
/// spelling documents intent the way `struct`'s `!` does.
///
/// `normalize` puts both sides through a function before comparing, so a
/// table can spell its aliases the way someone would actually write them
/// rather than in whatever form the normalizer leaves behind.
///
/// `parse` returns the message rather than an error type: the core wraps it
/// in a `SchemaError` and the pyo3 crate in a `PyErr`, and neither has to
/// know about the other's.
#[macro_export]
macro_rules! closed_set {
    ($(#[$m:meta])* $name:ident, $ty:ty, $what:literal, $accepted:literal,
     normalize = $norm:expr,
     [$($val:expr => $canon:literal $(| $alias:literal)*),+ $(,)?]) => {
        $crate::closed_set!(@build $(#[$m])* $name, $ty, $what, $accepted,
            |a: &str, b: &str| { let f = $norm; f(a) == f(b) },
            [$($val => $canon $(| $alias)*),+]);
    };
    ($(#[$m:meta])* $name:ident, $ty:ty, $what:literal, $accepted:literal,
     [$($val:expr => $canon:literal $(| $alias:literal)*),+ $(,)?]) => {
        $crate::closed_set!(@build $(#[$m])* $name, $ty, $what, $accepted,
            |a: &str, b: &str| a == b,
            [$($val => $canon $(| $alias)*),+]);
    };
    (@build $(#[$m:meta])* $name:ident, $ty:ty, $what:literal, $accepted:literal,
     $eq:expr, [$($val:expr => $canon:literal $(| $alias:literal)*),+ $(,)?]) => {
        $(#[$m])*
        pub struct $name;

        impl $name {
            /// The names this set publishes -- one per value.
            pub const ALL: &'static [&'static str] = &[$($canon),+];

            /// Every spelling `parse` accepts, published or not.
            pub const ACCEPTED: &'static [&'static str] = &[$($canon $(, $alias)*),+];

            /// The value a name stands for, or a message naming what was
            /// expected.
            pub fn parse(s: &str) -> Result<$ty, String> {
                let eq = $eq;
                $(if eq(s, $canon) $(|| eq(s, $alias))* {
                    return Ok($val);
                })+
                Err(format!("{} {:?} is not supported ({})", $what, s, $accepted))
            }
        }
    };
}

pub mod compile;
pub mod digest;
pub mod error;
pub mod expr;
pub mod model;
pub mod pack;
pub mod program;
pub mod schema;
pub mod unpack;
pub mod value;

pub use compile::{compile, Options};
pub use error::{Kind, SchemaError};
pub use pack::{run as pack, run_into as pack_into, PackOutcome};
pub use program::Program;
pub use schema::{ByteOrder, CrcOverrides, ExprIn, FieldIn, OverIn, TypeIn};
pub use unpack::{run as unpack, Outcome};
pub use value::Value;
