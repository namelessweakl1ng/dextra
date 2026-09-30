# Dextra Type System

## 1. Type Representation

Internally, every type is an instance of a `Type` subclass. Types are interned by the `TypeContext` so structural equality is identity equality.

```
Type
├── PrimitiveType
│   ├── Int
│   ├── Float
│   ├── Bool
│   ├── String
│   └── Void
├── ArrayType          { element_type: Type }
├── StructType         { name, fields: list[(name, Type)] }
├── EnumType           { enum_name, variant_names: list[str] }
└── FunctionType       { params: list[Type], return_type: Type }
```

`UnknownType` is used internally as a sentinel during inference; it must never survive to the typed AST.

## 1.1 LLVM representation

This is the canonical mapping from Dextra types to LLVM IR types. The mapping is
implemented in `src/dextra/codegen/llvm_backend.py` (`_llvm_type`):

| Dextra type | LLVM value type       | Storage in alloca slot | By-value / by-reference |
|-------------|-----------------------|------------------------|-------------------------|
| `Int`       | `i64`                 | `i64`                  | by value                |
| `Float`    | `double`              | `double`               | by value                |
| `Bool`      | `i1`                  | `i1`                   | by value                |
| `String`    | `%DxString*`          | `%DxString*`           | by reference (heap)     |
| `[T]`       | `%DxArray*`            | `%DxArray*`            | by reference (heap)     |
| Struct      | `%struct.Name*`       | `%struct.Name*`         | by reference (heap)     |
| Enum        | `i64`                 | `i64`                  | by value (variant tag)  |
| `Void`      | `void`                | (n/a)                  | only as function return |

Implications:

* **Structs are heap-allocated.** A `StructLit` calls `dx_alloc_struct` and
  returns a heap pointer; the struct's lifetime is therefore independent of
  the function that created it. Returning a struct from a function returns
  a stable pointer — no dangling stack address.
* **Strings and arrays are heap-allocated** by the runtime.
* **Slot types match value types.** A `let x: User = ...` binding creates an
  `alloca %struct.User*` (a single pointer slot), and storing the struct
  stores the pointer.
* **Arrays store elements as `i64` slots** at the runtime level. For
  pointer-typed elements (String, Array, Struct) the LLVM backend uses
  `ptrtoint` on store and `inttoptr` on load. For `Int` elements no cast
  is needed.


## 2. Type Rules

### 2.1 Literals

| Literal     | Type     |
|-------------|----------|
| `10`        | `Int`    |
| `1.5`       | `Float`  |
| `true`      | `Bool`   |
| `false`     | `Bool`   |
| `"hello"`   | `String` |

### 2.2 Arithmetic

| Op  | LHS    | RHS    | Result | Notes |
|-----|--------|--------|--------|-------|
| `+` | Int    | Int    | Int    | |
| `-` | Int    | Int    | Int    | |
| `*` | Int    | Int    | Int    | |
| `/` | Int    | Int    | Int    | signed division |
| `%` | Int    | Int    | Int    | signed remainder |
| `+` | Float  | Float  | Float  | |
| `-` | Float  | Float  | Float  | |
| `*` | Float  | Float  | Float  | |
| `/` | Float  | Float  | Float  | |
| `+` | String | String | String | string concat (lowered to runtime call) |

Mixing `Int` and `Float` operands is a **type error** (no implicit promotion in v0.2.0).

### 2.3 Comparison

| Op     | Operand types               | Result |
|--------|-----------------------------|--------|
| `== !=`| same type, scalar only      | `Bool` |
| `< > <= >=` | `Int` or `Float` (same) | `Bool` |

Comparing `String`s with `<` is a type error in v0.2.0. `==` on strings is allowed and lowered to a runtime `dx_string_eq` byte comparison.

### 2.4 Boolean

| Op  | Operands | Result |
|-----|----------|--------|
| `&&`| `Bool`   | `Bool` |
| `||`| `Bool`   | `Bool` |
| `!` | `Bool`   | `Bool` |

### 2.5 Unary

| Op  | Operand | Result |
|-----|---------|--------|
| `-` | `Int`   | `Int`  |
| `-` | `Float` | `Float`|
| `!` | `Bool`  | `Bool` |

### 2.6 Index

`arr[i]`:
* `arr` must be `ArrayType`.
* `i` must be `Int`.
* Result is `arr.element_type`.

### 2.7 Field Access

`expr.field`:
* `expr` must be `StructType`.
* `field` must exist on the struct.
* Result is the field's declared type.

### 2.8 Call

`f(a, b)`:
* `f` must resolve to a function declaration.
* Argument count must match parameter count.
* Argument types must match parameter types (no implicit conversion).
* Result is the function's return type.

### 2.9 Array Literal

`[1, 2, 3]`:
* All element expressions must have the same type `T`.
* Result is `ArrayType(T)`.

`[]` is an error (ambiguous element type) unless an annotation is given.

### 2.10 Struct Literal

`Foo { x: 1, y: "a" }`:
* `Foo` must resolve to a struct declaration.
* Every field must be initialized exactly once.
* No extra fields.
* Each initializer type must match the declared field type.

## 3. Inference Rules

| Construct                      | Inferred Type |
|--------------------------------|---------------|
| `let x = 10`                   | `Int`         |
| `let x = 1.5`                  | `Float`       |
| `let x = true`                 | `Bool`        |
| `let x = "hi"`                 | `String`      |
| `let x = [1, 2]`               | `[Int]`       |
| `let x = f(...)`               | function return type |
| `let x = a + b`                | per §2.2 |
| `let x = arr[0]`               | `arr.element_type` |
| `let x = obj.field`            | field's declared type |

If an explicit annotation is present (`let x: T = e`), the inferred type of `e` must be `T` exactly (no subtyping).

## 4. Mutability Rules

* `let x = ...` — `x` is immutable. `x = ...` is `E0301`.
* `let mut x = ...` — `x` is mutable. `x = ...` is allowed if types match.
* `let xs = [...]` — `xs` is immutable; `xs[0] = ...` is `E0301`.
* `let mut xs = [...]` — `xs` is mutable; `xs[0] = ...` is allowed.
* Struct fields inherit mutability from the binding. `u.name = ...` requires `u` to be `let mut`.

## 5. Type Compatibility

Dextra has **no implicit conversions** in v0.2.0. The following are all errors:

```dextra
let x: Int = 1.5           // E0200
let x: Float = 1           // E0200
let x: Int = true          // E0200
let b: Bool = 1 == 2       // OK (both sides Bool)
let s: String = "a" + 1    // E0200 (Int ≠ String)
```

## 6. Void Rules

* `Void` is valid only as a function return type.
* A function with no declared return type has return type `Void`.
* `return` (no expression) is valid only in a `Void` function.
* `return expr` is valid only if `expr`'s type matches the function's return type.
* Calling a `Void` function in an expression context is a type error.

## 7. Function Body Checks

* All paths through a non-`Void` function must end in `return` (or otherwise
  exit the block via `break`/`continue`).  Falling off the end of a
  non-Void function is a **compile-time error** (`E0218`).

  The analysis is control-flow-aware:
  - An `if` without `else` is treated as not-always-exiting (the implicit
    fall-through path does not return).
  - An `if`/`else` exits iff both branches exit.
  - An `else if` chain exits iff the final `else` branch exits and every
    prior branch exits.
  - `while true { ... }` exits iff its body always exits.
  - `while <non-literal-true> { ... }` and `for ... { ... }` are treated
    as not-always-exiting (the loop may execute zero times).
  - `return`, `break`, `continue` all exit their enclosing block.

  This rule was promoted from "documented limitation" to "hard error" in
  v0.1.3 (Phase 1.3).

## 8. Numeric Edge Cases

* **Integer division by zero** (`x / 0` or `x % 0`) is **undefined behavior**
  in LLVM — it produces a poison value. Dextra emits a compile-time
  **warning** (`W0001`) when the divisor is a literal `0` or `0.0`. When
  the divisor is a variable, no warning is emitted (the compiler can't
  prove the runtime value is zero). The behavior at runtime is undefined.
* **Floating-point division by 0.0** produces `±Inf` or `NaN` per IEEE 754.
  This is also warned about at compile time when the divisor is a literal.
* **Out-of-bounds array access** is **undefined behavior** in v0.2.0.
  Bounds checking is planned for a future release. See the roadmap in `README.md`.

## 9. String Equality

String `==` and `!=` are supported and lowered to a runtime `dx_string_eq`
call that performs a byte-level comparison. Strings of different lengths
are always unequal; strings of the same length are compared with `memcmp`.

