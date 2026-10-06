---
name: ibl-access
description: Use this skill when a task needs IBL data access setup, ONE/Alyx authentication, or selecting between public and private data modes.
---

# IBL Access

## Use this skill when
- You need to connect to IBL data via ONE.
- You need to switch between `public` and `private` access modes.
- You need session/insertion queries constrained by releases/tags.

## Local references (read before browsing)
- `../SOURCES.md`: provenance index for this skill and its references.
- `references/one_auth.md`: ONE instantiation, online/offline modes, auth patterns.
- `references/session_search.md`: `search`, `search_terms`, `search_insertions`, and Alyx REST patterns.

Default policy: use these local references first. Browse official docs only when behavior differs from references or an API call fails unexpectedly.

## Workflow
1. Check `IBL_AGENT_DATA_OFFLINE`; if it is `1`, follow Offline mode below. Otherwise, determine mode (`public` or `private`) and record it in run metadata.
2. Configure ONE base URL:
- Public: `https://openalyx.internationalbrainlab.org`
- Private: `https://alyx.internationalbrainlab.org`
3. Validate auth state:
- interactive login path,
- non-interactive env path.
4. Build query strategy with performance defaults:
- narrow filters first,
- `limit`/`offset` paging for large pulls,
- `details=True` only when needed,
- `query_type="local"` vs `"remote"` selected explicitly.
5. Return connection status and provenance fields required by profile reports.

## Data-offline mode (`IBL_AGENT_DATA_OFFLINE=1`)
- Perform cache-only loading with `one.api.One(cache_dir=...)`. Do not use `one.api.ONE(...)`.
- Do not use `mode="remote"`, `cache_rest`, or `no_cache=True` as these all reach Alyx.
- `connect_one()` raises `OfflineModeError` under this flag; construct `one.api.One(cache_dir=...)` directly.
- Searches and loads only see what is already in the cache. If a dataset is absent, report it as missing and ask the user to provide it - do not trigger a download.
- Record offline mode and the cache root in the provenance block.

## Outputs
- Resolved endpoint.
- Auth mode used.
- Query constraints (release/tag/session filters).
- Provenance block for reports.
