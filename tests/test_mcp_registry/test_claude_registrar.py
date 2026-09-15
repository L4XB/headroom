"""Tests for the Claude Code MCP registrar."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from headroom.mcp_registry.base import RegisterStatus, ServerSpec
from headroom.mcp_registry.claude import ClaudeRegistrar
from headroom.mcp_registry.install import build_headroom_spec

_RESOLVED_COMMAND = ("/usr/bin/python", "-m", "headroom.cli")
_RESOLVED_ARGS = ("-m", "headroom.cli", "mcp", "serve")


def _make_registrar(
    tmp_path: Path,
    *,
    cli: str | None = "/usr/local/bin/claude",
) -> ClaudeRegistrar:
    """Build a registrar pointed at ``tmp_path`` as $HOME."""
    return ClaudeRegistrar(claude_cli=cli, home_dir=tmp_path)


def _spec() -> ServerSpec:
    return ServerSpec(
        name="headroom",
        command="/usr/bin/python",
        args=("-m", "headroom.cli", "mcp", "serve"),
        env={},
    )


def _install_spec(monkeypatch: pytest.MonkeyPatch) -> ServerSpec:
    monkeypatch.setattr(
        "headroom.mcp_registry.install.resolve_headroom_command",
        lambda: list(_RESOLVED_COMMAND),
    )
    return build_headroom_spec()


# ----------------------------------------------------------------------
# detect()
# ----------------------------------------------------------------------


def test_detect_true_when_cli_present(tmp_path: Path) -> None:
    reg = _make_registrar(tmp_path, cli="/usr/local/bin/claude")
    assert reg.detect() is True


def test_detect_true_when_only_claude_dir_exists(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    reg = _make_registrar(tmp_path, cli=None)
    assert reg.detect() is True


def test_detect_true_when_only_modern_config_exists(tmp_path: Path) -> None:
    (tmp_path / ".claude.json").write_text("{}")
    reg = _make_registrar(tmp_path, cli=None)
    assert reg.detect() is True


def test_detect_false_when_neither_present(tmp_path: Path) -> None:
    reg = _make_registrar(tmp_path, cli=None)
    assert reg.detect() is False


# ----------------------------------------------------------------------
# get_server() — file-based reads
# ----------------------------------------------------------------------


def test_get_server_returns_none_when_unregistered(tmp_path: Path) -> None:
    reg = _make_registrar(tmp_path, cli=None)
    assert reg.get_server("headroom") is None


def test_get_server_reads_modern_config(tmp_path: Path) -> None:
    cfg = tmp_path / ".claude.json"
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "headroom": {
                        "command": _RESOLVED_COMMAND[0],
                        "args": list(_RESOLVED_ARGS),
                        "env": {"HEADROOM_PROXY_URL": "http://127.0.0.1:9000"},
                    }
                }
            }
        )
    )
    reg = _make_registrar(tmp_path, cli=None)
    got = reg.get_server("headroom")
    assert got is not None
    assert got.command == _RESOLVED_COMMAND[0]
    assert got.args == _RESOLVED_ARGS
    assert got.env == {"HEADROOM_PROXY_URL": "http://127.0.0.1:9000"}


def test_get_server_falls_back_to_legacy(tmp_path: Path) -> None:
    cfg = tmp_path / ".claude" / "mcp.json"
    cfg.parent.mkdir()
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "headroom": {
                        "command": _RESOLVED_COMMAND[0],
                        "args": list(_RESOLVED_ARGS),
                    }
                }
            }
        )
    )
    reg = _make_registrar(tmp_path, cli=None)
    got = reg.get_server("headroom")
    assert got is not None
    assert got.command == _RESOLVED_COMMAND[0]
    assert got.args == _RESOLVED_ARGS
    assert got.env == {}


def test_get_server_reads_claude_config_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / ".claude.json"
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "headroom": {
                        "command": _RESOLVED_COMMAND[0],
                        "args": list(_RESOLVED_ARGS),
                    }
                }
            }
        )
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    reg = ClaudeRegistrar(claude_cli=None)
    got = reg.get_server("headroom")
    assert got is not None
    assert got.command == _RESOLVED_COMMAND[0]
    assert got.args == _RESOLVED_ARGS


# ----------------------------------------------------------------------
# register_server() — happy paths
# ----------------------------------------------------------------------


def test_register_via_cli_calls_claude_mcp_add(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reg = _make_registrar(tmp_path, cli="/usr/local/bin/claude")
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=fake_result) as run_mock:
        result = reg.register_server(_install_spec(monkeypatch))
    assert result.status == RegisterStatus.REGISTERED
    add_call = run_mock.call_args
    assert add_call is not None
    add_cmd = add_call.args[0]
    assert add_cmd[:6] == [
        "/usr/local/bin/claude",
        "mcp",
        "add",
        "headroom",
        "-s",
        "user",
    ]
    assert add_cmd[-(len(_RESOLVED_ARGS) + 2) :] == [
        "--",
        _RESOLVED_COMMAND[0],
        *_RESOLVED_ARGS,
    ]
    assert add_call.kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(tmp_path)


def test_register_via_cli_includes_env(tmp_path: Path) -> None:
    spec = ServerSpec(
        name="headroom",
        command=_RESOLVED_COMMAND[0],
        args=_RESOLVED_ARGS,
        env={"HEADROOM_PROXY_URL": "http://127.0.0.1:9000"},
    )
    reg = _make_registrar(tmp_path, cli="/usr/local/bin/claude")
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=fake_result) as run_mock:
        reg.register_server(spec)
    add_call = run_mock.call_args
    assert add_call is not None
    add_cmd = add_call.args[0]
    assert "-e" in add_cmd
    e_idx = add_cmd.index("-e")
    assert add_cmd[e_idx + 1] == "HEADROOM_PROXY_URL=http://127.0.0.1:9000"
    assert add_call.kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(tmp_path)


def test_register_via_cli_without_overrides_keeps_ambient_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "ambient")
    reg = ClaudeRegistrar(claude_cli="/usr/local/bin/claude")
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=fake_result) as run_mock:
        reg.register_server(_spec())
    assert run_mock.call_args is not None
    assert run_mock.call_args.kwargs["env"] is None


def test_register_via_cli_prefers_explicit_config_dir_over_ambient_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "explicit-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "ambient")
    reg = ClaudeRegistrar(claude_cli="/usr/local/bin/claude", config_dir=config_dir)
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=fake_result) as run_mock:
        reg.register_server(_spec())
    assert run_mock.call_args is not None
    assert run_mock.call_args.kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(config_dir)


def test_register_writes_file_when_no_cli(tmp_path: Path) -> None:
    reg = _make_registrar(tmp_path, cli=None)
    result = reg.register_server(_spec())
    assert result.status == RegisterStatus.REGISTERED
    cfg = tmp_path / ".claude.json"
    data = json.loads(cfg.read_text())
    assert "headroom" in data["mcpServers"]
    assert data["mcpServers"]["headroom"]["command"] == _RESOLVED_COMMAND[0]
    assert data["mcpServers"]["headroom"]["args"] == list(_RESOLVED_ARGS)


def test_register_writes_to_legacy_when_only_legacy_exists(tmp_path: Path) -> None:
    legacy = tmp_path / ".claude" / "mcp.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"mcpServers": {}}))
    reg = _make_registrar(tmp_path, cli=None)
    result = reg.register_server(_spec())
    assert result.status == RegisterStatus.REGISTERED
    data = json.loads(legacy.read_text())
    assert "headroom" in data["mcpServers"]
    # Modern config should NOT have been created.
    assert not (tmp_path / ".claude.json").exists()


def test_register_writes_to_claude_config_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    reg = ClaudeRegistrar(claude_cli=None)
    result = reg.register_server(_spec())
    assert result.status == RegisterStatus.REGISTERED
    cfg = tmp_path / ".claude.json"
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["headroom"]["command"] == _RESOLVED_COMMAND[0]
    assert not (tmp_path / ".claude" / ".claude.json").exists()


# ----------------------------------------------------------------------
# register_server() — already / mismatch / force
# ----------------------------------------------------------------------


def test_register_already_when_spec_matches(tmp_path: Path) -> None:
    cfg = tmp_path / ".claude.json"
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "headroom": {
                        "command": _RESOLVED_COMMAND[0],
                        "args": list(_RESOLVED_ARGS),
                    }
                }
            }
        )
    )
    reg = _make_registrar(tmp_path, cli="/usr/local/bin/claude")
    with patch("subprocess.run") as run_mock:
        result = reg.register_server(_spec())
    assert result.status == RegisterStatus.ALREADY
    run_mock.assert_not_called()  # should not touch CLI when already matching


def test_register_mismatch_when_spec_differs_no_force(tmp_path: Path) -> None:
    cfg = tmp_path / ".claude.json"
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "headroom": {
                        "command": _RESOLVED_COMMAND[0],
                        "args": list(_RESOLVED_ARGS),
                        "env": {"HEADROOM_PROXY_URL": "http://127.0.0.1:9999"},
                    }
                }
            }
        )
    )
    reg = _make_registrar(tmp_path, cli="/usr/local/bin/claude")
    with patch("subprocess.run") as run_mock:
        result = reg.register_server(_spec())  # default proxy = no env
    assert result.status == RegisterStatus.MISMATCH
    assert "env" in (result.detail or "")
    run_mock.assert_not_called()  # do NOT overwrite without force


def test_register_force_overwrites_mismatch(tmp_path: Path) -> None:
    cfg = tmp_path / ".claude.json"
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "headroom": {
                        "command": "headroom-old",
                        "args": ["mcp", "serve"],
                    }
                }
            }
        )
    )
    reg = _make_registrar(tmp_path, cli="/usr/local/bin/claude")
    fake_ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=fake_ok) as run_mock:
        result = reg.register_server(_spec(), force=True)
    assert result.status == RegisterStatus.REGISTERED
    cmds = [call.args[0] for call in run_mock.call_args_list]
    assert any("remove" in c for c in cmds)
    assert any("add" in c for c in cmds)


# ----------------------------------------------------------------------
# CLI failure paths
# ----------------------------------------------------------------------


def test_register_cli_failure_falls_back_to_file(tmp_path: Path) -> None:
    reg = _make_registrar(tmp_path, cli="/usr/local/bin/claude")
    fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="claude: error")
    with patch("subprocess.run", return_value=fail):
        result = reg.register_server(_spec())
    # Even though CLI failed, we wrote the config file as a fallback.
    assert result.status == RegisterStatus.REGISTERED
    cfg = tmp_path / ".claude.json"
    assert cfg.exists()
    data = json.loads(cfg.read_text())
    assert "headroom" in data["mcpServers"]


# ----------------------------------------------------------------------
# unregister
# ----------------------------------------------------------------------


def test_unregister_via_cli(tmp_path: Path) -> None:
    reg = _make_registrar(tmp_path, cli="/usr/local/bin/claude")
    ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=ok) as run_mock:
        assert reg.unregister_server("headroom") is True
    assert run_mock.call_args is not None
    cmd = run_mock.call_args.args[0]
    assert cmd[:5] == ["/usr/local/bin/claude", "mcp", "remove", "headroom", "-s"]
    assert cmd[5] == "user"
    assert run_mock.call_args.kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(tmp_path)


def test_unregister_via_file_when_no_cli(tmp_path: Path) -> None:
    cfg = tmp_path / ".claude.json"
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "headroom": {"command": _RESOLVED_COMMAND[0], "args": list(_RESOLVED_ARGS)},
                    "other": {"command": "other"},
                }
            }
        )
    )
    reg = _make_registrar(tmp_path, cli=None)
    assert reg.unregister_server("headroom") is True
    data = json.loads(cfg.read_text())
    assert "headroom" not in data["mcpServers"]
    assert "other" in data["mcpServers"]


def test_unregister_via_cli_also_removes_stale_legacy_entry(tmp_path: Path) -> None:
    legacy = tmp_path / ".claude" / "mcp.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"mcpServers": {"headroom": {"command": "old"}}}))
    reg = _make_registrar(tmp_path, cli="/usr/local/bin/claude")
    ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=ok):
        assert reg.unregister_server("headroom") is True
    data = json.loads(legacy.read_text())
    assert "headroom" not in data["mcpServers"]


def test_unregister_returns_false_when_absent(tmp_path: Path) -> None:
    reg = _make_registrar(tmp_path, cli=None)
    assert reg.unregister_server("headroom") is False


def test_unregister_removes_from_claude_config_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / ".claude.json"
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "headroom": {"command": _RESOLVED_COMMAND[0], "args": list(_RESOLVED_ARGS)},
                    "other": {"command": "other"},
                }
            }
        )
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    reg = ClaudeRegistrar(claude_cli=None)
    assert reg.unregister_server("headroom") is True
    data = json.loads(cfg.read_text())
    assert "headroom" not in data["mcpServers"]
    assert "other" in data["mcpServers"]


# ----------------------------------------------------------------------
# Robustness: bad JSON should not crash
# ----------------------------------------------------------------------


@pytest.mark.parametrize("contents", ["", "not json", "{", "[]"])
def test_get_server_robust_to_bad_json(tmp_path: Path, contents: str) -> None:
    cfg = tmp_path / ".claude.json"
    cfg.write_text(contents)
    reg = _make_registrar(tmp_path, cli=None)
    assert reg.get_server("headroom") is None


@pytest.mark.parametrize("mcp_servers", ["null", "[]", '"oops"'])
def test_get_server_robust_to_non_dict_mcp_servers(tmp_path: Path, mcp_servers: str) -> None:
    cfg = tmp_path / ".claude.json"
    cfg.write_text(f'{{"mcpServers": {mcp_servers}}}')
    reg = _make_registrar(tmp_path, cli=None)
    assert reg.get_server("headroom") is None


@pytest.mark.parametrize("mcp_servers", ["null", "[]", '"oops"'])
def test_unregister_robust_to_non_dict_mcp_servers(tmp_path: Path, mcp_servers: str) -> None:
    cfg = tmp_path / ".claude.json"
    cfg.write_text(f'{{"mcpServers": {mcp_servers}}}')
    reg = _make_registrar(tmp_path, cli=None)
    assert reg.unregister_server("headroom") is False


@pytest.mark.parametrize("mcp_servers", ["null", "[]", '"oops"'])
def test_register_robust_to_non_dict_mcp_servers(tmp_path: Path, mcp_servers: str) -> None:
    cfg = tmp_path / ".claude.json"
    cfg.write_text(f'{{"mcpServers": {mcp_servers}}}')
    reg = _make_registrar(tmp_path, cli=None)
    result = reg.register_server(_spec())
    assert result.status == RegisterStatus.REGISTERED
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["headroom"]["command"] == _RESOLVED_COMMAND[0]


@pytest.mark.parametrize("contents", ["not json", "{", '{"projects": }', "[]"])
def test_register_via_file_preserves_malformed_config(tmp_path: Path, contents: str) -> None:
    """Registering must NOT clobber an existing but unparseable config.

    ~/.claude.json holds unrelated Claude state (projects, oauthAccount,
    session history). Before the fix a malformed file was read as {} and then
    overwritten with only {"mcpServers": ...}, destroying everything else."""
    cfg = tmp_path / ".claude.json"
    cfg.write_text(contents, encoding="utf-8")
    reg = _make_registrar(tmp_path, cli=None)

    result = reg.register_server(_spec())

    assert result.status == RegisterStatus.FAILED
    assert "not valid JSON" in result.detail
    # The original bytes are untouched — nothing was overwritten.
    assert cfg.read_text(encoding="utf-8") == contents


def test_register_via_file_merges_into_existing_valid_config(tmp_path: Path) -> None:
    """The happy path still merges: unrelated keys are preserved and mcpServers
    gains the headroom entry."""
    cfg = tmp_path / ".claude.json"
    cfg.write_text(
        json.dumps({"projects": {"/x": {"y": 1}}, "oauthAccount": {"id": "abc"}}),
        encoding="utf-8",
    )
    reg = _make_registrar(tmp_path, cli=None)

    result = reg.register_server(_spec())

    assert result.status == RegisterStatus.REGISTERED
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["projects"] == {"/x": {"y": 1}}
    assert data["oauthAccount"] == {"id": "abc"}
    assert "headroom" in data["mcpServers"]


# ---------------------------------------------------------------------------
# Plugin-provided MCP servers (#3570)
# ---------------------------------------------------------------------------


def _install_plugin(
    home: Path,
    plugin_key: str,
    servers: dict[str, dict] | None,
    *,
    version: str = "1.0.0",
) -> Path:
    """Install a plugin the way Claude Code lays one out on disk.

    ``servers=None`` installs a plugin that ships no ``.mcp.json`` at all,
    which is the common case and must not be mistaken for one that does.
    """
    name, _, marketplace = plugin_key.partition("@")
    install_path = home / ".claude" / "plugins" / "cache" / marketplace / name / version
    install_path.mkdir(parents=True, exist_ok=True)
    if servers is not None:
        (install_path / ".mcp.json").write_text(json.dumps(servers), encoding="utf-8")

    registry_path = home / ".claude" / "plugins" / "installed_plugins.json"
    registry = (
        json.loads(registry_path.read_text())
        if registry_path.exists()
        else {
            "version": 2,
            "plugins": {},
        }
    )
    registry["plugins"].setdefault(plugin_key, []).append(
        {"scope": "user", "installPath": str(install_path), "version": version}
    )
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    return install_path


_PLUGIN_SERENA = {
    "serena": {
        "command": "uvx",
        "args": ["--from", "git+https://github.com/oraios/serena", "serena", "start-mcp-server"],
    }
}


def test_plugin_provided_server_is_found(tmp_path: Path) -> None:
    """The whole point: this server is invisible to ``get_server``.

    It lives in the plugin's own ``.mcp.json``, never in ``mcpServers``, so
    every check built on ``get_server`` reports a clean configuration while a
    second Serena runs beside Headroom's.
    """
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    registrar = _make_registrar(tmp_path)

    assert registrar.get_server("serena") is None

    found = registrar.get_plugin_servers("serena")
    assert len(found) == 1
    assert found[0].plugin == "serena@claude-plugins-official"
    assert found[0].spec.command == "uvx"
    assert "git+https://github.com/oraios/serena" in found[0].spec.args
    assert found[0].disable_command == "claude plugin disable serena@claude-plugins-official"


def test_a_plugin_that_ships_no_mcp_server_is_not_reported(tmp_path: Path) -> None:
    """Most plugins have no ``.mcp.json``; a missing file is not a finding."""
    _install_plugin(tmp_path, "gopls-lsp@claude-plugins-official", None)
    registrar = _make_registrar(tmp_path)

    assert registrar.get_plugin_servers("serena") == []


def test_a_plugin_shipping_a_different_server_is_not_reported(tmp_path: Path) -> None:
    """Name-matched, not plugin-matched: a plugin with some other MCP server
    is an ordinary installation, not a duplicate Serena."""
    _install_plugin(tmp_path, "context7@claude-plugins-official", {"context7": {"command": "npx"}})
    registrar = _make_registrar(tmp_path)

    assert registrar.get_plugin_servers("serena") == []


def test_no_plugins_installed_reports_nothing(tmp_path: Path) -> None:
    registrar = _make_registrar(tmp_path)
    assert registrar.get_plugin_servers("serena") == []


def test_a_malformed_plugin_registry_is_not_fatal(tmp_path: Path) -> None:
    """A registry Headroom cannot parse is not a reason to fail the command
    it was only trying to annotate."""
    registry_path = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("{not json", encoding="utf-8")

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def test_a_malformed_plugin_manifest_is_skipped(tmp_path: Path) -> None:
    install_path = _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    (install_path / ".mcp.json").write_text("[]", encoding="utf-8")

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def test_one_plugin_installed_at_two_scopes_is_reported_once(tmp_path: Path) -> None:
    """Claude records a user-scope and a project-scope install separately, but
    they can share an install path -- and there is still only one server."""
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    registry_path = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    registry = json.loads(registry_path.read_text())
    entry = dict(registry["plugins"]["serena@claude-plugins-official"][0])
    entry["scope"] = "project"
    registry["plugins"]["serena@claude-plugins-official"].append(entry)
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    assert len(_make_registrar(tmp_path).get_plugin_servers("serena")) == 1


def _set_plugin_enabled(home: Path, plugin_key: str, enabled: bool) -> None:
    """Write what ``claude plugin enable/disable <key>`` writes."""
    settings_path = home / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    settings.setdefault("enabledPlugins", {})[plugin_key] = enabled
    settings_path.write_text(json.dumps(settings), encoding="utf-8")


def test_a_disabled_plugin_is_not_reported(tmp_path: Path) -> None:
    """``claude plugin disable`` -- the command this warning recommends -- leaves
    the install record in place and only flips ``enabledPlugins``. Reporting it
    anyway would keep warning right after the user did what was asked."""
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    _set_plugin_enabled(tmp_path, "serena@claude-plugins-official", False)

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def test_an_explicitly_enabled_plugin_is_reported(tmp_path: Path) -> None:
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    _set_plugin_enabled(tmp_path, "serena@claude-plugins-official", True)

    assert len(_make_registrar(tmp_path).get_plugin_servers("serena")) == 1


def test_a_plugin_with_no_enabled_entry_is_still_reported(tmp_path: Path) -> None:
    """Only an explicit ``false`` means off. Staying quiet about a plugin that
    Claude may well be running is the worse of the two errors for a detector."""
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"gopls-lsp@claude-plugins-official": True}}),
        encoding="utf-8",
    )

    assert len(_make_registrar(tmp_path).get_plugin_servers("serena")) == 1


def test_a_disabled_plugin_does_not_silence_a_different_one(tmp_path: Path) -> None:
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    _install_plugin(tmp_path, "serena@someone-else", _PLUGIN_SERENA)
    _set_plugin_enabled(tmp_path, "serena@claude-plugins-official", False)

    found = _make_registrar(tmp_path).get_plugin_servers("serena")
    assert [f.plugin for f in found] == ["serena@someone-else"]


def test_a_wrapped_manifest_is_read_too(tmp_path: Path) -> None:
    """Both shapes are in the wild: of the 14 manifests in the
    ``claude-plugins-official`` marketplace, 9 are flat and 5 wrap the map in
    ``mcpServers``. Reading only the flat one misses over a third of them."""
    _install_plugin(tmp_path, "serena@claude-plugins-official", {"mcpServers": _PLUGIN_SERENA})

    found = _make_registrar(tmp_path).get_plugin_servers("serena")
    assert len(found) == 1
    assert found[0].spec.command == "uvx"


def test_a_wrapped_manifest_of_another_server_is_not_reported(tmp_path: Path) -> None:
    _install_plugin(
        tmp_path,
        "gopls-lsp@claude-plugins-official",
        {"mcpServers": {"gopls": {"command": "gopls"}}},
    )

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def _make_project_scope(home: Path, project_path: Path, scope: str = "local") -> None:
    """Rewrite the single install record as a project-scope one."""
    registry_path = home / ".claude" / "plugins" / "installed_plugins.json"
    registry = json.loads(registry_path.read_text())
    record = registry["plugins"]["serena@claude-plugins-official"][0]
    record["scope"] = scope
    record["projectPath"] = str(project_path)
    registry_path.write_text(json.dumps(registry), encoding="utf-8")


def test_a_project_scope_install_is_reported_inside_its_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude launches it in that project, so staying quiet there is the false
    negative that matters -- it is exactly where the second Serena runs."""
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    project = tmp_path / "work" / "api"
    project.mkdir(parents=True)
    _make_project_scope(tmp_path, project)
    monkeypatch.chdir(project)

    assert len(_make_registrar(tmp_path).get_plugin_servers("serena")) == 1


def test_a_project_scope_install_is_reported_below_its_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    project = tmp_path / "work" / "api"
    nested = project / "src" / "deep"
    nested.mkdir(parents=True)
    _make_project_scope(tmp_path, project)
    monkeypatch.chdir(nested)

    assert len(_make_registrar(tmp_path).get_plugin_servers("serena")) == 1


def test_a_project_scope_install_is_quiet_outside_its_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    project = tmp_path / "work" / "api"
    project.mkdir(parents=True)
    elsewhere = tmp_path / "work" / "unrelated"
    elsewhere.mkdir(parents=True)
    _make_project_scope(tmp_path, project)
    monkeypatch.chdir(elsewhere)

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def test_a_sibling_directory_that_shares_a_prefix_is_not_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/work/api-v2` is not inside `/work/api`, however the strings compare."""
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    project = tmp_path / "work" / "api"
    project.mkdir(parents=True)
    sibling = tmp_path / "work" / "api-v2"
    sibling.mkdir(parents=True)
    _make_project_scope(tmp_path, project)
    monkeypatch.chdir(sibling)

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def test_a_project_scope_install_without_a_path_is_not_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record that cannot be placed must not be spoken for."""
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    registry_path = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    registry = json.loads(registry_path.read_text())
    registry["plugins"]["serena@claude-plugins-official"][0]["scope"] = "local"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def test_a_disabled_project_scope_install_is_not_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    project = tmp_path / "work" / "api"
    project.mkdir(parents=True)
    _make_project_scope(tmp_path, project)
    _set_plugin_enabled(tmp_path, "serena@claude-plugins-official", False)
    monkeypatch.chdir(project)

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def test_two_different_plugins_are_both_reported(tmp_path: Path) -> None:
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    _install_plugin(tmp_path, "serena@someone-else", _PLUGIN_SERENA)

    found = _make_registrar(tmp_path).get_plugin_servers("serena")
    assert sorted(f.plugin for f in found) == [
        "serena@claude-plugins-official",
        "serena@someone-else",
    ]


def test_detection_does_not_touch_the_plugin(tmp_path: Path) -> None:
    """Headroom must not mutate a third-party plugin -- the issue asks for a
    warning, not a removal."""
    install_path = _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    manifest = install_path / ".mcp.json"
    before = manifest.read_bytes()
    registry_path = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    registry_before = registry_path.read_bytes()

    _make_registrar(tmp_path).get_plugin_servers("serena")

    assert manifest.read_bytes() == before
    assert registry_path.read_bytes() == registry_before


def _set_dir_plugin_enabled(
    directory: Path, plugin_key: str, enabled: bool, *, local: bool = False
) -> None:
    """Write what ``claude plugin enable/disable`` writes inside a project.

    ``local=True`` targets ``settings.local.json`` -- the local scope Claude
    keeps out of version control -- instead of the shared ``settings.json``.
    """
    settings_dir = directory / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    name = "settings.local.json" if local else "settings.json"
    settings_path = settings_dir / name
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    settings.setdefault("enabledPlugins", {})[plugin_key] = enabled
    settings_path.write_text(json.dumps(settings), encoding="utf-8")


def _project_scope_install(tmp_path: Path, project: Path) -> None:
    """One project-scope Serena install rooted at ``project``."""
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    _make_project_scope(tmp_path, project)


def test_a_project_false_overrides_a_user_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The more specific file wins. Reading only the user settings warns right
    through a disable the user ran inside the project."""
    project = tmp_path / "work" / "api"
    project.mkdir(parents=True)
    _project_scope_install(tmp_path, project)
    _set_plugin_enabled(tmp_path, "serena@claude-plugins-official", True)
    _set_dir_plugin_enabled(project, "serena@claude-plugins-official", False)
    monkeypatch.chdir(project)

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def test_a_local_false_overrides_a_user_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "work" / "api"
    project.mkdir(parents=True)
    _project_scope_install(tmp_path, project)
    _set_plugin_enabled(tmp_path, "serena@claude-plugins-official", True)
    _set_dir_plugin_enabled(project, "serena@claude-plugins-official", False, local=True)
    monkeypatch.chdir(project)

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def test_a_project_true_overrides_a_user_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction: turned off globally, back on in this project, so
    Claude launches it here and the duplicate is real."""
    project = tmp_path / "work" / "api"
    project.mkdir(parents=True)
    _project_scope_install(tmp_path, project)
    _set_plugin_enabled(tmp_path, "serena@claude-plugins-official", False)
    _set_dir_plugin_enabled(project, "serena@claude-plugins-official", True)
    monkeypatch.chdir(project)

    assert len(_make_registrar(tmp_path).get_plugin_servers("serena")) == 1


def test_a_local_true_overrides_a_user_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "work" / "api"
    project.mkdir(parents=True)
    _project_scope_install(tmp_path, project)
    _set_plugin_enabled(tmp_path, "serena@claude-plugins-official", False)
    _set_dir_plugin_enabled(project, "serena@claude-plugins-official", True, local=True)
    monkeypatch.chdir(project)

    assert len(_make_registrar(tmp_path).get_plugin_servers("serena")) == 1


def test_local_settings_override_project_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Within one directory, ``settings.local.json`` is the more specific of
    the two."""
    project = tmp_path / "work" / "api"
    project.mkdir(parents=True)
    _project_scope_install(tmp_path, project)
    _set_dir_plugin_enabled(project, "serena@claude-plugins-official", True)
    _set_dir_plugin_enabled(project, "serena@claude-plugins-official", False, local=True)
    monkeypatch.chdir(project)

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def test_a_project_disable_applies_below_the_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The layer belongs to the record's own project, not to the working
    directory alone -- running from a subdirectory must not resurrect it."""
    project = tmp_path / "work" / "api"
    nested = project / "src" / "deep"
    nested.mkdir(parents=True)
    _project_scope_install(tmp_path, project)
    _set_dir_plugin_enabled(project, "serena@claude-plugins-official", False)
    monkeypatch.chdir(nested)

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def test_a_project_setting_does_not_apply_outside_that_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user-scope install stays reported elsewhere, whatever some unrelated
    project's settings say about it."""
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    project = tmp_path / "work" / "api"
    project.mkdir(parents=True)
    elsewhere = tmp_path / "work" / "unrelated"
    elsewhere.mkdir(parents=True)
    _set_dir_plugin_enabled(project, "serena@claude-plugins-official", False)
    monkeypatch.chdir(elsewhere)

    assert len(_make_registrar(tmp_path).get_plugin_servers("serena")) == 1


def test_a_cwd_disable_silences_a_user_scope_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user-scope plugin turned off in this project is not launched here."""
    _install_plugin(tmp_path, "serena@claude-plugins-official", _PLUGIN_SERENA)
    project = tmp_path / "work" / "api"
    project.mkdir(parents=True)
    _set_dir_plugin_enabled(project, "serena@claude-plugins-official", False)
    monkeypatch.chdir(project)

    assert _make_registrar(tmp_path).get_plugin_servers("serena") == []


def test_a_non_boolean_enabled_value_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude writes booleans; anything else is not a disable, and silencing a
    duplicate on a typo is the worse of the two errors. ``0`` is the case that
    separates dropping the value from coercing it -- ``bool(0)`` is ``False``,
    which would read as a disable nobody wrote."""
    project = tmp_path / "work" / "api"
    project.mkdir(parents=True)
    _project_scope_install(tmp_path, project)
    (project / ".claude").mkdir(parents=True, exist_ok=True)
    (project / ".claude" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"serena@claude-plugins-official": 0}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    assert len(_make_registrar(tmp_path).get_plugin_servers("serena")) == 1
