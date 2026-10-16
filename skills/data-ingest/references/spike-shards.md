# Spike shards

Read this before anything else if the dataset has spikes to convert. If it has
none, or its spikes are referenced in place, skip the preflight and log why.

## What to establish
- Sorter and version, per recording; which units were kept and why (the
  source-selection rule); what QC exists.
- Whether recordings are assigned to anatomy, and whether alignment or histology
  QC is carried.
- For every stored per-unit summary metric, the window it covers. Whole-recording
  metrics describe the whole recording; where there is a task period, also ship
  the task-window equivalents.

## Preflight
Run this first, so a missing environment fails before a long read of the source:

```bash
.venv/bin/python -c "import sys, spikepack; print(sys.version.split()[0], spikepack.__version__)"
```

Always use `.venv/bin/python`, never `uv sync` or `uv run` (see `AGENTS.md`).

- **Python < 3.11: stop** and explain; `spikepack` needs 3.11+. Do not install
  anything or substitute another shard writer.
- **`spikepack` missing: ask before installing.** Offer `uv sync --extra ingest`.
  While that extra is still pending in `pyproject.toml`, offer instead:
  `uv pip install --python .venv/bin/python "spikepack @ git+https://github.com/int-brain-lab/spikepack@30ab06ff7794d89cab17cb54338db869ad90d036"`.
  If the user declines, stop.
- Log the interpreter version, the `spikepack` version and any install, noting
  that an install outside the lockfile is removed by any later sync.

## Writing shards
**Snap every spike time to the quantization grid before calling
`spikepack.write_blosc`.** Only tick-aligned origins make the
`time_origin_ticks` and `time_origin_seconds` decoders agree exactly:

```python
q = quantization_us
times_seconds = np.rint(times_seconds * 1e6 / q) * q / 1e6
```

Snapping moves each spike by at most half a tick, the bound the encoder already
applies. Record it under `provenance.yaml` `conversion.lossy`.

`extra_meta` carries at least: `dataset_name`, `dataset_version`, `shard_key`,
`shard_id`, `session_id`, `subject_id`, `source_selection_rule`, `time_base`,
`n_spikes`, `n_units`, `cluster_encoding`, `time_encoding`, `compression`.

## Verifying and rerunning
Shard bytes differ between builds of the same arrays (the compressor's thread
order varies). Verify a rebuild by decoding and comparing arrays, never by file
hashes; hashes are right only for checking a copy or upload.

## Memory and workers
All of a recording's spikes are read before units are selected, so peak memory
per worker scales with the largest recording. Choose the worker count from the
sample build (hard rule 2).
