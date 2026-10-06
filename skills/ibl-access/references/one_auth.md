## Purpose
Canonical ONE/Alyx authentication patterns for public and private IBL access.

## Verified
- Verified against official docs on 2026-03-03.
- Primary sources:
  - https://int-brain-lab.github.io/ONE/_autosummary/one.api.html
  - https://docs.internationalbrainlab.org/notebooks_external/one_quickstart.html
  - https://int-brain-lab.github.io/ONE/FAQ.html

## Endpoints
- Public Alyx: `https://openalyx.internationalbrainlab.org`
- Private Alyx: `https://alyx.internationalbrainlab.org`

## Core calls
```python
from one.api import ONE, One
```

```python
# Public online access
one = ONE(base_url="https://openalyx.internationalbrainlab.org", silent=True)

# Private online access (non-interactive)
one = ONE(
    base_url="https://alyx.internationalbrainlab.org",
    username="<ALYX_USER>",
    password="<ALYX_PASSWORD>",
    silent=True,
)
```

## Constructor parameters used by this project
- `base_url`: Alyx endpoint.
- `username` / `password`: for explicit private auth.
- `silent`: `True` for non-interactive calls, `False` when interactive auth is allowed.
- `cache_dir` (if supported by installed ONE): local data cache location.
- `cache_rest`: cache REST responses on disk (`None` lets ONE choose defaults).
- `mode`: typically `"remote"` for Alyx-backed queries.

## Online/offline mode
- `mode="remote"` for server-backed access.
- Cache-only access requires `One(cache_dir=...)`, not `ONE(mode="local")`, whenever operating from an existing local cache only.

## Recommended runtime profiles
```python
# Fast iterative analysis against already cached metadata + data
one_local = One(
    cache_dir="<ONE_CACHE_DIR>"
)

# Fresh server-backed discovery and metadata refresh
one_remote = ONE(
    base_url="https://openalyx.internationalbrainlab.org",
    mode="remote",
    cache_rest=None,
    silent=True,
)
```

```python
# Private server non-interactive auth
one_private = ONE(
    base_url="https://alyx.internationalbrainlab.org",
    username="<ALYX_USER>",
    password="<ALYX_PASSWORD>",
    mode="remote",
    silent=True,
)
```

## Brain Wide Map paper note
The upstream `paper-brain-wide-map` examples instantiate public ONE against:

```python
one = ONE(base_url="https://openalyx.internationalbrainlab.org")
```

Operational guidance for this repo:
- Prefer the normal public OpenAlyx configuration above for BWM paper questions.
- If a local cache is already populated, `One(cache_dir=...)` remains the preferred rerun mode.
- Some upstream example scripts include `password="international"` for public access examples; treat that as an example-specific detail, not a general requirement for all generated code.

## Cache behavior guidance
- Use `One(cache_dir=...)` for repeated notebook reruns when you do not need fresh Alyx state.
- Use `one.alyx.rest(..., no_cache=True)` for specific calls when you suspect stale cached REST responses.
- Prefer query-level freshness toggles (`no_cache=True`) over globally disabling caching.

## Failure handling
- Auth failure or unreachable server: raise access error with endpoint + reason.
- Missing ONE dependency: instruct install with `uv sync --extra ibl`.
