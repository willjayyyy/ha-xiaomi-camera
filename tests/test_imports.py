"""Every name the bridge imports from itself must exist.

The test suite deliberately avoids the closed-source vendor SDK, so most of
this package is never imported during a test run and a name that stopped
existing goes unnoticed. That has happened: a helper was removed along with the
code around it while ``bridge.__main__`` still imported it, which every test
passed through and which would have failed at startup, in a container, as an
ImportError with no other explanation.

Reading the imports statically rather than importing ``bridge.__main__``
directly is what makes this possible at all -- importing it would pull in the
SDK. It catches the case that actually occurs: a module referring to something
its own package no longer provides.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

_BRIDGE = Path(__file__).resolve().parent.parent / "addon" / "rootfs" / "app" / "bridge"

#: Modules that reach the vendor SDK, directly or through an import of their
#: own. They can only be read, not imported.
_NEEDS_SDK = {"__main__", "account", "cameras", "streaming", "api", "restream"}

_IMPORTABLE = sorted(
    path.stem
    for path in _BRIDGE.glob("*.py")
    if path.stem not in _NEEDS_SDK and not path.stem.startswith("__")
)

_ALL_MODULES = sorted(path.stem for path in _BRIDGE.glob("*.py"))


@pytest.mark.parametrize("name", _IMPORTABLE)
def test_module_imports(name: str) -> None:
    importlib.import_module(f"bridge.{name}")


@pytest.mark.parametrize("name", _ALL_MODULES)
def test_names_imported_from_within_the_package_exist(name: str) -> None:
    """Resolve each `from .sibling import name` against the sibling's source.

    Compared against what the sibling defines at module level rather than by
    importing it, so this works for the modules that need the SDK too -- which
    are exactly the ones no other test covers.
    """
    tree = ast.parse((_BRIDGE / f"{name}.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 1 or not node.module:
            continue
        source = _BRIDGE / f"{node.module}.py"
        if not source.exists():
            pytest.fail(
                f"bridge/{name}.py imports from missing bridge/{node.module}.py"
            )
        defined = _module_level_names(source)
        for alias in node.names:
            assert alias.name in defined, (
                f"bridge/{name}.py imports {alias.name!r} from "
                f"bridge/{node.module}.py, which does not define it"
            )


def _module_level_names(path: Path) -> set[str]:
    """Names a module binds at its top level, however they are bound."""
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
    return names
