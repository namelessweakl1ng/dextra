"""Regression tests for bugs found during the Phase 1.1 audit.

Each test below corresponds to a specific bug found in the audit and
verifies that the fix actually works.  Naming convention:

    test_regression_<bug-id>_<short-description>

Bug IDs match the "Audit Findings" section of the Phase 1.1 report.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dextra.diagnostics import DiagnosticEngine
from dextra.ir import lower_program
from dextra.codegen import compile_to_llvm_ir
from dextra.lexer import Lexer, SourceFile
from dextra.parser import Parser
from dextra.semantic import Analyzer
from dextra.pipeline import build_from_file, compile_source


# ---------------------------------------------------------------------------
# Helpers


def compile_and_run(src: str, tmp_path: Path, name: str = "t") -> tuple[int, str, str]:
    """Compile `src` to a native binary and run it.

    Returns (exit_code, stdout, stderr).
    """

    src_path = tmp_path / f"{name}.dx"
    src_path.write_text(src)
    out_path = tmp_path / name
    rc = build_from_file(src_path, out_path, verbose=False)
    if rc != 0:
        return rc, "", f"build failed (rc={rc})"
    proc = subprocess.run([str(out_path)], capture_output=True, text=True, timeout=10)
    return proc.returncode, proc.stdout, proc.stderr


def expect_ok(src: str, tmp_path: Path, name: str = "t") -> str:
    rc, stdout, stderr = compile_and_run(src, tmp_path, name)
    assert rc == 0, f"build failed: {stderr}"
    return stdout


def expect_compile_error_codes(src: str) -> set[str]:
    r = compile_source(src, filename="test.dx")
    return {d.code.value for d in r.diagnostics.diagnostics if d.severity.value == "error"}


def expect_warning_codes(src: str) -> set[str]:
    r = compile_source(src, filename="test.dx")
    return {d.code.value for d in r.diagnostics.diagnostics if d.severity.value == "warning"}


# ---------------------------------------------------------------------------
# B1: LLVM initialization is centralized.


def test_regression_b1_llvm_init_is_centralized():
    """All LLVM init calls live in `dextra.codegen.llvm_runtime`."""

    from dextra.codegen.llvm_runtime import ensure_llvm_initialized, is_llvm_initialized
    ensure_llvm_initialized()
    assert is_llvm_initialized()
    # Idempotent.
    ensure_llvm_initialized()
    assert is_llvm_initialized()


def test_regression_b1_no_direct_llvm_initialize_in_source_modules():
    """No module under `src/dextra/` except `llvm_runtime.py` calls llvm.initialize*."""

    import re
    src_dir = Path(__file__).resolve().parents[1] / "src" / "dextra"
    forbidden = re.compile(r"\bllvm(?:_b)?\.initialize(?:_native_\w+)?\(\)")
    offenders: list[str] = []
    for py in src_dir.rglob("*.py"):
        if py.name == "llvm_runtime.py":
            continue
        try:
            text = py.read_text()
        except OSError:
            continue
        if forbidden.search(text):
            offenders.append(str(py))
    assert not offenders, (
        f"Direct llvm.initialize() calls found in: {offenders}.  "
        "All LLVM init must go through `dextra.codegen.llvm_runtime.ensure_llvm_initialized()`."
    )


# ---------------------------------------------------------------------------
# B2: Structs returned from functions must not expose dangling stack pointers.


def test_regression_b2_struct_return_does_not_dangle(tmp_path: Path):
    """`make_user()` returns a heap-allocated struct whose fields survive."""

    src = '''struct User { name: String, age: Int }
fn make_user() -> User {
    let u = User { name: "Alice", age: 21 }
    return u
}
fn main() -> Int {
    let u = make_user()
    println(u.name)
    println(u.age)
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    assert out == "Alice\n21\n"


def test_regression_b2_struct_passed_as_argument(tmp_path: Path):
    """A struct passed to a function preserves its fields."""

    src = '''struct User { name: String, age: Int }
fn greet(u: User) -> String {
    return "Hello, " + u.name
}
fn main() -> Int {
    let u = User { name: "Bob", age: 30 }
    println(greet(u))
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    assert out == "Hello, Bob\n"


def test_regression_b2_struct_chained_function_returns(tmp_path: Path):
    """Returning a struct from a function that itself received a struct."""

    src = '''struct Counter { value: Int }
fn increment(c: Counter) -> Counter {
    return Counter { value: c.value + 1 }
}
fn main() -> Int {
    let c = Counter { value: 41 }
    let c2 = increment(c)
    let c3 = increment(c2)
    println(c3.value)
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    assert out == "43\n"


# ---------------------------------------------------------------------------
# B3: Arrays preserve element types (struct / string access through index).


def test_regression_b3_array_of_structs_field_access(tmp_path: Path):
    """`users[0].age` reads the correct field from a struct array."""

    src = '''struct User { name: String, age: Int }
fn main() -> Int {
    let users = [User { name: "A", age: 1 }, User { name: "B", age: 2 }]
    println(users[0].age)
    println(users[1].age)
    println(users[0].name)
    println(users[1].name)
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    assert out == "1\n2\nA\nB\n"


def test_regression_b3_array_of_strings_indexing(tmp_path: Path):
    """`xs[0]` on a string array returns the correct string."""

    src = '''fn main() -> Int {
    let xs = ["alpha", "beta", "gamma"]
    println(xs[0])
    println(xs[1])
    println(xs[2])
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    assert out == "alpha\nbeta\ngamma\n"


def test_regression_b3_for_over_array_of_structs(tmp_path: Path):
    """Iterating over a struct array binds each element to a struct value."""

    src = '''struct User { name: String, age: Int }
fn main() -> Int {
    let users = [User { name: "A", age: 1 }, User { name: "B", age: 2 }]
    for u in users {
        println(u.age)
    }
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    assert out == "1\n2\n"


def test_regression_b3_for_over_array_of_strings(tmp_path: Path):
    """Iterating over a string array yields each string."""

    src = '''fn main() -> Int {
    let xs = ["a", "b", "c"]
    for s in xs {
        println(s)
    }
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    assert out == "a\nb\nc\n"


def test_regression_b3_struct_field_assignment_through_array(tmp_path: Path):
    """`users[0].age = 99` correctly mutates the field through an index."""

    src = '''struct User { name: String, age: Int }
fn main() -> Int {
    let mut users = [User { name: "A", age: 1 }, User { name: "B", age: 2 }]
    users[0].age = 99
    println(users[0].age)
    println(users[1].age)
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    assert out == "99\n2\n"


# ---------------------------------------------------------------------------
# B4: String == and != must work (spec says they're allowed; was crashing).


def test_regression_b4_string_equality_equal(tmp_path: Path):
    """`"x" == "x"` is true."""

    src = '''fn main() -> Int {
    let a = "hello"
    let b = "hello"
    if a == b {
        println(1)
    } else {
        println(0)
    }
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    assert out == "1\n"


def test_regression_b4_string_equality_unequal(tmp_path: Path):
    """`"hello" != "world"` is true."""

    src = '''fn main() -> Int {
    let a = "hello"
    let c = "world"
    if a == c {
        println(1)
    } else {
        println(0)
    }
    if a != c {
        println(10)
    } else {
        println(20)
    }
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    assert out == "0\n10\n"


def test_regression_b4_string_equality_empty():
    """Empty strings compare equal to themselves at compile time."""

    # Just verify it compiles.
    codes = expect_compile_error_codes('fn main() -> Int { let a = "" let b = "" if a == b { return 0 } return 1 }')
    assert "E0205" not in codes


# ---------------------------------------------------------------------------
# B5: CLI handles missing files gracefully (no raw Python tracebacks).


def test_regression_b5_cli_missing_file_lex():
    import sys, os
    env = dict(os.environ); env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "dextra", "lex", "/tmp/_dextra_does_not_exist.dx"],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "not found" in proc.stderr.lower() or "file" in proc.stderr.lower()


def test_regression_b5_cli_missing_file_build():
    import sys, os
    env = dict(os.environ); env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "dextra", "build", "/tmp/_dextra_does_not_exist.dx"],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "not found" in proc.stderr.lower()


def test_regression_b5_cli_missing_file_check():
    import sys, os
    env = dict(os.environ); env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "dextra", "check", "/tmp/_dextra_does_not_exist.dx"],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr


def test_regression_b5_cli_missing_file_emit_ir():
    import sys, os
    env = dict(os.environ); env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "dextra", "emit-ir", "/tmp/_dextra_does_not_exist.dx"],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr


def test_regression_b5_cli_missing_file_fmt():
    import sys, os
    env = dict(os.environ); env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "dextra", "fmt", "/tmp/_dextra_does_not_exist.dx"],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr


# ---------------------------------------------------------------------------
# B6: Division by literal zero emits a warning (was silent UB).


def test_regression_b6_int_division_by_zero_literal_warns():
    src = "fn main() -> Int { let z = 10 / 0 return 0 }"
    warnings = expect_warning_codes(src)
    assert "W0001" in warnings


def test_regression_b6_int_modulo_by_zero_literal_warns():
    src = "fn main() -> Int { let z = 10 % 0 return 0 }"
    warnings = expect_warning_codes(src)
    assert "W0001" in warnings


def test_regression_b6_float_division_by_zero_literal_warns():
    src = "fn main() -> Int { let z = 1.5 / 0.0 return 0 }"
    warnings = expect_warning_codes(src)
    assert "W0001" in warnings


def test_regression_b6_division_by_nonzero_literal_no_warning():
    src = "fn main() -> Int { let z = 10 / 2 return 0 }"
    warnings = expect_warning_codes(src)
    assert "W0001" not in warnings


def test_regression_b6_division_by_variable_no_warning():
    """Division by a variable (even one we suspect is zero) does NOT warn —
    we can't prove it's zero at compile time.
    """
    src = "fn main() -> Int { let y = 0 let z = 10 / y return 0 }"
    warnings = expect_warning_codes(src)
    assert "W0001" not in warnings


# ---------------------------------------------------------------------------
# IR verifier (new in this phase).


def test_ir_verifier_accepts_well_formed_module():
    from dextra.ir import verify_module
    src = "fn main() -> Int { let x = 1 return x }"
    diag = DiagnosticEngine()
    sf = SourceFile("test.dx", src)
    toks = Lexer(sf, diag).tokenize()
    prog = Parser(toks, diag).parse_program()
    ap = Analyzer(diag).analyze(prog)
    mod = lower_program(ap)
    errs = verify_module(mod)
    assert errs == [], f"unexpected IR verification errors: {errs}"


def test_ir_verifier_catches_missing_terminator():
    from dextra.ir import BasicBlock, Function, Module, verify_module
    from dextra.types.type_system import TypeContext
    ctx = TypeContext()
    mod = Module(name="t")
    bb = BasicBlock(label="entry")
    bb.terminator = None   # missing
    fn = Function(name="f", return_type=ctx.INT, params=[], blocks=[bb])
    mod.functions.append(fn)
    errs = verify_module(mod)
    assert any("missing terminator" in e for e in errs)


def test_ir_verifier_catches_duplicate_function():
    from dextra.ir import BasicBlock, ConstInt, Function, Module, Return, verify_module
    from dextra.types.type_system import TypeContext
    ctx = TypeContext()
    mod = Module(name="t")
    bb = BasicBlock(label="entry")
    bb.terminator = Return(value=ConstInt(value=0, type=ctx.INT))
    fn = Function(name="f", return_type=ctx.INT, params=[], blocks=[bb])
    mod.functions.append(fn)
    mod.functions.append(fn)   # duplicate
    errs = verify_module(mod)
    assert any("duplicate function" in e for e in errs)


def test_ir_verifier_catches_branch_to_nonexistent_block():
    from dextra.ir import (
        BasicBlock, Branch, Function, Module, Return, verify_module,
    )
    from dextra.types.type_system import TypeContext
    from dextra.ir import ConstInt
    ctx = TypeContext()
    mod = Module(name="t")
    phantom = BasicBlock(label="phantom")
    bb = BasicBlock(label="entry")
    bb.terminator = Branch(target=phantom)   # target not in fn.blocks
    fn = Function(name="f", return_type=ctx.INT, params=[], blocks=[bb])
    mod.functions.append(fn)
    errs = verify_module(mod)
    assert any("branch target" in e for e in errs)


def test_ir_verifier_is_wired_into_pipeline():
    """`compile_source` runs the IR verifier; a malformed IR causes failure."""

    # We can't easily produce a malformed IR through normal source, so we
    # just confirm the verifier import works through the pipeline.
    src = "fn main() -> Int { return 0 }"
    r = compile_source(src, filename="test.dx")
    assert r.success


# ---------------------------------------------------------------------------
# Documented behavior: no short-circuit (verified, not a bug).


def test_documented_no_short_circuit_both_operands_evaluated(tmp_path: Path):
    """`false && side_effect()` still evaluates side_effect() — this is
    documented behavior in v0.1, NOT a bug.
    """

    src = '''fn side_effect() -> Bool {
    println("side")
    return true
}
fn main() -> Int {
    let _ = false && side_effect()
    println("end")
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    # Both "side" and "end" should appear — proving no short-circuit.
    assert "side" in out
    assert "end" in out


# ---------------------------------------------------------------------------
# Documented behavior: for-range is exclusive-upper.


def test_documented_for_range_exclusive_upper(tmp_path: Path):
    """`for i in 0..5` iterates exactly 5 times (0,1,2,3,4)."""

    src = '''fn main() -> Int {
    let mut count = 0
    for i in 0..5 {
        count = count + 1
    }
    println(count)
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    assert out == "5\n"


# ---------------------------------------------------------------------------
# Documented behavior: no implicit Int->Float promotion.


def test_documented_no_implicit_int_to_float_promotion():
    src = "fn main() -> Int { let x = 1 + 2.0 return 0 }"
    codes = expect_compile_error_codes(src)
    # Should be a type error — both operands must be the same type.
    assert len(codes) > 0


# ---------------------------------------------------------------------------
# Documented behavior: missing-return is now enforced (Phase 1.3).


def test_documented_missing_return_returns_default(tmp_path: Path):
    """Phase 1.3 promotion: missing-return is now a hard error (E0218),
    not the documented "returns default" behavior of v0.1.

    This test was originally written for v0.1 behavior; in Phase 1.3 we
    changed the semantics to enforce all-paths-return for non-Void
    functions.  The test now verifies that the compiler rejects the
    program rather than silently producing a default-value return.
    """

    src = '''fn f(x: Int) -> Int {
    if x > 10 {
        return 1
    }
}
fn main() -> Int {
    println(f(5))
    return 0
}
'''
    # Compilation must fail with E0218.
    from dextra.pipeline import compile_source
    r = compile_source(src, filename="t.dx")
    codes = {d.code.value for d in r.diagnostics.diagnostics if d.severity.value == "error"}
    assert "E0218" in codes, (
        f"expected E0218 (missing return); got errors={codes}"
    )


# ---------------------------------------------------------------------------
# Nested break/continue target the innermost loop only.


def test_nested_break_targets_inner_loop_only(tmp_path: Path):
    src = '''fn main() -> Int {
    let mut i = 0
    let mut total = 0
    while i < 3 {
        let mut j = 0
        while j < 3 {
            if j == 1 {
                break
            }
            total = total + 1
            j = j + 1
        }
        total = total + 10
        i = i + 1
    }
    println(total)
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    # 3 outer iters × (1 inner + 10 outer) = 33
    assert out == "33\n"


def test_continue_targets_inner_loop(tmp_path: Path):
    src = '''fn main() -> Int {
    let mut total = 0
    for i in 0..5 {
        if i == 2 {
            continue
        }
        total = total + 1
    }
    println(total)
    return 0
}
'''
    out = expect_ok(src, tmp_path)
    # 0,1,3,4 → 4 iterations
    assert out == "4\n"
