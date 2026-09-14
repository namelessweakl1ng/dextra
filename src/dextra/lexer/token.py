"""Token kinds and the `Token` dataclass."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from dextra.lexer.source import SourceSpan


class TokenKind(Enum):
    # Literals
    INTEGER = auto()
    FLOAT = auto()
    STRING = auto()

    # Identifiers / keywords
    IDENTIFIER = auto()
    # Keywords
    KW_LET = auto()
    KW_MUT = auto()
    KW_FN = auto()
    KW_RETURN = auto()
    KW_IF = auto()
    KW_ELSE = auto()
    KW_WHILE = auto()
    KW_FOR = auto()
    KW_IN = auto()
    KW_BREAK = auto()
    KW_CONTINUE = auto()
    KW_STRUCT = auto()
    KW_ENUM = auto()
    KW_MATCH = auto()
    KW_IMPL = auto()
    KW_TRUE = auto()
    KW_FALSE = auto()
    KW_IMPORT = auto()
    KW_EXPORT = auto()

    # Type-name keywords (also reserved)
    KW_INT = auto()
    KW_FLOAT = auto()
    KW_BOOL = auto()
    KW_STRING = auto()
    KW_VOID = auto()

    # Operators
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()

    EQ = auto()
    EQ_EQ = auto()
    BANG = auto()
    BANG_EQ = auto()
    LT = auto()
    GT = auto()
    LT_EQ = auto()
    GT_EQ = auto()
    AND_AND = auto()
    OR_OR = auto()

    # Punctuation
    LPAREN = auto()
    RPAREN = auto()
    LBRACE = auto()
    RBRACE = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    COLON = auto()
    SEMICOLON = auto()
    COMMA = auto()
    DOT = auto()
    DOT_DOT = auto()
    ARROW = auto()
    FAT_ARROW = auto()

    # End
    EOF = auto()


# Map keyword text -> TokenKind
KEYWORDS: dict[str, TokenKind] = {
    "let": TokenKind.KW_LET,
    "mut": TokenKind.KW_MUT,
    "fn": TokenKind.KW_FN,
    "return": TokenKind.KW_RETURN,
    "if": TokenKind.KW_IF,
    "else": TokenKind.KW_ELSE,
    "while": TokenKind.KW_WHILE,
    "for": TokenKind.KW_FOR,
    "in": TokenKind.KW_IN,
    "break": TokenKind.KW_BREAK,
    "continue": TokenKind.KW_CONTINUE,
    "struct": TokenKind.KW_STRUCT,
    "enum": TokenKind.KW_ENUM,
    "match": TokenKind.KW_MATCH,
    "impl": TokenKind.KW_IMPL,
    "true": TokenKind.KW_TRUE,
    "false": TokenKind.KW_FALSE,
    "import": TokenKind.KW_IMPORT,
    "export": TokenKind.KW_EXPORT,
    "Int": TokenKind.KW_INT,
    "Float": TokenKind.KW_FLOAT,
    "Bool": TokenKind.KW_BOOL,
    "String": TokenKind.KW_STRING,
    "Void": TokenKind.KW_VOID,
}


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    span: SourceSpan
    # `value` carries the parsed payload for INTEGER, FLOAT, STRING, IDENTIFIER tokens.
    value: int | float | str | None = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        if self.value is None:
            return f"Token({self.kind.name}@{self.span})"
        return f"Token({self.kind.name}={self.value!r}@{self.span})"

    @property
    def text(self) -> str:
        """Convenience: best-effort textual rendering for diagnostics."""
        if self.value is not None:
            return str(self.value)
        return self.kind.name
