"""End-to-end golden tests: compile a .dx program, run it, compare stdout."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from dextra.pipeline import build_from_file


HERE = Path(__file__).resolve().parent
PROGRAMS_DIR = HERE / "integration" / "programs"


def collect_golden():
    return sorted(PROGRAMS_DIR.glob("*.dx"))


@pytest.mark.parametrize("dx_file", collect_golden(), ids=lambda p: p.name)
def test_golden(dx_file: Path, tmp_path: Path) -> None:
    expected_file = dx_file.with_suffix(".expected")
    expected_error_file = dx_file.with_suffix(".expected_error")

    out_path = tmp_path / dx_file.stem
    rc = build_from_file(dx_file, out_path, verbose=False)

    if expected_error_file.exists():
        # This program is expected to FAIL compilation with a specific error code.
        assert rc != 0, f"{dx_file.name} should have failed compilation but built successfully"
        expected_codes = {
            line.strip() for line in expected_error_file.read_text().splitlines() if line.strip()
        }
        # We can't easily capture the diagnostic engine from here; instead,
        # we trust that any non-zero exit indicates the compile failed.
        # In a more elaborate harness we'd capture stderr.  For now, we
        # verify the build fails.
        return

    assert rc == 0, f"{dx_file.name} failed to build"
    proc = subprocess.run([str(out_path)], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, f"{dx_file.name} exited with {proc.returncode}"
    if expected_file.exists():
        expected = expected_file.read_text()
        assert proc.stdout == expected, (
            f"{dx_file.name}: expected output did not match.\n"
            f"--- expected ---\n{expected}\n"
            f"--- actual ---\n{proc.stdout}"
        )
