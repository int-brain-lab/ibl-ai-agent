# Contributing

## Setup

```bash
UV_CACHE_DIR=.uv-cache uv sync --extra ibl --extra notebook
```

## Quality gates

```bash
UV_CACHE_DIR=.uv-cache uv run ruff check .
UV_CACHE_DIR=.uv-cache uv run pytest -q
```

## Refactor rules

- Keep `ibl_ai_agent/ask` layered (`domain`, `app`, `infra`).
- Add/adjust architecture guard tests when moving responsibilities.
- Prefer typed contracts over untyped dict plumbing in runtime internals.
- Keep CLI commands thin; shared behavior should live in `commands/common.py` or `commands/kernel.py`.

## Changelog and versioning

This project follows documents evolutions in two changelogs.

- **`CHANGELOG.md`** — agent/code changes (version bumps, new features, skill updates).
- **`CHANGELOG_DATA.md`** — dataset changes (new archive versions, schema additions, column changes).

Dataset versions are independent of the agent version; both use semver. A dataset minor bump (e.g. 1.1.0 → 1.2.0) adds columns or new files without breaking existing queries.

## Releasing a new dataset archive

When bumping a dataset version (e.g. `bwm_ephys`), the archive's own metadata files must match the new version **before** the tar is uploaded to S3. Forgetting this is the most common release mistake.

Checklist before uploading a new `<dataset>-<version>.tar`:

1. **`provenance.yaml`** inside the archive — set `dataset_version: <new_version>`.
2. **`manifest.json`** inside the archive — set `dataset_version: <new_version>` and ensure the `files` list includes every new file added in this release.
3. **`schema.yaml`** — set `dataset_version: <new_version>` and add entries for any new tables or stores.
4. Repack the tar, compute its SHA1 (`shasum -a 1 <archive>.tar`), and update the `sha1` field in `scripts/download_datasets.py`.
5. Run the release validator against the extracted archive before pushing:
   ```bash
   uv run python scripts/validate_bwm_ephys_release.py <path/to/extracted/version>
   ```
   The script checks version strings, manifest completeness, row/column counts, and array shapes.

## Docs policy

Authoritative docs are:

- `docs/ARCHITECTURE.md`
- `docs/ask/ASK_RUNTIME.md`
- `docs/CONTRIBUTING.md`

If behavior changes, update docs in the same PR.
