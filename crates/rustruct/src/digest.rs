use md5::Digest as _;

/// Rocksoft CRC model.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CrcSpec {
    pub width: u8,
    pub poly: u64,
    pub init: u64,
    pub xorout: u64,
    pub refin: bool,
    pub refout: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Algo {
    Crc(CrcSpec),
    /// RFC 1071 Internet checksum (IPv4 header) — an extra preset
    /// needed for the IPv4 reference format.
    IpChecksum,
    Md5,
    Sha1,
    Sha256,
}

/// A Rocksoft CRC model with `refin`/`refout` tied together, which is how
/// every preset below happens to be specified.
const fn crc(width: u8, poly: u64, init: u64, xorout: u64, refl: bool) -> Algo {
    Algo::Crc(CrcSpec {
        width,
        poly,
        init,
        xorout,
        refin: refl,
        refout: refl,
    })
}

crate::closed_set!(
    /// Every algorithm `digest(algo=...)` accepts.
    Algos, Algo, "digest algorithm",
    "crc16_ccitt/crc16_ibm/crc32/crc32c/crc64_xz/ip/md5/sha1/sha256",
    [
        // CRC-16/IBM-3740 (aka CCITT-FALSE)
        crc(16, 0x1021, 0xFFFF, 0x0000, false) => "crc16_ccitt",
        // CRC-16/ARC (aka IBM)
        crc(16, 0x8005, 0x0000, 0x0000, true) => "crc16_ibm",
        crc(32, 0x04C11DB7, 0xFFFF_FFFF, 0xFFFF_FFFF, true) => "crc32",
        crc(32, 0x1EDC6F41, 0xFFFF_FFFF, 0xFFFF_FFFF, true) => "crc32c",
        crc(64, 0x42F0E1EBA9EA3693, 0xFFFF_FFFF_FFFF_FFFF, 0xFFFF_FFFF_FFFF_FFFF, true)
            => "crc64_xz",
        Algo::IpChecksum => "ip",
        Algo::Md5 => "md5",
        Algo::Sha1 => "sha1",
        Algo::Sha256 => "sha256",
    ]
);

impl Algo {
    /// The preset a name stands for. `Algos::parse` with the message
    /// dropped, kept because every caller here only wants the option.
    pub fn preset(name: &str) -> Option<Algo> {
        Algos::parse(name).ok()
    }

    pub fn width_bytes(&self) -> usize {
        match self {
            Algo::Crc(s) => (s.width as usize) / 8,
            Algo::IpChecksum => 2,
            Algo::Md5 => 16,
            Algo::Sha1 => 20,
            Algo::Sha256 => 32,
        }
    }

    /// CRC/ip → integer in the byteorder context; hashes → bytes.
    pub fn is_int(&self) -> bool {
        matches!(self, Algo::Crc(_) | Algo::IpChecksum)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum DigestVal {
    Int(u64),
    Bytes(Vec<u8>),
}

fn reflect(v: u64, width: u8) -> u64 {
    let mut out = 0u64;
    for i in 0..width {
        if v >> i & 1 == 1 {
            out |= 1 << (width - 1 - i);
        }
    }
    out
}

pub enum Hasher {
    Crc { spec: CrcSpec, state: u64 },
    Ip { sum: u32, odd: Option<u8> },
    Md5(md5::Md5),
    Sha1(sha1::Sha1),
    Sha256(sha2::Sha256),
}

impl Hasher {
    pub fn new(algo: &Algo) -> Hasher {
        match algo {
            Algo::Crc(spec) => Hasher::Crc {
                spec: *spec,
                state: spec.init & mask(spec.width),
            },
            Algo::IpChecksum => Hasher::Ip { sum: 0, odd: None },
            Algo::Md5 => Hasher::Md5(md5::Md5::new()),
            Algo::Sha1 => Hasher::Sha1(sha1::Sha1::new()),
            Algo::Sha256 => Hasher::Sha256(sha2::Sha256::new()),
        }
    }

    pub fn update(&mut self, data: &[u8]) {
        match self {
            Hasher::Crc { spec, state } => {
                // Bit-serial Rocksoft reference implementation: correct for
                // any combination of refin/refout; speed is a question for
                // after benchmarking (not the hot path exercised by tests).
                let m = mask(spec.width);
                for &byte in data {
                    for i in 0..8u8 {
                        let bit_in = if spec.refin {
                            byte >> i & 1
                        } else {
                            byte >> (7 - i) & 1
                        };
                        let top = (*state >> (spec.width - 1)) & 1;
                        *state = (*state << 1) & m;
                        if top ^ (bit_in as u64) == 1 {
                            *state ^= spec.poly & m;
                        }
                    }
                }
            }
            Hasher::Ip { sum, odd } => {
                let mut data = data;
                if let Some(hi) = odd.take() {
                    if let Some((&lo, rest)) = data.split_first() {
                        *sum += u32::from(u16::from_be_bytes([hi, lo]));
                        data = rest;
                    } else {
                        *odd = Some(hi);
                        return;
                    }
                }
                let mut chunks = data.chunks_exact(2);
                for ch in &mut chunks {
                    *sum += u32::from(u16::from_be_bytes([ch[0], ch[1]]));
                }
                if let [last] = chunks.remainder() {
                    *odd = Some(*last);
                }
                // Intermediate fold-in so u32 doesn't overflow.
                while *sum > 0xFFFF {
                    *sum = (*sum & 0xFFFF) + (*sum >> 16);
                }
            }
            Hasher::Md5(h) => h.update(data),
            Hasher::Sha1(h) => h.update(data),
            Hasher::Sha256(h) => h.update(data),
        }
    }

    pub fn update_zeros(&mut self, mut n: usize) {
        const ZEROS: [u8; 64] = [0; 64];
        while n > 0 {
            let take = n.min(64);
            self.update(&ZEROS[..take]);
            n -= take;
        }
    }

    pub fn finalize(self) -> DigestVal {
        match self {
            Hasher::Crc { spec, mut state } => {
                if spec.refout {
                    state = reflect(state, spec.width);
                }
                DigestVal::Int((state ^ spec.xorout) & mask(spec.width))
            }
            Hasher::Ip { mut sum, odd } => {
                if let Some(hi) = odd {
                    sum += u32::from(u16::from_be_bytes([hi, 0]));
                }
                while sum > 0xFFFF {
                    sum = (sum & 0xFFFF) + (sum >> 16);
                }
                DigestVal::Int(u64::from(!(sum as u16)))
            }
            Hasher::Md5(h) => DigestVal::Bytes(h.finalize().to_vec()),
            Hasher::Sha1(h) => DigestVal::Bytes(h.finalize().to_vec()),
            Hasher::Sha256(h) => DigestVal::Bytes(h.finalize().to_vec()),
        }
    }
}

fn mask(width: u8) -> u64 {
    if width >= 64 {
        u64::MAX
    } else {
        (1u64 << width) - 1
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn check(name: &str, expected: u64) {
        let algo = Algo::preset(name).unwrap();
        let mut h = Hasher::new(&algo);
        h.update(b"123456789");
        assert_eq!(h.finalize(), DigestVal::Int(expected), "{name}");
    }

    #[test]
    fn crc_vectors() {
        check("crc16_ccitt", 0x29B1);
        check("crc16_ibm", 0xBB3D);
        check("crc32", 0xCBF43926);
        check("crc32c", 0xE3069283);
        check("crc64_xz", 0x995DC9BBDF1939FA);
    }

    #[test]
    fn crc_split_updates() {
        let algo = Algo::preset("crc32").unwrap();
        let mut h = Hasher::new(&algo);
        h.update(b"1234");
        h.update(b"56789");
        assert_eq!(h.finalize(), DigestVal::Int(0xCBF43926));
    }

    #[test]
    fn ip_checksum_rfc1071() {
        // Example: an IPv4 header with the checksum field zeroed out.
        let hdr: [u8; 20] = [
            0x45, 0x00, 0x00, 0x73, 0x00, 0x00, 0x40, 0x00, 0x40, 0x11, 0x00, 0x00, 0xc0, 0xa8,
            0x00, 0x01, 0xc0, 0xa8, 0x00, 0xc7,
        ];
        let mut h = Hasher::new(&Algo::IpChecksum);
        h.update(&hdr);
        assert_eq!(h.finalize(), DigestVal::Int(0xB861));
    }

    #[test]
    fn sha256_empty() {
        let mut h = Hasher::new(&Algo::Sha256);
        h.update(b"");
        let DigestVal::Bytes(b) = h.finalize() else {
            panic!()
        };
        assert_eq!(
            b[..4],
            [0xe3, 0xb0, 0xc4, 0x42],
            "sha256 of empty input must start with e3b0c442"
        );
    }
}
