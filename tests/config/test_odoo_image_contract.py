"""Static contracts for the derived StockAI Odoo 19 image and add-on."""

from __future__ import annotations

import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = PROJECT_ROOT / "docker" / "odoo.Dockerfile"
ODOO_REQUIREMENTS_INPUT = PROJECT_ROOT / "docker" / "odoo-requirements.in"
ODOO_REQUIREMENTS_LOCK = PROJECT_ROOT / "docker" / "odoo-requirements.txt"
ODOO_BOOTSTRAP = PROJECT_ROOT / "odoo" / "bootstrap" / "bootstrap.py"
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


def _locked_requirement_blocks(lock: str) -> list[list[str]]:
    lines = [line for line in lock.splitlines() if line and not line.startswith("#")]
    starts = [index for index, line in enumerate(lines) if not line[0].isspace()]
    return [
        lines[start : starts[index + 1] if index + 1 < len(starts) else len(lines)]
        for index, start in enumerate(starts)
    ]


def test_odoo_python_dependency_closure_is_exactly_pinned_and_hashed() -> None:
    assert ODOO_REQUIREMENTS_INPUT.read_text(encoding="utf-8") == ("boto3==1.43.62\n")

    blocks = _locked_requirement_blocks(
        ODOO_REQUIREMENTS_LOCK.read_text(encoding="utf-8")
    )
    assert blocks
    assert {block[0].partition("==")[0] for block in blocks} == {
        "boto3",
        "botocore",
        "jmespath",
        "python-dateutil",
        "s3transfer",
        "six",
        "urllib3",
    }
    for requirement, *hashes in blocks:
        assert requirement.endswith(" \\")
        assert re.fullmatch(
            r"[a-z0-9][a-z0-9._-]*==\S+", requirement.removesuffix(" \\")
        )
        assert hashes
        for item in hashes:
            normalized = item.removesuffix(" \\")
            assert re.fullmatch(r"    --hash=sha256:[0-9a-f]{64}", normalized)


def test_odoo_image_extends_the_approved_digest_and_keeps_upstream_startup() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    normalized_dockerfile = " ".join(dockerfile.replace("\\\n", "").split())

    assert re.findall(r"^FROM\s+(\S+)", dockerfile, flags=re.MULTILINE) == [
        OFFICIAL_ODOO_IMAGE
    ]
    assert "boto3==1.43.62" not in dockerfile
    assert (
        "--ignore-installed --require-hashes --requirement /tmp/odoo-requirements.txt"
    ) in normalized_dockerfile
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
        "docker/odoo-requirements.txt",
        "odoo/addons/stockai_procurement",
        "odoo/bootstrap/bootstrap.py",
        "odoo/bootstrap/sinks.py",
        "scripts/odoo/seed.py",
        "scripts/odoo/verify_seed.py",
    ]
    assert "/tmp/odoo-requirements.txt" in dockerfile
    assert "/mnt/extra-addons/stockai_procurement" in dockerfile
    assert "/opt/stockai/bootstrap.py" in dockerfile
    assert "/opt/stockai/sinks.py" in dockerfile
    assert "/opt/stockai/seed.py" in dockerfile
    assert "/opt/stockai/verify_seed.py" in dockerfile


def test_bootstrap_uses_the_isolated_sink_boundary() -> None:
    bootstrap = ODOO_BOOTSTRAP.read_text(encoding="utf-8")

    assert 'sys.path.insert(0, "/opt/stockai")' in bootstrap
    assert "from sinks import sink_from_environment" in bootstrap
    assert "secret_sink = sink_from_environment()" in bootstrap
    assert "class _FileSink" not in bootstrap
    assert "class _SecretsManagerSink" not in bootstrap


def test_addon_manifest_has_only_the_approved_dependencies() -> None:
    manifest = _manifest()

    assert manifest["depends"] == ["purchase_stock", "account", "analytic", "mail"]
    assert manifest["installable"] is True
    assert manifest["application"] is False
    assert manifest["data"] == [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "security/rules.xml",
        "views/preferences.xml",
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
    assert "scripts/*" in ignored
    assert "!scripts/odoo/seed.py" in ignored
    assert "!scripts/odoo/verify_seed.py" in ignored
