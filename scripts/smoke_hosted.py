"""Smoke-check a deployed AK5 server (Vercel or any host).

Checks ``/readyz``, ``/meta`` (API version), the dashboard HTML, and that one
``/_next/static/chunks/*.js`` referenced by that HTML is served as JavaScript.

Usage::

    uv run python scripts/smoke_hosted.py https://ak5-five.vercel.app
    uv run python scripts/smoke_hosted.py http://127.0.0.1:8000 --json

Exit status: 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass

import httpx

EXPECTED_API_VERSION = "2"
DEFAULT_TIMEOUT_SECONDS = 15.0
_CHUNK_RE = re.compile(r"""["'](/_next/static/chunks/[^"'?#]+\.js)""")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def first_chunk_path(html: str) -> str | None:
    match = _CHUNK_RE.search(html)
    return match.group(1) if match else None


def _check_readyz(client: httpx.Client) -> CheckResult:
    resp = client.get("/readyz")
    try:
        body = resp.json() if resp.status_code == 200 else None
    except ValueError:
        body = None
    status = body.get("status") if isinstance(body, dict) else None
    if status == "ready":
        return CheckResult("readyz", True, "ready")
    return CheckResult("readyz", False, f"HTTP {resp.status_code}: {resp.text[:200]}")


def _check_meta(client: httpx.Client) -> CheckResult:
    resp = client.get("/meta")
    try:
        body = resp.json() if resp.is_success else None
    except ValueError:
        body = None
    version = body.get("api_version") if isinstance(body, dict) else None
    if str(version) == EXPECTED_API_VERSION:
        return CheckResult("meta", True, f"api_version {version}")
    return CheckResult("meta", False, f"HTTP {resp.status_code}: api_version={version!r}")


def _check_dashboard(client: httpx.Client) -> tuple[CheckResult, CheckResult]:
    resp = client.get("/")
    is_html = resp.status_code == 200 and "text/html" in resp.headers.get("content-type", "")
    if not is_html:
        dashboard = CheckResult("dashboard", False, f"HTTP {resp.status_code} {resp.headers.get('content-type', '')}")
        return dashboard, CheckResult("next_chunk", False, "skipped: no dashboard HTML")
    dashboard = CheckResult("dashboard", True, "HTML 200")

    chunk = first_chunk_path(resp.text)
    if chunk is None:
        return dashboard, CheckResult(
            "next_chunk", False, "dashboard HTML references no /_next/static/chunks/*.js (index.html only?)"
        )
    chunk_resp = client.get(chunk)
    content_type = chunk_resp.headers.get("content-type", "")
    if chunk_resp.status_code == 200 and "javascript" in content_type:
        return dashboard, CheckResult("next_chunk", True, f"{chunk} 200 {content_type}")
    return dashboard, CheckResult("next_chunk", False, f"{chunk} HTTP {chunk_resp.status_code} {content_type}")


def run_checks(base_url: str, *, transport: httpx.BaseTransport | None = None) -> list[CheckResult]:
    with httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=True,
        transport=transport,
    ) as client:
        results: list[CheckResult] = []
        for name, check in (("readyz", _check_readyz), ("meta", _check_meta)):
            try:
                results.append(check(client))
            except httpx.HTTPError as exc:
                results.append(CheckResult(name, False, f"{type(exc).__name__}: {exc}"))
        try:
            results.extend(_check_dashboard(client))
        except httpx.HTTPError as exc:
            results.append(CheckResult("dashboard", False, f"{type(exc).__name__}: {exc}"))
        return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-check a deployed AK5 server.")
    parser.add_argument("base_url", help="Server origin, e.g. https://ak5-five.vercel.app")
    parser.add_argument("--json", action="store_true", help="Print results as JSON.")
    args = parser.parse_args(argv)

    results = run_checks(args.base_url)
    if args.json:
        print(json.dumps([asdict(r) for r in results]))
    else:
        for r in results:
            print(f"{'PASS' if r.ok else 'FAIL'}  {r.name:<10} {r.detail}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
