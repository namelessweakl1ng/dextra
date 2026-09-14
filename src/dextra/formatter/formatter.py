"""Deterministic Dextra source formatter.

The formatter reprints the AST with a consistent style:
  * 4-space indentation
  * no trailing whitespace
  * trailing newline at EOF
  * single space around binary operators (except `.` member access and unary)
  * struct fields separated by `, `
  * blocks open on the same line as their header, close on a new line
"""
from __future__ import annotations

from typing import Optional

from dextra.ast import nodes as ast


class Formatter:
    INDENT = "    "

    def format_program(self, prog: ast.Program) -> str:
        out: list[str] = []
        for d in prog.declarations:
            out.append(self._format_decl(d, 0))
        return "\n".join(out) + "\n"

    # -- declarations ---------------------------------------------------

    def _format_decl(self, decl: ast.Declaration, indent: int) -> str:
        if isinstance(decl, ast.FunctionDeclaration):
            return self._format_function(decl, indent)
        if isinstance(decl, ast.StructDeclaration):
            return self._format_struct(decl, indent)
        return f"// <unknown decl: {type(decl).__name__}>"

    def _format_function(self, fn: ast.FunctionDeclaration, indent: int) -> str:
        pad = self.INDENT * indent
        params = ", ".join(f"{p.name}: {self._format_type(p.type)}" for p in fn.parameters)
        ret = f" -> {self._format_type(fn.return_type)}" if fn.return_type is not None else ""
        header = f"{pad}fn {fn.name}({params}){ret} {{"
        body = self._format_block(fn.body, indent + 1)
        return f"{header}\n{body}\n{pad}}}"

    def _format_struct(self, sd: ast.StructDeclaration, indent: int) -> str:
        pad = self.INDENT * indent
        if not sd.fields:
            return f"{pad}struct {sd.name} {{}}"
        fields = ", ".join(f"{f.name}: {self._format_type(f.type)}" for f in sd.fields)
        return f"{pad}struct {sd.name} {{ {fields} }}"

    def _format_type(self, ty: Optional[ast.TypeExpr]) -> str:
        if ty is None:
            return "Void"
        if isinstance(ty, ast.PrimitiveTypeExpr):
            return ty.name
        if isinstance(ty, ast.ArrayTypeExpr):
            return f"[{self._format_type(ty.element)}]"
        if isinstance(ty, ast.NamedTypeExpr):
            return ty.name
        return f"<unknown type: {type(ty).__name__}>"

    # -- blocks / statements -------------------------------------------

    def _format_block(self, block: ast.Block, indent: int) -> str:
        if not block.statements:
            return ""
        lines = []
        for stmt in block.statements:
            lines.append(self._format_stmt(stmt, indent))
        return "\n".join(lines)

    def _format_stmt(self, stmt: ast.Statement, indent: int) -> str:
        pad = self.INDENT * indent
        if isinstance(stmt, ast.LetStatement):
            mut = "mut " if stmt.mutable else ""
            ann = f": {self._format_type(stmt.type_annotation)}" if stmt.type_annotation else ""
            return f"{pad}let {mut}{stmt.name}{ann} = {self._format_expr(stmt.value)}"
        if isinstance(stmt, ast.ReturnStatement):
            if stmt.value is None:
                return f"{pad}return"
            return f"{pad}return {self._format_expr(stmt.value)}"
        if isinstance(stmt, ast.BreakStatement):
            return f"{pad}break"
        if isinstance(stmt, ast.ContinueStatement):
            return f"{pad}continue"
        if isinstance(stmt, ast.IfStatement):
            s = f"{pad}if {self._format_expr(stmt.condition)} {{\n"
            s += self._format_block(stmt.then_block, indent + 1)
            s += f"\n{pad}}}"
            cur: object = stmt.else_branch
            while cur is not None:
                if isinstance(cur, ast.IfStatement):
                    s += f" else if {self._format_expr(cur.condition)} {{\n"
                    s += self._format_block(cur.then_block, indent + 1)
                    s += f"\n{pad}}}"
                    cur = cur.else_branch
                elif isinstance(cur, ast.Block):
                    s += " else {\n"
                    s += self._format_block(cur, indent + 1)
                    s += f"\n{pad}}}"
                    cur = None
                else:
                    cur = None
            return s
        if isinstance(stmt, ast.WhileStatement):
            s = f"{pad}while {self._format_expr(stmt.condition)} {{\n"
            s += self._format_block(stmt.body, indent + 1)
            s += f"\n{pad}}}"
            return s
        if isinstance(stmt, ast.ForStatement):
            s = f"{pad}for {stmt.var} in {self._format_expr(stmt.iterable)} {{\n"
            s += self._format_block(stmt.body, indent + 1)
            s += f"\n{pad}}}"
            return s
        if isinstance(stmt, ast.Assignment):
            return f"{pad}{self._format_expr(stmt.target)} = {self._format_expr(stmt.value)}"
        if isinstance(stmt, ast.ExpressionStatement):
            return f"{pad}{self._format_expr(stmt.expression)}"
        if isinstance(stmt, ast.Block):
            inner = self._format_block(stmt, indent + 1)
            return f"{pad}{{\n{inner}\n{pad}}}"
        return f"{pad}// <unknown stmt: {type(stmt).__name__}>"

    # -- expressions ----------------------------------------------------

    def _format_expr(self, expr: ast.Expression) -> str:
        if isinstance(expr, ast.IntegerLiteral):
            return str(expr.value)
        if isinstance(expr, ast.FloatLiteral):
            return repr(expr.value)
        if isinstance(expr, ast.StringLiteral):
            return self._escape_string(expr.value)
        if isinstance(expr, ast.BooleanLiteral):
            return "true" if expr.value else "false"
        if isinstance(expr, ast.IdentifierExpression):
            return expr.name
        if isinstance(expr, ast.ArrayLiteral):
            return "[" + ", ".join(self._format_expr(e) for e in expr.elements) + "]"
        if isinstance(expr, ast.StructLiteral):
            fields = ", ".join(f"{f.name}: {self._format_expr(f.value)}" for f in expr.fields)
            return f"{expr.type_name} {{ {fields} }}"
        if isinstance(expr, ast.BinaryOp):
            return f"{self._format_expr(expr.left)} {expr.op} {self._format_expr(expr.right)}"
        if isinstance(expr, ast.UnaryOp):
            return f"{expr.op}{self._format_expr(expr.operand)}"
        if isinstance(expr, ast.CallExpression):
            args = ", ".join(self._format_expr(a) for a in expr.arguments)
            return f"{self._format_expr(expr.callee)}({args})"
        if isinstance(expr, ast.IndexExpression):
            return f"{self._format_expr(expr.target)}[{self._format_expr(expr.index)}]"
        if isinstance(expr, ast.FieldAccessExpression):
            return f"{self._format_expr(expr.target)}.{expr.field}"
        if isinstance(expr, ast.RangeExpression):
            return f"{self._format_expr(expr.start)}..{self._format_expr(expr.end)}"
        return f"<unknown expr: {type(expr).__name__}>"

    def _escape_string(self, s: str) -> str:
        s = s.replace("\\", "\\\\")
        s = s.replace('"', '\\"')
        s = s.replace("\n", "\\n")
        s = s.replace("\t", "\\t")
        return f'"{s}"'
