# Dextra Compiler Architecture

## 1. Pipeline Overview

```
              Dextra Source (.dx)
                    │
                    ▼
                 Lexer
                    │  tokens
                    ▼
                 Parser
                    │  AST
                    ▼
            Semantic Analysis
                    │  resolved AST (symbols attached)
                    ▼
              Type Checker
                    │  typed AST (types attached)
                    ▼
                Dextra IR
                    │  CFG of BasicBlocks/Instructions
                    ▼
              LLVM Backend
                    │  LLVM IR (llvmlite.ir)
                    ▼
                LLVM IR (text)
                    │
                    ▼
              Object Code (llvmlite.binding)
                    │
                    ▼
             Link (gcc) → Native Binary
```

The diagram separates responsibilities conceptually. Name resolution and type
checking run together in `semantic/analyzer.py`. IR lowering consumes the resulting
`AnalyzedProgram`, including its type context and declaration metadata.

## 2. Stage Contracts

### 2.1 Lexer

**Input:**  raw source string + filename.
**Output:** `list[Token]` ending with an `EOF` token. Errors are emitted as `Diagnostic` objects with `kind=Error` and code `E0001`.
**Invariants:**
* Every `Token` carries a `SourceSpan` with `filename`, `line`, `column`, `start_offset`, `end_offset`.
* Comments and whitespace are not produced as tokens — they are consumed and discarded.
* The lexer never throws raw Python exceptions on bad input; it emits a `Diagnostic` and recovers by skipping the offending character.

### 2.2 Parser

**Input:**  `list[Token]`.
**Output:** `Program` AST root.
**Invariants:**
* The parser does not perform type checking or name resolution.
* The parser produces structured diagnostics (`E0002`, `E0003`) with source spans on unexpected tokens.
* Every AST node carries a `SourceSpan`.
* The parser uses recursive descent with explicit precedence levels; no parser generators are used.

### 2.3 Semantic Analysis

**Input:**  `Program` AST.
**Output:** `AnalyzedProgram` containing the AST, `TypeContext`, global `Scope`,
and struct, function, and enum metadata. Resolved symbols and types are attached
as `_resolved` and `_type`; diagnostics go to `DiagnosticEngine`.
**Invariants:**
* Every identifier that survives this stage is bound to a declaration.
* Unknown identifiers are reported as `E0100`.
* Duplicate declarations in the same scope are reported as `E0101`.
* Scopes are lexical and nest by block.

### 2.4 Type Checker

Type checking runs within `Analyzer.analyze()`, not a separate pass or
`TypedProgram` class. Expression types are attached before returning
`AnalyzedProgram`.
**Invariants:**
* Every expression that survives this stage has a non-`Unknown` type.
* Type mismatches are reported as `E0200`.
* Invalid assignments (immutable target, type mismatch) are reported as `E0201` or `E0301`.
* Invalid function calls (wrong arity, wrong argument types) are reported as `E0202`.
* Conditions in `if`/`while`/`for` headers must be `Bool` — `E0203`.

### 2.5 Dextra IR

**Input:**  `AnalyzedProgram`.
**Output:** `ir.Module` — a list of `ir.Function`s, each containing `ir.BasicBlock`s of `ir.Instruction`s.
**Invariants:**
* Every function has a unique `entry` block.
* Every block ends with a terminator (`Return`, `Branch`, or `CondBranch`).
* Every `Value` has a `Type`.
* The IR is in SSA form for temporaries; locals are explicit `Alloca`+`Load`/`Store` (matches LLVM lowering).

### 2.6 LLVM Backend

**Input:**  `ir.Module`.
**Output:** `llvmlite.ir.Module` — a textual LLVM IR module.
**Invariants:**
* Every Dextra IR instruction maps to one or more LLVM instructions.
* External runtime functions (`dx_string_*`, `dx_print_*`, `dx_array_*`, `dx_alloc_struct`) are declared as LLVM `declare`s.
* `main` is lowered to a C-compatible `i64 @main()` entry point.
* LLVM target/asm-printer initialization is **centralized** in
  `src/dextra/codegen/llvm_runtime.py`. No other module calls
  `llvmlite.binding.initialize*` directly — all callers go through
  `ensure_llvm_initialized()` / `parse_and_verify()` /
  `make_target_machine()` / `emit_object_from_ir_text()`.
* `ensure_llvm_initialized()` calls only `llvm.initialize_native_target()`
  and `llvm.initialize_native_asmprinter()`. It does **NOT** call the
  deprecated `llvm.initialize()` (LLVM core init) — modern llvmlite
  (≥0.42) auto-initializes the LLVM core lazily on first use.  The two
  `initialize_native_*` calls are still required: without
  `initialize_native_target()`, `Target.from_default_triple()` raises
  `RuntimeError: Unable to find target for this triple (no targets are
  registered)`; without `initialize_native_asmprinter()`,
  `TargetMachine.emit_object()` raises `RuntimeError: TargetMachine
  can't emit a file of this type`.
* Each `LLVMBackend` instance creates its own `ir.Context()` so that
  repeated compilations in the same Python process don't collide on
  identified type names like `struct.User`.

### 2.6.1 IR Verification

After lowering, the Dextra IR is verified by `dextra.ir.verifier.verify_module`
before LLVM codegen. This catches structural bugs that LLVM's verifier
doesn't know about:

* duplicate function names,
* missing terminators on basic blocks,
* branches that target blocks not in the same function,
* undefined `Temp` / `Local` references,
* `Temp` defined more than once.

LLVM's own verifier (`ModuleRef.verify()`) is then run after parsing the
textual IR, before object emission. Both verifications are hard correctness
boundaries — a failure here is a compiler bug, not a user error.

### 2.7 Native Code Emission

**Input:**  textual LLVM IR.
**Output:** native executable.
**Pipeline:**
1. `parse_and_verify()` (from `llvm_runtime.py`) parses the IR and runs
   LLVM's own verifier.
2. `emit_object_from_ir_text()` invokes `TargetMachine.emit_object()`
   to produce a `.o` file.
3. The runtime C file is compiled with `gcc -c`.
4. `gcc` links the two object files into a final executable.

If LLVM verification fails, the build exits with code 2
(infrastructure/compiler-internal failure) and the bad IR is dumped to
`<output>.bad.ll` for inspection.

## 3. Module Layout

```
src/dextra/
├── __init__.py
├── __main__.py            # entry: python -m dextra
├── pipeline.py            # compile_source / build_from_file driver
├── diagnostics/
│   └── diagnostics.py     # Diagnostic, DiagnosticEngine, ErrorCode
├── lexer/
│   ├── source.py          # SourceFile, SourcePosition, SourceSpan
│   ├── token.py           # Token, TokenKind
│   └── lexer.py           # Lexer
├── parser/
│   └── parser.py          # Parser (recursive descent)
├── ast/
│   └── nodes.py           # AST node dataclasses
├── semantic/
│   ├── symbols.py         # Symbol, SymbolKind
│   ├── scope.py           # Scope (lexical chain)
│   └── analyzer.py        # semantic analysis + type checking
├── types/
│   └── type_system.py     # Type hierarchy + TypeContext
├── ir/
│   ├── dextra_ir.py       # Module/Function/BasicBlock/Instruction
│   ├── lowering.py        # typed AST → Dextra IR
│   └── verifier.py        # IR invariant checks (new in Phase 1.1)
├── codegen/
│   ├── llvm_backend.py    # Dextra IR → llvmlite.ir.Module
│   ├── llvm_runtime.py    # Centralized LLVM init + parse_and_verify (new)
│   └── native.py           # object emission + linker
├── runtime/
│   └── runtime.c          # dx_* runtime functions
├── modules/
│   └── __init__.py        # placeholder package; no module resolver implemented
├── formatter/
│   └── formatter.py       # dextra fmt
└── cli/
    └── main.py            # CLI command dispatch
```

## 4. Diagnostic Engine

The diagnostic engine (see `docs/diagnostics.md`) is shared across all stages. Each stage reports diagnostics into a `DiagnosticEngine`; the CLI aggregates and prints them at the end of the run. A run with any `Error`-level diagnostic exits non-zero before reaching code generation.

## 5. Runtime

The Dextra runtime (`runtime/runtime.c`) provides:

* `DxString` — a length-prefixed UTF-8 string struct.
* `dx_string_new`, `dx_string_concat`, `dx_string_print`, `dx_string_println`, `dx_string_length`.
* `dx_print_int`, `dx_print_float`, `dx_print_bool`, `dx_println_int`, etc.
* `DxArray` — a length-prefixed, type-erased array.
* `dx_array_new(n)`, `dx_array_length`, `dx_array_get`, `dx_array_set`.
* `dx_string_eq` — length-aware byte comparison.
* `dx_alloc_struct` — heap allocation for struct literals.

The runtime is deliberately minimal. Memory is allocated with `malloc` and never freed in v0.2.0; this is documented in `docs/language-spec.md`.

## 6. Testing Strategy

* **Unit tests:** per subsystem (`tests/test_lexer.py`, `test_parser.py`, …). Fast, no LLVM involvement.
* **Golden tests:** `tests/integration/programs/*.dx` paired with `*.expected` stdout.
* **Negative tests:** `tests/integration/programs/bad_*.dx` paired with `*.expected_error` containing the expected error code.
* **End-to-end:** compile → run binary → compare stdout.

See `docs/getting-started.md` for how to run the suite.

## 7. Roadmap

| Phase | Status | Notes |
|-------|--------|-------|
| 0 — Specification       | done   | this document + sibling docs |
| 1 — Lexer               | done   | full token coverage |
| 2 — Parser              | done   | recursive descent, including enums and match |
| 3 — Semantic analysis   | done   | symbols + scopes + name resolution |
| 4 — Type system         | done   | inference + checking |
| 5 — Dextra IR           | done   | Module/Function/BasicBlock/Instruction |
| 6 — LLVM backend        | done   | via llvmlite.ir |
| 7 — Native execution    | done   | llvmlite.binding + gcc link |
| 8 — Runtime             | done   | strings, arrays, print |
| 9 — Standard library    | partial| builtin print/println/length |
| 10 — CLI                | done   | build/run/check/emit-ir/fmt/new |
| 11 — Modules            | planned| `import`/`export` reserved |
| 12 — Formatter          | done   | deterministic, opinionated |
| 13 — Advanced features  | partial| unit enums and match implemented; payload variants, methods, closures planned |
