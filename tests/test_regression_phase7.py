"""Phase 7 regression tests: enums and match.

Covers:
  - enum declaration parsing + semantics
  - enum variant references (Color.Red)
  - match expression parsing + semantics
  - match exhaustiveness (E0219)
  - unknown variant (E0220)
  - scrutinee not enum (E0221)
  - arm type mismatch (E0222)
  - duplicate arm (E0223)
  - end-to-end compilation + execution
  - wildcard arm
  - enum as function parameter
  - enum as function return value
  - enum in struct field
  - enum in array
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dextra.pipeline import build_from_file, compile_source


# ---------------------------------------------------------------------------
# Helpers


def _compile_and_run(src: str, tmp_path: Path, name: str = "t") -> tuple[int, str, str]:
    src_path = tmp_path / f"{name}.dx"
    src_path.write_text(src)
    out_path = tmp_path / name
    rc = build_from_file(src_path, out_path, verbose=False)
    if rc != 0:
        return rc, "", f"build failed (rc={rc})"
    proc = subprocess.run([str(out_path)], capture_output=True, text=True, timeout=10)
    return proc.returncode, proc.stdout, proc.stderr


def _expect_ok(src: str, tmp_path: Path, name: str = "t") -> str:
    rc, stdout, stderr = _compile_and_run(src, tmp_path, name)
    assert rc == 0, f"build failed: {stderr}"
    return stdout


def _compile_error_codes(src: str) -> set[str]:
    r = compile_source(src, filename="t.dx")
    return {d.code.value for d in r.diagnostics.diagnostics
            if d.severity.value == "error"}


# ===========================================================================
# Enum declarations
# ===========================================================================


class TestEnumDeclarations:
    def test_simple_enum_compiles(self, tmp_path: Path):
        src = '''enum Color { Red, Green, Blue }
fn main() -> Int { return 0 }
'''
        out = _expect_ok(src, tmp_path)
        assert out == ""

    def test_enum_with_trailing_comma(self, tmp_path: Path):
        src = '''enum Color { Red, Green, Blue, }
fn main() -> Int { return 0 }
'''
        out = _expect_ok(src, tmp_path)
        assert out == ""

    def test_empty_enum_compiles(self, tmp_path: Path):
        """An enum with no variants is structurally valid (though useless)."""

        src = '''enum Empty { }
fn main() -> Int { return 0 }
'''
        out = _expect_ok(src, tmp_path)
        assert out == ""

    def test_duplicate_enum_rejected(self):
        src = '''enum Color { Red, Green, Blue }
enum Color { X, Y }
fn main() -> Int { return 0 }
'''
        codes = _compile_error_codes(src)
        assert "E0101" in codes

    def test_duplicate_variant_rejected(self):
        src = '''enum Color { Red, Green, Red }
fn main() -> Int { return 0 }
'''
        codes = _compile_error_codes(src)
        assert "E0101" in codes


# ===========================================================================
# Enum variant references
# ===========================================================================


class TestEnumVariantReferences:
    def test_variant_reference_type(self, tmp_path: Path):
        """`Color.Red` has type `Color`."""

        src = '''enum Color { Red, Green, Blue }
fn f(c: Color) -> Int { return 0 }
fn main() -> Int {
    f(Color.Red)
    f(Color.Green)
    f(Color.Blue)
    return 0
}
'''
        out = _expect_ok(src, tmp_path)
        assert out == ""

    def test_unknown_variant_rejected(self):
        src = '''enum Color { Red, Green, Blue }
fn f(c: Color) -> Int { return 0 }
fn main() -> Int {
    f(Color.Purple)
    return 0
}
'''
        codes = _compile_error_codes(src)
        assert "E0220" in codes

    def test_variant_as_function_return(self, tmp_path: Path):
        src = '''enum Color { Red, Green, Blue }
fn favorite() -> Color {
    return Color.Green
}
fn main() -> Int {
    let c = favorite()
    match c {
        Color.Red => println(1),
        Color.Green => println(2),
        Color.Blue => println(3),
    }
    return 0
}
'''
        out = _expect_ok(src, tmp_path)
        assert out == "2\n"

    def test_enum_in_struct_field(self, tmp_path: Path):
        src = '''enum Status { Active, Inactive }
struct User { name: String, status: Status }
fn main() -> Int {
    let u = User { name: "Alice", status: Status.Active }
    match u.status {
        Status.Active => println(1),
        Status.Inactive => println(0),
    }
    return 0
}
'''
        out = _expect_ok(src, tmp_path)
        assert out == "1\n"

    def test_enum_in_array(self, tmp_path: Path):
        src = '''enum Color { Red, Green, Blue }
fn main() -> Int {
    let colors = [Color.Red, Color.Green, Color.Blue]
    println(length(colors))
    return 0
}
'''
        out = _expect_ok(src, tmp_path)
        assert out == "3\n"


# ===========================================================================
# Match expression
# ===========================================================================


class TestMatchExpression:
    def test_match_all_variants(self, tmp_path: Path):
        src = '''enum Color { Red, Green, Blue }
fn describe(c: Color) -> Int {
    return match c {
        Color.Red => 1,
        Color.Green => 2,
        Color.Blue => 3,
    }
}
fn main() -> Int {
    println(describe(Color.Red))
    println(describe(Color.Green))
    println(describe(Color.Blue))
    return 0
}
'''
        out = _expect_ok(src, tmp_path)
        assert out == "1\n2\n3\n"

    def test_match_with_wildcard(self, tmp_path: Path):
        src = '''enum Color { Red, Green, Blue }
fn describe(c: Color) -> Int {
    return match c {
        Color.Red => 1,
        _ => 99,
    }
}
fn main() -> Int {
    println(describe(Color.Red))
    println(describe(Color.Green))
    println(describe(Color.Blue))
    return 0
}
'''
        out = _expect_ok(src, tmp_path)
        assert out == "1\n99\n99\n"

    def test_match_as_statement(self, tmp_path: Path):
        """Match with Void arms can be used as a statement."""

        src = '''enum Color { Red, Green, Blue }
fn main() -> Int {
    let c = Color.Green
    match c {
        Color.Red => println("red"),
        Color.Green => println("green"),
        Color.Blue => println("blue"),
    }
    return 0
}
'''
        out = _expect_ok(src, tmp_path)
        assert out == "green\n"

    def test_match_with_string_result(self, tmp_path: Path):
        src = '''enum Color { Red, Green, Blue }
fn name(c: Color) -> String {
    return match c {
        Color.Red => "red",
        Color.Green => "green",
        Color.Blue => "blue",
    }
}
fn main() -> Int {
    println(name(Color.Red))
    println(name(Color.Green))
    println(name(Color.Blue))
    return 0
}
'''
        out = _expect_ok(src, tmp_path)
        assert out == "red\ngreen\nblue\n"

    def test_match_trailing_comma(self, tmp_path: Path):
        src = '''enum Color { Red, Green, Blue }
fn describe(c: Color) -> Int {
    return match c {
        Color.Red => 1,
        Color.Green => 2,
        Color.Blue => 3,
    }
}
fn main() -> Int {
    println(describe(Color.Red))
    return 0
}
'''
        out = _expect_ok(src, tmp_path)
        assert out == "1\n"


# ===========================================================================
# Match exhaustiveness and error cases
# ===========================================================================


class TestMatchErrors:
    def test_non_exhaustive_match_rejected(self):
        src = '''enum Color { Red, Green, Blue }
fn f(c: Color) -> Int {
    return match c {
        Color.Red => 1,
    }
}
'''
        codes = _compile_error_codes(src)
        assert "E0219" in codes

    def test_unknown_variant_in_pattern_rejected(self):
        src = '''enum Color { Red, Green, Blue }
fn f(c: Color) -> Int {
    return match c {
        Color.Red => 1,
        Color.Green => 2,
        Color.Blue => 3,
        Color.Purple => 4,
    }
}
'''
        codes = _compile_error_codes(src)
        assert "E0220" in codes

    def test_wrong_enum_in_pattern_rejected(self):
        src = '''enum Color { Red, Green, Blue }
enum Other { X, Y, Z }
fn f(c: Color) -> Int {
    return match c {
        Other.X => 1,
        Color.Green => 2,
        Color.Red => 3,
        Color.Blue => 4,
    }
}
'''
        codes = _compile_error_codes(src)
        assert "E0220" in codes

    def test_duplicate_arm_rejected(self):
        src = '''enum Color { Red, Green, Blue }
fn f(c: Color) -> Int {
    return match c {
        Color.Red => 1,
        Color.Red => 2,
        Color.Green => 3,
        Color.Blue => 4,
    }
}
'''
        codes = _compile_error_codes(src)
        assert "E0223" in codes

    def test_arm_type_mismatch_rejected(self):
        src = '''enum Color { Red, Green, Blue }
fn f(c: Color) -> Int {
    return match c {
        Color.Red => 1,
        Color.Green => "x",
        Color.Blue => 3,
    }
}
'''
        codes = _compile_error_codes(src)
        assert "E0222" in codes

    def test_scrutinee_not_enum_rejected(self):
        src = '''fn f(x: Int) -> Int {
    return match x {
        _ => 1,
    }
}
'''
        codes = _compile_error_codes(src)
        assert "E0221" in codes

    def test_wildcard_before_variants_rejected(self):
        src = '''enum Color { Red, Green, Blue }
fn f(c: Color) -> Int {
    return match c {
        _ => 99,
        Color.Red => 1,
    }
}
'''
        codes = _compile_error_codes(src)
        assert "E0223" in codes


# ===========================================================================
# End-to-end enum + match programs
# ===========================================================================


class TestEnumEndToEnd:
    def test_state_machine(self, tmp_path: Path):
        """A simple state machine using enums + match."""

        src = '''enum State { Idle, Running, Stopped }

fn next(state: State) -> State {
    return match state {
        State.Idle => State.Running,
        State.Running => State.Stopped,
        State.Stopped => State.Idle,
    }
}

fn label(state: State) -> String {
    return match state {
        State.Idle => "idle",
        State.Running => "running",
        State.Stopped => "stopped",
    }
}

fn main() -> Int {
    let mut s = State.Idle
    let mut i = 0
    while i < 5 {
        println(label(s))
        s = next(s)
        i = i + 1
    }
    return 0
}
'''
        out = _expect_ok(src, tmp_path)
        assert out == "idle\nrunning\nstopped\nidle\nrunning\n"

    def test_option_like_enum(self, tmp_path: Path):
        """A simple Option-like enum (no payload yet, but demonstrates the pattern)."""

        src = '''enum Result { Ok, Err }

fn divide(a: Int, b: Int) -> Result {
    if b == 0 {
        return Result.Err
    }
    return Result.Ok
}

fn main() -> Int {
    let r = divide(10, 2)
    match r {
        Result.Ok => println("ok"),
        Result.Err => println("err"),
    }
    let r2 = divide(10, 0)
    match r2 {
        Result.Ok => println("ok"),
        Result.Err => println("err"),
    }
    return 0
}
'''
        out = _expect_ok(src, tmp_path)
        assert out == "ok\nerr\n"
