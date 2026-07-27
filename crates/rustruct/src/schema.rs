use crate::program::IntPrim;

/// Compiler input IR. The py-crate builds it from Python tuples;
/// core tests build it from literals.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ByteOrder {
    Big,
    Little,
}

#[derive(Debug, Clone)]
pub struct FieldIn {
    pub name: Option<String>,
    pub ty: TypeIn,
}

impl FieldIn {
    pub fn named(name: &str, ty: TypeIn) -> FieldIn {
        FieldIn {
            name: Some(name.to_string()),
            ty,
        }
    }
    pub fn anon(ty: TypeIn) -> FieldIn {
        FieldIn { name: None, ty }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BinOp {
    Add,
    Sub,
    Mul,
    Div,
    Shl,
    Shr,
    And,
    Or,
    Xor,
    Eq,
    Ne,
    Lt,
    Le,
    Gt,
    Ge,
}

#[derive(Debug, Clone)]
pub enum ExprIn {
    Imm(i64),
    Greedy,
    Ref(String),
    Bin(BinOp, Box<ExprIn>, Box<ExprIn>),
}

#[derive(Debug, Clone, Default)]
pub struct CrcOverrides {
    pub poly: Option<u64>,
    pub init: Option<u64>,
    pub xorout: Option<u64>,
    pub refin: Option<bool>,
    pub refout: Option<bool>,
}

impl CrcOverrides {
    pub fn any(&self) -> bool {
        self.poly.is_some()
            || self.init.is_some()
            || self.xorout.is_some()
            || self.refin.is_some()
            || self.refout.is_some()
    }
}

#[derive(Debug, Clone)]
pub enum OverIn {
    Star,
    Names(Vec<String>),
}

#[derive(Debug, Clone)]
pub enum TypeIn {
    Int {
        prim: IntPrim,
        byteorder: Option<ByteOrder>,
        const_: Option<i128>,
    },
    Float {
        is64: bool,
        byteorder: Option<ByteOrder>,
    },
    Bool {
        const_: Option<bool>,
    },
    Raw {
        len: Option<usize>,
        const_: Option<Vec<u8>>,
    },
    Bytes {
        len: ExprIn,
        max: Option<usize>,
    },
    StrT {
        len: ExprIn,
        max: Option<usize>,
        encoding: String,
        errors: String,
    },
    CStrT {
        max: Option<usize>,
        encoding: String,
        errors: String,
    },
    Bits {
        width: u8,
        signed: bool,
    },
    FlagsT {
        base: IntPrim,
        byteorder: Option<ByteOrder>,
        names: Vec<(String, u64)>,
        rest: String,
    },
    DigestT {
        algo: String,
        overrides: CrcOverrides,
        over: OverIn,
        verify: bool,
    },
    StructT {
        fields: Vec<FieldIn>,
        byteorder: Option<ByteOrder>,
        size: Option<ExprIn>,
    },
    ArrayT {
        elem: Box<TypeIn>,
        count: Option<ExprIn>,
        until_eof: bool,
    },
    SwitchT {
        on: ExprIn,
        cases: Vec<(i64, TypeIn)>,
        default: Option<Box<TypeIn>>,
    },
    CondT {
        pred: ExprIn,
        then: Box<TypeIn>,
    },
}
