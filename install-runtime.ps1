param([string]$Root = $PSScriptRoot)

$ErrorActionPreference = 'Stop'
$Root = [IO.Path]::GetFullPath($Root)
$config = Get-Content -Raw -LiteralPath (Join-Path $Root 'program\config\runtime_download.json') | ConvertFrom-Json
$url = [string]$config.download_url
$expected = [string]$config.sha256
$archiveName = [string]$config.archive_name
if (-not $url -or $expected -notmatch '^[0-9a-fA-F]{64}$' -or $archiveName -notmatch '^[a-zA-Z0-9_.-]+\.zip$') {
  throw '运行环境下载地址或校验值无效。请检查 program/config/runtime_download.json。'
}

$required = @(
  'python\python.exe', 'python\pythonw.exe', 'python\python313._pth',
  'portrait-python310\python.exe', 'portrait-python310\python310._pth',
  'qwen-tts\llama-tts.exe'
)
function Test-Runtime([string]$Directory) {
  foreach ($file in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $Directory $file) -PathType Leaf)) { return $false }
  }
  return $true
}

$destination = Join-Path $Root 'program\runtime'
$downloadDir = Join-Path $Root 'cache\runtime_install'
$incomplete = Join-Path $downloadDir 'install.incomplete'
if ((Test-Runtime $destination) -and (-not (Test-Path -LiteralPath $incomplete))) { exit 0 }

New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
try {
  $lock = [IO.File]::Open((Join-Path $downloadDir 'install.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
} catch [IO.IOException] {
  throw '另一个窗口正在安装运行环境，请等它完成后重试。'
}

try {
  if ((Test-Runtime $destination) -and (-not (Test-Path -LiteralPath $incomplete))) { exit 0 }
  $archive = Join-Path $downloadDir $archiveName
  $partial = $archive + '.partial'
  if (Test-Path -LiteralPath $archive) {
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
      Remove-Item -LiteralPath $archive -Force
    }
  }
  if (-not (Test-Path -LiteralPath $archive)) {
    Write-Host '正在下载 Windows 运行环境。首次启动请保持网络连接。'
    try {
      [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
      Invoke-WebRequest -Uri $url -OutFile $partial -UseBasicParsing -TimeoutSec 600
      if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $expected) {
        throw '下载文件校验失败，请检查网络与下载地址，再重新打开工作台。'
      }
      Move-Item -LiteralPath $partial -Destination $archive
    } finally {
      if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
    }
  }

  Write-Host '正在解压运行环境。'
  $temp = Join-Path $downloadDir ('extract-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $temp | Out-Null
  try {
    # The archive contains long PyTorch paths. Windows tar and Robocopy
    # support paths beyond the legacy Windows PowerShell file APIs.
    $system = [Environment]::SystemDirectory
    $tar = Join-Path $system 'tar.exe'
    if (-not (Test-Path -LiteralPath $tar -PathType Leaf)) {
      throw 'Windows 缺少系统解压工具。请安装系统更新后重试。'
    }
    & $tar -xf $archive -C $temp
    if ($LASTEXITCODE -ne 0) { throw "运行环境解压失败。请检查磁盘空间和目录权限（代码 $LASTEXITCODE）。" }
    $runtime = @((Join-Path $temp 'program\runtime'), (Join-Path $temp 'runtime'), $temp) |
      Where-Object { Test-Runtime $_ } | Select-Object -First 1
    if (-not $runtime) { throw '压缩包内缺少 Windows 运行组件。' }
    $workspacePath = (Resolve-Path -LiteralPath $Root).ProviderPath.TrimEnd('\') + '\'
    $stagingPath = (Resolve-Path -LiteralPath $temp).ProviderPath.TrimEnd('\') + '\'
    $runtime = (Resolve-Path -LiteralPath $runtime).ProviderPath.TrimEnd('\')
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    $destination = (Resolve-Path -LiteralPath $destination).ProviderPath
    if ((-not $stagingPath.StartsWith($workspacePath, [StringComparison]::OrdinalIgnoreCase)) -or
        (-not ($runtime + '\').StartsWith($stagingPath, [StringComparison]::OrdinalIgnoreCase)) -or
        (-not $destination.StartsWith($workspacePath, [StringComparison]::OrdinalIgnoreCase))) {
      throw '运行环境安装目录超出工作台文件夹。'
    }
    New-Item -ItemType File -Force -Path $incomplete | Out-Null
    & (Join-Path $system 'robocopy.exe') $runtime $destination /E /MOVE /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) { throw "运行环境复制失败。请检查磁盘空间和目录权限（代码 $LASTEXITCODE）。" }
    if (-not (Test-Runtime $destination)) { throw '运行环境安装后仍缺少组件。' }
  } finally {
    $resolved = (Resolve-Path -LiteralPath $temp -ErrorAction SilentlyContinue).ProviderPath
    $parent = (Resolve-Path -LiteralPath $downloadDir).ProviderPath
    if ($resolved -and (Split-Path -Parent $resolved) -eq $parent -and (Split-Path -Leaf $resolved) -match '^extract-[0-9a-f]{32}$') {
      Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
  [IO.File]::Delete($incomplete)
  Write-Host '运行环境已准备好。'
} finally {
  $lock.Dispose()
}
