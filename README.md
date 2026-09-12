# binnacle_auth — credential access for The Binnacle apps

```
~/.config/binnacle/tokens/<app>   (0600, dir 0700)
        │  Service Account token
        ▼
1Password SDK  ──read-only──▶  The Binnacle vault  ──▶  item fields
```

One mechanism for every app and every session type: launchd, a GUI terminal,
and SSH all read the same file. There is no environment-variable, Keychain or
`.env` fallback anywhere; if 1Password is unreachable the caller gets a
`CredentialError`.

## Why a token file instead of the macOS Keychain
The login Keychain is unreadable from SSH and agent sessions, which is why
credentialed work used to need a console session or ad-hoc shell hacks. A 0600
file under a 0700 directory is readable from any session, protected at rest by
FileVault, and is what 1Password recommends for Service Account tokens on
servers. The reader refuses any file that is not owned by the user with mode
0600 in a 0700 directory.

## Setup (once per machine)

1. In 1Password (web, Developer → Service Accounts) each app has a Service
   Account with **read** access to *The Binnacle* vault: `Bearing`, `Purser`,
   `Meridian`, `Tally`, `MadeWild`, `The Binnacle`. Each token is also stored in the vault as an API
   Credential item titled `Service Account Auth Token: <App>`.
2. Store one token by pasting it (from any app venv):
   ```bash
   venv/bin/binnacle-auth store --app purser
   ```
3. Provision the rest from their vault items:
   ```bash
   venv/bin/binnacle-auth bootstrap --via purser
   ```
4. Verify:
   ```bash
   venv/bin/binnacle-auth doctor            # all apps
   venv/bin/binnacle-auth doctor --app bearing --expect "Meevo Multi Unit Login"
   venv/bin/binnacle-auth items --app purser  # titles only, never values
   ```

## Using it from an app

```python
from shared_utils.binnacle_auth import OnePasswordClient, fetch_login_credential, fetch_field

# async
op = OnePasswordClient("bearing")
creds = await op.get_login_credential("Meevo Multi Unit Login")   # {'username', 'password'}
api   = await op.get_api_credential("Paylocity API — Prod NextGen")  # {'client_id', 'client_secret', 'token_url'}
zip_pw = await op.get_field("ME SFTP", "Zip password - daily csv extracts")

# sync
creds = fetch_login_credential("bearing", "Meevo Multi Unit Login")
zip_pw = fetch_field("bearing", "ME SFTP", "Zip password - daily csv extracts")
```

Each app keeps a single `credentials.py` naming its app id and the vault items
it uses, so item titles are not scattered through the code.

## The `op` CLI over SSH
`thebinnacle/scripts/binnacle-op.zsh` defines an `op` shell function that injects
`OP_SERVICE_ACCOUNT_TOKEN` from the token store for that one invocation, so
`op item list --vault "The Binnacle"` works in any session without `op signin`
or the desktop-app integration. Install with `thebinnacle/scripts/install_shell_integration.sh`.

## Rotation
Rotate the Service Account in 1Password, update its `Service Account Auth Token: <App>`
item, then on the Mac Studio: `binnacle-auth store --app <app>` (or `bootstrap --force`).
Restart the app's launchd job.
