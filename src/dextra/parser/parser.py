"""Hand-written recursive-descent parser for Dextra.

Produces an AST from a token stream.  The parser does *not* perform
semantic checks — only syntactic structure.  On unexpected tokens it
emits `E0002`/`E0003` diagnostics and synchronizes to a statement
boundary (one of `}`, `;`, `fn`, `struct`, `let`, `if`, `while`,
`for`, `return`, EOF) before continuing.
"""
from __future__ import annotations

from typing import Optional

from dextra.ast import nodes as ast
from dextra.diagnostics import Diagnostic, DiagnosticEngine, ErrorCode, Severity
from dextra.lexer import Token, TokenKind


class Parser:
    def __init__(self, tokens: list[Token], diagnostics: DiagnosticEngine) -> None:
        self.tokens = tokens
        self.diagnostics = diagnostics
        self._i = 0

    # -- public --------------------------------------------------------

    def parse_program(self) -> ast.Program:
        declarations: list[ast.Declaration] = []
        while self._peek_kind() is not TokenKind.EOF:
            decl = self._parse_declaration()
            if decl is not None:
                declarations.append(decl)
        eof = self._peek()
        return ast.Program(declarations=declarations, span=eof.span)

    # -- lookahead helpers ---------------------------------------------

    def _peek_kind(self, ahead: int = 0) -> TokenKind:
        tok = self._peek(ahead)
        return tok.kind

    def _peek(self, ahead: int = 0) -> Token:
        idx = self._i + ahead
        if idx >= len(self.tokens):
            return self.tokens[-1]  # EOF token
        return self.tokens[idx]

    def _advance(self) -> Token:
        tok = self._peek()
        if self._i < len(self.tokens) - 1:
            self._i += 1
        return tok

    def _expect(self, kind: TokenKind, code: ErrorCode, msg: str) -> Optional[Token]:
        tok = self._peek()
        if tok.kind is kind:
            return self._advance()
        self.diagnostics.report(
            Diagnostic(
                code=code,
                severity=Severity.ERROR,
                message=f"{msg}, found `{tok.text}`",
                span=tok.span,
                help=f"expected `{kind.name}`",
            )
        )
        return None

    def _error(self, code: ErrorCode, msg: str, span=None, help: str | None = None) -> None:
        self.diagnostics.report(
            Diagnostic(
                code=code,
                severity=Severity.ERROR,
                message=msg,
                span=span if span is not None else self._peek().span,
                help=help,
            )
        )

    # -- declarations ---------------------------------------------------

    def _parse_declaration(self) -> Optional[ast.Declaration]:
        kind = self._peek_kind()
        if kind is TokenKind.KW_FN:
            return self._parse_function()
        if kind is TokenKind.KW_STRUCT:
            return self._parse_struct()
        if kind is TokenKind.KW_ENUM:
            return self._parse_enum()
        self._error(
            ErrorCode.E0002_UNEXPECTED_TOKEN,
            f"expected `fn`, `struct`, or `enum` at top level, found `{self._peek().text}`",
        )
        # Recover by skipping to the next top-level keyword.
        self._synchronize_top_level()
        return None

    def _parse_function(self) -> Optional[ast.FunctionDeclaration]:
        start = self._advance()  # fn
        name_tok = self._expect(TokenKind.IDENTIFIER, ErrorCode.E0002_UNEXPECTED_TOKEN, "expected function name")
        if name_tok is None:
            self._synchronize_top_level()
            return None
        if self._expect(TokenKind.LPAREN, ErrorCode.E0002_UNEXPECTED_TOKEN, "expected `(`") is None:
            self._synchronize_top_level()
            return None
        params: list[ast.Parameter] = []
        if self._peek_kind() is not TokenKind.RPAREN:
            params = self._parse_parameter_list()
        self._expect(TokenKind.RPAREN, ErrorCode.E0005_EXPECTED_RPAREN, "expected `)` after parameter list")

        return_type: Optional[ast.TypeExpr] = None
        if self._peek_kind() is TokenKind.ARROW:
            self._advance()
            return_type = self._parse_type()

        body = self._parse_block()
        if body is None:
            self._synchronize_top_level()
            return None
        span = start.span.merge(body.span)
        return ast.FunctionDeclaration(
            name=name_tok.value,
            parameters=params,
            return_type=return_type,
            body=body,
            span=span,
        )

    def _parse_parameter_list(self) -> list[ast.Parameter]:
        params: list[ast.Parameter] = []
        while True:
            p = self._parse_parameter()
            if p is not None:
                params.append(p)
            if self._peek_kind() is TokenKind.COMMA:
                self._advance()
                # Allow trailing comma
                if self._peek_kind() is TokenKind.RPAREN:
                    break
                continue
            break
        return params

    def _parse_parameter(self) -> Optional[ast.Parameter]:
        name_tok = self._expect(TokenKind.IDENTIFIER, ErrorCode.E0002_UNEXPECTED_TOKEN, "expected parameter name")
        if name_tok is None:
            return None
        self._expect(TokenKind.COLON, ErrorCode.E0007_EXPECTED_COLON, "expected `:` after parameter name")
        ty = self._parse_type()
        if ty is None:
            return None
        return ast.Parameter(name=name_tok.value, type=ty, span=name_tok.span.merge(ty.span))

    def _parse_struct(self) -> Optional[ast.StructDeclaration]:
        start = self._advance()  # struct
        name_tok = self._expect(TokenKind.IDENTIFIER, ErrorCode.E0002_UNEXPECTED_TOKEN, "expected struct name")
        if name_tok is None:
            self._synchronize_top_level()
            return None
        if self._expect(TokenKind.LBRACE, ErrorCode.E0002_UNEXPECTED_TOKEN, "expected `{`") is None:
            self._synchronize_top_level()
            return None
        fields: list[ast.FieldDecl] = []
        while self._peek_kind() is not TokenKind.RBRACE:
            if self._peek_kind() is TokenKind.EOF:
                self._error(ErrorCode.E0004_EXPECTED_RBRACE, "unterminated struct body")
                return None
            field = self._parse_field()
            if field is not None:
                fields.append(field)
            if self._peek_kind() is TokenKind.COMMA:
                self._advance()
                continue
            break
        end_tok = self._expect(TokenKind.RBRACE, ErrorCode.E0004_EXPECTED_RBRACE, "expected `}`")
        span = start.span.merge(end_tok.span if end_tok else self._peek().span)
        return ast.StructDeclaration(name=name_tok.value, fields=fields, span=span)

    def _parse_enum(self) -> Optional[ast.EnumDeclaration]:
        """Parse an enum declaration:

            enum Name {
                Variant1,
                Variant2,
                ...
            }

        v0.1 enums are unit-variant only — no payload types after each
        variant name.  Trailing commas are allowed.
        """

        start = self._advance()  # enum
        name_tok = self._expect(
            TokenKind.IDENTIFIER,
            ErrorCode.E0002_UNEXPECTED_TOKEN,
            "expected enum name",
        )
        if name_tok is None:
            self._synchronize_top_level()
            return None
        if self._expect(TokenKind.LBRACE, ErrorCode.E0002_UNEXPECTED_TOKEN,
                        "expected `{`") is None:
            self._synchronize_top_level()
            return None
        variants: list[ast.EnumVariant] = []
        while self._peek_kind() is not TokenKind.RBRACE:
            if self._peek_kind() is TokenKind.EOF:
                self._error(ErrorCode.E0004_EXPECTED_RBRACE, "unterminated enum body")
                return None
            v_tok = self._expect(
                TokenKind.IDENTIFIER,
                ErrorCode.E0002_UNEXPECTED_TOKEN,
                "expected variant name",
            )
            if v_tok is not None:
                variants.append(ast.EnumVariant(name=v_tok.value, span=v_tok.span))
            if self._peek_kind() is TokenKind.COMMA:
                self._advance()
                # Allow trailing comma
                if self._peek_kind() is TokenKind.RBRACE:
                    break
                continue
            break
        end_tok = self._expect(TokenKind.RBRACE, ErrorCode.E0004_EXPECTED_RBRACE,
                                "expected `}`")
        span = start.span.merge(end_tok.span if end_tok else self._peek().span)
        return ast.EnumDeclaration(name=name_tok.value, variants=variants, span=span)


    def _parse_field(self) -> Optional[ast.FieldDecl]:
        name_tok = self._expect(TokenKind.IDENTIFIER, ErrorCode.E0002_UNEXPECTED_TOKEN, "expected field name")
        if name_tok is None:
            self._synchronize_in_struct()
            return None
        self._expect(TokenKind.COLON, ErrorCode.E0007_EXPECTED_COLON, "expected `:` after field name")
        ty = self._parse_type()
        if ty is None:
            return None
        return ast.FieldDecl(name=name_tok.value, type=ty, span=name_tok.span.merge(ty.span))

    # -- types ----------------------------------------------------------

    def _parse_type(self) -> Optional[ast.TypeExpr]:
        tok = self._peek()
        if tok.kind is TokenKind.KW_INT:
            self._advance()
            return ast.PrimitiveTypeExpr("Int", tok.span)
        if tok.kind is TokenKind.KW_FLOAT:
            self._advance()
            return ast.PrimitiveTypeExpr("Float", tok.span)
        if tok.kind is TokenKind.KW_BOOL:
            self._advance()
            return ast.PrimitiveTypeExpr("Bool", tok.span)
        if tok.kind is TokenKind.KW_STRING:
            self._advance()
            return ast.PrimitiveTypeExpr("String", tok.span)
        if tok.kind is TokenKind.KW_VOID:
            self._advance()
            return ast.PrimitiveTypeExpr("Void", tok.span)
        if tok.kind is TokenKind.LBRACKET:
            self._advance()
            elem = self._parse_type()
            if elem is None:
                return None
            self._expect(TokenKind.RBRACKET, ErrorCode.E0006_EXPECTED_RBRACKET, "expected `]`")
            return ast.ArrayTypeExpr(element=elem, span=tok.span.merge(self._peek().span))
        if tok.kind is TokenKind.IDENTIFIER:
            self._advance()
            return ast.NamedTypeExpr(name=tok.value, span=tok.span)
        self._error(
            ErrorCode.E0102_UNKNOWN_TYPE,
            f"expected type, found `{tok.text}`",
        )
        self._advance()
        return None

    # -- statements -----------------------------------------------------

    def _parse_block(self) -> Optional[ast.Block]:
        lbrace = self._expect(TokenKind.LBRACE, ErrorCode.E0004_EXPECTED_RBRACE, "expected `{`")
        if lbrace is None:
            return None
        statements: list[ast.Statement] = []
        while self._peek_kind() not in (TokenKind.RBRACE, TokenKind.EOF):
            stmt = self._parse_statement()
            if stmt is not None:
                statements.append(stmt)
            else:
                # Recovery: skip to next statement boundary.
                self._synchronize_in_block()
        rbrace = self._expect(TokenKind.RBRACE, ErrorCode.E0004_EXPECTED_RBRACE, "expected `}`")
        span = lbrace.span.merge(rbrace.span if rbrace else self._peek().span)
        return ast.Block(statements=statements, span=span)

    def _parse_statement(self) -> Optional[ast.Statement]:
        kind = self._peek_kind()
        if kind is TokenKind.KW_LET:
            return self._parse_let()
        if kind is TokenKind.KW_RETURN:
            return self._parse_return()
        if kind is TokenKind.KW_BREAK:
            tok = self._advance()
            return ast.BreakStatement(span=tok.span)
        if kind is TokenKind.KW_CONTINUE:
            tok = self._advance()
            return ast.ContinueStatement(span=tok.span)
        if kind is TokenKind.KW_IF:
            return self._parse_if()
        if kind is TokenKind.KW_WHILE:
            return self._parse_while()
        if kind is TokenKind.KW_FOR:
            return self._parse_for()
        if kind is TokenKind.LBRACE:
            return self._parse_block()
        # Expression statement or assignment
        return self._parse_expr_statement()

    def _parse_let(self) -> ast.LetStatement:
        start = self._advance()  # let
        mutable = False
        if self._peek_kind() is TokenKind.KW_MUT:
            self._advance()
            mutable = True
        name_tok = self._expect(TokenKind.IDENTIFIER, ErrorCode.E0002_UNEXPECTED_TOKEN, "expected variable name")
        type_annotation: Optional[ast.TypeExpr] = None
        if self._peek_kind() is TokenKind.COLON:
            self._advance()
            type_annotation = self._parse_type()
        self._expect(TokenKind.EQ, ErrorCode.E0008_EXPECTED_EQ, "expected `=` in let binding")
        value = self._parse_expression()
        span = start.span.merge(value.span if value else self._peek().span)
        if name_tok is None:
            return ast.LetStatement(name="<error>", mutable=mutable,
                                    type_annotation=type_annotation, value=value, span=span)
        return ast.LetStatement(
            name=name_tok.value,
            mutable=mutable,
            type_annotation=type_annotation,
            value=value,
            span=span,
        )

    def _parse_return(self) -> ast.ReturnStatement:
        start = self._advance()  # return
        # Return with no expression if next token starts a new statement / closes a block.
        nxt = self._peek_kind()
        if nxt in (TokenKind.RBRACE, TokenKind.EOF, TokenKind.KW_LET, TokenKind.KW_RETURN,
                   TokenKind.KW_IF, TokenKind.KW_WHILE, TokenKind.KW_FOR,
                   TokenKind.KW_BREAK, TokenKind.KW_CONTINUE, TokenKind.SEMICOLON):
            return ast.ReturnStatement(value=None, span=start.span)
        value = self._parse_expression()
        return ast.ReturnStatement(value=value, span=start.span.merge(value.span))

    def _parse_if(self) -> ast.IfStatement:
        start = self._advance()  # if
        cond = self._parse_expression()
        then_block = self._parse_block()
        else_branch: Optional[ast.IfStatement | ast.Block] = None
        if self._peek_kind() is TokenKind.KW_ELSE:
            self._advance()
            if self._peek_kind() is TokenKind.KW_IF:
                else_branch = self._parse_if()
            else:
                else_branch = self._parse_block()
        end_span = (else_branch.span if else_branch else then_block.span) if then_block else self._peek().span
        return ast.IfStatement(
            condition=cond,
            then_block=then_block,
            else_branch=else_branch,
            span=start.span.merge(end_span),
        )

    def _parse_while(self) -> ast.WhileStatement:
        start = self._advance()
        cond = self._parse_expression()
        body = self._parse_block()
        end_span = body.span if body else self._peek().span
        return ast.WhileStatement(condition=cond, body=body, span=start.span.merge(end_span))

    def _parse_for(self) -> ast.ForStatement:
        start = self._advance()
        var_tok = self._expect(TokenKind.IDENTIFIER, ErrorCode.E0002_UNEXPECTED_TOKEN, "expected loop variable")
        self._expect(TokenKind.KW_IN, ErrorCode.E0002_UNEXPECTED_TOKEN, "expected `in` in for loop")
        # Parse the iterable as an expression and detect a range post-hoc.
        iterable = self._parse_expression()
        # `parse_expression` will already have consumed `a..b` as a RangeExpression
        # via the postfix layer, because `..` is two chars and we route it as a
        # custom operator handled below in `_parse_range_or_lower`.
        body = self._parse_block()
        end_span = body.span if body else self._peek().span
        return ast.ForStatement(
            var=var_tok.value if var_tok else "<error>",
            iterable=iterable,
            body=body,
            span=start.span.merge(end_span),
        )

    def _parse_expr_statement(self) -> Optional[ast.Statement]:
        expr = self._parse_expression()
        if expr is None:
            return None
        if self._peek_kind() is TokenKind.EQ:
            # Assignment
            self._advance()
            value = self._parse_expression()
            span = expr.span.merge(value.span)
            return ast.Assignment(target=expr, value=value, span=span)
        # Bare expression statement.  Only allow calls and match
        # expressions — bare literals like `1 + 2` are useless and
        # probably user error.  `match` with Void arms is a valid
        # statement (like a switch in C).
        if not isinstance(expr, (ast.CallExpression, ast.MatchExpression)):
            self._error(
                ErrorCode.E0002_UNEXPECTED_TOKEN,
                "expression has no effect (use it, assign it, or remove it)",
                span=expr.span,
            )
        return ast.ExpressionStatement(expression=expr, span=expr.span)

    # -- expressions ----------------------------------------------------

    def _parse_expression(self) -> ast.Expression:
        return self._parse_or()

    def _parse_or(self) -> ast.Expression:
        left = self._parse_and()
        while self._peek_kind() is TokenKind.OR_OR:
            op = self._advance()
            right = self._parse_and()
            left = ast.BinaryOp(op="||", left=left, right=right, span=left.span.merge(right.span))
        return left

    def _parse_and(self) -> ast.Expression:
        left = self._parse_equality()
        while self._peek_kind() is TokenKind.AND_AND:
            self._advance()
            right = self._parse_equality()
            left = ast.BinaryOp(op="&&", left=left, right=right, span=left.span.merge(right.span))
        return left

    def _parse_equality(self) -> ast.Expression:
        left = self._parse_comparison()
        while self._peek_kind() in (TokenKind.EQ_EQ, TokenKind.BANG_EQ):
            op = self._advance()
            right = self._parse_comparison()
            left = ast.BinaryOp(op=op.kind and ("==" if op.kind is TokenKind.EQ_EQ else "!="),
                                left=left, right=right, span=left.span.merge(right.span))
        return left

    def _parse_comparison(self) -> ast.Expression:
        left = self._parse_additive()
        while self._peek_kind() in (TokenKind.LT, TokenKind.GT, TokenKind.LT_EQ, TokenKind.GT_EQ):
            op_tok = self._advance()
            right = self._parse_additive()
            sym = {TokenKind.LT: "<", TokenKind.GT: ">", TokenKind.LT_EQ: "<=", TokenKind.GT_EQ: ">="}[op_tok.kind]
            left = ast.BinaryOp(op=sym, left=left, right=right, span=left.span.merge(right.span))
        return left

    def _parse_additive(self) -> ast.Expression:
        left = self._parse_multiplicative()
        while self._peek_kind() in (TokenKind.PLUS, TokenKind.MINUS):
            op_tok = self._advance()
            right = self._parse_multiplicative()
            sym = "+" if op_tok.kind is TokenKind.PLUS else "-"
            left = ast.BinaryOp(op=sym, left=left, right=right, span=left.span.merge(right.span))
        return left

    def _parse_multiplicative(self) -> ast.Expression:
        left = self._parse_range()
        while self._peek_kind() in (TokenKind.STAR, TokenKind.SLASH, TokenKind.PERCENT):
            op_tok = self._advance()
            right = self._parse_range()
            sym = {TokenKind.STAR: "*", TokenKind.SLASH: "/", TokenKind.PERCENT: "%"}[op_tok.kind]
            left = ast.BinaryOp(op=sym, left=left, right=right, span=left.span.merge(right.span))
        return left

    def _parse_range(self) -> ast.Expression:
        # Range operator `..` sits between multiplicative and unary in
        # our precedence.  Operands must be integers; this is checked
        # later by the type checker.
        left = self._parse_unary()
        while self._peek_kind() is TokenKind.DOT_DOT:
            self._advance()
            right = self._parse_unary()
            left = ast.RangeExpression(start=left, end=right, span=left.span.merge(right.span))
        return left

    def _parse_unary(self) -> ast.Expression:
        if self._peek_kind() in (TokenKind.MINUS, TokenKind.BANG):
            op_tok = self._advance()
            operand = self._parse_unary()
            return ast.UnaryOp(op="-" if op_tok.kind is TokenKind.MINUS else "!",
                               operand=operand, span=op_tok.span.merge(operand.span))
        return self._parse_postfix()

    def _parse_postfix(self) -> ast.Expression:
        expr = self._parse_primary()
        while True:
            kind = self._peek_kind()
            if kind is TokenKind.LPAREN:
                lparen = self._advance()
                args: list[ast.Expression] = []
                if self._peek_kind() is not TokenKind.RPAREN:
                    while True:
                        arg = self._parse_expression()
                        args.append(arg)
                        if self._peek_kind() is TokenKind.COMMA:
                            self._advance()
                            if self._peek_kind() is TokenKind.RPAREN:
                                break
                            continue
                        break
                rparen = self._expect(TokenKind.RPAREN, ErrorCode.E0005_EXPECTED_RPAREN, "expected `)` after arguments")
                end_span = rparen.span if rparen else self._peek().span
                expr = ast.CallExpression(callee=expr, arguments=args, span=expr.span.merge(end_span))
            elif kind is TokenKind.LBRACKET:
                self._advance()
                idx = self._parse_expression()
                rbr = self._expect(TokenKind.RBRACKET, ErrorCode.E0006_EXPECTED_RBRACKET, "expected `]`")
                end_span = rbr.span if rbr else self._peek().span
                expr = ast.IndexExpression(target=expr, index=idx, span=expr.span.merge(end_span))
            elif kind is TokenKind.DOT:
                self._advance()
                field_tok = self._expect(TokenKind.IDENTIFIER, ErrorCode.E0002_UNEXPECTED_TOKEN, "expected field name")
                if field_tok is None:
                    break
                expr = ast.FieldAccessExpression(target=expr, field=field_tok.value, span=expr.span.merge(field_tok.span))
            else:
                break
        return expr

    def _parse_primary(self) -> ast.Expression:
        tok = self._peek()
        if tok.kind is TokenKind.INTEGER:
            self._advance()
            return ast.IntegerLiteral(value=tok.value, span=tok.span)
        if tok.kind is TokenKind.FLOAT:
            self._advance()
            return ast.FloatLiteral(value=tok.value, span=tok.span)
        if tok.kind is TokenKind.STRING:
            self._advance()
            return ast.StringLiteral(value=tok.value, span=tok.span)
        if tok.kind is TokenKind.KW_TRUE:
            self._advance()
            return ast.BooleanLiteral(value=True, span=tok.span)
        if tok.kind is TokenKind.KW_FALSE:
            self._advance()
            return ast.BooleanLiteral(value=False, span=tok.span)
        if tok.kind is TokenKind.KW_MATCH:
            return self._parse_match()
        if tok.kind is TokenKind.IDENTIFIER:
            self._advance()
            # Struct literal?  Only if the next non-trivia token is `{` *and*
            # what follows is a `field:` pattern.  We disambiguate conservatively
            # so that `if x { ... }` is parsed as a block, not a struct literal.
            if self._peek_kind() is TokenKind.LBRACE and self._looks_like_struct_literal():
                return self._parse_struct_literal(tok)
            return ast.IdentifierExpression(name=tok.value, span=tok.span)
        if tok.kind is TokenKind.LPAREN:
            self._advance()
            inner = self._parse_expression()
            self._expect(TokenKind.RPAREN, ErrorCode.E0005_EXPECTED_RPAREN, "expected `)`")
            return inner
        if tok.kind is TokenKind.LBRACKET:
            return self._parse_array_literal()
        self._error(ErrorCode.E0003_EXPECTED_EXPRESSION, f"expected expression, found `{tok.text}`")
        self._advance()
        # Return a synthetic integer literal so the AST has *something*;
        # upstream stages will detect the diagnostic and skip codegen.
        return ast.IntegerLiteral(value=0, span=tok.span)

    def _parse_match(self) -> ast.MatchExpression:
        """Parse a match expression:

            match scrutinee {
                Pattern1 => body1,
                Pattern2 => body2,
                _ => default,
            }

        Patterns in v0.1 are either `_` (wildcard) or `EnumName.VariantName`.
        Arms are separated by commas (trailing comma allowed).
        """

        start = self._advance()  # match
        scrutinee = self._parse_expression()
        if self._expect(TokenKind.LBRACE, ErrorCode.E0004_EXPECTED_RBRACE,
                        "expected `{` to open match arms") is None:
            return ast.MatchExpression(scrutinee=scrutinee, arms=[],
                                       span=start.span.merge(scrutinee.span))
        arms: list[ast.MatchArm] = []
        if self._peek_kind() is not TokenKind.RBRACE:
            while True:
                arm = self._parse_match_arm()
                if arm is not None:
                    arms.append(arm)
                if self._peek_kind() is TokenKind.COMMA:
                    self._advance()
                    if self._peek_kind() is TokenKind.RBRACE:
                        break
                    continue
                break
        end_tok = self._expect(TokenKind.RBRACE, ErrorCode.E0004_EXPECTED_RBRACE,
                                "expected `}` to close match arms")
        end_span = end_tok.span if end_tok else self._peek().span
        return ast.MatchExpression(
            scrutinee=scrutinee, arms=arms,
            span=start.span.merge(end_span),
        )

    def _parse_match_arm(self) -> Optional[ast.MatchArm]:
        """Parse one arm of a match: `pattern => body`."""

        # Pattern: `_` (wildcard) or `EnumName.VariantName`.
        # The wildcard `_` is lexed as an IDENTIFIER whose value is "_".
        pat_tok = self._peek()
        if pat_tok.kind is TokenKind.IDENTIFIER and pat_tok.value == "_":
            self._advance()
            pattern: ast.Pattern = ast.WildcardPattern(span=pat_tok.span)
        elif pat_tok.kind is TokenKind.IDENTIFIER:
            # EnumName.VariantName
            enum_tok = self._advance()
            if self._peek_kind() is not TokenKind.DOT:
                self._error(ErrorCode.E0002_UNEXPECTED_TOKEN,
                            f"expected `.` after enum name in match pattern, found `{self._peek().text}`")
                self._sync_to_arrow()
                pattern = ast.EnumVariantPattern(
                    enum_name=enum_tok.value,
                    variant_name="<error>",
                    span=enum_tok.span,
                )
            else:
                self._advance()  # consume '.'
                v_tok = self._expect(TokenKind.IDENTIFIER, ErrorCode.E0002_UNEXPECTED_TOKEN,
                                      "expected variant name after `.`")
                if v_tok is None:
                    return None
                pattern = ast.EnumVariantPattern(
                    enum_name=enum_tok.value,
                    variant_name=v_tok.value,
                    span=enum_tok.span.merge(v_tok.span),
                )
        else:
            self._error(ErrorCode.E0002_UNEXPECTED_TOKEN,
                        f"expected pattern (`_` or `EnumName.VariantName`), found `{pat_tok.text}`")
            self._advance()
            return None
        # `=>` separates pattern from body.
        if self._peek_kind() is TokenKind.FAT_ARROW:
            self._advance()
        else:
            self._error(ErrorCode.E0009_EXPECTED_ARROW,
                        f"expected `=>` after match pattern, found `{self._peek().text}`")
        body = self._parse_expression()
        return ast.MatchArm(pattern=pattern, body=body,
                              span=pattern.span.merge(body.span))

    def _sync_to_arrow(self) -> None:
        """Skip tokens until we hit `=>`, `,`, `}`, or EOF."""

        while self._peek_kind() not in (TokenKind.FAT_ARROW, TokenKind.COMMA,
                                         TokenKind.RBRACE, TokenKind.EOF):
            self._advance()

    def _looks_like_struct_literal(self) -> bool:
        # Peek (LBRACE) (IDENTIFIER COLON ...) — if so, treat as struct literal.
        # We require at least one `ident :` inside the braces.
        if self._peek_kind(1) is not TokenKind.IDENTIFIER:
            return False
        if self._peek_kind(2) is not TokenKind.COLON:
            return False
        return True

    def _parse_struct_literal(self, name_tok: Token) -> ast.StructLiteral:
        lbrace = self._advance()  # {
        fields: list[ast.StructLiteralField] = []
        if self._peek_kind() is not TokenKind.RBRACE:
            while True:
                fn_tok = self._expect(TokenKind.IDENTIFIER, ErrorCode.E0002_UNEXPECTED_TOKEN, "expected field name")
                self._expect(TokenKind.COLON, ErrorCode.E0007_EXPECTED_COLON, "expected `:` after field name")
                value = self._parse_expression()
                if fn_tok is not None:
                    fields.append(ast.StructLiteralField(name=fn_tok.value, value=value, span=fn_tok.span.merge(value.span)))
                if self._peek_kind() is TokenKind.COMMA:
                    self._advance()
                    if self._peek_kind() is TokenKind.RBRACE:
                        break
                    continue
                break
        rbrace = self._expect(TokenKind.RBRACE, ErrorCode.E0004_EXPECTED_RBRACE, "expected `}`")
        end_span = rbrace.span if rbrace else self._peek().span
        return ast.StructLiteral(type_name=name_tok.value, fields=fields, span=name_tok.span.merge(end_span))

    def _parse_array_literal(self) -> ast.ArrayLiteral:
        lbr = self._advance()  # [
        elements: list[ast.Expression] = []
        if self._peek_kind() is not TokenKind.RBRACKET:
            while True:
                el = self._parse_expression()
                elements.append(el)
                if self._peek_kind() is TokenKind.COMMA:
                    self._advance()
                    if self._peek_kind() is TokenKind.RBRACKET:
                        break
                    continue
                break
        rbr = self._expect(TokenKind.RBRACKET, ErrorCode.E0006_EXPECTED_RBRACKET, "expected `]`")
        end_span = rbr.span if rbr else self._peek().span
        return ast.ArrayLiteral(elements=elements, span=lbr.span.merge(end_span))

    # -- error recovery -------------------------------------------------

    def _synchronize_top_level(self) -> None:
        while self._peek_kind() not in (TokenKind.KW_FN, TokenKind.KW_STRUCT, TokenKind.EOF):
            self._advance()

    def _synchronize_in_block(self) -> None:
        while self._peek_kind() not in (
            TokenKind.RBRACE, TokenKind.EOF,
            TokenKind.KW_LET, TokenKind.KW_RETURN, TokenKind.KW_IF,
            TokenKind.KW_WHILE, TokenKind.KW_FOR, TokenKind.KW_BREAK,
            TokenKind.KW_CONTINUE, TokenKind.SEMICOLON,
        ):
            self._advance()
        if self._peek_kind() is TokenKind.SEMICOLON:
            self._advance()

    def _synchronize_in_struct(self) -> None:
        while self._peek_kind() not in (TokenKind.RBRACE, TokenKind.COMMA, TokenKind.EOF):
            self._advance()
