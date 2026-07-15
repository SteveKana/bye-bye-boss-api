"""Entry point: `python -m app.cli <command>`.

Commands:
    new-module <name> [--force]   scaffold a new feature module
    list-modules                  list discovered modules
    routes                        print the registered route table
"""

from __future__ import annotations

import argparse
import sys


def _count_endpoints(router) -> int:
    """Count real endpoints under a router, descending through FastAPI's lazy
    `_IncludedRouter` wrappers (0.100+ nested includes are not pre-flattened)."""
    total = 0
    for route in getattr(router, "routes", []):
        inner = getattr(route, "original_router", None)
        if inner is not None:
            total += _count_endpoints(inner)
        elif getattr(route, "methods", None):
            total += 1
    return total


def _cmd_new_module(args: argparse.Namespace) -> int:
    from app.cli.generator import generate_module

    try:
        target = generate_module(args.name, force=args.force)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"✓ created module at {target}")
    print("\nNext steps:")
    print(f"  1. alembic revision --autogenerate -m 'add {target.name}'")
    print("  2. alembic upgrade head")
    print("  3. start the app — the module is already auto-registered.")
    return 0


def _cmd_list_modules(_: argparse.Namespace) -> int:
    from app.main import MODULES

    for module in MODULES:
        count = _count_endpoints(module.router) if module.router else 0
        print(f"  {module.name:<16} order={module.order:<4} endpoints={count}")
    return 0


def _cmd_routes(_: argparse.Namespace) -> int:
    from app.main import app

    paths: dict = app.openapi().get("paths", {})
    rows = [
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
    ]
    for method, path in sorted(rows, key=lambda r: (r[1], r[0])):
        print(f"  {method:<8} {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new-module", help="scaffold a new feature module")
    p_new.add_argument("name", help="singular resource name, e.g. 'product'")
    p_new.add_argument("--force", action="store_true", help="overwrite if exists")
    p_new.set_defaults(func=_cmd_new_module)

    sub.add_parser("list-modules", help="list discovered modules").set_defaults(
        func=_cmd_list_modules
    )
    sub.add_parser("routes", help="print the route table").set_defaults(
        func=_cmd_routes
    )

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
