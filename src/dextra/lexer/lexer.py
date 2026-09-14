"""The Dextra lexer.

Hand-written scanner.  Produces a list of `Token` ending with EOF.
Comments and whitespace are consumed and do not produce tokens.

The lexer never raises raw Python exceptions on bad input.  It reports
`E0001` diagnostics to the provided `DiagnosticEngine` and recovers by
skipping the offending character(s).
"""
from __future__ import annotations

from dextra.diagnostics import Diagnostic, DiagnosticEngine, ErrorCode, Severity
from dextra.lexer.source import SourceFile, SourceSpan
from dextra.lexer.token import KEYWORDS, Token, TokenKind


class Lexer:
    def __init__(self, source: SourceFile, diagnostics: DiagnosticEngine) -> None:
        self.source = source
        self.text = source.text
        self.diagnostics = diagnostics
        self._pos = 0

    # -- public ---------------------------------------------------------

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while True:
            kind, span, value = self._next_token()
            tokens.append(Token(kind, span, value))
            if kind is TokenKind.EOF:
                break
        return tokens

    # -- internal -------------------------------------------------------

    def _make_span(self, start: int, end: int) -> SourceSpan:
        return SourceSpan(
            start=self.source.position_at(start),
            end=self.source.position_at(end),
        )

    def _peek(self, ahead: int = 0) -> str:
        idx = self._pos + ahead
        if idx >= len(self.text):
            return ""
        return self.text[idx]

    def _advance(self, n: int = 1) -> None:
        self._pos += n

    def _next_token(self) -> tuple[TokenKind, SourceSpan, int | float | str | None]:
        # Skip whitespace and comments.
        while True:
            ch = self._peek()
            if ch == "":
                # EOF
                pos = self.source.position_at(self._pos)
                span = SourceSpan(start=pos, end=pos)
                return TokenKind.EOF, span, None
            if ch in " \t\r\n":
                self._advance()
                continue
            if ch == "/" and self._peek(1) == "/":
                # line comment
                while self._peek() not in ("", "\n"):
                    self._advance()
                continue
            break

        start = self._pos
        ch = self._peek()

        # Identifiers / keywords
        if ch.isalpha() or ch == "_":
            return self._lex_identifier(start)

        # Numbers
        if ch.isdigit():
            return self._lex_number(start)

        # Strings
        if ch == '"':
            return self._lex_string(start)

        # Operators / punctuation
        return self._lex_operator(start)

    def _lex_identifier(self, start: int) -> tuple[TokenKind, SourceSpan, str]:
        while self._peek().isalnum() or self._peek() == "_":
            self._advance()
        end = self._pos
        text = self.text[start:end]
        span = self._make_span(start, end)
        kind = KEYWORDS.get(text, TokenKind.IDENTIFIER)
        return kind, span, text

    def _lex_number(self, start: int) -> tuple[TokenKind, SourceSpan, int | float]:
        while self._peek().isdigit():
            self._advance()
        if self._peek() == "." and self._peek(1).isdigit():
            self._advance()  # consume '.'
            while self._peek().isdigit():
                self._advance()
            end = self._pos
            text = self.text[start:end]
            span = self._make_span(start, end)
            return TokenKind.FLOAT, span, float(text)
        end = self._pos
        text = self.text[start:end]
        span = self._make_span(start, end)
        return TokenKind.INTEGER, span, int(text)

    def _lex_string(self, start: int) -> tuple[TokenKind, SourceSpan, str]:
        self._advance()  # consume opening "
        out: list[str] = []
        while True:
            ch = self._peek()
            if ch == "":
                self._report_lexical_error(
                    start, self._pos, "unterminated string literal"
                )
                break
            if ch == '"':
                self._advance()  # consume closing "
                break
            if ch == "\\":
                self._advance()
                esc = self._peek()
                if esc == "":
                    self._report_lexical_error(
                        start, self._pos, "unterminated string literal"
                    )
                    break
                mapping = {"n": "\n", "t": "\t", "\\": "\\", '"': '"', "0": "\0"}
                if esc in mapping:
                    out.append(mapping[esc])
                else:
                    self._report_lexical_error(
                        self._pos, self._pos + 1, f"unknown escape sequence '\\{esc}'"
                    )
                self._advance()
                continue
            out.append(ch)
            self._advance()
        end = self._pos
        span = self._make_span(start, end)
        return TokenKind.STRING, span, "".join(out)

    def _report_lexical_error(self, start: int, end: int, msg: str) -> None:
        span = self._make_span(start, max(start + 1, end))
        self.diagnostics.report(
            Diagnostic(
                code=ErrorCode.E0001_LEXICAL,
                severity=Severity.ERROR,
                message=msg,
                span=span,
            )
        )

    def _lex_operator(self, start: int) -> tuple[TokenKind, SourceSpan, None]:
        ch = self._peek()
        nxt = self._peek(1)

        # two-character operators
        two = ch + nxt
        two_map = {
            "==": TokenKind.EQ_EQ,
            "!=": TokenKind.BANG_EQ,
            "<=": TokenKind.LT_EQ,
            ">=": TokenKind.GT_EQ,
            "&&": TokenKind.AND_AND,
            "||": TokenKind.OR_OR,
            "->": TokenKind.ARROW,
            "..": TokenKind.DOT_DOT,
            "=>": TokenKind.FAT_ARROW,
        }
        if two in two_map:
            self._advance(2)
            return two_map[two], self._make_span(start, self._pos), None

        one_map = {
            "+": TokenKind.PLUS,
            "-": TokenKind.MINUS,
            "*": TokenKind.STAR,
            "/": TokenKind.SLASH,
            "%": TokenKind.PERCENT,
            "=": TokenKind.EQ,
            "!": TokenKind.BANG,
            "<": TokenKind.LT,
            ">": TokenKind.GT,
            "(": TokenKind.LPAREN,
            ")": TokenKind.RPAREN,
            "{": TokenKind.LBRACE,
            "}": TokenKind.RBRACE,
            "[": TokenKind.LBRACKET,
            "]": TokenKind.RBRACKET,
            ":": TokenKind.COLON,
            ";": TokenKind.SEMICOLON,
            ",": TokenKind.COMMA,
            ".": TokenKind.DOT,
        }
        if ch in one_map:
            self._advance()
            return one_map[ch], self._make_span(start, self._pos), None

        # Unknown character: report and skip
        self._report_lexical_error(start, start + 1, f"unexpected character {ch!r}")
        self._advance()
        return TokenKind.EOF, self._make_span(start, start + 1), None
