---
name: neuro-data-discovery
description: Find and safely reuse public neuroscience data — DANDI (NWB), OpenNeuro (BIDS), Allen Brain Atlas, NeuroMorpho, NeuroStore/Neurosynth — and sanity-check it after loading (shape, sampling rate, units, events). Use for neuroscience/brain-science data search or reuse (神经科学、脑科学、公开数据集、EEG、fMRI、电生理、钙成像).
license: MIT
metadata:
  origin: builtin
---

# Neuro data discovery and reuse

## Search
Use `search_neuro_datasets(query, sources, modality)` first; then details:
- **DANDI** (NWB files, electrophysiology/imaging): `get_dandiset(id)`. Stream files without full download:
  `dandi` CLI (`dandi download DANDI:000123/draft --download dandiset.yaml`) or `remfile` + `pynwb` / `lindi`.
- **OpenNeuro** (BIDS; MRI/EEG/MEG/iEEG): `get_openneuro_dataset(id)`; download with `openneuro-py`
  (`openneuro download --dataset ds000001 --include sub-01`) or DataLad.
- **Allen Brain Atlas**: `allen_query(criteria)` wraps the RMA API, e.g.
  `model::Structure,rma::criteria,[acronym$eq'VISp']`. For cell types / connectivity prefer `allensdk`.
- **NeuroMorpho** (neuron reconstructions): `https://neuromorpho.org/api/neuron/select?q=species:rat`.
- **NeuroStore / Neurosynth** (coordinate-based meta-analysis): `https://neurostore.org/api/studies/?search=…`;
  use NiMARE for analysis.
- NeuroVault's API ignores most filters — page through and filter locally.

## After loading: integrity checks (do all of them, write results to notes/data-checks.md)
- Shapes and counts: subjects, sessions, channels/voxels, trials — match the dataset description?
- Sampling rate and units (V vs µV, s vs ms); resample explicitly if combining sources.
- Events/triggers align with the signal (plot a few epochs around events).
- Missing data, bad channels, NaNs, saturation, line noise (50/60 Hz).
- BIDS datasets: run `bids-validator` (or `bids_validator` Python package) if available.
- Record dataset IDs, versions/snapshot tags and licenses in `data/SOURCES.md`; cite the dataset DOI.

## Analysis libraries (install into the workspace venv with `uv pip install …`)
MNE-Python (EEG/MEG), Nilearn/nibabel (fMRI), SpikeInterface (spike sorting), pynwb (NWB), NeuroKit2 (physiology),
suite2p/CaImAn (calcium imaging), Brian2 (spiking network simulation). Keep analyses laptop-sized unless told otherwise.
