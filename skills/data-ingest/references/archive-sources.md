# Sources from a versioned data archive

Read this when the source is a large public archive that keeps several versions
of the same data, such as IBL's ONE/Alyx database, DANDI, OpenNeuro or the Allen
Brain Observatory. A lab's own files rarely have this problem; an archive almost
always does, because it re-processes, re-releases and re-tags data after
publication. Everything here adds to `skills/data-ingest/SKILL.md`; it does not
replace any of it.

The general rules come first. Sections for individual archives follow, for the
archives an ingestion has used; add one when a new archive needs it.

## Every archive
- **A release is not a full copy of the data.** A release tag, dandiset version
  or snapshot can carry one modality for some sessions and another modality for
  others, with the rest under another release. List the files of **every
  session**, not only the release listing, and record, per modality, which
  release, collection and revision you took (in the package's provenance.yaml and,
  where it varies, as `recordings` columns).
- **Overlap with the rest of the archive.** The SKILL.md overlap check compares
  against configured packages only. An archive dataset often reuses sessions the
  archive published before (here, 459 of 497 sessions were Brain Wide Map
  sessions). Count the overlap with the archive's other releases by session id,
  whether or not those releases are configured on this machine.
- **Pin every input to an id.** Record the archive's own id for each file read
  (dataset UUID, asset id), so a rebuild reads the same bytes. The same keys
  form the `resolve:` block of anything referenced in place rather than
  converted.
- **Re-processed versions of a derived field.** When the archive holds more than
  one version of a field, for example an anatomy alignment redone after
  release or a re-run of spike sorting, which version defines the package is a
  question for the user. Ask it; then ship a flag or a difference column comparing
  the version you used with the current one, so analysts can see where they
  disagree.
- **A newer version of the same pipeline is not automatically a confound.** When
  groups differ only in the version of one pipeline (not a different pipeline),
  show the cross-tabulation to the user and ask whether to treat it as a
  confound. Record the answer and its source in the package's
  scientific-context.md either way.
- **Mounted buckets.** A read-only mount can fill local disk with its read cache
  (about 95 GB in one run where the package was under 10 GB). Include it in the
  disk estimate of hard rule 2.

## ONE/Alyx (IBL)
- **Tag, collection, revision.** A dataset is identified by session, collection
  (for example `alf/probe00/pykilosort`), revision (a date such as `2024-05-06`, or
  none) and the tags it carries. Choose these per modality and log the choice.
  The paper's code repository usually shows which revision the authors loaded.
- **Do not filter on `default_dataset`.** The flag moves when data is
  re-processed, for example after an alignment is resolved again, so a build that
  filters on it fails or silently switches version. Select by collection,
  revision and tag, and fail on more than one match.
- **De-duplicate Alyx list results by dataset id.** Paginated REST queries can
  return the same row twice.
- **On an S3 mount, read files by the path in each dataset's file record**
  (`data_url`, which contains the dataset UUID). `one.load_dataset_from_id` can
  fail there with `ALFObjectNotFound` because the ONE cache tables do not index
  the ids. Point writable caches (`iblatlas`, ONE tables) at your work directory,
  not at the mount.
- **Referenced in place**: `resolve: {one_eid, dataset, collection, revision}`.

## DANDI
No ingestion has used DANDI yet. Apply the "Every archive" rules: pin the
published dandiset version (not `draft`) and each asset id. Referenced in place:
`resolve: {dandiset_id, version, asset_path}`; no repo code resolves this form
yet. Add what an ingestion finds here, with evidence in
the repository's ingestion notes.
