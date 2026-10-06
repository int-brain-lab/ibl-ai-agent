from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any


PUBLIC_BASE_URL = "https://openalyx.internationalbrainlab.org"
PRIVATE_BASE_URL = "https://alyx.internationalbrainlab.org"
PUBLIC_DEFAULT_USERNAME = "intbrainlab"
PUBLIC_DEFAULT_PASSWORD = "international"


class AccessError(RuntimeError):
    """Raised when IBL access setup or query fails."""


class OfflineModeError(AccessError):
    """Raised when a remote call is attempted while IBL_AGENT_DATA_OFFLINE=1."""


def require_online() -> None:
    """Raise ``OfflineModeError`` if offline mode is enabled via ``IBL_AGENT_DATA_OFFLINE=1``."""
    if os.environ.get("IBL_AGENT_DATA_OFFLINE") == "1":
        raise OfflineModeError(
            "Offline mode is enabled (IBL_AGENT_DATA_OFFLINE=1); remote Alyx/ONE and dataset "
            "downloads are disabled. Use configured local data or unset IBL_AGENT_DATA_OFFLINE."
        )


class AccessMode(str, Enum):
    public = "public"
    private = "private"


@dataclass
class AccessStatus:
    mode: AccessMode
    base_url: str
    interactive_auth: bool
    connected: bool
    user: str | None = None
    message: str | None = None


@dataclass
class SessionQuery:
    subject: str | None = None
    lab: str | None = None
    task_protocol: str | None = None
    dataset: str | None = None
    date_start: date | None = None
    date_end: date | None = None
    limit: int = 20



def resolve_mode(mode: AccessMode | str) -> AccessMode:
    if isinstance(mode, AccessMode):
        return mode
    value = str(mode).strip().lower()
    if value == AccessMode.public.value:
        return AccessMode.public
    if value == AccessMode.private.value:
        return AccessMode.private
    raise AccessError(f"Invalid mode '{mode}', expected 'public' or 'private'.")


def resolve_base_url(mode: AccessMode | str, base_url: str | None = None) -> str:
    if base_url:
        return base_url
    return PUBLIC_BASE_URL if resolve_mode(mode) == AccessMode.public else PRIVATE_BASE_URL


def _import_one() -> Any:
    try:
        from one.api import ONE  # type: ignore
    except Exception as exc:  # pragma: no cover - exercised by CLI behavior
        raise AccessError(
            "ONE-api is not installed. Install optional deps with: uv sync --extra ibl"
        ) from exc
    return ONE


def _credentials(
    mode: AccessMode | str, username: str | None, password: str | None
) -> tuple[str | None, str | None]:
    mode = resolve_mode(mode)
    env_user = os.getenv("IBL_ALYX_USERNAME")
    env_pass = os.getenv("IBL_ALYX_PASSWORD")

    user = username or env_user
    pwd = password or env_pass

    if mode == AccessMode.public:
        return user or PUBLIC_DEFAULT_USERNAME, pwd or PUBLIC_DEFAULT_PASSWORD
    return user, pwd


def connect_one(
    *,
    mode: AccessMode | str,
    base_url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    cache_dir: str | None = None,
    interactive: bool = True,
    silent: bool = True,
) -> tuple[Any, AccessStatus]:

    require_online()
    resolved_mode = resolve_mode(mode)
    resolved_url = resolve_base_url(resolved_mode, base_url)
    user, pwd = _credentials(resolved_mode, username, password)

    ONE = _import_one()
    kwargs: dict[str, Any] = {"base_url": resolved_url, "silent": silent}

    # ONE accepts cache_dir in modern versions; if unavailable it will be ignored below.
    if cache_dir:
        kwargs["cache_dir"] = cache_dir

    if user and pwd:
        kwargs["username"] = user
        kwargs["password"] = pwd
    elif resolved_mode == AccessMode.private and not interactive:
        raise AccessError(
            "Private mode requires credentials for non-interactive auth "
            "(--username/--password or IBL_ALYX_USERNAME/IBL_ALYX_PASSWORD)."
        )
    else:
        kwargs["silent"] = False

    try:
        one = ONE(**kwargs)
        status = AccessStatus(
            mode=resolved_mode,
            base_url=resolved_url,
            interactive_auth=not (user and pwd),
            connected=True,
            user=user,
            message="Connected successfully.",
        )
        return one, status
    except Exception as exc:  # pragma: no cover - network/credential dependent
        raise AccessError(f"Failed to connect to Alyx at {resolved_url}: {exc}") from exc


def _as_date_range(start: date | None, end: date | None) -> list[str] | None:
    if not start and not end:
        return None
    if start and end:
        return [start.isoformat(), end.isoformat()]
    if start:
        return [start.isoformat(), date.today().isoformat()]
    return ["1900-01-01", end.isoformat()]  # type: ignore[arg-type]


def search_sessions(one: Any, query: SessionQuery) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {"details": True}
    if query.subject:
        kwargs["subject"] = query.subject
    if query.dataset:
        kwargs["datasets"] = query.dataset
    if query.task_protocol:
        kwargs["task_protocol"] = query.task_protocol
    date_range = _as_date_range(query.date_start, query.date_end)
    if date_range:
        kwargs["date_range"] = date_range
    if query.lab:
        kwargs["lab"] = query.lab

    try:
        out = one.search(**kwargs)
    except Exception as exc:  # pragma: no cover - depends on remote server
        raise AccessError(f"Session search failed: {exc}") from exc

    # one.search(details=True) usually returns (eids, details).
    if isinstance(out, tuple) and len(out) >= 2:
        details = out[1]
    else:
        details = out

    rows: list[dict[str, Any]] = []
    for row in list(details)[: query.limit]:
        if isinstance(row, dict):
            rows.append(
                {
                    "eid": row.get("id") or row.get("eid"),
                    "subject": row.get("subject"),
                    "lab": row.get("lab"),
                    "start_time": row.get("start_time"),
                    "task_protocol": row.get("task_protocol"),
                }
            )
        else:
            rows.append({"eid": str(row)})
    return rows
