# Changelog

All notable changes to the IBL AI Agent are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
- `bwm_behavior` dataset version 2.0.0 with wheel recovery for all 459 sessions
  and Lightning Pose preferred over DeepLabCut per camera.
See `CHANGELOG_DATA.md` for further details.

### Changed
- `scripts/download_datasets.py`: `bwm_behavior` archive updated to version 2.0.0
  (new filename, SHA1, and URL).

---

## [0.2.0] - 2026-06-08

### Added
- `bwm_ephys` dataset version 1.2.0 with waveforms, additional waveform features for each cluster and autocorrelograms.
See `CHANGELOG_DATA.md` for further details.

### Changed
- `scripts/download_datasets.py`: `bwm_ephys` archive updated to version 1.2.0
  (new filename, SHA1, and URL).
- Skill references updated to document the new cell-level files and loading
  guidance for `bwm_ephys ≥ 1.2.0`.

---

## [0.1.0] - 2026-02 *(initial release)*

### Added
- Initial agent scaffold with `bwm_ephys 1.1.0` and `bwm_behavior 1.1.0` datasets.
- Skill system for IBL data loading, analysis, anatomy, and Neuropixels access.
- `scripts/download_datasets.py` for bootstrapping public BWM archives.

---

[Unreleased]: https://github.com/int-brain-lab/ibl-ai-agent/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/int-brain-lab/ibl-ai-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/int-brain-lab/ibl-ai-agent/releases/tag/v0.1.0
