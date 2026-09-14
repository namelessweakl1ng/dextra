"""LLVM backend.

Lowers Dextra IR to an `llvmlite.ir.Module`.  Uses the llvmlite API
to construct LLVM IR programmatically.  The result is a textual LLVM
IR module that can be passed to `llvmlite.binding` for native
compilation, or to `dextra emit-ir` for inspection.

Type representation (single source of truth — see docs/type-system.md):

| Dextra type | LLVM value type   | Storage in alloca slot    | Notes                         |
|------------|-------------------|---------------------------|-------------------------------|
| Int        | i64               | i64                       | by value                      |
| Float      | double            | double                    | by value                      |
| Bool       | i1                | i1                        | by value                      |
| String     | %DxString*        | %DxString*                | heap-allocated, by reference  |
| Array [T]  | %DxArray*         | %DxArray*                 | heap-allocated, by reference  |
| Struct     | %struct.Name*     | %struct.Name*             | heap-allocated, by reference  |
| Void       | void              | (n/a)                     | only as function return       |

Strings, arrays, and structs are all *by-reference* at the LLVM level —
their LLVM value is already a pointer to the heap-allocated payload.
This means:

  * Returning a struct from a function returns a heap pointer, not a
    dangling stack address.
  * Storing a struct in a `let` binding stores the pointer.
  * Storing a struct in an array stores the pointer (as i64).

For element-type preservation in arrays, see `ArrayLit` lowering.
"""
from __future__ import annotations

from llvmlite import ir

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
    Function as DxFunction,
    GetElement,
    GetField,
    Instruction,
    Load,
    Local,
    Module as DxModule,
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
from dextra.types.type_system import (
    ArrayType,
    EnumType,
    PrimitiveType,
    StructType,
    Type,
    TypeContext,
)


# By-reference Dextra types (LLVM value is already a pointer).
_BY_REFERENCE_PRIMITIVES = ("String", "Void")  # Void is special; only String is a value here


class LLVMBackend:
    def __init__(self, ctx: TypeContext) -> None:
        self.ctx = ctx
        # Use a *fresh* LLVM Context per backend instance so repeated
        # compilations in the same Python process don't collide on
        # identified type names like `struct.User`.  llvmlite's default
        # `ir.Context()` is process-global, which would otherwise cause
        # "already defined" errors when the same struct appears in two
        # successive compilations.
        self.llvm_context = ir.Context()
        self.module = ir.Module(name="dextra", context=self.llvm_context)
        self.module.triple = "x86_64-pc-linux-gnu"
        self._declare_runtime()
        self._struct_types: dict[str, ir.IdentifiedStructType] = {}
        self._string_globals: dict[str, ir.GlobalVariable] = {}
        self._string_counter = 0

    # -- public ---------------------------------------------------------

    def lower(self, mod: DxModule) -> ir.Module:
        # Declare any struct types first.
        # We discover struct types lazily from the IR (e.g. StructLit),
        # but for proper forward-declaration we just iterate over the
        # TypeContext's registered structs.
        for name, st in self.ctx._structs.items():
            self._get_llvm_struct_type(st)
        # Lower each function.
        for fn in mod.functions:
            self._lower_function(fn)
        return self.module

    # -- runtime declarations -------------------------------------------

    def _declare_runtime(self) -> None:
        # DxString
        dx_string = ir.LiteralStructType([ir.IntType(64), ir.IntType(8).as_pointer()])
        self._dx_string_type = dx_string
        self._dx_string_ptr = dx_string.as_pointer()

        # DxArray
        dx_array = ir.LiteralStructType([ir.IntType(64), ir.IntType(64).as_pointer()])
        self._dx_array_type = dx_array
        self._dx_array_ptr = dx_array.as_pointer()

        # Declare runtime functions.
        i64 = ir.IntType(64)
        i8p = ir.IntType(8).as_pointer()
        f64 = ir.DoubleType()

        def decl(name, ret, args):
            fnty = ir.FunctionType(ret, args)
            fn = ir.Function(self.module, fnty, name=name)
            return fn

        self._fn_dx_string_new = decl("dx_string_new", self._dx_string_ptr, [i8p, i64])
        self._fn_dx_string_concat = decl("dx_string_concat", self._dx_string_ptr, [self._dx_string_ptr, self._dx_string_ptr])
        self._fn_dx_string_length = decl("dx_string_length", i64, [self._dx_string_ptr])
        self._fn_dx_string_eq = decl("dx_string_eq", i64, [self._dx_string_ptr, self._dx_string_ptr])
        self._fn_dx_string_print = decl("dx_string_print", ir.VoidType(), [self._dx_string_ptr])
        self._fn_dx_string_println = decl("dx_string_println", ir.VoidType(), [self._dx_string_ptr])

        # Struct heap allocator — see docs/type-system.md "Struct" entry.
        # Returns i8* (opaque pointer); caller bitcasts to the struct type.
        self._fn_dx_alloc_struct = decl("dx_alloc_struct", i8p, [i64])

        self._fn_dx_array_new = decl("dx_array_new", self._dx_array_ptr, [i64])
        self._fn_dx_array_length = decl("dx_array_length", i64, [self._dx_array_ptr])
        self._fn_dx_array_get = decl("dx_array_get", i64, [self._dx_array_ptr, i64])
        self._fn_dx_array_set = decl("dx_array_set", ir.VoidType(), [self._dx_array_ptr, i64, i64])

        self._fn_dx_print_int = decl("dx_print_int", ir.VoidType(), [i64])
        self._fn_dx_print_float = decl("dx_print_float", ir.VoidType(), [f64])
        self._fn_dx_print_bool = decl("dx_print_bool", ir.VoidType(), [i64])
        self._fn_dx_println_int = decl("dx_println_int", ir.VoidType(), [i64])
        self._fn_dx_println_float = decl("dx_println_float", ir.VoidType(), [f64])
        self._fn_dx_println_bool = decl("dx_println_bool", ir.VoidType(), [i64])
        self._fn_dx_println_newline = decl("dx_println_newline", ir.VoidType(), [])
        self._fn_dx_print_string = decl("dx_print_string", ir.VoidType(), [self._dx_string_ptr])
        self._fn_dx_println_string = decl("dx_println_string", ir.VoidType(), [self._dx_string_ptr])

    # -- type mapping ---------------------------------------------------

    def _llvm_type(self, ty: Type) -> ir.Type:
        if isinstance(ty, PrimitiveType):
            return {
                "Int": ir.IntType(64),
                "Float": ir.DoubleType(),
                "Bool": ir.IntType(1),
                "String": self._dx_string_ptr,
                "Void": ir.VoidType(),
            }[ty.name]
        if isinstance(ty, ArrayType):
            return self._dx_array_ptr
        if isinstance(ty, StructType):
            return self._get_llvm_struct_type(ty).as_pointer()
        if isinstance(ty, EnumType):
            # Enums are represented as their variant tag (an i64).
            # Type safety is enforced statically by the compiler.
            return ir.IntType(64)
        raise RuntimeError(f"cannot lower type {ty}")

    def _slot_type(self, ty: Type) -> ir.Type:
        """The LLVM type of an alloca slot that holds a value of type `ty`.

        For by-value primitives (Int, Float, Bool) the slot is just the
        value type.  For by-reference types (String, Array, Struct) the
        LLVM value is itself a pointer, so the slot is `T*` — i.e. it
        holds the pointer directly.  No bitcast-on-load/store is needed.
        """

        return self._llvm_type(ty)

    def _get_llvm_struct_type(self, st: StructType) -> ir.IdentifiedStructType:
        if st.struct_name in self._struct_types:
            return self._struct_types[st.struct_name]
        t = self.module.context.get_identified_type(f"struct.{st.struct_name}")
        # Set body now.
        field_types = [self._llvm_type(ft) for _, ft in st.fields]
        t.set_body(*field_types)
        self._struct_types[st.struct_name] = t
        return t

    def _approx_struct_size(self, llvm_st: ir.IdentifiedStructType) -> int:
        """Conservative byte size for an LLVM struct type.

        LLVM's actual struct layout may add alignment padding between
        fields and at the end.  We compute a tight upper bound by:
          1. aligning each field's start to the field's natural alignment
             (8 for pointers/doubles/i64, 1 for i8/i1);
          2. summing field sizes;
          3. rounding the final total up to the struct alignment
             (max of all field alignments).
        This is the same algorithm LLVM uses for non-packed literal structs.
        """

        offset = 0
        max_align = 1
        for elty in llvm_st.elements:
            sz, al = self._type_size_align(elty)
            max_align = max(max_align, al)
            # Round offset up to field alignment.
            if al > 0 and offset % al != 0:
                offset += al - (offset % al)
            offset += sz
        # Round final size up to struct alignment.
        if max_align > 0 and offset % max_align != 0:
            offset += max_align - (offset % max_align)
        # Always allocate at least 1 byte so malloc never returns NULL for
        # zero-sized structs.
        return max(1, offset)

    def _type_size_align(self, ty: ir.Type) -> tuple[int, int]:
        """Return (size_bytes, alignment_bytes) for an LLVM type.

        For types we actually use as struct fields: i64, double, i1, ptr.
        """

        if isinstance(ty, ir.IntType):
            # i1 → 1 byte (LLVM rounds up), other ints → ceil(width/8) bytes
            sz = 1 if ty.width == 1 else (ty.width + 7) // 8
            return (sz, sz if sz <= 8 else 8)
        if isinstance(ty, ir.DoubleType):
            return (8, 8)
        if isinstance(ty, ir.PointerType):
            return (8, 8)
        if isinstance(ty, ir.IdentifiedStructType):
            return (self._approx_struct_size(ty), 8)
        if isinstance(ty, ir.ArrayType):
            esz, eal = self._type_size_align(ty.element)
            return (esz * ty.count, eal)
        # Default: 8 bytes, 8-aligned.
        return (8, 8)

    # -- per-function ---------------------------------------------------

    def _lower_function(self, fn: DxFunction) -> None:
        ret_ty = self._llvm_type(fn.return_type)
        param_tys = [self._llvm_type(p.type) for p in fn.params]
        fnty = ir.FunctionType(ret_ty, param_tys)
        llvm_fn = ir.Function(self.module, fnty, name=fn.name)
        # Set parameter names.
        for p, llvm_p in zip(fn.params, llvm_fn.args):
            llvm_p.name = p.name

        # Create the function-level builder state.
        self._llvm_fn = llvm_fn
        self._blocks: dict[str, ir.Block] = {}
        self._values: dict[int, ir.Value] = {}     # Python id -> LLVM value
        self._alloca_slots: dict[str, ir.Value] = {}

        # Pre-create all basic blocks so terminators can target them.
        for bb in fn.blocks:
            self._blocks[bb.label] = llvm_fn.append_basic_block(bb.label)

        # Lower each block.
        for bb in fn.blocks:
            self._lower_block(bb)

    def _lower_block(self, bb: BasicBlock) -> None:
        self._builder = ir.IRBuilder(self._blocks[bb.label])
        for instr in bb.instructions:
            self._lower_instruction(instr)
        self._lower_terminator(bb)

    # -- instruction lowering -------------------------------------------

    def _lower_instruction(self, instr: Instruction) -> None:
        if isinstance(instr, Alloca):
            # For "by-value" types (Int, Float, Bool), alloca the slot directly.
            # For "by-reference" types (String, Array, Struct), the *value* is
            # already a pointer, so the slot must be a pointer-to-pointer.
            slot_ty = self._slot_type(instr.type)
            slot = self._builder.alloca(slot_ty, name=instr.name)
            self._alloca_slots[instr.name] = slot
            return
        if isinstance(instr, Store):
            target = self._alloca_slots[instr.target.name]
            val = self._value(instr.value)
            self._builder.store(val, target)
            return
        if isinstance(instr, Load):
            source = self._alloca_slots[instr.source.name]
            v = self._builder.load(source, name=instr.result.name)
            self._values[id(instr.result)] = v
            return
        if isinstance(instr, BinOp):
            l = self._value(instr.left)
            r = self._value(instr.right)
            v = self._lower_binop(instr.op, l, r, instr.result)
            self._values[id(instr.result)] = v
            return
        if isinstance(instr, UnaryOp):
            operand = self._value(instr.operand)
            if instr.op == "-":
                if isinstance(operand.type, ir.IntType):
                    v = self._builder.sub(ir.IntType(64)(0), operand, name=instr.result.name)
                else:
                    v = self._builder.fsub(ir.DoubleType()(0.0), operand, name=instr.result.name)
            elif instr.op == "!":
                v = self._builder.xor(operand, ir.IntType(1)(1), name=instr.result.name)
            else:
                raise RuntimeError(f"unknown unary op {instr.op}")
            self._values[id(instr.result)] = v
            return
        if isinstance(instr, StringLit):
            gv = self._string_globals.get(instr.value)
            if gv is None:
                encoded = instr.value.encode("utf-8") + b"\0"
                arr_ty = ir.ArrayType(ir.IntType(8), len(encoded))
                name = f".str{self._string_counter}"
                self._string_counter += 1
                gv = ir.GlobalVariable(self.module, arr_ty, name=name)
                gv.global_constant = True
                gv.initializer = ir.Constant(arr_ty, bytearray(encoded))
                self._string_globals[instr.value] = gv
            # Bitcast [N x i8]* to i8*.
            i8p = ir.IntType(8).as_pointer()
            ptr = self._builder.bitcast(gv, i8p, name=instr.result.name + ".ptr")
            length = len(instr.value.encode("utf-8"))
            call = self._builder.call(self._fn_dx_string_new,
                                       [ptr, ir.IntType(64)(length)],
                                       name=instr.result.name)
            self._values[id(instr.result)] = call
            return
        if isinstance(instr, ArrayLit):
            length = len(instr.elements)
            arr = self._builder.call(self._fn_dx_array_new,
                                      [ir.IntType(64)(length)],
                                      name=instr.result.name + ".arr")
            for i, el in enumerate(instr.elements):
                # Coerce element to i64 (either an int or a pointer bitcast).
                v = self._coerce_to_i64(el)
                # Store via dx_array_set(arr, i, v).
                self._builder.call(self._fn_dx_array_set,
                                    [arr, ir.IntType(64)(i), v])
            self._values[id(instr.result)] = arr
            return
        if isinstance(instr, StructLit):
            st = self.ctx.lookup_struct(instr.struct_name)
            assert st is not None
            llvm_st = self._get_llvm_struct_type(st)
            # Heap-allocate the struct so its lifetime outlives the function
            # that creates it.  Returning a struct from a function returns
            # a stable heap pointer rather than a dangling stack address.
            # Compute the size by summing (rounded-up) field sizes — this is
            # the same layout LLVM will apply to a literal struct.
            size_bytes = self._approx_struct_size(llvm_st)
            mem_i8 = self._builder.call(
                self._fn_dx_alloc_struct,
                [ir.IntType(64)(size_bytes)],
                name=instr.result.name + ".mem",
            )
            mem = self._builder.bitcast(mem_i8, llvm_st.as_pointer(),
                                         name=instr.result.name + ".bc")
            for i, (val, (_, ft)) in enumerate(zip(instr.fields, st.fields)):
                llvm_val = self._value(val)
                addr = self._builder.gep(mem, [ir.IntType(32)(0), ir.IntType(32)(i)],
                                          name=f"{instr.result.name}.fld{i}.addr")
                self._builder.store(llvm_val, addr)
            self._values[id(instr.result)] = mem
            return
        if isinstance(instr, GetField):
            obj = self._value(instr.obj)
            st = self.ctx.lookup_struct(instr.struct_name)
            assert st is not None
            addr = self._builder.gep(obj, [ir.IntType(32)(0), ir.IntType(32)(instr.field_index)],
                                      name=instr.result.name + ".addr")
            v = self._builder.load(addr, name=instr.result.name)
            self._values[id(instr.result)] = v
            return
        if isinstance(instr, SetField):
            obj = self._value(instr.obj)
            v = self._value(instr.value)
            addr = self._builder.gep(obj, [ir.IntType(32)(0), ir.IntType(32)(instr.field_index)],
                                      name="setfld.addr")
            self._builder.store(v, addr)
            return
        if isinstance(instr, GetElement):
            arr = self._value(instr.array)
            idx = self._value(instr.index)
            raw = self._builder.call(self._fn_dx_array_get, [arr, idx],
                                     name=instr.result.name + ".raw")
            # `dx_array_get` returns i64.  We must reverse the coercion
            # performed when the element was stored:
            #   * Int     → i64 is the value itself
            #   * Bool    → zext i1 to i64 → trunc back to i1
            #   * Float   → bitcast double to i64 → bitcast back to double
            #   * pointer → ptrtoint → inttoptr back to the typed pointer
            elem_llvm_ty = self._llvm_type(instr.result.type)
            if isinstance(elem_llvm_ty, ir.PointerType):
                v = self._builder.inttoptr(raw, elem_llvm_ty,
                                            name=instr.result.name)
            elif isinstance(elem_llvm_ty, ir.DoubleType):
                v = self._builder.bitcast(raw, ir.DoubleType(),
                                           name=instr.result.name + ".f")
            elif isinstance(elem_llvm_ty, ir.IntType) and elem_llvm_ty.width == 1:
                v = self._builder.trunc(raw, ir.IntType(1),
                                         name=instr.result.name + ".b")
            else:
                v = raw
            self._values[id(instr.result)] = v
            return
        if isinstance(instr, SetElement):
            arr = self._value(instr.array)
            idx = self._value(instr.index)
            # Coerce element to i64: either it IS an i64, or it's a pointer
            # that needs ptrtoint (for String/Array/Struct elements).
            v = self._coerce_to_i64_value(instr.value)
            self._builder.call(self._fn_dx_array_set, [arr, idx, v])
            return
        if isinstance(instr, Call):
            args = [self._value(a) for a in instr.args]
            if instr.name in ("print", "println") and instr.args:
                # Dispatch on argument type.
                arg = instr.args[0]
                self._emit_print(arg, instr.name == "println")
                # No result.
                return
            if instr.name == "length":
                arg = instr.args[0]
                arg_ty = arg.type
                if isinstance(arg_ty, ArrayType):
                    call = self._builder.call(self._fn_dx_array_length, args, name=instr.result.name)
                else:
                    call = self._builder.call(self._fn_dx_string_length, args, name=instr.result.name)
                self._values[id(instr.result)] = call
                return
            # User function.
            callee = self.module.globals.get(instr.name)
            if callee is None:
                raise RuntimeError(f"undefined function `{instr.name}` in IR")
            if instr.result is None:
                self._builder.call(callee, args)
            else:
                call = self._builder.call(callee, args, name=instr.result.name)
                self._values[id(instr.result)] = call
            return
        raise RuntimeError(f"unhandled IR instruction: {type(instr).__name__}")

    def _emit_print(self, arg: Value, newline: bool) -> None:
        arg_val = self._value(arg)
        if isinstance(arg.type, PrimitiveType):
            name = arg.type.name
            if name == "Int":
                fn = self._fn_dx_println_int if newline else self._fn_dx_print_int
            elif name == "Float":
                fn = self._fn_dx_println_float if newline else self._fn_dx_print_float
            elif name == "Bool":
                # Bools are i1; zext to i64 first.
                arg_val = self._builder.zext(arg_val, ir.IntType(64), name="bool_zext")
                fn = self._fn_dx_println_bool if newline else self._fn_dx_print_bool
            elif name == "String":
                fn = self._fn_dx_println_string if newline else self._fn_dx_print_string
            else:
                raise RuntimeError(f"cannot print {name}")
            self._builder.call(fn, [arg_val])
            return
        if isinstance(arg.type, EnumType):
            # Enums are i64 tags.  Print as an Int.
            fn = self._fn_dx_println_int if newline else self._fn_dx_print_int
            self._builder.call(fn, [arg_val])
            return
        raise RuntimeError(f"cannot print type {arg.type}")

    def _lower_binop(self, op: str, l: ir.Value, r: ir.Value, result: Temp) -> ir.Value:
        if isinstance(l.type, ir.IntType) and l.type.width == 1:
            # Bool ops.
            if op == "&&":
                return self._builder.and_(l, r, name=result.name)
            if op == "||":
                return self._builder.or_(l, r, name=result.name)
            if op == "==":
                return self._builder.icmp_signed("==", l, r, name=result.name)
            if op == "!=":
                return self._builder.icmp_signed("!=", l, r, name=result.name)
            raise RuntimeError(f"unsupported bool op {op}")
        if isinstance(l.type, ir.IntType):
            # Int ops.
            if op == "+": return self._builder.add(l, r, name=result.name)
            if op == "-": return self._builder.sub(l, r, name=result.name)
            if op == "*": return self._builder.mul(l, r, name=result.name)
            if op == "/": return self._builder.sdiv(l, r, name=result.name)
            if op == "%": return self._builder.srem(l, r, name=result.name)
            if op == "==": return self._builder.icmp_signed("==", l, r, name=result.name)
            if op == "!=": return self._builder.icmp_signed("!=", l, r, name=result.name)
            if op == "<":  return self._builder.icmp_signed("<", l, r, name=result.name)
            if op == ">":  return self._builder.icmp_signed(">", l, r, name=result.name)
            if op == "<=": return self._builder.icmp_signed("<=", l, r, name=result.name)
            if op == ">=": return self._builder.icmp_signed(">=", l, r, name=result.name)
            raise RuntimeError(f"unsupported int op {op}")
        if isinstance(l.type, ir.DoubleType):
            if op == "+": return self._builder.fadd(l, r, name=result.name)
            if op == "-": return self._builder.fsub(l, r, name=result.name)
            if op == "*": return self._builder.fmul(l, r, name=result.name)
            if op == "/": return self._builder.fdiv(l, r, name=result.name)
            if op == "==": return self._builder.fcmp_ordered("==", l, r, name=result.name)
            if op == "!=": return self._builder.fcmp_ordered("!=", l, r, name=result.name)
            if op == "<":  return self._builder.fcmp_ordered("<", l, r, name=result.name)
            if op == ">":  return self._builder.fcmp_ordered(">", l, r, name=result.name)
            if op == "<=": return self._builder.fcmp_ordered("<=", l, r, name=result.name)
            if op == ">=": return self._builder.fcmp_ordered(">=", l, r, name=result.name)
            raise RuntimeError(f"unsupported float op {op}")
        # String concat / equality (both operands are %DxString*).
        if op == "+" and isinstance(l.type, ir.PointerType) and isinstance(r.type, ir.PointerType):
            # Confirm both point to DxString by structural match.  Since all
            # by-reference types are pointers, this only fires when both
            # operands are pointers — but only String supports `+`.
            # The type checker has already rejected struct/array `+`, so
            # we can rely on reaching here only with strings.
            return self._builder.call(self._fn_dx_string_concat, [l, r], name=result.name)
        if op == "==" and isinstance(l.type, ir.PointerType) and isinstance(r.type, ir.PointerType):
            call = self._builder.call(self._fn_dx_string_eq, [l, r], name=result.name + ".eq")
            return self._builder.trunc(call, ir.IntType(1), name=result.name)
        if op == "!=" and isinstance(l.type, ir.PointerType) and isinstance(r.type, ir.PointerType):
            call = self._builder.call(self._fn_dx_string_eq, [l, r], name=result.name + ".eq")
            eq_i1 = self._builder.trunc(call, ir.IntType(1), name=result.name + ".eq_i1")
            return self._builder.xor(eq_i1, ir.IntType(1)(1), name=result.name)
        raise RuntimeError(f"unsupported operand types {l.type}, {r.type} for `{op}`")

    def _coerce_to_i64_value(self, v: Value) -> ir.Value:
        return self._coerce_to_i64(v)

    def _coerce_to_i64(self, v: Value) -> ir.Value:
        """Coerce any Dextra value to an i64 slot for DxArray storage.

        The DxArray runtime stores every element as an `i64`.  Dextra values
        that fit naturally in an i64 (Int itself; Bool via zext; pointers
        via ptrtoint) are coerced directly.  Float (double) is exactly 64
        bits wide and 8-aligned, so we bitcast it losslessly to i64 — no
        information is lost.
        """

        val = self._value(v)
        if isinstance(val.type, ir.IntType) and val.type.width == 64:
            return val
        if isinstance(val.type, ir.IntType):
            return self._builder.zext(val, ir.IntType(64), name="coerce")
        if isinstance(val.type, ir.DoubleType):
            # bitcast double -> i64 is lossless (both 64 bits, both 8-aligned).
            return self._builder.bitcast(val, ir.IntType(64), name="coerce.f2i")
        if isinstance(val.type, ir.PointerType):
            return self._builder.ptrtoint(val, ir.IntType(64), name="coerce")
        raise RuntimeError(f"cannot coerce {val.type} to i64")

    def _value(self, v: Value) -> ir.Value:
        if isinstance(v, ConstInt):
            return ir.IntType(64)(v.value)
        if isinstance(v, ConstFloat):
            return ir.DoubleType()(v.value)
        if isinstance(v, ConstBool):
            return ir.IntType(1)(1 if v.value else 0)
        if isinstance(v, ConstString):
            # ConstString should not appear in IR — StringLit is used instead.
            raise RuntimeError("ConstString in IR; should have been StringLit")
        if isinstance(v, Param):
            return self._llvm_fn.args[v.index]
        if isinstance(v, Local):
            # Locals are alloca'd slots — returned via Store/Load.
            # If we ever see a Local here, it's a bug — Store/Load handle it.
            return self._alloca_slots[v.name]
        if isinstance(v, Temp):
            return self._values[id(v)]
        raise RuntimeError(f"unknown value kind: {type(v).__name__}")

    # -- terminators ----------------------------------------------------

    def _lower_terminator(self, bb: BasicBlock) -> None:
        t = bb.terminator
        if t is None:
            return
        if isinstance(t, Return):
            if t.value is None:
                self._builder.ret_void()
            else:
                self._builder.ret(self._value(t.value))
            return
        if isinstance(t, Branch):
            self._builder.branch(self._blocks[t.target.label])
            return
        if isinstance(t, CondBranch):
            cond = self._value(t.cond)
            # cond is i1 already.
            self._builder.cbranch(cond, self._blocks[t.then_target.label], self._blocks[t.else_target.label])
            return
        raise RuntimeError(f"unhandled terminator: {type(t).__name__}")


def compile_to_llvm_ir(mod: DxModule, ctx: TypeContext) -> ir.Module:
    """Public entry point."""

    backend = LLVMBackend(ctx)
    return backend.lower(mod)
