"""Top-level driver for semantic analysis and type checking.

Responsibilities:

* Register struct and function declarations in the global scope.
* Register builtin functions (print, println, etc.).
* For each function, walk its body and:
  * Build lexical scopes.
  * Resolve every identifier to a `Symbol`.
  * Attach a `Type` to every expression.
  * Report semantic and type diagnostics.

The pass mutates the AST in place: each `IdentifierExpression`,
`CallExpression`, etc. gains an `resolved` attribute pointing to its
`Symbol`; each `Expression` gains a `type` attribute.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from dextra.ast import nodes as ast
from dextra.diagnostics import Diagnostic, DiagnosticEngine, ErrorCode, Severity
from dextra.lexer.source import SourceSpan
from dextra.semantic.scope import Scope
from dextra.semantic.symbols import (
    EnumInfo,
    FunctionInfo,
    StructInfo,
    Symbol,
    SymbolKind,
)
from dextra.types.type_system import (
    UNKNOWN,
    ArrayType,
    EnumType,
    FunctionType,
    PrimitiveType,
    StructType,
    Type,
    TypeContext,
)


# Attach points for resolved symbols/types on AST nodes.
# We use plain attributes so the AST remains dataclass-friendly.

def _attach_resolved(node: ast.AST, sym: Symbol) -> None:
    setattr(node, "_resolved", sym)


def _get_resolved(node: ast.AST) -> Optional[Symbol]:
    return getattr(node, "_resolved", None)


def _attach_type(node: ast.AST, ty: Type) -> None:
    setattr(node, "_type", ty)


def get_type(node: ast.AST) -> Optional[Type]:
    """Public helper for downstream stages (IR lowering, etc.)."""

    return getattr(node, "_type", None)


def get_resolved(node: ast.AST) -> Optional[Symbol]:
    """Public helper for downstream stages."""

    return getattr(node, "_resolved", None)


def get_enum_variant(node: ast.AST) -> Optional[tuple[EnumType, str]]:
    """If `node` is a `FieldAccessExpression` that was resolved to an
    enum variant reference (`EnumName.VariantName`), return the
    `(enum_type, variant_name)` tuple.  Otherwise return None.

    Used by the IR lowering to emit a `ConstInt` with the variant's tag.
    """

    return getattr(node, "_enum_variant", None)


@dataclass
class AnalyzedProgram:
    program: ast.Program
    ctx: TypeContext
    global_scope: Scope
    structs: dict[str, StructInfo]
    functions: dict[str, FunctionInfo]
    enums: dict[str, EnumInfo]


class Analyzer:
    def __init__(self, diag: DiagnosticEngine) -> None:
        self.diag = diag
        self.ctx = TypeContext()
        self.global_scope = Scope(parent=None)
        self.structs: dict[str, StructInfo] = {}
        self.functions: dict[str, FunctionInfo] = {}
        self.enums: dict[str, EnumInfo] = {}
        self._loop_depth = 0
        self._current_function_return: Type = self.ctx.VOID
        self._current_function: Optional[ast.FunctionDeclaration] = None
        # Contextual type stack: pushed by `let x: T = ...` so that array
        # literals `[]` (which have no elements to infer from) can inherit
        # the annotation's element type.  Empty list = no contextual type.
        self._expected_type_stack: list[Optional[Type]] = []
        self._register_builtins()

    # -- public --------------------------------------------------------

    def analyze(self, program: ast.Program) -> AnalyzedProgram:
        self._collect_top_level(program)
        for fn in (d for d in program.declarations if isinstance(d, ast.FunctionDeclaration)):
            self._check_function(fn)
        return AnalyzedProgram(
            program=program,
            ctx=self.ctx,
            global_scope=self.global_scope,
            structs=self.structs,
            functions=self.functions,
            enums=self.enums,
        )

    # -- builtins ------------------------------------------------------

    def _register_builtins(self) -> None:
        # print/println accept Int/Float/Bool/String — we model these as 4
        # separate builtin functions each (no overloading in v0.1).
        # The IR/codegen layer dispatches based on the argument's type.
        # For the type checker we treat `print` and `println` as accepting
        # any of {Int, Float, Bool, String} and returning Void.
        for fname in ("print", "println"):
            sym = Symbol(
                name=fname,
                kind=SymbolKind.FUNCTION,
                span=SourceSpan.synthetic("<builtins>"),
                type=self.ctx.VOID,
                is_builtin=True,
            )
            self.global_scope.declare(sym)
            self.functions[fname] = FunctionInfo(
                decl=None,  # type: ignore[arg-type]
                param_types=[],
                return_type=self.ctx.VOID,
                is_builtin=True,
            )
        # `length(s: String) -> Int` and `length(arr) -> Int` (overloaded by arg type)
        sym = Symbol(
            name="length",
            kind=SymbolKind.FUNCTION,
            span=SourceSpan.synthetic("<builtins>"),
            type=self.ctx.INT,
            is_builtin=True,
        )
        self.global_scope.declare(sym)
        self.functions["length"] = FunctionInfo(
            decl=None,  # type: ignore[arg-type]
            param_types=[self.ctx.STRING],   # the IR layer also accepts arrays
            return_type=self.ctx.INT,
            is_builtin=True,
        )

    # -- top-level collection ------------------------------------------

    def _collect_top_level(self, program: ast.Program) -> None:
        # First pass: register all struct, function, and enum *declarations*
        # so that forward references work.
        for decl in program.declarations:
            if isinstance(decl, ast.StructDeclaration):
                self._register_struct(decl)
            elif isinstance(decl, ast.FunctionDeclaration):
                self._register_function(decl)
            elif isinstance(decl, ast.EnumDeclaration):
                self._register_enum(decl)
        # Second pass: resolve struct field types now that all structs exist.
        for sname, sinfo in list(self.structs.items()):
            resolved_fields: list[tuple[str, Type]] = []
            for f in sinfo.decl.fields:
                ft = self._resolve_type_expr(f.type)
                resolved_fields.append((f.name, ft))
            struct_type = StructType(struct_name=sname,
                                     fields=tuple(resolved_fields))
            self.ctx.register_struct(struct_type)
            # Update the existing symbol's type
            sym = self.global_scope.lookup_local(sname)
            assert sym is not None
            sym.type = struct_type
            sinfo.field_types = {fn: ft for fn, ft in resolved_fields}
            sinfo.field_indices = {fn: i for i, (fn, _) in enumerate(resolved_fields)}

    def _register_struct(self, decl: ast.StructDeclaration) -> None:
        if self.global_scope.lookup_local(decl.name) is not None:
            self.diag.report(Diagnostic(
                code=ErrorCode.E0101_DUPLICATE_DECLARATION,
                severity=Severity.ERROR,
                message=f"duplicate declaration of `{decl.name}`",
                span=decl.span,
            ))
            return
        sym = Symbol(
            name=decl.name,
            kind=SymbolKind.STRUCT,
            span=decl.span,
            decl=decl,
        )
        self.global_scope.declare(sym)
        self.structs[decl.name] = StructInfo(
            decl=decl,
            field_types={},
            field_indices={},
        )

    def _register_function(self, decl: ast.FunctionDeclaration) -> None:
        if self.global_scope.lookup_local(decl.name) is not None:
            self.diag.report(Diagnostic(
                code=ErrorCode.E0101_DUPLICATE_DECLARATION,
                severity=Severity.ERROR,
                message=f"duplicate declaration of `{decl.name}`",
                span=decl.span,
            ))
            return
        # Compute function type lazily — param types may reference a struct
        # that hasn't been registered yet.  Defer to second pass.
        self.functions[decl.name] = FunctionInfo(
            decl=decl,
            param_types=[],
            return_type=self.ctx.VOID,   # placeholder, fixed in second pass
            is_builtin=False,
        )
        sym = Symbol(
            name=decl.name,
            kind=SymbolKind.FUNCTION,
            span=decl.span,
            decl=decl,
        )
        self.global_scope.declare(sym)

    def _register_enum(self, decl: ast.EnumDeclaration) -> None:
        if self.global_scope.lookup_local(decl.name) is not None:
            self.diag.report(Diagnostic(
                code=ErrorCode.E0101_DUPLICATE_DECLARATION,
                severity=Severity.ERROR,
                message=f"duplicate declaration of `{decl.name}`",
                span=decl.span,
            ))
            return
        # Check for duplicate variant names within the enum.
        seen: set[str] = set()
        variant_names: list[str] = []
        for v in decl.variants:
            if v.name in seen:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0101_DUPLICATE_DECLARATION,
                    severity=Severity.ERROR,
                    message=f"duplicate variant `{v.name}` in enum `{decl.name}`",
                    span=v.span,
                ))
                continue
            seen.add(v.name)
            variant_names.append(v.name)
        enum_type = EnumType(
            enum_name=decl.name,
            variant_names=tuple(variant_names),
        )
        self.ctx.register_enum(enum_type)
        sym = Symbol(
            name=decl.name,
            kind=SymbolKind.ENUM,
            span=decl.span,
            type=enum_type,
            decl=decl,
        )
        self.global_scope.declare(sym)
        self.enums[decl.name] = EnumInfo(
            decl=decl,
            variant_names=variant_names,
            variant_indices={v: i for i, v in enumerate(variant_names)},
        )

    def _resolve_type_expr(self, ty: ast.TypeExpr) -> Type:
        if isinstance(ty, ast.PrimitiveTypeExpr):
            t = self.ctx.primitive(ty.name)
            if t is None:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0102_UNKNOWN_TYPE,
                    severity=Severity.ERROR,
                    message=f"unknown primitive type `{ty.name}`",
                    span=ty.span,
                ))
                return UNKNOWN
            return t
        if isinstance(ty, ast.ArrayTypeExpr):
            elem = self._resolve_type_expr(ty.element)
            return self.ctx.array(elem)
        if isinstance(ty, ast.NamedTypeExpr):
            sym = self.global_scope.lookup(ty.name)
            if sym is None or sym.kind not in (SymbolKind.STRUCT, SymbolKind.ENUM):
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0102_UNKNOWN_TYPE,
                    severity=Severity.ERROR,
                    message=f"unknown type `{ty.name}`",
                    span=ty.span,
                ))
                return UNKNOWN
            assert sym.type is not None
            return sym.type
        self.diag.report(Diagnostic(
            code=ErrorCode.E0102_UNKNOWN_TYPE,
            severity=Severity.ERROR,
            message="unknown type expression",
            span=ty.span,
        ))
        return UNKNOWN

    # -- per-function checking -----------------------------------------

    def _check_function(self, fn: ast.FunctionDeclaration) -> None:
        # Resolve param + return types now.
        param_types = [self._resolve_type_expr(p.type) for p in fn.parameters]
        return_type = (self._resolve_type_expr(fn.return_type)
                       if fn.return_type is not None else self.ctx.VOID)

        # Update function info.
        finfo = self.functions[fn.name]
        finfo.param_types = param_types
        finfo.return_type = return_type
        # Update function symbol's type.
        sym = self.global_scope.lookup_local(fn.name)
        assert sym is not None
        sym.type = FunctionType(
            param_types=tuple(param_types),
            return_type=return_type,
        )

        # Enforce main -> Int.
        if fn.name == "main":
            if return_type is not self.ctx.INT:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0401_MAIN_MUST_RETURN_INT,
                    severity=Severity.ERROR,
                    message="`main` must return `Int`",
                    span=fn.span,
                ))

        # Build the function scope.
        fn_scope = Scope(parent=self.global_scope)
        for i, (p, t) in enumerate(zip(fn.parameters, param_types)):
            psym = Symbol(
                name=p.name,
                kind=SymbolKind.PARAMETER,
                span=p.span,
                type=t,
                mutable=True,   # parameters are mutable in v0.1
                decl=p,
            )
            if not fn_scope.declare(psym):
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0101_DUPLICATE_DECLARATION,
                    severity=Severity.ERROR,
                    message=f"duplicate parameter `{p.name}`",
                    span=p.span,
                ))

        prev_return = self._current_function_return
        prev_fn = self._current_function
        self._current_function_return = return_type
        self._current_function = fn
        self._check_block(fn.body, fn_scope)
        self._current_function_return = prev_return
        self._current_function = prev_fn

        # Control-flow-aware return analysis: every reachable path through
        # a non-Void function must end in a `return`.  We do NOT rely on
        # LLVM's verifier to catch this — Dextra reports a clean diagnostic
        # instead.
        if return_type is not self.ctx.VOID and not self._block_always_returns(fn.body):
            self.diag.report(Diagnostic(
                code=ErrorCode.E0218_MISSING_RETURN,
                severity=Severity.ERROR,
                message=f"function `{fn.name}` is missing a `return` on some control-flow path",
                span=fn.body.span,
                help="add a `return` at the end of the function, or ensure every `if` branch returns",
            ))

    # -- return-path analysis ------------------------------------------

    def _block_always_returns(self, block: ast.Block) -> bool:
        """Return True iff every reachable path through `block` terminates
        via a `return`, `break`, or `continue`.

        `break`/`continue` count as "terminating" because control leaves
        the block — the enclosing loop's continuation logic decides
        whether the function ultimately returns.
        """

        for stmt in block.statements:
            if self._stmt_always_exits(stmt):
                return True
        return False

    def _stmt_always_exits(self, stmt: ast.Statement) -> bool:
        """Return True iff executing `stmt` always transfers control out
        of the enclosing block (via `return`, `break`, or `continue`)."""

        if isinstance(stmt, (ast.ReturnStatement, ast.BreakStatement, ast.ContinueStatement)):
            return True
        if isinstance(stmt, ast.Block):
            return self._block_always_returns(stmt)
        if isinstance(stmt, ast.IfStatement):
            return self._if_always_exits(stmt)
        if isinstance(stmt, ast.WhileStatement):
            # `while true { ... }` with a body that always exits is an
            # infinite loop that never falls through.  We only treat it
            # as "always exits" if the condition is the literal `true`,
            # otherwise the loop may not execute at all and execution
            # would fall through to the next statement.
            if isinstance(stmt.condition, ast.BooleanLiteral) and stmt.condition.value:
                return self._block_always_returns(stmt.body)
            return False
        if isinstance(stmt, ast.ForStatement):
            # A for-range `for i in 0..0 { }` may not execute; we can't
            # statically prove the body always runs.  Conservative: False.
            return False
        # Let / Assignment / ExpressionStatement — never exits.
        return False

    def _if_always_exits(self, stmt: ast.IfStatement) -> bool:
        """An `if` always exits iff both branches always exit (or the
        condition is statically `true` and the then-branch exits)."""

        # If condition is literally `true`, only the then-branch matters.
        if isinstance(stmt.condition, ast.BooleanLiteral) and stmt.condition.value:
            return self._block_always_returns(stmt.then_block)
        # If there's no `else`, the implicit fall-through doesn't exit.
        if stmt.else_branch is None:
            return False
        then_exits = self._block_always_returns(stmt.then_block)
        if isinstance(stmt.else_branch, ast.Block):
            else_exits = self._block_always_returns(stmt.else_branch)
        else:
            # `else if ...` — recurse.
            else_exits = self._stmt_always_exits(stmt.else_branch)
        return then_exits and else_exits

    # -- statements ----------------------------------------------------

    def _check_block(self, block: ast.Block, parent_scope: Scope) -> None:
        scope = Scope(parent=parent_scope)
        for stmt in block.statements:
            self._check_statement(stmt, scope)

    def _check_statement(self, stmt: ast.Statement, scope: Scope) -> None:
        if isinstance(stmt, ast.LetStatement):
            self._check_let(stmt, scope)
        elif isinstance(stmt, ast.ReturnStatement):
            self._check_return(stmt, scope)
        elif isinstance(stmt, ast.IfStatement):
            self._check_if(stmt, scope)
        elif isinstance(stmt, ast.WhileStatement):
            self._check_while(stmt, scope)
        elif isinstance(stmt, ast.ForStatement):
            self._check_for(stmt, scope)
        elif isinstance(stmt, ast.Assignment):
            self._check_assignment(stmt, scope)
        elif isinstance(stmt, ast.ExpressionStatement):
            self._check_expression(stmt.expression, scope)
        elif isinstance(stmt, ast.Block):
            self._check_block(stmt, scope)
        elif isinstance(stmt, (ast.BreakStatement, ast.ContinueStatement)):
            if self._loop_depth == 0:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0104_BREAK_OUTSIDE_LOOP,
                    severity=Severity.ERROR,
                    message=f"`{type(stmt).__name__.lower().replace('statement','')}` outside of a loop",
                    span=stmt.span,
                ))
        else:  # pragma: no cover - defensive
            self.diag.report(Diagnostic(
                code=ErrorCode.E0999_INTERNAL,
                severity=Severity.ERROR,
                message=f"unhandled statement {type(stmt).__name__}",
                span=stmt.span,
            ))

    def _check_let(self, stmt: ast.LetStatement, scope: Scope) -> None:
        # If there is an explicit type annotation, push it as the contextual
        # expected type for the initializer expression.  This lets an empty
        # array literal `[]` inherit the annotation's element type instead
        # of being rejected for "cannot infer element type".
        expected: Optional[Type] = None
        if stmt.type_annotation is not None:
            expected = self._resolve_type_expr(stmt.type_annotation)
        self._expected_type_stack.append(expected)
        try:
            value_ty = self._check_expression(stmt.value, scope)
        finally:
            self._expected_type_stack.pop()
        if stmt.type_annotation is not None:
            ann_ty = expected
            if ann_ty is not UNKNOWN and value_ty is not UNKNOWN and ann_ty is not value_ty:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0217_ANNOTATION_MISMATCH,
                    severity=Severity.ERROR,
                    message=f"type annotation mismatch: expected `{ann_ty}`, found `{value_ty}`",
                    span=stmt.value.span,
                    help=f"change the annotation to `{value_ty}` or change the initializer",
                ))
            final_ty = ann_ty
        else:
            if value_ty is UNKNOWN:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0200_TYPE_MISMATCH,
                    severity=Severity.ERROR,
                    message="cannot infer type of `let` binding with no annotation",
                    span=stmt.span,
                ))
            final_ty = value_ty
        sym = Symbol(
            name=stmt.name,
            kind=SymbolKind.VARIABLE,
            span=stmt.span,
            type=final_ty,
            mutable=stmt.mutable,
            decl=stmt,
        )
        if not scope.declare(sym):
            self.diag.report(Diagnostic(
                code=ErrorCode.E0101_DUPLICATE_DECLARATION,
                severity=Severity.ERROR,
                message=f"duplicate declaration of `{stmt.name}` in this scope",
                span=stmt.span,
            ))
        _attach_resolved(stmt, sym)

    def _check_return(self, stmt: ast.ReturnStatement, scope: Scope) -> None:
        if stmt.value is None:
            if self._current_function_return is not self.ctx.VOID:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0216_RETURN_WITHOUT_VALUE,
                    severity=Severity.ERROR,
                    message=f"`return` without a value in a function returning `{self._current_function_return}`",
                    span=stmt.span,
                ))
            return
        if self._current_function_return is self.ctx.VOID:
            self.diag.report(Diagnostic(
                code=ErrorCode.E0215_RETURN_IN_VOID,
                severity=Severity.ERROR,
                message="`return` with a value in a `Void` function",
                span=stmt.value.span,
            ))
            # Still type-check the expression for cascading errors.
            self._check_expression(stmt.value, scope)
            return
        ty = self._check_expression(stmt.value, scope)
        if ty is UNKNOWN or self._current_function_return is UNKNOWN:
            return
        if ty is not self._current_function_return:
            self.diag.report(Diagnostic(
                code=ErrorCode.E0214_RETURN_TYPE_MISMATCH,
                severity=Severity.ERROR,
                message=f"return type mismatch: expected `{self._current_function_return}`, found `{ty}`",
                span=stmt.value.span,
            ))

    def _check_if(self, stmt: ast.IfStatement, scope: Scope) -> None:
        cond_ty = self._check_expression(stmt.condition, scope)
        if cond_ty is not UNKNOWN and cond_ty is not self.ctx.BOOL:
            self.diag.report(Diagnostic(
                code=ErrorCode.E0203_CONDITION_NOT_BOOL,
                severity=Severity.ERROR,
                message=f"`if` condition must be `Bool`, found `{cond_ty}`",
                span=stmt.condition.span,
            ))
        self._check_block(stmt.then_block, scope)
        if stmt.else_branch is not None:
            if isinstance(stmt.else_branch, ast.Block):
                self._check_block(stmt.else_branch, scope)
            else:
                # IfStatement
                self._check_statement(stmt.else_branch, scope)

    def _check_while(self, stmt: ast.WhileStatement, scope: Scope) -> None:
        cond_ty = self._check_expression(stmt.condition, scope)
        if cond_ty is not UNKNOWN and cond_ty is not self.ctx.BOOL:
            self.diag.report(Diagnostic(
                code=ErrorCode.E0203_CONDITION_NOT_BOOL,
                severity=Severity.ERROR,
                message=f"`while` condition must be `Bool`, found `{cond_ty}`",
                span=stmt.condition.span,
            ))
        self._loop_depth += 1
        try:
            self._check_block(stmt.body, scope)
        finally:
            self._loop_depth -= 1

    def _check_for(self, stmt: ast.ForStatement, scope: Scope) -> None:
        # Resolve iterable type.
        iter_ty = self._check_expression(stmt.iterable, scope)
        element_ty: Type
        if isinstance(stmt.iterable, ast.RangeExpression):
            if iter_ty is not UNKNOWN and iter_ty is not self.ctx.INT:
                # Actually range expressions always produce Int; the type
                # checker should have already verified operands are Int.
                pass
            element_ty = self.ctx.INT
        else:
            if isinstance(iter_ty, ArrayType):
                element_ty = iter_ty.element_type
            else:
                if iter_ty is not UNKNOWN:
                    self.diag.report(Diagnostic(
                        code=ErrorCode.E0206_INDEX_NOT_ARRAY,
                        severity=Severity.ERROR,
                        message=f"`for` over non-array type `{iter_ty}`",
                        span=stmt.iterable.span,
                    ))
                element_ty = UNKNOWN
        body_scope = Scope(parent=scope)
        sym = Symbol(
            name=stmt.var,
            kind=SymbolKind.VARIABLE,
            span=stmt.span,
            type=element_ty,
            mutable=False,
        )
        body_scope.declare(sym)
        self._loop_depth += 1
        try:
            self._check_block(stmt.body, body_scope)
        finally:
            self._loop_depth -= 1

    def _check_assignment(self, stmt: ast.Assignment, scope: Scope) -> None:
        target_ty = self._check_lvalue(stmt.target, scope)
        value_ty = self._check_expression(stmt.value, scope)
        # Mutability check.
        sym = _get_resolved(stmt.target)
        if sym is not None and not sym.mutable and sym.kind is not SymbolKind.PARAMETER:
            self.diag.report(Diagnostic(
                code=ErrorCode.E0301_IMMUTABLE_ASSIGNMENT,
                severity=Severity.ERROR,
                message=f"cannot assign to immutable variable `{sym.name}`",
                span=stmt.span,
                help="declare the variable as mutable with `let mut`",
            ))
        if (target_ty is not UNKNOWN and value_ty is not UNKNOWN
                and target_ty is not value_ty):
            self.diag.report(Diagnostic(
                code=ErrorCode.E0201_INVALID_ASSIGNMENT,
                severity=Severity.ERROR,
                message=f"cannot assign `{value_ty}` to `{target_ty}`",
                span=stmt.span,
            ))

    def _check_lvalue(self, expr: ast.Expression, scope: Scope) -> Type:
        if isinstance(expr, ast.IdentifierExpression):
            sym = scope.lookup(expr.name)
            if sym is None:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0100_UNKNOWN_IDENTIFIER,
                    severity=Severity.ERROR,
                    message=f"unknown identifier `{expr.name}`",
                    span=expr.span,
                ))
                _attach_type(expr, UNKNOWN)
                return UNKNOWN
            _attach_resolved(expr, sym)
            _attach_type(expr, sym.type or UNKNOWN)
            return sym.type or UNKNOWN
        if isinstance(expr, ast.IndexExpression):
            target_ty = self._check_lvalue(expr.target, scope)
            idx_ty = self._check_expression(expr.index, scope)
            if isinstance(target_ty, ArrayType):
                elem_ty = target_ty.element_type
            else:
                if target_ty is not UNKNOWN:
                    self.diag.report(Diagnostic(
                        code=ErrorCode.E0206_INDEX_NOT_ARRAY,
                        severity=Severity.ERROR,
                        message=f"cannot index `{target_ty}`",
                        span=expr.span,
                    ))
                elem_ty = UNKNOWN
            if idx_ty is not UNKNOWN and idx_ty is not self.ctx.INT:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0207_INDEX_NOT_INT,
                    severity=Severity.ERROR,
                    message=f"array index must be `Int`, found `{idx_ty}`",
                    span=expr.index.span,
                ))
            _attach_type(expr, elem_ty)
            return elem_ty
        if isinstance(expr, ast.FieldAccessExpression):
            target_ty = self._check_lvalue(expr.target, scope)
            if isinstance(target_ty, StructType):
                ft = target_ty.field_type(expr.field)
                if ft is None:
                    self.diag.report(Diagnostic(
                        code=ErrorCode.E0103_UNKNOWN_FIELD,
                        severity=Severity.ERROR,
                        message=f"struct `{target_ty.struct_name}` has no field `{expr.field}`",
                        span=expr.span,
                    ))
                    ft = UNKNOWN
                # Inherit mutability from the target's root.
                _attach_resolved(expr, _get_resolved(expr.target))
                _attach_type(expr, ft)
                return ft
            if target_ty is not UNKNOWN:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0208_FIELD_ACCESS_NOT_STRUCT,
                    severity=Severity.ERROR,
                    message=f"cannot access field `{expr.field}` on non-struct type `{target_ty}`",
                    span=expr.span,
                ))
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        self.diag.report(Diagnostic(
            code=ErrorCode.E0201_INVALID_ASSIGNMENT,
            severity=Severity.ERROR,
            message="invalid assignment target",
            span=expr.span,
        ))
        _attach_type(expr, UNKNOWN)
        return UNKNOWN

    # -- expressions ---------------------------------------------------

    def _check_expression(self, expr: ast.Expression, scope: Scope) -> Type:
        if isinstance(expr, ast.IntegerLiteral):
            _attach_type(expr, self.ctx.INT)
            return self.ctx.INT
        if isinstance(expr, ast.FloatLiteral):
            _attach_type(expr, self.ctx.FLOAT)
            return self.ctx.FLOAT
        if isinstance(expr, ast.StringLiteral):
            _attach_type(expr, self.ctx.STRING)
            return self.ctx.STRING
        if isinstance(expr, ast.BooleanLiteral):
            _attach_type(expr, self.ctx.BOOL)
            return self.ctx.BOOL
        if isinstance(expr, ast.IdentifierExpression):
            sym = scope.lookup(expr.name)
            if sym is None:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0100_UNKNOWN_IDENTIFIER,
                    severity=Severity.ERROR,
                    message=f"unknown identifier `{expr.name}`",
                    span=expr.span,
                ))
                _attach_type(expr, UNKNOWN)
                return UNKNOWN
            _attach_resolved(expr, sym)
            assert sym.type is not None
            _attach_type(expr, sym.type)
            return sym.type
        if isinstance(expr, ast.ArrayLiteral):
            elem_ty: Type = UNKNOWN
            # Contextual element type: if this array literal appears as
            # `let xs: [T] = [...]` and is empty, inherit `T` from the
            # annotation.  Otherwise, infer from the elements.
            contextual_elem_ty: Optional[Type] = None
            if self._expected_type_stack and self._expected_type_stack[-1] is not None:
                maybe_arr = self._expected_type_stack[-1]
                if isinstance(maybe_arr, ArrayType):
                    contextual_elem_ty = maybe_arr.element_type
            for el in expr.elements:
                t = self._check_expression(el, scope)
                if elem_ty is UNKNOWN:
                    elem_ty = t
                elif t is not UNKNOWN and t is not elem_ty:
                    self.diag.report(Diagnostic(
                        code=ErrorCode.E0212_HETEROGENEOUS_ARRAY,
                        severity=Severity.ERROR,
                        message=f"array literal has heterogeneous element types: `{elem_ty}` and `{t}`",
                        span=el.span,
                    ))
            if elem_ty is UNKNOWN and not expr.elements:
                if contextual_elem_ty is not None and contextual_elem_ty is not UNKNOWN:
                    # Empty array literal with an explicit element-type
                    # annotation: inherit the annotation's element type.
                    elem_ty = contextual_elem_ty
                else:
                    self.diag.report(Diagnostic(
                        code=ErrorCode.E0213_EMPTY_ARRAY_NO_TYPE,
                        severity=Severity.ERROR,
                        message="cannot infer type of empty array literal — add an explicit annotation",
                        span=expr.span,
                    ))
            arr_ty = self.ctx.array(elem_ty)
            _attach_type(expr, arr_ty)
            return arr_ty
        if isinstance(expr, ast.StructLiteral):
            return self._check_struct_literal(expr, scope)
        if isinstance(expr, ast.BinaryOp):
            return self._check_binary(expr, scope)
        if isinstance(expr, ast.UnaryOp):
            return self._check_unary(expr, scope)
        if isinstance(expr, ast.CallExpression):
            return self._check_call(expr, scope)
        if isinstance(expr, ast.IndexExpression):
            target_ty = self._check_expression(expr.target, scope)
            idx_ty = self._check_expression(expr.index, scope)
            if isinstance(target_ty, ArrayType):
                elem_ty = target_ty.element_type
            else:
                if target_ty is not UNKNOWN:
                    self.diag.report(Diagnostic(
                        code=ErrorCode.E0206_INDEX_NOT_ARRAY,
                        severity=Severity.ERROR,
                        message=f"cannot index `{target_ty}`",
                        span=expr.span,
                    ))
                elem_ty = UNKNOWN
            if idx_ty is not UNKNOWN and idx_ty is not self.ctx.INT:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0207_INDEX_NOT_INT,
                    severity=Severity.ERROR,
                    message=f"array index must be `Int`, found `{idx_ty}`",
                    span=expr.index.span,
                ))
            _attach_type(expr, elem_ty)
            return elem_ty
        if isinstance(expr, ast.FieldAccessExpression):
            # Special case: `EnumName.VariantName` — the target is an
            # identifier referring to an enum type, not a struct value.
            # We rewrite this into an `EnumVariantExpression` semantically
            # (without mutating the AST) and attach the enum type.
            if isinstance(expr.target, ast.IdentifierExpression):
                sym = scope.lookup(expr.target.name)
                if sym is not None and sym.kind is SymbolKind.ENUM:
                    enum_type = sym.type
                    assert isinstance(enum_type, EnumType)
                    if not enum_type.has_variant(expr.field):
                        self.diag.report(Diagnostic(
                            code=ErrorCode.E0220_UNKNOWN_VARIANT,
                            severity=Severity.ERROR,
                            message=f"enum `{enum_type.enum_name}` has no variant `{expr.field}`",
                            span=expr.span,
                        ))
                        _attach_type(expr, UNKNOWN)
                        return UNKNOWN
                    # Mark this FieldAccessExpression as an enum variant
                    # reference so the IR lowering can lower it to a
                    # ConstInt (the variant's tag).
                    setattr(expr, "_enum_variant", (enum_type, expr.field))
                    _attach_type(expr, enum_type)
                    return enum_type
            target_ty = self._check_expression(expr.target, scope)
            if isinstance(target_ty, StructType):
                ft = target_ty.field_type(expr.field)
                if ft is None:
                    self.diag.report(Diagnostic(
                        code=ErrorCode.E0103_UNKNOWN_FIELD,
                        severity=Severity.ERROR,
                        message=f"struct `{target_ty.struct_name}` has no field `{expr.field}`",
                        span=expr.span,
                    ))
                    ft = UNKNOWN
                _attach_type(expr, ft)
                return ft
            if target_ty is not UNKNOWN:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0208_FIELD_ACCESS_NOT_STRUCT,
                    severity=Severity.ERROR,
                    message=f"cannot access field `{expr.field}` on non-struct type `{target_ty}`",
                    span=expr.span,
                ))
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        if isinstance(expr, ast.RangeExpression):
            start_ty = self._check_expression(expr.start, scope)
            end_ty = self._check_expression(expr.end, scope)
            if start_ty is not UNKNOWN and start_ty is not self.ctx.INT:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0200_TYPE_MISMATCH,
                    severity=Severity.ERROR,
                    message=f"range start must be `Int`, found `{start_ty}`",
                    span=expr.start.span,
                ))
            if end_ty is not UNKNOWN and end_ty is not self.ctx.INT:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0200_TYPE_MISMATCH,
                    severity=Severity.ERROR,
                    message=f"range end must be `Int`, found `{end_ty}`",
                    span=expr.end.span,
                ))
            _attach_type(expr, self.ctx.INT)   # range itself has type Int (the element type)
            return self.ctx.INT
        if isinstance(expr, ast.MatchExpression):
            return self._check_match(expr, scope)
        self.diag.report(Diagnostic(
            code=ErrorCode.E0999_INTERNAL,
            severity=Severity.ERROR,
            message=f"unhandled expression {type(expr).__name__}",
            span=expr.span,
        ))
        _attach_type(expr, UNKNOWN)
        return UNKNOWN

    def _check_struct_literal(self, expr: ast.StructLiteral, scope: Scope) -> Type:
        sym = self.global_scope.lookup(expr.type_name)
        if sym is None or sym.kind is not SymbolKind.STRUCT:
            self.diag.report(Diagnostic(
                code=ErrorCode.E0102_UNKNOWN_TYPE,
                severity=Severity.ERROR,
                message=f"unknown struct `{expr.type_name}`",
                span=expr.span,
            ))
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        struct_type = sym.type
        assert isinstance(struct_type, StructType)
        seen: set[str] = set()
        for f in expr.fields:
            if f.name in seen:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0211_DUPLICATE_STRUCT_FIELD,
                    severity=Severity.ERROR,
                    message=f"duplicate field `{f.name}` in struct literal",
                    span=f.span,
                ))
                continue
            seen.add(f.name)
            declared_ty = struct_type.field_type(f.name)
            if declared_ty is None:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0210_UNKNOWN_STRUCT_FIELD,
                    severity=Severity.ERROR,
                    message=f"struct `{expr.type_name}` has no field `{f.name}`",
                    span=f.span,
                ))
                self._check_expression(f.value, scope)
                continue
            value_ty = self._check_expression(f.value, scope)
            if value_ty is not UNKNOWN and value_ty is not declared_ty:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0200_TYPE_MISMATCH,
                    severity=Severity.ERROR,
                    message=f"field `{f.name}` expects `{declared_ty}`, found `{value_ty}`",
                    span=f.value.span,
                ))
        for fn, _ in struct_type.fields:
            if fn not in seen:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0209_MISSING_STRUCT_FIELD,
                    severity=Severity.ERROR,
                    message=f"missing field `{fn}` in struct literal of type `{expr.type_name}`",
                    span=expr.span,
                ))
        _attach_resolved(expr, sym)
        _attach_type(expr, struct_type)
        return struct_type

    def _check_match(self, expr: ast.MatchExpression, scope: Scope) -> Type:
        """Type-check a `match` expression.

        Semantics:
          * The scrutinee must be an enum value.
          * Each arm's pattern must be either:
              - `EnumName.VariantName` for one of the enum's variants, OR
              - `_` (wildcard — matches anything, must come last).
          * All arm body expressions must have the same type — that's
            the type of the match expression.
          * The match must be exhaustive: either there's a `_` arm, or
            every variant of the enum is covered by some arm.
          * Variants may not be matched more than once.
          * A `_` arm may not appear before any variant arm (it would
            shadow them).
        """

        scrutinee_ty = self._check_expression(expr.scrutinee, scope)
        if not isinstance(scrutinee_ty, EnumType):
            if scrutinee_ty is not UNKNOWN:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0221_MATCH_SCRUTINEE_NOT_ENUM,
                    severity=Severity.ERROR,
                    message=f"`match` scrutinee must be an enum, found `{scrutinee_ty}`",
                    span=expr.scrutinee.span,
                ))
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        # Check arms.
        seen_variants: set[str] = set()
        has_wildcard: bool = False
        has_wildcard_before_variants: bool = False
        arm_types: list[Type] = []
        for arm in expr.arms:
            if isinstance(arm.pattern, ast.WildcardPattern):
                if has_wildcard:
                    self.diag.report(Diagnostic(
                        code=ErrorCode.E0223_DUPLICATE_MATCH_ARM,
                        severity=Severity.ERROR,
                        message="duplicate `_` arm in match",
                        span=arm.pattern.span,
                    ))
                has_wildcard = True
                # If there are subsequent arms after the wildcard, that's
                # a logic error (they'd be unreachable).
                # We don't enforce that here — but we mark has_wildcard.
            elif isinstance(arm.pattern, ast.EnumVariantPattern):
                # Pattern must reference the same enum as the scrutinee.
                if arm.pattern.enum_name != scrutinee_ty.enum_name:
                    self.diag.report(Diagnostic(
                        code=ErrorCode.E0220_UNKNOWN_VARIANT,
                        severity=Severity.ERROR,
                        message=(
                            f"match arm references enum `{arm.pattern.enum_name}` "
                            f"but scrutinee is enum `{scrutinee_ty.enum_name}`"
                        ),
                        span=arm.pattern.span,
                    ))
                elif not scrutinee_ty.has_variant(arm.pattern.variant_name):
                    self.diag.report(Diagnostic(
                        code=ErrorCode.E0220_UNKNOWN_VARIANT,
                        severity=Severity.ERROR,
                        message=(
                            f"enum `{scrutinee_ty.enum_name}` has no variant "
                            f"`{arm.pattern.variant_name}`"
                        ),
                        span=arm.pattern.span,
                    ))
                else:
                    if arm.pattern.variant_name in seen_variants:
                        self.diag.report(Diagnostic(
                            code=ErrorCode.E0223_DUPLICATE_MATCH_ARM,
                            severity=Severity.ERROR,
                            message=f"duplicate match arm for `{scrutinee_ty.enum_name}.{arm.pattern.variant_name}`",
                            span=arm.pattern.span,
                        ))
                    seen_variants.add(arm.pattern.variant_name)
                    if has_wildcard:
                        has_wildcard_before_variants = True
            # Check the arm body and collect its type.
            body_ty = self._check_expression(arm.body, scope)
            arm_types.append(body_ty)
        # If wildcard appears before variants, those variants are unreachable.
        if has_wildcard_before_variants:
            self.diag.report(Diagnostic(
                code=ErrorCode.E0223_DUPLICATE_MATCH_ARM,
                severity=Severity.ERROR,
                message="`_` arm must come last — subsequent variant arms are unreachable",
                span=expr.span,
            ))
        # Exhaustiveness check.
        if not has_wildcard:
            missing = [v for v in scrutinee_ty.variant_names if v not in seen_variants]
            if missing:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0219_NON_EXHAUSTIVE_MATCH,
                    severity=Severity.ERROR,
                    message=(
                        f"non-exhaustive match: missing variants "
                        f"{', '.join(repr(v) for v in missing)}"
                    ),
                    span=expr.span,
                    help="add an arm for each missing variant, or a `_` arm",
                ))
        # Compute the result type.  All arm bodies must agree.
        result_ty: Type = UNKNOWN
        for at in arm_types:
            if at is UNKNOWN:
                continue
            if result_ty is UNKNOWN:
                result_ty = at
            elif at is not result_ty:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0222_MATCH_ARM_TYPE_MISMATCH,
                    severity=Severity.ERROR,
                    message=f"match arms have different types: `{result_ty}` and `{at}`",
                    span=expr.span,
                ))
        _attach_type(expr, result_ty)
        return result_ty

    def _check_binary(self, expr: ast.BinaryOp, scope: Scope) -> Type:
        lt = self._check_expression(expr.left, scope)
        rt = self._check_expression(expr.right, scope)
        op = expr.op
        if lt is UNKNOWN or rt is UNKNOWN:
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        # String concat
        if op == "+" and lt is self.ctx.STRING and rt is self.ctx.STRING:
            _attach_type(expr, self.ctx.STRING)
            return self.ctx.STRING
        # Boolean
        if op in ("&&", "||"):
            if lt is self.ctx.BOOL and rt is self.ctx.BOOL:
                _attach_type(expr, self.ctx.BOOL)
                return self.ctx.BOOL
            self.diag.report(Diagnostic(
                code=ErrorCode.E0205_INVALID_BINARY,
                severity=Severity.ERROR,
                message=f"`{op}` requires `Bool` operands, found `{lt}` and `{rt}`",
                span=expr.span,
            ))
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        # Comparison
        if op in ("==", "!="):
            if lt is rt and lt in (self.ctx.INT, self.ctx.FLOAT, self.ctx.BOOL, self.ctx.STRING):
                _attach_type(expr, self.ctx.BOOL)
                return self.ctx.BOOL
            self.diag.report(Diagnostic(
                code=ErrorCode.E0205_INVALID_BINARY,
                severity=Severity.ERROR,
                message=f"`{op}` requires operands of the same primitive type, found `{lt}` and `{rt}`",
                span=expr.span,
            ))
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        if op in ("<", ">", "<=", ">="):
            if lt is rt and lt in (self.ctx.INT, self.ctx.FLOAT):
                _attach_type(expr, self.ctx.BOOL)
                return self.ctx.BOOL
            self.diag.report(Diagnostic(
                code=ErrorCode.E0205_INVALID_BINARY,
                severity=Severity.ERROR,
                message=f"`{op}` requires numeric operands of the same type, found `{lt}` and `{rt}`",
                span=expr.span,
            ))
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        # Arithmetic
        if op in ("+", "-", "*", "/", "%"):
            if lt is rt and lt in (self.ctx.INT, self.ctx.FLOAT):
                # Compile-time check: division/modulo by a literal zero.
                # This is undefined behavior in LLVM (sdiv by 0 / fdiv by 0.0
                # produce poison values).  We emit a warning rather than an
                # error so users can still write `1 / 0` if they really want
                # to, but the behavior is explicitly documented as UB.
                if op in ("/", "%") and isinstance(expr.right, ast.IntegerLiteral) and expr.right.value == 0:
                    self.diag.report(Diagnostic(
                        code=ErrorCode.W0001_DIVISION_BY_ZERO,
                        severity=Severity.WARNING,
                        message=f"`{op}` by zero is undefined behavior",
                        span=expr.span,
                        help="this will produce a poison value at runtime; guard with an explicit check",
                    ))
                elif op in ("/", "%") and isinstance(expr.right, ast.FloatLiteral) and expr.right.value == 0.0:
                    self.diag.report(Diagnostic(
                        code=ErrorCode.W0001_DIVISION_BY_ZERO,
                        severity=Severity.WARNING,
                        message=f"`{op}` by 0.0 is undefined behavior",
                        span=expr.span,
                        help="this will produce NaN/Inf at runtime; guard with an explicit check",
                    ))
                _attach_type(expr, lt)
                return lt
            self.diag.report(Diagnostic(
                code=ErrorCode.E0205_INVALID_BINARY,
                severity=Severity.ERROR,
                message=f"`{op}` requires numeric operands of the same type, found `{lt}` and `{rt}`",
                span=expr.span,
            ))
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        self.diag.report(Diagnostic(
            code=ErrorCode.E0999_INTERNAL,
            severity=Severity.ERROR,
            message=f"unhandled binary operator `{op}`",
            span=expr.span,
        ))
        _attach_type(expr, UNKNOWN)
        return UNKNOWN

    def _check_unary(self, expr: ast.UnaryOp, scope: Scope) -> Type:
        operand_ty = self._check_expression(expr.operand, scope)
        if expr.op == "-":
            if operand_ty in (self.ctx.INT, self.ctx.FLOAT):
                _attach_type(expr, operand_ty)
                return operand_ty
            self.diag.report(Diagnostic(
                code=ErrorCode.E0204_INVALID_UNARY,
                severity=Severity.ERROR,
                message=f"unary `-` requires `Int` or `Float`, found `{operand_ty}`",
                span=expr.span,
            ))
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        if expr.op == "!":
            if operand_ty is self.ctx.BOOL:
                _attach_type(expr, self.ctx.BOOL)
                return self.ctx.BOOL
            self.diag.report(Diagnostic(
                code=ErrorCode.E0204_INVALID_UNARY,
                severity=Severity.ERROR,
                message=f"unary `!` requires `Bool`, found `{operand_ty}`",
                span=expr.span,
            ))
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        self.diag.report(Diagnostic(
            code=ErrorCode.E0999_INTERNAL,
            severity=Severity.ERROR,
            message=f"unhandled unary operator `{expr.op}`",
            span=expr.span,
        ))
        _attach_type(expr, UNKNOWN)
        return UNKNOWN

    def _check_call(self, expr: ast.CallExpression, scope: Scope) -> Type:
        if not isinstance(expr.callee, ast.IdentifierExpression):
            self.diag.report(Diagnostic(
                code=ErrorCode.E0999_INTERNAL,
                severity=Severity.ERROR,
                message="only direct function calls are supported in v0.1",
                span=expr.span,
            ))
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        # Resolve callee.
        name = expr.callee.name
        sym = scope.lookup(name)
        if sym is None or sym.kind is not SymbolKind.FUNCTION:
            self.diag.report(Diagnostic(
                code=ErrorCode.E0105_UNKNOWN_FUNCTION,
                severity=Severity.ERROR,
                message=f"unknown function `{name}`",
                span=expr.callee.span,
            ))
            # Still type-check arguments so cascading errors are useful.
            for a in expr.arguments:
                self._check_expression(a, scope)
            _attach_type(expr, UNKNOWN)
            return UNKNOWN
        _attach_resolved(expr, sym)
        _attach_resolved(expr.callee, sym)

        # Check argument count and types.
        # Builtin print/println accept exactly one of {Int, Float, Bool, String}.
        if sym.is_builtin and name in ("print", "println"):
            if len(expr.arguments) != 1:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0202_INVALID_FUNCTION_CALL,
                    severity=Severity.ERROR,
                    message=f"`{name}` takes exactly one argument, got {len(expr.arguments)}",
                    span=expr.span,
                ))
                for a in expr.arguments:
                    self._check_expression(a, scope)
                _attach_type(expr, self.ctx.VOID)
                return self.ctx.VOID
            arg_ty = self._check_expression(expr.arguments[0], scope)
            if arg_ty is not UNKNOWN and arg_ty not in (
                self.ctx.INT, self.ctx.FLOAT, self.ctx.BOOL, self.ctx.STRING,
            ):
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0202_INVALID_FUNCTION_CALL,
                    severity=Severity.ERROR,
                    message=f"`{name}` does not accept `{arg_ty}` — only `Int`, `Float`, `Bool`, or `String`",
                    span=expr.span,
                ))
            _attach_type(expr, self.ctx.VOID)
            return self.ctx.VOID
        if sym.is_builtin and name == "length":
            if len(expr.arguments) != 1:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0202_INVALID_FUNCTION_CALL,
                    severity=Severity.ERROR,
                    message=f"`length` takes exactly one argument, got {len(expr.arguments)}",
                    span=expr.span,
                ))
                _attach_type(expr, self.ctx.INT)
                return self.ctx.INT
            arg_ty = self._check_expression(expr.arguments[0], scope)
            if arg_ty is not UNKNOWN and not isinstance(arg_ty, ArrayType) and arg_ty is not self.ctx.STRING:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0202_INVALID_FUNCTION_CALL,
                    severity=Severity.ERROR,
                    message=f"`length` requires `String` or array, found `{arg_ty}`",
                    span=expr.span,
                ))
            _attach_type(expr, self.ctx.INT)
            return self.ctx.INT

        # User-defined function.
        finfo = self.functions.get(name)
        assert finfo is not None
        if len(expr.arguments) != len(finfo.param_types):
            self.diag.report(Diagnostic(
                code=ErrorCode.E0202_INVALID_FUNCTION_CALL,
                severity=Severity.ERROR,
                message=f"function `{name}` expects {len(finfo.param_types)} argument(s), got {len(expr.arguments)}",
                span=expr.span,
            ))
            for a in expr.arguments:
                self._check_expression(a, scope)
            _attach_type(expr, finfo.return_type)
            return finfo.return_type
        for arg, pty in zip(expr.arguments, finfo.param_types):
            aty = self._check_expression(arg, scope)
            if aty is not UNKNOWN and pty is not UNKNOWN and aty is not pty:
                self.diag.report(Diagnostic(
                    code=ErrorCode.E0202_INVALID_FUNCTION_CALL,
                    severity=Severity.ERROR,
                    message=f"argument to `{name}` expects `{pty}`, found `{aty}`",
                    span=arg.span,
                ))
        _attach_type(expr, finfo.return_type)
        return finfo.return_type
