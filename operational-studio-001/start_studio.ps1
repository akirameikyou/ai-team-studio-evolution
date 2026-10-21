# AI Team Studio - start (relay + Studio page) and open it in Chrome.
# Usage: double-click start_studio.bat, or:  powershell -ExecutionPolicy Bypass -File start_studio.ps1
# Starts only what is not already running. Uses ports 8787 (relay) and 8773 (Studio page). Nothing is sent to any AI.
param([switch]$NoBrowser)   # -NoBrowser: start and check only (used for testing)
$ErrorActionPreference = 'Stop'
$here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$relay = 8787; $page = 8773
$url   = "http://localhost:$page/"

function Test-Port($port) {
  try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $port); $c.Close(); return $true } catch { return $false }
}
function Wait-Port($port, $seconds) {
  for ($i = 0; $i -lt ($seconds * 2); $i++) { if (Test-Port $port) { return $true }; Start-Sleep -Milliseconds 500 }
  return $false
}

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { Write-Host 'Python が見つかりません。Python 3 を入れてから、もう一度実行してください。'; Read-Host 'Enter で閉じる'; exit 1 }

if (Test-Port $relay) { Write-Host "中継: すでに動いています（$relay）" }
else {
  Start-Process -FilePath $py.Source -ArgumentList @("`"$here\proxy\studio_proxy.py`"", '--port', "$relay") -WorkingDirectory "$here\proxy" -WindowStyle Minimized
  if (Wait-Port $relay 15) { Write-Host "中継: 起動しました（$relay）" } else { Write-Host "中継: 起動を確かめられませんでした（$relay）" }
}

if (Test-Port $page) { Write-Host "スタジオ: すでに動いています（$page）" }
else {
  Start-Process -FilePath $py.Source -ArgumentList @('-m', 'http.server', "$page", '--bind', '127.0.0.1', '--directory', "`"$here`"") -WorkingDirectory $here -WindowStyle Minimized
  if (Wait-Port $page 15) { Write-Host "スタジオ: 起動しました（$page）" } else { Write-Host "スタジオ: 起動を確かめられませんでした（$page）" }
}

try {
  $h = Invoke-RestMethod -Uri "http://127.0.0.1:$relay/health" -TimeoutSec 5
  Write-Host ('使える API: ' + ($h.providers -join ', '))
} catch { Write-Host '中継の状態を読めませんでした。' }

# Chrome keeps this page's saved record and your sign-ins between sessions. Always the same address (localhost),
# because the browser keeps a separate record for 127.0.0.1.
$chrome = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe", "$env:LocalAppData\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($NoBrowser) { Write-Host 'ブラウザは開きません（-NoBrowser）' }
elseif ($chrome) { Start-Process -FilePath $chrome -ArgumentList $url } else { Start-Process $url }
if (-not $NoBrowser) { Write-Host "開きました: $url" }
Write-Host '中継とスタジオは、最小化されたウィンドウで動いています。止めるときは stop_studio.bat を実行してください。'
Start-Sleep -Seconds 4
