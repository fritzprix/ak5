"""``ak5 skill show|install`` — the agent skill bundled with this package."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

import click

SKILL_NAME = "ak5"
EXIT_SKIPPED = 3

# Project-relative (or home-relative with --global) skill directories per harness.
HARNESS_DIRS: dict[str, Path] = {
    "agents": Path(".agents") / "skills",
    "claude": Path(".claude") / "skills",
    "cursor": Path(".cursor") / "skills",
}


@dataclass(frozen=True)
class SkillFile:
    relpath: Path
    data: bytes


@dataclass(frozen=True)
class InstallResult:
    target: str
    path: Path
    status: str  # installed | updated | unchanged | skipped


def bundled_skill_root() -> Traversable:
    return files("ak5") / "skills" / SKILL_NAME


def bundled_skill_files() -> list[SkillFile]:
    out: list[SkillFile] = []

    def walk(node: Traversable, rel: Path) -> None:
        for child in node.iterdir():
            child_rel = rel / child.name
            if child.is_dir():
                walk(child, child_rel)
            elif child.is_file():
                out.append(SkillFile(child_rel, child.read_bytes()))

    walk(bundled_skill_root(), Path())
    return sorted(out, key=lambda f: f.relpath.as_posix())


def bundled_skill_md() -> bytes:
    return (bundled_skill_root() / "SKILL.md").read_bytes()


def skill_md_is_current(path: Path) -> bool:
    """Whether an installed ``SKILL.md`` matches the copy bundled with this CLI."""
    try:
        return path.read_bytes() == bundled_skill_md()
    except OSError:
        return False


def skill_dir(target: str, base: Path) -> Path:
    return base / HARNESS_DIRS[target] / SKILL_NAME


def installed_skill_paths(bases: list[Path]) -> list[Path]:
    """Existing ``<harness>/skills/ak5/SKILL.md`` files under each base directory."""
    found: list[Path] = []
    for base in bases:
        for target in HARNESS_DIRS:
            candidate = skill_dir(target, base) / "SKILL.md"
            if candidate.is_file() and candidate not in found:
                found.append(candidate)
    return found


def install_skill(dest: Path, target: str, *, force: bool) -> InstallResult:
    bundle = bundled_skill_files()
    existing = [dest / f.relpath for f in bundle if (dest / f.relpath).is_file()]
    changed = [f for f in bundle if not (dest / f.relpath).is_file() or (dest / f.relpath).read_bytes() != f.data]
    if not changed:
        return InstallResult(target, dest, "unchanged")
    if existing and not force:
        return InstallResult(target, dest, "skipped")
    for f in changed:
        path = dest / f.relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f.data)
        if path.suffix == ".sh":
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return InstallResult(target, dest, "updated" if existing else "installed")


@click.group("skill")
def skill_group() -> None:
    """Show or install the AK5 agent skill bundled with this CLI version.

    \b
    Agents:
      ak5 skill show                       # read the workflow (roles, loop, exit codes)
      ak5 skill install --harness claude   # save it where your harness loads skills
    """


@skill_group.command("show")
def skill_show() -> None:
    """Print the bundled SKILL.md to stdout."""
    # Bytes go to the binary buffer; the text stream would encode with the locale code page
    # (cp949/cp1252 on Windows), which cannot represent every character in SKILL.md.
    click.echo(bundled_skill_md(), nl=False)


@skill_group.command("install")
@click.option(
    "--harness",
    "harnesses",
    multiple=True,
    type=click.Choice(sorted(HARNESS_DIRS)),
    help="Skill directory to install into (repeatable): agents=.agents/skills, "
    "claude=.claude/skills, cursor=.cursor/skills. Default: agents.",
)
@click.option("--global", "use_home", is_flag=True, help="Install under your home directory instead of the current directory.")
@click.option(
    "--dir",
    "custom_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Install into <DIR>/ak5 instead of a harness directory.",
)
@click.option("--force", is_flag=True, help="Overwrite an existing ak5 skill that differs from this version.")
@click.pass_context
def skill_install(
    ctx: click.Context,
    harnesses: tuple[str, ...],
    use_home: bool,
    custom_dir: Path | None,
    force: bool,
) -> None:
    """Copy the bundled skill into your agent harness's skill directory.

    Exits 3 when an existing copy differs and --force was not given.
    """
    json_mode = bool(ctx.obj and ctx.obj.get("json"))
    base = Path.home() if use_home else Path.cwd()
    targets: list[tuple[str, Path]] = [(h, skill_dir(h, base)) for h in harnesses]
    if custom_dir is not None:
        targets.append(("dir", custom_dir / SKILL_NAME))
    if not targets:
        targets.append(("agents", skill_dir("agents", base)))

    results = [install_skill(dest, target, force=force) for target, dest in targets]
    if json_mode:
        click.echo(
            json.dumps(
                {"results": [{"target": r.target, "path": str(r.path), "status": r.status} for r in results]}
            )
        )
    else:
        for r in results:
            click.echo(f"{r.status:<9} {r.path}")
        if any(r.status == "skipped" for r in results):
            click.echo("An existing ak5 skill differs from this version; re-run with --force to overwrite.", err=True)
    if any(r.status == "skipped" for r in results):
        ctx.exit(EXIT_SKIPPED)
