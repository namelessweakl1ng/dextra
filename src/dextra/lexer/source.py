"""Source location primitives shared by lexer, parser, and diagnostics.

A `SourceFile` holds the raw text and a list of line start offsets so that
any byte offset can be cheaply converted into a (line, column) pair.

A `SourceSpan` is the unit of source location carried by every token and
AST node.  Spans are intentionally immutable and hashable so they can be
used as dictionary keys.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourcePosition:
    """A 1-indexed (line, column) position in a source file."""

    filename: str
    line: int
    column: int
    offset: int

    def __str__(self) -> str:
        return f"{self.filename}:{self.line}:{self.column}"


@dataclass(frozen=True)
class SourceSpan:
    """A half-open span [start, end) in a source file."""

    start: SourcePosition
    end: SourcePosition

    @property
    def filename(self) -> str:
        return self.start.filename

    @property
    def line(self) -> int:
        return self.start.line

    @property
    def column(self) -> int:
        return self.start.column

    def merge(self, other: "SourceSpan") -> "SourceSpan":
        """Return the smallest span that contains both `self` and `other`."""

        return SourceSpan(
            start=min(self.start, other.start, key=lambda p: p.offset),
            end=max(self.end, other.end, key=lambda p: p.offset),
        )

    @classmethod
    def synthetic(cls, filename: str = "<synthetic>") -> "SourceSpan":
        """Return a placeholder span for compiler-generated nodes."""

        pos = SourcePosition(filename, 0, 0, 0)
        return cls(start=pos, end=pos)

    def __str__(self) -> str:
        return f"{self.start.filename}:{self.start.line}:{self.start.column}"


class SourceFile:
    """A source file with cheap line lookups."""

    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self.text = text
        # Precompute the byte offset of the start of each line.
        # Line 1 starts at offset 0.
        self._line_starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                self._line_starts.append(i + 1)

    def position_at(self, offset: int) -> SourcePosition:
        """Convert a byte offset to a (line, column) position."""

        # Binary search for the largest line-start <= offset.
        lo, hi = 0, len(self._line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        line_no = lo + 1
        column = offset - self._line_starts[lo] + 1
        return SourcePosition(self.name, line_no, column, offset)

    def line_text(self, line_no: int) -> str:
        """Return the text of line `line_no` (1-indexed), without the trailing newline."""

        if line_no < 1 or line_no > len(self._line_starts):
            return ""
        start = self._line_starts[line_no - 1]
        end = self._line_starts[line_no] if line_no < len(self._line_starts) else len(self.text)
        return self.text[start:end].rstrip("\n").rstrip("\r")
