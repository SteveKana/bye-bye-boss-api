"""Architecture guardrails for the modular monolith.

These tests keep module boundaries real instead of aspirational:

1. A module may import another module's **public root** (`app.modules.X`) but
   never its internals (`app.modules.X.models`, `.service`, ...).
2. Every cross-module dependency must be declared in the module's `depends_on`.
3. The module dependency graph must be acyclic (also enforced at startup).
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.core.module import discover_modules

MODULES_DIR = Path(__file__).resolve().parent.parent / "app" / "modules"


def _module_files() -> list[Path]:
    # Only files that live *inside* a module package (skip app/modules/__init__.py).
    return [
        p
        for p in MODULES_DIR.rglob("*.py")
        if len(p.relative_to(MODULES_DIR).parts) >= 2
    ]


def _owning_module(path: Path) -> str:
    return path.relative_to(MODULES_DIR).parts[0]


def _scan(path: Path) -> tuple[list[str], set[str]]:
    """Return (internal-import violations, cross-module root deps) for a file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    current = _owning_module(path)
    violations: list[str] = []
    deps: set[str] = set()

    def handle(dotted: str, lineno: int) -> None:
        parts = dotted.split(".")
        if len(parts) < 3 or parts[0] != "app" or parts[1] != "modules":
            return
        target = parts[2]
        if target == current:
            return  # importing your own submodules is fine
        if len(parts) > 3:
            violations.append(
                f"{path.relative_to(MODULES_DIR)}:{lineno} imports internals "
                f"'{dotted}' — import from 'app.modules.{target}' instead"
            )
        else:
            deps.add(target)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level or node.module is None:
                continue
            if node.module == "app.modules":  # `from app.modules import X`
                for alias in node.names:
                    if alias.name != current:
                        deps.add(alias.name)
            else:
                handle(node.module, node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                handle(alias.name, node.lineno)

    return violations, deps


def test_no_cross_module_internal_imports() -> None:
    violations: list[str] = []
    for path in _module_files():
        file_violations, _ = _scan(path)
        violations.extend(file_violations)
    message = "Cross-module imports must target the package root:\n" + "\n".join(
        violations
    )
    assert not violations, message


def test_cross_module_deps_are_declared() -> None:
    declared = {m.name: set(m.depends_on) for m in discover_modules()}
    actual: dict[str, set[str]] = {name: set() for name in declared}
    for path in _module_files():
        owner = _owning_module(path)
        if owner not in declared:
            continue
        _, deps = _scan(path)
        actual[owner] |= deps

    problems = []
    for name, deps in actual.items():
        undeclared = deps - declared[name]
        if undeclared:
            problems.append(
                f"module '{name}' imports {sorted(undeclared)} but does not list "
                f"them in `depends_on` (declared: {sorted(declared[name])})"
            )
    assert not problems, "\n".join(problems)


def test_module_graph_is_acyclic() -> None:
    # discover_modules() raises ModuleError on cycles or missing dependencies.
    discover_modules()
