<#
  Removes goto from Windows. Your links (%LOCALAPPDATA%\goto\goto.db) are kept
  unless you pass -Purge.

  Easiest: double-click uninstall-windows.cmd
  Or:      powershell -ExecutionPolicy Bypass -File .\uninstall-windows.ps1 [-Purge]
#>
param([switch]$Purge)

$ErrorActionPreference = 'Stop'
$App    = Join-Path $env:LOCALAPPDATA 'goto'
$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'

Write-Host "==> Stopping goto" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like '*goto.py*serve*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Remove-ItemProperty -Path $RunKey -Name 'goto' -ErrorAction SilentlyContinue

Write-Host "==> Removing the goto command" -ForegroundColor Cyan
$envKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
$rawPath = [string]$envKey.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
$bin = Join-Path $App 'bin'
$kept = @($rawPath.Split(';') | Where-Object { $_ -and $_ -ne '%LOCALAPPDATA%\goto\bin' -and $_ -ne $bin })
$envKey.SetValue('Path', ($kept -join ';'), [Microsoft.Win32.RegistryValueKind]::ExpandString)
$envKey.Close()
[Environment]::SetEnvironmentVariable('GOTO_HOME', $null, 'User')     # also refreshes PATH
[Environment]::SetEnvironmentVariable('GOTO_SERVER', $null, 'User')

Write-Host "==> Removing 'goto' from the hosts file (Windows will ask for permission)" -ForegroundColor Cyan
$hostsScript = @'
$h = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$lines = [IO.File]::ReadAllLines($h)
$keep = $lines | Where-Object { $_ -notmatch '^[ \t]*(127\.0\.0\.1|::1)[ \t]+goto[ \t]*$' }
[IO.File]::WriteAllLines($h, [string[]]$keep, [Text.Encoding]::ASCII)
ipconfig /flushdns | Out-Null
'@
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($hostsScript))
try {
    Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded"
} catch {
    Write-Host "   Skipped (permission declined). You can remove the 'goto' lines from" -ForegroundColor Yellow
    Write-Host "   $env:SystemRoot\System32\drivers\etc\hosts yourself." -ForegroundColor Yellow
}

if (Test-Path $App) {
    if ($Purge) {
        Remove-Item -Recurse -Force $App
        Write-Host "goto removed, including your links."
    } else {
        Get-ChildItem $App | Where-Object { $_.Name -notlike 'goto.db*' } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "goto removed. Your links are still in $App\goto.db (run with -Purge to delete them)."
    }
} else {
    Write-Host "goto removed."
}
