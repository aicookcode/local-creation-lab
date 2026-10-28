# Models

Model weights are intentionally excluded from the GitHub source package.

On the first launch, the startup-preparation window downloads and verifies all five model groups before opening the workbench:

- `voice_qwen`
- `voice_zipvoice`
- `subtitle_base`
- `subtitle_small`
- `portrait`

Sources are configured in `program/config/model_download_sources.json`; the manifest and expected sizes/SHA256 values are in `program/config/model_download_manifest.json`. Do not commit downloaded weights, cache files, projects, logs or user data to the source repository.
