"""Module abstraction + auto-discovery.

A *module* is a self-contained feature slice (its own models, schemas,
repository, service, routes, events). Each module package exposes a single
`module = Module(...)` in its `__init__.py`. At startup the app scans
`app/modules/*`, imports each package (which imports its models & listeners as a
side effect), and registers every module's router automatically.

This is the "easier" win over hand-wiring every router: drop in a new module
folder (or run `python -m app.cli new-module <name>`) and it self-registers.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger("app.modules")

Hook = Callable[[], Awaitable[None]]


class ModuleError(RuntimeError):
    """Raised when module discovery or dependency validation fails."""


@dataclass
class Module:
    name: str
    router: APIRouter | None = None
    order: int = 100  # lower = registered/started earlier
    # Names of other modules this one relies on (see their public `__init__`).
    # Declaring them turns a cryptic ImportError into a clear startup message
    # and lets the architecture test verify boundaries. Direction is one-way:
    # feature modules -> supporting modules (e.g. auth) -> core. Never a cycle.
    depends_on: list[str] = field(default_factory=list)
    on_startup: Hook | None = None
    on_shutdown: Hook | None = None
    tags: list[str] = field(default_factory=list)


def discover_modules(package: str = "app.modules") -> list[Module]:
    """Import every sub-package of `package`, collect their `module` objects, and
    validate the dependency graph (existence + acyclicity)."""
    pkg = importlib.import_module(package)
    found: list[Module] = []
    for info in pkgutil.iter_modules(pkg.__path__):
        if not info.ispkg:
            continue
        try:
            mod = importlib.import_module(f"{package}.{info.name}")
        except Exception as exc:  # a missing/broken dependency surfaces here
            raise ModuleError(
                f"Failed to import module '{info.name}'. It likely depends on a "
                f"module that is missing or broken. Original error: {exc}"
            ) from exc
        candidate = getattr(mod, "module", None)
        if isinstance(candidate, Module):
            found.append(candidate)
        else:
            logger.warning("module_without_definition", package=info.name)

    _validate_dependencies(found)
    found.sort(key=lambda m: (m.order, m.name))
    logger.info("modules_discovered", modules=[m.name for m in found])
    return found


def _validate_dependencies(modules: list[Module]) -> None:
    names = {m.name for m in modules}
    for module in modules:
        for dep in module.depends_on:
            if dep not in names:
                raise ModuleError(
                    f"Module '{module.name}' declares a dependency on '{dep}', "
                    f"which is not registered. Add that module or remove the "
                    f"dependency from its `depends_on`."
                )
    _assert_acyclic(modules)


def _assert_acyclic(modules: list[Module]) -> None:
    graph = {m.name: list(m.depends_on) for m in modules}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(graph, WHITE)
    path: list[str] = []

    def visit(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for dep in graph.get(node, []):
            if color[dep] == GRAY:
                cycle = " -> ".join(path[path.index(dep):] + [dep])
                raise ModuleError(f"Circular module dependency detected: {cycle}")
            if color[dep] == WHITE:
                visit(dep)
        path.pop()
        color[node] = BLACK

    for node in graph:
        if color[node] == WHITE:
            visit(node)
