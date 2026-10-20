from pathlib import Path

import pytest

from ideastatica_mcp.config import Settings
from ideastatica_mcp.service import ServiceError, ServiceManager, _tail_lines, find_free_port, is_reachable
from ideastatica_mcp.tools.common import to_expression


def _settings(tmp_path: Path, **overrides) -> Settings:
    s = Settings.from_env()
    s.log_dir = tmp_path / "logs"
    s.output_dir = tmp_path / "out"
    s.autostart = False
    s.api_url = "http://127.0.0.1:59999"
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def test_unreachable_attach_without_autostart_raises(tmp_path):
    manager = ServiceManager(_settings(tmp_path))
    assert manager.mode == "attached"
    assert manager.is_running() is False
    with pytest.raises(ServiceError, match="IDEASTATICA_AUTOSTART"):
        manager.ensure_running()
    status = manager.status()
    assert status["reachable"] is False and status["managed_process"]["running"] is False


def test_managed_without_executable_raises(tmp_path):
    manager = ServiceManager(_settings(tmp_path, api_url=None, autostart=True, setup_dir=tmp_path / "nowhere"))
    assert manager.mode == "managed"
    with pytest.raises(ServiceError, match="was not found"):
        manager.ensure_running()
    assert manager.stop() is False


def test_managed_without_autostart_raises(tmp_path):
    manager = ServiceManager(_settings(tmp_path, api_url=None, autostart=False))
    with pytest.raises(ServiceError, match="service_start"):
        manager.ensure_running()


def test_helpers(tmp_path):
    port = find_free_port()
    assert 1024 < port < 65536
    assert is_reachable(f"http://127.0.0.1:{port}", 0.5) is False
    log = tmp_path / "svc.log"
    log.write_text("useful line\nSerilog failure: noise\n   at System.Net.Http.Foo\nanother useful line\n", encoding="utf-8")
    assert _tail_lines(log) == ["useful line", "another useful line"]


def test_to_expression_avoids_exponent_notation():
    assert to_expression(1e-05) == "0.00001"
    assert to_expression(2.5e-3) == "0.0025"
    assert to_expression(1500000.0) == "1500000.0"
