param([string]$Root)
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($Root)) { $Root = $PSScriptRoot }
$Root = [IO.Path]::GetFullPath($Root)
$configPath = Join-Path $Root 'program\config\runtime_download.json'
$config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
$url = [string]$config.download_url
$expected = ([string]$config.sha256).ToLowerInvariant()
$archiveName = [string]$config.archive_name
if ([string]::IsNullOrWhiteSpace($url)) {
  Write-Host '[ERROR] Runtime download URL is not configured.'
  Write-Host 'Edit PROGRAM\CONFIG\runtime-download.json and set download_url and sha256.'
  exit 2
}
if ([string]::IsNullOrWhiteSpace($archiveName)) { throw 'archive_name is empty.' }
$downloadDir = Join-Path $Root 'cache\runtime_install'
New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
$archive = Join-Path $downloadDir $archiveName
$partial = $archive + '.partial'
Write-Host '[INFO] Downloading the Windows runtime package...'
if (-not (Test-Path -LiteralPath $archive)) {
  if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
  Invoke-WebRequest -Uri $url -OutFile $partial -UseBasicParsing
  Move-Item -LiteralPath $partial -Destination $archive -Force
}
if (-not [string]::IsNullOrWhiteSpace($expected)) {
  $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $expected) {
    Remove-Item -LiteralPath $archive -Force
    throw "Runtime package SHA256 mismatch. Expected $expected, got $actual."
  }
}
$probe = Join-Path $Root 'program\runtime\python\pythonw.exe'
if (-not (Test-Path -LiteralPath $probe)) {
  Write-Host '[INFO] Extracting the Windows runtime package...'
  $temp = Join-Path $downloadDir ('extract-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Force -Path $temp | Out-Null
  try {
    Expand-Archive -LiteralPath $archive -DestinationPath $temp -Force
    $runtime = Join-Path $temp 'program\runtime'
    if (-not (Test-Path -LiteralPath $runtime)) { $runtime = Join-Path $temp 'runtime' }
    if (-not (Test-Path -LiteralPath $runtime) -and (Test-Path -LiteralPath (Join-Path $temp 'python\pythonw.exe'))) { $runtime = $temp }
    if (-not (Test-Path -LiteralPath $runtime)) { throw 'The archive does not contain PROGRAM\RUNTIME.' }
    New-Item -ItemType Directory -Force -Path (Join-Path $Root 'program') | Out-Null
    Copy-Item -LiteralPath $runtime -Destination (Join-Path $Root 'program') -Recurse -Force
  } finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
  }
}
if (-not (Test-Path -LiteralPath $probe)) { throw 'Runtime extraction completed but pythonw.exe is still missing.' }
Write-Host '[OK] Runtime is ready.'
exit 0

