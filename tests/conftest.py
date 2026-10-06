from __future__ import annotations

import pytest

@pytest.fixture(autouse=True)
def _online_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove offline mode by default during testing"""
    monkeypatch.delenv("IBL_AGENT_DATA_OFFLINE", raising=False)