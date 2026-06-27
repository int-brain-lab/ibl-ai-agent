---
name: ibl-anatomy
description: Use this skill to navigate the IBL anatomical atlas — region lookup by name/id/coordinate, cortical depth and layer assignment, hierarchy traversal, mapping between Allen/Beryl/Cosmos/Swanson, and brain-map visualisation. Required whenever a question involves anatomical location, cortical depth, superficial vs deep layers, or brain region identity of recorded units.
---

# IBL Anatomy

## Use this skill when
- Converting between region acronyms, IDs, and atlas mappings.
- Looking up which region contains a CCF coordinate.
- Traversing the region hierarchy (ancestors, descendants, leaves).
- Visualising per-region scalars or point clouds on slices, flatmaps, or Swanson projections.

## Do not use this skill when
- The task is spike-sorting QC or cluster-to-region assignment — use `ibl-load/references/brain_regions_qc.md`.
- The task is raw Neuropixels preprocessing — use `ibl-neuropixel`.

## References
- `references/atlas_navigation.md`: verified API patterns for `BrainRegions`, `AllenAtlas`, and `iblatlas.plots`.

## Workflow
1. Use `BrainRegions` alone when no coordinate lookup is needed — no file I/O.
2. Instantiate `AllenAtlas(res_um=25)` only when CCF volume, slicing, or `get_labels` is required.
3. Declare one mapping per metric before any groupby: `Allen`, `Beryl`, `Cosmos`, or `Swanson` (+ `-lr` variants for hemisphere-sensitive analyses).
4. For hemisphere-aware plots, use `prepare_lr_data` and a `-lr` mapping; note that `-lr` encodes left as negative ID.
5. Use `iblatlas.plots` helpers before writing custom matplotlib; prefer `plot_points_on_slice` for coordinate clouds and `plot_swanson_vector` for Swanson projections.

## Quality gates
- Never mix IDs or acronyms from different mappings in one aggregation.
- State the mapping used in every region-level output and figure caption.
- All coordinates passed to iblatlas must be in metres, bregma-relative, axis order (ML, AP, DV).
