import sys

import pytest

from ideastatica_mcp import signin_guard
from ideastatica_mcp.config import Settings
from ideastatica_mcp.service import ServiceManager
from ideastatica_mcp.signin_guard import SignInGuard, is_signin_window

LICENSE = r"C:\Program Files\IDEA StatiCa\StatiCa 26.0\License.exe"


def test_matches_only_the_licence_signin_window():
    assert is_signin_window("Sign in - IDEA StatiCa", LICENSE)
    assert is_signin_window("Sign in - IDEA StatiCa", "LICENSE.EXE")
    assert not is_signin_window("Sign in - IDEA StatiCa", r"C:\Windows\explorer.exe")  # other process
    assert not is_signin_window("Sign in to Outlook", LICENSE)  # other title
    assert not is_signin_window("IDEA StatiCa", LICENSE)


def test_sweep_closes_matching_windows_and_logs(tmp_path, monkeypatch):
    windows = [
        (1, "Sign in - IDEA StatiCa", LICENSE),
        (2, "Sign in - Microsoft", r"C:\Program Files\Edge\msedge.exe"),
        (3, "Sign in - IDEA StatiCa", LICENSE),
    ]
    closed = []
    monkeypatch.setattr(signin_guard, "_list_top_windows", lambda: windows)
    monkeypatch.setattr(signin_guard, "_close_window", lambda hwnd: closed.append(hwnd) or True)
    guard = SignInGuard(log_file=tmp_path / "guard.log")
    assert guard.sweep() == 2
    assert closed == [1, 3]
    assert guard.status()["closed_windows"] == 2
    assert guard.status()["last_closed_at"] is not None
    assert (tmp_path / "guard.log").read_text(encoding="utf-8").count("closed 'Sign in - IDEA StatiCa'") == 2


def test_disabled_setting_never_starts_the_guard(tmp_path):
    settings = Settings.from_env()
    settings.log_dir = tmp_path / "logs"
    settings.api_url = "http://127.0.0.1:59999"
    settings.autostart = False
    settings.dismiss_signin = False
    manager = ServiceManager(settings)
    assert manager.ensure_signin_guard() is False
    assert manager.status()["signin_guard"] == {"enabled": False}


@pytest.mark.skipif(sys.platform != "win32", reason="the guard only runs on Windows")
def test_guard_start_is_idempotent(monkeypatch):
    monkeypatch.setattr(signin_guard, "_list_top_windows", lambda: [])
    guard = SignInGuard(interval=0.05)
    try:
        assert guard.start() is True
        first = guard._thread
        assert guard.start() is True and guard._thread is first and guard.running
    finally:
        guard.stop()
        first.join(1.0)
    assert not guard.running
