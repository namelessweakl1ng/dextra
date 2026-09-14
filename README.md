# Dextra

A statically typed, compiled programming language built from scratch in Python.
Dextra source code is lexed, parsed, type-checked, lowered through a custom
intermediate representation, compiled to LLVM IR via `llvmlite`, and linked
into a native executable.

```
Dextra Source (.dx)
       │
       ▼
    Lexer ─────► tokens
       │
       ▼
    Parser ────► AST
       │
       ▼
  Semantic Analysis ──► symbols + scopes
       │
       ▼
   Type Checker ──► typed AST
       │
       ▼
   Dextra IR ──► Module / Function / BasicBlock / Instruction
       │
       ▼
   LLVM Backend (llvmlite) ──► LLVM IR
       │
       ▼
   Object Code (llvmlite.binding)
       │
       ▼
   Link (gcc) ──► Native Executable
```

This is not a toy interpreter.  The output of `dextra build hello.dx` is a
genuine native ELF binary produced through the LLVM toolchain.

## Features

- **Statically typed** with local type inference — `let x = 10` infers `Int`.
- **Mutable vs immutable** — `let mut x = 10` vs `let x = 10`; assignment to an
  immutable binding is a compile error (`E0301`).
- **Primitive types** — `Int` (i64), `Float` (double), `Bool` (i1), `String`
  (runtime-managed UTF-8), `Void`.
- **Functions** with parameters, return types, recursion, and lexical scopes.
- **Operators** — `+ - * / %`, `== != < > <= >=`, `&& || !`, unary `- !`.
- **Control flow** — `if` / `else if` / `else`, `while`, `for x in start..end`,
  `for x in array`, `break`, `continue`.
- **Arrays** — `[1, 2, 3]`, indexing, element mutation on `let mut` arrays.
- **Structs** — nominal types with named fields, struct literals, field access.
- **Strings** — escape sequences (`\n \t \\ \" \0`), `+` concatenation.
- **Standard library** — builtin `print`, `println`, `length`.
- **Diagnostics** — Rust-style errors with code, source line, caret underline,
  and help text.
- **Formatter** — `dextra fmt` for deterministic source formatting.
- **Project scaffolding** — `dextra new myproject`.

## Example

```dextra
struct User {
    name: String,
    age: Int
}

fn greet(user: User) -> String {
    return "Hello, " + user.name
}

fn main() -> Int {
    let user = User {
        name: "Dextra",
        age: 1
    }

    println(greet(user))

    return 0
}
```

Compile and run:

```bash
dextra run main.dx
```

Output:

```
Hello, Dextra
```

## Installation

### Prerequisites

- Python 3.10 or newer
- `llvmlite` (`pip install llvmlite`)
- `gcc` (used as the final linker; `clang` also works)

### Install

```bash
pip install -e .
```

Verify:

```bash
dextra --version                # Dextra 0.1.3 (also reports Python + LLVM versions)
dextra run examples/hello.dx    # Hello, Dextra!
```

## Quick start

```bash
dextra new hello-world
cd hello-world
dextra run
```

This scaffolds a project:

```
hello-world/
├── dextra.toml
└── src/
    └── main.dx
```

## CLI reference

```bash
dextra build main.dx -o program      # compile to ./program
dextra run main.dx                    # build and run
dextra check main.dx                  # lex + parse + typecheck, no codegen
dextra emit-ir main.dx -o main.ll     # emit LLVM IR text
dextra lex main.dx                    # print tokens
dextra ast main.dx                    # print the AST
dextra fmt main.dx -i                 # format source in place
dextra new myproject                  # scaffold a new project
dextra clean                          # remove generated artifacts
dextra --version
dextra --help
```

## Language overview

### Variables

```dextra
let x: Int = 10       // explicit
let x = 10             // inferred
let mut y = 20         // mutable
y = 30                 // OK
```

### Functions

```dextra
fn add(a: Int, b: Int) -> Int {
    return a + b
}

fn factorial(n: Int) -> Int {
    if n <= 1 {
        return 1
    }
    return n * factorial(n - 1)
}

fn main() -> Int {
    println(factorial(10))   // 3628800
    return 0
}
```

### Control flow

```dextra
let mut i = 0
while i < 10 {
    println(i)
    i = i + 1
}

for j in 0..10 {
    println(j)
}

for n in [1, 2, 3] {
    println(n)
}
```

### Structs and arrays

```dextra
struct User {
    name: String,
    age: Int
}

fn main() -> Int {
    let u = User { name: "Alice", age: 21 }
    println(u.name)

    let xs = [1, 2, 3, 4, 5]
    let mut total = 0
    for n in xs {
        total = total + n
    }
    println(total)
    return 0
}
```

### Diagnostics

```dextra
fn main() -> Int {
    let x: Int = "hello"
    return x
}
```

produces:

```
error[E0217]: type annotation mismatch: expected `Int`, found `String`

  --> main.dx:2:18
   |
 2 |     let x: Int = "hello"
   |                      ^^^^^^^
   |
   = help: change the annotation to `String` or change the initializer
```

See [`docs/diagnostics.md`](docs/diagnostics.md) for the full error catalog.

## Compiler pipeline

The full pipeline is documented in [`docs/architecture.md`](docs/architecture.md).
Every stage is independently testable, and the test suite exercises each layer
in isolation as well as end-to-end.

| Stage          | Input            | Output                          |
|----------------|------------------|---------------------------------|
| Lexer          | source text      | `list[Token]`                   |
| Parser         | tokens           | `Program` AST                    |
| Semantic       | AST              | resolved AST + symbol table     |
| Type checker   | resolved AST     | typed AST                        |
| IR lowering    | typed AST        | `ir.Module` (CFG of blocks)      |
| LLVM backend   | `ir.Module`      | `llvmlite.ir.Module` (text IR)   |
| Native emission| LLVM IR text     | `.o` object file                 |
| Linker         | `.o` + runtime   | native executable                |

## Testing

```bash
python -m pytest                 # full suite
python -m pytest tests/test_lexer.py
python -m pytest tests/test_integration.py    # golden + negative e2e
```

Tests are split across:

- `tests/test_lexer.py` — token kinds, source positions, error recovery
- `tests/test_parser.py` — grammar, precedence, error reporting
- `tests/test_semantic.py` — symbol resolution, type checking, error codes
- `tests/test_ir.py` — IR lowering, basic blocks, terminators
- `tests/test_codegen.py` — LLVM IR validity, native compilation, execution
- `tests/test_integration.py` — golden tests (`.dx` → run → compare stdout) + negative tests

## Project structure

```
dextra/
├── pyproject.toml
├── README.md  CHANGELOG.md  CONTRIBUTING.md  LICENSE
├── docs/                            specification + architecture docs
├── src/dextra/                      compiler source
│   ├── lexer/  parser/  ast/
│   ├── semantic/  types/  ir/
│   ├── codegen/  runtime/  diagnostics/
│   ├── formatter/  modules/  cli/
│   └── pipeline.py                  top-level compile() driver
├── tests/                           unit + golden + negative + e2e
├── examples/                        13 sample .dx programs
└── benchmarks/                      (planned)
```

## Roadmap

Dextra is at v0.1.  Planned future work:

- Short-circuit evaluation for `&&` / `||`.
- Bounds checking on array access.
- Implicit `Int` → `Float` promotion in mixed arithmetic.
- Module system (`import` / `export` keywords are reserved).
- Enums with payload variants and `match` expressions.
- Methods on structs (`impl`).
- Closures and first-class function values.
- Garbage collection or a simple ownership model.
- Optimization passes (constant folding, dead-code elimination).

See [`CHANGELOG.md`](CHANGELOG.md) for the v0.1 feature set and known limitations.

## Technical details

- **LLVM IR generation** uses [`llvmlite`](https://llvmlite.readthedocs.io/),
  which bundles LLVM.  No external `clang` or `llc` is required for IR
  generation — only `gcc` is needed for the final link step.
- **Type representation** is an interned hierarchy (`PrimitiveType`,
  `ArrayType`, `StructType`, `FunctionType`) so structural equality is identity
  equality throughout the compiler.
- **IR** uses SSA temporaries for expression results and explicit `alloca`+`load`/`store`
  for mutable locals, matching LLVM's lowering model.
- **Runtime** is a single ~120-line C file (`runtime/runtime.c`) providing
  `DxString`, `DxArray`, and `dx_print_*` overloads.  Memory is `malloc`'d
  and never freed in v0.1.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).  PRs that add new language features
must update the spec, grammar, implementation, tests, and an example.

## License

MIT — see [`LICENSE`](LICENSE).
