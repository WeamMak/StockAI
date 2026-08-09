"""Static contracts for the derived StockAI Odoo 19 image and add-on."""

from __future__ import annotations

import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = PROJECT_ROOT / "docker" / "odoo.Dockerfile"
ADDON_ROOT = PROJECT_ROOT / "odoo" / "addons" / "stockai_procurement"
OFFICIAL_ODOO_IMAGE = (
    "odoo@sha256:4872f23288454b724fd2d26c176a418276c2b3552e9aa752f9396b59d864b3a0"
)


def _manifest() -> dict[str, object]:
    parsed = ast.literal_eval(
        (ADDON_ROOT / "__manifest__.py").read_text(encoding="utf-8")
    )
    assert isinstance(parsed, dict)
    return parsed


def test_odoo_image_extends_the_approved_digest_and_keeps_upstream_startup() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert re.findall(r"^FROM\s+(\S+)", dockerfile, flags=re.MULTILINE) == [
        OFFICIAL_ODOO_IMAGE
    ]
    assert "boto3==1.43.62" in dockerfile
    assert "COPY . ." not in dockerfile
    assert "ENTRYPOINT" not in dockerfile
    assert "CMD" not in dockerfile
    assert re.search(r"^USER\s+odoo$", dockerfile, flags=re.MULTILINE)


def test_odoo_image_copies_only_the_addon_and_finite_bootstrap_code() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    copy_sources = re.findall(
        r"^COPY(?:\s+--\S+)*\s+(\S+)\s+\S+", dockerfile, re.MULTILINE
    )

    assert copy_sources == [
        "odoo/addons/stockai_procurement",
        "odoo/bootstrap/bootstrap.py",
    ]
    assert "/mnt/extra-addons/stockai_procurement" in dockerfile
    assert "/opt/stockai/bootstrap.py" in dockerfile


def test_addon_manifest_has_only_the_approved_dependencies() -> None:
    manifest = _manifest()

    assert manifest["depends"] == ["purchase_stock", "account", "analytic", "mail"]
    assert manifest["installable"] is True
    assert manifest["application"] is False
    assert manifest["data"] == [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "security/rules.xml",
    ]


def test_image_build_context_excludes_local_secret_sinks() -> None:
    ignored = set(
        (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    )

    assert "**/*.key" in ignored
    assert "**/*.pem" in ignored
    assert "**/api-key" in ignored
    assert "**/bootstrap-api-key" in ignored
    assert ".env*" in ignored
    assert "reports" in ignored
