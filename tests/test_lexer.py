"""Lexer unit tests."""
from dextra.diagnostics import DiagnosticEngine
from dextra.lexer import KEYWORDS, Lexer, SourceFile, TokenKind


def lex(src: str):
    diag = DiagnosticEngine()
    sf = SourceFile("test.dx", src)
    toks = Lexer(sf, diag).tokenize()
    return toks, diag, sf


def test_keywords():
    toks, diag, _ = lex("let mut fn return if else while for in break continue struct enum match impl true false import export")
    # All keywords should be their respective KW_ kinds.
    assert not diag.has_errors
    kinds = [t.kind for t in toks if t.kind is not TokenKind.EOF]
    assert TokenKind.KW_LET in kinds
    assert TokenKind.KW_MUT in kinds
    assert TokenKind.KW_FN in kinds
    assert TokenKind.KW_RETURN in kinds
    assert TokenKind.KW_IF in kinds
    assert TokenKind.KW_ELSE in kinds
    assert TokenKind.KW_WHILE in kinds
    assert TokenKind.KW_FOR in kinds
    assert TokenKind.KW_IN in kinds
    assert TokenKind.KW_BREAK in kinds
    assert TokenKind.KW_CONTINUE in kinds
    assert TokenKind.KW_STRUCT in kinds
    assert TokenKind.KW_ENUM in kinds
    assert TokenKind.KW_MATCH in kinds
    assert TokenKind.KW_IMPL in kinds
    assert TokenKind.KW_TRUE in kinds
    assert TokenKind.KW_FALSE in kinds
    assert TokenKind.KW_IMPORT in kinds
    assert TokenKind.KW_EXPORT in kinds


def test_primitive_type_keywords():
    toks, _, _ = lex("Int Float Bool String Void")
    kinds = [t.kind for t in toks if t.kind is not TokenKind.EOF]
    assert kinds == [TokenKind.KW_INT, TokenKind.KW_FLOAT, TokenKind.KW_BOOL,
                     TokenKind.KW_STRING, TokenKind.KW_VOID]


def test_integer_literals():
    toks, _, _ = lex("0 42 1234567890")
    ints = [t.value for t in toks if t.kind is TokenKind.INTEGER]
    assert ints == [0, 42, 1234567890]


def test_float_literals():
    toks, _, _ = lex("1.5 3.14 0.001")
    floats = [t.value for t in toks if t.kind is TokenKind.FLOAT]
    assert floats == [1.5, 3.14, 0.001]


def test_string_literals():
    toks, _, _ = lex('"hello" "world" "a\\nb" "tab\\there" "\\"" "\\\\"')
    strs = [t.value for t in toks if t.kind is TokenKind.STRING]
    assert strs == ["hello", "world", "a\nb", "tab\there", '"', "\\"]


def test_identifiers_and_keywords():
    toks, _, _ = lex("foo bar baz let x")
    kinds = [t.kind for t in toks if t.kind is not TokenKind.EOF]
    assert kinds == [TokenKind.IDENTIFIER, TokenKind.IDENTIFIER, TokenKind.IDENTIFIER,
                     TokenKind.KW_LET, TokenKind.IDENTIFIER]


def test_operators():
    toks, _, _ = lex("+ - * / % == != < > <= >= && || ! = -> .. .")
    ops = [t.kind for t in toks if t.kind is not TokenKind.EOF]
    expected = [TokenKind.PLUS, TokenKind.MINUS, TokenKind.STAR, TokenKind.SLASH,
                TokenKind.PERCENT, TokenKind.EQ_EQ, TokenKind.BANG_EQ,
                TokenKind.LT, TokenKind.GT, TokenKind.LT_EQ, TokenKind.GT_EQ,
                TokenKind.AND_AND, TokenKind.OR_OR, TokenKind.BANG,
                TokenKind.EQ, TokenKind.ARROW, TokenKind.DOT_DOT, TokenKind.DOT]
    assert ops == expected


def test_punctuation():
    toks, _, _ = lex("( ) { } [ ] : ; ,")
    kinds = [t.kind for t in toks if t.kind is not TokenKind.EOF]
    assert kinds == [TokenKind.LPAREN, TokenKind.RPAREN, TokenKind.LBRACE, TokenKind.RBRACE,
                     TokenKind.LBRACKET, TokenKind.RBRACKET, TokenKind.COLON,
                     TokenKind.SEMICOLON, TokenKind.COMMA]


def test_source_positions():
    toks, _, _ = lex("let x = 10")
    let_tok = toks[0]
    assert let_tok.kind is TokenKind.KW_LET
    assert let_tok.span.start.line == 1
    assert let_tok.span.start.column == 1
    assert let_tok.span.start.offset == 0
    x_tok = toks[1]
    assert x_tok.span.start.column == 5
    eq_tok = toks[2]
    assert eq_tok.span.start.column == 7
    int_tok = toks[3]
    assert int_tok.span.start.column == 9


def test_multiline_positions():
    toks, _, _ = lex("let x = 1\nlet y = 2")
    # Second `let` keyword sits at index 4 (after let, x, =, 1, then let).
    let2 = toks[4]
    assert let2.kind is TokenKind.KW_LET
    assert let2.span.start.line == 2
    assert let2.span.start.column == 1
    y_tok = toks[5]
    assert y_tok.span.start.line == 2
    assert y_tok.span.start.column == 5


def test_comments_skipped():
    toks, _, _ = lex("// this is a comment\nlet x = 1")
    kinds = [t.kind for t in toks if t.kind is not TokenKind.EOF]
    assert kinds == [TokenKind.KW_LET, TokenKind.IDENTIFIER, TokenKind.EQ, TokenKind.INTEGER]


def test_unterminated_string_error():
    toks, diag, _ = lex('"oops')
    assert diag.has_errors
    assert any(d.code.value == "E0001" for d in diag.diagnostics)


def test_unknown_character_error():
    toks, diag, _ = lex("let x = @")
    assert diag.has_errors
    assert any(d.code.value == "E0001" for d in diag.diagnostics)


def test_eof_always_present():
    toks, _, _ = lex("let x = 1")
    assert toks[-1].kind is TokenKind.EOF
