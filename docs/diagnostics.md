# Dextra Diagnostics

## 1. Diagnostic Structure

Every diagnostic carries:

```python
@dataclass
class Diagnostic:
    code: ErrorCode            # e.g. E0200
    severity: Severity         # Error | Warning | Note | Help
    message: str
    span: SourceSpan           # may be synthetic for ICEs
    notes: list[str] = []
    help: str | None = None
```

## 2. Severity Levels

| Severity | Meaning |
|----------|---------|
| `Error`   | Compilation cannot proceed past this stage. |
| `Warning` | Compilation proceeds; the user should review. |
| `Note`    | Subsidiary info attached to a parent diagnostic. |
| `Help`    | Actionable suggestion. |

The CLI prints all diagnostics in source order. If any `Error` is present, the compiler exits non-zero before reaching LLVM codegen.

## 3. Error Codes

### Lexical (E0001)
| Code  | Meaning |
|-------|---------|
| `E0001` | Lexical error (unterminated string, invalid character, malformed number). |

### Syntactic (E0002–E0009)
| Code  | Meaning |
|-------|---------|
| `E0002` | Unexpected token. |
| `E0003` | Expected expression. |
| `E0004` | Expected `}` (unterminated block). |
| `E0005` | Expected `)` (unterminated call/paren). |
| `E0006` | Expected `]` (unterminated array). |
| `E0007` | Expected `:` in parameter/field/let annotation. |
| `E0008` | Expected `=` in let/assignment. |
| `E0009` | Expected `->` in return type. |

### Semantic (E0100–E0199)
| Code  | Meaning |
|-------|---------|
| `E0100` | Unknown identifier. |
| `E0101` | Duplicate declaration in the same scope. |
| `E0102` | Unknown type. |
| `E0103` | Unknown field. |
| `E0104` | `break`/`continue` outside a loop. |
| `E0105` | Unknown function. |

### Type errors (E0200–E0299)
| Code  | Meaning |
|-------|---------|
| `E0200` | Type mismatch. |
| `E0201` | Invalid assignment (type mismatch on assignment). |
| `E0202` | Invalid function call (arity or argument type). |
| `E0203` | Condition must be `Bool`. |
| `E0204` | Invalid operand type for unary operator. |
| `E0205` | Invalid operand types for binary operator. |
| `E0206` | Index target is not an array. |
| `E0207` | Index must be `Int`. |
| `E0208` | Field access on non-struct. |
| `E0209` | Missing struct field in literal. |
| `E0210` | Unknown struct field in literal. |
| `E0211` | Duplicate struct field in literal. |
| `E0212` | Array literal has heterogeneous element types. |
| `E0213` | Empty array literal without annotation. |
| `E0214` | `return` expression type does not match function return type. |
| `E0215` | `return` with expression in a `Void` function. |
| `E0216` | `return` without expression in a non-`Void` function. |
| `E0217` | Mismatched explicit type annotation on `let`. |

### Mutability (E0300–E0399)
| Code  | Meaning |
|-------|---------|
| `E0300` | Unknown field. (legacy alias of `E0103`) |
| `E0301` | Assignment to immutable value. |
| `E0302` | Assignment to immutable field (struct field mutability). |

### Return / control flow (E0400–E0499)
| Code  | Meaning |
|-------|---------|
| `E0400` | Invalid `return` (e.g. at top level). |
| `E0401` | `main` must return `Int`. |

### Internal (E0999)
| Code  | Meaning |
|-------|---------|
| `E0999` | Internal compiler error — please file a bug. |

### Warnings (W-prefixed)

| Code  | Meaning |
|-------|---------|
| `W0001` | Division or modulo by literal zero — undefined behavior. |

## 4. Diagnostic Formatting

Diagnostics are rendered in a Rust-style layout:

```
error[E0200]: type mismatch: expected `Int`, found `String`

  --> main.dx:2:13
   |
 2 | let x: Int = "hello"
   |              ^^^^^^^
   |
help: this `String` literal cannot be assigned to an `Int` binding
```

The `-->` line uses `<filename>:<line>:<column>` of the span's start. The `|` gutter shows the source line with the offending range underlined with `^`.

## 5. Recovery Strategy

* The lexer skips one character on an unknown character and emits `E0001`.
* The parser uses a synchronization set: on an unexpected token, it emits `E0002`, then advances until it reaches a token in the sync set (`}`, `;`, `fn`, `struct`, `let`, `if`, `while`, `for`, `return`, EOF).
* Semantic analysis collects all errors before failing; it does not abort on the first error.
* Type checking follows the same collect-don't-abort policy.
* LLVM codegen is reached only if no `Error` diagnostics remain.
