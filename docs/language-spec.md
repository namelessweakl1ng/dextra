# Dextra Language Specification

**Version:** 0.2.0
**Status:** Current specification — describes the language as implemented in this release.

## 1. Overview

Dextra is a small, statically typed, compiled programming language. Its design is influenced by Rust, Swift, Kotlin, and modern C-like languages, but it has its own identity.

Design goals:

1. Statically typed with local type inference.
2. Compiled to native code through LLVM.
3. Modern, readable syntax.
4. Small and understandable — every feature must justify its complexity.
5. Strong, layered compiler architecture (Lexer → Parser → AST → Semantic → Types → IR → LLVM).
6. Useful, source-location-aware diagnostics.

Non-goals for v0.2.0:

* Ownership / borrowing / lifetimes (Rust-style).
* Generics.
* Closures (planned for a later milestone).
* Enum payload variants (planned); unit enums and pattern matching are supported.
* A package registry.

## 2. Lexical Structure

### 2.1 Source Files

A Dextra source file is a UTF-8 text file with the extension `.dx`. Source files are organized into lines; the lexer tracks `line` and `column` for every token.

### 2.2 Whitespace

Whitespace (space, tab, carriage return) separates tokens but is otherwise insignificant. Newlines are insignificant — Dextra is not whitespace-sensitive. Statements are not terminated by semicolons; the parser uses grammar-driven parsing and accepts optional trailing separators.

### 2.3 Comments

```dextra
// single-line comment, runs to end of line
```

Block comments are not supported in v0.2.0.

### 2.4 Identifiers

```text
identifier ::= [A-Za-z_][A-Za-z0-9_]*
```

Identifiers starting with `_` are reserved for compiler internals and pattern wildcards.

### 2.5 Keywords

```text
let  mut  fn  return  if  else  while  for  in  break  continue
struct  enum  match  impl  true  false  import  export
```

Keywords cannot be used as identifiers.

### 2.6 Integer Literals

```text
integer ::= [0-9]+
```

Integer literals are 64-bit signed (`i64` in LLVM) and represented internally as `Int`. Hexadecimal (`0x...`), binary (`0b...`), and octal (`0o...`) literals are not supported in v0.2.0.

### 2.7 Float Literals

```text
float ::= [0-9]+ "." [0-9]+
```

Float literals are 64-bit IEEE 754 (`double` in LLVM) and represented as `Float`. A trailing dot without fractional digits (`10.`) is not a valid float — write `10.0`.

### 2.8 String Literals

```text
string ::= '"' (escape | ordinary_char)* '"'
```

Escape sequences:

| Escape | Meaning |
|--------|---------|
| `\n`   | newline |
| `\t`   | tab     |
| `\\`   | backslash |
| `\"`   | double quote |
| `\0`   | null byte |

`String` is an opaque, length-prefixed runtime type — see §6.

### 2.9 Boolean Literals

`true` and `false` are the only `Bool` literals.

### 2.10 Operators and Punctuation

```text
+  -  *  /  %
==  !=  <  >  <=  >=
&&  ||  !
=
(  )  {  }  [  ]
:  ;  ,  .  ->  ..
```

## 3. Types

### 3.1 Primitive Types

| Type   | LLVM Type | Notes |
|--------|-----------|-------|
| `Int`  | `i64`     | 64-bit signed integer |
| `Float`| `double`  | 64-bit IEEE 754 |
| `Bool` | `i1`      | 1-bit, `true`/`false` |
| `String` | `ptr` to runtime `DxString` | opaque runtime type |
| `Void` | `void`    | only valid as a return type |

### 3.2 Composite Types

| Type            | Example          |
|-----------------|------------------|
| `[T]`           | `[Int]` — array of `Int` |
| `struct Name`   | user-defined     |

### 3.3 Function Types

Function types are not first-class in v0.2.0. Functions are named and called by name; pointers to functions are not yet supported.

## 4. Variables

### 4.1 Immutable bindings

```dextra
let name: Type = expr
let name = expr            // inferred
```

Immutable bindings cannot be reassigned and their fields/elements cannot be mutated through them.

### 4.2 Mutable bindings

```dextra
let mut name: Type = expr
let mut name = expr
```

Mutable bindings may be reassigned. Mutable references to mutable bindings may have their fields/elements mutated.

### 4.3 Scoping

Bindings are scoped to the block in which they are declared. Inner blocks may shadow outer bindings — the inner binding shadows the outer for the duration of the inner block. After the block ends, the outer binding is visible again.

## 5. Functions

```dextra
fn name(param: Type, ...) -> ReturnType {
    body
}
```

* `main` is the program entry point and must return `Int`.
* `main` may take no parameters in v0.2.0.
* Non-`Void` functions must return on all paths; missing returns are reported as `E0218`.
* Functions may be recursive.
* Functions are not first-class values in v0.2.0.

## 6. Strings

`String` is a runtime-managed, length-prefixed UTF-8 string. The runtime provides:

* `dx_string_new(bytes, len)` — allocate from raw bytes.
* `dx_string_concat(a, b)` — concatenate.
* `dx_string_print(s)` / `dx_string_println(s)` — print.
* `dx_string_length(s)` — return length in bytes.

Concatenation: `"a" + "b"` is syntactic sugar for `dx_string_concat("a", "b")`.

## 7. Arrays

```dextra
let xs: [Int] = [1, 2, 3]
let first = xs[0]
let mut xs = [1, 2, 3]
xs[0] = 10
let empty: [Float] = []      // explicit element type required for []
```

* Arrays in v0.2.0 are fixed-length, heap-allocated, and passed by reference.
* Element type must be uniform.
* Empty array literals require an explicit element-type annotation on the
  binding (`let xs: [T] = []`).  An untyped `let xs = []` is an error.
* Out-of-bounds access is undefined behavior in v0.2.0 (no bounds checks).
  Negative indices are also undefined behavior.  A future release will
  add checked indexing.
* Element storage: the runtime `DxArray` stores each element in an `i64`
  slot.  Ints and Bools fit directly; pointers (String / Array / Struct)
  are stored as `ptrtoint`; Floats (`double`) are stored via lossless
  `bitcast double ↔ i64`.  No element-type information is preserved at
  runtime — type safety is enforced statically by the compiler.

## 8. Structs

```dextra
struct User {
    name: String,
    age: Int
}

let u = User { name: "Alice", age: 21 }
println(u.name)
```

* Structs are nominal types.
* Construction uses `{ field: value, ... }` syntax.
* Field access uses `.`.
* Field order in construction need not match declaration order, but all fields must be supplied.
* Trailing commas in field lists are allowed.
* Methods are not supported in v0.2.0 (planned).

## 9. Control Flow

### 9.1 If / else

```dextra
if cond { ... }
if cond { ... } else { ... }
if cond { ... } else if cond2 { ... } else { ... }
```

`cond` must have type `Bool`. If/else is a statement; it does not produce a value.

### 9.2 While

```dextra
while cond { ... }
```

`break` and `continue` are supported inside `while` bodies.

### 9.3 For-range

```dextra
for i in 0..10 { ... }
for x in array { ... }
```

`0..10` is an inclusive-lower, exclusive-upper range over `Int`. Iterating an array yields its elements by value (a copy for primitives, a reference for arrays/structs).

### 9.4 Break / Continue

Valid only inside `while` and `for`. Use outside a loop is a compile error.

### 9.5 Return

```dextra
return expr
return          // valid only in Void functions
```

### 9.6 Enums and Match

Enums have unit variants without payloads: `enum Color { Red, Green, Blue }`.
`Color.Red` has nominal type `Color` and is represented by its zero-based `i64` tag.

```dextra
let color = Color.Red
let label = match color {
    Color.Red => "red",
    _ => "other",
}
```

Patterns are enum variants or `_`. Matches must cover every variant or include
a wildcard. Arms must have compatible types; a match with `Void` arms can be
used as a statement. See `CHANGELOG.md` for diagnostics `E0219`–`E0223`.

## 10. Operators and Precedence

From lowest to highest:

| Precedence | Operator | Associativity | Notes |
|------------|----------|---------------|-------|
| 1          | `||`     | left          | logical or — **eager**: both operands always evaluated |
| 2          | `&&`     | left          | logical and — **eager**: both operands always evaluated |
| 3          | `== != < > <= >=` | left   | comparison |
| 4          | `+ -`    | left          | additive |
| 5          | `* / %`  | left          | multiplicative |
| 6          | `- !` (unary) | prefix  | unary |
| 7          | `[] () .`  | postfix       | indexing, call, field access |

**Eager evaluation**: `&&` and `||` in v0.2.0 are eager — both operands are
always evaluated, even when the result is determined by the left operand
alone.  `false && side_effect()` will call `side_effect()`.  This is
documented behavior, not a bug.  Short-circuit evaluation is a planned
roadmap feature.

## 11. Type Inference

* `let x = 10` infers `Int`.
* `let x = 1.5` infers `Float`.
* `let x = true` infers `Bool`.
* `let x = "hi"` infers `String`.
* `let xs = [1, 2, 3]` infers `[Int]` (all elements same type).
* `let xs = []` is a type error (ambiguous element type) unless an explicit annotation is given.
* Function parameters and return types must be annotated explicitly.
* Struct fields must be annotated explicitly.

## 12. Mutability

* Mutation requires `let mut`.
* Assigning to an immutable variable → `E0301`.
* Mutating an element of an immutable array → `E0301`.
* Mutating a field of an immutable struct → `E0301`.
* Reassigning a parameter is allowed (parameters are implicitly mutable).

## 13. Modules (planned, not in v0.2.0)

The `import` and `export` keywords are reserved but unimplemented. Each `.dx` file is currently a self-contained program.

## 14. Standard Library (builtin)

The following names are in scope in every Dextra program without an import:

| Name | Type | Notes |
|------|------|-------|
| `print(x)`   | `fn(x: T) -> Void` for `T ∈ {Int, Float, Bool, String}` | prints without trailing newline |
| `println(x)` | `fn(x: T) -> Void` for `T ∈ {Int, Float, Bool, String}` | prints with trailing newline |
| `println()`  | `fn() -> Void` | prints just a newline |
