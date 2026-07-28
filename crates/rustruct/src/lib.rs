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
     normalize,
     [$($val:expr => $canon:literal $(| $alias:literal)*),+ $(,)?]) => {
        $crate::closed_set!(@decl $(#[$m])* $name, $ty, $what, $accepted,
            [$($canon $(, $alias)*),+], [$($canon),+]);

        impl $name {
            pub fn parse(s: &str) -> Result<$ty, String> {
                // A scan rather than a `match`, because the comparison is
                // not string equality. `loose_eq` allocates nothing, so
                // this stays cheaper than normalizing into a String and
                // matching on that.
                $(if $crate::loose_eq(s, $canon) $(|| $crate::loose_eq(s, $alias))* {
                    return Ok($val);
                })+
                Err(Self::unknown(s))
            }
        }
    };
    ($(#[$m:meta])* $name:ident, $ty:ty, $what:literal, $accepted:literal,
     [$($val:expr => $canon:literal $(| $alias:literal)*),+ $(,)?]) => {
        $crate::closed_set!(@decl $(#[$m])* $name, $ty, $what, $accepted,
            [$($canon $(, $alias)*),+], [$($canon),+]);

        impl $name {
            pub fn parse(s: &str) -> Result<$ty, String> {
                match s {
                    $($canon $(| $alias)* => Ok($val),)+
                    _ => Err(Self::unknown(s)),
                }
            }
        }
    };
    (@decl $(#[$m:meta])* $name:ident, $ty:ty, $what:literal, $accepted:literal,
     [$($every:literal),+], [$($pub_name:literal),+]) => {
        $(#[$m])*
        pub struct $name;

        impl $name {
            /// The names this set publishes -- one per value.
            pub const ALL: &'static [&'static str] = &[$($pub_name),+];

            /// Every spelling `parse` accepts, published or not.
            pub const ACCEPTED: &'static [&'static str] = &[$($every),+];

            fn unknown(s: &str) -> String {
                format!("{} {:?} is not supported ({})", $what, s, $accepted)
            }
        }
    };
}

/// Equal ignoring ASCII case and any `-`/`_`.
///
/// What `closed_set!(normalize)` compares with. Written as a walk rather
/// than `to_ascii_lowercase().replace(...)` on both sides because that
/// allocated two `String`s per candidate, and the sets it runs over are
/// small enough that the scan is the cheap part.
#[doc(hidden)]
pub fn loose_eq(a: &str, b: &str) -> bool {
    let mut x = a
        .bytes()
        .filter(|c| *c != b'-' && *c != b'_')
        .map(|c| c.to_ascii_lowercase());
    let mut y = b
        .bytes()
        .filter(|c| *c != b'-' && *c != b'_')
        .map(|c| c.to_ascii_lowercase());
    loop {
        match (x.next(), y.next()) {
            (None, None) => return true,
            (p, q) if p == q => continue,
            _ => return false,
        }
    }
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
