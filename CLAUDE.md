# Shared Utilities

## What This Is
A shared Python package providing reusable utilities across projects. Install in any project with `pip install -e ~/Documents/projects/shared-utils/`.

## Modules
- **secrets_manager** — 1Password CLI (`op`) wrapper for secure credential retrieval
- **browser** — Playwright-based browser automation (Auth0 login, headless Chromium)

## Projects Using This
- ME Operator Intelligence Dashboard (`~/Documents/projects/ME Operator Intelligence/Dashboard/`)

## Key Facts
- **Install:** `pip install -e ~/Documents/projects/shared-utils/`
- **Install with browser:** `pip install -e ~/Documents/projects/shared-utils/[browser]`
- **Post-install (browser):** `playwright install chromium` — required once after install
- **Requires:** 1Password CLI (`op`) installed and signed in (`op signin`)
- **Gotchas:** User must run `op signin` at least once before use
