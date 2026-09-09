"""binnacle_auth — the one way Binnacle apps read credentials.

    ~/.config/binnacle/tokens/<app>  →  1Password SDK  →  The Binnacle vault  →  item fields

Every app (bearing, purser, meridian, binnacle) has its own 1Password Service
Account. Its token lives in one place: a 0600 file under a 0700 directory in the
user's home. Nothing else — no Keychain, no .env, no launchd plist, no shell
export — may hold a credential or a token.

Why a file and not the macOS Keychain: the login Keychain is only readable from
a console (GUI) session. SSH sessions, agent sessions and remote tooling cannot
read it, which is what forced ad-hoc workarounds in the past. A 0600 file is
readable from every session type, is protected at rest by FileVault, and is what
1Password itself recommends for Service Account tokens on servers.

Rules enforced here, mechanically:
  - The token file must be a regular file owned by the current user, mode 0600,
    inside a directory with no group/other permissions. Anything else is refused.
  - Vault access is read-only. This module never creates or updates items.
  - Tokens and secret values are never logged; only item titles are.
  - There is no environment-variable fallback. If 1Password is unreachable the
    caller gets a CredentialError, not a stale copy from somewhere else.

Item names in the vault may contain em dashes, which are invalid in op://
secret-reference paths, so lookups go through the items API by exact title.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import stat
import sys
from pathlib import Path

__all__ = [
    "APPS",
    "DEFAULT_VAULT",
    "TOKEN_DIR",
    "CredentialError",
    "TokenStore",
    "OnePasswordClient",
    "fetch_login_credential",
    "fetch_api_credential",
    "fetch_field",
    "TOKEN_ITEM_TITLE",
    "main",
]

logger = logging.getLogger("binnacle_auth")

DEFAULT_VAULT = "The Binnacle"
TOKEN_DIR = Path("~/.config/binnacle/tokens").expanduser()

# Every app that reads the vault. Adding an app = create its Service Account in
# 1Password (read access to The Binnacle vault), then `binnacle-auth store --app <name>`.
APPS = ("binnacle", "bearing", "meridian", "purser", "tally")

# 1Password Service Account tokens carry this prefix. Rejecting anything else
# stops a pasted password or op:// reference from landing in the token file.
_TOKEN_PREFIX = "ops_"

_GROUP_OTHER_BITS = stat.S_IRWXG | stat.S_IRWXO


class CredentialError(RuntimeError):
    """Raised when a token or credential cannot be obtained safely."""


class TokenStore:
    """Reads and writes per-app Service Account tokens with strict permissions."""

    def __init__(self, directory: Path = TOKEN_DIR):
        self.directory = Path(directory)

    def path(self, app: str) -> Path:
        if not app or "/" in app or app.startswith("."):
            raise CredentialError(f"Invalid app name {app!r}")
        return self.directory / app

    def available(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(p.name for p in self.directory.iterdir() if p.is_file())

    def read(self, app: str) -> str:
        path = self.path(app)
        if not path.exists():
            raise CredentialError(
                f"No Service Account token for app '{app}' at {path}. "
                f"Store one with: binnacle-auth store --app {app}"
            )
        self._check_permissions(path)
        token = path.read_text(encoding="utf-8").strip()
        if not token:
            raise CredentialError(f"Token file {path} is empty")
        return token

    def write(self, app: str, token: str) -> Path:
        token = token.strip()
        if not token.startswith(_TOKEN_PREFIX):
            raise CredentialError(
                "That does not look like a 1Password Service Account token "
                f"(expected it to start with '{_TOKEN_PREFIX}'). Nothing written."
            )
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        path = self.path(app)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
        os.chmod(path, 0o600)
        return path

    def _check_permissions(self, path: Path) -> None:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise CredentialError(f"{path} is not a regular file")
        if info.st_uid != os.getuid():
            raise CredentialError(f"{path} is not owned by the current user")
        if info.st_mode & _GROUP_OTHER_BITS:
            raise CredentialError(
                f"{path} is mode {stat.S_IMODE(info.st_mode):04o}; it must be 0600. "
                f"Fix with: chmod 600 {path}"
            )
        dir_info = path.parent.stat()
        if dir_info.st_mode & _GROUP_OTHER_BITS:
            raise CredentialError(
                f"{path.parent} is mode {stat.S_IMODE(dir_info.st_mode):04o}; it must be 0700. "
                f"Fix with: chmod 700 {path.parent}"
            )


class OnePasswordClient:
    """Read-only access to one app's view of The Binnacle vault."""

    def __init__(
        self,
        app: str,
        vault: str = DEFAULT_VAULT,
        token_store: TokenStore | None = None,
        integration_version: str = "1.0.0",
    ):
        self.app = app
        self.vault = vault
        self.token_store = token_store or TokenStore()
        self.integration_version = integration_version
        self._op_client = None
        self._vault_id: str | None = None
        self._item_ids: dict[str, str] | None = None

    # -- SDK plumbing -------------------------------------------------------

    async def _get_op_client(self):
        if self._op_client is None:
            try:
                from onepassword import Client
            except ImportError as e:  # pragma: no cover - environment problem
                raise CredentialError(
                    "onepassword SDK not installed. Run: pip install -e ../shared-utils"
                ) from e
            token = self.token_store.read(self.app)
            try:
                self._op_client = await Client.authenticate(
                    auth=token,
                    integration_name=self.app.capitalize(),
                    integration_version=self.integration_version,
                )
            except Exception as e:
                raise CredentialError(
                    f"1Password rejected the Service Account token for app "
                    f"'{self.app}' ({self.token_store.path(self.app)}): {e}"
                ) from e
            logger.info("op_client_init app=%s vault=%s", self.app, self.vault)
        return self._op_client

    async def _resolve_vault_id(self) -> str:
        if self._vault_id is None:
            client = await self._get_op_client()
            for vault in await client.vaults.list():
                if vault.title == self.vault:
                    self._vault_id = vault.id
                    break
            else:
                raise CredentialError(
                    f"Vault '{self.vault}' is not visible to app '{self.app}'. "
                    f"Grant its Service Account read access to the vault."
                )
        return self._vault_id

    async def _item_id_map(self, refresh: bool = False) -> dict[str, str]:
        if self._item_ids is None or refresh:
            client = await self._get_op_client()
            vault_id = await self._resolve_vault_id()
            ids: dict[str, str] = {}
            for overview in await client.items.list(vault_id):
                ids.setdefault(overview.title, overview.id)
            self._item_ids = ids
        return self._item_ids

    async def _get_item(self, title: str):
        ids = await self._item_id_map()
        item_id = ids.get(title)
        if item_id is None:
            ids = await self._item_id_map(refresh=True)
            item_id = ids.get(title)
        if item_id is None:
            raise CredentialError(
                f"Item '{title}' not found in vault '{self.vault}' for app '{self.app}'."
            )
        client = await self._get_op_client()
        vault_id = await self._resolve_vault_id()
        return await client.items.get(vault_id, item_id)

    # -- Public read API ----------------------------------------------------

    async def list_item_titles(self) -> list[str]:
        return sorted(await self._item_id_map(refresh=True))

    async def get_item_fields(self, title: str) -> dict[str, str]:
        """All populated fields of an item, keyed by lower-cased field title."""
        item = await self._get_item(title)
        fields: dict[str, str] = {}
        for field in item.fields:
            if field.title and field.value:
                fields.setdefault(field.title.lower(), field.value)
        logger.info("op_item_read app=%s item=%s", self.app, title)
        return fields

    async def get_field(self, title: str, field_title: str) -> str:
        fields = await self.get_item_fields(title)
        value = fields.get(field_title.lower())
        if not value:
            raise CredentialError(
                f"Item '{title}' has no populated field '{field_title}'. "
                f"Fields present: {sorted(fields)}"
            )
        return value

    async def get_login_credential(self, title: str) -> dict[str, str]:
        """A Login item as {'username', 'password'}; 'email' counts as username."""
        fields = await self.get_item_fields(title)
        username = fields.get("username") or fields.get("email")
        password = fields.get("password")
        if not username or not password:
            raise CredentialError(
                f"Item '{title}' is missing required fields "
                f"(username/email and password). Fields present: {sorted(fields)}"
            )
        return {"username": username, "password": password}

    async def get_api_credential(self, title: str) -> dict[str, str]:
        """An API Credential item as {'client_id', 'client_secret', 'token_url'?}.

        1Password's API Credential template stores the id in `username`, the
        secret in `credential` and an optional URL in `hostname`.
        """
        fields = await self.get_item_fields(title)
        client_id = fields.get("username")
        client_secret = fields.get("credential")
        if not client_id or not client_secret:
            raise CredentialError(
                f"Item '{title}' is missing required fields (username and credential). "
                f"Fields present: {sorted(fields)}"
            )
        result = {"client_id": client_id, "client_secret": client_secret}
        if fields.get("hostname"):
            result["token_url"] = fields["hostname"]
        return result

    async def check(self, expected_items: list[str] | None = None) -> dict:
        """Connectivity report with no secret values in it."""
        report = {
            "app": self.app,
            "vault": self.vault,
            "token_path": str(self.token_store.path(self.app)),
            "ok": False,
        }
        try:
            titles = await self.list_item_titles()
        except CredentialError as e:
            report["error"] = str(e)
            return report
        report["item_count"] = len(titles)
        missing = [name for name in (expected_items or []) if name not in titles]
        report["missing_items"] = missing
        report["ok"] = not missing
        if missing:
            report["error"] = f"Expected items not in vault: {missing}"
        return report


# -- Sync conveniences for non-async callers ---------------------------------

def fetch_login_credential(app: str, title: str) -> dict[str, str]:
    return asyncio.run(OnePasswordClient(app).get_login_credential(title))


def fetch_api_credential(app: str, title: str) -> dict[str, str]:
    return asyncio.run(OnePasswordClient(app).get_api_credential(title))


def fetch_field(app: str, title: str, field_title: str) -> str:
    return asyncio.run(OnePasswordClient(app).get_field(title, field_title))


# -- CLI ---------------------------------------------------------------------

def _cmd_apps(store: TokenStore, _args) -> int:
    present = set(store.available())
    print(f"Token directory: {store.directory}")
    for app in APPS:
        print(f"  {app:10s} {'token present' if app in present else 'NO TOKEN'}")
    extra = sorted(present - set(APPS))
    for app in extra:
        print(f"  {app:10s} token present (not a known app)")
    return 0


def _print_report(report: dict) -> None:
    status = "OK" if report["ok"] else "FAIL"
    print(f"[{status}] app={report['app']} vault={report['vault']!r} token={report['token_path']}")
    if "item_count" in report:
        print(f"       items visible: {report['item_count']}")
    if report.get("missing_items"):
        print(f"       missing items: {report['missing_items']}")
    if report.get("error") and not report.get("missing_items"):
        print(f"       {report['error']}")


def _cmd_doctor(store: TokenStore, args) -> int:
    apps = list(APPS) if args.all or not args.app else [args.app]
    failures = 0
    for app in apps:
        client = OnePasswordClient(app, vault=args.vault, token_store=store)
        report = asyncio.run(client.check(expected_items=args.expect))
        _print_report(report)
        failures += 0 if report["ok"] else 1
    return 1 if failures else 0


def _cmd_items(store: TokenStore, args) -> int:
    client = OnePasswordClient(args.app, vault=args.vault, token_store=store)
    for title in asyncio.run(client.list_item_titles()):
        print(title)
    return 0


def _cmd_store(store: TokenStore, args) -> int:
    if sys.stdin.isatty():
        token = getpass.getpass(f"Paste the 1Password Service Account token for '{args.app}': ")
    else:
        token = sys.stdin.read()
    token = token.strip()
    if not token.startswith(_TOKEN_PREFIX):
        print(f"Refused: token must start with '{_TOKEN_PREFIX}'. Nothing written.", file=sys.stderr)
        return 1

    # Prove the token works before it is written anywhere.
    probe = OnePasswordClient(args.app, vault=args.vault, token_store=_StaticStore(store, token))
    report = asyncio.run(probe.check())
    if not report["ok"]:
        print(f"Refused: token did not authenticate. {report.get('error', '')}", file=sys.stderr)
        return 1
    path = store.write(args.app, token)
    print(f"Stored token for '{args.app}' at {path} (mode 0600). "
          f"Vault '{args.vault}' visible with {report['item_count']} items.")
    return 0


# The vault holds each app's Service Account token as an API Credential item
# with this title pattern, so one stored token can provision the rest.
TOKEN_ITEM_TITLE = "Service Account Auth Token: {app}"


def _cmd_bootstrap(store: TokenStore, args) -> int:
    source = OnePasswordClient(args.via, vault=args.vault, token_store=store)
    targets = [a for a in APPS if a != args.via and (args.force or a not in store.available())]
    if not targets:
        print("Every other app already has a token. Use --force to overwrite.")
        return 0
    failures = 0
    for app in targets:
        title = TOKEN_ITEM_TITLE.format(app=app.capitalize())
        try:
            token = asyncio.run(source.get_field(title, "credential"))
            probe = OnePasswordClient(app, vault=args.vault, token_store=_StaticStore(store, token))
            report = asyncio.run(probe.check())
            if not report["ok"]:
                raise CredentialError(report.get("error", "token did not authenticate"))
            path = store.write(app, token)
            print(f"[OK]   {app:10s} token from '{title}' stored at {path}")
        except CredentialError as e:
            failures += 1
            print(f"[FAIL] {app:10s} {e}")
    return 1 if failures else 0


class _StaticStore(TokenStore):
    """A TokenStore that serves one in-memory token; used to verify before writing."""

    def __init__(self, real: TokenStore, token: str):
        super().__init__(real.directory)
        self._token = token

    def read(self, _app: str) -> str:
        return self._token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="binnacle-auth",
        description="Manage and verify per-app 1Password Service Account access to The Binnacle vault.",
    )
    parser.add_argument("--vault", default=DEFAULT_VAULT, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("apps", help="Show which apps have a stored token").set_defaults(func=_cmd_apps)

    doctor = sub.add_parser("doctor", help="Verify token, authentication and vault visibility")
    doctor.add_argument("--app", choices=APPS)
    doctor.add_argument("--all", action="store_true", help="Check every known app (default)")
    doctor.add_argument("--expect", action="append", default=[], metavar="ITEM",
                        help="Item title that must exist (repeatable)")
    doctor.set_defaults(func=_cmd_doctor)

    items = sub.add_parser("items", help="List item titles visible to an app (never values)")
    items.add_argument("--app", required=True, choices=APPS)
    items.set_defaults(func=_cmd_items)

    store_cmd = sub.add_parser("store", help="Store a Service Account token (prompt or stdin)")
    store_cmd.add_argument("--app", required=True, choices=APPS)
    store_cmd.set_defaults(func=_cmd_store)

    boot = sub.add_parser("bootstrap",
                          help="Provision the other apps' tokens from their vault items, "
                               "using one app whose token is already stored")
    boot.add_argument("--via", required=True, choices=APPS, help="App whose token is already stored")
    boot.add_argument("--force", action="store_true", help="Overwrite tokens that already exist")
    boot.set_defaults(func=_cmd_bootstrap)

    args = parser.parse_args(argv)
    try:
        return args.func(TokenStore(), args)
    except CredentialError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
