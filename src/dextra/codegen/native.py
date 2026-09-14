"""Native code emission and linking.

Compiles an `llvmlite.ir.Module` to a native object file via the centralized
LLVM runtime helpers, then invokes `gcc` (or `clang`/`cc`) to link the
object with the Dextra runtime.

LLVM target/asm-printer initialization is owned by
`dextra.codegen.llvm_runtime` — this module does NOT call
`llvm.initialize*` directly.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from shutil import which

from llvmlite import ir as llvmir

from dextra.codegen.llvm_runtime import (
    make_target_machine,
    parse_and_verify,
)
from dextra.runtime import RUNTIME_C_PATH


def compile_ir_to_object(llvm_mod: llvmir.Module, out_path: Path) -> None:
    """Compile an `llvmlite.ir.Module` to a native object file (.o).

    Pipeline: ir.Module → text → parse_and_verify (LLVM verifier) → object.
    LLVM verification is a hard correctness boundary — invalid IR raises here.
    """

    parsed = parse_and_verify(str(llvm_mod))
    tm = make_target_machine()
    obj_bytes = tm.emit_object(parsed)
    out_path.write_bytes(obj_bytes)


def compile_runtime(out_path: Path, cc: str = "gcc") -> None:
    """Compile runtime.c to an object file."""

    cmd = [cc, "-c", "-O2", "-fPIC",
           str(RUNTIME_C_PATH), "-o", str(out_path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"runtime compilation failed (cc={cc}):\n{res.stderr}"
        )


def link_executable(obj_paths: list[Path], out_path: Path, cc: str = "gcc") -> None:
    """Link one or more .o files into an executable."""

    cmd = [cc] + [str(p) for p in obj_paths] + ["-o", str(out_path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"linking failed (cc={cc}):\n{res.stderr}"
        )


def build_executable(llvm_mod: llvmir.Module, out_path: Path, cc: str = "gcc",
                     verbose: bool = False) -> None:
    """Full pipeline: IR → object → linked executable (self-contained)."""

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        prog_obj = tmpdir / "prog.o"
        runtime_obj = tmpdir / "runtime.o"
        if verbose:
            print(f"[codegen] compiling LLVM IR → {prog_obj}")
        compile_ir_to_object(llvm_mod, prog_obj)
        if verbose:
            print(f"[codegen] compiling runtime → {runtime_obj}")
        compile_runtime(runtime_obj, cc=cc)
        if verbose:
            print(f"[codegen] linking → {out_path}")
        link_executable([prog_obj, runtime_obj], out_path, cc=cc)


def emit_llvm_ir_text(llvm_mod: llvmir.Module, out_path: Path) -> None:
    """Write the LLVM IR text to a file (used by `dextra emit-ir`)."""

    out_path.write_text(str(llvm_mod))


def find_cc() -> str:
    """Locate a C compiler on PATH.  Preference: gcc, cc, clang."""

    for name in ("gcc", "cc", "clang"):
        path = which(name)
        if path is not None:
            return path
    raise RuntimeError("no C compiler found — install gcc or clang")

