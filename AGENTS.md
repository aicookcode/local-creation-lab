# Local Media Studio 7.1

This package is the English-layout, full-model-preload edition of the local offline audio and video workstation. Shipped package, program, model and configuration paths use ASCII filenames. GUI labels and names used by existing project documents may contain Chinese.

## Package layout

```text
PACKAGE/
  local_media_studio.exe
  start_workbench.bat
  start_workbench_diagnostic.bat
  start_workbench.ps1
  install-runtime.ps1
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

The source release omits runtime binaries, downloaded model weights and generated data. The launchers create required directories at first use.

## Startup contract

`local_media_studio.exe` and `start_workbench.bat` use the same `program/program_entry.py` entry. The launcher checks FFmpeg and fonts, and installs or repairs the runtime from the configured SHA256-pinned GitHub release when required. The entry then opens a small startup-preparation window and verifies or downloads every model group before showing the main workbench:

- `subtitle_base`
- `subtitle_small`
- `voice_qwen`
- `voice_zipvoice`
- `portrait`

The model manifest is `program/config/model_download_manifest.json`; sources are in `program/config/model_download_sources.json`. Every downloaded file is written to a temporary file, checked for the manifest size and SHA256, then atomically renamed. ZipVoice FP32 assets and its phoneme data come from one archive. The installer verifies individual files and the phoneme data directory. It generates the Wav2Lip and Whisper-small integrity records from pinned manifest metadata.

If startup preparation fails, the workbench stays closed and shows the reason, the next actions, and `logs/startup_preflight.log`. A Python exception is written to `logs/startup_error.log`. The diagnostic entry runs the same preparation and then keeps the Python diagnostic console visible.

## Subtitle and portrait behavior

- Quick creation and the two video tabs save the subtitle display choice. With subtitles disabled, automation can omit SRT and subtitle style from export and skips subtitle checks. Old projects with no choice still display subtitles. The recommended portrait engine still needs the transcript for lip timing.
- In `detail_v2`, an explicitly selected mouth-reference image takes precedence. In automatic mode, confirmed visible teeth enable animation within the source mouth opening. If the photo fails that check, use the model-generated reference path. Existing masters can still be exported using their matching caches.

## Development rules

- Keep machine-facing filenames and path components ASCII. Keep shipped paths and new program-owned storage paths ASCII. Preserve historical project document names and references at their compatibility boundary.
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
