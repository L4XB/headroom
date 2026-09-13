"""`mcp reconcile` / `mcp status` must surface a plugin-provided Serena (#3570).

Headroom registers Serena itself, and the `claude-plugins-official` marketplace
ships a `serena` plugin. A user with both runs two Serena MCP servers, and every
check Headroom had was built on `mcpServers` -- which a plugin's server never
appears in -- so `reconcile` called the configuration consistent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from headroom.cli.main import main

_PLUGIN_SERENA = {
    "serena": {
        "command": "uvx",
        "args": ["--from", "git+https://github.com/oraios/serena", "serena", "start-mcp-server"],
    }
}


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home directory with a Claude install and nothing else."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".claude").mkdir()
    return tmp_path


def _install_serena_plugin(home: Path, plugin_key: str = "serena@claude-plugins-official") -> None:
    name, _, marketplace = plugin_key.partition("@")
    install_path = home / ".claude" / "plugins" / "cache" / marketplace / name / "1.0.0"
    install_path.mkdir(parents=True)
    (install_path / ".mcp.json").write_text(json.dumps(_PLUGIN_SERENA), encoding="utf-8")
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    plugin_key: [
                        {"scope": "user", "installPath": str(install_path), "version": "1.0.0"}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


def test_reconcile_reports_a_plugin_provided_serena(fake_home: Path) -> None:
    _install_serena_plugin(fake_home)

    result = CliRunner().invoke(main, ["mcp", "reconcile"])

    assert result.exit_code == 0, result.output
    assert "serena@claude-plugins-official" in result.output
    # The pointer, not a removal: the plugin is not Headroom's to change.
    assert "claude plugin disable serena@claude-plugins-official" in result.output


def test_reconcile_stays_quiet_without_a_plugin_serena(fake_home: Path) -> None:
    """The accept control. A warning that fires on every run is one users learn
    to skip, and most installs have no plugin Serena at all."""
    result = CliRunner().invoke(main, ["mcp", "reconcile"])

    assert result.exit_code == 0, result.output
    assert "plugin" not in result.output.lower()


def test_status_reports_a_plugin_provided_serena(fake_home: Path) -> None:
    _install_serena_plugin(fake_home)

    result = CliRunner().invoke(main, ["mcp", "status"])

    assert result.exit_code == 0, result.output
    assert "serena@claude-plugins-official" in result.output


def test_reconcile_does_not_remove_the_plugin(fake_home: Path) -> None:
    _install_serena_plugin(fake_home)
    manifest = next((fake_home / ".claude" / "plugins" / "cache").rglob(".mcp.json"))
    before = manifest.read_bytes()

    CliRunner().invoke(main, ["mcp", "reconcile"])

    assert manifest.exists()
    assert manifest.read_bytes() == before
