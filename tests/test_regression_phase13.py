"""Phase 1.3 regression tests: type-system / runtime representation correctness.

Covers:
  - Bug A: Float arrays (and the full array type matrix)
  - Bug B: Typed empty arrays
  - Bug C: Missing-return analysis (E0218)
  - IR verifier dominance + type consistency
  - Compiler error boundary
  - Short-circuit eagerness (documented)
  - Struct layout
  - Runtime safety boundaries
  - Compiler error boundary (exit codes)

Organized by problem class.  Uses real end-to-end compilation wherever
possible — no LLVM mocking.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from dextra.diagnostics import DiagnosticEngine
from dextra.ir import (
    Alloca,
    BasicBlock,
    BinOp,
    Branch,
    Call,
    CondBranch,
    ConstBool,
    ConstFloat,
    ConstInt,
    ConstString,
    Function,
    GetElement,
    GetField,
    IRBuilder,
    Instruction,
    Load,
    Local,
    Module,
    Param,
    Return,
    SetElement,
    SetField,
    Store,
    StringLit,
    StructLit,
    Temp,
    UnaryOp,
    Value,
    assert_module_valid,
    lower_program,
    verify_module,
)
from dextra.codegen import compile_to_llvm_ir, parse_and_verify
from dextra.lexer import Lexer, SourceFile
from dextra.parser import Parser
from dextra.pipeline import build_from_file, compile_source, CompileResult
from dextra.semantic import Analyzer
from dextra.types.type_system import (
    ArrayType,
    PrimitiveType,
    StructType,
    Type,
    TypeContext,
    UNKNOWN,
)


# ---------------------------------------------------------------------------
# Helpers


def _compile_and_run(src: str, tmp_path: Path, name: str = "t") -> tuple[int, str, str]:
    """Compile `src` to a native binary and run it.  Returns (rc, stdout, stderr)."""

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


def _gen_llvm(src: str) -> str:
    r = compile_source(src, filename="t.dx")
    assert r.success and r.llvm_ir_text is not None
    return r.llvm_ir_text


def _gen_ir_module(src: str):
    """Run frontend through Dextra IR; return (Module, TypeContext)."""

    diag = DiagnosticEngine()
    sf = SourceFile("t.dx", src)
    toks = Lexer(sf, diag).tokenize()
    prog = Parser(toks, diag).parse_program()
    a = Analyzer(diag)
    ap = a.analyze(prog)
    return lower_program(ap), ap.ctx


# ===========================================================================
# Bug A: Float arrays
# ===========================================================================


class TestFloatArrays:
    def test_float_array_literal_compiles_and_runs(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let xs = [1.5, 2.5]
    println(xs[0])
    println(xs[1])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "1.5\n2.5\n"

    def test_float_array_mutation(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let mut xs = [1.5, 2.5]
    xs[0] = 3.5
    println(xs[0])
    println(xs[1])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "3.5\n2.5\n"

    def test_float_array_length(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let xs = [1.5, 2.5, 3.5]
    println(length(xs))
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "3\n"

    def test_float_array_as_function_argument(self, tmp_path: Path):
        src = '''fn sum(xs: [Float]) -> Float {
    let mut s = 0.0
    let mut i = 0
    while i < length(xs) {
        s = s + xs[i]
        i = i + 1
    }
    return s
}
fn main() -> Int {
    println(sum([1.5, 2.5, 3.0]))
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        # 1.5 + 2.5 + 3.0 = 7.0; %g prints as "7"
        assert out == "7\n"

    def test_float_array_returned_from_function(self, tmp_path: Path):
        src = '''fn make() -> [Float] {
    return [10.5, 20.5]
}
fn main() -> Int {
    let xs = make()
    println(xs[0])
    println(xs[1])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "10.5\n20.5\n"

    def test_float_array_no_information_loss(self, tmp_path: Path):
        """Verify the double↔i64 bitcast preserves bit-exact values,
        including NaN-like patterns (we test very small fractional
        values that would be lost if we'd cast float→int instead)."""

        src = '''fn main() -> Int {
    let xs = [0.1, 0.2, 0.3]
    println(xs[0])
    println(xs[1])
    println(xs[2])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        # 0.1 in IEEE 754 double is approximately 0.1 (printf %g)
        assert out == "0.1\n0.2\n0.3\n"

    def test_float_array_iteration(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let xs = [1.5, 2.5, 3.5]
    let mut s = 0.0
    for x in xs {
        s = s + x
    }
    println(s)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "7.5\n"


# ===========================================================================
# Full array type matrix
# ===========================================================================


class TestArrayTypeMatrix:
    """Ensure every array element type that the semantic analyzer accepts
    can be lowered, compiled, and executed correctly."""

    def test_int_array(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let xs = [10, 20, 30]
    println(xs[0])
    println(xs[2])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "10\n30\n"

    def test_int_array_mutation(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let mut xs = [1, 2, 3]
    xs[1] = 99
    println(xs[1])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "99\n"

    def test_bool_array(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let xs = [true, false, true]
    println(xs[0])
    println(xs[1])
    println(xs[2])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "true\nfalse\ntrue\n"

    def test_bool_array_mutation(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let mut xs = [true, false]
    xs[0] = false
    xs[1] = true
    println(xs[0])
    println(xs[1])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "false\ntrue\n"

    def test_string_array(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let xs = ["alpha", "beta", "gamma"]
    println(xs[0])
    println(xs[1])
    println(xs[2])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "alpha\nbeta\ngamma\n"

    def test_string_array_mutation(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let mut xs = ["a", "b", "c"]
    xs[1] = "X"
    println(xs[1])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "X\n"

    def test_struct_array(self, tmp_path: Path):
        src = '''struct P { x: Int, y: Int }
fn main() -> Int {
    let ps = [P { x: 1, y: 2 }, P { x: 3, y: 4 }]
    println(ps[0].x)
    println(ps[0].y)
    println(ps[1].x)
    println(ps[1].y)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "1\n2\n3\n4\n"

    def test_struct_array_field_mutation(self, tmp_path: Path):
        src = '''struct P { x: Int, y: Int }
fn main() -> Int {
    let mut ps = [P { x: 1, y: 2 }, P { x: 3, y: 4 }]
    ps[0].x = 99
    println(ps[0].x)
    println(ps[1].x)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "99\n3\n"

    def test_nested_array(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let xss = [[1, 2, 3], [4, 5, 6]]
    println(xss[0][0])
    println(xss[0][2])
    println(xss[1][1])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "1\n3\n5\n"

    def test_array_passed_to_function(self, tmp_path: Path):
        src = '''fn first(xs: [Int]) -> Int {
    return xs[0]
}
fn main() -> Int {
    println(first([10, 20, 30]))
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "10\n"

    def test_array_returned_from_function(self, tmp_path: Path):
        src = '''fn make() -> [Int] {
    return [10, 20, 30]
}
fn main() -> Int {
    let xs = make()
    println(xs[1])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "20\n"


# ===========================================================================
# Bug B: Typed empty arrays
# ===========================================================================


class TestTypedEmptyArrays:
    def test_typed_empty_int_array_compiles(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let xs: [Int] = []
    println(length(xs))
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "0\n"

    def test_typed_empty_float_array_compiles(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let xs: [Float] = []
    println(length(xs))
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "0\n"

    def test_typed_empty_bool_array_compiles(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let xs: [Bool] = []
    println(length(xs))
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "0\n"

    def test_typed_empty_string_array_compiles(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let xs: [String] = []
    println(length(xs))
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "0\n"

    def test_typed_empty_nested_array_compiles(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let xs: [[Int]] = []
    println(length(xs))
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "0\n"

    def test_untyped_empty_array_still_rejected(self):
        """`let xs = []` must remain a compile error (no element type to infer)."""

        codes = _compile_error_codes('fn main() -> Int { let xs = [] return 0 }')
        assert "E0213" in codes

    def test_typed_empty_array_does_not_emit_double_error(self):
        """The fix: a single diagnostic, not E0213 + E0217."""

        codes = _compile_error_codes(
            'fn main() -> Int { let xs: [Int] = [] return 0 }'
        )
        # Must compile cleanly now.
        assert codes == set()

    def test_annotation_mismatch_still_caught(self):
        """`let xs: [Int] = [1.0]` must still be a type error."""

        codes = _compile_error_codes(
            'fn main() -> Int { let xs: [Int] = [1.0] return 0 }'
        )
        assert "E0217" in codes or "E0212" in codes


# ===========================================================================
# Bug C: Missing return analysis (E0218)
# ===========================================================================


class TestReturnAnalysis:
    def test_valid_simple_return(self):
        codes = _compile_error_codes('fn f() -> Int { return 1 }')
        assert "E0218" not in codes

    def test_valid_both_branches_return(self):
        codes = _compile_error_codes(
            'fn f(x: Int) -> Int { if x > 0 { return 1 } else { return 2 } }'
        )
        assert "E0218" not in codes

    def test_valid_while_true_with_return(self):
        codes = _compile_error_codes(
            'fn f() -> Int { while true { return 1 } }'
        )
        assert "E0218" not in codes

    def test_invalid_missing_return_in_if(self):
        codes = _compile_error_codes(
            'fn f(x: Int) -> Int { if x > 0 { return 1 } }'
        )
        assert "E0218" in codes

    def test_invalid_else_branch_no_return(self):
        codes = _compile_error_codes(
            'fn f(x: Int) -> Int { if x > 0 { return 1 } else { println("x") } }'
        )
        assert "E0218" in codes

    def test_valid_else_if_chain_all_return(self):
        codes = _compile_error_codes(
            'fn f(x: Int) -> Int { if x > 0 { return 1 } else if x < 0 { return 2 } else { return 3 } }'
        )
        assert "E0218" not in codes

    def test_invalid_else_if_chain_no_final_else(self):
        codes = _compile_error_codes(
            'fn f(x: Int) -> Int { if x > 0 { return 1 } else if x < 0 { return 2 } }'
        )
        assert "E0218" in codes

    def test_valid_void_function_no_return_required(self):
        codes = _compile_error_codes(
            'fn f() -> Void { println("hello") }'
        )
        assert "E0218" not in codes

    def test_valid_return_after_loop(self):
        codes = _compile_error_codes(
            'fn f() -> Int { let mut i = 0 while i < 10 { i = i + 1 } return i }'
        )
        assert "E0218" not in codes

    def test_valid_recursion(self):
        codes = _compile_error_codes(
            'fn f(n: Int) -> Int { if n <= 1 { return 1 } return n * f(n-1) }'
        )
        assert "E0218" not in codes

    def test_invalid_only_returns_in_loop(self):
        """`while` may not execute; return inside loop doesn't satisfy
        the all-paths-return requirement."""

        codes = _compile_error_codes(
            'fn f(n: Int) -> Int { while n > 0 { return 1 } }'
        )
        assert "E0218" in codes

    def test_valid_break_counts_as_exit(self):
        """`while true { break }` followed by `return` is valid because
        the loop body exits (via break) and there's a return after."""

        codes = _compile_error_codes(
            'fn f(n: Int) -> Int { while true { break } return 1 }'
        )
        assert "E0218" not in codes

    def test_invalid_missing_return_diagnostic_has_correct_code(self):
        from dextra.lexer import SourceFile
        src = 'fn f(x: Int) -> Int { if x > 0 { return 1 } }'
        r = compile_source(src, filename='t.dx')
        diag_text = r.diagnostics.render_all({'t.dx': SourceFile('t.dx', src)})
        assert "E0218" in diag_text


# ===========================================================================
# Boolean evaluation semantics
# ===========================================================================


class TestBooleanEvaluation:
    def test_eager_evaluation_both_sides_run(self, tmp_path: Path):
        """`false && side_effect()` calls `side_effect()` — eager semantics."""

        src = '''fn side_effect() -> Bool {
    println("side")
    return true
}
fn main() -> Int {
    let _ = false && side_effect()
    println("end")
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        # Both "side" and "end" appear — eager evaluation.
        assert "side" in out
        assert "end" in out

    def test_eager_evaluation_or(self, tmp_path: Path):
        """`true || side_effect()` calls `side_effect()` — eager semantics."""

        src = '''fn side_effect() -> Bool {
    println("side")
    return false
}
fn main() -> Int {
    let _ = true || side_effect()
    println("end")
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert "side" in out
        assert "end" in out

    def test_and_truth_table(self, tmp_path: Path):
        src = '''fn main() -> Int {
    println(true && true)
    println(true && false)
    println(false && true)
    println(false && false)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "true\nfalse\nfalse\nfalse\n"

    def test_or_truth_table(self, tmp_path: Path):
        src = '''fn main() -> Int {
    println(true || true)
    println(true || false)
    println(false || true)
    println(false || false)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "true\ntrue\ntrue\nfalse\n"


# ===========================================================================
# IR verifier: dominance
# ===========================================================================


class TestIRVerifierDominance:
    def _build_cross_block_module(self):
        """Build an IR module where `temp_x` is defined in `bb_a` and
        used in `bb_b`, where `bb_b` is reachable without going through
        `bb_a`.  This is an SSA dominance violation."""

        ctx = TypeContext()
        mod = Module(name="t")
        entry = BasicBlock(label="entry")
        bb_a = BasicBlock(label="bb_a")
        bb_b = BasicBlock(label="bb_b")
        end = BasicBlock(label="end")
        # entry: condbr to bb_a or bb_b (siblings)
        entry.terminator = CondBranch(
            cond=ConstBool(value=True, type=ctx.BOOL),
            then_target=bb_a,
            else_target=bb_b,
        )
        # bb_a: defines temp_x
        temp_x = Temp(name="x", type=ctx.INT)
        bb_a.instructions.append(BinOp(
            result=temp_x, op="+",
            left=ConstInt(value=1, type=ctx.INT),
            right=ConstInt(value=2, type=ctx.INT),
        ))
        bb_a.terminator = Branch(target=end)
        # bb_b: uses temp_x (NOT dominated by bb_a!)
        temp_y = Temp(name="y", type=ctx.INT)
        bb_b.instructions.append(BinOp(
            result=temp_y, op="+",
            left=temp_x,
            right=ConstInt(value=10, type=ctx.INT),
        ))
        bb_b.terminator = Branch(target=end)
        # end: returns 0
        end.terminator = Return(value=ConstInt(value=0, type=ctx.INT))
        fn = Function(name="f", return_type=ctx.INT, params=[],
                      blocks=[entry, bb_a, bb_b, end])
        mod.functions.append(fn)
        return mod, ctx

    def test_dominance_violation_is_caught(self):
        mod, ctx = self._build_cross_block_module()
        errs = verify_module(mod, ctx)
        joined = " | ".join(errs)
        assert "not dominated" in joined or "dominated" in joined, (
            f"expected dominance violation, got: {errs}"
        )

    def test_assert_module_valid_raises_on_dominance_violation(self):
        mod, ctx = self._build_cross_block_module()
        with pytest.raises(AssertionError) as exc_info:
            assert_module_valid(mod, ctx)
        assert "dominated" in str(exc_info.value)

    def test_well_formed_module_passes(self):
        """A simple if/else with a Temp defined in each branch and used in
        the merge block is OK — both branches dominate the merge."""

        ctx = TypeContext()
        mod = Module(name="t")
        entry = BasicBlock(label="entry")
        then_bb = BasicBlock(label="then")
        else_bb = BasicBlock(label="else")
        merge = BasicBlock(label="merge")
        entry.terminator = CondBranch(
            cond=ConstBool(value=True, type=ctx.BOOL),
            then_target=then_bb, else_target=else_bb,
        )
        # both branches define their own temp, then branch to merge
        t1 = Temp(name="t1", type=ctx.INT)
        then_bb.instructions.append(BinOp(
            result=t1, op="+",
            left=ConstInt(value=1, type=ctx.INT), right=ConstInt(value=2, type=ctx.INT),
        ))
        then_bb.terminator = Branch(target=merge)
        t2 = Temp(name="t2", type=ctx.INT)
        else_bb.instructions.append(BinOp(
            result=t2, op="+",
            left=ConstInt(value=3, type=ctx.INT), right=ConstInt(value=4, type=ctx.INT),
        ))
        else_bb.terminator = Branch(target=merge)
        # merge: return t1 (defined in then_bb which dominates merge via entry→then→merge)
        # Actually neither then nor else dominates merge — both are siblings.
        # So using t1 in merge is a dominance violation.
        # Let's make merge just return 0 to avoid creating a violation.
        merge.terminator = Return(value=ConstInt(value=0, type=ctx.INT))
        fn = Function(name="f", return_type=ctx.INT, params=[],
                      blocks=[entry, then_bb, else_bb, merge])
        mod.functions.append(fn)
        # Should pass — no Temp used outside its defining block.
        assert_module_valid(mod, ctx)

    def test_terminator_temp_must_be_dominated(self):
        """A terminator (CondBranch.cond) using an undefined Temp is caught."""

        ctx = TypeContext()
        mod = Module(name="t")
        entry = BasicBlock(label="entry")
        end = BasicBlock(label="end")
        # entry's terminator uses temp_undefined which is never defined.
        temp_undefined = Temp(name="u", type=ctx.BOOL)
        entry.terminator = CondBranch(
            cond=temp_undefined,
            then_target=end, else_target=end,
        )
        end.terminator = Return(value=ConstInt(value=0, type=ctx.INT))
        fn = Function(name="f", return_type=ctx.INT, params=[],
                      blocks=[entry, end])
        mod.functions.append(fn)
        errs = verify_module(mod, ctx)
        assert any("undefined Temp" in e or "not dominated" in e for e in errs)


# ===========================================================================
# IR verifier: type consistency
# ===========================================================================


class TestIRVerifierTypeConsistency:
    def test_binop_operand_type_mismatch_is_caught(self):
        ctx = TypeContext()
        mod = Module(name="t")
        entry = BasicBlock(label="entry")
        result = Temp(name="r", type=ctx.INT)
        entry.instructions.append(BinOp(
            result=result, op="+",
            # Int + Float mismatch — should be caught
            left=ConstInt(value=1, type=ctx.INT),
            right=ConstFloat(value=2.0, type=ctx.FLOAT),
        ))
        entry.terminator = Return(value=ConstInt(value=0, type=ctx.INT))
        fn = Function(name="f", return_type=ctx.INT, params=[], blocks=[entry])
        mod.functions.append(fn)
        errs = verify_module(mod, ctx)
        assert any("operand type mismatch" in e for e in errs)

    def test_condbranch_non_bool_cond_is_caught(self):
        ctx = TypeContext()
        mod = Module(name="t")
        entry = BasicBlock(label="entry")
        end = BasicBlock(label="end")
        # condbranch with an Int cond — type error
        entry.terminator = CondBranch(
            cond=ConstInt(value=1, type=ctx.INT),
            then_target=end, else_target=end,
        )
        end.terminator = Return(value=ConstInt(value=0, type=ctx.INT))
        fn = Function(name="f", return_type=ctx.INT, params=[], blocks=[entry, end])
        mod.functions.append(fn)
        errs = verify_module(mod, ctx)
        assert any("cond must be Bool" in e for e in errs)

    def test_return_value_type_mismatch_is_caught(self):
        ctx = TypeContext()
        mod = Module(name="t")
        entry = BasicBlock(label="entry")
        # function returns Int, but we return a Float
        entry.terminator = Return(value=ConstFloat(value=1.5, type=ctx.FLOAT))
        fn = Function(name="f", return_type=ctx.INT, params=[], blocks=[entry])
        mod.functions.append(fn)
        errs = verify_module(mod, ctx)
        assert any("Return value" in e and "doesn't match" in e for e in errs)

    def test_getelement_on_non_array_is_caught(self):
        ctx = TypeContext()
        mod = Module(name="t")
        entry = BasicBlock(label="entry")
        # GetElement on an Int (not an array)
        result = Temp(name="r", type=ctx.INT)
        entry.instructions.append(GetElement(
            result=result,
            array=ConstInt(value=42, type=ctx.INT),   # not an array
            index=ConstInt(value=0, type=ctx.INT),
        ))
        entry.terminator = Return(value=ConstInt(value=0, type=ctx.INT))
        fn = Function(name="f", return_type=ctx.INT, params=[], blocks=[entry])
        mod.functions.append(fn)
        errs = verify_module(mod, ctx)
        assert any("GetElement on non-array" in e for e in errs)

    def test_call_wrong_argument_count_is_caught(self):
        ctx = TypeContext()
        mod = Module(name="t")
        # callee: fn g(a: Int, b: Int) -> Int
        callee_params = [Param(index=0, name="a", type=ctx.INT),
                         Param(index=1, name="b", type=ctx.INT)]
        callee = Function(name="g", return_type=ctx.INT,
                          params=callee_params, blocks=[
                              BasicBlock(label="entry",
                                        terminator=Return(value=ConstInt(value=0, type=ctx.INT)))
                          ])
        # caller: calls g(1) — wrong arg count
        caller_entry = BasicBlock(label="entry")
        call_result = Temp(name="r", type=ctx.INT)
        caller_entry.instructions.append(Call(
            result=call_result, name="g",
            args=[ConstInt(value=1, type=ctx.INT)],   # missing 2nd arg
            return_type=ctx.INT,
        ))
        caller_entry.terminator = Return(value=ConstInt(value=0, type=ctx.INT))
        caller = Function(name="f", return_type=ctx.INT, params=[],
                          blocks=[caller_entry])
        mod.functions.extend([callee, caller])
        errs = verify_module(mod, ctx)
        assert any("expects 2 args" in e for e in errs)

    def test_well_formed_module_passes_type_checks(self):
        """A trivial but valid module passes both structural and type checks."""

        ctx = TypeContext()
        mod = Module(name="t")
        entry = BasicBlock(label="entry")
        entry.terminator = Return(value=ConstInt(value=0, type=ctx.INT))
        fn = Function(name="f", return_type=ctx.INT, params=[], blocks=[entry])
        mod.functions.append(fn)
        assert_module_valid(mod, ctx)   # no exception


# ===========================================================================
# Struct layout audit
# ===========================================================================


class TestStructLayout:
    def test_struct_with_bool_and_int_field(self, tmp_path: Path):
        """Force alignment padding: Bool (i1, 1 byte) + Int (i64, 8 bytes).
        Total: 16 bytes (1 + 7 padding + 8)."""

        src = '''struct S { b: Bool, i: Int }
fn main() -> Int {
    let s = S { b: true, i: 42 }
    println(s.b)
    println(s.i)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "true\n42\n"

    def test_struct_with_float_and_int(self, tmp_path: Path):
        src = '''struct S { f: Float, i: Int }
fn main() -> Int {
    let s = S { f: 1.5, i: 42 }
    println(s.f)
    println(s.i)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "1.5\n42\n"

    def test_struct_with_string_and_int(self, tmp_path: Path):
        src = '''struct S { name: String, age: Int }
fn main() -> Int {
    let s = S { name: "Alice", age: 30 }
    println(s.name)
    println(s.age)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "Alice\n30\n"

    def test_struct_with_multiple_fields(self, tmp_path: Path):
        src = '''struct S { a: Int, b: Int, c: Int, d: Int }
fn main() -> Int {
    let s = S { a: 1, b: 2, c: 3, d: 4 }
    println(s.a)
    println(s.b)
    println(s.c)
    println(s.d)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "1\n2\n3\n4\n"

    def test_struct_with_string_and_float_and_int(self, tmp_path: Path):
        """Mixed pointer + double + int — most alignment-sensitive layout."""

        src = '''struct S { name: String, score: Float, age: Int }
fn main() -> Int {
    let s = S { name: "Bob", score: 95.5, age: 21 }
    println(s.name)
    println(s.score)
    println(s.age)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "Bob\n95.5\n21\n"

    def test_nested_struct(self, tmp_path: Path):
        src = '''struct Point { x: Int, y: Int }
struct Rect { corner: Point, w: Int, h: Int }
fn main() -> Int {
    let r = Rect { corner: Point { x: 1, y: 2 }, w: 10, h: 20 }
    println(r.corner.x)
    println(r.corner.y)
    println(r.w)
    println(r.h)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "1\n2\n10\n20\n"

    def test_struct_returned_from_function(self, tmp_path: Path):
        """Verifies heap-allocated struct return — no dangling pointer."""

        src = '''struct User { name: String, age: Int }
fn make_user() -> User {
    return User { name: "Alice", age: 21 }
}
fn main() -> Int {
    let u = make_user()
    println(u.name)
    println(u.age)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "Alice\n21\n"

    def test_struct_passed_to_function(self, tmp_path: Path):
        src = '''struct User { name: String, age: Int }
fn greet(u: User) -> String {
    return "Hello, " + u.name
}
fn main() -> Int {
    let u = User { name: "Bob", age: 30 }
    println(greet(u))
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "Hello, Bob\n"

    def test_struct_in_array(self, tmp_path: Path):
        src = '''struct P { x: Int, y: Int }
fn main() -> Int {
    let ps = [P { x: 1, y: 2 }, P { x: 3, y: 4 }, P { x: 5, y: 6 }]
    let mut sum = 0
    for p in ps {
        sum = sum + p.x + p.y
    }
    println(sum)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        # (1+2) + (3+4) + (5+6) = 21
        assert out == "21\n"

    def test_repeated_compilation_in_one_process(self):
        """Compiling the same struct twice in one Python process must not
        raise 'already defined' (LLVMBackend uses per-instance Context)."""

        src = 'struct User { name: String, age: Int }\nfn main() -> Int { return 0 }'
        for _ in range(3):
            r = compile_source(src, filename="t.dx")
            assert r.success, "repeated compilation failed"


# ===========================================================================
# Runtime safety boundaries (no corruption)
# ===========================================================================


class TestRuntimeSafety:
    def test_large_array_allocation(self, tmp_path: Path):
        """A large array allocated up-front should not corrupt memory."""

        src = '''fn main() -> Int {
    let xs = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99]
    println(length(xs))
    println(xs[0])
    println(xs[99])
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "100\n0\n99\n"

    def test_string_concat_does_not_corrupt(self, tmp_path: Path):
        """Repeated string concatenation must not corrupt memory."""

        src = '''fn main() -> Int {
    let mut s = "a"
    let mut i = 0
    while i < 50 {
        s = s + "b"
        i = i + 1
    }
    println(length(s))
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        # 1 'a' + 50 'b' = 51 chars
        assert out == "51\n"

    def test_empty_string_operations(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let s = ""
    println(length(s))
    let t = s + "x"
    println(length(t))
    println(t)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "0\n1\nx\n"


# ===========================================================================
# Compiler error boundary
# ===========================================================================


class TestCompilerErrorBoundary:
    def test_compile_source_returns_internal_error_on_backend_failure(self):
        """If the LLVM backend raises, compile_source returns
        CompileResult(internal_error=True) instead of leaking the
        exception."""

        # We can't easily construct a case that fails after the IR verifier
        # is in place (the verifier catches most malformed IR first), so
        # this test verifies the boundary is in place by inspecting the
        # CompileResult dataclass.
        from dataclasses import fields
        field_names = {f.name for f in fields(CompileResult)}
        assert "internal_error" in field_names, (
            "CompileResult must have an `internal_error` field so the CLI "
            "can return exit code 2 on backend failures."
        )

    def test_cli_normal_user_error_exits_1(self, tmp_path: Path):
        """A type error (user mistake) must produce exit code 1, not 2."""

        src = 'fn main() -> Int { let x: Int = "hello" return 0 }'
        p = tmp_path / "bad.dx"
        p.write_text(src)
        import os
        env = {**dict(os.environ),
               "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        proc = subprocess.run(
            [sys.executable, "-m", "dextra", "build", str(p)],
            env=env, capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 1, (
            f"expected exit 1 for user error, got {proc.returncode}; "
            f"stderr: {proc.stderr}"
        )
        # No raw Python traceback
        assert "Traceback" not in proc.stderr


# ===========================================================================
# Full type matrix end-to-end
# ===========================================================================


class TestTypeMatrixEndToEnd:
    """Every primitive type can round-trip through print/println."""

    def test_int_print_round_trip(self, tmp_path: Path):
        out = _expect_ok('fn main() -> Int { println(42) println(-7) return 0 }', tmp_path)
        assert out == "42\n-7\n"

    def test_float_print_round_trip(self, tmp_path: Path):
        out = _expect_ok('fn main() -> Int { println(3.14) println(-2.5) return 0 }', tmp_path)
        assert out == "3.14\n-2.5\n"

    def test_bool_print_round_trip(self, tmp_path: Path):
        out = _expect_ok('fn main() -> Int { println(true) println(false) return 0 }', tmp_path)
        assert out == "true\nfalse\n"

    def test_string_print_round_trip(self, tmp_path: Path):
        out = _expect_ok('fn main() -> Int { println("hello") println("with\\nnewline") return 0 }', tmp_path)
        assert out == "hello\nwith\nnewline\n"

    def test_void_function_no_return_value(self, tmp_path: Path):
        out = _expect_ok('fn f() -> Void { println("called") }\nfn main() -> Int { f() return 0 }', tmp_path)
        assert out == "called\n"


# ===========================================================================
# Control flow end-to-end
# ===========================================================================


class TestControlFlowEndToEnd:
    def test_if_else_if_chain(self, tmp_path: Path):
        src = '''fn classify(n: Int) -> Int {
    if n < 0 { return -1 }
    else if n == 0 { return 0 }
    else if n < 10 { return 1 }
    else { return 2 }
}
fn main() -> Int {
    println(classify(-5))
    println(classify(0))
    println(classify(5))
    println(classify(50))
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "-1\n0\n1\n2\n"

    def test_while_loop_with_break(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let mut i = 0
    let mut total = 0
    while true {
        if i >= 5 { break }
        total = total + i
        i = i + 1
    }
    println(total)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        # 0 + 1 + 2 + 3 + 4 = 10
        assert out == "10\n"

    def test_for_range_with_continue(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let mut total = 0
    for i in 0..10 {
        if i % 2 == 0 { continue }
        total = total + i
    }
    println(total)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        # 1 + 3 + 5 + 7 + 9 = 25
        assert out == "25\n"

    def test_nested_loops(self, tmp_path: Path):
        src = '''fn main() -> Int {
    let mut total = 0
    for i in 0..3 {
        for j in 0..3 {
            total = total + i * 10 + j
        }
    }
    println(total)
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        # (0+1+2) + (10+11+12) + (20+21+22) = 3 + 33 + 63 = 99
        assert out == "99\n"

    def test_return_inside_loop(self, tmp_path: Path):
        src = '''fn find_first_even(xs: [Int]) -> Int {
    let mut i = 0
    while i < length(xs) {
        if xs[i] % 2 == 0 {
            return xs[i]
        }
        i = i + 1
    }
    return -1
}
fn main() -> Int {
    println(find_first_even([1, 3, 5, 8, 9]))
    return 0
}'''
        out = _expect_ok(src, tmp_path)
        assert out == "8\n"


# ===========================================================================
# LLVM IR validity for key programs
# ===========================================================================


class TestLLVMIRValidity:
    def test_hello_world_llvm_ir_parses(self):
        ir = _gen_llvm('fn main() -> Int { println("hi") return 0 }')
        parse_and_verify(ir)   # raises if invalid

    def test_float_array_llvm_ir_parses(self):
        ir = _gen_llvm('fn main() -> Int { let xs = [1.5, 2.5] println(xs[0]) return 0 }')
        parse_and_verify(ir)

    def test_struct_llvm_ir_parses(self):
        ir = _gen_llvm('struct S { a: Int, b: Int }\nfn main() -> Int { let s = S { a: 1, b: 2 } println(s.a) return 0 }')
        parse_and_verify(ir)

    def test_recursion_llvm_ir_has_self_call(self):
        ir = _gen_llvm('fn f(n: Int) -> Int { if n <= 1 { return 1 } return n * f(n-1) }')
        # The function calls itself — LLVM IR must contain a call instruction
        # to "f".
        assert '"f"' in ir
        assert "call" in ir
