param(
  [ValidateSet('gui', 'diagnostic')]
  [string]$Mode = 'gui'
)

$ErrorActionPreference = 'Stop'
$root = [IO.DirectoryInfo]::new($PSScriptRoot)

function Find-ProgramDirectory {
  Get-ChildItem -LiteralPath $root.FullName -Directory -Force |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'program_entry.py') } |
    Select-Object -First 1
}

function Find-PythonRuntime([IO.DirectoryInfo]$program) {
  Get-ChildItem -LiteralPath $program.FullName -Directory -Force |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'python\python313._pth') } |
    Select-Object -First 1
}

function Find-CoreDirectory([IO.DirectoryInfo]$program) {
  Get-ChildItem -LiteralPath $program.FullName -Directory -Force |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'video\ffmpeg.exe') } |
    Select-Object -First 1
}

function Find-FontDirectory([IO.DirectoryInfo]$program) {
  foreach ($top in (Get-ChildItem -LiteralPath $program.FullName -Directory -Force)) {
    $direct = @(Get-ChildItem -LiteralPath $top.FullName -Filter '*.otf' -File -ErrorAction SilentlyContinue)
    if ($direct.Count -gt 0) { return $top }
    foreach ($nested in (Get-ChildItem -LiteralPath $top.FullName -Directory -Force -ErrorAction SilentlyContinue)) {
      if (@(Get-ChildItem -LiteralPath $nested.FullName -Filter '*.otf' -File -ErrorAction SilentlyContinue).Count -gt 0) { return $nested }
    }
  }
  return $null
}

try {
  $program = Find-ProgramDirectory
  if (-not $program) { throw 'The program directory or program_entry.py is missing.' }

  $runtime = Find-PythonRuntime $program
  $pythonName = if ($Mode -eq 'gui') { 'pythonw.exe' } else { 'python.exe' }
  $python = if ($runtime) { Join-Path $runtime.FullName (Join-Path 'python' $pythonName) } else { $null }
  if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    $installer = Join-Path $root.FullName 'install-runtime.ps1'
    if (-not (Test-Path -LiteralPath $installer)) { throw 'The bundled Python runtime and installer are missing.' }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Root $root.FullName
    if ($LASTEXITCODE -ne 0) { throw 'Runtime download or extraction failed.' }
    $program = Find-ProgramDirectory
    $runtime = Find-PythonRuntime $program
    $python = if ($runtime) { Join-Path $runtime.FullName (Join-Path 'python' $pythonName) } else { $null }
  }
  if (-not $python -or -not (Test-Path -LiteralPath $python)) { throw "$pythonName is missing after runtime installation." }

  $entry = Join-Path $program.FullName 'program_entry.py'
  if (-not (Test-Path -LiteralPath $entry)) { throw 'The program entry is missing.' }
  $core = Find-CoreDirectory $program
  $fonts = Find-FontDirectory $program
  if (-not $core -or -not (Test-Path -LiteralPath (Join-Path $core.FullName 'video\ffmpeg.exe'))) { throw 'The bundled FFmpeg is missing.' }
  if (-not $fonts) { throw 'The bundled fonts are missing.' }

  $env:PYTHONNOUSERSITE = '1'
  $env:PYTHONUTF8 = '1'
  if ($Mode -eq 'gui') {
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $python
    $start.WorkingDirectory = $root.FullName
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.Arguments = '-X utf8 "' + $entry.Replace('"', '\"') + '"'
    [void][Diagnostics.Process]::Start($start)
    exit 0
  }

  Write-Host 'Starting diagnostic mode...'
  & $python -X utf8 $entry
  exit $LASTEXITCODE
}
catch {
  Write-Error $_.Exception.Message
  exit 1
}
