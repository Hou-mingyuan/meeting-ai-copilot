from __future__ import annotations

import sys
from pathlib import Path

from cloud_asr_volcengine import application_resource_root


def test_source_resource_root_contains_version() -> None:
    assert (application_resource_root() / "VERSION").is_file()


def test_frozen_resource_root_uses_meipass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert application_resource_root() == tmp_path.resolve()
