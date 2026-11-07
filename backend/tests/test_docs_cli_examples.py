"""Issue #19: every ``ak5 …`` example in the user docs must match the real CLI.

Each example is resolved against the click command tree: subcommands must exist
and every ``--option`` must belong to the resolved command (or be a root option).
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import pytest
from ak5.cli.main import cli

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = [
    "README.md",
    "backend/README.md",
    "docs/MANUAL.md",
    "skills/ak5/SKILL.md",
    ".agents/skills/cursor-delegate/SKILL.md",
    ".agents/skills/ak5-live-test/SKILL.md",
    ".agents/rules/cli-nonblocking-rule.md",
]
_ROOT_OPTS = {"--json", "--help", "--version"}
# `ak5 ` not preceded by a word/path char; stops at a backtick or line end.
_EXAMPLE_RE = re.compile(r"(?<![\w/.\-\[])ak5 ([^`\n]+)")
_SPLIT_RE = re.compile(r"\s+(?:·|&&|;|→|\|\|)\s+|\s+\|\s+(?=ak5\b)")


def _doc_lines(text: str) -> list[str]:
    """Doc lines with table pipes unescaped; the v1 column of migration tables is dropped."""
    lines: list[str] = []
    in_v1_table = False
    # Join shell line continuations so options on later lines are checked too.
    text = re.sub(r"\\\n\s*", " ", text)
    for raw in text.splitlines():
        line = raw.replace("\\|", "|")
        stripped = line.strip()
        if stripped.startswith("| v1 |"):
            in_v1_table = True
            continue
        if in_v1_table:
            if not stripped.startswith("|"):
                in_v1_table = False
            else:
                cells = stripped.strip("|").split(" | ")
                line = " | ".join(cells[1:])
        lines.append(line)
    return lines


def _examples(text: str) -> list[str]:
    out: list[str] = []
    for m in (m for line in _doc_lines(text) for m in _EXAMPLE_RE.finditer(line)):
        line = m.group(1)
        line = re.split(r"\s#\s|\s{2,}#", line)[0]  # trailing shell comment
        for part in _SPLIT_RE.split(line):
            part = part.strip()
            if part.startswith("ak5 "):
                part = part[4:]
            if part:
                out.append(part)
    return out


def _option_names(cmd: click.Command) -> set[str]:
    names: set[str] = set()
    for p in cmd.params:
        if isinstance(p, click.Option):
            names.update(p.opts)
            names.update(p.secondary_opts)
    return names


def _check(example: str) -> list[str]:
    # Quoted strings are arguments (e.g. the --exec command), never ak5 options.
    unquoted = re.sub(r"'[^']*'|\"[^\"]*\"", " Q ", example)
    tokens = unquoted.replace("[", " ").replace("]", " ").split()
    cmds: list[click.Command] = [cli]
    errors: list[str] = []
    descending = True
    for tok in tokens:
        if not tok[0].isascii() or (
            tok.startswith(("'", '"', "<", "@", "$", "{", "(", "-x", "-e")) and not tok.startswith("--")
        ):
            descending = False
            continue
        pieces = [p.rstrip(",.:)") for p in re.split(r"[|/]", tok)]
        if tok.startswith("--"):
            for piece in pieces:
                name = piece.split("=", 1)[0].rstrip(",.:)")
                if not name.startswith("--") or name in _ROOT_OPTS:
                    continue
                if not any(name in _option_names(c) for c in cmds):
                    errors.append(f"unknown option {name!r} for `ak5 {example}`")
            continue
        if tok.startswith("-"):
            continue
        if not descending:
            continue
        group = cmds[-1]
        if not isinstance(group, click.Group):
            descending = False
            continue
        names = [p for p in pieces if p]
        if names and all(group.get_command(click.Context(group), n) for n in names):
            # Alternatives (claim|submit|…) all exist; keep checking options against all of them.
            cmds = [group.get_command(click.Context(group), n) for n in names]  # type: ignore[misc]
            if len(cmds) > 1:
                descending = False
        elif names and isinstance(group, click.Group) and group is not cli and not group.invoke_without_command:
            errors.append(f"unknown subcommand {tok!r} for `ak5 {example}`")
            descending = False
        else:
            descending = False
    return errors


def _all_examples() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for rel in DOCS:
        for ex in _examples((REPO_ROOT / rel).read_text(encoding="utf-8")):
            found.append((rel, ex))
    return found


def test_docs_have_cli_examples():
    assert len(_all_examples()) > 100


@pytest.mark.parametrize("rel", DOCS)
def test_doc_cli_examples_match_cli(rel: str):
    errors: list[str] = []
    for ex in _examples((REPO_ROOT / rel).read_text(encoding="utf-8")):
        errors.extend(_check(ex))
    assert not errors, f"{rel}:\n" + "\n".join(errors)


def test_checker_catches_v1_examples():
    """Guard the guard: v1-only flags and commands are reported."""
    assert _check("ticket create --title x --assign user_pm")
    assert _check("subscribe create proj --for-agent me --exec x")
    assert _check("ticket frobnicate TK-1")
    assert not _check("ticket claim TK-1 --caps py")
    assert not _check("ticket claim|heartbeat|release <id> --token T")
    assert not _check("subscribe add b --exec 'tool --not-an-ak5-flag'")
