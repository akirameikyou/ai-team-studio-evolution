# AI Team Studio - stop the relay and the Studio page started by start_studio.ps1.
# Stops only Python processes whose command line is this Studio's relay or this Studio's page server.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$targets = Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object {
  $_.CommandLine -and (($_.CommandLine -like '*studio_proxy.py*') -or ($_.CommandLine -like '*http.server 8773*'))
}
if (-not $targets) { Write-Host '止めるものはありません。'; Start-Sleep -Seconds 2; exit 0 }
foreach ($p in $targets) { Write-Host ("停止: " + $p.ProcessId + "  " + $p.CommandLine); Stop-Process -Id $p.ProcessId -Confirm:$false }
Write-Host '止めました。スタジオの記録はブラウザに、記録の写しは proxy\exchange\_snapshots に残っています。'
Start-Sleep -Seconds 3
