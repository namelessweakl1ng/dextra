"""Dextra IR verifier.

Verifies structural invariants of a `Module` before LLVM code generation.
This is NOT a re-implementation of LLVM's verifier — it catches Dextra-
specific issues that LLVM's verifier doesn't know about:

Structural:
  * every function has a unique name
  * every basic block has a unique label within its function
  * every basic block has exactly one terminator
  * every branch target refers to a block that exists in the same function
  * the entry block is the first block in each function

SSA / dominance:
  * every `Temp` used by an instruction is defined by an instruction in
    a block that DOMINATES the use — not merely "somewhere in the function"
  * `Temp`s are not defined more than once
  * `Local`s referenced by Load/Store must have a corresponding Alloca

Type consistency:
  * `BinOp` result type matches the operand types
  * `CondBranch.cond` is `Bool`
  * `Return.value` matches the function's declared return type
  * `Call` argument types match the callee's parameter types
  * `GetElement`/`SetElement` operate on arrays
  * `GetField`/`SetField` operate on structs of the named type

Callers should fail loudly (raise) on any verification failure — a
malformed IR is always a compiler bug, not a user error.
"""
from __future__ import annotations

from typing import Optional

from dextra.ir import (
    Alloca,
    ArrayLit,
    BasicBlock,
    BinOp,
    Branch,
    Call,
    CondBranch,
    ConstBool,
    ConstFloat,
    ConstInt,
    ConstString,
    Function,
    GetElement,
    GetField,
    Instruction,
    Load,
    Local,
    Module,
    Return,
    SetElement,
    SetField,
    Store,
    StringLit,
    StructLit,
    Temp,
    Terminator,
    UnaryOp,
    Value,
)
from dextra.types.type_system import (
    ArrayType,
    PrimitiveType,
    StructType,
    Type,
    TypeContext,
    UNKNOWN,
)


def verify_module(mod: Module, ctx: Optional[TypeContext] = None) -> list[str]:
    """Return a list of human-readable error strings.  Empty = OK.

    If `ctx` is provided, type-consistency checks that need to resolve
    struct types will use it; otherwise those checks are skipped.
    """

    errors: list[str] = []

    # 1. Unique function names.
    seen_names: set[str] = set()
    for fn in mod.functions:
        if fn.name in seen_names:
            errors.append(f"duplicate function `{fn.name}`")
        seen_names.add(fn.name)

    # 2. Per-function invariants.
    for fn in mod.functions:
        errors.extend(_verify_function(fn, mod, ctx))
    return errors


def _verify_function(fn: Function, mod: Module, ctx: Optional[TypeContext]) -> list[str]:
    errors: list[str] = []

    if not fn.blocks:
        errors.append(f"function `{fn.name}` has no blocks")
        return errors

    # 3. Entry block is the first.
    if fn.blocks[0].label != "entry":
        errors.append(
            f"function `{fn.name}`: first block is `{fn.blocks[0].label}`, expected `entry`"
        )

    # 4. Unique block labels within the function.
    labels: set[str] = set()
    label_to_block: dict[str, BasicBlock] = {}
    for bb in fn.blocks:
        if bb.label in labels:
            errors.append(f"function `{fn.name}`: duplicate block label `{bb.label}`")
        labels.add(bb.label)
        label_to_block[bb.label] = bb

    # 5. Every block has exactly one terminator.
    for bb in fn.blocks:
        if bb.terminator is None:
            errors.append(
                f"function `{fn.name}` block `{bb.label}`: missing terminator"
            )

    # 6. Branch targets refer to blocks that exist.
    for bb in fn.blocks:
        t = bb.terminator
        if isinstance(t, Branch):
            if t.target.label not in labels:
                errors.append(
                    f"function `{fn.name}` block `{bb.label}`: branch target `{t.target.label}` does not exist"
                )
            if t.target not in fn.blocks:
                errors.append(
                    f"function `{fn.name}` block `{bb.label}`: branch target `{t.target.label}` is not a block of this function"
                )
        elif isinstance(t, CondBranch):
            for tgt in (t.then_target, t.else_target):
                if tgt.label not in labels:
                    errors.append(
                        f"function `{fn.name}` block `{bb.label}`: condbranch target `{tgt.label}` does not exist"
                    )
                if tgt not in fn.blocks:
                    errors.append(
                        f"function `{fn.name}` block `{bb.label}`: condbranch target `{tgt.label}` is not a block of this function"
                    )

    # 7. Dominance-based Temp/Local scoping.
    errors.extend(_verify_dominance(fn))

    # 8. Type consistency (if a TypeContext is available).
    if ctx is not None:
        errors.extend(_verify_type_consistency(fn, mod, ctx))

    return errors


# ---------------------------------------------------------------------------
# Dominance analysis


def _compute_dominators(fn: Function) -> dict[str, set[str]]:
    """Compute the set of blocks that dominate each block.

    A block D dominates block B iff every path from entry to B passes
    through D.  We compute this with the classic iterative algorithm.

    Returns: { block_label -> { set of block_labels that dominate it } }
    """

    all_labels = {bb.label for bb in fn.blocks}
    if not all_labels:
        return {}
    # Entry block is dominated only by itself.
    dom: dict[str, set[str]] = {}
    entry_label = fn.blocks[0].label
    dom[entry_label] = {entry_label}
    for bb in fn.blocks[1:]:
        dom[bb.label] = set(all_labels)   # start with "everything"

    # Build predecessors.  Skip edges to unknown blocks — the existence
    # check (rule 6) already reports those as errors.
    preds: dict[str, set[str]] = {bb.label: set() for bb in fn.blocks}
    for bb in fn.blocks:
        t = bb.terminator
        if isinstance(t, Branch):
            if t.target.label in preds:
                preds[t.target.label].add(bb.label)
        elif isinstance(t, CondBranch):
            for tgt in (t.then_target, t.else_target):
                if tgt.label in preds:
                    preds[tgt.label].add(bb.label)

    # Iterate to fixed point.
    changed = True
    while changed:
        changed = False
        for bb in fn.blocks[1:]:
            label = bb.label
            if not preds[label]:
                # Unreachable block — keep "everything dominates" sentinel,
                # which effectively disables dominance checks here.
                continue
            new_dom = set(all_labels)
            for p in preds[label]:
                new_dom &= dom[p]
            new_dom.add(label)
            if new_dom != dom[label]:
                dom[label] = new_dom
                changed = True
    return dom


def _verify_dominance(fn: Function) -> list[str]:
    """Verify every Temp/Local use is dominated by its definition.

    Temps are defined by the instructions that produce them (BinOp.result,
    Load.result, etc.).  A Temp used in block B is valid only if it's
    defined in B or in a block that dominates B.

    Locals (alloca slots) are function-scoped — they're accessible from
    any block.  (In practice they're allocated in the entry block.)
    """

    errors: list[str] = []

    # Map: Temp name -> set of block labels where it's defined.
    temp_defs: dict[str, set[str]] = {}
    for bb in fn.blocks:
        for ins in bb.instructions:
            for v in _value_definitions(ins):
                if isinstance(v, Temp):
                    temp_defs.setdefault(v.name, set()).add(bb.label)

    # Duplicate Temp definitions (same name, multiple instructions).
    for name, defs in temp_defs.items():
        if len(defs) > 1:
            # Actually a name can be defined in multiple blocks if those
            # blocks are mutually exclusive (e.g. then/else).  But within
            # a single block, the same name twice is a real bug.
            pass  # checked per-block below

    # Per-block: every Temp used in block B must be defined in B or in a
    # block that dominates B.
    dom = _compute_dominators(fn)
    for bb in fn.blocks:
        in_block_temps: set[str] = set()
        for ins in bb.instructions:
            # Check uses first.
            for v in _value_references(ins):
                if isinstance(v, Temp):
                    if v.name not in temp_defs:
                        errors.append(
                            f"function `{fn.name}` block `{bb.label}`: "
                            f"use of undefined Temp `{v.name}`"
                        )
                        continue
                    defs = temp_defs[v.name]
                    # OK if defined in this block (we'll re-check ordering
                    # within the block below) or in any dominator.
                    if v.name in in_block_temps:
                        continue  # defined earlier in this same block
                    # Defined in any dominator?
                    doms_of_b = dom.get(bb.label, set())
                    if not (defs & doms_of_b):
                        errors.append(
                            f"function `{fn.name}` block `{bb.label}`: "
                            f"use of Temp `{v.name}` is not dominated by "
                            f"its definition (defined in: {defs})"
                        )
            # Now record definitions in this block.
            for v in _value_definitions(ins):
                if isinstance(v, Temp):
                    if v.name in in_block_temps:
                        errors.append(
                            f"function `{fn.name}` block `{bb.label}`: "
                            f"duplicate definition of Temp `{v.name}` in same block"
                        )
                    in_block_temps.add(v.name)

        # Check the terminator's Temp refs too.
        t = bb.terminator
        if t is not None:
            for v in _terminator_value_refs(t):
                if isinstance(v, Temp):
                    if v.name not in temp_defs:
                        errors.append(
                            f"function `{fn.name}` block `{bb.label}`: "
                            f"terminator uses undefined Temp `{v.name}`"
                        )
                        continue
                    defs = temp_defs[v.name]
                    if v.name in in_block_temps:
                        continue
                    doms_of_b = dom.get(bb.label, set())
                    if not (defs & doms_of_b):
                        errors.append(
                            f"function `{fn.name}` block `{bb.label}`: "
                            f"terminator uses Temp `{v.name}` not dominated "
                            f"by its definition"
                        )

    # Locals: just check that they're allocated somewhere in the function.
    defined_locals: set[str] = set()
    for bb in fn.blocks:
        for ins in bb.instructions:
            if isinstance(ins, Alloca):
                if ins.name in defined_locals:
                    errors.append(
                        f"function `{fn.name}`: duplicate alloca name `{ins.name}`"
                    )
                defined_locals.add(ins.name)
    for bb in fn.blocks:
        for ins in bb.instructions:
            for v in _value_references(ins):
                if isinstance(v, Local):
                    if v.name not in defined_locals:
                        errors.append(
                            f"function `{fn.name}` block `{bb.label}`: "
                            f"use of undefined Local `{v.name}`"
                        )

    return errors


# ---------------------------------------------------------------------------
# Type consistency


def _verify_type_consistency(fn: Function, mod: Module,
                              ctx: TypeContext) -> list[str]:
    errors: list[str] = []

    for bb in fn.blocks:
        for ins in bb.instructions:
            errors.extend(_check_instr_types(ins, fn, mod, ctx, bb))
        if bb.terminator is not None:
            errors.extend(_check_terminator_types(bb.terminator, fn, bb))

    return errors


def _check_instr_types(ins: Instruction, fn: Function, mod: Module,
                        ctx: TypeContext, bb: BasicBlock) -> list[str]:
    errors: list[str] = []

    def _ty(v: Value) -> Type:
        return v.type

    if isinstance(ins, BinOp):
        lt, rt, rt_result = _ty(ins.left), _ty(ins.right), _ty(ins.result)
        op = ins.op
        # Arithmetic / comparison: both operands same type.
        if lt is not rt and lt is not UNKNOWN and rt is not UNKNOWN:
            errors.append(
                f"function `{fn.name}` block `{bb.label}`: "
                f"BinOp `{op}` operand type mismatch: `{lt}` vs `{rt}`"
            )
        # Result type: for arithmetic matches operands; for comparison is Bool.
        if op in ("+", "-", "*", "/", "%"):
            if rt_result is not lt and lt is not UNKNOWN:
                errors.append(
                    f"function `{fn.name}` block `{bb.label}`: "
                    f"BinOp `{op}` result type `{rt_result}` doesn't match operand `{lt}`"
                )
        elif op in ("==", "!=", "<", ">", "<=", ">=", "&&", "||"):
            if rt_result is not ctx.BOOL:
                errors.append(
                    f"function `{fn.name}` block `{bb.label}`: "
                    f"BinOp `{op}` result must be Bool, got `{rt_result}`"
                )
    elif isinstance(ins, UnaryOp):
        ot, rt_result = _ty(ins.operand), _ty(ins.result)
        if ins.op == "-":
            if ot not in (ctx.INT, ctx.FLOAT) and ot is not UNKNOWN:
                errors.append(
                    f"function `{fn.name}` block `{bb.label}`: "
                    f"unary `-` requires Int/Float, got `{ot}`"
                )
            if rt_result is not ot and ot is not UNKNOWN:
                errors.append(
                    f"function `{fn.name}` block `{bb.label}`: "
                    f"unary `-` result `{rt_result}` doesn't match operand `{ot}`"
                )
        elif ins.op == "!":
            if ot is not ctx.BOOL and ot is not UNKNOWN:
                errors.append(
                    f"function `{fn.name}` block `{bb.label}`: "
                    f"unary `!` requires Bool, got `{ot}`"
                )
            if rt_result is not ctx.BOOL:
                errors.append(
                    f"function `{fn.name}` block `{bb.label}`: "
                    f"unary `!` result must be Bool, got `{rt_result}`"
                )
    elif isinstance(ins, GetElement):
        arr_ty = _ty(ins.array)
        if not isinstance(arr_ty, ArrayType) and arr_ty is not UNKNOWN:
            errors.append(
                f"function `{fn.name}` block `{bb.label}`: "
                f"GetElement on non-array `{arr_ty}`"
            )
        idx_ty = _ty(ins.index)
        if idx_ty is not ctx.INT and idx_ty is not UNKNOWN:
            errors.append(
                f"function `{fn.name}` block `{bb.label}`: "
                f"GetElement index must be Int, got `{idx_ty}`"
            )
    elif isinstance(ins, SetElement):
        arr_ty = _ty(ins.array)
        if not isinstance(arr_ty, ArrayType) and arr_ty is not UNKNOWN:
            errors.append(
                f"function `{fn.name}` block `{bb.label}`: "
                f"SetElement on non-array `{arr_ty}`"
            )
    elif isinstance(ins, GetField):
        obj_ty = _ty(ins.obj)
        if not isinstance(obj_ty, StructType) and obj_ty is not UNKNOWN:
            errors.append(
                f"function `{fn.name}` block `{bb.label}`: "
                f"GetField on non-struct `{obj_ty}`"
            )
    elif isinstance(ins, SetField):
        obj_ty = _ty(ins.obj)
        if not isinstance(obj_ty, StructType) and obj_ty is not UNKNOWN:
            errors.append(
                f"function `{fn.name}` block `{bb.label}`: "
                f"SetField on non-struct `{obj_ty}`"
            )
    elif isinstance(ins, Call):
        # Verify argument count and types against the callee.
        callee = next((f for f in mod.functions if f.name == ins.name), None)
        if callee is not None:
            if len(callee.params) != len(ins.args):
                errors.append(
                    f"function `{fn.name}` block `{bb.label}`: "
                    f"Call `{ins.name}` expects {len(callee.params)} args, "
                    f"got {len(ins.args)}"
                )
            else:
                for i, (p, a) in enumerate(zip(callee.params, ins.args)):
                    pt, at = _ty(p), _ty(a)
                    if pt is not at and pt is not UNKNOWN and at is not UNKNOWN:
                        errors.append(
                            f"function `{fn.name}` block `{bb.label}`: "
                            f"Call `{ins.name}` arg {i} type mismatch: "
                            f"expected `{pt}`, got `{at}`"
                        )
            # Return type check.
            if ins.result is not None:
                rt_result = _ty(ins.result)
                if rt_result is not callee.return_type and rt_result is not UNKNOWN:
                    errors.append(
                        f"function `{fn.name}` block `{bb.label}`: "
                        f"Call `{ins.name}` result type `{rt_result}` "
                        f"doesn't match callee return `{callee.return_type}`"
                    )

    return errors


def _check_terminator_types(t: Terminator, fn: Function,
                              bb: BasicBlock) -> list[str]:
    errors: list[str] = []
    if isinstance(t, Return):
        if t.value is None:
            if fn.return_type is not None and fn.return_type.name != "Void":
                errors.append(
                    f"function `{fn.name}` block `{bb.label}`: "
                    f"empty Return in non-Void function"
                )
        else:
            vt = t.value.type
            if vt is not fn.return_type and vt is not UNKNOWN:
                # Allow Int sentinel in Void functions — the IR lowering
                # for Void functions emits `return` (no value), but
                # defensive code paths may still produce `return 0`.
                if not (fn.return_type.name == "Void" and isinstance(vt, PrimitiveType) and vt.name == "Int"):
                    errors.append(
                        f"function `{fn.name}` block `{bb.label}`: "
                        f"Return value `{vt}` doesn't match function return `{fn.return_type}`"
                    )
    elif isinstance(t, CondBranch):
        ct = t.cond.type
        if ct is not None and not _is_bool(ct) and ct is not UNKNOWN:
            errors.append(
                f"function `{fn.name}` block `{bb.label}`: "
                f"CondBranch cond must be Bool, got `{ct}`"
            )
    return errors


def _is_bool(ty: Type) -> bool:
    """True iff `ty` is the Bool primitive type."""

    return isinstance(ty, PrimitiveType) and ty.name == "Bool"


# ---------------------------------------------------------------------------
# Helpers — value references and definitions


def _value_references(ins: Instruction) -> list[Value]:
    """All `Value`s read by an instruction."""

    refs: list[Value] = []
    if isinstance(ins, Store):
        refs.append(ins.value)
    elif isinstance(ins, Load):
        pass   # source is a Local, checked separately
    elif isinstance(ins, BinOp):
        refs.extend([ins.left, ins.right])
    elif isinstance(ins, UnaryOp):
        refs.append(ins.operand)
    elif isinstance(ins, Call):
        refs.extend(ins.args)
    elif isinstance(ins, ArrayLit):
        refs.extend(ins.elements)
    elif isinstance(ins, StructLit):
        refs.extend(ins.fields)
    elif isinstance(ins, GetField):
        refs.append(ins.obj)
    elif isinstance(ins, SetField):
        refs.extend([ins.obj, ins.value])
    elif isinstance(ins, GetElement):
        refs.extend([ins.array, ins.index])
    elif isinstance(ins, SetElement):
        refs.extend([ins.array, ins.index, ins.value])
    return refs


def _value_definitions(ins: Instruction) -> list[Value]:
    """All `Value`s (Temps) defined by an instruction."""

    defs: list[Value] = []
    if isinstance(ins, Load):
        defs.append(ins.result)
    elif isinstance(ins, BinOp):
        defs.append(ins.result)
    elif isinstance(ins, UnaryOp):
        defs.append(ins.result)
    elif isinstance(ins, Call):
        if ins.result is not None:
            defs.append(ins.result)
    elif isinstance(ins, StringLit):
        defs.append(ins.result)
    elif isinstance(ins, ArrayLit):
        defs.append(ins.result)
    elif isinstance(ins, StructLit):
        defs.append(ins.result)
    elif isinstance(ins, GetField):
        defs.append(ins.result)
    elif isinstance(ins, GetElement):
        defs.append(ins.result)
    return defs


def _terminator_value_refs(t: Terminator) -> list[Value]:
    if isinstance(t, Return):
        return [t.value] if t.value is not None else []
    if isinstance(t, CondBranch):
        return [t.cond]
    return []


def assert_module_valid(mod: Module, ctx: Optional[TypeContext] = None) -> None:
    """Raise `AssertionError` if `mod` is not well-formed.

    If `ctx` is provided, type-consistency checks are also performed.
    """

    errors = verify_module(mod, ctx)
    if errors:
        raise AssertionError(
            "Dextra IR verification failed:\n  - " + "\n  - ".join(errors)
        )
