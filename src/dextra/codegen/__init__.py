"""Re-exports for the LLVM backend."""

from dextra.codegen.llvm_backend import LLVMBackend, compile_to_llvm_ir
from dextra.codegen.llvm_runtime import (
    emit_object_from_ir_text,
    ensure_llvm_initialized,
    is_llvm_initialized,
    llvm_environment_info,
    make_target_machine,
    parse_and_verify,
)
from dextra.codegen.native import (
    build_executable,
    compile_ir_to_object,
    compile_runtime,
    emit_llvm_ir_text,
    find_cc,
    link_executable,
)

__all__ = [
    "LLVMBackend",
    "compile_to_llvm_ir",
    "build_executable",
    "compile_ir_to_object",
    "compile_runtime",
    "emit_llvm_ir_text",
    "find_cc",
    "link_executable",
    # LLVM runtime:
    "emit_object_from_ir_text",
    "ensure_llvm_initialized",
    "is_llvm_initialized",
    "llvm_environment_info",
    "make_target_machine",
    "parse_and_verify",
]
