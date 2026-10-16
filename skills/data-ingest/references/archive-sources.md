# Sources from a versioned data archive

Read this when the source is a large public archive that re-processes and
re-releases its data, such as IBL's ONE/Alyx database, DANDI, OpenNeuro or the
Allen Brain Observatory. General rules first, then one section per archive an
ingestion has used; add a section when a new archive needs one.

## Every archive
- **A release is not a full copy of the data.** One release can carry a modality
  for some sessions only, with the rest under another release. List the files of
  **every session**, not only the release listing, and record per modality the
  release, collection and revision taken (in provenance.yaml, and as
  `recordings` columns where it varies).
- **Count overlap with the archive's other releases** by session id, whether or
  not they are configured on this machine. Archive datasets often reuse sessions
  published before.
- **Pin every input to the archive's own id** (dataset UUID, asset id), so a
  rebuild reads the same bytes. The same keys form the `resolve:` block of
  anything referenced in place.
- **Re-processed versions of a derived field** (an alignment redone, a re-sort):
  ask the user which version defines the package, then ship a flag or difference
  column comparing it with the current one.
- **Groups that differ only in the version of one pipeline**: show the user the
  cross-tabulation and ask whether it is a confound; record the answer and its
  source in scientific-context.md.

## ONE/Alyx (IBL)
- **Tag, collection, revision.** A dataset is identified by session, collection
  (e.g. `alf/probe00/pykilosort`), revision (a date such as `2024-05-06`, or
  none) and tags. Choose these per modality and log the choice; the authors'
  code usually shows which revision they loaded.
- **Do not filter on `default_dataset`.** The flag moves when data is
  re-processed, so a build that filters on it fails or silently switches
  version. Select by collection, revision and tag, and fail on more than one
  match.
- **De-duplicate Alyx list results by dataset id**; paginated queries can
  repeat rows.
- **On an S3 mount, read files by each dataset's `data_url`** (it contains the
  dataset UUID); `one.load_dataset_from_id` can fail there with
  `ALFObjectNotFound`. Point writable caches (`iblatlas`, ONE tables) at your
  work directory.
- **Referenced in place**: `resolve: {one_eid, dataset, collection, revision}`.

## DANDI
Not yet used by an ingestion. Pin the published dandiset version (not `draft`)
and each asset id. Referenced in place: `resolve: {dandiset_id, version,
asset_path}`; no repo code resolves this form yet.
