# Install the garden CLI, dsh and its skills on a Windows laptop, from the portal alone.
#
# Usage, in Windows PowerShell 5.1 (the version most laptops have) or PowerShell 7:
#   $env:GARDEN_API_URL = 'https://portal.example'
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
# or, as a one-liner, which needs no execution policy change:
#   $env:GARDEN_API_URL = 'https://portal.example'; irm https://portal.example/api/agent/downloads/install.ps1 | iex
#
# Installs Node.js when missing or older than 24. It gets Python 3.12
# itself with the pinned uv release, whose SHA-256 it checks, and it uses tar.exe, which Windows 10 and
# later include. Nothing is installed unless the portal's SHA-256 matches.
#
# Environment:
#   GARDEN_API_URL       the portal address (required)
#   GARDEN_PYTHON          a Python 3.12 to run garden with, instead of the one the installer fetches
#   GARDEN_BIN_DIR          where the garden.cmd and dsh.cmd launchers go (default: %USERPROFILE%\.local\bin)
#   GARDEN_HOME             garden's own folder, where dsh is unpacked too (default: %USERPROFILE%\.garden)
#   GARDEN_DSH_HOME         dsh's home, where cordis.patch.yml goes (default: %USERPROFILE%\.dsh)
#   GARDEN_INSTALL_TEST     set to 1 for an unattended run. The login link goes to GARDEN_LOGIN_LINK_FILE
#                          when that is set, so a browser can approve it; otherwise it is opened here.
#   GARDEN_LOGIN_LINK_FILE  with GARDEN_INSTALL_TEST=1: the file the login link is written to
#
# The model is the portal's: no model setting, endpoint or key is taken or written here.
# dsh's model call uses the person's CLI login, which `garden dsh` reads when it starts.
# The folders must use ASCII characters, because the launchers are batch files.
# This mirrors install.sh. The tests check its text; the Windows run is done by hand, with the steps in
# agent/README.md.

$savedErrorActionPreference = $ErrorActionPreference
$savedProgressPreference = $ProgressPreference
# The installer sets these for the garden commands it runs; they go back to what the caller had.
$savedGardenHome = $env:GARDEN_HOME
$savedPortalUrl = $env:GARDEN_API_URL
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Windows PowerShell 5.1 can default to TLS 1.0, which the portal and GitHub refuse. Add TLS 1.2.
if ($PSVersionTable.PSEdition -ne 'Core') {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
}

$portal = $env:GARDEN_API_URL
if (-not $portal) { throw 'Set GARDEN_API_URL to the portal address, such as https://portal.example' }
$portal = $portal.TrimEnd('/')

# uv and the Python it installs are pinned here. The uv zip is checked against its published SHA-256.
$uvVersion = '0.13.1'
$uvSha256 = '3b2661a463a5baea7f5076ea2b25ab71d77f36a6c0d809e5350651792f80a672'
$pythonVersion = '3.12.15'
$nodeVersion = '26.13.0'
$nodeSha256 = 'dcc60d59a0746999f99064a01c020445d54d84228c691cdbf25b49ac3b59150a'

$userFolder = $env:USERPROFILE
$binDir = if ($env:GARDEN_BIN_DIR) { $env:GARDEN_BIN_DIR } else { Join-Path $userFolder '.local\bin' }
$gardenHome = if ($env:GARDEN_HOME) { $env:GARDEN_HOME } else { Join-Path $userFolder '.garden' }
$dshHome = if ($env:GARDEN_DSH_HOME) { $env:GARDEN_DSH_HOME } else { Join-Path $userFolder '.dsh' }
$manifestPath = Join-Path $userFolder '.local\share\garden\install-manifest.json'
$gardenHomeExisted = Test-Path -LiteralPath $gardenHome
$binDirExisted = Test-Path -LiteralPath $binDir
$dshHomeExisted = Test-Path -LiteralPath $dshHome
$pythonHomeExisted = Test-Path -LiteralPath (Join-Path $gardenHome 'python')
$uvDirExisted = Test-Path -LiteralPath (Join-Path $gardenHome "tools\uv-$uvVersion")
$pyzExisted = Test-Path -LiteralPath (Join-Path $gardenHome 'garden.pyz')
$configExisted = Test-Path -LiteralPath (Join-Path $gardenHome 'config.json')
$skillsExisted = Test-Path -LiteralPath (Join-Path $gardenHome 'skills')
$dshConfigExisted = Test-Path -LiteralPath (Join-Path $gardenHome 'dsh-config.yml')
$gardenLauncherExisted = Test-Path -LiteralPath (Join-Path $binDir 'garden.cmd')
$dshLauncherExisted = Test-Path -LiteralPath (Join-Path $binDir 'dsh.cmd')
$dshPatchExisted = Test-Path -LiteralPath (Join-Path $dshHome 'cordis.patch.yml')
$script:DshFolderCreated = $false
$script:DshPatchWritten = $false
$script:LoginSucceeded = $false
$script:NodeTreePath = $null
$script:NodeTreeExisted = $true
$script:WebDirCreated = $false
$script:ProgramShortcutDirCreated = $false
$script:WebShortcutFiles = @()
$manifestRoot = Split-Path $manifestPath
New-Item -ItemType Directory -Path $manifestRoot -Force | Out-Null
if (Test-Path -LiteralPath $manifestPath) {
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.version -ne 1) { throw 'Unsupported Garden install manifest version.' }
} else {
    $manifest = [pscustomobject]@{
        version = 1
        artifacts = @()
        path_entries = @()
        demo_projects = @()
        docker_images = @()
        logins = @()
    }
}
$manifestEncoding = New-Object System.Text.UTF8Encoding $false
function Save-InstallManifest {
    $temporary = "$manifestPath.tmp"
    $json = ConvertTo-Json -InputObject $manifest -Depth 20
    [System.IO.File]::WriteAllText($temporary, $json, $manifestEncoding)
    Move-Item -LiteralPath $temporary -Destination $manifestPath -Force
}
function Test-ManifestOwnsPath([string]$path) {
    $full = [System.IO.Path]::GetFullPath($path)
    foreach ($entry in @($manifest.artifacts)) {
        if ([string]::Equals($entry.path, $full, [StringComparison]::OrdinalIgnoreCase)) { return $true }
        if ($entry.kind -eq 'tree' -and $full.StartsWith($entry.path.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}
function Record-InstallArtifact([string]$path, [string]$kind, [string]$root) {
    if (-not (Test-Path -LiteralPath $path)) { return }
    $full = [System.IO.Path]::GetFullPath($path)
    $entry = [pscustomobject]@{
        path = $full
        resolved_path = $full
        root = [System.IO.Path]::GetFullPath($root)
        kind = $kind
        owner = 'agent-installer'
    }
    if ($kind -eq 'file') {
        $entry | Add-Member -NotePropertyName sha256 -NotePropertyValue (Get-FileHash -Algorithm SHA256 -LiteralPath $full).Hash.ToLowerInvariant()
    }
    $items = @($manifest.artifacts)
    $index = -1
    for ($i = 0; $i -lt $items.Count; $i++) {
        if ([string]::Equals($items[$i].path, $full, [StringComparison]::OrdinalIgnoreCase)) { $index = $i; break }
    }
    if ($index -ge 0) {
        if ($items[$index].kind -eq 'tree') { $entry.kind = 'tree'; $entry.root = $items[$index].root }
        $items[$index] = $entry
    } else {
        $items += $entry
    }
    $manifest.artifacts = @($items)
    Save-InstallManifest
}
function Record-UserPathEntry([string]$value, [bool]$createdValue = $false) {
    foreach ($entry in @($manifest.path_entries)) {
        if ($entry.scope -eq 'windows-user' -and [string]::Equals($entry.value.TrimEnd('\'), $value.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) { return }
    }
    $manifest.path_entries = @(@($manifest.path_entries) + [pscustomobject]@{ value = $value; scope = 'windows-user'; owner = 'agent-installer'; created_value = $createdValue })
    Save-InstallManifest
}
function Record-InstallLogin([string]$portalUrl, [string]$launcher, [string]$gardenFolder) {
    $logins = @($manifest.logins)
    $entry = [pscustomobject]@{ portal = $portalUrl; launcher = $launcher; garden_home = $gardenFolder }
    $index = -1
    for ($i = 0; $i -lt $logins.Count; $i++) { if ($logins[$i].portal -eq $portalUrl) { $index = $i; break } }
    if ($index -ge 0) { $logins[$index] = $entry } else { $logins += $entry }
    $manifest.logins = @($logins)
    Save-InstallManifest
}
function Write-InstallManifest {
    if (-not $gardenHomeExisted -and (Test-Path -LiteralPath $gardenHome)) {
        Record-InstallArtifact $gardenHome 'tree' (Split-Path $gardenHome)
    } else {
        if (-not $pythonHomeExisted) { Record-InstallArtifact (Join-Path $gardenHome 'python') 'tree' $gardenHome }
        if (-not $uvDirExisted) { Record-InstallArtifact (Join-Path $gardenHome "tools\uv-$uvVersion") 'tree' $gardenHome }
        if (-not $pyzExisted) { Record-InstallArtifact (Join-Path $gardenHome 'garden.pyz') 'file' $gardenHome }
        if (-not $configExisted) { Record-InstallArtifact (Join-Path $gardenHome 'config.json') 'file' $gardenHome }
        if (-not $skillsExisted) { Record-InstallArtifact (Join-Path $gardenHome 'skills') 'tree' $gardenHome }
        if ($script:DshFolderCreated) { Record-InstallArtifact (Join-Path $gardenHome 'dsh') 'tree' $gardenHome }
        if (-not $dshConfigExisted) { Record-InstallArtifact (Join-Path $gardenHome 'dsh-config.yml') 'file' $gardenHome }
    }
    if (-not $binDirExisted) { Record-InstallArtifact $binDir 'directory' (Split-Path $binDir) }
    if (-not $gardenLauncherExisted) { Record-InstallArtifact (Join-Path $binDir 'garden.cmd') 'file' $binDir }
    if (-not $dshLauncherExisted) { Record-InstallArtifact (Join-Path $binDir 'dsh.cmd') 'file' $binDir }
    if (-not $dshHomeExisted) { Record-InstallArtifact $dshHome 'tree' (Split-Path $dshHome) }
    elseif ($script:DshPatchWritten -and -not $dshPatchExisted) { Record-InstallArtifact (Join-Path $dshHome 'cordis.patch.yml') 'file' $dshHome }
    if ($script:NodeTreePath -and -not $script:NodeTreeExisted) { Record-InstallArtifact $script:NodeTreePath 'tree' (Split-Path $script:NodeTreePath) }
    if ($script:WebDirCreated) { Record-InstallArtifact (Join-Path $gardenHome 'web') 'tree' $gardenHome }
    if ($script:ProgramShortcutDirCreated) { Record-InstallArtifact $script:ProgramShortcutDir 'directory' (Split-Path $script:ProgramShortcutDir) }
    foreach ($path in $script:WebShortcutFiles) { Record-InstallArtifact $path 'file' (Split-Path $path) }
    if ($script:LoginSucceeded) { Record-InstallLogin $portal (Join-Path $binDir 'garden.cmd') $gardenHome }
}
$stamp = Get-Date -Format 'yyyyMMddHHmmss'
$utf8 = New-Object System.Text.UTF8Encoding $false

foreach ($folder in @($binDir, $gardenHome, $dshHome)) {
    if ($folder -match '[^\x00-\x7F]') {
        throw "The folder $folder has characters outside ASCII. Set GARDEN_BIN_DIR, GARDEN_HOME and GARDEN_DSH_HOME to ASCII folders, then run the installer again."
    }
}

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('garden-install-' + [System.Guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
# Progress: every stage prints one numbered line, [n/total], then a Done line; a stage that fails gets a
# Failed line from the catch at the end. The total is worked out below, once the Python stages are known.
$script:StepNumber = 0
$script:TotalSteps = 0
$script:CurrentStep = $null
# A meter is drawn only on a console, so piped output (CI, redirected) has the stage lines alone.
$script:ShowMeter = $false
try { $script:ShowMeter = -not [Console]::IsOutputRedirected } catch { }

function Start-Step([string]$message) {
    $script:StepNumber++
    $script:CurrentStep = $message
    Write-Host "[$($script:StepNumber)/$($script:TotalSteps)] $message"
}

function Complete-Step {
    Write-Host "[$($script:StepNumber)/$($script:TotalSteps)] Done: $($script:CurrentStep)"
    $script:CurrentStep = $null
}

function Format-Size([long]$bytes) {
    if ($bytes -ge 1048576) { return ('{0:N1} MB' -f ($bytes / 1048576)) }
    if ($bytes -ge 1024) { return ('{0:N0} KB' -f ($bytes / 1024)) }
    return "$bytes bytes"
}

# Streams one file to $destination, with no Invoke-WebRequest bar (it makes downloads slow on Windows
# PowerShell 5.1). On a console a meter shows the bytes received and the total, at most once a second.
# The caller starts and completes the stage, so the file is checked inside the same stage.
function Receive-File([string]$uri, [string]$destination, [string]$activity, [long]$expectedSize = 0) {
    $response = $null
    $inputStream = $null
    $outputStream = $null
    $savedProgress = $ProgressPreference
    try {
        $request = [System.Net.HttpWebRequest]::Create($uri)
        $request.AllowAutoRedirect = $true
        $response = $request.GetResponse()
        if ($expectedSize -le 0) { $expectedSize = $response.ContentLength }
        $inputStream = $response.GetResponseStream()
        $outputStream = [System.IO.File]::Create($destination)
        $buffer = New-Object byte[] 65536
        $received = [long]0
        $lastReport = [DateTime]::UtcNow
        # Write-Progress is silent under the installer's SilentlyContinue, so it is turned on here only.
        $ProgressPreference = 'Continue'
        while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $outputStream.Write($buffer, 0, $count)
            $received += $count
            if ($script:ShowMeter -and ([DateTime]::UtcNow - $lastReport).TotalSeconds -ge 1) {
                Show-Meter $activity $received $expectedSize
                $lastReport = [DateTime]::UtcNow
            }
        }
        if ($script:ShowMeter) { Show-Meter $activity $received $expectedSize }
    } finally {
        if ($script:ShowMeter) { Write-Progress -Activity $activity -Completed }
        $ProgressPreference = $savedProgress
        if ($outputStream) { $outputStream.Dispose() }
        if ($inputStream) { $inputStream.Dispose() }
        if ($response) { $response.Dispose() }
    }
}

function Show-Meter([string]$activity, [long]$received, [long]$expectedSize) {
    if ($expectedSize -gt 0) {
        $percent = [int][Math]::Min(100, [Math]::Floor(100 * $received / $expectedSize))
        $status = '{0} of {1} ({2}%)' -f (Format-Size $received), (Format-Size $expectedSize), $percent
        Write-Progress -Activity $activity -Status $status -PercentComplete $percent
    } else {
        Write-Progress -Activity $activity -Status ('{0} received' -f (Format-Size $received))
    }
}

function Get-Sha256([string]$path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
}

# Runs a program and returns its exit code and its standard output. Its standard error is not an error
# here: Windows PowerShell 5.1 would otherwise stop on anything a program writes to stderr.
function Invoke-Program([string]$file, [string[]]$arguments) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $file @arguments 2>$null
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $saved
    }
    return [pscustomobject]@{ Code = $code; Output = @($output) }
}

# Prints the path of a Python 3.12 that uv installs under GARDEN_HOME. GARDEN_PYTHON, when it is set,
# is used as it is and checked by the caller.
function Get-Python {
    if ($env:GARDEN_PYTHON) { return $env:GARDEN_PYTHON }
    $uvDir = Join-Path $gardenHome "tools\uv-$uvVersion"
    $uv = Join-Path $uvDir 'uv.exe'
    New-Item -ItemType Directory -Path $gardenHome -Force | Out-Null
    if (-not (Test-Path $uv)) {
        $zip = Join-Path $tmp 'uv.zip'
        Start-Step "Downloading uv $uvVersion..."
        Receive-File "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip" $zip "uv $uvVersion"
        if ((Get-Sha256 $zip) -ne $uvSha256) {
            throw 'uv does not match its published SHA-256. Nothing was installed.'
        }
        Complete-Step
        Start-Step 'Unpacking uv...'
        New-Item -ItemType Directory -Path $uvDir -Force | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $uvDir -Force
        Complete-Step
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $gardenHome 'python'
    try {
        # --no-bin keeps uv from writing a python shim into the launcher folder, where uv would warn
        # about an existing one. Its output is discarded, as it is for every program here.
        Start-Step "Installing Python $pythonVersion..."
        $install = Invoke-Program $uv @('python', 'install', '--no-bin', '--quiet', $pythonVersion)
        if ($install.Code -ne 0) { throw "uv could not install Python $pythonVersion." }
        $found = Invoke-Program $uv @('python', 'find', '--managed-python', $pythonVersion)
        $path = $found.Output | Select-Object -Last 1
        if ($found.Code -ne 0 -or -not $path) { throw "Python $pythonVersion was not found after install." }
        Complete-Step
        return [string]$path
    } finally {
        Remove-Item Env:UV_PYTHON_INSTALL_DIR -ErrorAction SilentlyContinue
    }
}

# Adds a folder to the user's PATH, so that garden and dsh run by name in new terminals.
function Add-UserPath([string]$folder) {
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @()
    if ($current) { $entries = @($current -split ';' | Where-Object { $_ }) }
    $wanted = $folder.TrimEnd('\')
    if (-not $env:GARDEN_INSTALL_TEST -and -not ($entries | Where-Object { $_.TrimEnd('\') -ieq $wanted })) {
        [Environment]::SetEnvironmentVariable('Path', (@($folder) + $entries) -join ';', 'User')
        Record-UserPathEntry $folder (-not [bool]$current)
        Write-Output "Added $folder to your PATH. Open a new terminal to run garden and dsh by name."
    }
    $env:Path = "$folder;$env:Path"
}

# Ensures Node.js (pinned to $nodeVersion) is available on the machine.
# If node is missing or older than v24, downloads the pinned release, checks its SHA-256 and adds it to PATH.
function Ensure-Node {
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    if ($nodeCmd) {
        try {
            $verOutput = (& $nodeCmd.Source -v).Trim().TrimStart('v')
            $major = [int]($verOutput.Split('.')[0])
            if ($major -ge 24) {
                return
            }
        } catch { }
    }

    $nodeToolsDir = Join-Path $gardenHome "tools\node-v$nodeVersion"
    $nodeDir = Join-Path $nodeToolsDir "node-v$nodeVersion-win-x64"
    $nodeExe = Join-Path $nodeDir 'node.exe'
    $script:NodeTreePath = $nodeToolsDir
    $script:NodeTreeExisted = Test-Path -LiteralPath $nodeToolsDir

    if (-not (Test-Path $nodeExe)) {
        $zip = Join-Path $tmp 'node.zip'
        Start-Step "Downloading Node.js $nodeVersion..."
        Receive-File "https://nodejs.org/dist/v$nodeVersion/node-v$nodeVersion-win-x64.zip" $zip "Node.js $nodeVersion"
        if ((Get-Sha256 $zip) -ne $nodeSha256) {
            throw 'Node.js does not match its published SHA-256. Nothing was installed.'
        }
        Complete-Step
        Start-Step 'Unpacking Node.js...'
        New-Item -ItemType Directory -Path $nodeToolsDir -Force | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $nodeToolsDir -Force
        Complete-Step
    }

    Add-UserPath $nodeDir
}

# The browser UI (`dsh web`) listens on this fixed local port, so the shortcuts and the stop script agree.
$webPort = 8765

# Writes the start and stop scripts for the browser UI and the shortcuts that run them. Each run replaces
# them. The start script runs dsh web with no console window, opens its address once it listens, and reuses
# a server that is already running. The stop script ends the server that the start script began.
function Install-WebShortcuts([string]$launcher, [string]$gardenFolder) {
    if ($env:GARDEN_INSTALL_TEST) { return }
    $webDir = Join-Path $gardenFolder 'web'
    $webDirExisted = Test-Path -LiteralPath $webDir
    if ($webDirExisted -and -not (Test-ManifestOwnsPath $webDir)) {
        Write-Warning "Preserving the existing, unrecorded web folder at $webDir."
        return
    }
    New-Item -ItemType Directory -Force -Path $webDir | Out-Null
    if (-not $webDirExisted) { $script:WebDirCreated = $true }
    # The paths are written into the scripts as single-quoted strings, so an apostrophe in a path is doubled.
    $quoted = { param([string]$text) $text.Replace("'", "''") }
    $startText = @'
# Starts the Garden Agent browser UI with no console window, then opens it. Started by a shortcut.
$port = __PORT__
$url = "http://127.0.0.1:$port/"
$webDir = '__WEBDIR__'
$launcher = '__LAUNCHER__'
# The shortcut runs the home it was installed into, not whatever home the person's environment names.
$env:GARDEN_HOME = '__GARDENFOLDER__'
function Test-Listening {
    try { $client = New-Object System.Net.Sockets.TcpClient; $client.Connect('127.0.0.1', $port); $client.Close(); return $true } catch { return $false }
}
if (-not (Test-Listening)) {
    $proc = Start-Process -FilePath $launcher -ArgumentList @('dsh', 'web', '--port', "$port", '--no-open') `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $webDir 'web.log') -RedirectStandardError (Join-Path $webDir 'web.err')
    Set-Content -Path (Join-Path $webDir 'web.pid') -Value $proc.Id
    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-Listening) -and -not $proc.HasExited -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-Listening)) { exit 1 }
}
# dsh web prints its address with the access token in it, and the page needs that token. The last line
# of its log is the address of the server that is listening now.
$address = $url
$printed = Select-String -Path (Join-Path $webDir 'web.log') -Pattern '^dsh web: (http://\S+)' -ErrorAction SilentlyContinue | Select-Object -Last 1
if ($printed) { $address = $printed.Matches[0].Groups[1].Value }
Start-Process $address
'@
    $stopText = @'
# Stops the Garden Agent browser UI. Started by the "Stop Garden Agent" shortcut.
$port = __PORT__
$webDir = '__WEBDIR__'
$pidFile = Join-Path $webDir 'web.pid'
if (Test-Path $pidFile) {
    $started = Get-Content -Path $pidFile -ErrorAction SilentlyContinue
    if ($started) { & taskkill.exe /PID $started /T /F *> $null }
    Move-Item -Path $pidFile -Destination "$pidFile.stopped" -Force -ErrorAction SilentlyContinue
}
# A server started by hand is found by the port it listens on.
Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { & taskkill.exe /PID $_.OwningProcess /T /F *> $null }
'@
    $startText = $startText.Replace('__PORT__', "$webPort").Replace('__WEBDIR__', (& $quoted $webDir)).Replace('__LAUNCHER__', (& $quoted $launcher)).Replace('__GARDENFOLDER__', (& $quoted $gardenFolder))
    $stopText = $stopText.Replace('__PORT__', "$webPort").Replace('__WEBDIR__', (& $quoted $webDir))
    $startScript = Join-Path $webDir 'start-web.ps1'
    $stopScript = Join-Path $webDir 'stop-web.ps1'
    if ((Test-Path -LiteralPath $startScript) -and -not (Test-ManifestOwnsPath $startScript)) { return }
    if ((Test-Path -LiteralPath $stopScript) -and -not (Test-ManifestOwnsPath $stopScript)) { return }
    [System.IO.File]::WriteAllText($startScript, $startText, $utf8)
    [System.IO.File]::WriteAllText($stopScript, $stopText, $utf8)
    $script:WebShortcutFiles += @($startScript, $stopScript)

    # A shortcut that starts PowerShell directly leaves PowerShell's own console window visible, so the
    # shortcuts start wscript, which runs PowerShell in a hidden window (style 0) with a small script.
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $vbsTemplate = @'
' Runs a Garden Agent script with no console window. Started by a Garden Agent shortcut.
CreateObject("WScript.Shell").Run """__PS__"" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""__SCRIPT__""", 0, False
'@
    $startVbs = Join-Path $webDir 'start-web.vbs'
    $stopVbs = Join-Path $webDir 'stop-web.vbs'
    if ((Test-Path -LiteralPath $startVbs) -and -not (Test-ManifestOwnsPath $startVbs)) { return }
    if ((Test-Path -LiteralPath $stopVbs) -and -not (Test-ManifestOwnsPath $stopVbs)) { return }
    [System.IO.File]::WriteAllText($startVbs, $vbsTemplate.Replace('__PS__', $powershell).Replace('__SCRIPT__', $startScript), [System.Text.Encoding]::ASCII)
    [System.IO.File]::WriteAllText($stopVbs, $vbsTemplate.Replace('__PS__', $powershell).Replace('__SCRIPT__', $stopScript), [System.Text.Encoding]::ASCII)
    $script:WebShortcutFiles += @($startVbs, $stopVbs)
    $programs = Join-Path ([Environment]::GetFolderPath('Programs')) 'Garden Agent'
    $desktop = [Environment]::GetFolderPath('Desktop')
    if (Test-Path -LiteralPath $programs) {
        if (-not (Test-ManifestOwnsPath $programs)) {
            Write-Warning "Preserving the existing, unrecorded shortcuts folder at $programs."
            $programs = $null
        }
    } else {
        New-Item -ItemType Directory -Force -Path $programs | Out-Null
        $script:ProgramShortcutDirCreated = $true
        $script:ProgramShortcutDir = $programs
    }
    $shell = New-Object -ComObject WScript.Shell
    $shortcuts = @(
        @{ Path = (Join-Path $desktop 'Garden Agent.lnk'); Script = $startVbs; Text = 'Start the Garden Agent browser UI' }
    )
    if ($programs) {
        $shortcuts += @(
            @{ Path = (Join-Path $programs 'Garden Agent.lnk'); Script = $startVbs; Text = 'Start the Garden Agent browser UI' },
            @{ Path = (Join-Path $programs 'Stop Garden Agent.lnk'); Script = $stopVbs; Text = 'Stop the Garden Agent browser UI' }
        )
    }
    foreach ($item in $shortcuts) {
        if ((Test-Path -LiteralPath $item.Path) -and -not (Test-ManifestOwnsPath $item.Path)) {
            Write-Warning "Preserving the existing, unrecorded shortcut at $($item.Path)."
            continue
        }
        $link = $shell.CreateShortcut($item.Path)
        $link.TargetPath = $wscript
        $link.Arguments = "//B //Nologo `"$($item.Script)`""
        $link.WorkingDirectory = $webDir
        $link.WindowStyle = 7
        $link.Description = $item.Text
        $link.Save()
        $script:WebShortcutFiles += $item.Path
    }
}

# Signs the person in with the CLI. Its output is the sign-in link with one line on what to do, not the
# CLI's own messages. The browser opens quietly. The test mode writes the link to GARDEN_LOGIN_LINK_FILE
# for a browser to approve, or opens it here when no file is given.
function Invoke-SignIn([string]$python, [string]$pyz) {
    $log = Join-Path $tmp 'login.log'
    $login = Start-Process -FilePath $python -ArgumentList ('"{0}" login --no-open' -f $pyz) `
        -RedirectStandardOutput $log -RedirectStandardError (Join-Path $tmp 'login.err') `
        -NoNewWindow -PassThru
    # PowerShell 5.1 keeps the exit code only if the process handle was read before the wait.
    $null = $login.Handle
    $link = $null
    for ($i = 0; $i -lt 100 -and -not $link; $i++) {
        Start-Sleep -Milliseconds 100
        if (Test-Path $log) {
            $line = Get-Content -Path $log -ErrorAction SilentlyContinue | Where-Object { $_ -match '^  http' } | Select-Object -First 1
            if ($line) { $link = $line.Trim() }
        }
    }
    if (-not $link) { $login.Kill(); throw 'the login link did not appear' }
    if ($env:GARDEN_LOGIN_LINK_FILE) {
        [System.IO.File]::WriteAllText($env:GARDEN_LOGIN_LINK_FILE, "$link`r`n", $utf8)
        Write-Output "The login link is in $($env:GARDEN_LOGIN_LINK_FILE). Open it to approve the login."
    } else {
        Write-Output "Approve the sign-in in your browser: $link"
        if ($env:GARDEN_INSTALL_TEST) {
            Invoke-WebRequest -UseBasicParsing -Uri $link | Out-Null
        } else {
            try { Start-Process $link } catch { }
        }
    }
    $login.WaitForExit()
    if ($login.ExitCode -ne 0) { throw 'garden login exited with an error' }
}

$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
if (-not (Test-Path $tar)) { $tar = 'tar' }

try {
    # The stages this run takes, for the [n/total] prefix: eight common ones, plus the Python stages when
    # GARDEN_PYTHON is unset (two more of them when uv has to be fetched). Keep this in step with Start-Step.
    $script:TotalSteps = 8
    if (-not $env:GARDEN_PYTHON) {
        $script:TotalSteps += 1
        if (-not (Test-Path (Join-Path (Join-Path $gardenHome "tools\uv-$uvVersion") 'uv.exe'))) { $script:TotalSteps += 2 }
    }

    $needNodeDownload = $false
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeCmd) {
        $nodeDir = Join-Path (Join-Path $gardenHome "tools\node-v$nodeVersion") "node-v$nodeVersion-win-x64"
        if (-not (Test-Path (Join-Path $nodeDir 'node.exe'))) { $needNodeDownload = $true }
    } else {
        try {
            $verOutput = (& $nodeCmd.Source -v).Trim().TrimStart('v')
            $major = [int]($verOutput.Split('.')[0])
            if ($major -lt 24) {
                $nodeDir = Join-Path (Join-Path $gardenHome "tools\node-v$nodeVersion") "node-v$nodeVersion-win-x64"
                if (-not (Test-Path (Join-Path $nodeDir 'node.exe'))) { $needNodeDownload = $true }
            }
        } catch { }
    }
    if ($needNodeDownload) { $script:TotalSteps += 2 }

    Ensure-Node

    $python = Get-Python
    if (-not (Get-Command $python -ErrorAction SilentlyContinue)) { throw "Python 3.12 could not be found at $python. Set GARDEN_PYTHON, or run the installer again." }
    $versionCheck = Invoke-Program $python @('-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)')
    if ($versionCheck.Code -ne 0) { throw "Python 3.12 or newer is needed; $python is older." }

    # The manifest lists each artifact with its URL and SHA-256. It needs no sign-in.
    $manifestPath = Join-Path $tmp 'manifest.json'
    Start-Step 'Fetching download manifest...'
    Invoke-WebRequest -UseBasicParsing -Uri "$portal/api/agent/manifest" -OutFile $manifestPath
    Complete-Step
    $manifest = Get-Content -Raw -Path $manifestPath | ConvertFrom-Json

    # Downloads one artifact into $tmp as one stage, and checks it against the manifest. Nothing is installed here.
    function Get-Artifact([string]$name) {
        $entry = $manifest.artifacts.PSObject.Properties[$name]
        if (-not $entry) { throw "The portal does not offer $name. Ask your administrator for an installer that includes it." }
        $artifact = $entry.Value
        $source = Join-Path $tmp $name
        $size = [long]$artifact.size_bytes
        Start-Step "Downloading $name ($(Format-Size $size))..."
        Receive-File ($portal + $artifact.url) $source "Downloading $name" $size
        if ((Get-Sha256 $source) -ne $artifact.sha256.ToLowerInvariant()) {
            throw "$name does not match the portal's SHA-256. Nothing was installed."
        }
        Complete-Step
        return $source
    }

    New-Item -ItemType Directory -Force -Path $gardenHome, $binDir, $dshHome | Out-Null
    $pyz = Join-Path $gardenHome 'garden.pyz'
    $gardenFile = Get-Artifact 'garden.pyz'
    Start-Step 'Installing garden CLI...'
    Copy-Item -Path $gardenFile -Destination $pyz -Force
    Complete-Step

    # The launcher runs the zipapp with the interpreter checked above. A batch file passes the exit code on.
    $launcher = Join-Path $binDir 'garden.cmd'
    $nodeRuntime = (Get-Command node).Source | Split-Path
    $launcherText = "@echo off`r`nset `"PATH=$nodeRuntime;%PATH%`"`r`n`"$python`" `"$pyz`" %*`r`nexit /b %ERRORLEVEL%`r`n"
    [System.IO.File]::WriteAllText($launcher, $launcherText, [System.Text.Encoding]::ASCII)

    # config.json holds where the portal is, the MCP server's command and arguments, and dsh's home. No secret.
    # Node cannot start a .cmd file without a shell (spawn fails with EINVAL), so dsh starts the MCP
    # server with the portable Python and the zipapp, not with garden.cmd.
    $config = @{ portal_url = $portal; garden_command = $python; garden_args = @($pyz, 'mcp'); dsh_home = $dshHome } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText((Join-Path $gardenHome 'config.json'), $config, $utf8)

    $env:GARDEN_HOME = $gardenHome
    $env:GARDEN_API_URL = $portal

    Start-Step 'Installing garden skills...'
    $skills = Invoke-Program $launcher @('skills', 'install', '--to', (Join-Path $gardenHome 'skills'))
    if ($skills.Code -ne 0) { throw 'Installing the skills failed.' }
    Complete-Step

    # dsh is unpacked from the portal's Windows bundle, which carries its own node_modules. The bundle is a
    # fresh copy each time, so an older dsh never mixes with a newer one.
    $bundle = Get-Artifact 'dsh-bundle-windows-x64.tar.gz'
    $dshFolder = Join-Path $gardenHome 'dsh'
    Start-Step 'Unpacking dsh...'
    if (Test-Path -LiteralPath $dshFolder) {
        $existingDsh = Get-Item -LiteralPath $dshFolder -Force
        if (($existingDsh.Attributes -band [IO.FileAttributes]::ReparsePoint) -or -not (Test-ManifestOwnsPath $dshFolder)) {
            throw "Refusing to replace the existing unrecorded dsh folder at $dshFolder."
        }
        $dshBackup = "$dshFolder.bak-$stamp-$([Guid]::NewGuid())"
        Move-Item -LiteralPath $dshFolder -Destination $dshBackup
        Record-InstallArtifact $dshBackup 'tree' $gardenHome
    }
    New-Item -ItemType Directory -Path $dshFolder | Out-Null
    $script:DshFolderCreated = $true
    & $tar -xzf $bundle -C $dshFolder
    if ($LASTEXITCODE -ne 0) { throw 'dsh could not be unpacked from the portal''s bundle.' }
    Complete-Step

    # dsh's launcher is written by garden: it runs `garden dsh`, which reads the login at run time.
    $dshLauncher = Join-Path $binDir 'dsh.cmd'
    $launcherWrite = Invoke-Program $launcher @('dsh-launcher', '--to', $dshLauncher, '--garden', $launcher)
    if ($launcherWrite.Code -ne 0) { throw 'Writing the dsh launcher failed.' }

    # dsh reads its home patch on start. An existing file is kept as a dated backup.
    $patch = Join-Path $dshHome 'cordis.patch.yml'
    if (Test-Path $patch) { Move-Item -Path $patch -Destination "$patch.bak-$stamp" }
    # The template is installed beside garden; garden writes the patch once it is signed in (below).
    $configFile = Get-Artifact 'dsh-config.yml'
    Start-Step 'Installing dsh configuration template...'
    Copy-Item -Path $configFile -Destination (Join-Path $gardenHome 'dsh-config.yml') -Force
    Complete-Step

    if (-not $env:GARDEN_INSTALL_TEST) { Add-UserPath $binDir }

    # The browser UI's shortcuts need no sign-in, so they are made before it.
    Install-WebShortcuts $launcher $gardenHome

    # Sign in last: garden, dsh, the skills and the patch are installed before it, so a failed sign-in
    # leaves them in place, and the error names the one command to retry.
    try {
        Invoke-SignIn $python $pyz
        $script:LoginSucceeded = $true
    } catch {
        throw "Sign-in did not finish ($($_.Exception.Message)). Everything else is installed. Run garden login to sign in."
    }
    # The patch names the portal's default model, which needs the sign-in, so garden writes it now.
    $patchWrite = Invoke-Program $launcher @('dsh-config')
    if ($patchWrite.Code -ne 0) { throw 'Writing the dsh patch failed. Run garden dsh-config for details.' }
    $script:DshPatchWritten = $true

    # A smoke check: the signed-in person can list their tools.
    $tools = Invoke-Program $launcher @('tools', '--json')
    if ($tools.Code -ne 0) { throw 'The smoke check failed. Run garden whoami for details.' }
    $listed = ($tools.Output -join [Environment]::NewLine) | ConvertFrom-Json
    $toolCount = @($listed).Count
    if ($toolCount -gt 0) {
        Write-Output "Ready: $toolCount tools are available. Run garden tools to see them."
    } else {
        Write-Warning 'No tools are listed for you yet. Ask the person who runs Garden to give you a permission, then run garden tools again.'
    }

    Write-Output "garden is installed at $launcher. Skills are in $(Join-Path $gardenHome 'skills')."
    Write-Output "dsh is installed at $dshLauncher. It uses the portal's model with your sign-in."
    if (Get-Command node -ErrorAction SilentlyContinue) {
        Write-Output 'Run dsh to start it. Your sign-in is read when it starts, and is never written to dsh''s config.'
    } else {
        Write-Warning 'Node.js is not installed. dsh needs it: install Node.js, then run dsh.'
    }
} catch {
    if ($script:CurrentStep) { Write-Host "[$($script:StepNumber)/$($script:TotalSteps)] Failed: $($script:CurrentStep)" }
    throw
} finally {
    try { Write-InstallManifest } catch { Write-Warning "Could not update the Garden install manifest: $($_.Exception.Message)" }
    # Keep temporary downloads recoverable rather than permanently deleting them.
    if (Get-Command trash -ErrorAction SilentlyContinue) { & trash $tmp }
    $env:GARDEN_HOME = $savedGardenHome
    $env:GARDEN_API_URL = $savedPortalUrl
    $ErrorActionPreference = $savedErrorActionPreference
    $ProgressPreference = $savedProgressPreference
}
