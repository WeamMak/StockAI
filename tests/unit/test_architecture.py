"""Guard the dependency boundaries defined by the approved project plan."""

from __future__ import annotations

import ast
import unittest
from collections.abc import Iterator
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "procurement"

REQUIRED_PACKAGES = (
    Path("."),
    Path("adapters"),
    Path("adapters/aws"),
    Path("adapters/odoo"),
    Path("agent"),
    Path("api"),
    Path("bootstrap"),
    Path("domain"),
    Path("mcp_server"),
    Path("observability"),
    Path("ports"),
)

FORBIDDEN_IMPORTS_BY_PACKAGE = {
    "domain": (
        "procurement.adapters",
        "procurement.agent",
        "procurement.api",
        "procurement.bootstrap",
        "procurement.mcp_server",
        "procurement.observability",
        "procurement.ports",
        "boto3",
        "botocore",
        "fastapi",
        "httpx",
        "langgraph",
        "mcp",
    ),
    "ports": (
        "procurement.adapters",
        "procurement.agent",
        "procurement.api",
        "procurement.bootstrap",
        "procurement.mcp_server",
        "procurement.observability",
        "boto3",
        "botocore",
        "fastapi",
        "httpx",
        "langgraph",
        "mcp",
    ),
    "agent": (
        "procurement.adapters",
        "procurement.api",
        "procurement.bootstrap",
        "procurement.mcp_server",
        "boto3",
        "botocore",
        "fastapi",
        "mcp",
    ),
    "adapters": (
        "procurement.agent",
        "procurement.api",
        "procurement.bootstrap",
        "procurement.mcp_server",
    ),
    "api": (
        "procurement.adapters",
        "procurement.bootstrap",
        "procurement.mcp_server",
    ),
    "mcp_server": (
        "procurement.agent",
        "procurement.api",
        "procurement.bootstrap",
    ),
    "observability": (
        "procurement.adapters",
        "procurement.agent",
        "procurement.api",
        "procurement.bootstrap",
        "procurement.mcp_server",
    ),
}


def _current_package(source_file: Path) -> tuple[str, ...]:
    relative_path = source_file.relative_to(PACKAGE_ROOT)
    package_parts = relative_path.parent.parts
    return ("procurement", *package_parts)


def _imported_modules(source_file: Path) -> Iterator[tuple[str, int]]:
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=source_file)
    current_package = _current_package(source_file)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
            continue

        if not isinstance(node, ast.ImportFrom):
            continue

        if node.level:
            retained_parts = len(current_package) - (node.level - 1)
            base_parts = current_package[:retained_parts]
        else:
            base_parts = ()

        module_parts = tuple(node.module.split(".")) if node.module else ()
        imported_base = ".".join((*base_parts, *module_parts))
        if imported_base:
            yield imported_base, node.lineno

        for alias in node.names:
            if alias.name != "*" and imported_base:
                yield f"{imported_base}.{alias.name}", node.lineno


def _matches_prefix(module: str, forbidden_prefix: str) -> bool:
    return module == forbidden_prefix or module.startswith(f"{forbidden_prefix}.")


class ArchitectureTest(unittest.TestCase):
    def test_required_packages_exist(self) -> None:
        missing_paths = [
            str(package_path / "__init__.py")
            for package_path in REQUIRED_PACKAGES
            if not (PACKAGE_ROOT / package_path / "__init__.py").is_file()
        ]

        self.assertEqual(
            missing_paths,
            [],
            "Required Python packages are missing:\n" + "\n".join(missing_paths),
        )

    def test_imports_respect_package_boundaries(self) -> None:
        violations: list[str] = []

        for package_name, forbidden_prefixes in FORBIDDEN_IMPORTS_BY_PACKAGE.items():
            package_path = PACKAGE_ROOT / package_name
            for source_file in sorted(package_path.rglob("*.py")):
                for imported_module, line_number in _imported_modules(source_file):
                    for forbidden_prefix in forbidden_prefixes:
                        if _matches_prefix(imported_module, forbidden_prefix):
                            relative_file = source_file.relative_to(PROJECT_ROOT)
                            violations.append(
                                f"{relative_file}:{line_number} imports "
                                f"{imported_module}"
                            )

        self.assertEqual(
            violations,
            [],
            "Package boundary violations:\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
