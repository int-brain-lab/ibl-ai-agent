# Interfaces pending implementation

The skill is written against these interfaces, and none of them exists yet.
Check before calling one. **This list is also the stand-in allowlist**: a missing
interface named here may be stood in for inside `ingestion/convert.py` under
`SKILL.md` hard rule 3, with the stand-in marked and logged. A missing helper
that is not named here stops ingestion — tell the user instead of reimplementing
it. Keep the list current: add an interface here only when the team intends to
build it.

- `ibl_ai_agent/datasets/spike_store.py`: `load_spike_shard`, moved from
  `bwm_ephys.py` and re-exported from it. A move, not a behaviour change. This
  is the read side only; ingestion writes through `spikepack`.
- `ibl_ai_agent/datasets/one_trials.py`: loads `trials` per session through ONE
  and returns one concatenated DataFrame with an `eid` column, which is the
  extraction's input. It lives in the repo, with a test, because every package
  needs identical behaviour from it.
- `bwm_simple._build_trials`: will accept a path or a DataFrame, with `roster`
  optional. With `roster=None` the roster filter and merge are skipped, and
  `subject`, `date`, `session_number` and `lab` are not emitted.
- `bwm_ephys._build_events`: will gain a keyword-only `event_time_dtype`
  (default `np.float32`) and keyword-only control of the session key and carried
  columns. Ingested packages pass `np.float64`. The cast happens inside the
  function, so up-casting its output afterwards would not recover the precision.
- `pyproject.toml` `ingest` extra: see `spike-shards.md`.
- the dataset validator: checks a built package against `project-structure.md`.
  What a build's own checks have been seen to miss, so the validator must cover
  it: a column spec carrying keys other than `dtype`, `units` and `description`
  (unquoted prose in a YAML flow mapping splits into stray keys, and a check that
  compares only column names passes it); the same quantity named differently by
  schema and store metadata, such as quantization in microseconds; and rebuild
  comparison by decoded content rather than by file hash, since compressed stores
  are not byte-reproducible.
