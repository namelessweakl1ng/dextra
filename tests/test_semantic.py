"""Semantic + type-checker unit tests."""
from dextra.diagnostics import DiagnosticEngine, ErrorCode
from dextra.lexer import Lexer, SourceFile
from dextra.parser import Parser
from dextra.semantic import Analyzer


def analyze(src: str):
    diag = DiagnosticEngine()
    sf = SourceFile("test.dx", src)
    toks = Lexer(sf, diag).tokenize()
    prog = Parser(toks, diag).parse_program()
    a = Analyzer(diag)
    analyzed = a.analyze(prog)
    return analyzed, diag


def error_codes(diag) -> set[str]:
    return {d.code.value for d in diag.diagnostics if d.severity.value == "error"}


def test_let_with_inferred_int():
    analyzed, diag = analyze("fn f() -> Int { let x = 10 return x }")
    assert not diag.has_errors


def test_let_with_inferred_string():
    analyzed, diag = analyze('fn f() -> Int { let s = "hi" return 0 }')
    assert not diag.has_errors


def test_let_with_inferred_bool():
    analyzed, diag = analyze("fn f() -> Int { let b = true return 0 }")
    assert not diag.has_errors


def test_let_with_inferred_float():
    analyzed, diag = analyze("fn f() -> Int { let x = 1.5 return 0 }")
    assert not diag.has_errors


def test_annotation_mismatch_int_string():
    _, diag = analyze('fn f() -> Int { let x: Int = "hello" return 0 }')
    assert "E0217" in error_codes(diag)


def test_immutable_assignment_error():
    _, diag = analyze("fn f() -> Int { let x = 10 x = 20 return x }")
    assert "E0301" in error_codes(diag)


def test_mutable_assignment_ok():
    _, diag = analyze("fn f() -> Int { let mut x = 10 x = 20 return x }")
    assert not diag.has_errors


def test_unknown_identifier():
    _, diag = analyze("fn f() -> Int { return missing_var }")
    assert "E0100" in error_codes(diag)


def test_duplicate_declaration():
    _, diag = analyze("fn f() -> Int { let x = 1 let x = 2 return x }")
    assert "E0101" in error_codes(diag)


def test_unknown_type_annotation():
    _, diag = analyze("fn f() -> Int { let x: Bogus = 0 return x }")
    assert "E0102" in error_codes(diag)


def test_main_must_return_int():
    _, diag = analyze("fn main() -> Void { }")
    assert "E0401" in error_codes(diag)


def test_main_returning_int_ok():
    _, diag = analyze("fn main() -> Int { return 0 }")
    assert not diag.has_errors


def test_unknown_function():
    _, diag = analyze("fn f() -> Int { return missing(1) }")
    assert "E0105" in error_codes(diag)


def test_wrong_argument_count():
    _, diag = analyze("fn add(a: Int, b: Int) -> Int { return a + b }\nfn main() -> Int { return add(1) }")
    assert "E0202" in error_codes(diag)


def test_wrong_argument_type():
    _, diag = analyze('fn add(a: Int, b: Int) -> Int { return a + b }\nfn main() -> Int { return add(1, "x") }')
    assert "E0202" in error_codes(diag)


def test_condition_must_be_bool():
    _, diag = analyze("fn f() -> Int { if 123 { return 1 } return 0 }")
    assert "E0203" in error_codes(diag)


def test_break_outside_loop():
    _, diag = analyze("fn f() -> Int { break return 0 }")
    assert "E0104" in error_codes(diag)


def test_continue_outside_loop():
    _, diag = analyze("fn f() -> Int { continue return 0 }")
    assert "E0104" in error_codes(diag)


def test_break_inside_loop():
    _, diag = analyze("fn f() -> Int { while true { break } return 0 }")
    assert not diag.has_errors


def test_int_plus_string_is_error():
    _, diag = analyze('fn f() -> Int { let x = 1 + "a" return 0 }')
    assert "E0205" in error_codes(diag)


def test_string_plus_string_ok():
    _, diag = analyze('fn f() -> Int { let s = "a" + "b" return 0 }')
    assert not diag.has_errors


def test_struct_unknown_field():
    _, diag = analyze("struct U { name: String }\nfn f() -> Int { let u = U { name: \"x\", bogus: 1 } return 0 }")
    assert "E0210" in error_codes(diag)


def test_struct_missing_field():
    _, diag = analyze("struct U { name: String, age: Int }\nfn f() -> Int { let u = U { name: \"x\" } return 0 }")
    assert "E0209" in error_codes(diag)


def test_array_heterogeneous_error():
    _, diag = analyze('fn f() -> Int { let xs = [1, "x", 2] return 0 }')
    assert "E0212" in error_codes(diag)


def test_array_access_on_non_array():
    _, diag = analyze("fn f() -> Int { let x = 5 return x[0] }")
    assert "E0206" in error_codes(diag)


def test_array_index_must_be_int():
    _, diag = analyze("fn f() -> Int { let xs = [1,2,3] return xs[\"x\"] }")
    assert "E0207" in error_codes(diag)


def test_field_access_on_non_struct():
    _, diag = analyze("fn f() -> Int { let x = 5 return x.name }")
    assert "E0208" in error_codes(diag)


def test_return_type_mismatch():
    _, diag = analyze("fn f() -> Int { return \"hello\" }")
    assert "E0214" in error_codes(diag)


def test_return_value_in_void_function():
    _, diag = analyze("fn f() -> Void { return 1 }")
    assert "E0215" in error_codes(diag)


def test_shadow_in_inner_scope():
    _, diag = analyze("""
fn f() -> Int {
    let x = 10
    if true {
        let x = 20
    }
    return x
}
""")
    assert not diag.has_errors


def test_unknown_variable_in_inner_scope_after_exit():
    _, diag = analyze("""
fn f() -> Int {
    if true {
        let x = 20
    }
    return x
}
""")
    assert "E0100" in error_codes(diag)


def test_for_range_loop():
    _, diag = analyze("fn f() -> Int { for i in 0..10 { println(i) } return 0 }")
    assert not diag.has_errors


def test_for_array_loop():
    _, diag = analyze("fn f() -> Int { let xs = [1,2,3] for x in xs { println(x) } return 0 }")
    assert not diag.has_errors


def test_factorial_compiles():
    _, diag = analyze("""
fn factorial(n: Int) -> Int {
    if n <= 1 { return 1 }
    return n * factorial(n - 1)
}
""")
    assert not diag.has_errors
