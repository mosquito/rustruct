use crate::error::Kind;
use crate::program::Reg;

/// Postfix expression program. All arithmetic is i64.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Ins {
    Imm(i64),
    Reg(Reg),
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

#[derive(Debug, Clone, PartialEq)]
pub struct Expr(pub Vec<Ins>);

pub const EXPR_STACK: usize = 8;

impl Expr {
    pub fn imm(n: i64) -> Expr {
        Expr(vec![Ins::Imm(n)])
    }

    /// The constant value, if the expression is a single Imm (after
    /// constant folding in compile this covers all static lengths).
    pub fn as_const(&self) -> Option<i64> {
        match self.0.as_slice() {
            [Ins::Imm(n)] => Some(*n),
            _ => None,
        }
    }

    /// Evaluation; `get` reads a register (Reg{up,idx} from the top of the
    /// frame stack).
    pub fn eval(&self, get: &mut dyn FnMut(Reg) -> Result<i64, Kind>) -> Result<i64, Kind> {
        let mut stack = [0i64; EXPR_STACK];
        let mut sp = 0usize;
        for ins in &self.0 {
            match ins {
                Ins::Imm(n) => {
                    stack[sp] = *n;
                    sp += 1;
                }
                Ins::Reg(r) => {
                    stack[sp] = get(*r)?;
                    sp += 1;
                }
                op => {
                    let b = stack[sp - 1];
                    let a = stack[sp - 2];
                    sp -= 2;
                    let v = match op {
                        Ins::Add => a.checked_add(b).ok_or(Kind::Overflow)?,
                        Ins::Sub => a.checked_sub(b).ok_or(Kind::Overflow)?,
                        Ins::Mul => a.checked_mul(b).ok_or(Kind::Overflow)?,
                        Ins::Div => {
                            if b == 0 {
                                return Err(Kind::DivZero);
                            }
                            a.checked_div(b).ok_or(Kind::Overflow)?
                        }
                        Ins::Shl => {
                            if !(0..64).contains(&b) {
                                return Err(Kind::Overflow);
                            }
                            a.checked_shl(b as u32).ok_or(Kind::Overflow)?
                        }
                        Ins::Shr => {
                            if !(0..64).contains(&b) {
                                return Err(Kind::Overflow);
                            }
                            a >> b
                        }
                        Ins::And => a & b,
                        Ins::Or => a | b,
                        Ins::Xor => a ^ b,
                        Ins::Eq => (a == b) as i64,
                        Ins::Ne => (a != b) as i64,
                        Ins::Lt => (a < b) as i64,
                        Ins::Le => (a <= b) as i64,
                        Ins::Gt => (a > b) as i64,
                        Ins::Ge => (a >= b) as i64,
                        _ => unreachable!(),
                    };
                    stack[sp] = v;
                    sp += 1;
                }
            }
        }
        debug_assert_eq!(sp, 1);
        Ok(stack[0])
    }
}
