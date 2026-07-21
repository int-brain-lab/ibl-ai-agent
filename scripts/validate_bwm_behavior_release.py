"""Validate an extracted bwm_behavior release archive (issue #18 wheel fix, issue #19 pose rename).

Checks version strings, table presence/manifest completeness, that the wheel
fix landed (recovered wheel-session count plus sampled shards carrying a
uniform-100 Hz ``wheel.velocity``), and that the pose tracker column is
populated.

Usage:
    uv run python scripts/validate_bwm_behavior_release.py reports/datasets/bwm_behavior/2.0.0
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

VERSION = "2.0.0"
WHEEL_FS_HZ = 100.0
TABLES = (
    "metadata/sessions", "metadata/trials", "metadata/events",
    "metadata/wheel_availability", "metadata/pose_availability",
    "features/trial_behavior_features", "features/wheel_trial_features",
    "features/pose_trial_features", "features/event_aligned_behavior_features",
    "features/behavior_session_features", "features/movement_state_epochs",
    "features/quiescence_state_epochs", "features/behavior_state_session_features",
)


def validate(root: Path, *, min_wheel_sessions: int = 450, sample: int = 25) -> list[str]:
    """Return a list of failure messages (empty list means the archive passed)."""
    fails: list[str] = []

    def req(condition: bool, message: str) -> None:
        if not condition:
            fails.append(message)

    schema = yaml.safe_load((root / "schema.yaml").read_text(encoding="utf-8"))
    provenance = yaml.safe_load((root / "provenance.yaml").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    req(schema.get("dataset_name") == "bwm_behavior", "schema dataset_name is not bwm_behavior")
    for name, obj in (("schema", schema), ("provenance", provenance), ("manifest", manifest)):
        req(str(obj.get("dataset_version")) == VERSION, f"{name} dataset_version is not {VERSION}")

    manifest_files = {str(item.get("path")) for item in manifest.get("files", [])}
    for table in TABLES:
        path = root / f"{table}.parquet"
        req(path.exists(), f"missing {table}.parquet")
        req(f"{table}.parquet" in manifest_files, f"manifest omits {table}.parquet")
        if path.exists():
            req(len(pd.read_parquet(path)) > 0, f"empty {table}.parquet")

    availability = pd.read_parquet(root / "metadata" / "wheel_availability.parquet")
    present = int(availability["wheel_present"].fillna(False).astype(bool).sum())
    req(present >= min_wheel_sessions, f"only {present} wheel sessions (expected >= {min_wheel_sessions})")

    pose_availability = pd.read_parquet(root / "metadata" / "pose_availability.parquet")
    req("tracker" in pose_availability.columns, "pose_availability.parquet is missing the tracker column")
    present_trackers = set(pose_availability.loc[pose_availability["pose_present"], "tracker"].dropna().unique())
    req(present_trackers <= {"lightningPose", "dlc"}, f"unexpected tracker values: {present_trackers}")

    from ibl_ai_agent.datasets import bwm_behavior

    shards = sorted((root / "sessions").glob("*.zip"))
    req(bool(shards), "no session shards under sessions/")
    for shard_path in shards[:: max(1, len(shards) // max(1, sample))][:sample]:
        shard = bwm_behavior.load_behavior_session_shard(shard_path)
        wheel = shard["meta"].get("wheel", {})
        if not wheel.get("present"):
            continue
        req("wheel.velocity" in shard, f"{shard_path.name}: missing wheel.velocity")
        req(abs(float(wheel.get("fs", 0.0)) - WHEEL_FS_HZ) < 1e-6, f"{shard_path.name}: wheel fs is not {WHEEL_FS_HZ:g}")
        times = np.asarray(shard.get("wheel.timestamps", []), dtype=np.float64)
        if times.size >= 2:
            req(abs(1.0 / np.median(np.diff(times)) - WHEEL_FS_HZ) < 1.0, f"{shard_path.name}: non-uniform grid")
    return fails


def main() -> int:
    root = Path(sys.argv[1]).expanduser().resolve()
    fails = validate(root)
    print(f"{root}: {'PASS' if not fails else 'FAIL'} ({len(fails)} failure(s))")
    for message in fails:
        print(f"  - {message}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())