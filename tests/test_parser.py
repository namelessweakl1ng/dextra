"""Parser unit tests."""
from dextra.ast import nodes as ast
from dextra.diagnostics import DiagnosticEngine
from dextra.lexer import Lexer, SourceFile
from dextra.parser import Parser


def parse(src: str):
    diag = DiagnosticEngine()
    sf = SourceFile("test.dx", src)
    toks = Lexer(sf, diag).tokenize()
    prog = Parser(toks, diag).parse_program()
    return prog, diag


def test_simple_function():
    prog, diag = parse("fn f() -> Int { return 1 }")
    assert not diag.has_errors
    assert len(prog.declarations) == 1
    fn = prog.declarations[0]
    assert isinstance(fn, ast.FunctionDeclaration)
    assert fn.name == "f"
    assert len(fn.parameters) == 0
    assert isinstance(fn.return_type, ast.PrimitiveTypeExpr)
    assert fn.return_type.name == "Int"


def test_function_with_params():
    prog, diag = parse("fn add(a: Int, b: Int) -> Int { return a + b }")
    assert not diag.has_errors
    fn = prog.declarations[0]
    assert len(fn.parameters) == 2
    assert fn.parameters[0].name == "a"
    assert fn.parameters[1].name == "b"


def test_struct_declaration():
    prog, diag = parse("struct User { name: String, age: Int }")
    assert not diag.has_errors
    sd = prog.declarations[0]
    assert isinstance(sd, ast.StructDeclaration)
    assert sd.name == "User"
    assert len(sd.fields) == 2
    assert sd.fields[0].name == "name"


def test_let_statement_with_annotation():
    prog, diag = parse("fn f() -> Int { let x: Int = 10 return x }")
    assert not diag.has_errors
    fn = prog.declarations[0]
    stmt = fn.body.statements[0]
    assert isinstance(stmt, ast.LetStatement)
    assert stmt.name == "x"
    assert not stmt.mutable
    assert stmt.type_annotation is not None


def test_let_statement_mutable():
    prog, diag = parse("fn f() -> Int { let mut x = 10 return x }")
    assert not diag.has_errors
    fn = prog.declarations[0]
    stmt = fn.body.statements[0]
    assert isinstance(stmt, ast.LetStatement)
    assert stmt.mutable


def test_operator_precedence():
    prog, diag = parse("fn f() -> Int { return 1 + 2 * 3 }")
    assert not diag.has_errors
    fn = prog.declarations[0]
    ret = fn.body.statements[0]
    assert isinstance(ret, ast.ReturnStatement)
    add = ret.value
    assert isinstance(add, ast.BinaryOp)
    assert add.op == "+"
    mul = add.right
    assert isinstance(mul, ast.BinaryOp)
    assert mul.op == "*"


def test_paren_override_precedence():
    prog, diag = parse("fn f() -> Int { return (1 + 2) * 3 }")
    assert not diag.has_errors
    fn = prog.declarations[0]
    ret = fn.body.statements[0]
    mul = ret.value
    assert isinstance(mul, ast.BinaryOp)
    assert mul.op == "*"
    add = mul.left
    assert isinstance(add, ast.BinaryOp)
    assert add.op == "+"


def test_unary_minus():
    prog, diag = parse("fn f() -> Int { return -5 }")
    assert not diag.has_errors
    ret = prog.declarations[0].body.statements[0]
    assert isinstance(ret.value, ast.UnaryOp)
    assert ret.value.op == "-"


def test_unary_not():
    prog, diag = parse("fn f() -> Int { let x = !true return 0 }")
    assert not diag.has_errors
    stmt = prog.declarations[0].body.statements[0]
    assert isinstance(stmt, ast.LetStatement)
    assert isinstance(stmt.value, ast.UnaryOp)
    assert stmt.value.op == "!"


def test_call_expression():
    prog, diag = parse("fn f() -> Int { println(1) return 0 }")
    assert not diag.has_errors
    stmt = prog.declarations[0].body.statements[0]
    assert isinstance(stmt, ast.ExpressionStatement)
    call = stmt.expression
    assert isinstance(call, ast.CallExpression)
    assert len(call.arguments) == 1


def test_if_else():
    prog, diag = parse("fn f() -> Int { if x { return 1 } else { return 2 } }")
    assert not diag.has_errors
    stmt = prog.declarations[0].body.statements[0]
    assert isinstance(stmt, ast.IfStatement)
    assert stmt.else_branch is not None
    assert isinstance(stmt.else_branch, ast.Block)


def test_if_else_if_chain():
    prog, diag = parse("fn f() -> Int { if x { return 1 } else if y { return 2 } else { return 3 } }")
    assert not diag.has_errors
    stmt = prog.declarations[0].body.statements[0]
    assert isinstance(stmt, ast.IfStatement)
    assert isinstance(stmt.else_branch, ast.IfStatement)


def test_while_loop():
    prog, diag = parse("fn f() -> Int { while x < 10 { x = x + 1 } return 0 }")
    assert not diag.has_errors
    stmt = prog.declarations[0].body.statements[0]
    assert isinstance(stmt, ast.WhileStatement)


def test_for_range():
    prog, diag = parse("fn f() -> Int { for i in 0..10 { println(i) } return 0 }")
    assert not diag.has_errors
    stmt = prog.declarations[0].body.statements[0]
    assert isinstance(stmt, ast.ForStatement)
    assert isinstance(stmt.iterable, ast.RangeExpression)


def test_array_literal():
    prog, diag = parse("fn f() -> Int { let xs = [1, 2, 3] return 0 }")
    assert not diag.has_errors
    stmt = prog.declarations[0].body.statements[0]
    assert isinstance(stmt, ast.LetStatement)
    assert isinstance(stmt.value, ast.ArrayLiteral)
    assert len(stmt.value.elements) == 3


def test_struct_literal():
    prog, diag = parse("""
struct User { name: String, age: Int }
fn f() -> Int {
    let u = User { name: "Alice", age: 21 }
    return 0
}
""")
    assert not diag.has_errors
    fn = prog.declarations[1]
    stmt = fn.body.statements[0]
    assert isinstance(stmt, ast.LetStatement)
    assert isinstance(stmt.value, ast.StructLiteral)
    assert stmt.value.type_name == "User"
    assert len(stmt.value.fields) == 2


def test_index_expression():
    prog, diag = parse("fn f() -> Int { let xs = [1,2,3] return xs[0] }")
    assert not diag.has_errors
    ret = prog.declarations[0].body.statements[1]
    assert isinstance(ret.value, ast.IndexExpression)


def test_field_access():
    prog, diag = parse("struct U { name: String }\nfn f() -> Int { let u = U { name: \"a\" } return 0 }")
    assert not diag.has_errors


def test_string_escapes_in_literal():
    prog, diag = parse('fn f() -> Int { let s = "a\\nb\\tc" return 0 }')
    assert not diag.has_errors
    stmt = prog.declarations[0].body.statements[0]
    assert stmt.value.value == "a\nb\tc"


def test_unexpected_token_error():
    prog, diag = parse("fn f() -> Int { } }")
    assert diag.has_errors


def test_unterminated_block_error():
    prog, diag = parse("fn f() -> Int { let x = 1")
    assert diag.has_errors
