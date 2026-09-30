# Dextra Formal Grammar

**Notation:** EBNF-style. `{ X }` means zero or more, `[ X ]` means optional, `|` means alternation. Terminals are quoted; non-terminals are unquoted.

## 1. Top-level

```ebnf
program        ::= declaration* EOF ;

declaration    ::= function_decl
                 | struct_decl
                 | enum_decl
                 ;

function_decl  ::= "fn" IDENTIFIER "(" [ parameter_list ] ")" [ "->" type ] block ;

parameter_list ::= parameter ( "," parameter )* [ "," ] ;

parameter      ::= IDENTIFIER ":" type ;

struct_decl    ::= "struct" IDENTIFIER "{" [ field_list ] "}" ;

field_list     ::= field ( "," field )* [ "," ] ;

field          ::= IDENTIFIER ":" type ;

enum_decl      ::= "enum" IDENTIFIER "{" [ IDENTIFIER ( "," IDENTIFIER )* [ "," ] ] "}" ;
```

## 2. Statements

```ebnf
block          ::= "{" statement* "}" ;

statement      ::= let_stmt
                 | return_stmt
                 | break_stmt
                 | continue_stmt
                 | if_stmt
                 | while_stmt
                 | for_stmt
                 | assignment
                 | call_expr
                 | match_expr
                 | block
                 ;

let_stmt       ::= "let" [ "mut" ] IDENTIFIER [ ":" type ] "=" expression ;

return_stmt    ::= "return" [ expression ] ;

break_stmt     ::= "break" ;
continue_stmt  ::= "continue" ;

if_stmt        ::= "if" expression block
                   ( "else" ( if_stmt | block ) )? ;

while_stmt     ::= "while" expression block ;

for_stmt       ::= "for" IDENTIFIER "in" for_iterable block ;

for_iterable   ::= range_expr
                 | expression ;

range_expr     ::= expression ".." expression ;

assignment     ::= lvalue "=" expression ;

lvalue         ::= IDENTIFIER
                 | lvalue "[" expression "]"
                 | lvalue "." IDENTIFIER
                 | IDENTIFIER "{" ... "}"   // not an lvalue; struct literal
                 ;
```

**Note on statement parsing:** Dextra statements are not semicolon-terminated. The parser disambiguates `assignment` from `expression_statement` by looking at whether the parsed expression is a valid lvalue followed by `=`. A bare expression as a statement is allowed only if it is a function call or match expression (other bare expressions, like `1 + 2`, are rejected as unused).

## 3. Expressions

```ebnf
expression     ::= logical_or ;

logical_or     ::= logical_and ( "||" logical_and )* ;

logical_and    ::= equality ( "&&" equality )* ;

equality       ::= comparison ( ( "==" | "!=" ) comparison )* ;

comparison     ::= additive ( ( "<" | ">" | "<=" | ">=" ) additive )* ;

additive       ::= multiplicative ( ( "+" | "-" ) multiplicative )* ;

multiplicative ::= unary ( ( "*" | "/" | "%" ) unary )* ;

unary          ::= ( "-" | "!" ) unary
                 | postfix ;

postfix        ::= primary
                   ( "(" argument_list? ")"
                   | "[" expression "]"
                   | "." IDENTIFIER )* ;

primary        ::= INTEGER
                 | FLOAT
                 | STRING
                 | "true"
                 | "false"
                 | IDENTIFIER
                 | "(" expression ")"
                 | array_literal
                 | struct_literal
                 | match_expr
                 ;

argument_list  ::= expression ( "," expression )* [ "," ] ;

array_literal  ::= "[" [ expression ( "," expression )* [ "," ] ] "]" ;

struct_literal ::= IDENTIFIER "{" [ struct_field_init ( "," struct_field_init )* [ "," ] ] "}" ;

struct_field_init ::= IDENTIFIER ":" expression ;

match_expr     ::= "match" expression "{" [ match_arm ( "," match_arm )* [ "," ] ] "}" ;
match_arm      ::= pattern "=>" ( block | expression ) ;
pattern        ::= "_" | IDENTIFIER "." IDENTIFIER ;
```

## 4. Types

```ebnf
type           ::= "Int"
                 | "Float"
                 | "Bool"
                 | "String"
                 | "Void"
                 | array_type
                 | IDENTIFIER                // struct or enum name
                 ;

array_type     ::= "[" type "]" ;
```

## 5. Lexical Grammar (informal)

```ebnf
IDENTIFIER     ::= [A-Za-z_][A-Za-z0-9_]*
INTEGER        ::= [0-9]+
FLOAT          ::= [0-9]+ "." [0-9]+
STRING         ::= '"' ( '\\' [nt"\\0] | ~["\\] )* '"'
COMMENT        ::= "//" ~[\n]*
WHITESPACE     ::= [ \t\r\n]+
```

## 6. Precedence Table

From lowest to highest binding:

| Level | Operators                          | Associativity |
|-------|------------------------------------|---------------|
| 1     | `||`                               | left          |
| 2     | `&&`                               | left          |
| 3     | `== !=`                            | left          |
| 4     | `< > <= >=`                        | left          |
| 5     | `+ -`                              | left          |
| 6     | `* / %`                            | left          |
| 7     | unary `-` `!`                      | prefix        |
| 8     | call `()`, index `[]`, field `.`  | postfix       |

## 7. Reserved Keywords

```text
let mut fn return if else while for in break continue
struct enum match impl true false import export
```
