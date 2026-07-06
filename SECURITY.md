# Security Policy

## Supported Versions

**meeting-ai-copilot** is actively maintained on the `main` branch and the latest tagged release. Security fixes target `main` first.

| Version | Supported |
| --- | --- |
| 1.0.x (latest) | Yes |
| Older | Best effort |

## Reporting a Vulnerability

Do **not** open a public issue with exploit details, credentials, API keys, or proof-of-concept code.

Preferred channels:

1. GitHub **Private vulnerability reporting** or a **Security Advisory** for this repository, if enabled.
2. If private reporting is unavailable, open a public issue asking for a private contact channel **without** technical exploit details.

Include:

- Affected version, commit, or branch.
- Clear reproduction steps.
- Impact and attack scenario.
- Relevant logs or screenshots with secrets removed.

## Scope

### In scope

- Accidental logging or persistence of `cloud_asr_api_key` / `ai_api_key` in output files or logs.
- Insecure default handling of local `config.json` (permissions, accidental commit).
- Unsafe network handling in project code (TLS verification bypass, credential leakage in URLs).
- Supply-chain issues exploitable through this repository's shipped code or default configuration.

### Out of scope

- Compromise of a user's own Volcengine API keys outside this application.
- Vulnerabilities in Volcengine ASR / LLM cloud services themselves.
- Windows audio driver or meeting-software issues.
- Denial-of-service requiring unrealistic traffic or physical access to the user's PC.
- Missing Windows security headers (not applicable to this desktop tool).

## Secret Handling

This project follows a **bring-your-own-key (BYOK)** model:

- Never commit `config.json` with real keys. Use `config.example.json` as the template.
- Prefer environment variables in shared or CI environments:
  - `VOLC_ASR_API_KEY`
  - `VOLCENGINE_CODING_PLAN_API_KEY`
- Output files under `桌面\实时监听\` may contain meeting transcripts — treat them as sensitive.

## Dependency Audit

CI runs `pip audit` on every push/PR (informational; does not block merge by default). To audit locally:

```powershell
python -m pip install pip-audit
pip-audit -r requirements.txt
```

Pinned versions live in `requirements.txt`. Upgrade dependencies deliberately and re-run smoke tests after changes.

## Disclosure

Maintainers aim to acknowledge valid reports within 7 days and coordinate remediation before public disclosure. Timelines are best effort for this community project.
