## Purpose
Verified API patterns for `BrainRegions`, `AllenAtlas`, and `iblatlas.plots`.
Verified against iblatlas examples on 2026-05-18.

---

## BrainRegions

No file I/O; instantiate once and reuse.

```python
from iblatlas.regions import BrainRegions
br = BrainRegions()
```

### Lookup and remapping

```python
ids   = br.acronym2id(['MOp', 'VISp'])          # → int array
acros = br.id2acronym(ids)                        # → str array
beryl = br.acronym2acronym(['MOp', 'VISp'], mapping='Beryl')
beryl_ids = br.id2id(ids, mapping='Beryl')
```

Available mappings: `Allen`, `Beryl`, `Cosmos`, `Swanson` and their `-lr` variants.
`-lr` mappings encode hemisphere: **negative id = left, positive id = right**.
Remapping from a non-`-lr` source to a `-lr` target is not supported.

### Hierarchy

```python
anc  = br.ancestors(int(br.acronym2id('VISp')[0]))   # Bunch with .id, .acronym, .level
desc = br.descendants(int(br.acronym2id('VISp')[0]))
leaf_ids = br.leaves().id
```

### Filtering units to a parent region (e.g. Isocortex)

Always filter by the **original Allen `atlas_id`**, not by a remapped ID (Beryl/Cosmos).
Remapped IDs are coarser and can include regions outside the intended parent.

```python
br.compute_hierarchy()   # populates br.hierarchy (n_levels × n_regions) and br.iparent
# br.hierarchy[:, i] = ancestor indices of region i, from root to leaf
# A region is under parent P iff P's index appears anywhere in its column.

parent_id  = int(br.acronym2id('Isocortex')[0])
parent_idx = int(np.where(br.id == parent_id)[0][0])
is_child   = np.any(br.hierarchy == parent_idx, axis=0)   # bool array len = n_regions
child_allen_ids = set(br.id[is_child].tolist())

# Filter a units dataframe that has an 'atlas_id' (Allen) column
units_in_region = units[units['atlas_id'].isin(child_allen_ids)]
```

Use `beryl_acronym` (or another mapping) only for **grouping** after the filter, not for the filter itself.

### Region metadata

```python
info = br.get(br.acronym2id(['MOp', 'VISp']))
# Bunch fields: id, acronym, name, level, parent, order, rgb, hexcolor
```

---

## AllenAtlas

Only needed for coordinate lookup, atlas slicing, or point-on-slice plots. This will download
the atlas data if not already present.

```python
from iblatlas.atlas import AllenAtlas
ba = AllenAtlas(res_um=25)   # also 10 or 50; 25 is standard
br = ba.regions              # same as BrainRegions()
```

### Coordinate convention

All API inputs in **metres**, **bregma-relative**, axis order **(ML, AP, DV)**.
Typical workflow: keep values in µm, convert with `/ 1e6` at the call site.

| axis | sign | example |
|------|------|---------|
| ML (x) | right = positive | `−0.001` = 1 mm left |
| AP (y) | anterior = positive | `−0.002` = 2 mm posterior |
| DV (z) | dorsal = positive | `−0.003` = 3 mm ventral |

Left hemisphere: `x < 0`. Volume shape is `(528, 456, 320)` at 25 µm.

### Coordinate-based region lookup

```python
import numpy as np
xyz = np.array([[0.0, -2000/1e6, -3000/1e6]])   # ML=0, AP=−2 mm, DV=−3 mm
region_ids = ba.get_labels(xyz, mapping='Beryl')  # int array of remapped IDs
acronyms   = br.id2acronym(region_ids)
```

Use `mode='clip'` to suppress out-of-volume errors; default `mode='raise'`.

### label volume — index vs Allen ID (critical distinction)

**`ba.label` stores region indices, not Allen IDs.**

```python
region_idx = ba.label[iAP, iML, iDV]     # int, 0 … n_regions-1
allen_id   = ba.regions.id[region_idx]    # e.g. 315 for Isocortex
acronym    = ba.regions.acronym[region_idx]
rgb        = ba.regions.rgb[region_idx]   # (R, G, B) uint8
```

`ba.regions.rgb` is indexed by region index — use `ba.regions.rgb[ba.label[...]]` directly.
**Never use `ba.regions.rgb[allen_id]`** — Allen IDs are arbitrary integers and are NOT
valid indices into the regions arrays.

To map a full label slice to Allen colours in one step:

```python
rgb_lut = np.array(ba.regions.rgb, dtype=np.uint8)   # (n_regions, 3)
lbl_slice = ba.label[iAP, :, :]                       # (ML, DV)
rgb_img   = rgb_lut[lbl_slice]                        # (ML, DV, 3)
```

To build a mask for a specific region by acronym:

```python
_, idx = ba.regions.acronym2index('Isocortex')        # idx is position in regions arrays
mask = ba.label == idx[0][0]                           # bool volume
```

### Atlas slices

```python
ba.plot_cslice(-2000/1e6, volume='image')          # coronal at AP=−2 mm
ba.plot_cslice(-2000/1e6, volume='annotation', mapping='Allen')
ba.plot_hslice(-3000/1e6)                          # horizontal at DV=−3 mm
ba.plot_sslice( 1000/1e6)                          # sagittal  at ML=+1 mm
```

---

## iblatlas.plots

### Scalar on slice  *(region acronyms → colour)*

```python
from iblatlas.plots import plot_scalar_on_slice, prepare_lr_data

# Single hemisphere
fig, ax = plot_scalar_on_slice(
    acronyms, values,
    coord=-2000/1e6, slice='coronal',   # 'coronal' | 'sagittal' | 'horizontal' | 'top'
    mapping='Beryl', hemisphere='left',
    background='image', cmap='Reds', brain_atlas=ba,
)

# Bilateral — prepare arrays first
ac_lr, val_lr = prepare_lr_data(ac_lh, val_lh, ac_rh, val_rh)
fig, ax = plot_scalar_on_slice(ac_lr, val_lr, coord=-2000/1e6,
                               slice='coronal', hemisphere='both', brain_atlas=ba)
```

### Points on slice  *(xyz coordinates → aggregated colour)*

```python
from iblatlas.plots import plot_points_on_slice

xyz = np.c_[clusters['x'], clusters['y'], clusters['z']]  # metres
fig, ax = plot_points_on_slice(
    xyz, values=clusters['firing_rate'],
    coord=-2000/1e6, slice='coronal',
    mapping='Beryl', background='boundary',
    aggr='mean', fwhm=100, brain_atlas=ba,
)
# aggr options: 'mean', 'count', 'sum'; fwhm is gaussian kernel in µm
```

### Swanson flatmap

```python
from iblatlas.plots import plot_swanson_vector

plot_swanson_vector(acronyms, values, annotate=True, empty_color='silver')

# Bilateral
regions_rl = np.r_[br.acronym2id(acronyms), -br.acronym2id(acronyms)]   # neg = left
values_rl  = np.r_[val_rh, val_lh]
plot_swanson_vector(regions_rl, values_rl, hemisphere='both', cmap='magma', br=br)
```

Parent region values paint all children. Only Swanson-level regions or their parents are valid inputs.

### Dorsal-cortex flatmap

```python
from iblatlas.flatmaps import FlatMap
from iblatlas.plots import plot_scalar_on_flatmap

flmap = FlatMap(flatmap='dorsal_cortex', res_um=25)
fig, ax = plot_scalar_on_flatmap(
    acronyms, values,
    depth=0,               # depth in µm below surface
    mapping='Beryl', hemisphere='left',
    background='boundary', cmap='viridis', flmap_atlas=flmap,
)
```

---

## iblatlas.streamlines

Cortical-depth and flatmap-projection utilities. Requires `AllenAtlas`.
Downloads lookup files from S3 on first call and caches locally.

### Cortical depth from xyz

Works only for Isocortex voxels; returns `nan` outside.

```python
from iblatlas.streamlines.utils import xyz_to_depth

depth_pct = xyz_to_depth(xyz)              # % from surface (0 = surface, 100 = white matter)
depth_um  = xyz_to_depth(xyz, per=False)   # µm from surface
# xyz: (N, 3) metres, bregma-relative, (ML, AP, DV)
```

Typical pattern — get all voxels of a region, compute depth, paint onto a volume for slicing:

```python
region_idx = ba.regions.acronym2index('MOp')[1][0][0]
ba.label = ba.regions.mappings['Beryl-lr'][ba.label]
ixyz = np.where(ba.label == region_idx)                       # (AP, ML, DV) index order
xyz  = ba.bc.i2xyz(np.c_[ixyz[1], ixyz[0], ixyz[2]])         # → metres (ML, AP, DV)
depth_vol = np.full(ba.image.shape, np.nan)
depth_vol[ixyz[0], ixyz[1], ixyz[2]] = xyz_to_depth(xyz) * 100
ba.plot_cslice(620/1e6, volume='volume', region_values=depth_vol, cmap='viridis')
```

### Flatmap projection

Projects a volume or xyz point cloud onto the dorsal-cortex flatmap by
aggregating along cortical streamlines. Restricted to Isocortex.

```python
from iblatlas.streamlines.utils import project_volume_onto_flatmap, project_points_onto_flatmap, get_mask

proj, fig, ax = project_volume_onto_flatmap(ba.image, res_um=25, aggr='max', cmap='bone')

mask = get_mask('boundary')   # background: 'image' | 'annotation' | 'boundary'
proj = project_points_onto_flatmap(xyz, values, aggr='mean', plot=False)
proj[proj == 0] = np.nan
fig, ax = plt.subplots()
ax.imshow(mask, cmap='Greys'); ax.imshow(proj); ax.set_axis_off()
```

`aggr` options: `'mean'`, `'sum'`, `'count'`, `'std'`, `'median'`, `'min'`, `'max'`.
