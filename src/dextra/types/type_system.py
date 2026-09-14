"""Dextra type system.

Types are interned through a `TypeContext` so that structural equality
becomes identity equality (`is`).  `Unknown` is a sentinel used during
inference; it must never survive into the typed AST.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    pass


class Type:
    """Base class for all Dextra types."""

    name: str

    def __str__(self) -> str:  # pragma: no cover - overridden everywhere
        return self.name


@dataclass(frozen=True)
class PrimitiveType(Type):
    name: str

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class ArrayType(Type):
    element_type: Type

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"[{self.element_type}]"

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class StructType(Type):
    struct_name: str
    fields: tuple[tuple[str, Type], ...]   # tuple so it's hashable

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.struct_name

    def __str__(self) -> str:
        return self.struct_name

    def field_type(self, name: str) -> Optional[Type]:
        for fn, ft in self.fields:
            if fn == name:
                return ft
        return None

    def field_index(self, name: str) -> Optional[int]:
        for i, (fn, _) in enumerate(self.fields):
            if fn == name:
                return i
        return None


@dataclass(frozen=True)
class FunctionType(Type):
    param_types: tuple[Type, ...]
    return_type: Type

    @property
    def name(self) -> str:  # type: ignore[override]
        params = ", ".join(str(p) for p in self.param_types)
        return f"fn({params}) -> {self.return_type}"

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class EnumType(Type):
    """A user-defined enum type.

    v0.1 enums are unit-variant only.  Each variant has a stable tag
    (its index in `variant_names`).  Two `EnumType`s are equal iff their
    `enum_name` matches — variants are looked up by name.
    """

    enum_name: str
    variant_names: tuple[str, ...]

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.enum_name

    def __str__(self) -> str:
        return self.enum_name

    def variant_tag(self, variant_name: str) -> Optional[int]:
        """Return the 0-based tag of `variant_name`, or None if not a variant."""

        for i, v in enumerate(self.variant_names):
            if v == variant_name:
                return i
        return None

    def has_variant(self, variant_name: str) -> bool:
        return variant_name in self.variant_names


class UnknownType(Type):
    """Sentinel used during inference — never appears in the typed AST."""

    name = "<unknown>"


UNKNOWN = UnknownType()


class TypeContext:
    """Interns the primitive and array types so equality is identity."""

    def __init__(self) -> None:
        self._cache: dict[str, Type] = {}
        # Pre-populate primitives.
        for name in ("Int", "Float", "Bool", "String", "Void"):
            t = PrimitiveType(name)
            self._cache[name] = t
        self._structs: dict[str, StructType] = {}
        self._enums: dict[str, EnumType] = {}

    def primitive(self, name: str) -> Type:
        return self._cache[name]

    @property
    def INT(self) -> Type:
        return self._cache["Int"]

    @property
    def FLOAT(self) -> Type:
        return self._cache["Float"]

    @property
    def BOOL(self) -> Type:
        return self._cache["Bool"]

    @property
    def STRING(self) -> Type:
        return self._cache["String"]

    @property
    def VOID(self) -> Type:
        return self._cache["Void"]

    def array(self, element: Type) -> ArrayType:
        # Intern by element identity.
        cached = self._cache.get(f"array:{id(element)}")
        if cached is not None:
            return cached  # type: ignore[return-value]
        t = ArrayType(element_type=element)
        self._cache[f"array:{id(element)}"] = t
        return t

    def register_struct(self, struct_type: StructType) -> None:
        self._structs[struct_type.struct_name] = struct_type
        self._cache[struct_type.struct_name] = struct_type

    def lookup_struct(self, name: str) -> Optional[StructType]:
        return self._structs.get(name)

    def register_enum(self, enum_type: EnumType) -> None:
        self._enums: dict[str, EnumType] = getattr(self, "_enums", {})
        self._enums[enum_type.enum_name] = enum_type
        self._cache[enum_type.enum_name] = enum_type

    def lookup_enum(self, name: str) -> Optional[EnumType]:
        return getattr(self, "_enums", {}).get(name)

    def lookup_named(self, name: str) -> Optional[Type]:
        return self._cache.get(name)
