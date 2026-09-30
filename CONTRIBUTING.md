# Contributing to Dextra

Thanks for your interest in improving Dextra!  This document explains how to
set up a development environment, run the test suite, and submit changes.

## Development setup

Dextra is implemented in Python 3.10+ and uses `llvmlite` for LLVM IR
generation and `gcc` (or `clang`) for the final link step.

```bash
git clone <your-fork-url> dextra
cd dextra
pip install -e ".[dev]"          # installs dextra + pytest
```

Verify everything works:

```bash
python -m pytest                 # 96 tests, ~1 second
dextra --version                 # Dextra 0.2.0
dextra run examples/hello.dx     # Hello, Dextra!
```

## Project layout

See [`docs/architecture.md`](docs/architecture.md) for the full breakdown.
The short version:

```
src/dextra/         compiler source
├── lexer/          tokens, source spans, lexer
├── parser/         recursive-descent parser
├── ast/            AST node dataclasses
├── semantic/       symbols, scopes, name resolution, type checking
├── types/          type system (Primitive, Array, Struct, Function)
├── ir/             Dextra IR (Module/Function/BasicBlock/Instruction)
├── codegen/        LLVM backend (llvmlite) + native linker
├── runtime/        C runtime (strings, arrays, printing)
├── diagnostics/    error codes and Rust-style rendering
├── formatter/       dextra fmt
└── cli/            dextra command-line interface
tests/              unit + golden + negative + e2e tests
examples/           .dx programs demonstrating each feature
docs/               language spec, grammar, architecture, type system, etc.
```

## Running tests

```bash
# All tests
python -m pytest

# Unit tests only (no LLVM involvement)
python -m pytest tests/test_lexer.py tests/test_parser.py tests/test_semantic.py tests/test_ir.py

# End-to-end (compile .dx → run binary → compare stdout)
python -m pytest tests/test_integration.py

# Verbosity
python -m pytest -v
```

## Adding a new language feature

Follow the pipeline top-to-bottom.  Do not skip layers.

1. **Spec** — update `docs/language-spec.md` and `docs/grammar.md`.
2. **Lexer** — add a new keyword or operator in `lexer/token.py` and `lexer/lexer.py`.
3. **Parser** — add a parsing rule in `parser/parser.py`.
4. **AST** — add a new node in `ast/nodes.py` if needed.
5. **Semantic** — extend `semantic/analyzer.py` to resolve symbols and check types.
6. **Type system** — add the new type in `types/type_system.py` if needed.
7. **IR** — add the lowering rule in `ir/lowering.py` and any new IR instruction in `ir/dextra_ir.py`.
8. **LLVM backend** — extend `codegen/llvm_backend.py` to lower the new IR instruction.
9. **Runtime** — add C functions in `runtime/runtime.c` if needed.
10. **Tests** — add unit tests for each layer; add a golden test under `tests/integration/programs/`.
11. **Example** — add a `.dx` file under `examples/`.
12. **Docs** — update `CHANGELOG.md`.

## Diagnostic codes

Error codes live in `diagnostics/diagnostics.py`.  Use the next free code in
the appropriate range:

| Range      | Category                |
|------------|-------------------------|
| E0001      | Lexical                 |
| E0002-E0009 | Syntactic              |
| E0100-E0199 | Semantic               |
| E0200-E0299 | Type errors            |
| E0300-E0399 | Mutability             |
| E0400-E0499 | Control flow           |
| E0999      | Internal compiler error |

## Git workflow

Use meaningful commit messages following the existing style:

```
feat(lexer): add multi-line string literals
test(parser): add coverage for nested struct literals
fix(codegen): handle string globals across functions
docs: document the for-range loop in the language spec
```

Avoid `update`, `fix`, `stuff`, `final`, `final2`.

## Definition of done

A feature is complete only when:

- [ ] Specification exists
- [ ] Implementation exists
- [ ] Unit tests exist
- [ ] Integration / golden tests exist
- [ ] Error cases are tested
- [ ] Documentation exists
- [ ] Example exists where useful
- [ ] Compiler pipeline supports it end-to-end
- [ ] Generated LLVM IR is valid
- [ ] Native execution works
