# Local Media Studio 7.0

This package is the English-layout, full-model-preload edition of the local offline audio and video workstation. All machine-facing package, program, model, cache, project, log, user-data, configuration, license, script and executable names use ASCII/English naming. User-facing GUI text may remain Chinese.

## Package layout

```text
PACKAGE/
  local_media_studio.exe
  start_workbench.bat
  start_workbench_diagnostic.bat
  start_workbench.ps1
  USER_GUIDE.html
  program/
    program_entry.py
    core/
    config/
    runtime/
    resources/
    licenses/
  models/
    voice/
    subtitle/
    portrait/
  projects/
  user_data/
  cache/
  logs/
  maintenance/
```

`projects`, `cache`, `logs` and `maintenance` are new empty directories in this release. Do not restore files from the removed 6.x project tree into the new package unless they have been explicitly migrated and checked.

## Startup contract

`local_media_studio.exe` and `start_workbench.bat` use the same `program/program_entry.py` entry. The launcher first checks the bundled runtime, FFmpeg and fonts. The entry then opens a small startup-preparation window and verifies or downloads every model group before showing the main workbench:

- `subtitle_base`
- `subtitle_small`
- `voice_qwen`
- `voice_zipvoice`
- `portrait`

The model manifest is `program/config/model_download_manifest.json`; sources are in `program/config/model_download_sources.json`. Every downloaded file is written to a temporary file, checked for the manifest size and SHA256, then atomically renamed. ZipVoice official fp32 assets are published as one archive, so the installer downloads that archive once and extracts the requested member before applying the per-file checks. `portrait/integrity.json` is generated locally from the pinned Wav2Lip hash because it is package verification metadata, not a publisher model file.

If startup preparation fails, the workbench stays closed and shows the reason, the next actions, and `logs/startup_preflight.log`. A Python exception is written to `logs/startup_error.log`. The diagnostic entry runs the same preparation and then keeps the Python diagnostic console visible.

## Development rules

- Keep machine-facing filenames and path components ASCII. Do not add Chinese names to `program`, `models`, `projects`, `cache`, `logs`, `user_data` or `maintenance`.
- Keep model paths, sizes and SHA256 values synchronized between the manifest and source configuration. Never add an unverified model URL.
- Do not lower model precision, output quality, canvas, frame rate or audio quality to make startup or generation faster.
- Do not record microphones automatically. Do not send local media to a service.
- New production output must go into a new project folder and must not overwrite an existing result.
- Keep user-facing labels and explanatory text in Chinese when that helps the operator; those labels are not filesystem identifiers.
- Run `program/runtime/python/python.exe -X utf8 -m compileall -q program/core program/program_entry.py` after code changes.

## Verification commands

```powershell
$root = (Get-Location).Path
$python = Join-Path $root 'program\runtime\python\python.exe'
$api = Join-Path $root 'program\core\automation.py'
& $python -X utf8 -m compileall -q (Join-Path $root 'program\core') (Join-Path $root 'program\program_entry.py')
& $python -X utf8 $api --diagnose
& $python -X utf8 $api --list-voices
```

`--diagnose` reports missing components when the full model preload has not completed; this is an installation state, not a successful production check. The GUI startup path is the authoritative preflight because it installs and validates all five model groups before opening the workbench.
