"""Phase 1.2 regression test: LLVM initialization compatibility.

This test suite covers the Phase 1.2 fix: removing the deprecated
`llvm.initialize()` call while keeping the required
`llvm.initialize_native_target()` and `llvm.initialize_native_asmprinter()`
calls.  The tests exercise the real LLVM path — no mocking.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from dextra.codegen import (
    ensure_llvm_initialized,
    is_llvm_initialized,
    llvm_environment_info,
    parse_and_verify,
    emit_object_from_ir_text,
)
import dextra.codegen.llvm_runtime as llvm_runtime


# ---------------------------------------------------------------------------
# Section 5-6: Initialization contract & idempotency


def test_phase12_initialize_does_not_call_deprecated_initialize():
    """The deprecated `llvm.initialize()` MUST NOT be called in production.

    We use AST inspection rather than text search because the module's
    docstring mentions `llvm.initialize()` for explanatory reasons.
    """

    src = Path(llvm_runtime.__file__).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func
            # Match llvm.initialize() — but NOT llvm.initialize_native_target()
            # or llvm.initialize_native_asmprinter() (different attr name).
            if (isinstance(attr.value, ast.Name)
                    and attr.value.id == "llvm"
                    and attr.attr == "initialize"):
                pytest.fail(
                    f"deprecated llvm.initialize() called at "
                    f"line {node.lineno} of {llvm_runtime.__file__}"
                )


def test_phase12_initialize_calls_required_native_target():
    """The required `llvm.initialize_native_target()` MUST be present.

    Without it, `Target.from_default_triple()` raises
    'Unable to find target for this triple (no targets are registered)'.
    """

    src = Path(llvm_runtime.__file__).read_text()
    assert "llvm.initialize_native_target()" in src, (
        "llvm.initialize_native_target() must be called — without it "
        "Target.from_default_triple() raises RuntimeError"
    )


def test_phase12_initialize_calls_required_native_asmprinter():
    """The required `llvm.initialize_native_asmprinter()` MUST be present.

    Without it, `TargetMachine.emit_object()` raises
    'TargetMachine can't emit a file of this type'.
    """

    src = Path(llvm_runtime.__file__).read_text()
    assert "llvm.initialize_native_asmprinter()" in src, (
        "llvm.initialize_native_asmprinter() must be called — without it "
        "TargetMachine.emit_object() raises RuntimeError"
    )


def test_phase12_initialize_is_idempotent():
    """Repeated calls to ensure_llvm_initialized() must not fail."""

    ensure_llvm_initialized()
    first = is_llvm_initialized()
    ensure_llvm_initialized()
    ensure_llvm_initialized()
    ensure_llvm_initialized()
    assert first, "is_llvm_initialized() should be True after first call"


def test_phase12_initialize_actually_runs_native_target():
    """After ensure_llvm_initialized(), Target.from_default_triple() works.

    This is the real proof that initialization did the right thing —
    not a mocked test.
    """

    ensure_llvm_initialized()
    from llvmlite import binding as llvm
    target = llvm.Target.from_default_triple()
    assert target is not None


def test_phase12_initialize_actually_runs_asmprinter():
    """After ensure_llvm_initialized(), TargetMachine.emit_object() works."""

    ensure_llvm_initialized()
    from llvmlite import binding as llvm
    target = llvm.Target.from_default_triple()
    tm = target.create_target_machine(opt=2, reloc="pic", codemodel="default")
    mod = llvm.parse_assembly("define i64 @main() { ret i64 0 }")
    mod.verify()
    obj = tm.emit_object(mod)
    assert isinstance(obj, bytes) and len(obj) > 0


def test_phase12_llvm_environment_info_returns_string():
    """llvm_environment_info() returns a useful version string."""

    info = llvm_environment_info()
    assert "llvmlite" in info
    assert "LLVM" in info


# ---------------------------------------------------------------------------
# Section 8-10: LLVM IR parsing, verification, object generation, linking


_TRIVIAL_IR = "define i64 @main() { ret i64 0 }"


def test_phase12_parse_and_verify_succeeds_on_minimal_module():
    """parse_and_verify() succeeds on a minimal LLVM module."""

    mod = parse_and_verify(_TRIVIAL_IR)
    assert mod is not None


def test_phase12_parse_and_verify_raises_on_invalid_ir():
    """parse_and_verify() raises on malformed IR (hard correctness boundary)."""

    with pytest.raises(Exception):
        parse_and_verify("this is not valid LLVM IR")


def test_phase12_emit_object_returns_nonzero_bytes():
    """emit_object_from_ir_text returns a non-empty bytes object."""

    obj = emit_object_from_ir_text(_TRIVIAL_IR)
    assert isinstance(obj, bytes)
    assert len(obj) > 0


def test_phase12_emit_object_writes_real_elf_header():
    """The emitted object bytes start with an ELF magic number (0x7f 'ELF')
    on Linux x86_64.  This proves we emitted a real object file, not just
    a Python bytes object.
    """

    obj = emit_object_from_ir_text(_TRIVIAL_IR)
    # ELF magic: 0x7f, 'E', 'L', 'F'
    assert obj[:4] == b"\x7fELF", (
        f"expected ELF magic, got {obj[:4]!r}"
    )


# ---------------------------------------------------------------------------
# Sections 11-20: End-to-end compilation through the real CLI/pipeline


@pytest.fixture
def tmp_dx_file(tmp_path: Path):
    """Factory: write Dextra source to a tmp .dx file, return its path."""

    def _make(src: str, name: str = "prog") -> Path:
        p = tmp_path / f"{name}.dx"
        p.write_text(src)
        return p
    return _make


def _compile_and_run(dx_path: Path, tmp_path: Path, name: str = "prog") -> tuple[int, str, str]:
    """Compile a .dx file to a native binary and run it.  Returns (rc, stdout, stderr)."""

    from dextra.pipeline import build_from_file
    out_path = tmp_path / name
    rc = build_from_file(dx_path, out_path, verbose=False)
    if rc != 0:
        return rc, "", f"build failed (rc={rc})"
    proc = subprocess.run([str(out_path)], capture_output=True, text=True, timeout=10)
    return proc.returncode, proc.stdout, proc.stderr


# --- Section 11: minimal end-to-end ---


def test_phase12_minimal_main_returns_zero(tmp_dx_file, tmp_path: Path):
    src = "fn main() -> Int { return 0 }"
    dx = tmp_dx_file(src, "minimal")
    rc, stdout, stderr = _compile_and_run(dx, tmp_path, "minimal")
    assert rc == 0, f"exit code was {rc}; stderr={stderr}"
    assert stdout == "", f"stdout was {stdout!r}, expected empty"


# --- Section 12: hello world ---


def test_phase12_hello_world(tmp_dx_file, tmp_path: Path):
    src = '''fn main() -> Int { println("Hello, Dextra!") return 0 }'''
    dx = tmp_dx_file(src, "hello")
    rc, stdout, stderr = _compile_and_run(dx, tmp_path, "hello")
    assert rc == 0, f"exit={rc}; stderr={stderr}"
    assert stdout == "Hello, Dextra!\n"


# --- Section 13: arithmetic ---


def test_phase12_arithmetic(tmp_dx_file, tmp_path: Path):
    src = "fn main() -> Int { println(10 + 20) return 0 }"
    dx = tmp_dx_file(src, "arith")
    rc, stdout, stderr = _compile_and_run(dx, tmp_path, "arith")
    assert rc == 0, f"exit={rc}; stderr={stderr}"
    assert stdout == "30\n"


# --- Section 14: function call ---


def test_phase12_function_call(tmp_dx_file, tmp_path: Path):
    src = '''fn add(a: Int, b: Int) -> Int { return a + b }
fn main() -> Int { println(add(10, 20)) return 0 }'''
    dx = tmp_dx_file(src, "callfn")
    rc, stdout, stderr = _compile_and_run(dx, tmp_path, "callfn")
    assert rc == 0, f"exit={rc}; stderr={stderr}"
    assert stdout == "30\n"


# --- Section 15: conditional ---


def test_phase12_conditional(tmp_dx_file, tmp_path: Path):
    src = '''fn main() -> Int {
    let x = 10
    if x > 5 {
        println("yes")
    } else {
        println("no")
    }
    return 0
}'''
    dx = tmp_dx_file(src, "cond")
    rc, stdout, stderr = _compile_and_run(dx, tmp_path, "cond")
    assert rc == 0, f"exit={rc}; stderr={stderr}"
    assert stdout == "yes\n"


# --- Section 16: loop ---


def test_phase12_while_loop(tmp_dx_file, tmp_path: Path):
    src = '''fn main() -> Int {
    let mut i = 0
    while i < 5 {
        println(i)
        i = i + 1
    }
    return 0
}'''
    dx = tmp_dx_file(src, "loop")
    rc, stdout, stderr = _compile_and_run(dx, tmp_path, "loop")
    assert rc == 0, f"exit={rc}; stderr={stderr}"
    assert stdout == "0\n1\n2\n3\n4\n"


# --- Section 17: recursion ---


def test_phase12_recursion(tmp_dx_file, tmp_path: Path):
    src = '''fn factorial(n: Int) -> Int {
    if n <= 1 { return 1 }
    return n * factorial(n - 1)
}
fn main() -> Int { println(factorial(10)) return 0 }'''
    dx = tmp_dx_file(src, "rec")
    rc, stdout, stderr = _compile_and_run(dx, tmp_path, "rec")
    assert rc == 0, f"exit={rc}; stderr={stderr}"
    assert stdout == "3628800\n"


# --- Section 18: struct ---


def test_phase12_struct(tmp_dx_file, tmp_path: Path):
    src = '''struct User { name: String, age: Int }
fn main() -> Int {
    let user = User { name: "Alice", age: 21 }
    println(user.name)
    println(user.age)
    return 0
}'''
    dx = tmp_dx_file(src, "struct")
    rc, stdout, stderr = _compile_and_run(dx, tmp_path, "struct")
    assert rc == 0, f"exit={rc}; stderr={stderr}"
    assert stdout == "Alice\n21\n"


# --- Section 19: array ---


def test_phase12_array(tmp_dx_file, tmp_path: Path):
    src = '''fn main() -> Int {
    let numbers = [1, 2, 3, 4, 5]
    println(numbers[0])
    println(numbers[4])
    return 0
}'''
    dx = tmp_dx_file(src, "arr")
    rc, stdout, stderr = _compile_and_run(dx, tmp_path, "arr")
    assert rc == 0, f"exit={rc}; stderr={stderr}"
    assert stdout == "1\n5\n"


# --- Section 20: string concatenation ---


def test_phase12_string_concat(tmp_dx_file, tmp_path: Path):
    src = '''fn main() -> Int {
    let a = "Hello"
    let b = " Dextra"
    println(a + b)
    return 0
}'''
    dx = tmp_dx_file(src, "str")
    rc, stdout, stderr = _compile_and_run(dx, tmp_path, "str")
    assert rc == 0, f"exit={rc}; stderr={stderr}"
    assert stdout == "Hello Dextra\n"


# ---------------------------------------------------------------------------
# Section 30: verify generated LLVM IR for several programs


def _gen_llvm(src: str) -> str:
    from dextra.pipeline import compile_source
    r = compile_source(src, filename="test.dx")
    assert r.success and r.llvm_ir_text is not None
    return r.llvm_ir_text


def test_phase12_hello_world_llvm_ir_is_valid():
    """The LLVM IR for hello-world parses and verifies."""

    src = '''fn main() -> Int { println("Hello") return 0 }'''
    ir = _gen_llvm(src)
    parse_and_verify(ir)  # raises if invalid


def test_phase12_arithmetic_llvm_ir_has_add_instruction():
    src = "fn main() -> Int { let x = 10 + 20 return x }"
    ir = _gen_llvm(src)
    assert "add" in ir


def test_phase12_function_call_llvm_ir_has_call():
    src = '''fn id(x: Int) -> Int { return x }
fn main() -> Int { return id(42) }'''
    ir = _gen_llvm(src)
    assert "call" in ir


def test_phase12_conditional_llvm_ir_has_branch():
    src = '''fn main() -> Int { if true { return 1 } return 0 }'''
    ir = _gen_llvm(src)
    assert "br " in ir or "cond_br" in ir or "cbr" in ir or "branch" in ir.lower()


def test_phase12_loop_llvm_ir_has_branch_back_edge():
    src = '''fn main() -> Int { let mut i = 0 while i < 5 { i = i + 1 } return 0 }'''
    ir = _gen_llvm(src)
    assert "br " in ir  # branch instructions present


# ---------------------------------------------------------------------------
# Section 29: actual CLI


def test_phase12_cli_dextra_version_reports_llvm_info():
    env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
           "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        [sys.executable, "-m", "dextra", "--version"],
        env={**dict(__import__("os").environ), **env},
        capture_output=True, text=True,
    )
    # argparse writes version to stderr by default (with our custom action
    # we wrote to file=None which is also stderr).
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0
    assert "Dextra" in combined
    assert "llvmlite" in combined
    assert "LLVM" in combined


def test_phase12_cli_run_hello_world():
    """`dextra run examples/hello.dx` produces the expected output."""

    env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
           "PATH": "/usr/bin:/bin"}
    repo_root = Path(__file__).resolve().parents[1]
    hello = repo_root / "examples" / "hello.dx"
    proc = subprocess.run(
        [sys.executable, "-m", "dextra", "run", str(hello)],
        env={**dict(__import__("os").environ), **env},
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Hello, Dextra!" in proc.stdout
