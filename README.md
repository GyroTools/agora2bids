# agora2bids

Convert one Agora exam (study) into a BIDS dataset directory.

```bash
pip install -e .            # needs gtagora-connector, parrec2dcm >= 0.2.0 (Python >= 3.10), dcm2niix
export AGORA_URL="https://your-agora.example.com"
export AGORA_API_KEY="..."   # or a .env file in the working directory

agora2bids 12345 --output D:/data/my_bids        # convert exam 12345
agora2bids 12345 --output D:/data/my_bids --dry-run   # classify only, download nothing
```

Running another exam against the same `--output` adds another `sub-<id>/` folder; re-running the same exam replaces
its `ses-<exam>` folder. `dataset_description.json` is created if missing and `participants.tsv` gets one row per
subject.

## What it does

1. Resolves the exam's patient (`sub-<Patient.id>`, session `ses-<Exam.id>`).
2. Finds the imaging datasets of each series: DICOM, or PAR/REC when the series has no DICOM.
3. Downloads each dataset's Philips GOAL/DB parameters and decides whether it belongs in BIDS
   (and as which datatype) before downloading any image data.
4. Downloads included datasets to a temporary directory. PAR/REC is converted to DICOM with `parrec2dcm`
   (using the parameters), then everything goes through `dcm2niix -b y` (NIfTI + BIDS JSON sidecar).
5. Places the files into `sub-*/ses-*/{anat,func,dwi,fmap,perf}/` with BIDS names, adds `TaskName` / `IntendedFor`
   to the sidecars, and writes a report to `code/agora2bids/`.

## Classification (first match wins)

| Result | Rule |
|---|---|
| excluded | spectroscopy, SmartPlan / SENSE-reference (COCA) / coil-survey / survey-scout-localizer scans |
| `dwi` | `EX_DIFF_enable` DWI/DTI, or non-zero b-values / ImageType `DIFFUSION` |
| `perf` | `EX_FLL_mode` != NO, or ASL tags |
| `fmap` | `EX_ACQ_B0_map` = YES (B0), `EX_ACQ_B1_map` = YES (B1) |
| excluded | phase-contrast flow (no raw BIDS datatype) |
| `func` | EPI with a dynamic study of more than 5 dynamics (`task-unknown` unless `--task-label`) |
| `anat/angio` | inflow / contrast-enhanced angiography |
| `anat` | suffix from technique family and TR/TE/TI: IR -> `FLAIR` (TI >= 1500 ms) / STIR `T2w` (TI <= 250 ms) / `T1w`; SE -> `T2w` (TE >= 45), `PDw` (TR >= 1000), else `T1w`; GRE -> `T2starw` (TE >= 10, non-turbo), balanced / T2-prep `T2w`, else `T1w` |

GOAL parameters carry the operator's intent, DICOM what was measured. Intent flags prefer GOAL, measured values
prefer DICOM; when both exist and disagree the DICOM evidence wins and a warning goes into the report. Datasets
without GOAL/DB parameters are classified from DICOM tags (or the PAR/REC header) after download.

Derived images (ADC, FA, ...) and datasets that cannot be mapped are listed in the report, not written.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
