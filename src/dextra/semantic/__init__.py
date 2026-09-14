"""Re-exports for the semantic analysis subsystem."""

from dextra.semantic.analyzer import (
    AnalyzedProgram,
    Analyzer,
    get_enum_variant,
    get_resolved,
    get_type,
)
from dextra.semantic.scope import Scope
from dextra.semantic.symbols import EnumInfo, FunctionInfo, StructInfo, Symbol, SymbolKind

__all__ = [
    "AnalyzedProgram",
    "Analyzer",
    "EnumInfo",
    "FunctionInfo",
    "Scope",
    "StructInfo",
    "Symbol",
    "SymbolKind",
    "get_enum_variant",
    "get_resolved",
    "get_type",
]
