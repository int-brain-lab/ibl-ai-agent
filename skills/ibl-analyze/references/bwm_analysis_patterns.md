## Purpose
Compact routing guide for Brain Wide Map analysis patterns.

## Source Basis
- Upstream `paper-brain-wide-map` repo inspected on 2026-03-06.
- Key sources: `brainwidemap/bwm_loading.py`, decoding examples, and meta oscillation code.

## First Decision
Use `../../ibl-load/references/bwm_runtime_policy.md` for global loading order. This file only chooses the analysis pattern after local BWM schemas have been inspected.

Default route:
1. Use newest sufficient local `bwm_ephys` / `bwm_behavior` tables or spike shards.
2. Use `brainwidemap` helpers only when local derived fields are absent or the user asks for upstream paper behavior.
3. Escalate to `SessionLoader` / `SpikeSortingLoader` only for narrow missing-field fallbacks.

## Pattern Map
| Question shape | Preferred surface | Key checks |
| --- | --- | --- |
| counts, yield, labs, sessions, insertions, regions | `bwm_ephys/metadata/*.parquet`, then `bwm_query` / `bwm_units` | match row grain; report session/subject/lab denominators |
| unit features by region | local units plus `features/unit_features.parquet`, then `bwm_units` | report feature availability and tested-region thresholds |
| event responses, PSTHs, timing | local units/trials/events, event-response features, spike shards | state event, window, binning, trial mask, response criterion |
| spike timing, ACG, burstiness, depth/layer | local spike shards plus unit/channel metadata | define bin/window/zero-lag handling and anatomical split |
| movement, quiescence, wheel, pose, reaction/movement time | `bwm_behavior` features and state epochs plus `bwm_ephys` spikes if neural | do not use movement-onset features as literal movement/quiescence unless labeled proxy; pose tables use tracker-agnostic `pose_*` names with a `tracker` column (`lightningPose` or `dlc`) |
| passive responsiveness | local passive-response features or passive assets plus spikes | use two-sided semantics unless excitation-only is requested; report passive coverage |
| population correlation/covariance | local spike counts by declared state/epoch | state bin width, population definition, state segmentation |
| decoding | BWM decoding examples plus local spike/trial tables | log target, alignment, bins, cross-validation, null baseline |
| oscillations / long-timescale state | session temporal data plus spikes/events/trials | define behavioral state and oscillation metric; avoid aggregate-only shortcuts |

## Pattern Rules
### Local Derived Datasets
- Prefer local `bwm_ephys` when schema covers units, insertions, sessions, regions, task events, passive features, or good-unit spike shards.
- Prefer local `bwm_behavior` for movement state, wheel, pose, trial behavior, reaction time, movement time, and behavior event features.
- Record dataset path/version and join grain.

### Event-Aligned Analyses
- Apply the BWM trial mask before event-aligned statistics.
- For stored event-response features, report `event_name`, `window_spec`, baseline, response statistic, sign convention, and denominators.
- For latency, state peak versus onset versus population-trajectory latency and flag right-edge/censored values.

### Movement and Quiescence
- Literal movement-versus-quiescence needs behavior-derived state labels or wheel thresholds.
- Use `firstMovement` event-response metrics only for movement-onset questions or explicitly labeled proxies.
- Validate movement/quiescence intervals before spike counting.

### Region and Unit-Feature Summaries
- Do not present single-insertion or single-session regional effects as stable region-wide claims.
- For correlations, report unit, insertion, session, subject, and lab coverage where feasible.
- For skewed features such as firing rate, include a robust or transformed sensitivity when cheap.

### Passive Analyses
- Keep active-task and passive-response criteria separate.
- Report passive-eligible denominators and all-active-task denominators when overlap is analyzed.
- Break down passive rows by event family if one table mixes stimulus types.

### Visualising per-region scalars on the brain

For cortical analyses, use `plot_scalar_on_slice` with `slice='top'` to show values on the
dorsal brain surface — this is preferred over the Swanson flatmap for isocortex data because
it preserves spatial anatomy and is immediately readable.

```python
from iblatlas.plots import plot_scalar_on_slice
from iblatlas.atlas import AllenAtlas

ba = AllenAtlas(res_um=25)
fig, ax = plt.subplots(figsize=(8, 6))
fig, ax, cbar = plot_scalar_on_slice(
    acronyms_beryl, values_beryl,
    slice='top', mapping='Beryl', hemisphere='left',
    background='boundary', cmap='RdBu_r',
    clevels=[-0.6, 0.6], show_cbar=True,
    brain_atlas=ba, ax=ax,
)
cbar.set_label("your label here")
```

- `acronyms_beryl`: array of Beryl acronym strings matching the values.
- `clevels`: `[vmin, vmax]` — set symmetrically for diverging colormaps.
- `hemisphere='left'` shows left cortex; use `'both'` for bilateral views.
- For subcortical structures, prefer a coronal or horizontal slice instead.

## Missing-Field Rule
If a needed field is absent from local BWM and aggregate/unit tables:
1. inspect upstream helper behavior and schemas;
2. preserve the aggregate/release filter path;
3. use the narrowest local-cache or session fallback that supplies the missing field;
4. state exactly why the aggregate path was insufficient.

Do not start broad BWM summaries with probe-by-probe remote loading.
