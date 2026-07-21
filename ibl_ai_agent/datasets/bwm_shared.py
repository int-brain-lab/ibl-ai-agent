from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
import tarfile
import zipfile

import numpy as np

from ibl_ai_agent.datasets import bwm_simple
from ibl_ai_agent.datasets import bwm_session_assets as session_assets


ALYX_BASE_URL = "https://alyx.internationalbrainlab.org"
SIGNAL_COMPRESSION_VARIANT = "blosc_zstd_shuffle"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(*, dataset_name: str, dataset_version: str, dataset_dir: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(dataset_dir.rglob("*")):
        if not path.is_file():
            continue
        files.append({"path": str(path.relative_to(dataset_dir)), "size_bytes": path.stat().st_size, "sha256": sha256(path)})
    return {"dataset_name": dataset_name, "dataset_version": dataset_version, "created_at": now_iso(), "files": files}


def import_zarr() -> Any:
    try:
        import zarr
    except ImportError as exc:
        raise RuntimeError("zarr is required; install project dependencies first.") from exc
    return zarr


def zstd_compressor() -> Any:
    from numcodecs import Blosc

    return Blosc(cname="zstd", clevel=7, shuffle=Blosc.SHUFFLE)


def compress_array(arr: np.ndarray) -> tuple[bytes, dict[str, Any]]:
    from numcodecs import Blosc

    array = np.ascontiguousarray(arr)
    codec = Blosc(cname="zstd", clevel=7, shuffle=Blosc.SHUFFLE)
    payload = codec.encode(array)
    return payload, {
        "dtype": array.dtype.str,
        "shape": list(array.shape),
        "order": "C",
        "nbytes": int(array.nbytes),
        "compressed_nbytes": int(len(payload)),
        "typesize": int(array.dtype.itemsize),
        "codec": {"name": "blosc", "cname": "zstd", "clevel": 7, "shuffle": "shuffle"},
    }


def decompress_array(payload: bytes, spec: dict[str, Any]) -> np.ndarray:
    if int(spec.get('nbytes', 0)) == 0:
        return np.asarray([], dtype=np.dtype(spec['dtype'])).reshape(tuple(spec['shape']))
    from numcodecs import Blosc

    raw = Blosc().decode(payload)
    arr = np.frombuffer(raw, dtype=np.dtype(spec["dtype"]))
    return arr.reshape(tuple(spec["shape"]))


def write_array_shard(path: Path, *, metadata: dict[str, Any], arrays: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = dict(metadata)
    manifest["arrays"] = {}
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for name, arr in arrays.items():
            entry = f"arrays/{name}.blosc"
            payload, spec = compress_array(arr)
            spec["entry"] = entry
            manifest["arrays"][name] = spec
            zf.writestr(entry, payload)
        zf.writestr("meta.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))


def read_array_shard(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path, mode="r") as zf:
        meta = json.loads(zf.read("meta.json").decode("utf-8"))
        arrays = {name: decompress_array(zf.read(spec["entry"]), spec) for name, spec in meta["arrays"].items()}
    return {"meta": meta, "arrays": arrays}


def write_array_directory(path: Path, *, metadata: dict[str, Any], arrays: dict[str, np.ndarray], progress: callable | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    manifest = dict(metadata)
    manifest["arrays"] = {}
    for name, arr in arrays.items():
        entry = f"{name}.blosc"
        if progress is not None:
            progress(name, "compress_start", nbytes=int(np.ascontiguousarray(arr).nbytes))
        payload, spec = compress_array(arr)
        if progress is not None:
            progress(name, "compress_done", compressed_nbytes=int(len(payload)))
        spec["entry"] = entry
        manifest["arrays"][name] = spec
        if progress is not None:
            progress(name, "write_start", compressed_nbytes=int(len(payload)))
        (path / entry).write_bytes(payload)
        if progress is not None:
            progress(name, "write_done", compressed_nbytes=int(len(payload)))
    (path / "meta.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def read_array_directory(path: Path) -> dict[str, Any]:
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    arrays = {name: decompress_array((path / spec["entry"]).read_bytes(), spec) for name, spec in meta["arrays"].items()}
    return {"meta": meta, "arrays": arrays}


def write_release_archive(
    source_dir: Path,
    *,
    release_root: Path,
    dataset_name: str,
    dataset_version: str,
    exclude_names: set[str] | frozenset[str] | None = None,
) -> dict[str, Path]:
    """Write a deterministic plain tar release archive and checksum file."""
    if not source_dir.exists():
        raise FileNotFoundError(f"Missing source directory: {source_dir}")
    release_dir = release_root / dataset_name / dataset_version
    release_dir.mkdir(parents=True, exist_ok=True)
    archive_path = release_dir / f"{dataset_name}-{dataset_version}.tar"
    checksum_path = archive_path.with_name(f"{archive_path.name}.sha256")
    temp_path = archive_path.with_name(f".{archive_path.name}.tmp")
    digest = hashlib.sha256()
    ignore = frozenset(exclude_names or ())
    arc_root = Path(dataset_name) / dataset_version

    with temp_path.open("wb") as raw:
        with _HashingWriter(raw, digest) as hashing_writer:
            with tarfile.open(fileobj=hashing_writer, mode="w", format=tarfile.PAX_FORMAT) as tar:
                _add_path_to_tar(
                    tar,
                    source_dir,
                    arcname=arc_root,
                    exclude_names=ignore,
                )
    temp_path.replace(archive_path)
    checksum_path.write_text(f"{digest.hexdigest()}  {archive_path.name}\n", encoding="utf-8")
    return {"archive_path": archive_path, "checksum_path": checksum_path}


def make_remote_one(cache_root: Path) -> Any:
    from one.api import ONE

    remote_cache_dir = cache_root / "alyx.internationalbrainlab.org"
    remote_cache_dir.mkdir(parents=True, exist_ok=True)
    return ONE(base_url=ALYX_BASE_URL, mode="remote", silent=True, cache_dir=str(remote_cache_dir))


def scan_aggregate_table(cache_root: Path, table_type: str) -> dict[str, Any]:
    for domain in bwm_simple.DOMAIN_DIRS:
        path = cache_root / domain / "bwm_tables" / f"{table_type}.pqt"
        if path.exists():
            return {"present": True, "path": str(path)}
    return {"present": False, "path": None}


def prefetch_wheel(one_remote: Any, *, eid: str) -> None:
    one_remote.load_object(eid, "wheel", download_only=True)


def prefetch_pose(one_remote: Any, *, eid: str) -> None:
    failures: list[str] = []
    for camera_name in session_assets.CAMERA_NAMES:
        try:
            one_remote.load_object(eid, camera_name, attribute=["times", "dlc", "lightningPose", "features", "ROIMotionEnergy"], download_only=True)
        except Exception as exc:
            failures.append(f"{camera_name}: {exc}")
    if len(failures) == len(session_assets.CAMERA_NAMES):
        raise RuntimeError("; ".join(failures))


def prefetch_passive(one_remote: Any, *, eid: str, dataset_names: list[str] | tuple[str, ...] | None = None) -> dict[str, str]:
    wanted = list(dataset_names or session_assets.PASSIVE_DATASET_FILENAMES)
    statuses: dict[str, str] = {}
    failures: list[str] = []
    for dataset_name in wanted:
        try:
            collection, revision = _resolve_remote_dataset_location(one_remote, eid=eid, dataset_name=dataset_name)
            one_remote.load_dataset(
                eid,
                dataset_name,
                collection=collection,
                revision=revision,
                query_type="remote",
                download_only=True,
            )
            statuses[dataset_name] = "fetched"
        except Exception as exc:
            statuses[dataset_name] = f"failed: {exc}"
            failures.append(f"{dataset_name}: {exc}")
    if failures and len(failures) == len(wanted):
        raise RuntimeError("; ".join(failures))
    return statuses


def _resolve_remote_dataset_location(one_remote: Any, *, eid: str, dataset_name: str) -> tuple[str | None, str | None]:
    try:
        details = one_remote.list_datasets(
            eid,
            filename=dataset_name,
            details=True,
            query_type="remote",
        )
    except Exception:
        details = None
    if details is None:
        return "alf", None
    if hasattr(details, "empty"):
        if details.empty:
            return "alf", None
        row = details.iloc[0]
        collection = row["collection"] if "collection" in details.columns and row.get("collection") else "alf"
        revision = row["revision"] if "revision" in details.columns and row.get("revision") else None
        return collection, revision
    return "alf", None


class _HashingWriter:
    def __init__(self, raw, digest: Any) -> None:
        self._raw = raw
        self._digest = digest

    def write(self, data: bytes) -> int:
        self._digest.update(data)
        return self._raw.write(data)

    def tell(self) -> int:
        return self._raw.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._raw.seek(offset, whence)

    def seekable(self) -> bool:
        return self._raw.seekable()

    def flush(self) -> None:
        self._raw.flush()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> "_HashingWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._raw.close()


def _normalize_tarinfo(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
    tarinfo.uid = 0
    tarinfo.gid = 0
    tarinfo.uname = ""
    tarinfo.gname = ""
    tarinfo.mtime = 0
    if tarinfo.isdir():
        tarinfo.mode = 0o755
    elif tarinfo.isreg():
        tarinfo.mode = 0o644
    return tarinfo


def _add_path_to_tar(
    tar: tarfile.TarFile,
    path: Path,
    *,
    arcname: Path,
    exclude_names: frozenset[str],
) -> None:
    if path.name in exclude_names:
        return
    if path.is_dir():
        tar.add(path, arcname=str(arcname), recursive=False, filter=_normalize_tarinfo)
        children = sorted((child for child in path.iterdir() if child.name not in exclude_names), key=lambda child: child.name)
        for child in children:
            _add_path_to_tar(tar, child, arcname=arcname / child.name, exclude_names=exclude_names)
        return
    tar.add(path, arcname=str(arcname), recursive=False, filter=_normalize_tarinfo)
