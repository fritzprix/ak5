"""Tests for project-local multi-actor session and identity hint resolution."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest
from ak5.cli.commands.whoami import whoami_command
from ak5.cli.config import (
    clean_actor_id,
    find_project_root,
    get_actor_id,
    get_auth_headers,
    get_token,
    identity_path_for,
    list_identity_hints,
    resolve_session,
    save_session,
    session_path_for,
    write_identity_claim,
)
from click.testing import CliRunner


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AK5_PROJECT_ROOT", str(tmp_path))
    for key in ("AK5_ACTOR_ID", "AK5_ACTOR_TOKEN", "AK5_ACTOR_ROLE", "AK5_SESSION_FILE", "AK5_API_URL"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_find_project_root_prefers_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AK5_PROJECT_ROOT", str(tmp_path))
    assert find_project_root() == tmp_path.resolve()


def test_find_project_root_walks_to_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AK5_PROJECT_ROOT", raising=False)
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert find_project_root() == tmp_path.resolve()


def test_save_session_writes_session_and_identity_claim(project: Path) -> None:
    path = save_session(
        {
            "token": "jwt-a",
            "actor_id": "agent_reviewer",
            "role": "Senior Reviewer",
            "actor_type": "agent",
            "capabilities": ["code-review", "security"],
            "api_url": "http://127.0.0.1:8000/api/v1",
        },
        project_root=project,
    )
    assert path == session_path_for("agent_reviewer", project)
    assert path.exists()
    if sys.platform != "win32":
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600 or mode == 0o700  # some FS ignore bits; at least not world-readable preferred
        # Prefer owner-only when chmod succeeds
        assert not (mode & stat.S_IROTH)
    claim = identity_path_for("agent_reviewer", project)
    assert claim.exists()
    claim_data = json.loads(claim.read_text(encoding="utf-8"))
    assert claim_data["actor_id"] == "agent_reviewer"
    assert claim_data["role"] == "Senior Reviewer"
    assert "token" not in claim_data
    assert claim_data["capabilities"] == ["code-review", "security"]


def test_sole_claim_auto_binds(project: Path) -> None:
    save_session(
        {
            "token": "jwt-only",
            "actor_id": "solo_agent",
            "role": "Worker",
            "actor_type": "agent",
            "capabilities": ["impl"],
        },
        project_root=project,
    )
    resolved = resolve_session(project)
    assert resolved.source == "sole_claim"
    assert resolved.ambiguous is False
    assert resolved.data["actor_id"] == "solo_agent"
    assert get_actor_id() == "solo_agent"
    assert get_token() == "jwt-only"


def test_multiple_claims_are_ambiguous(project: Path) -> None:
    save_session(
        {"token": "t1", "actor_id": "agent_a", "role": "A", "actor_type": "agent", "capabilities": ["a"]},
        project_root=project,
    )
    save_session(
        {"token": "t2", "actor_id": "agent_b", "role": "B", "actor_type": "agent", "capabilities": ["b"]},
        project_root=project,
    )
    resolved = resolve_session(project)
    assert resolved.ambiguous is True
    assert resolved.source == "unbound"
    assert get_actor_id() is None
    assert get_token() is None
    hints = list_identity_hints(project)
    assert {h["actor_id"] for h in hints} == {"agent_a", "agent_b"}


def test_env_actor_id_selects_among_multiple(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_session(
        {"token": "t1", "actor_id": "agent_a", "role": "A", "actor_type": "agent", "capabilities": ["a"]},
        project_root=project,
    )
    save_session(
        {"token": "t2", "actor_id": "agent_b", "role": "B", "actor_type": "agent", "capabilities": ["b"]},
        project_root=project,
    )
    monkeypatch.setenv("AK5_ACTOR_ID", "agent_b")
    resolved = resolve_session(project)
    assert resolved.source == "env_actor"
    assert resolved.data["actor_id"] == "agent_b"
    assert get_token() == "t2"


def test_env_token_takes_precedence(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_session(
        {"token": "file-token", "actor_id": "agent_a", "role": "A", "actor_type": "agent"},
        project_root=project,
    )
    monkeypatch.setenv("AK5_ACTOR_TOKEN", "env-token")
    monkeypatch.setenv("AK5_ACTOR_ID", "from_env")
    monkeypatch.setenv("AK5_ACTOR_ROLE", "Env Role")
    resolved = resolve_session(project)
    assert resolved.source == "env_token"
    assert resolved.data["token"] == "env-token"
    assert resolved.data["actor_id"] == "from_env"
    assert get_token() == "env-token"


def test_ak5_session_file_env(project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom_session.json"
    custom.write_text(
        json.dumps(
            {
                "token": "custom-jwt",
                "actor_id": "custom_actor",
                "role": "Custom",
                "actor_type": "agent",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AK5_SESSION_FILE", str(custom))
    resolved = resolve_session(project)
    assert resolved.source == "session_file"
    assert resolved.data["actor_id"] == "custom_actor"
    assert get_token() == "custom-jwt"


def test_migrate_legacy_home_session(project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    legacy = tmp_path / "legacy_home_session.json"
    legacy.write_text(
        json.dumps(
            {
                "token": "legacy-jwt",
                "actor_id": "legacy_agent",
                "role": "Legacy Role",
                "actor_type": "agent",
                "api_url": "http://127.0.0.1:8000/api/v1",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("ak5.cli.config.LEGACY_SESSION_FILE", legacy)
    resolved = resolve_session(project)
    assert resolved.source == "legacy"
    assert resolved.data["actor_id"] == "legacy_agent"
    assert session_path_for("legacy_agent", project).exists()
    assert identity_path_for("legacy_agent", project).exists()
    assert get_token() == "legacy-jwt"


def test_whoami_exits_ambiguous_with_hints(project: Path) -> None:
    save_session(
        {"token": "t1", "actor_id": "agent_a", "role": "Reviewer", "actor_type": "agent", "capabilities": ["review"]},
        project_root=project,
    )
    save_session(
        {"token": "t2", "actor_id": "agent_b", "role": "Worker", "actor_type": "agent", "capabilities": ["impl"]},
        project_root=project,
    )
    runner = CliRunner()
    result = runner.invoke(whoami_command)
    assert result.exit_code == 1
    assert "Ambiguous" in result.output
    assert "@agent_a" in result.output
    assert "@agent_b" in result.output
    assert "AK5_ACTOR_ID" in result.output


def test_whoami_env_actor_without_session_file(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AK5_ACTOR_ID", "missing_agent")
    runner = CliRunner()
    result = runner.invoke(whoami_command)
    assert result.exit_code == 1
    assert "missing_agent" in result.output
    assert "no session file" in result.output.lower() or "login" in result.output.lower()


def test_clean_actor_id_strips_at_prefix() -> None:
    assert clean_actor_id("@agent-coder") == "agent-coder"
    assert clean_actor_id("  @UUID-Case  ") == "UUID-Case"
    assert clean_actor_id("plain") == "plain"


def test_resolve_session_strips_at_from_env_actor(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_session(
        {
            "token": "jwt",
            "actor_id": "b15fdf3e-6192-453d-9dc2-7b8ae6dab321",
            "role": "Coding Expert",
            "actor_type": "agent",
            "capabilities": ["coding"],
        },
        project_root=project,
    )
    monkeypatch.setenv("AK5_ACTOR_ID", "@b15fdf3e-6192-453d-9dc2-7b8ae6dab321")
    resolved = resolve_session()
    assert resolved.data["actor_id"] == "b15fdf3e-6192-453d-9dc2-7b8ae6dab321"
    assert resolved.source == "env_actor"
    assert get_token() == "jwt"


def test_whoami_shows_plain_actor_id_for_export(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_session(
        {
            "token": "jwt",
            "actor_id": "agent_coder",
            "role": "Coder",
            "actor_type": "agent",
            "capabilities": ["python"],
        },
        project_root=project,
    )
    monkeypatch.setenv("AK5_ACTOR_ID", "agent_coder")
    runner = CliRunner()
    result = runner.invoke(whoami_command)
    assert result.exit_code == 0
    assert "Actor ID" in result.output
    assert "agent_coder" in result.output
    assert "export AK5_ACTOR_ID=agent_coder" in result.output
    assert "subscribe create" in result.output


def test_write_claim_false_skips_identity(project: Path) -> None:
    save_session(
        {
            "token": "fallback-jwt",
            "actor_id": "cli_user",
            "role": "PM",
            "actor_type": "human",
            "capabilities": [],
        },
        project_root=project,
        write_claim=False,
    )
    assert session_path_for("cli_user", project).exists()
    assert not identity_path_for("cli_user", project).exists()
    assert list_identity_hints(project) == []


def test_get_auth_headers_skips_when_ambiguous(project: Path) -> None:
    save_session(
        {"token": "t1", "actor_id": "agent_a", "role": "A", "actor_type": "agent", "capabilities": ["a"]},
        project_root=project,
    )
    save_session(
        {"token": "t2", "actor_id": "agent_b", "role": "B", "actor_type": "agent", "capabilities": ["b"]},
        project_root=project,
    )
    assert get_auth_headers() == {}


def test_cli_user_token_reused_without_claim(
    project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("ak5.cli.config.LEGACY_SESSION_FILE", tmp_path / "no-legacy.json")
    save_session(
        {
            "token": "cli-jwt",
            "actor_id": "cli_user",
            "role": "PM",
            "actor_type": "human",
            "capabilities": [],
        },
        project_root=project,
        write_claim=False,
    )
    headers = get_auth_headers()
    assert headers.get("Authorization") == "Bearer cli-jwt"
    assert list_identity_hints(project) == []


def test_migrate_not_blocked_by_cli_user_only(
    project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    save_session(
        {"token": "cli-jwt", "actor_id": "cli_user", "role": "PM", "actor_type": "human"},
        project_root=project,
        write_claim=False,
    )
    legacy = tmp_path / "legacy_home_session.json"
    legacy.write_text(
        json.dumps(
            {
                "token": "legacy-jwt",
                "actor_id": "legacy_agent",
                "role": "Legacy Role",
                "actor_type": "agent",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("ak5.cli.config.LEGACY_SESSION_FILE", legacy)
    resolved = resolve_session(project)
    assert resolved.source == "legacy"
    assert resolved.data["actor_id"] == "legacy_agent"


def test_bound_without_token_no_cli_user_fallback(
    project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("ak5.cli.config.LEGACY_SESSION_FILE", tmp_path / "no-legacy.json")
    write_identity_claim(
        actor_id="bound_agent",
        role="Worker",
        actor_type="agent",
        capabilities=["x"],
        project_root=project,
    )
    # sole claim exists but no session JWT
    assert get_auth_headers() == {}
    monkeypatch.setenv("AK5_ACTOR_ID", "bound_agent")
    assert get_auth_headers() == {}


def test_ensure_ak5_gitignore(project: Path) -> None:
    from ak5.cli.config import ensure_ak5_dir

    d = ensure_ak5_dir(project)
    assert (d / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_require_auth_headers_exits_when_ambiguous(project: Path) -> None:
    from ak5.cli.config import require_auth_headers

    save_session(
        {"token": "t1", "actor_id": "agent_a", "role": "A", "actor_type": "agent", "capabilities": ["a"]},
        project_root=project,
    )
    save_session(
        {"token": "t2", "actor_id": "agent_b", "role": "B", "actor_type": "agent", "capabilities": ["b"]},
        project_root=project,
    )
    with pytest.raises(SystemExit) as exc:
        require_auth_headers()
    assert exc.value.code == 1


def test_clear_session_removes_claim(project: Path) -> None:
    from ak5.cli.config import clear_session

    save_session(
        {"token": "t1", "actor_id": "agent_a", "role": "A", "actor_type": "agent", "capabilities": ["a"]},
        project_root=project,
    )
    removed = clear_session("agent_a", project_root=project)
    assert removed == ["agent_a"]
    assert list_identity_hints(project) == []
    assert not session_path_for("agent_a", project).exists()


def test_write_identity_claim_lists_as_hint(project: Path) -> None:
    write_identity_claim(
        actor_id="hint_only",
        role="Hint Role",
        actor_type="agent",
        capabilities=["x"],
        project_root=project,
    )
    hints = list_identity_hints(project)
    assert len(hints) == 1
    assert hints[0]["actor_id"] == "hint_only"
    assert hints[0]["role"] == "Hint Role"
