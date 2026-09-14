"""Dextra IR.

A small, explicit IR with Module/Function/BasicBlock/Instruction.

This is **not** LLVM IR — it is a layer that decouples the frontend from
the LLVM backend and makes control flow explicit.  The IR is then
lowered to LLVM by `dextra.codegen.llvm_backend`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from dextra.types.type_system import Type, TypeContext


# -- values --------------------------------------------------------------

@dataclass
class Value:
    type: Type


@dataclass
class ConstInt(Value):
    value: int


@dataclass
class ConstFloat(Value):
    value: float


@dataclass
class ConstBool(Value):
    value: bool


@dataclass
class ConstString(Value):
    value: str


@dataclass
class Param(Value):
    index: int
    name: str


@dataclass
class Local(Value):
    """An alloca'd stack slot — addressed by name."""

    name: str


@dataclass
class Temp(Value):
    """An SSA temporary — addressed by name."""

    name: str


# -- instructions --------------------------------------------------------


class Instruction:
    """Marker base class for IR instructions."""


@dataclass
class Alloca(Instruction):
    name: str
    type: Type


@dataclass
class Store(Instruction):
    target: Local
    value: Value


@dataclass
class Load(Instruction):
    result: Temp
    source: Local


@dataclass
class BinOp(Instruction):
    result: Temp
    op: str
    left: Value
    right: Value


@dataclass
class UnaryOp(Instruction):
    result: Temp
    op: str
    operand: Value


@dataclass
class Call(Instruction):
    result: Optional[Temp]
    name: str
    args: list[Value]
    return_type: Type


@dataclass
class StringLit(Instruction):
    result: Temp
    value: str


@dataclass
class ArrayLit(Instruction):
    result: Temp
    elements: list[Value]
    element_type: Type


@dataclass
class StructLit(Instruction):
    result: Temp
    struct_name: str
    fields: list[Value]    # in declaration order


@dataclass
class GetField(Instruction):
    result: Temp
    obj: Value
    struct_name: str
    field_index: int


@dataclass
class SetField(Instruction):
    obj: Value
    struct_name: str
    field_index: int
    value: Value


@dataclass
class GetElement(Instruction):
    result: Temp
    array: Value
    index: Value


@dataclass
class SetElement(Instruction):
    array: Value
    index: Value
    value: Value


# -- terminators ---------------------------------------------------------


class Terminator:
    """Marker base class for terminators."""


@dataclass
class Return(Terminator):
    value: Optional[Value]


@dataclass
class Branch(Terminator):
    target: "BasicBlock"


@dataclass
class CondBranch(Terminator):
    cond: Value
    then_target: "BasicBlock"
    else_target: "BasicBlock"


# -- basic blocks & functions & module ----------------------------------


@dataclass
class BasicBlock:
    label: str
    instructions: list[Instruction] = field(default_factory=list)
    terminator: Optional[Terminator] = None

    def successors(self) -> list["BasicBlock"]:
        if isinstance(self.terminator, Branch):
            return [self.terminator.target]
        if isinstance(self.terminator, CondBranch):
            return [self.terminator.then_target, self.terminator.else_target]
        return []


@dataclass
class Function:
    name: str
    return_type: Type
    params: list[Param]
    blocks: list[BasicBlock] = field(default_factory=list)

    @property
    def entry(self) -> BasicBlock:
        return self.blocks[0]


@dataclass
class Module:
    name: str
    functions: list[Function] = field(default_factory=list)
    string_literals: dict[str, Temp] = field(default_factory=dict)


# -- IR builder ----------------------------------------------------------


class IRBuilder:
    """Helper that manages fresh-name generation and block creation."""

    def __init__(self, ctx: TypeContext) -> None:
        self.ctx = ctx
        self._counter = 0

    def fresh(self, prefix: str, ty: Type) -> Temp:
        self._counter += 1
        return Temp(name=f"{prefix}{self._counter}", type=ty)

    def fresh_local(self, prefix: str, ty: Type) -> Local:
        self._counter += 1
        return Local(name=f"{prefix}{self._counter}", type=ty)

    def new_block(self, label: str) -> BasicBlock:
        return BasicBlock(label=label)
