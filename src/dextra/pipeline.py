"""End-to-end driver: source → tokens → AST → typed AST → IR → LLVM IR → executable.

Used as the canonical pipeline by the CLI and by integration tests.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dextra.codegen import (
    compile_to_llvm_ir,
    emit_object_from_ir_text,
    find_cc,
    parse_and_verify,
)
from dextra.codegen.native import compile_runtime, link_executable
from dextra.diagnostics import DiagnosticEngine
from dextra.ir import assert_module_valid, lower_program
from dextra.lexer import Lexer, SourceFile
from dextra.parser import Parser
from dextra.semantic import Analyzer


@dataclass
class CompileResult:
    success: bool
    diagnostics: DiagnosticEngine
    llvm_ir_text: Optional[str] = None
    # True iff the failure was a compiler-internal bug (LLVM backend
    # raised an exception after semantic analysis accepted the program).
    # The CLI uses this to distinguish exit code 2 (infrastructure) from
    # exit code 1 (user error).
    internal_error: bool = False


def compile_source(source_text: str, filename: str = "<string>",
                   verbose: bool = False) -> CompileResult:
    """Run the frontend through LLVM IR generation.

    Returns a `CompileResult` carrying the LLVM IR text if successful.
    The result is NOT yet verified by LLVM — callers that need a
    verified module should use `compile_and_verify_source` or call
    `dextra.codegen.parse_and_verify` themselves.
    """

    diag = DiagnosticEngine()
    sf = SourceFile(filename, source_text)
    tokens = Lexer(sf, diag).tokenize()
    if diag.has_errors:
        return CompileResult(success=False, diagnostics=diag)
    if verbose:
        print(f"[lexer] {len(tokens)} tokens")
    prog = Parser(tokens, diag).parse_program()
    if verbose:
        print("[parser] AST constructed")
    if diag.has_errors:
        return CompileResult(success=False, diagnostics=diag)
    a = Analyzer(diag)
    analyzed = a.analyze(prog)
    if verbose:
        print("[semantic] symbols resolved")
    if diag.has_errors:
        return CompileResult(success=False, diagnostics=diag)
    # Dextra IR lowering — wrap in an error boundary so backend bugs don't
    # escape as raw Python tracebacks.  If we got here, semantic analysis
    # accepted the program; a backend failure is therefore a compiler-
    # internal bug, not a user error.
    try:
        mod = lower_program(analyzed)
    except Exception as exc:
        print(
            f"error: Dextra IR lowering raised {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print(
            "  (semantic analysis accepted this program; the failure is "
            "a compiler-internal bug, not a user error)",
            file=sys.stderr,
        )
        return CompileResult(success=False, diagnostics=diag, internal_error=True)
    if verbose:
        print("[ir] lowered")
    # Verify the IR before LLVM codegen — this catches structural bugs
    # (missing terminators, undefined Temp refs, dangling branches, etc.)
    # and type-consistency bugs (BinOp operand mismatch, CondBranch cond
    # not Bool, Call arg/return mismatch, Return type mismatch) that
    # would otherwise produce confusing LLVM errors.
    try:
        assert_module_valid(mod, analyzed.ctx)
    except AssertionError as exc:
        print(f"error: Dextra IR verification failed:\n{exc}", file=sys.stderr)
        return CompileResult(success=False, diagnostics=diag)
    # LLVM codegen — wrap in an error boundary so backend bugs don't
    # escape as raw Python tracebacks.  If we got here, semantic
    # analysis accepted the program; a backend failure is therefore a
    # compiler-internal bug, not a user error.
    try:
        llvm_mod = compile_to_llvm_ir(mod, analyzed.ctx)
    except Exception as exc:
        # Print a clear "internal compiler error" with enough context
        # to debug, plus the original exception type and message.
        print(
            f"error: Dextra LLVM backend raised {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print(
            "  (semantic analysis accepted this program; the failure is "
            "a compiler-internal bug, not a user error)",
            file=sys.stderr,
        )
        return CompileResult(success=False, diagnostics=diag, internal_error=True)
    if verbose:
        print("[llvm] LLVM IR generated")
    return CompileResult(success=True, diagnostics=diag, llvm_ir_text=str(llvm_mod))


def build_from_file(path: Path, out_path: Path, verbose: bool = False,
                    emit_ir: bool = False) -> int:
    """Compile a .dx file into a native executable.

    Returns 0 on success, 1 on failure (frontend or LLVM verification
    failure; runtime/linker failures raise).

    Pipeline:  source → tokens → AST → typed AST → Dextra IR → LLVM IR
    text → LLVM verify → object → runtime object → link → executable.
    LLVM verification is a hard correctness boundary — invalid IR
    fails the build here rather than producing a corrupt binary.
    """

    source = path.read_text()
    result = compile_source(source, filename=str(path), verbose=verbose)
    if not result.success or result.llvm_ir_text is None:
        print(result.diagnostics.render_all({str(path): SourceFile(str(path), source)}))
        return 1

    # LLVM verification step.  If the IR is invalid, this raises and
    # the build fails with a clear error.  We deliberately do NOT wrap
    # this in a broad `except Exception` — a verification failure is a
    # compiler bug, not a user error, and should surface loudly.
    try:
        parse_and_verify(result.llvm_ir_text)
    except Exception as exc:
        print(f"error: LLVM IR verification failed for {path}: {exc}",
              file=sys.stderr)
        # Dump the bad IR to a sibling .ll for debugging.
        ir_path = out_path.with_suffix(".bad.ll")
        ir_path.write_text(result.llvm_ir_text)
        print(f"  (LLVM IR written to {ir_path} for inspection)",
              file=sys.stderr)
        return 2  # 2 = infrastructure / compiler-internal failure

    if emit_ir:
        ir_path = out_path.with_suffix(".ll")
        ir_path.write_text(result.llvm_ir_text)
        return 0

    obj_bytes = emit_object_from_ir_text(result.llvm_ir_text)
    obj_path = out_path.with_suffix(".o")
    obj_path.write_bytes(obj_bytes)
    runtime_obj = out_path.with_suffix(".rt.o")
    compile_runtime(runtime_obj, cc=find_cc())
    link_executable([obj_path, runtime_obj], out_path, cc=find_cc())
    # Cleanup intermediates.
    for p in (obj_path, runtime_obj):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    return 0
