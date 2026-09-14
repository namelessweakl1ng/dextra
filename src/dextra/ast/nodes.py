"""Dextra AST node definitions.

Each node carries a `SourceSpan` for diagnostics.  Nodes are dataclasses
so equality / hashing are automatic.

The AST is purely syntactic — types and resolved symbols are attached
later by the semantic / type-checking stages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Union

from dextra.lexer.source import SourceSpan

if TYPE_CHECKING:
    pass


# -- shared helpers ------------------------------------------------------


# -- top level -----------------------------------------------------------

@dataclass
class Program:
    declarations: list["Declaration"]
    span: SourceSpan

    def __post_init__(self) -> None:
        if not self.declarations:
            return
        spans = [d.span for d in self.declarations if d is not None]
        if spans:
            self.span = spans[0]
            for s in spans[1:]:
                self.span = self.span.merge(s)


class Declaration:
    """Marker base class for top-level declarations."""

    span: SourceSpan


# -- type AST nodes ------------------------------------------------------


class TypeExpr:
    """Marker base class for type expressions."""

    span: SourceSpan


@dataclass
class PrimitiveTypeExpr(TypeExpr):
    name: str  # "Int" | "Float" | "Bool" | "String" | "Void"
    span: SourceSpan


@dataclass
class ArrayTypeExpr(TypeExpr):
    element: TypeExpr
    span: SourceSpan


@dataclass
class NamedTypeExpr(TypeExpr):
    name: str
    span: SourceSpan


# -- declarations --------------------------------------------------------


@dataclass
class Parameter:
    name: str
    type: TypeExpr
    span: SourceSpan


@dataclass
class FunctionDeclaration(Declaration):
    name: str
    parameters: list[Parameter]
    return_type: Optional[TypeExpr]   # None means implicit Void
    body: "Block"
    span: SourceSpan


@dataclass
class FieldDecl:
    name: str
    type: TypeExpr
    span: SourceSpan


@dataclass
class StructDeclaration(Declaration):
    name: str
    fields: list[FieldDecl]
    span: SourceSpan


@dataclass
class EnumVariant:
    """A single variant in an enum declaration.

    v0.1 enums are *unit* variants only (no payload).  The variant carries
    a stable tag (its index in the declaration order, 0-based).  Variants
    of the same enum are referred to as `EnumName.VariantName`.
    """

    name: str
    span: SourceSpan


@dataclass
class EnumDeclaration(Declaration):
    name: str
    variants: list[EnumVariant]
    span: SourceSpan


# -- statements ----------------------------------------------------------


class Statement:
    """Marker base class for statements."""

    span: SourceSpan


@dataclass
class Block(Statement):
    statements: list[Statement]
    span: SourceSpan


@dataclass
class LetStatement(Statement):
    name: str
    mutable: bool
    type_annotation: Optional[TypeExpr]
    value: "Expression"
    span: SourceSpan


@dataclass
class ReturnStatement(Statement):
    value: Optional["Expression"]
    span: SourceSpan


@dataclass
class BreakStatement(Statement):
    span: SourceSpan


@dataclass
class ContinueStatement(Statement):
    span: SourceSpan


@dataclass
class IfStatement(Statement):
    condition: "Expression"
    then_block: Block
    else_branch: Optional[Union["IfStatement", Block]]
    span: SourceSpan


@dataclass
class WhileStatement(Statement):
    condition: "Expression"
    body: Block
    span: SourceSpan


@dataclass
class ForStatement(Statement):
    var: str
    iterable: "Expression"   # either RangeExpr or an arbitrary expr
    body: Block
    span: SourceSpan


@dataclass
class Assignment(Statement):
    target: "Expression"     # IdentifierExpression | IndexExpr | FieldAccessExpr
    value: "Expression"
    span: SourceSpan


@dataclass
class ExpressionStatement(Statement):
    expression: "Expression"
    span: SourceSpan


# -- expressions ---------------------------------------------------------


class Expression:
    """Marker base class for expressions."""

    span: SourceSpan


@dataclass
class IntegerLiteral(Expression):
    value: int
    span: SourceSpan


@dataclass
class FloatLiteral(Expression):
    value: float
    span: SourceSpan


@dataclass
class StringLiteral(Expression):
    value: str
    span: SourceSpan


@dataclass
class BooleanLiteral(Expression):
    value: bool
    span: SourceSpan


@dataclass
class IdentifierExpression(Expression):
    name: str
    span: SourceSpan


@dataclass
class ArrayLiteral(Expression):
    elements: list[Expression]
    span: SourceSpan


@dataclass
class StructLiteralField:
    name: str
    value: Expression
    span: SourceSpan


@dataclass
class StructLiteral(Expression):
    type_name: str
    fields: list[StructLiteralField]
    span: SourceSpan


@dataclass
class BinaryOp(Expression):
    op: str   # "+", "-", "*", "/", "%", "==", "!=", "<", ">", "<=", ">=", "&&", "||"
    left: Expression
    right: Expression
    span: SourceSpan


@dataclass
class UnaryOp(Expression):
    op: str   # "-" or "!"
    operand: Expression
    span: SourceSpan


@dataclass
class CallExpression(Expression):
    callee: Expression       # always IdentifierExpression in v0.1
    arguments: list[Expression]
    span: SourceSpan


@dataclass
class IndexExpression(Expression):
    target: Expression
    index: Expression
    span: SourceSpan


@dataclass
class FieldAccessExpression(Expression):
    target: Expression
    field: str
    span: SourceSpan


@dataclass
class RangeExpression(Expression):
    start: Expression
    end: Expression
    span: SourceSpan


# -- patterns (for match) -----------------------------------------------


class Pattern:
    """Marker base class for match patterns."""


@dataclass
class WildcardPattern(Pattern):
    """`_` — matches anything."""

    span: SourceSpan


@dataclass
class EnumVariantPattern(Pattern):
    """`EnumName.VariantName` — matches the given variant.

    For v0.1 enums have no payload, so the pattern carries no sub-pattern.
    """

    enum_name: str
    variant_name: str
    span: SourceSpan


@dataclass
class MatchArm:
    """One arm of a `match` expression.

    `pattern` is matched against the scrutinee; if it matches, `body`
    is evaluated and its value becomes the match's value.
    """

    pattern: Pattern
    body: Expression
    span: SourceSpan


@dataclass
class MatchExpression(Expression):
    """`match scrutinee { arm1, arm2, ... }`.

    The scrutinee must be an enum value (in v0.1; integers may be added
    later).  Arms are tried in order; the first match wins.  Exhaustiveness
    is enforced at compile time for enum scrutinees: either an explicit
    `_` arm exists, or all variants of the enum are covered.
    """

    scrutinee: Expression
    arms: list[MatchArm]
    span: SourceSpan


@dataclass
class EnumVariantExpression(Expression):
    """A reference to an enum variant, e.g. `Color.Red`.

    At runtime this is just the variant's integer tag.  The type is the
    enum type.
    """

    enum_name: str
    variant_name: str
    span: SourceSpan
