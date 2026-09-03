# Shared Utilities

## What This Is
A shared Python package used by every app on the Mac Studio (Bearing, Purser, Meridian, The Binnacle). Installed **editable from the local checkout** so all four venvs run the same source:

```bash
venv/bin/pip install -e ../shared-utils            # credentials only
venv/bin/pip install -e "../shared-utils[browser]"  # + Playwright automation
```

## Modules
- **binnacle_auth** — THE credential path. Per-app 1Password Service Account token (`~/.config/binnacle/tokens/<app>`, 0600) → 1Password SDK → The Binnacle vault → item fields. See README.md.
- **browser** — Playwright helpers (`create_browser`, `auth0_login`).
- **me_portal** — ME Franchisee Portal session (retired in Bearing; kept for deliberate historical reloads). Requires explicit `credentials=`; it never reads a credential store itself.

## Rules
- No app reads a credential from `.env`, a launchd plist, a shell export, or the macOS Keychain. `binnacle_auth` is the only path, and it has no fallback.
- The SDK access is read-only. Nothing here creates or updates vault items.
- Never log a token or a field value. Item titles only.
- `binnacle-auth doctor` (installed console script) is the mechanical check. Run it before assuming a credential problem is elsewhere.

## Tests
```bash
../Purser/venv/bin/python -m pytest -q tests   # no venv of its own; any app venv works
```

## Remote
`origin` is `github.com/strique9/shared-utils`. Local checkouts are the runtime source; push after changes so a fresh clone matches.
