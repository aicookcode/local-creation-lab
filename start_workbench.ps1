param(
  [ValidateSet('gui', 'diagnostic')]
  [string]$Mode = 'gui'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$entry = Join-Path $root 'program\program_entry.py'
$runtime = Join-Path $root 'program\runtime'
$log = Join-Path $root 'logs\startup_error.log'

try {
  if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw '缺少程序入口，请重新解压完整的工作台压缩包。' }
  if (-not (Test-Path -LiteralPath (Join-Path $root 'program\core\video\ffmpeg.exe') -PathType Leaf)) {
    throw '缺少内置视频工具，请重新解压完整的工作台压缩包。'
  }
  if (-not (Get-ChildItem -LiteralPath (Join-Path $root 'program\resources\fonts') -File -Filter '*.otf')) {
    throw '缺少内置字体，请重新解压完整的工作台压缩包。'
  }

  $env:PYTHONNOUSERSITE = '1'
  $env:PYTHONUTF8 = '1'
  $env:PYTHONIOENCODING = 'utf-8'
  $env:PYTHONPYCACHEPREFIX = Join-Path $root 'cache\pycache'
  Remove-Item Env:PYTHONHOME,Env:PYTHONPATH -ErrorAction SilentlyContinue
  $installer = Join-Path $root 'install-runtime.ps1'
  & $installer -Root $root

  if ($Mode -eq 'diagnostic') {
    & (Join-Path $runtime 'python\python.exe') -X utf8 $entry
    exit $LASTEXITCODE
  }

  $start = New-Object Diagnostics.ProcessStartInfo
  $start.FileName = Join-Path $runtime 'python\pythonw.exe'
  $start.WorkingDirectory = $root
  $start.UseShellExecute = $false
  $start.CreateNoWindow = $true
  $start.Arguments = '-X utf8 "' + $entry + '"'
  [void][Diagnostics.Process]::Start($start)
  exit 0
} catch {
  $message = $_.Exception.Message
  try {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
    $_ | Out-String | Set-Content -LiteralPath $log -Encoding UTF8
  } catch {
    [Console]::Error.WriteLine($_.Exception.Message)
  }
  [Console]::Error.WriteLine($message)
  if ($Mode -eq 'gui') {
    Add-Type -AssemblyName System.Windows.Forms
    $details = @($message, '', "详细记录：$log") -join [Environment]::NewLine
    [void][System.Windows.Forms.MessageBox]::Show($details, '工作台启动未完成', 'OK', 'Error')
  }
  exit 1
}
