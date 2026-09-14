"""Symbols for the Dextra semantic analyzer.

A `Symbol` represents any named entity that can be referenced:
variables, parameters, functions, structs, struct fields, enums.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from dextra.ast.nodes import (
        EnumDeclaration,
        FieldDecl,
        FunctionDeclaration,
        Parameter,
        StructDeclaration,
    )
    from dextra.lexer.source import SourceSpan
    from dextra.types.type_system import Type


class SymbolKind(Enum):
    VARIABLE = "variable"
    PARAMETER = "parameter"
    FUNCTION = "function"
    STRUCT = "struct"
    ENUM = "enum"


@dataclass
class Symbol:
    name: str
    kind: SymbolKind
    span: "SourceSpan"
    type: "Type | None" = None
    mutable: bool = False

    # For function / struct / enum symbols we keep a back-pointer to the
    # AST node so downstream stages can find the body / fields / variants.
    decl: object | None = None  # FunctionDeclaration | StructDeclaration | EnumDeclaration
    is_builtin: bool = False


@dataclass
class StructInfo:
    """Bookkeeping struct attached to a struct symbol."""

    decl: "StructDeclaration"
    field_types: dict[str, "Type"]
    field_indices: dict[str, int]


@dataclass
class EnumInfo:
    """Bookkeeping struct attached to an enum symbol."""

    decl: "EnumDeclaration"
    variant_names: list[str]
    variant_indices: dict[str, int]


@dataclass
class FunctionInfo:
    """Bookkeeping struct attached to a function symbol."""

    decl: "FunctionDeclaration"
    param_types: list["Type"]
    return_type: "Type"
    is_builtin: bool = False
