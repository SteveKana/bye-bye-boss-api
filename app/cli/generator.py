"""Scaffold a new feature module from templates.

`new-module <singular>` renders `app/cli/templates/module/**` into
`app/modules/<plural>/`, wired and ready to auto-register. Example:

    python -m app.cli new-module product
    # -> app/modules/products/ with Product model/schemas/repo/service/routes
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE_ROOT = Path(__file__).parent / "templates" / "module"
MODULES_ROOT = Path(__file__).resolve().parents[1] / "modules"


def pascal_case(name: str) -> str:
    return "".join(part.capitalize() for part in name.replace("-", "_").split("_"))


def pluralize(word: str) -> str:
    if word.endswith("y") and (len(word) < 2 or word[-2] not in "aeiou"):
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


def _tokens(singular: str) -> dict[str, str]:
    singular = singular.strip().lower().replace("-", "_")
    plural = pluralize(singular)
    return {
        "{{CLASS}}": pascal_case(singular),
        "{{SINGULAR}}": singular,
        "{{PLURAL}}": plural,
        "{{VAR}}": singular,
    }


def _render(text: str, tokens: dict[str, str]) -> str:
    for key, value in tokens.items():
        text = text.replace(key, value)
    return text


def generate_module(singular: str, *, force: bool = False) -> Path:
    tokens = _tokens(singular)
    plural = tokens["{{PLURAL}}"]
    target = MODULES_ROOT / plural

    if target.exists() and not force:
        raise FileExistsError(
            f"Module '{plural}' already exists at {target}. Use --force to overwrite."
        )

    created: list[Path] = []
    for template in sorted(TEMPLATE_ROOT.rglob("*.tmpl")):
        rel = template.relative_to(TEMPLATE_ROOT)
        # Strip `.tmpl` and substitute the `resource` filename placeholder.
        out_rel = Path(str(rel)[: -len(".tmpl")].replace("resource", plural))
        out_path = target / out_rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_render(template.read_text(), tokens))
        created.append(out_path)

    return target
