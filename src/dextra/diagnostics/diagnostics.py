"""Dextra diagnostics subsystem.

A `Diagnostic` is the unit of compiler feedback.  It carries an error code,
a severity, a message, a source span, optional notes, and optional help text.
The `DiagnosticEngine` collects diagnostics and provides a pretty-printer
that renders them in a Rust-style format.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dextra.lexer.source import SourceFile, SourceSpan


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"
    HELP = "help"


class ErrorCode(Enum):
    # Lexical
    E0001_LEXICAL = "E0001"

    # Syntactic
    E0002_UNEXPECTED_TOKEN = "E0002"
    E0003_EXPECTED_EXPRESSION = "E0003"
    E0004_EXPECTED_RBRACE = "E0004"
    E0005_EXPECTED_RPAREN = "E0005"
    E0006_EXPECTED_RBRACKET = "E0006"
    E0007_EXPECTED_COLON = "E0007"
    E0008_EXPECTED_EQ = "E0008"
    E0009_EXPECTED_ARROW = "E0009"

    # Semantic
    E0100_UNKNOWN_IDENTIFIER = "E0100"
    E0101_DUPLICATE_DECLARATION = "E0101"
    E0102_UNKNOWN_TYPE = "E0102"
    E0103_UNKNOWN_FIELD = "E0103"
    E0104_BREAK_OUTSIDE_LOOP = "E0104"
    E0105_UNKNOWN_FUNCTION = "E0105"

    # Type errors
    E0200_TYPE_MISMATCH = "E0200"
    E0201_INVALID_ASSIGNMENT = "E0201"
    E0202_INVALID_FUNCTION_CALL = "E0202"
    E0203_CONDITION_NOT_BOOL = "E0203"
    E0204_INVALID_UNARY = "E0204"
    E0205_INVALID_BINARY = "E0205"
    E0206_INDEX_NOT_ARRAY = "E0206"
    E0207_INDEX_NOT_INT = "E0207"
    E0208_FIELD_ACCESS_NOT_STRUCT = "E0208"
    E0209_MISSING_STRUCT_FIELD = "E0209"
    E0210_UNKNOWN_STRUCT_FIELD = "E0210"
    E0211_DUPLICATE_STRUCT_FIELD = "E0211"
    E0212_HETEROGENEOUS_ARRAY = "E0212"
    E0213_EMPTY_ARRAY_NO_TYPE = "E0213"
    E0214_RETURN_TYPE_MISMATCH = "E0214"
    E0215_RETURN_IN_VOID = "E0215"
    E0216_RETURN_WITHOUT_VALUE = "E0216"
    E0217_ANNOTATION_MISMATCH = "E0217"
    E0218_MISSING_RETURN = "E0218"
    E0219_NON_EXHAUSTIVE_MATCH = "E0219"
    E0220_UNKNOWN_VARIANT = "E0220"
    E0221_MATCH_SCRUTINEE_NOT_ENUM = "E0221"
    E0222_MATCH_ARM_TYPE_MISMATCH = "E0222"
    E0223_DUPLICATE_MATCH_ARM = "E0223"

    # Mutability
    E0300_UNKNOWN_FIELD_ALIAS = "E0300"
    E0301_IMMUTABLE_ASSIGNMENT = "E0301"
    E0302_IMMUTABLE_FIELD = "E0302"

    # Control flow
    E0400_INVALID_RETURN = "E0400"
    E0401_MAIN_MUST_RETURN_INT = "E0401"

    # Internal
    E0999_INTERNAL = "E0999"

    # Warnings (W-prefixed codes)
    W0001_DIVISION_BY_ZERO = "W0001"


@dataclass
class Diagnostic:
    code: ErrorCode
    severity: Severity
    message: str
    span: "SourceSpan"
    notes: list[str] = field(default_factory=list)
    help: str | None = None

    def render(self, source_files: dict[str, "SourceFile"]) -> str:
        """Render this diagnostic in a human-readable, Rust-style format."""

        from dextra.lexer.source import SourceSpan  # local import to avoid cycles

        lines: list[str] = []
        code_str = self.code.value
        sev_str = self.severity.value
        lines.append(f"{sev_str}[{code_str}]: {self.message}")

        span = self.span
        if span is not None and span.line > 0:
            lines.append(f"  --> {span}")
            src = source_files.get(span.filename)
            if src is not None:
                snippet = src.line_text(span.line)
                lines.append(f"   |")
                lines.append(f"{span.line:>2} | {snippet}")
                # Underline the offending range on the source line.
                col_start = span.start.column
                col_end = max(col_start + 1, span.end.column)
                underline = " " * (len(str(span.line)) + 3 + col_start - 1) + "^" * max(1, col_end - col_start)
                lines.append(f"   | {underline}")

        for note in self.notes:
            lines.append(f"   = note: {note}")
        if self.help is not None:
            lines.append(f"   = help: {self.help}")

        return "\n".join(lines)


class DiagnosticEngine:
    """Collects diagnostics and reports whether compilation should proceed."""

    def __init__(self) -> None:
        self._diagnostics: list[Diagnostic] = []
        self._has_errors = False

    def report(self, diag: Diagnostic) -> None:
        self._diagnostics.append(diag)
        if diag.severity is Severity.ERROR:
            self._has_errors = True

    def extend(self, other: "DiagnosticEngine") -> None:
        for d in other._diagnostics:
            self.report(d)

    @property
    def has_errors(self) -> bool:
        return self._has_errors

    @property
    def diagnostics(self) -> list[Diagnostic]:
        return list(self._diagnostics)

    def errors(self) -> list[Diagnostic]:
        return [d for d in self._diagnostics if d.severity is Severity.ERROR]

    def render_all(self, source_files: dict[str, "SourceFile"]) -> str:
        return "\n\n".join(d.render(source_files) for d in self._diagnostics)
