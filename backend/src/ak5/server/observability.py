"""Request ids, structured logs and health checks (operations.md §4.1, §4.2, §4.4, §4.5, #113).

* every request gets a ``request_id`` (a safe ``X-Request-ID`` from the caller is kept,
  anything else is replaced) which is echoed in the response header, put on every log record
  and written into the audit payload;
* ``AK5_LOG_FORMAT=json`` switches to one JSON object per line (default ``text`` leaves the
  logging setup exactly as it was); ``AK5_LOG_LEVEL`` sets the level;
* the access log records a fixed set of fields only. Secrets can never appear in it, and a
  scrubbing filter additionally masks token-shaped strings in any log message or traceback.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import socket
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_QUIET_PATHS = frozenset({"/health", "/healthz", "/readyz"})

request_id_var: ContextVar[str | None] = ContextVar("ak5_request_id", default=None)
access_logger = logging.getLogger("ak5.access")


def instance_id() -> str:
    return os.environ.get("AK5_INSTANCE_ID") or f"{socket.gethostname()}-{os.getpid()}"


def new_request_id() -> str:
    return "req_" + secrets.token_hex(8)


def accept_request_id(value: str | None) -> str:
    """Keep a caller-supplied id only if it is a safe token; otherwise mint one."""
    if value and _SAFE_REQUEST_ID.match(value):
        return value
    return new_request_id()


def current_request_id() -> str | None:
    return request_id_var.get()


# --- scrubbing -------------------------------------------------------------------------

_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"\bak5_agt_[A-Za-z0-9_-]+"), "ak5_agt_[redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), "[jwt redacted]"),
    (
        re.compile(
            r"(?i)\b(authorization|cookie|set-cookie|x-csrf-token|ak5_session|ak5_csrf|"
            r"claim_token|client_secret|code_verifier|access_token|refresh_token)\b"
            r"(\s*[=:]\s*|\"\s*:\s*\")[^\s,;&\"']+"
        ),
        r"\1\2[redacted]",
    ),
)


def scrub(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class ScrubFilter(logging.Filter):
    """Masks token-shaped strings in the message and in exception text."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let logging break the app
            return True
        record.msg = scrub(message)
        record.args = None
        if record.exc_info and record.exc_info[1] is not None and not record.exc_text:
            record.exc_text = scrub(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = scrub(record.exc_text)
        return True


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


# --- formatters ------------------------------------------------------------------------

_ACCESS_FIELDS = (
    "method",
    "path",
    "status",
    "duration_ms",
    "principal_id",
    "workspace_id",
    "instance_id",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        for name in _ACCESS_FIELDS:
            if hasattr(record, name):
                payload[name] = getattr(record, name)
        if record.exc_text or record.exc_info:
            payload["exc"] = record.exc_text or self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"


def log_format() -> str:
    return (os.environ.get("AK5_LOG_FORMAT") or "text").strip().lower()


class UvicornAccessFilter(logging.Filter):
    """uvicorn's own access line carries the full request target, query string included.

    That is where an OIDC callback's ``?code=…&state=…`` would land (operations.md §4.4), and
    ``uvicorn.access`` has its own handler, so the handler-level ``ScrubFilter`` never sees it.
    Keep the line, drop the query string, mask token-shaped strings.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            target = scrub(args[2].split("?", 1)[0])
            record.args = (*args[:2], target, *args[3:])
        return True


def _secure_uvicorn_access_log(fmt: str) -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, UvicornAccessFilter) for f in access.filters):
        access.addFilter(UvicornAccessFilter())
    if fmt == "json":
        # ``ak5.access`` is the structured replacement; uvicorn's would be a plain-text duplicate.
        access.setLevel(logging.WARNING)


def configure_logging() -> None:
    """Idempotent. ``text`` (default) with no ``AK5_LOG_LEVEL`` leaves the logging setup as it was
    (apart from keeping query strings out of uvicorn's access line)."""
    fmt = log_format()
    level_name = (os.environ.get("AK5_LOG_LEVEL") or "").strip().upper()
    _secure_uvicorn_access_log(fmt)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_ak5_handler", False):
            root.removeHandler(handler)
    if fmt != "json" and not level_name:
        return
    handler = logging.StreamHandler()
    handler._ak5_handler = True  # type: ignore[attr-defined]
    handler.addFilter(ScrubFilter())
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(_TEXT_FORMAT))
    root.addHandler(handler)
    if level_name:
        root.setLevel(getattr(logging, level_name, logging.INFO))
    elif fmt == "json":
        root.setLevel(logging.INFO)


# --- ASGI middleware -------------------------------------------------------------------


class RequestContextMiddleware:
    """Outermost middleware: request id in/out and one access-log line per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        request_id = accept_request_id(headers.get(REQUEST_ID_HEADER.lower()))
        token = request_id_var.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                response_headers = list(message.get("headers") or [])
                response_headers = [
                    (k, v) for k, v in response_headers if k.lower() != REQUEST_ID_HEADER.lower().encode()
                ]
                response_headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            access_logger.exception(
                "unhandled error",
                extra=_access_extra(scope, 500, started),
            )
            raise
        finally:
            path = _route_template(scope)
            if path not in _QUIET_PATHS:
                access_logger.info(
                    "%s %s %s",
                    scope.get("method", "-"),
                    path,
                    status_holder["status"],
                    extra=_access_extra(scope, status_holder["status"], started),
                )
            request_id_var.reset(token)


def _route_template(scope: Scope) -> str:
    """``/api/v1/tickets/{ticket_id}``: the matched route with parameter values masked.

    Built from the real path (the matched route's own ``path`` is relative to its router) by
    replacing each path-parameter value with ``{name}``. Raw ids and the query string never
    reach the log; an unmatched request is just ``<unmatched>``.
    """
    raw = str(scope.get("path", ""))
    if scope.get("route") is None and not scope.get("endpoint"):
        return raw if raw in _QUIET_PATHS else "<unmatched>"
    path = raw
    for name, value in (scope.get("path_params") or {}).items():
        text = str(value)
        if not text:
            continue
        start = len(path)
        while True:  # last occurrence that is a whole segment (or run of segments)
            start = path.rfind(text, 0, start)
            if start < 0:
                break
            end = start + len(text)
            if (start == 0 or path[start - 1] == "/") and (end == len(path) or path[end] == "/"):
                path = f"{path[:start]}{{{name}}}{path[end:]}"
                break
    return path


def _access_extra(scope: Scope, status: int, started: float) -> dict[str, Any]:
    state = scope.get("state") or {}
    return {
        "method": scope.get("method"),
        "path": _route_template(scope),
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "principal_id": state.get("principal_id"),
        "workspace_id": state.get("workspace_id"),
        "instance_id": instance_id(),
    }


# --- audit: stamp the request id into every audit payload ------------------------------

_audit_hook_installed = False


def install_audit_hook() -> None:
    """Put ``request_id`` into the JSON payload of every ``AuditLog`` row written in a request."""
    global _audit_hook_installed
    if _audit_hook_installed:
        return
    from sqlalchemy import event

    from ak5.server.models.audit import AuditLog

    @event.listens_for(AuditLog, "before_insert")
    def _stamp(_mapper, _connection, target: AuditLog) -> None:  # noqa: ANN001
        rid = request_id_var.get()
        if not rid:
            return
        try:
            payload = json.loads(target.payload) if target.payload else {}
        except (TypeError, ValueError):
            return
        if isinstance(payload, dict) and "request_id" not in payload:
            payload["request_id"] = rid
            target.payload = json.dumps(payload)

    _audit_hook_installed = True


# --- readiness -------------------------------------------------------------------------


async def readiness_failures(db) -> list[str]:  # noqa: ANN001 - AsyncSession
    """Names of failed checks (``database`` / ``schema`` / ``blob``); details only go to the log.

    The response never says *why* a check failed, so /readyz cannot be used to map the stack.
    """
    from sqlalchemy import text

    failures: list[str] = []
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        logging.getLogger(__name__).exception("readyz: database check failed")
        return ["database"]
    try:
        from ak5.server.migrations import current_revision, head_revision

        connection = await db.connection()
        revision = await connection.run_sync(current_revision)
        if revision != head_revision():
            logging.getLogger(__name__).error(
                "readyz: schema at %s, expected %s", revision, head_revision()
            )
            failures.append("schema")
    except Exception:
        logging.getLogger(__name__).exception("readyz: schema check failed")
        failures.append("schema")
    try:
        from ak5.server.blob import get_blob_store

        check = getattr(get_blob_store(), "check_ready", None)
        if check is not None:
            await check()
    except Exception:
        logging.getLogger(__name__).exception("readyz: blob store check failed")
        failures.append("blob")
    return failures
