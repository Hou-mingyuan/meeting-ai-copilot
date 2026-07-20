#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".py", ".json", ".md", ".ps1", ".bat", ".yml", ".yaml", ".js", ".txt", ".spec"}
IGNORED_PARTS = {".git", ".venv", "build", "dist", "__pycache__", ".pytest_cache"}
RESERVED_PORTS = set(range(19060, 19070))


def repository_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    files = []
    for value in result.stdout.splitlines():
        path = ROOT / value
        if path.suffix.lower() in TEXT_SUFFIXES and not any(part in IGNORED_PARTS for part in path.parts):
            files.append(path)
    return files


def scan_secrets(files: list[Path]) -> list[str]:
    findings: list[str] = []
    private_key = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
    known_token = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b")
    json_secret = re.compile(
        r'"(?:cloud_asr_api_key|cloud_asr_access_key|ai_api_key|api_key|access_key|secret)"\s*:\s*"([^"]+)"',
        re.IGNORECASE,
    )
    allowed_fragments = {"example", "your-", "placeholder", "mock", "redacted", "<", "${"}
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if private_key.search(text):
            findings.append(f"{path.relative_to(ROOT)}: private key block")
        if known_token.search(text):
            findings.append(f"{path.relative_to(ROOT)}: token-like value")
        if path.suffix.lower() == ".json":
            for match in json_secret.finditer(text):
                value = match.group(1).strip()
                if value and not any(fragment in value.casefold() for fragment in allowed_fragments):
                    findings.append(f"{path.relative_to(ROOT)}: non-placeholder secret field")
    return findings


def scan_ports(files: list[Path]) -> list[str]:
    findings: list[str] = []
    url_port = re.compile(r"(?:127\.0\.0\.1|localhost|::1):(?P<port>\d{2,5})")
    cli_port = re.compile(r"--port(?:\s+|[=\"']+)(?P<port>\d{2,5})")
    for path in files:
        relative = path.relative_to(ROOT)
        if relative.parts and relative.parts[0] == "tests":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in (url_port, cli_port):
            for match in pattern.finditer(text):
                port = int(match.group("port"))
                if port not in RESERVED_PORTS:
                    line = text.count("\n", 0, match.start()) + 1
                    findings.append(f"{relative}:{line}: service port {port} is outside 19060-19069")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Repository secret and reserved-port checks")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    files = repository_files()
    findings = {"secrets": scan_secrets(files), "ports": scan_ports(files)}
    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    else:
        for category, items in findings.items():
            print(f"{category}: {'PASS' if not items else 'FAIL'}")
            for item in items:
                print(f"  {item}")
    return 1 if any(findings.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
