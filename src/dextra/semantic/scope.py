"""Lexical scope chain."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from dextra.semantic.symbols import Symbol


@dataclass
class Scope:
    parent: Optional["Scope"]
    symbols: dict[str, Symbol] = field(default_factory=dict)

    def declare(self, sym: Symbol) -> bool:
        if sym.name in self.symbols:
            return False
        self.symbols[sym.name] = sym
        return True

    def lookup(self, name: str) -> Optional[Symbol]:
        scope: Optional[Scope] = self
        while scope is not None:
            if name in scope.symbols:
                return scope.symbols[name]
            scope = scope.parent
        return None

    def lookup_local(self, name: str) -> Optional[Symbol]:
        return self.symbols.get(name)
