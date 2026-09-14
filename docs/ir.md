# Dextra Intermediate Representation

The Dextra IR sits between the typed AST and LLVM IR. Its purpose:

1. Decouple the frontend from the backend — backend engineers can target Dextra IR without understanding the parser or type checker.
2. Make control flow explicit (basic blocks, terminators).
3. Provide a surface for future optimizations (constant folding, dead-code elimination, unreachable-block elimination).

## 1. IR Module

```python
@dataclass
class Module:
    name: str
    functions: list[Function]
    globals: list[Global]
    externals: list[ExternalFunction]
```

## 2. Function

```python
@dataclass
class Function:
    name: str
    return_type: DextraType
    params: list[Parameter]
    blocks: list[BasicBlock]
    locals: list[Local]   # alloca'd

    @property
    def entry(self) -> BasicBlock: ...
```

* Every function has a unique `entry` block, which is the first block in `blocks`.
* Parameters are addressed by index; locals are addressed by their `Local` object.

## 3. Basic Block

```python
@dataclass
class BasicBlock:
    label: str            # unique within function
    instructions: list[Instruction]
    terminator: Terminator
```

A block has zero or more non-terminator instructions followed by exactly one terminator. Block successors are determined by the terminator.

## 4. Values

```python
@dataclass
class Value:
    type: DextraType

@dataclass
class Constant(Value):
    value: int | float | bool | str

@dataclass
class Local(Value):       # an alloca'd slot
    name: str

@dataclass
class Param(Value):
    index: int

@dataclass
class Temp(Value):        # an SSA temporary
    name: str
```

## 5. Instructions

| Instruction | Operands | Result |
|-------------|----------|--------|
| `BinOp(op, lhs, rhs)` | two values | `Temp` |
| `UnaryOp(op, operand)` | one value | `Temp` |
| `Call(name, args)` | list of values | `Temp` or `void` |
| `Load(ptr)` | `Local` | `Temp` |
| `Store(ptr, value)` | `Local`, `Value` | `void` |
| `Alloca(name, type)` | none | `Local` |
| `GetField(obj, field_idx)` | `Temp`, `int` | `Temp` |
| `SetField(obj, field_idx, value)` | `Temp`, `int`, `Value` | `void` |
| `GetElement(arr, idx)` | `Temp`, `Value` | `Temp` |
| `SetElement(arr, idx, value)` | `Temp`, `Value`, `Value` | `void` |
| `ArrayLit(elements)` | `list[Value]` | `Temp` |
| `StructLit(struct_name, fields)` | `list[Value]` | `Temp` |
| `StringLit(value)` | `str` | `Temp` |
| `Coerce(value, type)` | `Value`, `Type` | `Temp` (used for `Int`→`Float` if added later) |

## 6. Terminators

| Terminator | Semantics |
|------------|-----------|
| `Return(value?)` | return from function |
| `Branch(target)` | unconditional jump to `target` |
| `CondBranch(cond, then_target, else_target)` | jump based on `cond` (`Bool`) |

## 7. Lowering Examples

### 7.1 If/Else

```dextra
if x > 10 {
    println("big")
} else {
    println("small")
}
```

lowers to:

```
entry:
  %c = cmp gt %x, 10
  condbr %c, then, else
then:
  call println("big")
  br merge
else:
  call println("small")
  br merge
merge:
  ...
```

### 7.2 While

```dextra
while i < 10 {
  println(i)
  i = i + 1
}
```

lowers to:

```
entry:
  br cond
cond:
  %c = cmp lt %i, 10
  condbr %c, body, end
body:
  call println(%i)
  %next = add %i, 1
  store %i, %next
  br cond
end:
  ...
```

### 7.3 For-range

```dextra
for i in 0..10 {
  println(i)
}
```

desugars to (conceptually):

```
let __i = 0
let __end = 10
while __i < __end {
  let i = __i
  println(i)
  __i = __i + 1
}
```

## 8. Invariants

* Every block ends with a terminator.
* The entry block has no predecessors.
* A `Temp` is assigned exactly once (SSA).
* A `Local` is the result of exactly one `Alloca`.
* Every `Temp` has a non-`Unknown` type.
* The IR has no unresolved names — all identifiers were resolved in semantic analysis.

## 9. Validation

The IR module exposes `Module.validate()` which checks all invariants above and returns a list of human-readable error strings. This is invoked from the test suite to catch IR construction bugs.
