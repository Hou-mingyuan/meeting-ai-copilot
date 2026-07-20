from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("repo_checks", ROOT / "scripts" / "repo_checks.py")
assert SPEC and SPEC.loader
repo_checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repo_checks)


def test_repository_has_no_committed_secret_values() -> None:
    assert repo_checks.scan_secrets(repo_checks.repository_files()) == []


def test_runtime_and_documented_service_ports_are_reserved() -> None:
    assert repo_checks.scan_ports(repo_checks.repository_files()) == []
