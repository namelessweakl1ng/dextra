# Changelog

All notable changes to Dextra are documented in this file.  Versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.0] — Phase 7: Enums and Match

This release adds the first major language feature since v0.1.0: **enums**
and **pattern matching**.  This is a minor version bump because new syntax
is added but existing programs continue to compile.

### Added — Language Features

- **Enum declarations**: `enum Color { Red, Green, Blue }`.  v0.2.0 enums
  are *unit-variant only* (no payload).  Each variant has a stable tag
  (its 0-based index in declaration order).  Enum names live in the
  global scope alongside structs and functions.

- **Enum variant references**: `Color.Red` produces a value of type
  `Color`.  At runtime, this is just the variant's integer tag (an `i64`).
  Type safety is enforced statically — `Color.Purple` is a compile error.

- **Match expressions**: `match scrutinee { Pattern => body, ... }`.
  Patterns are either `EnumName.VariantName` or `_` (wildcard).
  Arms are tried in order; the first match wins.
  Match is an expression (has a value) but can also be used as a
  statement (with `Void` arms).

- **Exhaustiveness checking**: `E0219` if a `match` on an enum doesn't
  cover all variants and has no `_` arm.

- **New diagnostic codes**: `E0219` (non-exhaustive match), `E0220`
  (unknown variant), `E0221` (scrutinee not enum), `E0222` (match arm
  type mismatch), `E0223` (duplicate match arm).

### Added — Architecture

- `FAT_ARROW` token (`=>`) in the lexer for match arm syntax.
- `EnumType` in the type system — enums are nominal types with a
  variant-name-to-tag mapping.
- `EnumDeclaration`, `EnumVariant`, `EnumVariantPattern`,
  `WildcardPattern`, `MatchArm`, `MatchExpression` AST nodes.
- `_lower_match` in the IR lowering — lowers to a chain of CondBranch
  blocks with a result-slot alloca (or no slot for Void matches).
- Enum support in the LLVM backend (`_llvm_type` for `EnumType` → `i64`,
  `_emit_print` for enum printing).
- `get_enum_variant()` helper in the semantic analyzer for downstream
  stages to query if a `FieldAccessExpression` is an enum variant reference.
- Extended the IR lowering error boundary to wrap `lower_program()` in
  addition to `compile_to_llvm_ir()`.
- The parser now allows `MatchExpression` as a bare statement (not just
  `CallExpression`).

### Fixed

- **String literal deduplication removed from IR lowering.** Previously,
  identical string literals within a function were deduplicated to a
  single `StringLit` IR instruction.  This caused SSA dominance
  violations when the same string appeared in multiple match arms
  (sibling blocks).  Fix: always emit a fresh `StringLit` at the IR
  level; string globals are still deduplicated at the LLVM level.

### Tests

- 24 new Phase 7 regression tests in `tests/test_regression_phase7.py`.
- Total test count: 265 (was 241 in v0.1.3).

### Examples

- `examples/enums.dx` — enum declarations, variant references, match.
- `examples/match.dx` — pattern matching with exhaustiveness, wildcard.

## [0.1.3] — Phase 1.3: Type-System / Runtime Representation Correctness Audit

This release fixes three correctness bugs and strengthens the IR verifier.
It does not add new language features.

### Fixed — Correctness

- **Float arrays can now compile and execute correctly.**  Previously,
  `[1.5, 2.5]` was accepted by semantic analysis but the LLVM backend
  raised `RuntimeError: cannot coerce double to i64` when storing the
  element.  Fix: `_coerce_to_i64` now `bitcast`s `double ↔ i64` losslessly
  (both 64 bits, both 8-aligned), and `GetElement` reverses the bitcast
  on read.  No information is lost.

- **Typed empty arrays are now accepted.**  `let xs: [Int] = []` previously
  emitted a confusing double error (`E0213` empty-array-no-type followed by
  `E0217` annotation mismatch).  Fix: the `let` annotation's element type
  is pushed onto a contextual-type stack that the array-literal checker
  consults when it has no elements to infer from.

- **Missing return on a non-Void function is now a compile error (E0218).**
  Previously, `fn f(x: Int) -> Int { if x > 0 { return 1 } }` was silently
  accepted and returned a default value at runtime.  Now the analyzer
  performs proper control-flow analysis: an `if`/`else` exits iff both
  branches exit; a `while true { ... }` exits iff its body exits; a
  `while <non-true> { ... }` is treated as not-exiting (loop may not
  execute).  This is a deliberate semantic promotion (the v0.1 docs
  admitted the old behavior).

### Fixed — Architecture

- **IR verifier now uses dominance analysis**, not just block-list order.
  A `Temp` used in block B is now valid only if defined in B or in a
  block that DOMINATES B.  The old verifier would falsely accept a
  `Temp` defined in a sibling block reachable without going through the
  definition.
- **IR verifier now performs type-consistency checks**: `BinOp` operand
  types must match; `CondBranch.cond` must be `Bool`; `Return.value`
  must match the function's return type; `Call` argument types must
  match the callee's parameter types; `GetElement`/`SetElement` require
  array-typed targets; `GetField`/`SetField` require struct-typed
  targets.  The verifier is invoked with the `TypeContext` so it can
  resolve struct names.
- **Compiler error boundary.**  If semantic analysis accepts a program
  but the LLVM backend raises an exception, `compile_source()` now
  prints `error: Dextra LLVM backend raised <Type>: <message>` and
  returns a `CompileResult(internal_error=True)`.  The CLI then exits
  with code 2 (infrastructure failure) instead of leaking a raw Python
  traceback.

### Added

- New diagnostic code `E0218_MISSING_RETURN`.
- 35+ new Phase 1.3 regression tests in
  `tests/test_regression_phase13.py` covering all required test
  categories (Float arrays, typed empty arrays, return analysis,
  boolean evaluation, IR verifier, type consistency, struct layout,
  runtime safety, compiler error boundary, full type matrix).

### Documented

- Float array storage model: doubles are bitcast to/from `i64` slots.
- Missing-return semantics: hard error, no longer "returns default".
- Boolean evaluation: `&&` and `||` are EAGER (no short-circuit) —
  documented and regression-tested.
- Array index out-of-bounds: undefined behavior in v0.1.3.
- Integer division/modulo by zero: undefined behavior in v0.1.3
  (`W0001` warning only for literal-zero divisors).
- README updated to reflect version 0.1.3 and removed stale test count.

## [0.1.2] — Phase 1.2: LLVM Initialization Compatibility

This release fixes a forward-compatibility bug in LLVM initialization.
It does not add new language features.

### Fixed

- **Removed the deprecated `llvm.initialize()` call** from
  `dextra.codegen.llvm_runtime.ensure_llvm_initialized()`.  Modern
  llvmlite (≥0.42) initializes the LLVM core lazily on first use; the
  explicit `llvm.initialize()` call is unnecessary and is being
  deprecated/removed in upcoming llvmlite versions.
- **Kept the required `llvm.initialize_native_target()` and
  `llvm.initialize_native_asmprinter()` calls.**  These are *not*
  deprecated — they are required to register the host target and
  asm printer.  Without them, `Target.from_default_triple()` raises
  `RuntimeError: Unable to find target for this triple (no targets
  are registered)` and `TargetMachine.emit_object()` raises
  `RuntimeError: TargetMachine can't emit a file of this type`.
- The `ensure_llvm_initialized()` function remains idempotent
  (guarded by a module-level flag + lock).

### Added

- `dextra.codegen.llvm_runtime.llvm_environment_info()` — returns a
  human-readable version string (e.g. `"llvmlite 0.44.0, LLVM 15.0.7"`).
- `dextra --version` now reports Python, llvmlite, and LLVM versions
  on separate lines (custom argparse action — argparse's built-in
  `version` action collapses newlines to spaces).
- 28 new Phase 1.2 regression tests in
  `tests/test_regression_phase12.py` covering:
  * AST-based proof that `llvm.initialize()` is not called,
  * AST-based proof that the two required `initialize_native_*` calls
    ARE present,
  * idempotency of `ensure_llvm_initialized()`,
  * real (non-mocked) end-to-end: minimal main, hello world,
    arithmetic, function call, conditional, loop, recursion, struct,
    array, string concatenation,
  * ELF magic-number check on the emitted object file,
  * CLI `--version` and `dextra run examples/hello.dx`.

### Total test count

159 tests pass (was 131 in v0.1.1).  No tests were weakened or deleted.

## [Unreleased] — Phase 1.1: Audit, Stabilization, LLVM Compatibility

This release is a stabilization pass over v0.1.0.  It does not add new
language features.  Instead, it fixes real correctness bugs, centralizes
LLVM initialization, adds an IR verifier, and reconciles documentation
with the implementation.

### Fixed — Correctness

- **`struct` return from function exposed a dangling stack pointer.**
  `StructLit` was `alloca`'d inside the creating function, so `return u`
  returned a dangling pointer; reading `u.age` from the caller produced
  garbage.  Structs are now **heap-allocated** via a new runtime function
  `dx_alloc_struct`.  Returning a struct from a function is now safe.

- **Arrays of structs and strings crashed on element access.**  `DxArray`
  stored all elements as `i64`; when the element type was a pointer
  (String / Array / Struct), indexing through `arr[i].field` or
  `println(arr[i])` crashed the LLVM backend.  The `GetElement`
  instruction now performs an `inttoptr` from `i64` back to the proper
  pointer type, and `SetElement` does the reverse `ptrtoint`.

- **`continue` in a `for` loop caused an infinite loop.**  The for-loop's
  increment was emitted in the body block's fall-through; `continue`
  jumped directly to the condition block, skipping the increment.  Fixed
  by introducing a separate `for_latch` block that performs the increment;
  `continue` now targets the latch.

- **`==` and `!=` on strings crashed the LLVM backend** with a raw
  `RuntimeError`.  The type system docs already claimed string equality
  was supported via a `strcmp`-based runtime call, but the backend never
  emitted that call.  Added `dx_string_eq` to the runtime and wired up
  the lowering.

- **Field assignment through an array index** (`users[0].age = 99`) crashed
  with an `AssertionError` in the IR lowering.  The lowering was looking
  for a resolved `Symbol` on `users[0]`, but the analyzer doesn't attach
  one to `IndexExpression`s — only to `IdentifierExpression`s.  Fixed by
  reading the type from `get_type()` instead.

- **Division by a literal zero** (`x / 0`) was silently undefined behavior.
  Now emits a `W0001` warning at compile time when the divisor is a
  literal `0` or `0.0`.

### Fixed — Architecture / Tooling

- **LLVM initialization was scattered across 4 files** (pipeline.py,
  cli/main.py, native.py, tests/test_codegen.py) with 9 duplicated
  call sites.  Centralized in `src/dextra/codegen/llvm_runtime.py` —
  `ensure_llvm_initialized()` is now the single owner.  All other
  callers go through `parse_and_verify()` / `make_target_machine()` /
  `emit_object_from_ir_text()`.  The contract is explicit: "LLVM is
  initialized in `dextra.codegen.llvm_runtime`."

- **`LLVMBackend` used the global LLVM `Context`**, causing "already
  defined" errors when the same struct name (e.g. `User`) appeared in two
  successive compilations in the same Python process.  Each backend
  instance now creates its own `ir.Context()`.

- **CLI leaked raw Python tracebacks on missing input files.**  All
  subcommands (`lex`, `ast`, `check`, `emit-ir`, `build`, `run`, `fmt`)
  now print a friendly `error: file not found: ...` message and exit 1.

- **`pipeline.build_from_file` did not verify the LLVM IR.**  Added an
  explicit `parse_and_verify()` step before object emission.  If the IR
  fails LLVM's own verifier, the build exits with code 2 (infrastructure
  failure) and dumps the bad IR to `<output>.bad.ll` for inspection.

### Added

- **IR verifier** (`src/dextra/ir/verifier.py`).  Catches Dextra-specific
  IR invariants before LLVM codegen: duplicate function names, missing
  terminators, branches targeting nonexistent blocks, undefined `Temp` /
  `Local` references, and `Temp` defined more than once.  Wired into
  `compile_source()`.

- **Warning diagnostic severity** (`W0001_DIVISION_BY_ZERO`).
  Warnings are rendered as `warning[W0001]: ...` and do not block
  compilation.

- **Exit code convention documented.**  `0` = success, `1` = user /
  compilation error, `2` = infrastructure / compiler-internal failure
  (LLVM verification, linker failure, missing C compiler).

- **35 new regression tests** (`tests/test_regression_phase11.py`)
  covering every bug fixed in this phase.  Total test count: 131
  (was 96 in v0.1.0).

### Documented

- LLVM type representation table in `docs/type-system.md` §1.1 — explicit
  mapping from Dextra types to LLVM value types and alloca slot types.
- Numeric edge cases in `docs/type-system.md` §8 — division by zero,
  out-of-bounds array access.
- String equality in `docs/type-system.md` §9 — lowered to `dx_string_eq`.
- `W0001` in `docs/diagnostics.md`.
- IR verifier and centralized LLVM init in `docs/architecture.md`.

## [0.1.0] — 2026-09-13

First public release.  Dextra is now a working statically typed compiled
programming language: source code is lexed, parsed, type-checked, lowered
through a custom IR, compiled to LLVM IR via `llvmlite`, and linked into
a native executable.

### Added — Language

- Primitive types: `Int` (`i64`), `Float` (`double`), `Bool` (`i1`), `String` (runtime-managed), `Void`.
- Immutable (`let`) and mutable (`let mut`) bindings with local type inference.
- Functions with parameters, return types, recursion, and nested scopes.
- Arithmetic operators `+ - * / %` on `Int` and `Float`.
- Comparison operators `== != < > <= >=`.
- Boolean operators `&& || !` (no short-circuit evaluation).
- Unary `-` and `!`.
- `if` / `else if` / `else`.
- `while` loops with `break` and `continue`.
- `for x in start..end` range loops and `for x in array` element loops.
- Fixed-length arrays `[T]` with indexing and element mutation.
- Structs with named fields, struct literals, and field access.
- String literals with `\n \t \\ \" \0` escapes and `+` concatenation.
- Builtin `print` / `println` for `Int`, `Float`, `Bool`, `String`.
- Builtin `length(s)` / `length(arr)`.

### Added — Compiler architecture

- Hand-written lexer producing source-span-tagged tokens (`E0001` for lexical errors).
- Recursive-descent parser with structured diagnostics and error recovery
  (`E0002`–`E0009` for syntax errors).
- AST with explicit typed dataclasses for every node.
- Semantic analyzer with lexical scopes, symbol tables, and forward references.
- Static type checker with inference and ~20 type-error codes.
- Dextra IR: `Module` / `Function` / `BasicBlock` / `Instruction` with
  explicit control flow.
- LLVM backend using `llvmlite.ir` to construct LLVM IR programmatically.
- Native code emission via `llvmlite.binding`, linked with `gcc`.
- Minimal C runtime (`runtime.c`) for strings, arrays, and printing.
- Diagnostic engine with Rust-style rendering (error code, source line,
  caret underline, help text).
- Deterministic source formatter (`dextra fmt`).
- CLI: `build`, `run`, `check`, `emit-ir`, `lex`, `ast`, `fmt`, `new`, `clean`.

### Added — Tests & docs

- 96 unit + golden + negative + end-to-end tests passing in ~1 second.
- 13 example programs in `examples/`.
- Documentation in `docs/`: language spec, grammar, architecture,
  type system, diagnostics, IR, getting started.

### Known limitations

- No short-circuit evaluation of `&&` / `||`.
- No bounds checking on array access.
- No implicit numeric conversion (`Int` → `Float`).
- No type checking that all paths through a non-`Void` function return.
- Memory is `malloc`'d and never freed.
- No modules, enums, match, methods, closures, or generics (reserved keywords).
- Linux x86_64 only.
