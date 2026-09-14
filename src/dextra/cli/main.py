"""Dextra CLI — `dextra build`, `run`, `check`, `emit-ir`, `fmt`, `new`, etc.

Exit codes:
    0 — success
    1 — user / compilation error (missing file, parse error, type error, …)
    2 — infrastructure / compiler-internal failure (LLVM verification,
        linker failure, missing C compiler)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

from dextra import __version__
from dextra.codegen import find_cc, parse_and_verify, emit_object_from_ir_text, llvm_environment_info
from dextra.codegen.native import compile_runtime, link_executable
from dextra.diagnostics import DiagnosticEngine
from dextra.formatter import Formatter
from dextra.lexer import Lexer, SourceFile
from dextra.parser import Parser
from dextra.pipeline import compile_source
from dextra.semantic import Analyzer


def _llvm_env_line() -> str:
    """A single line summarizing the LLVM/llvmlite environment for --version."""

    try:
        import sys
        py_ver = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        return f"{py_ver}\n{llvm_environment_info()}"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Helpers


def _read_source(file_arg: str) -> tuple[Optional[Path], Optional[str], Optional[SourceFile]]:
    """Read a .dx source file, printing a friendly error if it's missing.

    Returns (path, source_text, source_file) — any may be None on failure.
    """

    path = Path(file_arg)
    try:
        source = path.read_text()
    except FileNotFoundError:
        print(f"error: file not found: {file_arg}", file=sys.stderr)
        return None, None, None
    except OSError as exc:
        print(f"error: cannot read {file_arg}: {exc}", file=sys.stderr)
        return None, None, None
    sf = SourceFile(str(path), source)
    return path, source, sf


def _print_diagnostics(diag: DiagnosticEngine, source_files: dict[str, SourceFile]) -> None:
    rendered = diag.render_all(source_files)
    if rendered:
        print(rendered)


def _fail(msg: str, code: int = 1) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return code


# ---------------------------------------------------------------------------
# Subcommands


def cmd_lex(args: argparse.Namespace) -> int:
    path, source, sf = _read_source(args.file)
    if path is None:
        return 1
    diag = DiagnosticEngine()
    toks = Lexer(sf, diag).tokenize()
    for t in toks:
        kind = t.kind.name
        if t.value is not None:
            print(f"{kind}({t.value!r})  @ {t.span}")
        else:
            print(f"{kind}  @ {t.span}")
    _print_diagnostics(diag, {args.file: sf})
    return 1 if diag.has_errors else 0


def cmd_ast(args: argparse.Namespace) -> int:
    path, source, sf = _read_source(args.file)
    if path is None:
        return 1
    diag = DiagnosticEngine()
    toks = Lexer(sf, diag).tokenize()
    prog = Parser(toks, diag).parse_program()
    _print_ast(prog, indent=0)
    _print_diagnostics(diag, {args.file: sf})
    return 1 if diag.has_errors else 0


def _print_ast(node, indent: int) -> None:
    pad = "  " * indent
    if node is None:
        print(f"{pad}None")
        return
    if isinstance(node, list):
        for x in node:
            _print_ast(x, indent)
        return
    cls = type(node).__name__
    attrs = {k: v for k, v in vars(node).items() if k != "span"}
    print(f"{pad}{cls}")
    for k, v in attrs.items():
        if hasattr(v, "__dict__") or isinstance(v, list):
            print(f"{pad}  {k}:")
            _print_ast(v, indent + 2)
        else:
            print(f"{pad}  {k}: {v!r}")


def cmd_check(args: argparse.Namespace) -> int:
    path, source, sf = _read_source(args.file)
    if path is None:
        return 1
    diag = DiagnosticEngine()
    toks = Lexer(sf, diag).tokenize()
    prog = Parser(toks, diag).parse_program()
    Analyzer(diag).analyze(prog)
    _print_diagnostics(diag, {args.file: sf})
    return 1 if diag.has_errors else 0


def cmd_emit_ir(args: argparse.Namespace) -> int:
    path, source, sf = _read_source(args.file)
    if path is None:
        return 1
    diag = DiagnosticEngine()
    toks = Lexer(sf, diag).tokenize()
    prog = Parser(toks, diag).parse_program()
    analyzed = Analyzer(diag).analyze(prog)
    if diag.has_errors:
        _print_diagnostics(diag, {args.file: sf})
        return 1
    from dextra.ir import lower_program
    from dextra.codegen import compile_to_llvm_ir
    mod = lower_program(analyzed)
    llvm_mod = compile_to_llvm_ir(mod, analyzed.ctx)
    ir_text = str(llvm_mod)
    # Verify before writing — fail loudly on broken IR.
    try:
        parse_and_verify(ir_text)
    except Exception as exc:
        return _fail(f"LLVM IR verification failed: {exc}", code=2)
    out_path = Path(args.output) if args.output else Path(args.file).with_suffix(".ll")
    out_path.write_text(ir_text)
    print(f"wrote LLVM IR to {out_path}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    src_path = Path(args.file)
    output = getattr(args, "output", None)
    out_path = Path(output) if output else src_path.with_suffix("")
    try:
        source = src_path.read_text()
    except FileNotFoundError:
        return _fail(f"file not found: {args.file}")
    except OSError as exc:
        return _fail(f"cannot read {args.file}: {exc}")
    sf = SourceFile(str(src_path), source)
    result = compile_source(source, filename=str(src_path), verbose=args.verbose)
    if not result.success or result.llvm_ir_text is None:
        _print_diagnostics(result.diagnostics, {str(src_path): sf})
        # Exit code: 1 for user errors (parse/type/semantic), 2 for
        # compiler-internal bugs (LLVM backend crashed after semantic
        # analysis accepted the program).
        return 2 if result.internal_error else 1
    # LLVM verification — hard correctness boundary.
    try:
        parse_and_verify(result.llvm_ir_text)
    except Exception as exc:
        bad_path = out_path.with_suffix(".bad.ll")
        bad_path.write_text(result.llvm_ir_text)
        print(f"error: LLVM IR verification failed: {exc}", file=sys.stderr)
        print(f"  (IR dumped to {bad_path})", file=sys.stderr)
        return 2
    if args.verbose:
        print("[codegen] compiling LLVM IR to object code")
    try:
        obj_bytes = emit_object_from_ir_text(result.llvm_ir_text)
    except Exception as exc:
        return _fail(f"object emission failed: {exc}", code=2)
    obj_path = out_path.with_suffix(".o")
    obj_path.write_bytes(obj_bytes)
    runtime_obj = out_path.with_suffix(".rt.o")
    try:
        compile_runtime(runtime_obj, cc=find_cc())
    except Exception as exc:
        return _fail(f"runtime compilation failed: {exc}", code=2)
    try:
        link_executable([obj_path, runtime_obj], out_path, cc=find_cc())
    except Exception as exc:
        return _fail(f"linking failed: {exc}", code=2)
    # Cleanup intermediates.
    for p in (obj_path, runtime_obj):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    print(f"built {out_path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    src_path = Path(args.file)
    out_path = src_path.with_suffix("")
    # Build with the same args object (it has `file` and `verbose`).
    rc = cmd_build(args)
    if rc != 0:
        return rc
    proc = subprocess.run([str(out_path)])
    return proc.returncode


def cmd_fmt(args: argparse.Namespace) -> int:
    path, source, sf = _read_source(args.file)
    if path is None:
        return 1
    diag = DiagnosticEngine()
    toks = Lexer(sf, diag).tokenize()
    prog = Parser(toks, diag).parse_program()
    if diag.has_errors:
        _print_diagnostics(diag, {args.file: sf})
        return 1
    fmt = Formatter()
    out = fmt.format_program(prog)
    if args.in_place:
        path.write_text(out)
        print(f"formatted {path}")
    else:
        print(out, end="")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    name = args.name
    project_dir = Path(name)
    if project_dir.exists():
        return _fail(f"directory `{name}` already exists")
    (project_dir / "src").mkdir(parents=True)
    (project_dir / "dextra.toml").write_text(_PROJECT_TOML.format(name=name))
    main_dx = project_dir / "src" / "main.dx"
    main_dx.write_text(_MAIN_DX)
    print(f"created {project_dir}/")
    print(f"  cd {name} && dextra run")
    return 0


_PROJECT_TOML = '''name = "{name}"
version = "0.1.0"
entry = "src/main.dx"
'''

_MAIN_DX = '''fn main() -> Int {
    println("Hello, Dextra!")
    return 0
}
'''


def cmd_clean(args: argparse.Namespace) -> int:
    """Remove generated artifacts from the project directory."""

    p = Path(".")
    count = 0
    for pattern in ("*.o", "*.ll", "*.out"):
        for f in p.glob(pattern):
            f.unlink()
            count += 1
    print(f"removed {count} artifacts")
    return 0


class _VersionAction(argparse.Action):
    """A custom --version action that prints multiple lines.

    argparse's built-in 'version' action collapses newlines to spaces
    via `_format_message` — we want the LLVM/llvmlite versions on
    separate lines, so we use this custom action.
    """

    def __init__(self, option_strings, dest, **kwargs):
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("help", "show version and exit")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        lines = [
            f"Dextra {__version__}",
            _llvm_env_line(),
        ]
        parser._print_message("\n".join(lines) + "\n", file=None)
        parser.exit(0)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dextra", description="Dextra compiler")
    p.add_argument("--version", action=_VersionAction)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("lex", help="print tokens for a .dx file")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_lex)

    sp = sub.add_parser("ast", help="print the AST for a .dx file")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_ast)

    sp = sub.add_parser("check", help="run lexer + parser + semantic + typecheck")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("emit-ir", help="emit LLVM IR text")
    sp.add_argument("file")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_emit_ir)

    sp = sub.add_parser("build", help="compile to a native executable")
    sp.add_argument("file")
    sp.add_argument("-o", "--output")
    sp.add_argument("--verbose", "-v", action="store_true")
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("run", help="build and run")
    sp.add_argument("file")
    sp.add_argument("--verbose", "-v", action="store_true")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("fmt", help="format source code")
    sp.add_argument("file")
    sp.add_argument("--in-place", "-i", action="store_true")
    sp.set_defaults(func=cmd_fmt)

    sp = sub.add_parser("new", help="scaffold a new Dextra project")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_new)

    sp = sub.add_parser("clean", help="remove generated artifacts")
    sp.set_defaults(func=cmd_clean)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
