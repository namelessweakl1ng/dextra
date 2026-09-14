"""Lower a typed AST into Dextra IR.

The lowering walks each function declaration, builds a `Function` IR
object with basic blocks, allocas for locals/params, and SSA temporaries
for expression results.  Control flow (if/while/for) becomes
`BasicBlock`s with `CondBranch`/`Branch` terminators.
"""
from __future__ import annotations

from typing import Optional

from dextra.ast import nodes as ast
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
    IRBuilder,
    Load,
    Local,
    Module,
    Param,
    Return,
    SetElement,
    SetField,
    Store,
    StringLit,
    StructLit,
    Temp,
    UnaryOp,
    Value,
)
from dextra.semantic import Analyzer, get_enum_variant, get_resolved, get_type
from dextra.types.type_system import ArrayType, EnumType, StructType, Type, TypeContext


class IRLowering:
    def __init__(self, analyzed) -> None:
        self.program = analyzed.program
        self.ctx: TypeContext = analyzed.ctx
        self.functions = analyzed.functions
        self.builder = IRBuilder(self.ctx)
        self._fn_blocks: list[BasicBlock] = []
        self._scope: dict[str, Local] = {}
        self._current_block: Optional[BasicBlock] = None
        self._string_constants: dict[str, Temp] = {}
        self._block_counter = 0
        self._break_target: Optional[BasicBlock] = None
        self._continue_target: Optional[BasicBlock] = None

    def lower(self) -> Module:
        mod = Module(name="dextra_module")
        for decl in self.program.declarations:
            if isinstance(decl, ast.FunctionDeclaration):
                fn = self._lower_function(decl)
                mod.functions.append(fn)
        return mod

    # -- per-function --------------------------------------------------

    def _lower_function(self, fn: ast.FunctionDeclaration) -> Function:
        finfo = self.functions[fn.name]
        params = [
            Param(index=i, name=p.name, type=t)
            for i, (p, t) in enumerate(zip(fn.parameters, finfo.param_types))
        ]
        entry = BasicBlock(label="entry")
        self._fn_blocks = [entry]
        ir_fn = Function(name=fn.name, return_type=finfo.return_type, params=params,
                         blocks=[entry])
        # Bind each parameter into an alloca (LLVM-style).
        self._scope = {}
        self._current_block = entry
        self._string_constants = {}
        self._block_counter = 0
        for p in params:
            slot = Local(name=f"{p.name}.addr", type=p.type)
            self._scope[p.name] = slot
            entry.instructions.append(Alloca(name=slot.name, type=p.type))
            entry.instructions.append(Store(target=slot, value=p))
        # Walk body.
        self._lower_block(fn.body)
        # Ensure terminator: if the block has no terminator, fall through to
        # a default `return`.  For Void functions, the default is `return`
        # (no value); for typed functions, it's `return <zero>` — though
        # note that the analyzer's E0218 check should already have
        # rejected any non-Void function that reaches this point.
        if self._current_block.terminator is None:
            self._current_block.terminator = Return(
                None if ir_fn.return_type is self.ctx.VOID
                else self._default_value(ir_fn.return_type)
            )
        # Any unreachable blocks (created but no predecessor) get a default
        # terminator too, so the LLVM backend doesn't crash on them.
        for bb in self._fn_blocks:
            if bb.terminator is None:
                bb.terminator = Return(
                    None if ir_fn.return_type is self.ctx.VOID
                    else self._default_value(ir_fn.return_type)
                )
        ir_fn.blocks = self._fn_blocks
        return ir_fn

    def _default_value(self, ty: Type) -> Value:
        if ty is self.ctx.INT:
            return ConstInt(value=0, type=ty)
        if ty is self.ctx.FLOAT:
            return ConstFloat(value=0.0, type=ty)
        if ty is self.ctx.BOOL:
            return ConstBool(value=False, type=ty)
        if ty is self.ctx.STRING:
            return ConstString(value="", type=ty)
        # Void
        return ConstInt(value=0, type=self.ctx.INT)

    def _new_block(self, prefix: str = "bb") -> BasicBlock:
        self._block_counter += 1
        bb = BasicBlock(label=f"{prefix}{self._block_counter}")
        self._fn_blocks.append(bb)
        return bb

    def _switch_to(self, bb: BasicBlock) -> None:
        self._current_block = bb

    def _emit(self, instr) -> None:
        self._current_block.instructions.append(instr)

    def _set_terminator(self, term) -> None:
        self._current_block.terminator = term

    # -- statements -----------------------------------------------------

    def _lower_block(self, block: ast.Block) -> None:
        # Save and restore scope for block-scoped lets.
        saved = dict(self._scope)
        for stmt in block.statements:
            self._lower_statement(stmt)
            if self._current_block.terminator is not None:
                break
        # Restore scope.  (Locals that were declared inside this block are
        # still in the IR — we just stop looking them up by name.)
        self._scope = saved

    def _lower_statement(self, stmt: ast.Statement) -> None:
        if isinstance(stmt, ast.LetStatement):
            value = self._lower_expression(stmt.value)
            var_ty = get_type(stmt.value) or self.ctx.INT
            slot = Local(name=f"{stmt.name}.slot", type=var_ty)
            self._scope[stmt.name] = slot
            self._emit(Alloca(name=slot.name, type=var_ty))
            self._emit(Store(target=slot, value=value))
        elif isinstance(stmt, ast.ReturnStatement):
            if stmt.value is None:
                self._set_terminator(Return(None))
            else:
                v = self._lower_expression(stmt.value)
                self._set_terminator(Return(v))
        elif isinstance(stmt, ast.BreakStatement):
            # Should be set by enclosing loop lowering.
            target = self._break_target
            assert target is not None
            self._set_terminator(Branch(target=target))
        elif isinstance(stmt, ast.ContinueStatement):
            target = self._continue_target
            assert target is not None
            self._set_terminator(Branch(target=target))
        elif isinstance(stmt, ast.IfStatement):
            self._lower_if(stmt)
        elif isinstance(stmt, ast.WhileStatement):
            self._lower_while(stmt)
        elif isinstance(stmt, ast.ForStatement):
            self._lower_for(stmt)
        elif isinstance(stmt, ast.Assignment):
            self._lower_assignment(stmt)
        elif isinstance(stmt, ast.ExpressionStatement):
            self._lower_expression(stmt.expression)
        elif isinstance(stmt, ast.Block):
            self._lower_block(stmt)

    def _lower_if(self, stmt: ast.IfStatement) -> None:
        cond = self._lower_expression(stmt.condition)
        then_bb = self._new_block("then")
        else_bb = self._new_block("else") if stmt.else_branch is not None else self._new_block("endif")
        endif = self._new_block("endif") if stmt.else_branch is not None else else_bb
        self._set_terminator(CondBranch(cond=cond, then_target=then_bb, else_target=else_bb))

        # then
        self._current_block = then_bb
        self._lower_block(stmt.then_block)
        if self._current_block.terminator is None:
            self._set_terminator(Branch(target=endif))

        # else
        if stmt.else_branch is not None:
            self._current_block = else_bb
            if isinstance(stmt.else_branch, ast.Block):
                self._lower_block(stmt.else_branch)
            else:
                self._lower_statement(stmt.else_branch)
            if self._current_block.terminator is None:
                self._set_terminator(Branch(target=endif))
        # endif
        self._current_block = endif

    def _lower_while(self, stmt: ast.WhileStatement) -> None:
        cond_bb = self._new_block("while_cond")
        body_bb = self._new_block("while_body")
        end_bb = self._new_block("while_end")
        self._set_terminator(Branch(target=cond_bb))

        self._current_block = cond_bb
        cond = self._lower_expression(stmt.condition)
        self._set_terminator(CondBranch(cond=cond, then_target=body_bb, else_target=end_bb))

        self._current_block = body_bb
        saved_break, saved_continue = self._break_target, self._continue_target
        self._break_target = end_bb
        self._continue_target = cond_bb
        self._lower_block(stmt.body)
        self._break_target = saved_break
        self._continue_target = saved_continue
        if self._current_block.terminator is None:
            self._set_terminator(Branch(target=cond_bb))

        self._current_block = end_bb

    def _lower_for(self, stmt: ast.ForStatement) -> None:
        # Allocate the loop variable.
        if isinstance(stmt.iterable, ast.RangeExpression):
            start = self._lower_expression(stmt.iterable.start)
            end = self._lower_expression(stmt.iterable.end)
            var_slot = Local(name=f"{stmt.var}.slot", type=self.ctx.INT)
            self._scope[stmt.var] = var_slot
            self._emit(Alloca(name=var_slot.name, type=var_slot.type))
            self._emit(Store(target=var_slot, value=start))
            end_slot = self.builder.fresh_local("end", self.ctx.INT)
            self._emit(Alloca(name=end_slot.name, type=end_slot.type))
            self._emit(Store(target=end_slot, value=end))

            cond_bb = self._new_block("for_cond")
            body_bb = self._new_block("for_body")
            latch_bb = self._new_block("for_latch")   # increment goes here
            end_bb = self._new_block("for_end")
            self._set_terminator(Branch(target=cond_bb))

            self._current_block = cond_bb
            i_val = self.builder.fresh("i", self.ctx.INT)
            self._emit(Load(result=i_val, source=var_slot))
            end_val = self.builder.fresh("end", self.ctx.INT)
            self._emit(Load(result=end_val, source=end_slot))
            cmp = self.builder.fresh("cmp", self.ctx.BOOL)
            self._emit(BinOp(result=cmp, op="<", left=i_val, right=end_val))
            self._set_terminator(CondBranch(cond=cmp, then_target=body_bb, else_target=end_bb))

            self._current_block = body_bb
            saved_break, saved_continue = self._break_target, self._continue_target
            self._break_target = end_bb
            # `continue` must still run the increment — point it at the
            # latch block, not the cond block.  Otherwise the loop
            # variable never advances and we spin forever.
            self._continue_target = latch_bb
            self._lower_block(stmt.body)
            self._break_target = saved_break
            self._continue_target = saved_continue
            if self._current_block.terminator is None:
                self._set_terminator(Branch(target=latch_bb))

            # Latch: increment i, jump back to cond.
            self._current_block = latch_bb
            cur = self.builder.fresh("i_cur", self.ctx.INT)
            self._emit(Load(result=cur, source=var_slot))
            nxt = self.builder.fresh("i_next", self.ctx.INT)
            self._emit(BinOp(result=nxt, op="+", left=cur, right=ConstInt(value=1, type=self.ctx.INT)))
            self._emit(Store(target=var_slot, value=nxt))
            self._set_terminator(Branch(target=cond_bb))
            self._current_block = end_bb
        else:
            # For-over-array.
            arr = self._lower_expression(stmt.iterable)
            arr_ty = get_type(stmt.iterable) or self.ctx.array(self.ctx.INT)
            assert isinstance(arr_ty, ArrayType)
            elem_ty = arr_ty.element_type
            i_slot = self.builder.fresh_local("i", self.ctx.INT)
            self._emit(Alloca(name=i_slot.name, type=i_slot.type))
            self._emit(Store(target=i_slot, value=ConstInt(value=0, type=self.ctx.INT)))
            len_val = self.builder.fresh("len", self.ctx.INT)
            self._emit(Call(result=len_val, name="length", args=[arr], return_type=self.ctx.INT))

            cond_bb = self._new_block("for_cond")
            body_bb = self._new_block("for_body")
            latch_bb = self._new_block("for_latch")   # increment goes here
            end_bb = self._new_block("for_end")
            self._set_terminator(Branch(target=cond_bb))

            self._current_block = cond_bb
            i_val = self.builder.fresh("i", self.ctx.INT)
            self._emit(Load(result=i_val, source=i_slot))
            cmp = self.builder.fresh("cmp", self.ctx.BOOL)
            self._emit(BinOp(result=cmp, op="<", left=i_val, right=len_val))
            self._set_terminator(CondBranch(cond=cmp, then_target=body_bb, else_target=end_bb))

            self._current_block = body_bb
            elem = self.builder.fresh("elem", elem_ty)
            self._emit(GetElement(result=elem, array=arr, index=i_val))
            var_slot = Local(name=f"{stmt.var}.slot", type=elem_ty)
            self._scope[stmt.var] = var_slot
            self._emit(Alloca(name=var_slot.name, type=var_slot.type))
            self._emit(Store(target=var_slot, value=elem))

            saved_break, saved_continue = self._break_target, self._continue_target
            self._break_target = end_bb
            self._continue_target = latch_bb   # see range-loop comment
            self._lower_block(stmt.body)
            self._break_target = saved_break
            self._continue_target = saved_continue
            if self._current_block.terminator is None:
                self._set_terminator(Branch(target=latch_bb))

            # Latch: increment i, jump back to cond.
            self._current_block = latch_bb
            cur = self.builder.fresh("i_cur", self.ctx.INT)
            self._emit(Load(result=cur, source=i_slot))
            nxt = self.builder.fresh("i_next", self.ctx.INT)
            self._emit(BinOp(result=nxt, op="+", left=cur, right=ConstInt(value=1, type=self.ctx.INT)))
            self._emit(Store(target=i_slot, value=nxt))
            self._set_terminator(Branch(target=cond_bb))
            self._current_block = end_bb

    def _lower_match(self, expr: ast.MatchExpression) -> Value:
        """Lower a `match` expression to a chain of CondBranch blocks.

        Strategy: enum values are i64 tags at runtime.  For each arm with
        an `EnumVariantPattern`, emit a block that compares the scrutinee's
        tag to the expected tag; if equal, evaluate the arm body, store it
        to the result slot, and branch to the merge block.  The wildcard
        arm (if present) is the catch-all at the end.

        For Void-typed matches (used as statements), no result slot is
        allocated — arms just branch to the merge block after evaluating
        their bodies.
        """

        # Lower the scrutinee to a temp.
        scrutinee_val = self._lower_expression(expr.scrutinee)
        result_ty = get_type(expr) or self.ctx.INT
        is_void = result_ty is self.ctx.VOID
        # Allocate a slot for the match's result (unless Void).
        result_slot = None
        if not is_void:
            result_slot = self.builder.fresh_local("match_result", result_ty)
            self._emit(Alloca(name=result_slot.name, type=result_ty))
        # Merge block: all arms branch here after storing their result.
        merge_bb = self._new_block("match_merge")
        # For each arm, allocate a check block + body block.
        arm_check_blocks: list[BasicBlock] = []
        arm_body_blocks: list[BasicBlock] = []
        for i, arm in enumerate(expr.arms):
            arm_check_blocks.append(self._new_block(f"match_chk{i}"))
            arm_body_blocks.append(self._new_block(f"match_arm{i}"))
        # Entry: branch to the first check block.
        self._set_terminator(Branch(target=arm_check_blocks[0]))
        # Lower each arm.
        for i, arm in enumerate(expr.arms):
            # Check block: compare scrutinee tag to expected tag (or pass
            # through for the wildcard).
            self._current_block = arm_check_blocks[i]
            if isinstance(arm.pattern, ast.WildcardPattern):
                # Wildcard: always matches; jump straight to body.
                self._set_terminator(Branch(target=arm_body_blocks[i]))
            else:
                # EnumVariantPattern: compare the tag.
                enum_type = self.ctx.lookup_enum(arm.pattern.enum_name)
                assert enum_type is not None
                tag = enum_type.variant_tag(arm.pattern.variant_name)
                assert tag is not None
                cmp = self.builder.fresh("match_cmp", self.ctx.BOOL)
                self._emit(BinOp(
                    result=cmp, op="==",
                    left=scrutinee_val,
                    right=ConstInt(value=tag, type=enum_type),
                ))
                next_target = (arm_check_blocks[i + 1]
                                if i + 1 < len(expr.arms) else merge_bb)
                self._set_terminator(CondBranch(
                    cond=cmp,
                    then_target=arm_body_blocks[i],
                    else_target=next_target,
                ))
            # Body block: evaluate the arm body, store to result slot
            # (if non-Void), branch to merge.
            self._current_block = arm_body_blocks[i]
            body_val = self._lower_expression(arm.body)
            if result_slot is not None:
                self._emit(Store(target=result_slot, value=body_val))
            self._set_terminator(Branch(target=merge_bb))
        # Merge block: load the result (if non-Void).
        self._current_block = merge_bb
        if is_void:
            # Return a placeholder — the caller won't use it.
            return ConstInt(value=0, type=self.ctx.INT)
        result_temp = self.builder.fresh("match_val", result_ty)
        self._emit(Load(result=result_temp, source=result_slot))
        return result_temp

    def _lower_assignment(self, stmt: ast.Assignment) -> None:
        v = self._lower_expression(stmt.value)
        if isinstance(stmt.target, ast.IdentifierExpression):
            slot = self._scope[stmt.target.name]
            self._emit(Store(target=slot, value=v))
        elif isinstance(stmt.target, ast.IndexExpression):
            arr = self._lower_expression(stmt.target.target)
            idx = self._lower_expression(stmt.target.index)
            self._emit(SetElement(array=arr, index=idx, value=v))
        elif isinstance(stmt.target, ast.FieldAccessExpression):
            obj = self._lower_expression(stmt.target.target)
            # Use the *type* attached to the target expression rather than
            # the resolved symbol, because the target may be a nested
            # expression like `users[0]` whose type is `StructType` but
            # whose resolved symbol is None.
            struct_type = get_type(stmt.target.target)
            assert struct_type is not None, (
                "field access on expression with no resolved type — "
                "this indicates a semantic-analyzer bug"
            )
            assert isinstance(struct_type, StructType), (
                f"field access on non-struct type {struct_type} — "
                "the type checker should have rejected this"
            )
            idx = struct_type.field_index(stmt.target.field)
            assert idx is not None
            self._emit(SetField(obj=obj, struct_name=struct_type.struct_name,
                                field_index=idx, value=v))
        else:
            raise RuntimeError(f"invalid assignment target: {type(stmt.target).__name__}")

    # -- expressions ----------------------------------------------------

    def _lower_expression(self, expr: ast.Expression) -> Value:
        if isinstance(expr, ast.IntegerLiteral):
            return ConstInt(value=expr.value, type=self.ctx.INT)
        if isinstance(expr, ast.FloatLiteral):
            return ConstFloat(value=expr.value, type=self.ctx.FLOAT)
        if isinstance(expr, ast.StringLiteral):
            # Always emit a fresh StringLit instruction — don't deduplicate
            # at the IR level.  String globals are deduplicated at the LLVM
            # level (see LLVMBackend._lower_instruction for StringLit).
            # IR-level dedup caused dominance violations when the same
            # string appeared in multiple match arms (sibling blocks).
            t = self.builder.fresh("str", self.ctx.STRING)
            self._emit(StringLit(result=t, value=expr.value))
            return t
        if isinstance(expr, ast.BooleanLiteral):
            return ConstBool(value=expr.value, type=self.ctx.BOOL)
        if isinstance(expr, ast.IdentifierExpression):
            slot = self._scope[expr.name]
            t = self.builder.fresh(expr.name, slot.type)
            self._emit(Load(result=t, source=slot))
            return t
        if isinstance(expr, ast.ArrayLiteral):
            elems = [self._lower_expression(e) for e in expr.elements]
            arr_ty = get_type(expr)
            assert isinstance(arr_ty, ArrayType)
            t = self.builder.fresh("arr", arr_ty)
            self._emit(ArrayLit(result=t, elements=elems, element_type=arr_ty.element_type))
            return t
        if isinstance(expr, ast.StructLiteral):
            sym = get_resolved(expr)
            assert sym is not None
            struct_type = sym.type
            assert isinstance(struct_type, StructType)
            # Build field values in declaration order.
            field_map = {f.name: self._lower_expression(f.value) for f in expr.fields}
            values = [field_map[fn] for fn, _ in struct_type.fields]
            t = self.builder.fresh("struct", struct_type)
            self._emit(StructLit(result=t, struct_name=struct_type.struct_name, fields=values))
            return t
        if isinstance(expr, ast.BinaryOp):
            left = self._lower_expression(expr.left)
            right = self._lower_expression(expr.right)
            ty = get_type(expr) or self.ctx.INT
            t = self.builder.fresh("t", ty)
            self._emit(BinOp(result=t, op=expr.op, left=left, right=right))
            return t
        if isinstance(expr, ast.UnaryOp):
            operand = self._lower_expression(expr.operand)
            ty = get_type(expr) or self.ctx.INT
            t = self.builder.fresh("u", ty)
            self._emit(UnaryOp(result=t, op=expr.op, operand=operand))
            return t
        if isinstance(expr, ast.CallExpression):
            args = [self._lower_expression(a) for a in expr.arguments]
            ret_ty = get_type(expr) or self.ctx.VOID
            result = None if ret_ty is self.ctx.VOID else self.builder.fresh("call", ret_ty)
            name = expr.callee.name if isinstance(expr.callee, ast.IdentifierExpression) else "<bad>"
            # For builtin print/println we dispatch on arg type at codegen.
            self._emit(Call(result=result, name=name, args=args, return_type=ret_ty))
            if result is None:
                return ConstInt(value=0, type=self.ctx.INT)   # void placeholder
            return result
        if isinstance(expr, ast.IndexExpression):
            arr = self._lower_expression(expr.target)
            idx = self._lower_expression(expr.index)
            elem_ty = get_type(expr) or self.ctx.INT
            t = self.builder.fresh("idx", elem_ty)
            self._emit(GetElement(result=t, array=arr, index=idx))
            return t
        if isinstance(expr, ast.FieldAccessExpression):
            # Enum variant reference: `EnumName.VariantName`.
            # The semantic analyzer marks these with `_enum_variant`
            # (see `get_enum_variant`).  They lower to a ConstInt with
            # the variant's tag, but the ConstInt's type is the *enum*
            # type (not Int) so the IR verifier and LLVM backend know
            # this is an enum value, not a plain Int.
            ev = get_enum_variant(expr)
            if ev is not None:
                enum_type, variant_name = ev
                tag = enum_type.variant_tag(variant_name)
                assert tag is not None
                return ConstInt(value=tag, type=enum_type)
            obj = self._lower_expression(expr.target)
            struct_type = get_type(expr.target)
            assert isinstance(struct_type, StructType)
            idx = struct_type.field_index(expr.field)
            assert idx is not None
            field_ty = struct_type.field_type(expr.field) or self.ctx.INT
            t = self.builder.fresh("fld", field_ty)
            self._emit(GetField(result=t, obj=obj, struct_name=struct_type.struct_name, field_index=idx))
            return t
        if isinstance(expr, ast.RangeExpression):
            # Range expressions only appear as `for` iterables, handled in
            # _lower_for.  Reaching one here is a bug.
            raise RuntimeError("range expression outside `for`")
        if isinstance(expr, ast.MatchExpression):
            return self._lower_match(expr)
        raise RuntimeError(f"unhandled expression {type(expr).__name__}")


def lower_program(analyzed) -> Module:
    """Convenience wrapper."""
    return IRLowering(analyzed).lower()
