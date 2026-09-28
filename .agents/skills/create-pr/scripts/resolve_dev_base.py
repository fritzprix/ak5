#!/usr/bin/env python3
"""Resolve the latest remote integration branch matching ``dev/MAJOR.MINOR.x``.

Prints the short branch name (e.g. ``dev/1.0.x``) to stdout.
Exit 1 if none found.
"""

from __future__ import annotations

import re
import subprocess
import sys

_DEV_RE = re.compile(r"^dev/(\d+)\.(\d+)\.x$")


def _list_remote_short_names() -> list[str]:
    raw = subprocess.check_output(["git", "branch", "-r"], text=True)
    names: list[str] = []
    for line in raw.splitlines():
        short = line.strip().lstrip("* ").removeprefix("origin/")
        if short and not short.startswith("HEAD"):
            names.append(short)
    return names


def main() -> int:
    try:
        subprocess.run(
            ["git", "fetch", "origin", "--prune"],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        print("git not found", file=sys.stderr)
        return 1

    found: list[tuple[int, int, str]] = []
    for short in _list_remote_short_names():
        m = _DEV_RE.fullmatch(short)
        if m:
            found.append((int(m.group(1)), int(m.group(2)), short))

    if not found:
        print(
            "No remote branches matching origin/dev/MAJOR.MINOR.x",
            file=sys.stderr,
        )
        return 1

    found.sort(key=lambda t: (t[0], t[1]), reverse=True)
    print(found[0][2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
