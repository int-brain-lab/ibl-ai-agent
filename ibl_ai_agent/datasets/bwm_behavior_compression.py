from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yaml

from ibl_ai_agent.datasets import bwm_behavior, bwm_shared


DEFAULT_DATASET_DIR = Path("reports/datasets/bwm_behavior/1.0.0")
DEFAULT_STRATEGIES = (
    "lossless-baseline",
    "conservative",
    "balanced",
    "aggressive",
    "balanced-pose-delta",
    "aggressive-pose-delta",
    "aggressive-pose-delta-wheel-native-left60-right60-body30",
    "aggressive-pose-delta-wheel100-pose50",
    "aggressive-pose-delta-30hz",
)


@dataclass(frozen=True)
class ProfileConfig:
    dataset_dir: Path = DEFAULT_DATASET_DIR
    output_root: Path = Path("reports/profiles")
    max_shards: int = 12
    strategy_names: tuple[str, ...] = DEFAULT_STRATEGIES
    selection: str = "largest"
    target_min_factor: float = 5.0
    verbose: bool = True


@dataclass(frozen=True)
class ProfileOutputs:
    report_dir: Path
    summary_path: Path
    strategy_summary_path: Path
    array_metrics_path: Path
    config_path: Path


@dataclass(frozen=True)
class ValidationConfig:
    dataset_dir: Path = DEFAULT_DATASET_DIR
    output_root: Path = Path("reports/profiles")
    max_shards: int = 12
    strategy_name: str = "aggressive-pose-delta-30hz"
    selection: str = "spread"
    verbose: bool = True


@dataclass(frozen=True)
class ValidationOutputs:
    report_dir: Path
    summary_path: Path
    array_validation_path: Path
    config_path: Path


@dataclass(frozen=True)
class FeatureValidationConfig:
    dataset_dir: Path = DEFAULT_DATASET_DIR
    output_root: Path = Path("reports/profiles")
    max_shards: int = 25
    strategy_name: str = "aggressive-pose-delta-30hz"
    selection: str = "spread"
    verbose: bool = True


@dataclass(frozen=True)
class FeatureValidationOutputs:
    report_dir: Path
    summary_path: Path
    feature_validation_path: Path
    row_validation_path: Path
    config_path: Path


@dataclass(frozen=True)
class Strategy:
    name: str
    xy_precision_px: float | None
    xy_delta: bool
    per_column_pose: bool
    likelihood_uint8: bool
    likelihood_bits: int
    other_float16: bool
    other_precision: float | None
    timestamp_tick_s: float | None
    wheel_position_precision: float | None
    wheel_velocity_precision: float | None
    wheel_velocity_dtype: str = "int16"
    wheel_downsample_rate_hz: float | None = None
    pose_downsample_rate_hz: float | None = None
    pose_camera_downsample_rates_hz: dict[str, float] | None = None
    fixed_rate_timestamps: bool = False


STRATEGIES: dict[str, Strategy] = {
    "lossless-baseline": Strategy(
        name="lossless-baseline",
        xy_precision_px=None,
        xy_delta=False,
        per_column_pose=False,
        likelihood_uint8=False,
        likelihood_bits=8,
        other_float16=False,
        other_precision=None,
        timestamp_tick_s=None,
        wheel_position_precision=None,
        wheel_velocity_precision=None,
    ),
    "conservative": Strategy(
        name="conservative",
        xy_precision_px=0.1,
        xy_delta=False,
        per_column_pose=False,
        likelihood_uint8=True,
        likelihood_bits=8,
        other_float16=True,
        other_precision=None,
        timestamp_tick_s=None,
        wheel_position_precision=None,
        wheel_velocity_precision=None,
    ),
    "balanced": Strategy(
        name="balanced",
        xy_precision_px=0.25,
        xy_delta=False,
        per_column_pose=False,
        likelihood_uint8=True,
        likelihood_bits=8,
        other_float16=True,
        other_precision=None,
        timestamp_tick_s=0.001,
        wheel_position_precision=0.0005,
        wheel_velocity_precision=0.001,
    ),
    "aggressive": Strategy(
        name="aggressive",
        xy_precision_px=0.5,
        xy_delta=False,
        per_column_pose=False,
        likelihood_uint8=True,
        likelihood_bits=8,
        other_float16=True,
        other_precision=None,
        timestamp_tick_s=0.001,
        wheel_position_precision=0.001,
        wheel_velocity_precision=0.005,
    ),
    "balanced-pose-delta": Strategy(
        name="balanced-pose-delta",
        xy_precision_px=0.25,
        xy_delta=True,
        per_column_pose=True,
        likelihood_uint8=True,
        likelihood_bits=8,
        other_float16=False,
        other_precision=0.01,
        timestamp_tick_s=0.001,
        wheel_position_precision=0.0005,
        wheel_velocity_precision=0.001,
    ),
    "aggressive-pose-delta": Strategy(
        name="aggressive-pose-delta",
        xy_precision_px=0.5,
        xy_delta=True,
        per_column_pose=True,
        likelihood_uint8=True,
        likelihood_bits=8,
        other_float16=False,
        other_precision=0.05,
        timestamp_tick_s=0.001,
        wheel_position_precision=0.001,
        wheel_velocity_precision=0.005,
    ),
    "aggressive-pose-delta-wheel-native-left60-right60-body30": Strategy(
        name="aggressive-pose-delta-wheel-native-left60-right60-body30",
        xy_precision_px=0.5,
        xy_delta=True,
        per_column_pose=True,
        likelihood_uint8=True,
        likelihood_bits=8,
        other_float16=False,
        other_precision=0.05,
        timestamp_tick_s=0.001,
        wheel_position_precision=0.001,
        wheel_velocity_precision=0.005,
        pose_camera_downsample_rates_hz={"leftCamera": 60.0, "rightCamera": 60.0, "bodyCamera": 30.0},
        fixed_rate_timestamps=True,
    ),
    "aggressive-pose-delta-wheel100-pose50": Strategy(
        name="aggressive-pose-delta-wheel100-pose50",
        xy_precision_px=0.5,
        xy_delta=True,
        per_column_pose=True,
        likelihood_uint8=True,
        likelihood_bits=8,
        other_float16=False,
        other_precision=0.05,
        timestamp_tick_s=0.001,
        wheel_position_precision=0.001,
        wheel_velocity_precision=0.005,
        wheel_velocity_dtype="int32",
        wheel_downsample_rate_hz=100.0,
        pose_downsample_rate_hz=50.0,
        fixed_rate_timestamps=True,
    ),
    "compact-pose-delta": Strategy(
        name="compact-pose-delta",
        xy_precision_px=1.0,
        xy_delta=True,
        per_column_pose=True,
        likelihood_uint8=True,
        likelihood_bits=4,
        other_float16=False,
        other_precision=0.1,
        timestamp_tick_s=0.001,
        wheel_position_precision=0.001,
        wheel_velocity_precision=0.005,
    ),
    "aggressive-pose-delta-30hz": Strategy(
        name="aggressive-pose-delta-30hz",
        xy_precision_px=0.5,
        xy_delta=True,
        per_column_pose=True,
        likelihood_uint8=True,
        likelihood_bits=8,
        other_float16=False,
        other_precision=0.05,
        timestamp_tick_s=0.001,
        wheel_position_precision=0.001,
        wheel_velocity_precision=0.005,
        wheel_velocity_dtype="int32",
        wheel_downsample_rate_hz=30.0,
        pose_downsample_rate_hz=30.0,
        fixed_rate_timestamps=True,
    ),
}


def profile_bwm_behavior_compression(config: ProfileConfig) -> ProfileOutputs:
    """Profile candidate lossy compression strategies for behavior session shards.

    The profile is read-only with respect to the source dataset. It decodes a
    sampled set of existing `sessions/*.zip` shards, applies candidate
    quantizers in memory, recompresses encoded arrays with the existing Blosc
    zstd/shuffle codec, and writes aggregate metrics under `reports/profiles`.
    """

    dataset_dir = config.dataset_dir
    sessions_dir = dataset_dir / "sessions"
    if not sessions_dir.exists():
        raise FileNotFoundError(f"Behavior session shard directory not found: {sessions_dir}")

    strategy_names = tuple(config.strategy_names)
    unknown = [name for name in strategy_names if name not in STRATEGIES]
    if unknown:
        raise ValueError(f"Unknown compression strategies: {', '.join(unknown)}")

    shards = _select_shards(sessions_dir, max_shards=config.max_shards, selection=config.selection)
    if not shards:
        raise FileNotFoundError(f"No behavior session zip shards found under {sessions_dir}")

    report_dir = config.output_root / f"bwm_behavior_compression_{bwm_shared.now_tag()}"
    report_dir.mkdir(parents=True, exist_ok=False)

    rows: list[dict[str, Any]] = []
    shard_rows: list[dict[str, Any]] = []
    for index, shard_path in enumerate(shards, start=1):
        if config.verbose:
            print(f"Profile shard {index}/{len(shards)}: {shard_path.name}")
        shard = bwm_shared.read_array_shard(shard_path)
        meta = shard["meta"]
        arrays = shard["arrays"]
        original_bytes_by_array = _original_compressed_bytes(meta)
        original_total = int(sum(original_bytes_by_array.values()))
        for strategy_name in strategy_names:
            strategy = STRATEGIES[strategy_name]
            candidate_arrays = _prepare_candidate_arrays(arrays, strategy=strategy)
            candidate_total = 0
            max_abs_error = 0.0
            weighted_abs_error = 0.0
            n_error_values = 0
            class_totals: dict[str, int] = {}
            for array_name, arr in candidate_arrays.items():
                columns = _columns_for_array(meta, array_name)
                result = _encode_candidate(array_name, arr, columns=columns, strategy=strategy)
                candidate_total += result["compressed_bytes"]
                class_totals[result["signal_class"]] = class_totals.get(result["signal_class"], 0) + result["compressed_bytes"]
                max_abs_error = max(max_abs_error, result["max_abs_error"])
                weighted_abs_error += result["mean_abs_error"] * result["n_values"]
                n_error_values += result["n_values"]
                rows.append(
                    {
                        "shard": shard_path.name,
                        "strategy": strategy.name,
                        "array": array_name,
                        "signal_class": result["signal_class"],
                        "dtype": str(arr.dtype),
                        "shape": json.dumps(list(arr.shape)),
                        "source_shape": json.dumps(list(np.asarray(arrays[array_name]).shape)),
                        "original_compressed_bytes": int(original_bytes_by_array.get(array_name, 0)),
                        "candidate_compressed_bytes": int(result["compressed_bytes"]),
                        "compression_factor_vs_current": _safe_factor(original_bytes_by_array.get(array_name, 0), result["compressed_bytes"]),
                        "max_abs_error": result["max_abs_error"],
                        "mean_abs_error": result["mean_abs_error"],
                        "encoded_parts": json.dumps(result["encoded_parts"], sort_keys=True),
                    }
                )
            shard_rows.append(
                {
                    "shard": shard_path.name,
                    "strategy": strategy.name,
                    "original_compressed_bytes": original_total,
                    "candidate_compressed_bytes": int(candidate_total),
                    "compression_factor_vs_current": _safe_factor(original_total, candidate_total),
                    "max_abs_error": max_abs_error,
                    "mean_abs_error": weighted_abs_error / n_error_values if n_error_values else 0.0,
                    "candidate_bytes_by_class": json.dumps(class_totals, sort_keys=True),
                }
            )

    array_df = pd.DataFrame(rows)
    shard_df = pd.DataFrame(shard_rows)
    summary_df = _summarize_strategy_metrics(shard_df)

    array_metrics_path = report_dir / "array_metrics.csv"
    strategy_summary_path = report_dir / "strategy_summary.csv"
    shard_metrics_path = report_dir / "shard_metrics.csv"
    config_path = report_dir / "config.yaml"
    summary_path = report_dir / "SUMMARY.md"

    array_df.to_csv(array_metrics_path, index=False)
    shard_df.to_csv(shard_metrics_path, index=False)
    summary_df.to_csv(strategy_summary_path, index=False)
    config_path.write_text(
        yaml.safe_dump(
            {
                "dataset_dir": str(dataset_dir),
                "sessions_dir": str(sessions_dir),
                "max_shards": config.max_shards,
                "selection": config.selection,
                "strategies": list(strategy_names),
                "target_min_factor": config.target_min_factor,
                "selected_shards": [p.name for p in shards],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    summary_path.write_text(
        _render_summary(
            dataset_dir=dataset_dir,
            shards=shards,
            summary_df=summary_df,
            target_min_factor=config.target_min_factor,
            strategy_names=strategy_names,
        ),
        encoding="utf-8",
    )

    return ProfileOutputs(
        report_dir=report_dir,
        summary_path=summary_path,
        strategy_summary_path=strategy_summary_path,
        array_metrics_path=array_metrics_path,
        config_path=config_path,
    )


def validate_bwm_behavior_compression(config: ValidationConfig) -> ValidationOutputs:
    """Validate reconstruction errors for one behavior compression strategy.

    The validation is read-only with respect to the source dataset. For
    downsampling strategies, errors compare reconstructed values against the
    retained/resampled samples, while the retained row ratio records how much of
    the original continuous trace remains.
    """

    dataset_dir = config.dataset_dir
    sessions_dir = dataset_dir / "sessions"
    if not sessions_dir.exists():
        raise FileNotFoundError(f"Behavior session shard directory not found: {sessions_dir}")
    if config.strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown compression strategy: {config.strategy_name}")

    strategy = STRATEGIES[config.strategy_name]
    shards = _select_shards(sessions_dir, max_shards=config.max_shards, selection=config.selection)
    if not shards:
        raise FileNotFoundError(f"No behavior session zip shards found under {sessions_dir}")

    report_dir = config.output_root / f"bwm_behavior_compression_validation_{bwm_shared.now_tag()}"
    report_dir.mkdir(parents=True, exist_ok=False)

    rows: list[dict[str, Any]] = []
    for index, shard_path in enumerate(shards, start=1):
        if config.verbose:
            print(f"Validate shard {index}/{len(shards)}: {shard_path.name}")
        shard = bwm_shared.read_array_shard(shard_path)
        meta = shard["meta"]
        source_arrays = shard["arrays"]
        candidate_arrays = _prepare_candidate_arrays(source_arrays, strategy=strategy)
        for array_name, candidate in candidate_arrays.items():
            columns = _columns_for_array(meta, array_name)
            reconstructed = _reconstruct_candidate_array(array_name, candidate, columns=columns, strategy=strategy)
            rows.append(
                _validation_row(
                    shard=shard_path.name,
                    array_name=array_name,
                    signal_class=_signal_class(array_name),
                    source=np.asarray(source_arrays[array_name]),
                    candidate=np.asarray(candidate),
                    reconstructed=np.asarray(reconstructed),
                )
            )

    validation_df = pd.DataFrame(rows)
    array_validation_path = report_dir / "array_validation.csv"
    config_path = report_dir / "config.yaml"
    summary_path = report_dir / "SUMMARY.md"

    validation_df.to_csv(array_validation_path, index=False)
    config_path.write_text(
        yaml.safe_dump(
            {
                "dataset_dir": str(dataset_dir),
                "sessions_dir": str(sessions_dir),
                "max_shards": config.max_shards,
                "selection": config.selection,
                "strategy": config.strategy_name,
                "selected_shards": [p.name for p in shards],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    summary_path.write_text(
        _render_validation_summary(
            dataset_dir=dataset_dir,
            shards=shards,
            strategy_name=config.strategy_name,
            validation_df=validation_df,
        ),
        encoding="utf-8",
    )

    return ValidationOutputs(report_dir=report_dir, summary_path=summary_path, array_validation_path=array_validation_path, config_path=config_path)


def validate_bwm_behavior_compression_features(config: FeatureValidationConfig) -> FeatureValidationOutputs:
    """Compare derived behavior features after compression reconstruction."""

    dataset_dir = config.dataset_dir
    sessions_dir = dataset_dir / "sessions"
    trials_path = dataset_dir / "metadata" / "trials.parquet"
    if not sessions_dir.exists():
        raise FileNotFoundError(f"Behavior session shard directory not found: {sessions_dir}")
    if not trials_path.exists():
        raise FileNotFoundError(f"Behavior trials table not found: {trials_path}")
    if config.strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown compression strategy: {config.strategy_name}")

    shards = _select_shards(sessions_dir, max_shards=config.max_shards, selection=config.selection)
    if not shards:
        raise FileNotFoundError(f"No behavior session zip shards found under {sessions_dir}")

    trials_df = pd.read_parquet(trials_path)
    trial_groups = bwm_behavior._trial_groups_by_eid(
        trials_df,
        columns=[col for col in ["eid", "trial_id", "stimOn_times", "goCue_times", "firstMovement_times", "response_times", "feedback_times"] if col in trials_df.columns],
    )

    source_rows = _empty_feature_row_buckets()
    candidate_rows = _empty_feature_row_buckets()
    for index, shard_path in enumerate(shards, start=1):
        if config.verbose:
            print(f"Validate feature shard {index}/{len(shards)}: {shard_path.name}")
        shard = bwm_shared.read_array_shard(shard_path)
        meta = shard["meta"]
        arrays = shard["arrays"]
        eid = str(meta.get("eid", shard_path.stem))
        trial_group = trial_groups.get(eid, pd.DataFrame(columns=["eid", "trial_id"]))

        _extend_feature_row_buckets(
            source_rows,
            _feature_rows_from_session_arrays(eid=eid, metadata=meta, arrays=arrays, trial_group=trial_group),
        )
        candidate = prepare_behavior_session_candidate(arrays, metadata=meta, strategy_name=config.strategy_name)
        reconstructed = reconstruct_behavior_session_candidate(candidate, metadata=meta, strategy_name=config.strategy_name)
        _extend_feature_row_buckets(
            candidate_rows,
            _feature_rows_from_session_arrays(eid=eid, metadata=meta, arrays=reconstructed, trial_group=trial_group),
        )

    source_tables = _feature_tables_from_rows(source_rows)
    candidate_tables = _feature_tables_from_rows(candidate_rows)
    row_df, feature_df = _compare_feature_tables(source_tables, candidate_tables)

    report_dir = config.output_root / f"bwm_behavior_compression_feature_validation_{bwm_shared.now_tag()}"
    report_dir.mkdir(parents=True, exist_ok=False)
    feature_validation_path = report_dir / "feature_validation.csv"
    row_validation_path = report_dir / "row_validation.csv"
    config_path = report_dir / "config.yaml"
    summary_path = report_dir / "SUMMARY.md"

    feature_df.to_csv(feature_validation_path, index=False)
    row_df.to_csv(row_validation_path, index=False)
    config_path.write_text(
        yaml.safe_dump(
            {
                "dataset_dir": str(dataset_dir),
                "sessions_dir": str(sessions_dir),
                "trials_path": str(trials_path),
                "max_shards": config.max_shards,
                "selection": config.selection,
                "strategy": config.strategy_name,
                "selected_shards": [p.name for p in shards],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    summary_path.write_text(
        _render_feature_validation_summary(
            dataset_dir=dataset_dir,
            shards=shards,
            strategy_name=config.strategy_name,
            row_df=row_df,
            feature_df=feature_df,
        ),
        encoding="utf-8",
    )

    return FeatureValidationOutputs(
        report_dir=report_dir,
        summary_path=summary_path,
        feature_validation_path=feature_validation_path,
        row_validation_path=row_validation_path,
        config_path=config_path,
    )


def prepare_behavior_session_candidate(
    arrays: dict[str, np.ndarray],
    *,
    metadata: dict[str, Any],
    strategy_name: str,
) -> dict[str, np.ndarray]:
    """Apply a compression strategy's semantic transforms to shard arrays."""

    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown compression strategy: {strategy_name}")
    return _prepare_candidate_arrays(arrays, strategy=STRATEGIES[strategy_name])


def reconstruct_behavior_session_candidate(
    arrays: dict[str, np.ndarray],
    *,
    metadata: dict[str, Any],
    strategy_name: str,
) -> dict[str, np.ndarray]:
    """Decode candidate arrays through the strategy quantization model."""

    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown compression strategy: {strategy_name}")
    strategy = STRATEGIES[strategy_name]
    reconstructed: dict[str, np.ndarray] = {}
    for array_name, arr in arrays.items():
        columns = _columns_for_array(metadata, array_name)
        reconstructed[array_name] = _reconstruct_candidate_array(
            array_name,
            np.asarray(arr),
            columns=columns,
            strategy=strategy,
        )
    return reconstructed


def write_behavior_session_shard(
    path: Path,
    *,
    metadata: dict[str, Any],
    arrays: dict[str, np.ndarray],
    strategy_name: str,
) -> None:
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown compression strategy: {strategy_name}")
    strategy = STRATEGIES[strategy_name]
    manifest = dict(metadata)
    manifest["format"] = bwm_behavior.BEHAVIOR_SESSION_SHARD_FORMAT_V2
    manifest["compression"] = {
        "name": "behavior_semantic_v2",
        "profile": strategy_name,
        "base_codec": "blosc_zstd_shuffle",
    }
    manifest["arrays"] = {}
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_STORED) as zf:
        candidate_arrays = prepare_behavior_session_candidate(arrays, metadata=metadata, strategy_name=strategy_name)
        for camera_name, camera_meta in manifest.get("cameras", {}).items():
            timestamps_name = f"{camera_name}.timestamps"
            features_name = f"{camera_name}.features"
            if timestamps_name in candidate_arrays:
                camera_meta["n_frames"] = int(np.asarray(candidate_arrays[timestamps_name]).shape[0])
            if features_name in candidate_arrays and np.asarray(candidate_arrays[features_name]).ndim == 2:
                camera_meta["n_features"] = int(np.asarray(candidate_arrays[features_name]).shape[1])
        for array_name, arr in candidate_arrays.items():
            columns = _columns_for_array(metadata, array_name)
            spec, payloads = _encode_array_for_storage(array_name, np.asarray(arr), columns=columns, strategy=strategy)
            manifest["arrays"][array_name] = spec
            for entry_name, payload in payloads.items():
                zf.writestr(entry_name, payload)
        zf.writestr("meta.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))


def read_behavior_session_shard(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path, mode="r") as zf:
        meta = json.loads(zf.read("meta.json").decode("utf-8"))
        if meta.get("format") not in bwm_behavior.BEHAVIOR_SESSION_SHARD_FORMATS_V2:
            return bwm_shared.read_array_shard(path)
        arrays = {
            name: _decode_array_from_storage(zf=zf, array_name=name, spec=spec)
            for name, spec in meta["arrays"].items()
        }
    return {"meta": meta, "arrays": arrays}


def build_behavior_feature_tables_from_shards(
    *,
    dataset_dir: Path,
    trials_df: pd.DataFrame,
    verbose: bool = True,
    progress_callback: callable | None = None,
    jobs: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    sessions_dir = dataset_dir / "sessions"
    if not sessions_dir.exists():
        raise FileNotFoundError(f"Missing behavior shard directory: {sessions_dir}")
    trial_groups = bwm_behavior._trial_groups_by_eid(
        trials_df,
        columns=[col for col in ["eid", "trial_id", "stimOn_times", "goCue_times", "firstMovement_times", "response_times", "feedback_times"] if col in trials_df.columns],
    )
    rows = _empty_feature_row_buckets()
    shards = sorted(sessions_dir.glob("*.zip"))
    total = len(shards)
    if max(1, jobs) == 1:
        for index, shard_path in enumerate(shards, start=1):
            if progress_callback is not None:
                progress_callback(index=index, total=total, shard_path=shard_path)
            if verbose and (index == 1 or index == len(shards) or index % 50 == 0):
                print(f"Refresh shard features {index}/{len(shards)}: {shard_path.name}")
            _extend_feature_row_buckets(rows, _feature_rows_for_shard(shard_path=shard_path, trial_groups=trial_groups))
    else:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
            futures = {
                executor.submit(_feature_rows_for_shard, shard_path=shard_path, trial_groups=trial_groups): shard_path
                for shard_path in shards
            }
            for index, future in enumerate(as_completed(futures), start=1):
                shard_path = futures[future]
                if progress_callback is not None:
                    progress_callback(index=index, total=total, shard_path=shard_path)
                if verbose and (index == 1 or index == len(shards) or index % 50 == 0):
                    print(f"Refresh shard features {index}/{len(shards)}: {shard_path.name}")
                _extend_feature_row_buckets(rows, future.result())
    tables = _feature_tables_from_rows(rows)
    return _coerce_feature_tables(tables)


def _feature_rows_for_shard(*, shard_path: Path, trial_groups: dict[str, pd.DataFrame]) -> dict[str, list[dict[str, Any]]]:
    shard = read_behavior_session_shard(shard_path)
    meta = shard["meta"]
    arrays = shard["arrays"]
    eid = str(meta.get("eid", shard_path.stem))
    trial_group = trial_groups.get(eid, pd.DataFrame(columns=["eid", "trial_id"]))
    return _feature_rows_from_session_arrays(eid=eid, metadata=meta, arrays=arrays, trial_group=trial_group)


def _empty_feature_row_buckets() -> dict[str, list[dict[str, Any]]]:
    return {
        "wheel_availability": [],
        "pose_availability": [],
        "wheel_trial_features": [],
        "pose_trial_features": [],
        "event_aligned_behavior_features": [],
        "movement_state_epochs": [],
        "quiescence_state_epochs": [],
        "behavior_state_session_features": [],
    }


def _extend_feature_row_buckets(target: dict[str, list[dict[str, Any]]], source: dict[str, list[dict[str, Any]]]) -> None:
    for table_name, rows in source.items():
        target[table_name].extend(rows)


def _feature_rows_from_session_arrays(
    *,
    eid: str,
    metadata: dict[str, Any],
    arrays: dict[str, np.ndarray],
    trial_group: pd.DataFrame,
) -> dict[str, list[dict[str, Any]]]:
    rows = _empty_feature_row_buckets()

    wheel_times = np.asarray(arrays.get("wheel.timestamps", []), dtype=np.float64)
    wheel_position = np.asarray(arrays.get("wheel.position", []), dtype=np.float64)
    wheel_velocity = np.asarray(arrays["wheel.velocity"], dtype=np.float32) if "wheel.velocity" in arrays else None
    wheel_present = wheel_times.size > 0 and wheel_position.shape == wheel_times.shape
    rows["wheel_availability"].append(
        {
            "eid": eid,
            "wheel_present": bool(wheel_present),
            "n_samples": int(wheel_times.size if wheel_present else 0),
            "t_start": float(wheel_times[0]) if wheel_present else np.nan,
            "t_end": float(wheel_times[-1]) if wheel_present else np.nan,
        }
    )
    if wheel_present:
        movement_rows, quiescence_rows, state_summary = bwm_behavior._detect_wheel_state_rows(
            eid=eid,
            timestamps=wheel_times,
            position=wheel_position,
        )
        rows["movement_state_epochs"].extend(movement_rows)
        rows["quiescence_state_epochs"].extend(quiescence_rows)
        rows["behavior_state_session_features"].append(state_summary)
        for trial in trial_group.itertuples(index=False):
            stim = _trial_float(trial, "stimOn_times")
            move = _trial_float(trial, "firstMovement_times")
            resp = _trial_float(trial, "response_times")
            start = stim if np.isfinite(stim) else move
            end = resp if np.isfinite(resp) else move
            if np.isfinite(start) and np.isfinite(end) and end < start and np.isfinite(move):
                end = move
            stats = bwm_behavior._summarize_wheel_window(
                timestamps=wheel_times,
                position=wheel_position,
                velocity=wheel_velocity,
                start=start,
                end=end,
            )
            rows["wheel_trial_features"].append(
                {
                    "eid": eid,
                    "trial_id": int(getattr(trial, "trial_id")),
                    "window_spec": "stimOn:response",
                    "wheel_present": True,
                    "movement_onset_time": move if np.isfinite(move) else np.nan,
                    "movement_peak_time": resp if np.isfinite(resp) else np.nan,
                    **stats,
                }
            )
        wheel_values = np.asarray(wheel_velocity if wheel_velocity is not None else wheel_position, dtype=float)
        prepared_wheel = bwm_behavior._prepare_event_aligned_signal(timestamps=wheel_times, values=wheel_values)
        _append_event_feature_rows(rows["event_aligned_behavior_features"], eid=eid, signal_name="wheel", prepared=prepared_wheel, trial_group=trial_group)
    else:
        rows["behavior_state_session_features"].append(
            {
                "eid": eid,
                "wheel_present": False,
                "movement_epoch_count": 0,
                "quiescence_epoch_count": 0,
                "fraction_time_moving": np.nan,
                "fraction_time_quiescent": np.nan,
                "median_movement_duration": np.nan,
                "median_quiescence_duration": np.nan,
            }
        )

    cameras = metadata.get("cameras", {})
    camera_seen = False
    for camera_name, camera_meta in cameras.items():
        timestamps_name = f"{camera_name}.timestamps"
        features_name = f"{camera_name}.features"
        if timestamps_name not in arrays or features_name not in arrays:
            continue
        timestamps = np.asarray(arrays[timestamps_name], dtype=np.float64)
        features = np.asarray(arrays[features_name], dtype=np.float32)
        if timestamps.size == 0 or features.ndim != 2 or features.shape[0] != timestamps.shape[0]:
            continue
        camera_seen = True
        rows["pose_availability"].append(
            {
                "eid": eid,
                "camera": str(camera_name),
                "pose_present": True,
                "n_frames": int(timestamps.size),
                "t_start": float(timestamps[0]),
                "t_end": float(timestamps[-1]),
                "tracker": camera_meta.get("tracker"),
            }
        )
        mag = np.nanmean(np.abs(features.astype(float, copy=False)), axis=1) if features.size else np.asarray([], dtype=float)
        prepared_mag = bwm_behavior._prepare_event_aligned_signal(timestamps=timestamps, values=mag)
        for trial in trial_group.itertuples(index=False):
            stim = _trial_float(trial, "stimOn_times")
            fb = _trial_float(trial, "feedback_times")
            stats = bwm_behavior._summarize_pose_window(
                timestamps=timestamps,
                features=features,
                start=stim,
                end=(fb if np.isfinite(fb) else stim),
            )
            rows["pose_trial_features"].append(
                {
                    "eid": eid,
                    "trial_id": int(getattr(trial, "trial_id")),
                    "camera": str(camera_name),
                    "window_spec": "stimOn:feedback",
                    "pose_present": True,
                    "feature_mean": stats["feature_mean"],
                    "feature_peak": stats["feature_peak"],
                }
            )
        _append_event_feature_rows(rows["event_aligned_behavior_features"], eid=eid, signal_name=str(camera_name), prepared=prepared_mag, trial_group=trial_group)

    if not camera_seen:
        rows["pose_availability"].append({"eid": eid, "camera": "", "pose_present": False, "n_frames": 0, "t_start": np.nan, "t_end": np.nan, "tracker": None})

    return rows


def _append_event_feature_rows(
    rows: list[dict[str, Any]],
    *,
    eid: str,
    signal_name: str,
    prepared: dict[str, np.ndarray],
    trial_group: pd.DataFrame,
) -> None:
    for trial in trial_group.itertuples(index=False):
        for event_name, source_col in bwm_behavior.EVENT_COLUMNS:
            if not hasattr(trial, source_col):
                continue
            event_time = _trial_float(trial, source_col)
            if not np.isfinite(event_time):
                continue
            rows.append(
                {
                    "eid": eid,
                    "trial_id": int(getattr(trial, "trial_id")),
                    "signal_name": str(signal_name),
                    "event_name": str(event_name),
                    "window_spec": bwm_behavior.BEHAVIOR_EVENT_WINDOW_SPEC,
                    **bwm_behavior._event_aligned_signal_summary_prepared(prepared=prepared, event_time=event_time),
                }
            )


def _trial_float(trial: Any, name: str) -> float:
    return float(getattr(trial, name, np.nan)) if hasattr(trial, name) else float("nan")


def _feature_tables_from_rows(rows: dict[str, list[dict[str, Any]]]) -> dict[str, pd.DataFrame]:
    return {table_name: pd.DataFrame(table_rows) for table_name, table_rows in rows.items()}


def _coerce_feature_tables(tables: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    wheel_availability = tables.get("wheel_availability", pd.DataFrame(columns=["eid", "wheel_present", "n_samples", "t_start", "t_end"]))
    if not wheel_availability.empty:
        wheel_availability["n_samples"] = wheel_availability["n_samples"].astype(np.int32)
        wheel_availability["wheel_present"] = wheel_availability["wheel_present"].astype(bool)
        for col in ("t_start", "t_end"):
            wheel_availability[col] = pd.to_numeric(wheel_availability[col], errors="coerce").astype(np.float32)

    pose_availability = tables.get("pose_availability", pd.DataFrame(columns=["eid", "camera", "pose_present", "n_frames", "t_start", "t_end", "tracker"]))
    if not pose_availability.empty:
        pose_availability["n_frames"] = pose_availability["n_frames"].astype(np.int32)
        pose_availability["pose_present"] = pose_availability["pose_present"].astype(bool)
        for col in ("t_start", "t_end"):
            pose_availability[col] = pd.to_numeric(pose_availability[col], errors="coerce").astype(np.float32)
        if "tracker" not in pose_availability.columns:
            pose_availability["tracker"] = None
        pose_availability["tracker"] = pose_availability["tracker"].astype(object)

    wheel_features = tables.get("wheel_trial_features", pd.DataFrame(columns=["eid", "trial_id", "window_spec", "wheel_present", "movement_onset_time", "movement_peak_time", "movement_direction", "movement_amplitude", "mean_velocity", "max_velocity"]))
    if not wheel_features.empty:
        wheel_features["trial_id"] = wheel_features["trial_id"].astype(np.int32)
        wheel_features["wheel_present"] = wheel_features["wheel_present"].astype(bool)
        for col in ("movement_onset_time", "movement_peak_time", "movement_amplitude", "mean_velocity", "max_velocity"):
            wheel_features[col] = pd.to_numeric(wheel_features[col], errors="coerce").astype(np.float32)

    pose_features = tables.get("pose_trial_features", pd.DataFrame(columns=["eid", "trial_id", "camera", "window_spec", "pose_present", "feature_mean", "feature_peak"]))
    if not pose_features.empty:
        pose_features["trial_id"] = pose_features["trial_id"].astype(np.int32)
        pose_features["pose_present"] = pose_features["pose_present"].astype(bool)
        for col in ("feature_mean", "feature_peak"):
            pose_features[col] = pd.to_numeric(pose_features[col], errors="coerce").astype(np.float32)

    event_features = tables.get("event_aligned_behavior_features", pd.DataFrame(columns=["eid", "trial_id", "signal_name", "event_name", "window_spec", "baseline", "peak", "peak_latency_ms", "mean_response", "modulation_index"]))
    if not event_features.empty:
        event_features["trial_id"] = event_features["trial_id"].astype(np.int32)
        for col in ("baseline", "peak", "peak_latency_ms", "mean_response", "modulation_index"):
            event_features[col] = pd.to_numeric(event_features[col], errors="coerce").astype(np.float32)

    movement_state_epochs = tables.get("movement_state_epochs", pd.DataFrame(columns=["eid", "movement_id", "t_start", "t_end", "duration_s", "peak_amplitude", "peak_velocity_time", "source_signal", "detector_name", "detector_version"]))
    if not movement_state_epochs.empty:
        movement_state_epochs["movement_id"] = movement_state_epochs["movement_id"].astype(np.int32)
        for col in ("t_start", "t_end", "duration_s", "peak_amplitude", "peak_velocity_time"):
            movement_state_epochs[col] = pd.to_numeric(movement_state_epochs[col], errors="coerce").astype(np.float32)

    quiescence_state_epochs = tables.get("quiescence_state_epochs", pd.DataFrame(columns=["eid", "quiescence_id", "t_start", "t_end", "duration_s", "derived_from", "min_duration_s"]))
    if not quiescence_state_epochs.empty:
        quiescence_state_epochs["quiescence_id"] = quiescence_state_epochs["quiescence_id"].astype(np.int32)
        for col in ("t_start", "t_end", "duration_s", "min_duration_s"):
            quiescence_state_epochs[col] = pd.to_numeric(quiescence_state_epochs[col], errors="coerce").astype(np.float32)

    behavior_state_session_features = tables.get("behavior_state_session_features", pd.DataFrame(columns=["eid", "wheel_present", "movement_epoch_count", "quiescence_epoch_count", "fraction_time_moving", "fraction_time_quiescent", "median_movement_duration", "median_quiescence_duration"]))
    if not behavior_state_session_features.empty:
        behavior_state_session_features["wheel_present"] = behavior_state_session_features["wheel_present"].fillna(False).astype(bool)
        for col in ("movement_epoch_count", "quiescence_epoch_count"):
            behavior_state_session_features[col] = pd.to_numeric(behavior_state_session_features[col], errors="coerce").fillna(0).astype(np.int32)
        for col in ("fraction_time_moving", "fraction_time_quiescent", "median_movement_duration", "median_quiescence_duration"):
            behavior_state_session_features[col] = pd.to_numeric(behavior_state_session_features[col], errors="coerce").astype(np.float32)

    return wheel_availability, pose_availability, wheel_features, pose_features, event_features, movement_state_epochs, quiescence_state_epochs, behavior_state_session_features


FEATURE_TABLE_KEYS = {
    "wheel_availability": ["eid"],
    "pose_availability": ["eid", "camera"],
    "wheel_trial_features": ["eid", "trial_id", "window_spec"],
    "pose_trial_features": ["eid", "trial_id", "camera", "window_spec"],
    "event_aligned_behavior_features": ["eid", "trial_id", "signal_name", "event_name", "window_spec"],
    "movement_state_epochs": ["eid", "movement_id"],
    "quiescence_state_epochs": ["eid", "quiescence_id"],
    "behavior_state_session_features": ["eid"],
}


def _compare_feature_tables(source_tables: dict[str, pd.DataFrame], candidate_tables: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    row_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    for table_name, keys in FEATURE_TABLE_KEYS.items():
        source = source_tables.get(table_name, pd.DataFrame())
        candidate = candidate_tables.get(table_name, pd.DataFrame())
        source_keys = source[keys].drop_duplicates() if not source.empty else pd.DataFrame(columns=keys)
        candidate_keys = candidate[keys].drop_duplicates() if not candidate.empty else pd.DataFrame(columns=keys)
        paired_keys = source_keys.merge(candidate_keys, on=keys, how="inner")
        row_rows.append(
            {
                "table": table_name,
                "source_rows": int(len(source)),
                "candidate_rows": int(len(candidate)),
                "paired_rows": int(len(paired_keys)),
                "missing_candidate_rows": int(len(source_keys) - len(paired_keys)),
                "extra_candidate_rows": int(len(candidate_keys) - len(paired_keys)),
            }
        )
        if source.empty or candidate.empty or paired_keys.empty:
            continue
        merged = source.merge(candidate, on=keys, how="inner", suffixes=("_source", "_candidate"))
        source_columns = [col for col in source.columns if col not in keys]
        for column in source_columns:
            source_col = f"{column}_source"
            candidate_col = f"{column}_candidate"
            if source_col not in merged.columns or candidate_col not in merged.columns:
                continue
            source_values = merged[source_col]
            candidate_values = merged[candidate_col]
            if pd.api.types.is_numeric_dtype(source_values) or pd.api.types.is_bool_dtype(source_values):
                feature_rows.append(_numeric_feature_validation_row(table_name, column, source_values, candidate_values))
            else:
                feature_rows.append(_categorical_feature_validation_row(table_name, column, source_values, candidate_values))
    return pd.DataFrame(row_rows), pd.DataFrame(feature_rows)


def _numeric_feature_validation_row(table_name: str, column: str, source_values: pd.Series, candidate_values: pd.Series) -> dict[str, Any]:
    source_numeric = pd.to_numeric(source_values, errors="coerce")
    candidate_numeric = pd.to_numeric(candidate_values, errors="coerce")
    finite = np.isfinite(source_numeric.to_numpy(dtype=float)) & np.isfinite(candidate_numeric.to_numpy(dtype=float))
    errors = np.abs(source_numeric.to_numpy(dtype=float)[finite] - candidate_numeric.to_numpy(dtype=float)[finite])
    return {
        "table": table_name,
        "column": column,
        "kind": "numeric",
        "paired_values": int(len(source_values)),
        "finite_pairs": int(finite.sum()),
        "mean_abs_error": float(errors.mean()) if errors.size else np.nan,
        "median_abs_error": float(np.median(errors)) if errors.size else np.nan,
        "p95_abs_error": float(np.percentile(errors, 95)) if errors.size else np.nan,
        "max_abs_error": float(errors.max(initial=0.0)) if errors.size else np.nan,
        "agreement_rate": np.nan,
    }


def _categorical_feature_validation_row(table_name: str, column: str, source_values: pd.Series, candidate_values: pd.Series) -> dict[str, Any]:
    source_str = source_values.astype("string").fillna("<NA>")
    candidate_str = candidate_values.astype("string").fillna("<NA>")
    agreement = (source_str == candidate_str).to_numpy()
    return {
        "table": table_name,
        "column": column,
        "kind": "categorical",
        "paired_values": int(len(source_values)),
        "finite_pairs": 0,
        "mean_abs_error": np.nan,
        "median_abs_error": np.nan,
        "p95_abs_error": np.nan,
        "max_abs_error": np.nan,
        "agreement_rate": float(agreement.mean()) if agreement.size else np.nan,
    }


def _render_feature_validation_summary(
    *,
    dataset_dir: Path,
    shards: list[Path],
    strategy_name: str,
    row_df: pd.DataFrame,
    feature_df: pd.DataFrame,
) -> str:
    lines = [
        "# BWM behavior compression feature validation",
        "",
        f"- Dataset: `{dataset_dir}`",
        f"- Strategy: `{strategy_name}`",
        f"- Sampled shards: `{len(shards)}`",
        "",
        "## Row coverage",
        "",
        "| Table | Source rows | Candidate rows | Paired rows | Missing candidate rows | Extra candidate rows |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in row_df.itertuples(index=False):
        lines.append(
            f"| `{row.table}` | `{row.source_rows}` | `{row.candidate_rows}` | `{row.paired_rows}` | `{row.missing_candidate_rows}` | `{row.extra_candidate_rows}` |"
        )
    lines.extend(["", "## Largest numeric feature errors", ""])
    numeric = feature_df.loc[feature_df.get("kind", pd.Series(dtype=str)) == "numeric"].copy() if not feature_df.empty else pd.DataFrame()
    if numeric.empty:
        lines.append("- No paired numeric feature values were available.")
    else:
        numeric = numeric.sort_values("p95_abs_error", ascending=False, na_position="last").head(12)
        lines.extend(
            [
                "| Table | Column | Finite pairs | Mean abs error | P95 abs error | Max abs error |",
                "| --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in numeric.itertuples(index=False):
            lines.append(
                f"| `{row.table}` | `{row.column}` | `{row.finite_pairs}` | `{_format_float(row.mean_abs_error)}` | `{_format_float(row.p95_abs_error)}` | `{_format_float(row.max_abs_error)}` |"
            )
    categorical = feature_df.loc[feature_df.get("kind", pd.Series(dtype=str)) == "categorical"].copy() if not feature_df.empty else pd.DataFrame()
    if not categorical.empty:
        lines.extend(["", "## Categorical agreement", "", "| Table | Column | Agreement rate |", "| --- | --- | ---: |"])
        for row in categorical.sort_values("agreement_rate", ascending=True, na_position="last").itertuples(index=False):
            lines.append(f"| `{row.table}` | `{row.column}` | `{_format_float(row.agreement_rate)}` |")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Source features are recomputed from the original local session shards.",
            "- Candidate features are recomputed from arrays after strategy downsampling and quantization reconstruction.",
            "- This report validates derived feature stability for sampled local shards; it does not define final release thresholds.",
            "",
            "## Selected shards",
            "",
        ]
    )
    lines.extend(f"- `{path.name}`" for path in shards)
    return "\n".join(lines) + "\n"


def _select_shards(sessions_dir: Path, *, max_shards: int, selection: str) -> list[Path]:
    shards = sorted(sessions_dir.glob("*.zip"))
    if max_shards < 1:
        raise ValueError("max_shards must be >= 1")
    if selection == "largest":
        return [p for _, p in sorted(((p.stat().st_size, p) for p in shards), reverse=True)[:max_shards]]
    if selection == "smallest":
        return [p for _, p in sorted((p.stat().st_size, p) for p in shards)[:max_shards]]
    if selection == "spread":
        ranked = [p for _, p in sorted((p.stat().st_size, p) for p in shards)]
        if len(ranked) <= max_shards:
            return ranked
        indices = np.linspace(0, len(ranked) - 1, num=max_shards).round().astype(int)
        return [ranked[int(i)] for i in indices]
    raise ValueError("selection must be one of: largest, smallest, spread")


def _original_compressed_bytes(meta: dict[str, Any]) -> dict[str, int]:
    return {name: int(spec.get("compressed_nbytes", 0)) for name, spec in meta.get("arrays", {}).items()}


def _columns_for_array(meta: dict[str, Any], array_name: str) -> list[str] | None:
    if not array_name.endswith(".features"):
        return None
    camera = array_name.split(".", 1)[0]
    columns = meta.get("cameras", {}).get(camera, {}).get("columns")
    return list(columns) if columns is not None else None


def _prepare_candidate_arrays(arrays: dict[str, np.ndarray], *, strategy: Strategy) -> dict[str, np.ndarray]:
    if (
        strategy.wheel_downsample_rate_hz is None
        and strategy.pose_downsample_rate_hz is None
        and not strategy.pose_camera_downsample_rates_hz
    ):
        return arrays

    prepared = dict(arrays)
    sample_indices: dict[str, np.ndarray] = {}
    for array_name, arr in arrays.items():
        if not array_name.endswith(".timestamps"):
            continue
        signal_name = array_name[: -len(".timestamps")]
        rate_hz = _downsample_rate_for_signal(signal_name, strategy)
        if rate_hz is None:
            continue
        sample_indices[signal_name] = _downsample_indices_nearest(arr, rate_hz=rate_hz)

    for signal_name, indices in sample_indices.items():
        timestamps_name = f"{signal_name}.timestamps"
        if timestamps_name in prepared:
            prepared[timestamps_name] = np.asarray(arrays[timestamps_name])[indices]
        features_name = f"{signal_name}.features"
        if features_name in prepared:
            prepared[features_name] = np.asarray(arrays[features_name])[indices]

    wheel_indices = sample_indices.get("wheel")
    if wheel_indices is not None:
        for array_name in ("wheel.position", "wheel.velocity"):
            if array_name in prepared:
                prepared[array_name] = np.asarray(arrays[array_name])[wheel_indices]

    return prepared


def _downsample_rate_for_signal(signal_name: str, strategy: Strategy) -> float | None:
    if signal_name == "wheel":
        return strategy.wheel_downsample_rate_hz
    if strategy.pose_camera_downsample_rates_hz is not None and signal_name in strategy.pose_camera_downsample_rates_hz:
        return strategy.pose_camera_downsample_rates_hz[signal_name]
    return strategy.pose_downsample_rate_hz


def _encode_array_for_storage(
    array_name: str,
    arr: np.ndarray,
    *,
    columns: list[str] | None,
    strategy: Strategy,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    payloads: dict[str, bytes] = {}
    base_entry = f"arrays/{array_name}"
    if strategy.name == "lossless-baseline":
        payload, spec = bwm_shared.compress_array(arr)
        entry = f"{base_entry}.blosc"
        payloads[entry] = payload
        spec.update({"entry": entry, "encoding": {"kind": "raw_blosc"}})
        return spec, payloads
    if array_name.endswith(".features") and columns:
        spec = _encode_feature_matrix_for_storage(base_entry=base_entry, matrix=np.asarray(arr), columns=columns, strategy=strategy, payloads=payloads)
        return spec, payloads
    if array_name.endswith(".timestamps") and strategy.timestamp_tick_s is not None:
        rate_hz = _downsample_rate_for_signal(array_name[: -len(".timestamps")], strategy)
        if strategy.fixed_rate_timestamps and rate_hz is not None:
            return _encode_fixed_rate_timestamps_for_storage(np.asarray(arr), rate_hz=rate_hz), payloads
        spec = _encode_timestamps_for_storage(base_entry=base_entry, values=np.asarray(arr), tick_s=strategy.timestamp_tick_s, payloads=payloads)
        return spec, payloads
    if array_name == "wheel.position" and strategy.wheel_position_precision is not None:
        spec = _encode_scaled_numeric_for_storage(base_entry=base_entry, values=np.asarray(arr), precision=strategy.wheel_position_precision, dtype=np.int32, payloads=payloads)
        return spec, payloads
    if array_name == "wheel.velocity" and strategy.wheel_velocity_precision is not None:
        velocity_dtype = np.int32 if strategy.wheel_velocity_dtype == "int32" else np.int16
        spec = _encode_scaled_numeric_for_storage(base_entry=base_entry, values=np.asarray(arr), precision=strategy.wheel_velocity_precision, dtype=velocity_dtype, payloads=payloads)
        return spec, payloads
    payload, spec = bwm_shared.compress_array(arr)
    entry = f"{base_entry}.blosc"
    payloads[entry] = payload
    spec.update({"entry": entry, "encoding": {"kind": "raw_blosc"}})
    return spec, payloads


def _decode_array_from_storage(*, zf: zipfile.ZipFile, array_name: str, spec: dict[str, Any]) -> np.ndarray:
    encoding = spec.get("encoding", {})
    kind = encoding.get("kind", "raw_blosc")
    if kind == "raw_blosc":
        return bwm_shared.decompress_array(zf.read(spec["entry"]), spec)
    if kind == "timestamp_fixed_rate":
        count = int(encoding["count"])
        start = float(encoding["start"])
        rate_hz = float(encoding["rate_hz"])
        if count == 0:
            return np.asarray([], dtype=np.float64)
        return start + np.arange(count, dtype=np.float64) / rate_hz
    if kind == "timestamp_delta_ticks":
        ticks = bwm_shared.decompress_array(zf.read(encoding["ticks_entry"]), encoding["ticks_spec"])
        return float(encoding["start"]) + ticks.astype(np.float64) * float(encoding["tick_s"])
    if kind == "scaled_numeric":
        encoded = bwm_shared.decompress_array(zf.read(encoding["entry"]), encoding["payload_spec"])
        return _decode_scaled_numeric(encoded, precision=float(encoding["precision"]))
    if kind == "feature_matrix":
        return _decode_feature_matrix_from_storage(zf=zf, spec=encoding)
    raise ValueError(f"Unsupported encoded shard kind for {array_name}: {kind}")


def _encode_feature_matrix_for_storage(
    *,
    base_entry: str,
    matrix: np.ndarray,
    columns: list[str],
    strategy: Strategy,
    payloads: dict[str, bytes],
) -> dict[str, Any]:
    if matrix.ndim != 2 or matrix.shape[1] != len(columns):
        payload, spec = bwm_shared.compress_array(matrix)
        entry = f"{base_entry}.blosc"
        payloads[entry] = payload
        spec.update({"entry": entry, "encoding": {"kind": "raw_blosc"}})
        return spec
    groups = _feature_column_groups(columns)
    if strategy.per_column_pose:
        groups = {f"{group_name}:{idx}:{columns[idx]}": [idx] for group_name, indices in groups.items() for idx in indices}
    group_specs: list[dict[str, Any]] = []
    for group_name, indices in groups.items():
        if not indices:
            continue
        block = matrix[:, indices]
        base_group_name = group_name.split(":", 1)[0]
        entry_prefix = f"{base_entry}.{group_name.replace(':', '_')}"
        if base_group_name == "xy" and strategy.xy_precision_px is not None:
            if strategy.xy_delta and np.isfinite(block).all():
                absolute, _, _ = _quantize_scaled(block, precision=strategy.xy_precision_px, dtype=np.int16, kind="xy_int16")
                absolute64 = absolute.astype(np.int64)
                first = absolute[:1].copy()
                delta64 = np.diff(absolute64, axis=0)
                delta_info = np.iinfo(np.int16)
                deltas = delta64.astype(np.int32) if delta64.size and (int(delta64.min()) < delta_info.min or int(delta64.max()) > delta_info.max) else delta64.astype(np.int16)
                first_entry = f"{entry_prefix}.first.blosc"
                delta_entry = f"{entry_prefix}.deltas.blosc"
                first_payload, first_spec = bwm_shared.compress_array(first)
                delta_payload, delta_spec = bwm_shared.compress_array(deltas)
                payloads[first_entry] = first_payload
                payloads[delta_entry] = delta_payload
                group_specs.append({"columns": indices, "kind": "delta_scaled", "precision": float(strategy.xy_precision_px), "first_entry": first_entry, "first_spec": first_spec, "delta_entry": delta_entry, "delta_spec": delta_spec, "shape": list(block.shape)})
            else:
                group_specs.append(_encode_feature_scaled_group(entry_prefix=entry_prefix, indices=indices, block=block, precision=float(strategy.xy_precision_px), payloads=payloads))
        elif base_group_name == "likelihood" and strategy.likelihood_uint8:
            encoded, _, _ = _quantize_likelihood(block, bits=strategy.likelihood_bits)
            payload_entry = f"{entry_prefix}.payload.blosc"
            payload, payload_spec = _compress_encoded_part(encoded)
            payloads[payload_entry] = payload
            group_spec = {"columns": indices, "kind": "likelihood", "bits": int(strategy.likelihood_bits), "entry": payload_entry, "payload_spec": payload_spec if isinstance(payload_spec, dict) else {}}
            if np.issubdtype(block.dtype, np.floating) and (~np.isfinite(block)).any():
                mask_entry = f"{entry_prefix}.nan_mask.blosc"
                mask_payload, mask_spec = bwm_shared.compress_array(_pack_nan_mask(block))
                payloads[mask_entry] = mask_payload
                group_spec["nan_mask_entry"] = mask_entry
                group_spec["nan_mask_spec"] = mask_spec
            group_specs.append(group_spec)
        elif strategy.other_precision is not None:
            group_specs.append(_encode_feature_scaled_group(entry_prefix=entry_prefix, indices=indices, block=block, precision=float(strategy.other_precision), payloads=payloads))
        elif strategy.other_float16:
            encoded = block.astype(np.float16)
            entry = f"{entry_prefix}.payload.blosc"
            payload, payload_spec = bwm_shared.compress_array(encoded)
            payloads[entry] = payload
            group_specs.append({"columns": indices, "kind": "float16", "entry": entry, "payload_spec": payload_spec})
        else:
            entry = f"{entry_prefix}.payload.blosc"
            payload, payload_spec = bwm_shared.compress_array(block)
            payloads[entry] = payload
            group_specs.append({"columns": indices, "kind": "raw", "entry": entry, "payload_spec": payload_spec})
    return {"dtype": "<f4", "shape": list(matrix.shape), "encoding": {"kind": "feature_matrix", "shape": list(matrix.shape), "columns": columns, "groups": group_specs}}


def _encode_feature_scaled_group(
    *,
    entry_prefix: str,
    indices: list[int],
    block: np.ndarray,
    precision: float,
    payloads: dict[str, bytes],
) -> dict[str, Any]:
    encoded, _, _ = _quantize_scaled(block, precision=precision, dtype=np.int16, kind="scaled")
    entry = f"{entry_prefix}.payload.blosc"
    payload, payload_spec = bwm_shared.compress_array(encoded)
    payloads[entry] = payload
    return {"columns": indices, "kind": "scaled", "precision": precision, "entry": entry, "payload_spec": payload_spec}


def _encode_fixed_rate_timestamps_for_storage(values: np.ndarray, *, rate_hz: float) -> dict[str, Any]:
    values = np.asarray(values, dtype=np.float64)
    return {"dtype": "<f8", "shape": list(values.shape), "encoding": {"kind": "timestamp_fixed_rate", "start": (float(values[0]) if values.size else float("nan")), "rate_hz": float(rate_hz), "count": int(values.size)}}


def _encode_timestamps_for_storage(
    *,
    base_entry: str,
    values: np.ndarray,
    tick_s: float,
    payloads: dict[str, bytes],
) -> dict[str, Any]:
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        ticks = np.asarray([], dtype=np.int32)
        start = float("nan")
    else:
        start = float(values[0])
        ticks_raw = np.rint((values - start) / tick_s)
        ticks = ticks_raw.astype(np.uint32) if np.nanmin(ticks_raw) >= 0 and np.nanmax(ticks_raw) <= np.iinfo(np.uint32).max else ticks_raw.astype(np.int64)
    entry = f"{base_entry}.ticks.blosc"
    payload, payload_spec = bwm_shared.compress_array(ticks)
    payloads[entry] = payload
    return {"dtype": "<f8", "shape": list(values.shape), "encoding": {"kind": "timestamp_delta_ticks", "start": start, "tick_s": float(tick_s), "ticks_entry": entry, "ticks_spec": payload_spec}}


def _encode_scaled_numeric_for_storage(
    *,
    base_entry: str,
    values: np.ndarray,
    precision: float,
    dtype: np.dtype,
    payloads: dict[str, bytes],
) -> dict[str, Any]:
    encoded, _, _ = _quantize_scaled(np.asarray(values), precision=precision, dtype=dtype, kind="scaled_numeric")
    entry = f"{base_entry}.payload.blosc"
    payload, payload_spec = bwm_shared.compress_array(encoded)
    payloads[entry] = payload
    return {"dtype": "<f4", "shape": list(np.asarray(values).shape), "encoding": {"kind": "scaled_numeric", "precision": float(precision), "entry": entry, "payload_spec": payload_spec}}


def _decode_feature_matrix_from_storage(*, zf: zipfile.ZipFile, spec: dict[str, Any]) -> np.ndarray:
    columns = list(spec["columns"])
    shape = tuple(spec["groups"][0].get("shape", [0, len(columns)])) if spec.get("groups") else (0, len(columns))
    if "shape" in spec:
        shape = tuple(spec["shape"])
    reconstructed = np.empty(shape, dtype=np.float32)
    for group in spec["groups"]:
        indices = group["columns"]
        kind = group["kind"]
        if kind == "delta_scaled":
            first = bwm_shared.decompress_array(zf.read(group["first_entry"]), group["first_spec"]).astype(np.int64)
            deltas = bwm_shared.decompress_array(zf.read(group["delta_entry"]), group["delta_spec"]).astype(np.int64)
            absolute = np.concatenate([first, first + np.cumsum(deltas, axis=0)], axis=0) if deltas.size else first
            decoded = absolute.astype(np.float32) * np.float32(group["precision"])
        elif kind == "scaled":
            encoded = bwm_shared.decompress_array(zf.read(group["entry"]), group["payload_spec"])
            decoded = _decode_scaled_numeric(encoded, precision=float(group["precision"]))
        elif kind == "likelihood":
            payload = zf.read(group["entry"])
            if group["bits"] == 4:
                packed = bwm_shared.decompress_array(payload, group["payload_spec"]["packed_uint4"])
                decoded = _unpack_uint4(packed, shape=tuple(group["payload_spec"]["shape"])).astype(np.float32) / np.float32((2 ** int(group["bits"])) - 1)
            else:
                encoded = bwm_shared.decompress_array(payload, group["payload_spec"])
                decoded = encoded.astype(np.float32) / np.float32((2 ** int(group["bits"])) - 1)
            if "nan_mask_entry" in group:
                nan_mask = _unpack_nan_mask(bwm_shared.decompress_array(zf.read(group["nan_mask_entry"]), group["nan_mask_spec"]), shape=decoded.shape)
                decoded[nan_mask] = np.nan
        elif kind == "float16":
            decoded = bwm_shared.decompress_array(zf.read(group["entry"]), group["payload_spec"]).astype(np.float16).astype(np.float32)
        else:
            decoded = bwm_shared.decompress_array(zf.read(group["entry"]), group["payload_spec"]).astype(np.float32)
        reconstructed[:, indices] = decoded
    return reconstructed


def _decode_scaled_numeric(encoded: np.ndarray, *, precision: float) -> np.ndarray:
    arr = np.asarray(encoded)
    decoded = arr.astype(np.float32) * np.float32(precision)
    if np.issubdtype(arr.dtype, np.integer):
        sentinel = np.iinfo(arr.dtype).min
        decoded[arr == sentinel] = np.nan
    return decoded


def _pack_nan_mask(values: np.ndarray) -> np.ndarray:
    return np.packbits((~np.isfinite(values)).ravel())


def _unpack_nan_mask(values: np.ndarray, *, shape: tuple[int, ...]) -> np.ndarray:
    unpacked = np.unpackbits(np.asarray(values, dtype=np.uint8))[: int(np.prod(shape))]
    return unpacked.astype(bool).reshape(shape)


def _unpack_uint4(values: np.ndarray, *, shape: tuple[int, ...]) -> np.ndarray:
    packed = np.asarray(values, dtype=np.uint8).ravel()
    unpacked = np.empty(packed.size * 2, dtype=np.uint8)
    unpacked[0::2] = (packed >> np.uint8(4)) & np.uint8(0x0F)
    unpacked[1::2] = packed & np.uint8(0x0F)
    return unpacked[: int(np.prod(shape))].reshape(shape)


def _downsample_indices_nearest(times: np.ndarray, *, rate_hz: float) -> np.ndarray:
    values = np.asarray(times, dtype=np.float64)
    finite = np.isfinite(values)
    if values.size == 0:
        return np.asarray([], dtype=np.int64)
    if not bool(finite.any()):
        return np.arange(values.size, dtype=np.int64)

    valid_indices = np.flatnonzero(finite)
    valid_times = values[valid_indices]
    start = valid_times[0]
    stop = valid_times[-1]
    if stop <= start:
        return valid_indices[:1].astype(np.int64)

    dt = 1.0 / float(rate_hz)
    grid = start + np.arange(int(np.floor((stop - start) / dt)) + 1, dtype=np.float64) * dt
    positions = np.searchsorted(valid_times, grid, side="left")
    positions = np.clip(positions, 0, len(valid_times) - 1)
    previous = np.maximum(positions - 1, 0)
    choose_previous = np.abs(valid_times[previous] - grid) <= np.abs(valid_times[positions] - grid)
    nearest_positions = np.where(choose_previous, previous, positions)
    return np.unique(valid_indices[nearest_positions]).astype(np.int64)


def _encode_candidate(array_name: str, arr: np.ndarray, *, columns: list[str] | None, strategy: Strategy) -> dict[str, Any]:
    if strategy.name == "lossless-baseline":
        payload, _ = bwm_shared.compress_array(arr)
        return _encoding_result(
            signal_class=_signal_class(array_name),
            compressed_bytes=len(payload),
            encoded_parts=[{"kind": "lossless", "dtype": str(arr.dtype), "shape": list(arr.shape), "bytes": len(payload)}],
            original=arr,
            reconstructed=arr,
        )

    if array_name.endswith(".features") and columns:
        return _encode_feature_matrix(array_name, arr, columns=columns, strategy=strategy)
    if array_name.endswith(".timestamps") and strategy.timestamp_tick_s is not None:
        rate_hz = _downsample_rate_for_signal(array_name[: -len(".timestamps")], strategy)
        if strategy.fixed_rate_timestamps and rate_hz is not None:
            return _encode_fixed_rate_timestamps(array_name, arr, rate_hz=rate_hz)
        return _encode_timestamps(array_name, arr, tick_s=strategy.timestamp_tick_s)
    if array_name == "wheel.position" and strategy.wheel_position_precision is not None:
        return _encode_scaled_numeric(array_name, arr, precision=strategy.wheel_position_precision, dtype=np.int32)
    if array_name == "wheel.velocity" and strategy.wheel_velocity_precision is not None:
        velocity_dtype = np.int32 if strategy.wheel_velocity_dtype == "int32" else np.int16
        return _encode_scaled_numeric(array_name, arr, precision=strategy.wheel_velocity_precision, dtype=velocity_dtype)

    payload, _ = bwm_shared.compress_array(arr)
    return _encoding_result(
        signal_class=_signal_class(array_name),
        compressed_bytes=len(payload),
        encoded_parts=[{"kind": "unchanged", "dtype": str(arr.dtype), "shape": list(arr.shape), "bytes": len(payload)}],
        original=arr,
        reconstructed=arr,
    )


def _encode_feature_matrix(array_name: str, arr: np.ndarray, *, columns: list[str], strategy: Strategy) -> dict[str, Any]:
    matrix = np.asarray(arr)
    if matrix.ndim != 2 or matrix.shape[1] != len(columns):
        payload, _ = bwm_shared.compress_array(matrix)
        return _encoding_result(
            signal_class=_signal_class(array_name),
            compressed_bytes=len(payload),
            encoded_parts=[{"kind": "unchanged_shape_mismatch", "bytes": len(payload)}],
            original=matrix,
            reconstructed=matrix,
        )

    parts: list[dict[str, Any]] = []
    reconstructed = np.empty(matrix.shape, dtype=np.float32)
    total_bytes = 0
    groups = _feature_column_groups(columns)
    if strategy.per_column_pose:
        groups = {f"{group_name}:{idx}:{columns[idx]}": [idx] for group_name, indices in groups.items() for idx in indices}
    for group_name, indices in groups.items():
        if not indices:
            continue
        block = matrix[:, indices]
        base_group_name = group_name.split(":", 1)[0]
        if base_group_name == "xy" and strategy.xy_precision_px is not None:
            if strategy.xy_delta:
                encoded, decoded, part = _quantize_delta_scaled(block, precision=strategy.xy_precision_px, dtype=np.int16, kind="xy_delta_int16")
            else:
                encoded, decoded, part = _quantize_scaled(block, precision=strategy.xy_precision_px, dtype=np.int16, kind="xy_int16")
        elif base_group_name == "likelihood" and strategy.likelihood_uint8:
            encoded, decoded, part = _quantize_likelihood(block, bits=strategy.likelihood_bits)
        elif strategy.other_precision is not None:
            encoded, decoded, part = _quantize_scaled(block, precision=strategy.other_precision, dtype=np.int16, kind=f"{base_group_name}_int16")
        elif strategy.other_float16:
            encoded = block.astype(np.float16)
            decoded = encoded.astype(np.float32)
            part = {"kind": f"{base_group_name}_float16", "dtype": "float16", "shape": list(encoded.shape)}
        else:
            encoded = block
            decoded = block
            part = {"kind": f"{base_group_name}_unchanged", "dtype": str(block.dtype), "shape": list(block.shape)}
        payload, _ = _compress_encoded_part(encoded)
        mask_bytes = _compressed_nan_mask_bytes(block)
        part["bytes"] = int(len(payload) + mask_bytes)
        part["payload_bytes"] = int(len(payload))
        part["nan_mask_bytes"] = int(mask_bytes)
        if strategy.per_column_pose:
            part["column"] = columns[indices[0]]
        parts.append(part)
        total_bytes += int(part["bytes"])
        reconstructed[:, indices] = decoded

    return _encoding_result(
        signal_class=_signal_class(array_name),
        compressed_bytes=total_bytes,
        encoded_parts=parts,
        original=matrix,
        reconstructed=reconstructed,
    )


def _feature_column_groups(columns: list[str]) -> dict[str, list[int]]:
    groups = {"xy": [], "likelihood": [], "other": []}
    for idx, col in enumerate(columns):
        low = col.lower()
        if low.endswith("_x") or low.endswith("_y") or low.endswith("[0]") or low.endswith("[1]"):
            groups["xy"].append(idx)
        elif "likelihood" in low:
            groups["likelihood"].append(idx)
        else:
            groups["other"].append(idx)
    return groups


def _quantize_scaled(block: np.ndarray, *, precision: float, dtype: np.dtype, kind: str) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    finite = np.isfinite(block)
    scale = 1.0 / precision
    info = np.iinfo(dtype)
    sentinel = info.min
    encoded64 = np.rint(np.where(finite, block, 0.0) * scale)
    clipped = np.clip(encoded64, info.min + 1, info.max)
    encoded = clipped.astype(dtype)
    encoded[~finite] = sentinel
    decoded = encoded.astype(np.float32) / np.float32(scale)
    decoded[~finite] = np.nan
    return encoded, decoded, {"kind": kind, "precision": precision, "dtype": np.dtype(dtype).name, "shape": list(encoded.shape)}


def _quantize_delta_scaled(block: np.ndarray, *, precision: float, dtype: np.dtype, kind: str) -> tuple[dict[str, np.ndarray], np.ndarray, dict[str, Any]]:
    absolute, decoded, part = _quantize_scaled(block, precision=precision, dtype=dtype, kind=kind)
    if absolute.shape[0] == 0:
        first = absolute[:0].copy()
        deltas = absolute[:0].copy()
    else:
        absolute64 = absolute.astype(np.int64)
        first = absolute[:1].copy()
        delta64 = np.diff(absolute64, axis=0)
        delta_info = np.iinfo(dtype)
        if delta64.size and (int(delta64.min()) < delta_info.min or int(delta64.max()) > delta_info.max):
            deltas = delta64.astype(np.int32)
        else:
            deltas = delta64.astype(dtype)
    encoded = {"first": first, "deltas": deltas}
    part.update(
        {
            "first_dtype": str(first.dtype),
            "delta_dtype": str(deltas.dtype),
            "first_shape": list(first.shape),
            "delta_shape": list(deltas.shape),
        }
    )
    return encoded, decoded, part


def _compress_encoded_part(encoded: np.ndarray | dict[str, np.ndarray]) -> tuple[bytes, dict[str, Any]]:
    if isinstance(encoded, dict):
        payloads = []
        metadata: dict[str, Any] = {}
        for name, values in encoded.items():
            payload, meta = bwm_shared.compress_array(values)
            payloads.append(payload)
            metadata[name] = meta
        return b"".join(payloads), metadata
    return bwm_shared.compress_array(encoded)


def _quantize_likelihood(block: np.ndarray, *, bits: int = 8) -> tuple[np.ndarray | dict[str, np.ndarray], np.ndarray, dict[str, Any]]:
    if bits not in (4, 8):
        raise ValueError("likelihood bits must be 4 or 8")
    finite = np.isfinite(block)
    levels = (2**bits) - 1
    clipped = np.clip(np.where(finite, block, 0.0), 0.0, 1.0)
    quantized = np.rint(clipped * float(levels)).astype(np.uint8)
    decoded = quantized.astype(np.float32) / np.float32(levels)
    decoded[~finite] = np.nan
    if bits == 4:
        encoded: np.ndarray | dict[str, np.ndarray] = {"packed_uint4": _pack_uint4(quantized)}
        dtype = "uint4-packed"
        kind = "likelihood_uint4"
    else:
        encoded = quantized
        dtype = "uint8"
        kind = "likelihood_uint8"
    return encoded, decoded, {"kind": kind, "precision": 1.0 / float(levels), "dtype": dtype, "shape": list(quantized.shape)}


def _pack_uint4(values: np.ndarray) -> np.ndarray:
    flat = np.asarray(values, dtype=np.uint8).ravel()
    if flat.size % 2:
        flat = np.concatenate([flat, np.zeros(1, dtype=np.uint8)])
    high = (flat[0::2] & np.uint8(0x0F)) << np.uint8(4)
    low = flat[1::2] & np.uint8(0x0F)
    return (high | low).astype(np.uint8)


def _encode_timestamps(array_name: str, arr: np.ndarray, *, tick_s: float) -> dict[str, Any]:
    values = np.asarray(arr, dtype=np.float64)
    if values.size == 0:
        encoded = np.asarray([], dtype=np.int32)
        decoded = values
    else:
        first = values[:1]
        ticks = np.rint((values - first[0]) / tick_s)
        if np.nanmin(ticks) >= np.iinfo(np.uint32).min and np.nanmax(ticks) <= np.iinfo(np.uint32).max:
            encoded = ticks.astype(np.uint32)
        else:
            encoded = ticks.astype(np.int64)
        decoded = first[0] + encoded.astype(np.float64) * tick_s
    payload, _ = bwm_shared.compress_array(encoded)
    first_payload, _ = bwm_shared.compress_array(values[:1])
    return _encoding_result(
        signal_class=_signal_class(array_name),
        compressed_bytes=len(payload) + len(first_payload),
        encoded_parts=[{"kind": "timestamp_delta_ticks", "tick_s": tick_s, "dtype": str(encoded.dtype), "shape": list(encoded.shape), "bytes": len(payload) + len(first_payload)}],
        original=values,
        reconstructed=decoded,
    )


def _encode_fixed_rate_timestamps(array_name: str, arr: np.ndarray, *, rate_hz: float) -> dict[str, Any]:
    values = np.asarray(arr, dtype=np.float64)
    if values.size == 0:
        header = np.asarray([np.nan, float(rate_hz)], dtype=np.float64)
        count = np.asarray([0], dtype=np.uint32)
        decoded = values
    else:
        header = np.asarray([values[0], float(rate_hz)], dtype=np.float64)
        count = np.asarray([values.size], dtype=np.uint32)
        decoded = values[0] + np.arange(values.size, dtype=np.float64) / float(rate_hz)
        finite = np.isfinite(values) & np.isfinite(decoded)
        if bool(finite.any()):
            max_deviation = float(np.max(np.abs(values[finite] - decoded[finite]), initial=0.0))
            if max_deviation > 0.5 / float(rate_hz):
                return _encode_timestamps(array_name, values, tick_s=0.001)
    header_payload, _ = bwm_shared.compress_array(header)
    count_payload, _ = bwm_shared.compress_array(count)
    bytes_ = len(header_payload) + len(count_payload)
    return _encoding_result(
        signal_class=_signal_class(array_name),
        compressed_bytes=bytes_,
        encoded_parts=[
            {
                "kind": "timestamp_fixed_rate",
                "rate_hz": float(rate_hz),
                "header_dtype": str(header.dtype),
                "count_dtype": str(count.dtype),
                "shape": list(values.shape),
                "bytes": bytes_,
            }
        ],
        original=values,
        reconstructed=decoded,
    )


def _encode_scaled_numeric(array_name: str, arr: np.ndarray, *, precision: float, dtype: np.dtype) -> dict[str, Any]:
    encoded, decoded, part = _quantize_scaled(np.asarray(arr), precision=precision, dtype=dtype, kind="scaled_numeric")
    payload, _ = bwm_shared.compress_array(encoded)
    mask_bytes = _compressed_nan_mask_bytes(np.asarray(arr))
    part["bytes"] = int(len(payload) + mask_bytes)
    part["payload_bytes"] = int(len(payload))
    part["nan_mask_bytes"] = int(mask_bytes)
    return _encoding_result(
        signal_class=_signal_class(array_name),
        compressed_bytes=int(part["bytes"]),
        encoded_parts=[part],
        original=np.asarray(arr),
        reconstructed=decoded,
    )


def _reconstruct_candidate_array(array_name: str, arr: np.ndarray, *, columns: list[str] | None, strategy: Strategy) -> np.ndarray:
    if strategy.name == "lossless-baseline":
        return np.asarray(arr)
    if array_name.endswith(".features") and columns:
        return _reconstruct_feature_matrix(arr, columns=columns, strategy=strategy)
    if array_name.endswith(".timestamps") and strategy.timestamp_tick_s is not None:
        rate_hz = _downsample_rate_for_signal(array_name[: -len(".timestamps")], strategy)
        if strategy.fixed_rate_timestamps and rate_hz is not None:
            fixed = _try_reconstruct_fixed_rate_timestamps(arr, rate_hz=rate_hz)
            if fixed is not None:
                return fixed
        return _reconstruct_timestamps(arr, tick_s=strategy.timestamp_tick_s)
    if array_name == "wheel.position" and strategy.wheel_position_precision is not None:
        return _reconstruct_scaled_numeric(arr, precision=strategy.wheel_position_precision, dtype=np.int32)
    if array_name == "wheel.velocity" and strategy.wheel_velocity_precision is not None:
        velocity_dtype = np.int32 if strategy.wheel_velocity_dtype == "int32" else np.int16
        return _reconstruct_scaled_numeric(arr, precision=strategy.wheel_velocity_precision, dtype=velocity_dtype)
    return np.asarray(arr)


def _reconstruct_feature_matrix(arr: np.ndarray, *, columns: list[str], strategy: Strategy) -> np.ndarray:
    matrix = np.asarray(arr)
    if matrix.ndim != 2 or matrix.shape[1] != len(columns):
        return matrix

    reconstructed = np.empty(matrix.shape, dtype=np.float32)
    groups = _feature_column_groups(columns)
    if strategy.per_column_pose:
        groups = {f"{group_name}:{idx}:{columns[idx]}": [idx] for group_name, indices in groups.items() for idx in indices}
    for group_name, indices in groups.items():
        if not indices:
            continue
        block = matrix[:, indices]
        base_group_name = group_name.split(":", 1)[0]
        if base_group_name == "xy" and strategy.xy_precision_px is not None:
            _, decoded, _ = _quantize_scaled(block, precision=strategy.xy_precision_px, dtype=np.int16, kind="xy_int16")
        elif base_group_name == "likelihood" and strategy.likelihood_uint8:
            _, decoded, _ = _quantize_likelihood(block, bits=strategy.likelihood_bits)
        elif strategy.other_precision is not None:
            _, decoded, _ = _quantize_scaled(block, precision=strategy.other_precision, dtype=np.int16, kind=f"{base_group_name}_int16")
        elif strategy.other_float16:
            decoded = block.astype(np.float16).astype(np.float32)
        else:
            decoded = block
        reconstructed[:, indices] = decoded
    return reconstructed


def _reconstruct_timestamps(arr: np.ndarray, *, tick_s: float) -> np.ndarray:
    values = np.asarray(arr, dtype=np.float64)
    if values.size == 0:
        return values
    ticks = np.rint((values - values[0]) / tick_s)
    return values[0] + ticks.astype(np.float64) * tick_s


def _try_reconstruct_fixed_rate_timestamps(arr: np.ndarray, *, rate_hz: float) -> np.ndarray | None:
    values = np.asarray(arr, dtype=np.float64)
    if values.size == 0:
        return values
    decoded = values[0] + np.arange(values.size, dtype=np.float64) / float(rate_hz)
    finite = np.isfinite(values) & np.isfinite(decoded)
    if bool(finite.any()):
        max_deviation = float(np.max(np.abs(values[finite] - decoded[finite]), initial=0.0))
        if max_deviation > 0.5 / float(rate_hz):
            return None
    return decoded


def _reconstruct_scaled_numeric(arr: np.ndarray, *, precision: float, dtype: np.dtype) -> np.ndarray:
    _, decoded, _ = _quantize_scaled(np.asarray(arr), precision=precision, dtype=dtype, kind="scaled_numeric")
    return decoded


def _compressed_nan_mask_bytes(arr: np.ndarray) -> int:
    if not np.issubdtype(arr.dtype, np.floating):
        return 0
    mask = ~np.isfinite(arr)
    if not bool(mask.any()):
        return 0
    packed = np.packbits(mask.ravel())
    payload, _ = bwm_shared.compress_array(packed)
    return len(payload)


def _encoding_result(*, signal_class: str, compressed_bytes: int, encoded_parts: list[dict[str, Any]], original: np.ndarray, reconstructed: np.ndarray) -> dict[str, Any]:
    orig = np.asarray(original, dtype=np.float64)
    rec = np.asarray(reconstructed, dtype=np.float64)
    finite = np.isfinite(orig) & np.isfinite(rec)
    if bool(finite.any()):
        errors = np.abs(orig[finite] - rec[finite])
        max_abs_error = float(errors.max(initial=0.0))
        mean_abs_error = float(errors.mean())
        n_values = int(errors.size)
    else:
        max_abs_error = 0.0
        mean_abs_error = 0.0
        n_values = 0
    return {
        "signal_class": signal_class,
        "compressed_bytes": int(compressed_bytes),
        "encoded_parts": encoded_parts,
        "max_abs_error": max_abs_error,
        "mean_abs_error": mean_abs_error,
        "n_values": n_values,
    }


def _validation_row(*, shard: str, array_name: str, signal_class: str, source: np.ndarray, candidate: np.ndarray, reconstructed: np.ndarray) -> dict[str, Any]:
    source_rows = int(source.shape[0]) if source.ndim else int(source.size)
    candidate_rows = int(candidate.shape[0]) if candidate.ndim else int(candidate.size)
    orig = np.asarray(candidate, dtype=np.float64)
    rec = np.asarray(reconstructed, dtype=np.float64)
    finite = np.isfinite(orig) & np.isfinite(rec)
    if bool(finite.any()):
        errors = np.abs(orig[finite] - rec[finite])
        max_abs_error = float(errors.max(initial=0.0))
        mean_abs_error = float(errors.mean())
        p95_abs_error = float(np.percentile(errors, 95))
        n_values = int(errors.size)
    else:
        max_abs_error = 0.0
        mean_abs_error = 0.0
        p95_abs_error = 0.0
        n_values = 0
    return {
        "shard": shard,
        "array": array_name,
        "signal_class": signal_class,
        "source_shape": json.dumps(list(source.shape)),
        "candidate_shape": json.dumps(list(candidate.shape)),
        "retained_row_ratio": float(candidate_rows / source_rows) if source_rows else 1.0,
        "max_abs_error": max_abs_error,
        "mean_abs_error": mean_abs_error,
        "p95_abs_error": p95_abs_error,
        "n_values": n_values,
    }


def _signal_class(array_name: str) -> str:
    if array_name.endswith(".features"):
        return "pose_features"
    if array_name.endswith(".timestamps"):
        return "timestamps"
    if array_name.startswith("wheel."):
        return "wheel"
    return "other"


def _safe_factor(original: int | float, candidate: int | float) -> float:
    if candidate <= 0:
        return math.inf
    return float(original) / float(candidate)


def _summarize_strategy_metrics(shard_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for strategy, group in shard_df.groupby("strategy", sort=False):
        original = int(group["original_compressed_bytes"].sum())
        candidate = int(group["candidate_compressed_bytes"].sum())
        rows.append(
            {
                "strategy": strategy,
                "sampled_shards": int(group["shard"].nunique()),
                "original_compressed_bytes": original,
                "candidate_compressed_bytes": candidate,
                "compression_factor_vs_current": _safe_factor(original, candidate),
                "median_shard_factor": float(group["compression_factor_vs_current"].median()),
                "max_abs_error": float(group["max_abs_error"].max()),
                "mean_abs_error": float(
                    np.average(
                        group["mean_abs_error"].to_numpy(dtype=float),
                        weights=np.maximum(group["original_compressed_bytes"].to_numpy(dtype=float), 1.0),
                    )
                ),
            }
        )
    return pd.DataFrame(rows)


def _summarize_validation_metrics(validation_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for signal_class, group in validation_df.groupby("signal_class", sort=False):
        weights = np.maximum(group["n_values"].to_numpy(dtype=float), 1.0)
        rows.append(
            {
                "signal_class": signal_class,
                "arrays": int(len(group)),
                "median_retained_row_ratio": float(group["retained_row_ratio"].median()),
                "max_abs_error": float(group["max_abs_error"].max()),
                "mean_abs_error": float(np.average(group["mean_abs_error"].to_numpy(dtype=float), weights=weights)),
                "p95_abs_error": float(np.average(group["p95_abs_error"].to_numpy(dtype=float), weights=weights)),
            }
        )
    return pd.DataFrame(rows)


def _render_summary(*, dataset_dir: Path, shards: list[Path], summary_df: pd.DataFrame, target_min_factor: float, strategy_names: tuple[str, ...]) -> str:
    lines = [
        "# BWM behavior compression profile",
        "",
        f"- Dataset: `{dataset_dir}`",
        f"- Sampled shards: `{len(shards)}`",
        f"- Strategies: `{', '.join(strategy_names)}`",
        f"- Target minimum factor: `{target_min_factor:g}x` vs current compressed shards",
        "",
        "## Strategy summary",
        "",
        "| Strategy | Factor vs current | Candidate size | Max abs error | Mean abs error | Target met |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in summary_df.itertuples(index=False):
        factor = float(row.compression_factor_vs_current)
        lines.append(
            "| {strategy} | {factor:.2f}x | {size} | {max_err:.6g} | {mean_err:.6g} | {target} |".format(
                strategy=row.strategy,
                factor=factor,
                size=_format_bytes(int(row.candidate_compressed_bytes)),
                max_err=float(row.max_abs_error),
                mean_err=float(row.mean_abs_error),
                target="yes" if factor >= target_min_factor else "no",
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation notes",
            "",
            "- Factors are measured against the current compressed `.blosc` payload sizes inside `sessions/*.zip`, not against raw NumPy bytes.",
            "- Candidate bytes are estimated from in-memory encoded arrays compressed with the existing Blosc zstd/shuffle codec plus NaN masks when needed.",
            "- `conservative` keeps timestamps/wheel unchanged and quantizes pose coordinates, likelihoods, and non-coordinate features.",
            "- `balanced` and `aggressive` additionally quantize timestamps and wheel signals.",
            "- This profile is read-only: it does not rewrite dataset shards.",
            "",
            "## Selected shards",
            "",
        ]
    )
    lines.extend(f"- `{p.name}`" for p in shards)
    lines.append("")
    return "\n".join(lines)


def _render_validation_summary(*, dataset_dir: Path, shards: list[Path], strategy_name: str, validation_df: pd.DataFrame) -> str:
    summary_df = _summarize_validation_metrics(validation_df)
    lines = [
        "# BWM behavior compression validation",
        "",
        f"- Dataset: `{dataset_dir}`",
        f"- Strategy: `{strategy_name}`",
        f"- Sampled shards: `{len(shards)}`",
        "",
        "## Signal-class summary",
        "",
        "| Signal class | Arrays | Median retained rows | Max abs error | Mean abs error | P95 abs error |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary_df.itertuples(index=False):
        lines.append(
            "| {signal_class} | {arrays} | {ratio:.3f} | {max_err:.6g} | {mean_err:.6g} | {p95_err:.6g} |".format(
                signal_class=row.signal_class,
                arrays=int(row.arrays),
                ratio=float(row.median_retained_row_ratio),
                max_err=float(row.max_abs_error),
                mean_err=float(row.mean_abs_error),
                p95_err=float(row.p95_abs_error),
            )
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- For downsampled strategies, retained-row ratios compare candidate samples to source samples.",
            "- Reconstruction errors compare encoded/reconstructed values against retained samples, not against dropped samples.",
            "- This report validates numeric storage distortion only; downstream feature stability still requires separate scientific validation.",
            "",
            "## Selected shards",
            "",
        ]
    )
    lines.extend(f"- `{p.name}`" for p in shards)
    lines.append("")
    return "\n".join(lines)


def _format_bytes(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}TiB"


def _format_float(value: float) -> str:
    if pd.isna(value):
        return "nan"
    return f"{float(value):.6g}"
