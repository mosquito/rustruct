//! Closed sets of names: the table `parse` matches on is the table
//! `vocabulary()` publishes, so the two cannot drift apart.
//!
//! Each set used to be a `match` plus a hand-kept list beside it, and they
//! drifted -- `crc32c` and `crc64_xz` were implemented and reachable while
//! being named nowhere a Python reader would look.

/// A set of names the core recognises, and nothing else.
pub struct ClosedSet<T: 'static> {
    /// Names the thing in a message: `"byteorder"`, `"encoding"`.
    pub what: &'static str,
    /// What that message says is allowed instead.
    pub allowed: &'static str,
    /// How a name is compared. [`str::eq`] for every set but encodings,
    /// which take [`loose_eq`] so separators and case do not matter.
    pub eq: fn(&str, &str) -> bool,
    /// One name per value: what `vocabulary()` publishes and a `rustruct.*`
    /// enum mirrors. `"network"` lives here rather than among the aliases
    /// because it *is* a member of `rustruct.ByteOrder`.
    pub names: &'static [(&'static str, T)],
    /// Spellings accepted without being published -- a way of writing a
    /// value rather than a value of its own, so they get no enum member.
    /// Searched only after `names` misses, which is why the common case
    /// touches nothing but the array above.
    pub aliases: &'static [(&'static str, T)],
}

impl<T: Clone> ClosedSet<T> {
    /// The published name of each value, in table order.
    pub fn names(&self) -> impl Iterator<Item = &'static str> + '_ {
        self.names.iter().map(|(name, _)| *name)
    }

    /// Every spelling `parse` accepts, published or not.
    pub fn accepted(&self) -> impl Iterator<Item = &'static str> + '_ {
        self.names()
            .chain(self.aliases.iter().map(|(name, _)| *name))
    }

    /// The value a name stands for, or a message naming what was expected.
    ///
    /// Hands back the message rather than an error type, so the core can
    /// wrap it in a `SchemaError` and the pyo3 crate in a `PyErr` without
    /// either knowing about the other's.
    pub fn parse(&self, s: &str) -> Result<T, String> {
        let hit = |table: &'static [(&'static str, T)]| {
            table
                .iter()
                .find(|(name, _)| (self.eq)(s, name))
                .map(|(_, value)| value.clone())
        };
        hit(self.names)
            .or_else(|| hit(self.aliases))
            .ok_or_else(|| format!("{} {s:?} is not supported ({})", self.what, self.allowed))
    }
}

/// Equal ignoring ASCII case and any `-`/`_`.
///
/// A walk rather than normalizing both sides into `String`s, which cost an
/// allocation per candidate on a path that runs for every schema compiled.
pub fn loose_eq(a: &str, b: &str) -> bool {
    fn key(s: &str) -> impl Iterator<Item = u8> + '_ {
        s.bytes()
            .filter(|c| !matches!(c, b'-' | b'_'))
            .map(|c| c.to_ascii_lowercase())
    }
    key(a).eq(key(b))
}
