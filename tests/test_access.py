from __future__ import annotations

import socket
from datetime import date

from anyio import Path
import pytest

from ibl_ai_agent.core import access


class DummyONE:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def search(self, **kwargs):
        return (
            ["eid-1", "eid-2"],
            [
                {
                    "id": "eid-1",
                    "subject": "SWC_001",
                    "lab": "cortexlab",
                    "start_time": "2022-02-01T12:00:00",
                    "task_protocol": "biased",
                },
                {
                    "id": "eid-2",
                    "subject": "SWC_002",
                    "lab": "hoferlab",
                    "start_time": "2022-02-02T12:00:00",
                    "task_protocol": "biased",
                },
            ],
        )


def test_private_non_interactive_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IBL_ALYX_USERNAME", raising=False)
    monkeypatch.delenv("IBL_ALYX_PASSWORD", raising=False)
    with pytest.raises(access.AccessError):
        access.connect_one(mode="private", interactive=False)


def test_public_defaults_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(access, "_import_one", lambda: DummyONE)
    one, status = access.connect_one(mode=access.AccessMode.public, interactive=False)
    assert status.connected
    assert status.mode == access.AccessMode.public
    assert one.kwargs["username"] == access.PUBLIC_DEFAULT_USERNAME

def test_connect_one_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBL_AGENT_DATA_OFFLINE", "1")
    monkeypatch.setattr(access, "_import_one", lambda: DummyONE)
    with pytest.raises(access.OfflineModeError):
        access.connect_one(mode=access.AccessMode.public, interactive=False)

def test_require_online_without_flag() -> None:
    access.require_online()

def test_require_online_when_flag_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBL_AGENT_DATA_OFFLINE", "0")
    access.require_online()

def test_offline_mode_error_is_access_error() -> None:
    assert issubclass(access.OfflineModeError, access.AccessError)

def test_recommended_offline_path_makes_no_connection_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    
    def deny(*args, **kwargs) -> None:
        raise AssertionError("Offline path attempted a network call")
    
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)

    from one.api import One, OneAlyx

    one_local = One(cache_dir=str(tmp_path))

    assert not isinstance(one_local, OneAlyx)
    assert not hasattr(one_local, "alyx")
    assert one_local.search() == []

def test_search_sessions_parses_details_tuple() -> None:
    one = DummyONE()
    out = access.search_sessions(
        one,
        access.SessionQuery(subject="SWC_001", date_start=date(2022, 2, 1), limit=1),
    )
    assert len(out) == 1
    assert out[0]["eid"] == "eid-1"
    assert out[0]["lab"] == "cortexlab"
