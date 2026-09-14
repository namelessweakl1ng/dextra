"""IR lowering tests — no LLVM involvement."""
from dextra.diagnostics import DiagnosticEngine
from dextra.ir import (
    BinOp,
    BasicBlock,
    Branch,
    Call,
    CondBranch,
    ConstInt,
    Function,
    Load,
    Local,
    Module,
    Param,
    Return,
    Store,
    lower_program,
)
from dextra.lexer import Lexer, SourceFile
from dextra.parser import Parser
from dextra.semantic import Analyzer


def lower(src: str) -> Module:
    diag = DiagnosticEngine()
    sf = SourceFile("test.dx", src)
    toks = Lexer(sf, diag).tokenize()
    prog = Parser(toks, diag).parse_program()
    a = Analyzer(diag)
    ap = a.analyze(prog)
    return lower_program(ap)


def test_simple_main():
    mod = lower("fn main() -> Int { return 42 }")
    assert len(mod.functions) == 1
    fn = mod.functions[0]
    assert fn.name == "main"
    assert len(fn.blocks) >= 1
    assert fn.entry.label == "entry"
    assert isinstance(fn.entry.terminator, Return)


def test_if_creates_branches():
    mod = lower("""
fn main() -> Int {
    if true { return 1 }
    return 0
}
""")
    fn = mod.functions[0]
    # Should have entry, then, endif at minimum.
    labels = [b.label for b in fn.blocks]
    assert "entry" in labels
    assert any(l.startswith("then") for l in labels)


def test_while_creates_loop_blocks():
    mod = lower("""
fn main() -> Int {
    let mut i = 0
    while i < 10 {
        i = i + 1
    }
    return 0
}
""")
    fn = mod.functions[0]
    labels = [b.label for b in fn.blocks]
    assert any("while_cond" in l for l in labels)
    assert any("while_body" in l for l in labels)
    assert any("while_end" in l for l in labels)


def test_for_range_uses_cond_branch():
    mod = lower("""
fn main() -> Int {
    for i in 0..10 { println(i) }
    return 0
}
""")
    fn = mod.functions[0]
    # Find the cond block and check its terminator is CondBranch.
    cond_blocks = [b for b in fn.blocks if "for_cond" in b.label]
    assert cond_blocks
    assert isinstance(cond_blocks[0].terminator, CondBranch)


def test_parameters_become_allocas():
    mod = lower("fn add(a: Int, b: Int) -> Int { return a + b }")
    fn = mod.functions[0]
    assert len(fn.params) == 2
    # Entry block should contain 2 allocas (one per param).
    allocas = [i for i in fn.entry.instructions if hasattr(i, "name")]
    assert len(allocas) >= 2


def test_call_instruction_emitted():
    mod = lower("""
fn helper(x: Int) -> Int { return x }
fn main() -> Int { return helper(42) }
""")
    main = next(f for f in mod.functions if f.name == "main")
    # Walk all blocks and find a Call instruction.
    found = False
    for bb in main.blocks:
        for ins in bb.instructions:
            if isinstance(ins, Call) and ins.name == "helper":
                found = True
                break
    assert found


def test_string_concat_lowered_to_runtime_call():
    mod = lower("""
fn main() -> Int {
    let s = "a" + "b"
    return 0
}
""")
    main = mod.functions[0]
    # The string concat is a BinOp with op="+", lowered as a BinOp in IR;
    # the LLVM backend dispatches it to dx_string_concat.  We just verify
    # the IR has the expected BinOp.
    found_concat = False
    for bb in main.blocks:
        for ins in bb.instructions:
            if isinstance(ins, BinOp) and ins.op == "+":
                found_concat = True
    assert found_concat


def test_all_blocks_have_terminators():
    mod = lower("""
fn main() -> Int {
    let mut i = 0
    while i < 5 {
        if i == 3 { break }
        i = i + 1
    }
    return i
}
""")
    for fn in mod.functions:
        for bb in fn.blocks:
            assert bb.terminator is not None, f"block {bb.label} has no terminator"
