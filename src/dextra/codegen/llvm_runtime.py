"""Centralized LLVM runtime initialization.

This module owns LLVM native-target and native-assembly-printer
initialization for the entire Dextra compiler.  No other module should
call `llvmlite.binding.initialize*` directly.

## Which init calls does Dextra actually need?

The llvmlite `binding` module exposes three initialization functions:

  * `llvm.initialize()`                  — core init (LLVMCore).
  * `llvm.initialize_native_target()`    — register the host target.
  * `llvm.initialize_native_asmprinter()` — register the host asm printer.

Empirically (verified against llvmlite 0.44 / LLVM 15):

  * `llvm.initialize()` is **not needed**.  Modern llvmlite initializes
    the LLVM core lazily on first use (e.g. inside `parse_assembly`).
    Older llvmlite versions accepted the call; future versions may
    deprecate or remove it.  Dextra does NOT call it.

  * `llvm.initialize_native_target()` **is required**.  Without it,
    `Target.from_default_triple()` raises
    `RuntimeError: Unable to find target for this triple
    (no targets are registered)`.

  * `llvm.initialize_native_asmprinter()` **is required**.  Without it,
    `TargetMachine.emit_object()` raises
    `RuntimeError: TargetMachine can't emit a file of this type`.

Both `initialize_native_*` calls are idempotent — calling them twice
in the same process is safe and produces no error.

## Why centralize?

  * Calling `initialize_native_*` more than once is safe but wasteful.
    We guard with a module-level flag so the second call is a fast no-op.
  * Tests, the CLI, and the pipeline driver all need LLVM to be ready
    before invoking `parse_assembly` / `Target.from_default_triple` /
    `create_target_machine`.  Funneling through one entry point makes
    the contract obvious and is the single place to adapt if the
    llvmlite init contract changes again.

Public API:
  * `ensure_llvm_initialized()` — call before any `llvmlite.binding`
    operation that needs the native target / asm printer.
  * `parse_and_verify(llvm_ir_text)` — parse textual IR, run LLVM's
    verifier, and return a `ModuleRef` ready for codegen.
  * `make_target_machine()` — return a `TargetMachine` for the host
    triple, configured the way Dextra wants it.
  * `emit_object_from_ir_text(llvm_ir_text)` — end-to-end
    parse → verify → emit_object.
  * `llvm_environment_info()` — version string for diagnostics.
"""
from __future__ import annotations

import threading

from llvmlite import binding as llvm


_initialized = False
_lock = threading.Lock()


def ensure_llvm_initialized() -> None:
    """Initialize the LLVM native target + native asm printer.

    This is the SINGLE place Dextra touches LLVM init.  Idempotent:
    safe to call from multiple threads, safe to call repeatedly.
    After the first successful call, subsequent calls are a fast no-op.

    Does NOT call `llvm.initialize()` (the deprecated LLVM core init) —
    modern llvmlite handles core init lazily.  See module docstring for
    the rationale and the empirical evidence.
    """

    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        # Required: registers the host target so Target.from_default_triple()
        # can return a Target.  Without this we'd get
        # "Unable to find target for this triple (no targets are registered)".
        llvm.initialize_native_target()
        # Required: registers the host asm printer so TargetMachine.emit_object()
        # can produce a binary.  Without this we'd get
        # "TargetMachine can't emit a file of this type".
        llvm.initialize_native_asmprinter()
        _initialized = True


def parse_and_verify(llvm_ir_text: str):
    """Parse textual LLVM IR, run LLVM's verifier, return a ModuleRef.

    Raises if the IR is invalid or fails verification — Dextra treats
    LLVM verification as a hard correctness boundary.
    """

    ensure_llvm_initialized()
    mod = llvm.parse_assembly(llvm_ir_text)
    mod.verify()
    return mod


def make_target_machine(opt: int = 2, reloc: str = "pic",
                        codemodel: str = "default"):
    """Return a `TargetMachine` for the host triple."""

    ensure_llvm_initialized()
    target = llvm.Target.from_default_triple()
    return target.create_target_machine(opt=opt, reloc=reloc, codemodel=codemodel)


def emit_object_from_ir_text(llvm_ir_text: str) -> bytes:
    """Parse + verify + emit a native object file from textual LLVM IR.

    Convenience wrapper for callers (CLI, pipeline) that hold IR as a string.
    """

    parsed = parse_and_verify(llvm_ir_text)
    tm = make_target_machine()
    return tm.emit_object(parsed)


def is_llvm_initialized() -> bool:
    """For tests and diagnostics."""

    return _initialized


def llvm_environment_info() -> str:
    """Return a human-readable LLVM/llvmlite version string.

    Used by `dextra --version` and by diagnostic messages.
    """

    try:
        ver = llvm.llvm_version_info   # e.g. (15, 0, 7)
        llvm_ver = ".".join(str(x) for x in ver)
    except Exception:
        llvm_ver = "unknown"
    try:
        import llvmlite
        llvmlite_ver = getattr(llvmlite, "__version__", "unknown")
    except Exception:
        llvmlite_ver = "unknown"
    return f"llvmlite {llvmlite_ver}, LLVM {llvm_ver}"
