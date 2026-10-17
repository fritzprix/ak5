"""HTTP client for AK5 REST API (cli.md §2–§3).

Does not import ak5.server / ak5.routers / ak5.services / ak5.models.
"""

from ak5.client.auth import resolve_access_token, set_access_token_resolver
from ak5.client.claims import (
    clear_claim_token,
    load_claim_token,
    resolve_claim_token,
    save_claim_token,
)
from ak5.client.errors import (
    EXIT_AUTH,
    EXIT_EMPTY,
    EXIT_GENERIC,
    EXIT_USAGE,
    EXIT_VERSION,
    ClientError,
    exit_code_for_error,
)
from ak5.client.http import AK5Client
from ak5.client.output import is_json_mode, print_error, print_result
from ak5.client.payload import load_json_payload

__all__ = [
    "AK5Client",
    "ClientError",
    "EXIT_AUTH",
    "EXIT_EMPTY",
    "EXIT_GENERIC",
    "EXIT_USAGE",
    "EXIT_VERSION",
    "clear_claim_token",
    "exit_code_for_error",
    "is_json_mode",
    "load_claim_token",
    "load_json_payload",
    "print_error",
    "print_result",
    "resolve_access_token",
    "resolve_claim_token",
    "save_claim_token",
    "set_access_token_resolver",
]
