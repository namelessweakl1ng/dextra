"""LLVM backend tests — verify IR lowering produces valid, executable LLVM IR."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dextra.codegen import compile_to_llvm_ir, emit_object_from_ir_text, parse_and_verify
from dextra.codegen.llvm_runtime import ensure_llvm_initialized, is_llvm_initialized
from dextra.codegen.native import compile_runtime, link_executable, find_cc
from dextra.diagnostics import DiagnosticEngine
from dextra.ir import lower_program
from dextra.lexer import Lexer, SourceFile
from dextra.parser import Parser
from dextra.semantic import Analyzer


def compile_llvm(src: str) -> str:
    diag = DiagnosticEngine()
    sf = SourceFile("test.dx", src)
    toks = Lexer(sf, diag).tokenize()
    prog = Parser(toks, diag).parse_program()
    a = Analyzer(diag)
    ap = a.analyze(prog)
    mod = lower_program(ap)
    llvm_mod = compile_to_llvm_ir(mod, ap.ctx)
    return str(llvm_mod)


def test_llvm_initialization_is_centralized_and_idempotent():
    """`ensure_llvm_initialized()` is the single entry point and is idempotent."""

    # Reset to fresh state for this test only.  We can't actually un-init
    # llvmlite, but we can verify the function doesn't raise on repeat calls.
    ensure_llvm_initialized()
    assert is_llvm_initialized()
    # Call again — should be a no-op, not an error.
    ensure_llvm_initialized()
    assert is_llvm_initialized()


def test_hello_world_llvm_ir_is_valid():
    ir_text = compile_llvm('fn main() -> Int { println("hello") return 0 }')
    parse_and_verify(ir_text)   # raises if invalid
    assert '"main"' in ir_text   # llvmlite quotes function names
    assert "dx_println_string" in ir_text


def test_arithmetic_llvm_ir_has_add():
    ir_text = compile_llvm("fn main() -> Int { let x = 1 + 2 return x }")
    assert "add" in ir_text


def test_llvm_ir_compiles_to_object():
    ir_text = compile_llvm("fn main() -> Int { return 0 }")
    obj = emit_object_from_ir_text(ir_text)
    assert isinstance(obj, bytes) and len(obj) > 0


def test_full_pipeline_executes_correctly(tmp_path: Path):
    """End-to-end: source → LLVM IR → object → linked exe → run → check stdout."""

    src = """fn main() -> Int { println(1 + 2) return 0 }"""
    ir_text = compile_llvm(src)
    parse_and_verify(ir_text)
    obj_bytes = emit_object_from_ir_text(ir_text)
    obj_path = tmp_path / "prog.o"
    obj_path.write_bytes(obj_bytes)
    runtime_obj = tmp_path / "runtime.o"
    compile_runtime(runtime_obj, cc=find_cc())
    exe_path = tmp_path / "prog"
    link_executable([obj_path, runtime_obj], exe_path, cc=find_cc())
    proc = subprocess.run([str(exe_path)], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0
    assert proc.stdout == "3\n"
