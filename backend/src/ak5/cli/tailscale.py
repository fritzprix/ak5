"""Discover Tailscale IP / MagicDNS name when the CLI is available."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class TailscaleInfo:
    ipv4: str | None
    dns_name: str | None


def _run(args: list[str], timeout: float = 2.0) -> str:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def detect_tailscale() -> TailscaleInfo:
    """Return Tailscale IPv4 and DNS name when `tailscale` is on PATH."""
    if shutil.which("tailscale") is None:
        return TailscaleInfo(ipv4=None, dns_name=None)

    ipv4: str | None = None
    raw_ip = _run(["tailscale", "ip", "-4"])
    if raw_ip:
        first = raw_ip.splitlines()[0].strip()
        if first:
            ipv4 = first

    dns_name: str | None = None
    raw_status = _run(["tailscale", "status", "--json"], timeout=3.0)
    if raw_status:
        try:
            payload = json.loads(raw_status)
            self_info = payload.get("Self") if isinstance(payload, dict) else None
            if isinstance(self_info, dict):
                name = self_info.get("DNSName")
                if isinstance(name, str) and name.strip():
                    dns_name = name.strip().rstrip(".")
        except json.JSONDecodeError:
            pass

    return TailscaleInfo(ipv4=ipv4, dns_name=dns_name)
