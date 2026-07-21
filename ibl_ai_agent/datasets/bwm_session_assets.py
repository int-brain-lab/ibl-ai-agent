from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


CAMERA_NAMES = ("leftCamera", "rightCamera", "bodyCamera")
WHEEL_POSITION_CANDIDATES = ("wheel.position.npy", "_ibl_wheel.position.npy")
WHEEL_TIMESTAMPS_CANDIDATES = ("wheel.timestamps.npy", "_ibl_wheel.timestamps.npy")
PASSIVE_DATASET_FILENAMES = (
    "_ibl_passivePeriods.intervalsTable.csv",
    "_ibl_passiveRFM.times.npy",
    "_ibl_passiveGabor.table.csv",
    "_ibl_passiveStims.table.csv",
)
NUMERIC_PARQUET_KINDS = {"i", "u", "f", "b"}


def resolve_session_alf_dir(cache_root: Path, *, lab: str, subject: str, date: str, session_number: int) -> Path | None:
    candidates = [
        cache_root / "alyx.internationalbrainlab.org" / lab / "Subjects" / subject / date / f"{session_number:03d}" / "alf",
        cache_root / lab / "Subjects" / subject / date / f"{session_number:03d}" / "alf",
        cache_root / "openalyx.internationalbrainlab.org" / lab / "Subjects" / subject / date / f"{session_number:03d}" / "alf",
    ]
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None

    def score(path: Path) -> tuple[int, int]:
        top_files = sum(1 for child in path.iterdir() if child.is_file())
        total_entries = sum(1 for _ in path.iterdir())
        return (top_files, total_entries)

    return max(existing, key=score)


def first_existing(root: Path, candidates: tuple[str, ...]) -> Path | None:
    for candidate in candidates:
        path = root / candidate
        if path.exists():
            return path
    return None


def find_camera_file(session_alf: Path, stems: list[str], suffixes: list[str]) -> Path | None:
    for stem in stems:
        for suffix in suffixes:
            direct = session_alf / f"{stem}{suffix}"
            if direct.exists():
                return direct
            matches = sorted(session_alf.glob(f"*/{stem}{suffix}"))
            if matches:
                return matches[0]
    return None


def write_numeric_parquet_columns(*, camera_group: Any, parquet_path: Path, compressor: Any) -> int:
    frame = pd.read_parquet(parquet_path)
    written = 0
    for column in frame.columns:
        values = frame[column]
        if values.dtype.kind not in NUMERIC_PARQUET_KINDS:
            continue
        name = f"{parquet_path.stem}__{column}".replace(".", "_")
        camera_group.create_dataset(name, data=values.to_numpy(), compressor=compressor, overwrite=True)
        written += 1
    return written


def wheel_assets_present(session_alf: Path) -> bool:
    return first_existing(session_alf, WHEEL_POSITION_CANDIDATES) is not None and first_existing(session_alf, WHEEL_TIMESTAMPS_CANDIDATES) is not None


def resolve_session_dir(cache_root: Path, *, lab: str, subject: str, date: str, session_number: int) -> Path | None:
    alf = resolve_session_alf_dir(cache_root, lab=lab, subject=subject, date=date, session_number=session_number)
    return alf.parent if alf is not None else None


def find_passive_files(session_dir: Path | None) -> dict[str, Path | None]:
    if session_dir is None or not session_dir.exists():
        return {name: None for name in PASSIVE_DATASET_FILENAMES}
    found: dict[str, Path | None] = {}
    for name in PASSIVE_DATASET_FILENAMES:
        matches = sorted(session_dir.rglob(name))
        found[name] = matches[0] if matches else None
    return found


def passive_missing_filenames(session_dir: Path | None) -> list[str]:
    found = find_passive_files(session_dir)
    return [name for name, path in found.items() if path is None]


def passive_assets_present(session_dir: Path | None) -> bool:
    found = find_passive_files(session_dir)
    return all(path is not None for path in found.values())


def pose_cameras_present(session_alf: Path | None) -> list[str]:
    if session_alf is None:
        return []
    present: list[str] = []
    for camera_name in CAMERA_NAMES:
        stems = [camera_name, f"_ibl_{camera_name}"]
        has_times = find_camera_file(session_alf, stems, [".times.npy"]) is not None
        has_pose = find_camera_file(session_alf, stems, [".lightningPose.pqt", ".lightningPose.npy", ".dlc.pqt", ".dlc.npy"]) is not None
        if has_times and has_pose:
            present.append(camera_name)
    return present
