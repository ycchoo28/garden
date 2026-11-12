# Run from the clone: Get-Content .\scripts\bootstrap.ps1 -Raw | Invoke-Expression
# Resolve the helper directory for both file invocation and the policy-independent inline form.
$bootstrapRoot = $PSScriptRoot
if (-not $bootstrapRoot -or -not (Test-Path (Join-Path $bootstrapRoot 'bootstrap.py'))) {
    $bootstrapRoot = Join-Path (Get-Location) 'scripts'
}
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$initialPath = $env:PATH
$savedBootstrapToolsCreated = $env:GARDEN_BOOTSTRAP_TOOLS_CREATED
$manifestRoot = Join-Path $env:USERPROFILE '.local\share\garden'
$manifestPath = Join-Path $manifestRoot 'install-manifest.json'
New-Item -ItemType Directory -Force $manifestRoot | Out-Null
if (Test-Path -LiteralPath $manifestPath) {
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.version -ne 1) { throw 'Unsupported Garden install manifest version.' }
    foreach ($prop in @('artifacts', 'path_entries', 'demo_projects', 'docker_images', 'logins')) {
        if (-not (Get-Member -InputObject $manifest -Name $prop)) {
            $manifest | Add-Member -MemberType NoteProperty -Name $prop -Value @()
        }
    }
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
function Save-Manifest {
    $temporary = "$manifestPath.tmp"
    $json = ConvertTo-Json -InputObject $manifest -Depth 20
    [System.IO.File]::WriteAllText($temporary, $json, (New-Object System.Text.UTF8Encoding $false))
    Move-Item -LiteralPath $temporary -Destination $manifestPath -Force
}
function Record-Artifact([string]$path, [string]$kind, [string]$root) {
    $full = [System.IO.Path]::GetFullPath($path)
    $entry = [pscustomobject]@{
        path = $full
        resolved_path = $full
        root = [System.IO.Path]::GetFullPath($root)
        kind = $kind
        owner = 'bootstrap'
    }
    if ($kind -eq 'file') {
        $entry | Add-Member -NotePropertyName sha256 -NotePropertyValue (Get-FileHash -Algorithm SHA256 -LiteralPath $full).Hash.ToLowerInvariant()
    }
    $items = @($manifest.artifacts)
    $index = -1
    for ($i = 0; $i -lt $items.Count; $i++) {
        if ([string]::Equals($items[$i].path, $full, [StringComparison]::OrdinalIgnoreCase)) { $index = $i; break }
    }
    if ($index -ge 0) { $items[$index] = $entry } else { $items += $entry }
    $manifest.artifacts = @($items)
    Save-Manifest
}
function Record-UserPath([string]$value, [bool]$createdValue = $false) {
    $entries = @($manifest.path_entries)
    foreach ($entry in $entries) {
        if ($entry.scope -eq 'windows-user' -and [string]::Equals($entry.value.TrimEnd('\'), $value.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) { return }
    }
    $manifest.path_entries = @($entries + [pscustomobject]@{ value = $value; scope = 'windows-user'; owner = 'bootstrap'; created_value = $createdValue })
    Save-Manifest
}
$bin = Join-Path $env:USERPROFILE '.local\bin'
$binExisted = Test-Path -LiteralPath $bin
New-Item -ItemType Directory -Force $bin | Out-Null
$env:PATH = "$bin;$env:PATH"
if (-not $binExisted) { Record-Artifact $bin 'directory' (Split-Path $bin) }
function Add-BootstrapPath([string]$folder) {
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    $userEntries = @($current -split ';' | Where-Object { $_ })
    $hasUserEntry = @($userEntries | Where-Object { [string]::Equals($_.TrimEnd('\'), $folder.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) }).Count -gt 0
    $processEntries = @($initialPath -split ';' | Where-Object { $_ })
    $hasProcessEntry = @($processEntries | Where-Object { [string]::Equals($_.TrimEnd('\'), $folder.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) }).Count -gt 0
    if ($env:GARDEN_BOOTSTRAP_NO_PERSIST -ne '1' -and -not $hasUserEntry -and -not $hasProcessEntry) {
        [Environment]::SetEnvironmentVariable('Path', $(if ($current) { "$folder;$current" } else { $folder }), 'User')
        Record-UserPath $folder (-not [bool]$current)
    }
    $env:PATH = "$folder;$env:PATH"
}
function Get-CheckedFile([string]$url, [string]$path, [string]$sha) {
    (New-Object Net.WebClient).DownloadFile($url, $path)
    if ((Get-FileHash -Algorithm SHA256 $path).Hash.ToLowerInvariant() -ne $sha) { throw "Checksum mismatch: $url" }
}
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('garden-bootstrap-' + [Guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if (-not (Test-Path -LiteralPath "$bin\uv.exe")) {
        Get-CheckedFile 'https://github.com/astral-sh/uv/releases/download/0.13.1/uv-x86_64-pc-windows-msvc.zip' "$tmp\uv.zip" '3b2661a463a5baea7f5076ea2b25ab71d77f36a6c0d809e5350651792f80a672'
        Expand-Archive "$tmp\uv.zip" "$tmp\uv"
        Copy-Item "$tmp\uv\uv.exe" "$bin\uv.exe"
        Record-Artifact "$bin\uv.exe" 'file' $bin
    }
}
Add-BootstrapPath $bin
$toolsDir = Join-Path $env:USERPROFILE '.local\share\garden-tools'
$toolsDirExisted = Test-Path -LiteralPath $toolsDir
$savedBootstrapToolsCreated = $env:GARDEN_BOOTSTRAP_TOOLS_CREATED
if (-not $toolsDirExisted) { $env:GARDEN_BOOTSTRAP_TOOLS_CREATED = '1' }
$gitHome = Join-Path $toolsDir 'git'
$gitBash = Join-Path $gitHome 'bin\bash.exe'
$gitComplete = Join-Path $gitHome '.complete'
$gitCompleteInManifest = @($manifest.artifacts | Where-Object {
    ([string]::Equals($_.path, $gitHome, [StringComparison]::OrdinalIgnoreCase) -and $_.complete -eq $true) -or
    [string]::Equals($_.path, $gitComplete, [StringComparison]::OrdinalIgnoreCase)
}).Count -gt 0
$bash = @("$env:ProgramFiles\Git\bin\bash.exe", "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $bash -and (Test-Path -LiteralPath $gitBash) -and (Test-Path -LiteralPath $gitComplete) -and $gitCompleteInManifest) {
    $bash = $gitBash
}
if (-not $bash) {
    $gitExisted = Test-Path -LiteralPath $gitHome
    if ($gitExisted -and -not (@($manifest.artifacts | Where-Object { $_.path -ieq $gitHome -and $_.kind -eq 'tree' }).Count)) {
        throw "Refusing to replace the unmanaged PortableGit folder $gitHome"
    }
    if (-not $gitExisted) {
        New-Item -ItemType Directory -Path $gitHome -Force | Out-Null
        Record-Artifact $gitHome 'tree' (Split-Path $gitHome)
    }
    Get-CheckedFile 'https://github.com/git-for-windows/git/releases/download/v2.56.1.windows.1/PortableGit-2.56.1-64-bit.7z.exe' "$tmp\git.exe" '9dd04a608ceb5f6ab32a73709e0ecb159d211da71b697c45ff81f6dd0c6b49c7'
    & "$tmp\git.exe" -y "-o$gitHome"
    if ($LASTEXITCODE -ne 0) { throw 'Portable Git extraction failed' }
    Set-Content -LiteralPath $gitComplete -Value 'complete'
    Record-Artifact $gitComplete 'file' $gitHome
    $gitEntry = @($manifest.artifacts | Where-Object { [string]::Equals($_.path, $gitHome, [StringComparison]::OrdinalIgnoreCase) }) | Select-Object -First 1
    if ($gitEntry) {
        $gitEntry | Add-Member -NotePropertyName complete -NotePropertyValue $true -Force
        Save-Manifest
    }
    $bash = $gitBash
}
Add-BootstrapPath (Split-Path $bash)
Add-BootstrapPath (Join-Path (Split-Path (Split-Path $bash)) 'usr\bin')
$savedUvPythonInstallDir = $env:UV_PYTHON_INSTALL_DIR
$env:UV_PYTHON_INSTALL_DIR = Join-Path $toolsDir 'python'
try {
    & uv run --no-project --python 3.12 "$bootstrapRoot\bootstrap.py"
    if ($LASTEXITCODE -ne 0) { throw 'Toolchain bootstrap failed' }
} finally {
    if ($savedUvPythonInstallDir) { $env:UV_PYTHON_INSTALL_DIR = $savedUvPythonInstallDir }
    else { Remove-Item Env:UV_PYTHON_INSTALL_DIR -ErrorAction SilentlyContinue }
    if ($savedBootstrapToolsCreated) { $env:GARDEN_BOOTSTRAP_TOOLS_CREATED = $savedBootstrapToolsCreated }
    else { Remove-Item Env:GARDEN_BOOTSTRAP_TOOLS_CREATED -ErrorAction SilentlyContinue }
}
$pathsFile = Join-Path $toolsDir 'paths'
if (Test-Path -LiteralPath $pathsFile) {
    Get-Content $pathsFile | ForEach-Object { $env:PATH = "$_;$env:PATH" }
}
