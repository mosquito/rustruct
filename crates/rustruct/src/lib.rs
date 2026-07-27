//! rustruct-core: parsing and building binary wire formats.
//! The core knows nothing about Python: the py-crate converts Value <-> PyObject.

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
