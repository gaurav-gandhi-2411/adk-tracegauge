# Usage: measure_install.ps1 -Run 1 [-Split]
# Times venv creation, pip install (headline: single `pip install adk-tracegauge`; -Split: deps first,
# then adk-tracegauge alone), and `adk-tracegauge quickstart`. Cold cache via --no-cache-dir.
param([int]$Run = 1, [switch]$Split)
$ErrorActionPreference = "Continue"
$root = "C:\tg\r$Run"
$log = "C:\tg\r$Run.log"
if (Test-Path $root) { throw "$root already exists - refusing to reuse a non-fresh venv" }
New-Item -ItemType Directory -Force C:\tg | Out-Null
$sw = [System.Diagnostics.Stopwatch]::StartNew()
function Step($name, [scriptblock]$body) {
  $t = Get-Date
  $s = [System.Diagnostics.Stopwatch]::StartNew()
  "[{0:HH:mm:ss.fff}] START {1}" -f $t, $name | Tee-Object -FilePath $log -Append
  & $body 2>&1 | Tee-Object -FilePath $log -Append | Out-Null
  $s.Stop()
  "[{0:HH:mm:ss.fff}] END   {1} elapsed={2:N1}s exit={3}" -f (Get-Date), $name, $s.Elapsed.TotalSeconds, $LASTEXITCODE | Tee-Object -FilePath $log -Append
}
"run=$Run split=$Split python=$(python --version)" | Tee-Object -FilePath $log
$total = [System.Diagnostics.Stopwatch]::StartNew()
Step "venv-create" { python -m venv $root }
$py = "$root\Scripts\python.exe"
if ($Split) {
  Step "pip-install google-adk[eval] (deps only)" { & $py -m pip install --no-cache-dir "google-adk[eval]>=2.6.0,<2.8.0" }
  Step "pip-install adk-tracegauge (own wheel)" { & $py -m pip install --no-cache-dir adk-tracegauge }
} else {
  Step "pip-install adk-tracegauge (headline, one command)" { & $py -m pip install --no-cache-dir adk-tracegauge }
}
Step "quickstart" { & "$root\Scripts\adk-tracegauge.exe" quickstart }
$total.Stop()
"TOTAL run=$Run split=$Split elapsed={0:N1}s" -f $total.Elapsed.TotalSeconds | Tee-Object -FilePath $log -Append
& $py -m pip list --format=freeze | Measure-Object -Line | ForEach-Object { "installed_packages=$($_.Lines)" } | Tee-Object -FilePath $log -Append
& $py -m pip list --format=freeze | Select-String "google-adk|adk-tracegauge|litellm" | Tee-Object -FilePath $log -Append
