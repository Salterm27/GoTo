<#
  Installs goto on Windows 10/11 for the current user.

  Easiest: double-click install-windows.cmd
  Or from PowerShell:  powershell -ExecutionPolicy Bypass -File .\install-windows.ps1 [-Port 8080]

  What it does:
    - copies goto.py to %LOCALAPPDATA%\goto      (your links live in goto.db there)
    - adds "127.0.0.1 goto" and "::1 goto" to the hosts file  (one admin prompt)
    - adds the `goto` command for PowerShell / Windows Terminal
    - starts goto now, hidden, and at every sign-in
  Safe to run again to upgrade; your links are kept.
#>
param([int]$Port = 80)

$ErrorActionPreference = 'Stop'
$Here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$App    = Join-Path $env:LOCALAPPDATA 'goto'
$Bin    = Join-Path $App 'bin'
$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$HostsFile = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'

function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host ""; Write-Host $msg -ForegroundColor Red; exit 1 }

function Find-Python {
    # Prefer the py launcher; skip the Microsoft Store "python" placeholder (it fails the check).
    foreach ($candidate in @('py -3', 'python', 'python3')) {
        $parts = $candidate.Split(' ')
        $exe = $parts[0]
        $extra = @()
        if ($parts.Count -gt 1) { $extra = $parts[1..($parts.Count - 1)] }
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $out = & $exe @extra -c "import sys; assert sys.version_info >= (3, 8); print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $path = ($out | Select-Object -Last 1).Trim()
                if (Test-Path $path) { return $path }
            }
        } catch { }
    }
    return $null
}

function Stop-Goto {
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like '*goto.py*serve*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Test-Goto($url) {
    try {
        $req = [System.Net.HttpWebRequest]::Create($url)
        $req.Proxy = $null
        $req.Timeout = 2000
        $resp = $req.GetResponse()
        $ok = ([int]$resp.StatusCode -eq 200)
        $resp.Close()
        return $ok
    } catch { return $false }
}

function Invoke-Elevated($script) {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    try {
        Start-Process powershell.exe -Verb RunAs -Wait -WindowStyle Hidden `
             -ArgumentList "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded"
        return $true
    } catch {
        return $false   # the user said No to the admin prompt
    }
}

# --------------------------------------------------------------------------
Write-Host ""
Write-Host "goto installer" -ForegroundColor White
Write-Host ""

$Python = Find-Python
if (-not $Python) {
    Fail ("goto needs Python 3.8 or newer.`n" +
          "Install it from https://www.python.org/downloads/ (or run: winget install Python.Python.3.12),`n" +
          "then run this installer again.")
}
$PythonW = Join-Path (Split-Path $Python) 'pythonw.exe'
if (-not (Test-Path $PythonW)) { $PythonW = $Python }
Say "Using Python at $Python"

if (-not (Test-Path (Join-Path $Here 'goto.py'))) { Fail "goto.py not found next to this installer ($Here)." }

Say "Installing goto into $App"
Stop-Goto
New-Item -ItemType Directory -Force -Path $App, $Bin | Out-Null
Copy-Item (Join-Path $Here 'goto.py') (Join-Path $App 'goto.py') -Force

# The `goto` command. (Use it from PowerShell or Windows Terminal: in the old cmd.exe,
# "goto" is a built-in command and always wins.)
$shim = "@echo off`r`n`"$Python`" `"%LOCALAPPDATA%\goto\goto.py`" %*`r`n"
[IO.File]::WriteAllText((Join-Path $Bin 'goto.cmd'), $shim, [Text.Encoding]::ASCII)

$envKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
$rawPath = [string]$envKey.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
$entries = @($rawPath.Split(';') | Where-Object { $_ })
if (($entries -notcontains '%LOCALAPPDATA%\goto\bin') -and ($entries -notcontains $Bin)) {
    $entries += '%LOCALAPPDATA%\goto\bin'
    $envKey.SetValue('Path', ($entries -join ';'), [Microsoft.Win32.RegistryValueKind]::ExpandString)
    # Setting any user variable through .NET tells Windows to refresh PATH for new windows.
    [Environment]::SetEnvironmentVariable('GOTO_HOME', $App, 'User')
}
$envKey.Close()
$env:Path = "$env:Path;$Bin"

# Hosts file: only ask for admin rights if something is missing.
$hostsText = ''
if (Test-Path $HostsFile) { $hostsText = [IO.File]::ReadAllText($HostsFile) }
$has4 = $hostsText -match '(?m)^[ \t]*127\.0\.0\.1[ \t]+([^#\r\n]*[ \t])?goto[ \t]*(#.*)?\r?$'
$has6 = $hostsText -match '(?m)^[ \t]*::1[ \t]+([^#\r\n]*[ \t])?goto[ \t]*(#.*)?\r?$'
if (-not ($has4 -and $has6)) {
    Say "Adding 'goto' to the hosts file (Windows will ask for permission)"
    $hostsScript = @'
$h = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$c = ''
if (Test-Path $h) { $c = [IO.File]::ReadAllText($h) }
$add = ''
if ($c -notmatch '(?m)^[ \t]*127\.0\.0\.1[ \t]+([^#\r\n]*[ \t])?goto[ \t]*(#.*)?\r?$') { $add += "127.0.0.1 goto`r`n" }
if ($c -notmatch '(?m)^[ \t]*::1[ \t]+([^#\r\n]*[ \t])?goto[ \t]*(#.*)?\r?$') { $add += "::1 goto`r`n" }
if ($add) {
    if ($c.Length -gt 0 -and -not $c.EndsWith("`n")) { $add = "`r`n" + $add }
    [IO.File]::AppendAllText($h, $add, [Text.Encoding]::ASCII)
}
ipconfig /flushdns | Out-Null
'@
    [void](Invoke-Elevated $hostsScript)
    # Check the result by reading the file: Windows doesn't always report the exit code
    # of an elevated process.
    $hostsText = ''
    if (Test-Path $HostsFile) { $hostsText = [IO.File]::ReadAllText($HostsFile) }
    $has4 = $hostsText -match '(?m)^[ \t]*127\.0\.0\.1[ \t]+([^#\r\n]*[ \t])?goto[ \t]*(#.*)?\r?$'
    $has6 = $hostsText -match '(?m)^[ \t]*::1[ \t]+([^#\r\n]*[ \t])?goto[ \t]*(#.*)?\r?$'
    if (-not ($has4 -and $has6)) {
        Fail ("Couldn't update the hosts file (the admin prompt was declined or blocked).`n" +
              "Run the installer again and choose Yes, or add these two lines to`n" +
              "$HostsFile yourself (Notepad, run as administrator):`n    127.0.0.1 goto`n    ::1 goto")
    }
}

# Make sure the port is free (after stopping any older goto).
Start-Sleep -Milliseconds 500
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @('0.0.0.0', '::', '127.0.0.1', '::1') }
if ($busy) {
    $owners = $busy.OwningProcess | Sort-Object -Unique | ForEach-Object {
        $proc = Get-Process -Id $_ -ErrorAction SilentlyContinue
        if ($_ -eq 4) { 'Windows (System) - usually IIS or another built-in web service' }
        elseif ($proc) { "$($proc.ProcessName) (PID $_)" } else { "PID $_" }
    }
    Fail ("Port $Port is already in use by: $($owners -join ', ').`n" +
          "Stop that program, or install goto on another port:`n" +
          "    install-windows.cmd -Port 8080      (links then look like goto:8080/mail)")
}

Say "Starting goto now and at every sign-in"
$serveArgs = "`"$App\goto.py`" serve --host localhost --port $Port --db `"$App\goto.db`" --log `"$App\server.log`""
Set-ItemProperty -Path $RunKey -Name 'goto' -Value "`"$PythonW`" $serveArgs"
Start-Process -FilePath $PythonW -ArgumentList $serveArgs -WindowStyle Hidden -WorkingDirectory $App

$url = 'http://goto'
if ($Port -ne 80) { $url = "http://goto:$Port" }
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    if (Test-Goto "$url/_/health") { $ok = $true; break }
    Start-Sleep -Milliseconds 300
}

Write-Host ""
if ($ok) {
    Say "Done! goto is running."
    Write-Host ""
    Write-Host "  Open $url/ in your browser to add links. The first time, type it with http://"
    Write-Host "  so the browser learns goto/ is a site; after that goto/mail just works."
    Write-Host ""
    Write-Host "  In a NEW PowerShell or Windows Terminal window you can also use:"
    Write-Host "    goto add mail https://mail.google.com -a m"
    Write-Host "    goto ls"
    if ($Port -ne 80) {
        [Environment]::SetEnvironmentVariable('GOTO_SERVER', $url, 'User')
        Write-Host ""
        Write-Host "  Note: running on port $Port, so links look like goto:$Port/mail"
    }
} else {
    Fail ("goto didn't answer yet. Look at the log:`n" +
          "    Get-Content `"$App\server.log`" -Tail 20")
}
