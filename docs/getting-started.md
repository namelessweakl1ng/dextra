# Getting Started with Dextra

## Prerequisites

* Python 3.10+
* `llvmlite` (`pip install llvmlite`)
* `gcc` (for linking)
* `pytest` (for running tests, optional)

## Installation

From the project root:

```bash
pip install -e .
```

This installs a `dextra` console script.

## Hello, World

Create `hello.dx`:

```dextra
fn main() -> Int {
    println("Hello, Dextra!")
    return 0
}
```

Compile and run:

```bash
dextra run hello.dx
```

Expected output:

```
Hello, Dextra!
```

## Inspecting Intermediate Stages

```bash
dextra lex hello.dx       # print tokens
dextra ast hello.dx       # print AST
dextra check hello.dx     # run type checking, print errors only
dextra emit-ir hello.dx   # write LLVM IR to hello.ll
dextra build hello.dx -o hello   # compile to ./hello
```

## Creating a New Project

```bash
dextra new myproject
cd myproject
dextra run
```

This scaffolds:

```
myproject/
├── dextra.toml
└── src/
    └── main.dx
```

## Running Tests

```bash
python -m pytest tests/
```

End-to-end (golden) tests:

```bash
python -m pytest tests/test_integration.py
```

## Troubleshooting

### `error: LLVM toolchain not found`

Dextra uses `llvmlite` for LLVM IR generation. Install it with `pip install llvmlite`. If you also see errors about `gcc`, install a C toolchain.

### `error[E0001]: unterminated string literal`

Check for a missing closing `"` in a string literal.

### `error[E0200]: type mismatch`

Dextra does no implicit conversions. `let x: Int = 1.5` is an error. Annotate or convert explicitly.

### `error[E0301]: cannot assign to immutable variable`

Use `let mut` instead of `let`.
