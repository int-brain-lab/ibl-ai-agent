"""Download public BWM archives, extract them, and configure local paths.

Usage:
    UV_CACHE_DIR=.uv-cache uv run python scripts/download_datasets.py
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
import yaml
from boto3.s3.transfer import TransferConfig
from botocore import UNSIGNED
from botocore.config import Config
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = REPO_ROOT / "reports" / "datasets"
CONFIG_PATH = REPO_ROOT / "data_locations.local.yaml"

BWM_DATASET_ROOTS = {
    "bwm_ephys": DATASETS_DIR / "bwm_ephys",
    "bwm_behavior": DATASETS_DIR / "bwm_behavior",
}


@dataclass(frozen=True)
class ArchiveSpec:
    dataset: str
    version: str
    url: str
    sha1: str

    @property
    def target_dir(self) -> Path:
        """Local extraction directory for this archive (``<dataset_root>/<version>/``)."""
        return BWM_DATASET_ROOTS[self.dataset] / self.version


@dataclass(frozen=True)
class ArchiveDownload:
    archive: ArchiveSpec
    target_dir: Path


@dataclass(frozen=True)
class ExactVersionPin:
    dataset: str
    root: Path
    configured_version: str
    current_version: str


@dataclass(frozen=True)
class ArchivePlan:
    downloads: list[ArchiveDownload]
    present: list[ArchiveDownload]
    exact_version_pins: list[ExactVersionPin]
    invalid_manual_roots: list[tuple[str, Path]]


ARCHIVES: list[ArchiveSpec] = [
    ArchiveSpec(
        dataset="bwm_ephys",
        version="1.2.0",
        url=(
            "https://ibl-brain-wide-map-public.s3.amazonaws.com/resources/"
            "ibl-agent-data/bwm_ephys-1.2.0.tar"
        ),
        sha1="384e1eddc3cf9249e26ce11bbadc3f86f3ced46d",
    ),
    ArchiveSpec(
        dataset="bwm_behavior",
        version="1.1.0",
        url=(
            "https://ibl-brain-wide-map-public.s3.amazonaws.com/resources/"
            "ibl-agent-data/bwm_behavior-1.1.0.tar"
        ),
        sha1="aa19a2ab3159c54b3f6b5889adf7a703681b152a",
    ),
]


def _is_s3_url(url: str) -> bool:
    """Return True if ``url`` points to an AWS S3 endpoint."""
    host = urlparse(url).netloc
    return "s3.amazonaws.com" in host or "s3-" in host


def _progress_bar() -> Progress:
    return Progress(
        TextColumn("  [bold]{task.description}[/]"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    )


def _download_s3(url: str, destination: Path) -> None:
    """Download a public S3 object using parallel multipart transfer."""
    parsed = urlparse(url)
    # Virtual-hosted-style: <bucket>.s3[...].amazonaws.com/<key>
    # Path-style:           s3[...].amazonaws.com/<bucket>/<key>
    if parsed.netloc.split(".")[1] == "s3":
        bucket = parsed.netloc.split(".")[0]
        key = parsed.path.lstrip("/")
    else:
        parts = parsed.path.lstrip("/").split("/", 1)
        bucket, key = parts[0], parts[1]

    s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED))
    size = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
    progress = _progress_bar()
    with progress:
        task_id = progress.add_task(Path(key).name, total=size)
        transfer_cfg = TransferConfig(max_concurrency=10, multipart_threshold=8 * 1024 * 1024)
        s3.download_file(
            bucket, key, str(destination),
            Callback=lambda n: progress.update(task_id, advance=n),
            Config=transfer_cfg,
        )


def _download_http(url: str, destination: Path, chunk_size: int = 1024 * 1024) -> None:
    """Stream ``url`` to ``destination`` over HTTP/HTTPS."""
    with urllib.request.urlopen(url) as response:
        total_bytes = int(response.headers.get("Content-Length") or 0) or None
        progress = _progress_bar()
        with progress, destination.open("wb") as out_file:
            task_id = progress.add_task(Path(url).name, total=total_bytes)
            while chunk := response.read(chunk_size):
                out_file.write(chunk)
                progress.update(task_id, advance=len(chunk))


def download_file(url: str, destination: Path) -> None:
    """Download ``url`` to ``destination``, using S3 multipart transfer when possible."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _is_s3_url(url):
        _download_s3(url, destination)
    else:
        _download_http(url, destination)


def extract_archive(archive_path: Path, target_directory: Path) -> None:
    """Extract ``archive_path`` into ``target_directory``.

    Format is auto-detected from the archive filename extension (zip, tar,
    tar.gz, tar.bz2, ...) via :func:`shutil.unpack_archive`.
    """
    target_directory.mkdir(parents=True, exist_ok=True)
    print(f"  extracting into {target_directory}")
    shutil.unpack_archive(str(archive_path), str(target_directory))


def install_archive(archive_path: Path, archive: ArchiveSpec, target_directory: Path, scratch_directory: Path) -> None:
    """Extract ``archive_path`` and move the expected version directory into place."""
    extract_root = scratch_directory / f"extract-{archive.dataset}-{archive.version}"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True)
    print(f"  extracting into {extract_root}")
    shutil.unpack_archive(str(archive_path), str(extract_root))

    candidates = [
        extract_root / archive.dataset / archive.version,
        extract_root / archive.version,
    ]
    source = next((candidate for candidate in candidates if has_schema(candidate)), None)
    if source is None:
        raise RuntimeError(
            f"{archive.dataset} {archive.version} archive did not contain an expected schema under "
            f"{archive.dataset}/{archive.version}/ or {archive.version}/"
        )

    if target_directory.exists() and not has_schema(target_directory):
        raise RuntimeError(f"Refusing to replace non-dataset path: {target_directory}")
    if has_schema(target_directory):
        print(f"  already installed at {target_directory}")
        return

    target_directory.parent.mkdir(parents=True, exist_ok=True)
    print(f"  installing into {target_directory}")
    shutil.move(str(source), str(target_directory))


def compute_sha1(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA1 hex digest of ``path``, reading in chunks."""
    hasher = hashlib.sha1()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_archive(archive: ArchiveSpec, archive_path: Path) -> None:
    """Raise ``RuntimeError`` if ``archive_path``'s SHA1 does not match ``archive.sha1``."""
    actual_sha1 = compute_sha1(archive_path)
    if actual_sha1 != archive.sha1:
        raise RuntimeError(
            f"sha1 mismatch for {archive.dataset} {archive.version}: "
            f"expected {archive.sha1}, got {actual_sha1}"
        )
    print(f"  sha1 verified ({actual_sha1})")


def has_schema(path: Path) -> bool:
    """Return True if ``path`` contains a ``schema.yaml`` file (i.e. an extracted dataset)."""
    return (path / "schema.yaml").exists()


def read_config() -> dict[str, Any]:
    """Load ``data_locations.local.yaml`` as a dict, or return ``{}`` if it does not exist."""
    if not CONFIG_PATH.exists():
        return {}
    payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"{CONFIG_PATH} must contain a YAML mapping.")
    return payload


def configured_dataset_roots(config: dict[str, Any]) -> dict[str, Path | None]:
    """Resolve each BWM dataset's configured root path from ``config``.

    Relative paths are resolved against ``CONFIG_PATH``'s parent (the repo
    root). Entries missing or with an empty ``root`` map to ``None``.
    """
    datasets = config.get("datasets", {})
    if not isinstance(datasets, dict):
        return {}

    roots: dict[str, Path | None] = {}
    for name in BWM_DATASET_ROOTS:
        raw = datasets.get(name, {})
        if not isinstance(raw, dict):
            roots[name] = None
            continue
        root = raw.get("root")
        if root is None or str(root).strip() == "":
            roots[name] = None
            continue
        path = Path(str(root)).expanduser()
        if not path.is_absolute():
            path = CONFIG_PATH.parent / path
        roots[name] = path
    return roots


def root_has_dataset(root: Path | None) -> bool:
    """Return True if ``root`` is, or contains a versioned subdirectory that is, a dataset.

    A "dataset" is identified by the presence of ``schema.yaml``. ``root``
    itself can either be a versioned dataset directory (e.g. ``.../1.1.0/``)
    or a parent that contains one or more versioned subdirectories.
    """
    if root is None:
        return False
    if has_schema(root):
        return True
    if not root.exists():
        return False
    return any(path.is_dir() and has_schema(path) for path in root.iterdir())


def config_has_manual_bwm_roots(config: dict[str, Any]) -> bool:
    """Return True if ``config`` declares any non-empty BWM dataset root."""
    return any(root is not None for root in configured_dataset_roots(config).values())


def config_resolves_bwm_roots(config: dict[str, Any]) -> bool:
    """Return True if every BWM dataset root in ``config`` points at an existing dataset."""
    roots = configured_dataset_roots(config)
    return all(root_has_dataset(roots.get(name)) for name in BWM_DATASET_ROOTS)


def plan_current_archives(config: dict[str, Any]) -> ArchivePlan:
    """Plan downloads needed for the current public archive versions.

    A configured root that directly contains ``schema.yaml`` is treated as an
    exact-version pin and is never overwritten. A configured root without
    ``schema.yaml`` is treated as a version-parent directory.
    """
    roots = configured_dataset_roots(config)
    downloads: list[ArchiveDownload] = []
    present: list[ArchiveDownload] = []
    exact_version_pins: list[ExactVersionPin] = []
    invalid_manual_roots: list[tuple[str, Path]] = []

    for archive in ARCHIVES:
        configured_root = roots.get(archive.dataset)
        root = configured_root or BWM_DATASET_ROOTS[archive.dataset]

        if has_schema(root):
            if root.name == archive.version:
                present.append(ArchiveDownload(archive=archive, target_dir=root))
            else:
                exact_version_pins.append(
                    ExactVersionPin(
                        dataset=archive.dataset,
                        root=root,
                        configured_version=root.name,
                        current_version=archive.version,
                    )
                )
            continue

        if configured_root is not None and not root.exists():
            invalid_manual_roots.append((archive.dataset, root))
            continue

        target_dir = root / archive.version
        item = ArchiveDownload(archive=archive, target_dir=target_dir)
        if has_schema(target_dir):
            present.append(item)
        else:
            downloads.append(item)

    return ArchivePlan(
        downloads=downloads,
        present=present,
        exact_version_pins=exact_version_pins,
        invalid_manual_roots=invalid_manual_roots,
    )


def write_default_config(config: dict[str, Any]) -> None:
    """Write ``data_locations.local.yaml`` pointing at the freshly-extracted BWM roots.

    Preserves any unrelated keys already in ``config`` and fills in
    ``datasets.<name>.root`` (relative to the repo root) plus a
    ``preferred_version: latest`` default for each BWM dataset.
    """
    payload = dict(config)
    datasets = payload.get("datasets")
    if not isinstance(datasets, dict):
        datasets = {}
    else:
        datasets = dict(datasets)

    for name, root in BWM_DATASET_ROOTS.items():
        raw = datasets.get(name)
        if not isinstance(raw, dict):
            raw = {}
        else:
            raw = dict(raw)
        raw["root"] = root.relative_to(REPO_ROOT).as_posix()
        raw.setdefault("preferred_version", "latest")
        datasets[name] = raw

    payload["datasets"] = datasets
    payload.setdefault("one_cache", {"root": None})

    CONFIG_PATH.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    print(f"  wrote {CONFIG_PATH.relative_to(REPO_ROOT)}")


def main() -> int:
    """Download missing public BWM archives and configure local BWM roots.

    Exit codes:
        0 -- BWM datasets are already configured, or were just downloaded,
             extracted, verified, and a default ``data_locations.local.yaml``
             was written.
        1 -- one or more archives did not produce the expected ``schema.yaml``
             after extraction (treated as a build/layout error).
        2 -- ``data_locations.local.yaml`` already declares manual BWM roots
             but at least one of them does not resolve to a dataset; the
             config is left untouched so the user can fix or remove it.
    """
    config = read_config()
    plan = plan_current_archives(config)

    if plan.exact_version_pins:
        print(f"{CONFIG_PATH.relative_to(REPO_ROOT)} pins one or more exact dataset versions.")
        for pin in plan.exact_version_pins:
            print(
                f"  {pin.dataset}: {pin.root} is version {pin.configured_version}; "
                f"current archive is {pin.current_version}"
            )
        print("Leaving the manual config unchanged; point root at a version-parent directory to bootstrap current archives.")
        return 2

    if plan.invalid_manual_roots:
        print(f"{CONFIG_PATH.relative_to(REPO_ROOT)} contains BWM dataset roots that do not exist.")
        for dataset, root in plan.invalid_manual_roots:
            print(f"  {dataset}: {root}")
        print("Leaving the manual config unchanged; edit it or remove it before bootstrapping.")
        return 2

    if not plan.downloads:
        print("Current BWM dataset archives are already installed:")
        for item in plan.present:
            print(f"  {item.archive.dataset} {item.archive.version}: {item.target_dir}")
        if not CONFIG_PATH.exists() or not config_has_manual_bwm_roots(config):
            write_default_config(config)
        return 0

    with tempfile.TemporaryDirectory() as scratch_directory:
        scratch_path = Path(scratch_directory)
        for item in plan.downloads:
            archive = item.archive
            print(f"\n[{archive.url}]")
            archive_path = scratch_path / Path(archive.url).name
            download_file(archive.url, archive_path)
            verify_archive(archive, archive_path)
            install_archive(archive_path, archive, item.target_dir, scratch_path)

    missing = [item.target_dir for item in (*plan.present, *plan.downloads) if not has_schema(item.target_dir)]
    if missing:
        print("Downloaded archives did not produce the expected schemas:")
        for path in missing:
            print(f"  missing {path.relative_to(REPO_ROOT) / 'schema.yaml'}")
        return 1

    write_default_config(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
