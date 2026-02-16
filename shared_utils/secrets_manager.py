"""Thin wrapper around 1Password CLI (`op`) for secure credential retrieval."""

import json
import subprocess


def get_secret(reference: str) -> str:
    """Retrieve a single secret from 1Password.

    Args:
        reference: 1Password secret reference (e.g., "op://Vault/Item/field")

    Returns:
        The secret value as a string.

    Raises:
        RuntimeError: If the `op` CLI fails (not installed, not signed in, etc.)
    """
    try:
        result = subprocess.run(
            ["op", "read", reference],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except FileNotFoundError:
        raise RuntimeError(
            "1Password CLI (`op`) not found. Install with: brew install --cask 1password-cli"
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to read secret '{reference}': {e.stderr.strip()}")


def get_secrets(references: dict[str, str]) -> dict[str, str]:
    """Retrieve multiple secrets from 1Password.

    Args:
        references: Mapping of friendly names to 1Password references.
            Example: {"username": "op://Vault/Item/username",
                      "password": "op://Vault/Item/password"}

    Returns:
        Dict with the same keys, values replaced with actual secrets.
    """
    return {name: get_secret(ref) for name, ref in references.items()}
