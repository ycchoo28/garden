# Run from the clone: Get-Content .\scripts\bootstrap.ps1 -Raw | Invoke-Expression
# Resolve the helper directory for both file invocation and the policy-independent inline form.
$bootstrapRoot = $PSScriptRoot
if (-not $bootstrapRoot -or -not (Test-Path (Join-Path $bootstrapRoot 'bootstrap.py'))) {
    $bootstrapRoot = Join-Path (Get-Location) 'scripts'
}
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$bin = Join-Path $env:USERPROFILE '.local\bin'
New-Item -ItemType Directory -Force $bin | Out-Null
$env:PATH = "$bin;$env:PATH"
function Add-BootstrapPath([string]$folder) {
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($env:GARDEN_BOOTSTRAP_NO_PERSIST -ne '1' -and -not (($current -split ';') -contains $folder)) {
        [Environment]::SetEnvironmentVariable('Path', "$folder;$current", 'User')
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
    Get-CheckedFile 'https://github.com/astral-sh/uv/releases/download/0.13.1/uv-x86_64-pc-windows-msvc.zip' "$tmp\uv.zip" '3b2661a463a5baea7f5076ea2b25ab71d77f36a6c0d809e5350651792f80a672'
    Expand-Archive "$tmp\uv.zip" "$tmp\uv"
    Copy-Item "$tmp\uv\uv.exe" "$bin\uv.exe"
}
Add-BootstrapPath $bin
$bash = @("$env:ProgramFiles\Git\bin\bash.exe", "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe", "$env:USERPROFILE\.local\share\garden-tools\git\bin\bash.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $bash) {
    $gitHome = Join-Path $env:USERPROFILE '.local\share\garden-tools\git'
    Get-CheckedFile 'https://github.com/git-for-windows/git/releases/download/v2.56.1.windows.1/PortableGit-2.56.1-64-bit.7z.exe' "$tmp\git.exe" '9dd04a608ceb5f6ab32a73709e0ecb159d211da71b697c45ff81f6dd0c6b49c7'
    & "$tmp\git.exe" -y "-o$gitHome"
    if ($LASTEXITCODE -ne 0) { throw 'Portable Git extraction failed' }
    $bash = "$gitHome\bin\bash.exe"
}
Add-BootstrapPath (Split-Path $bash)
Add-BootstrapPath (Join-Path (Split-Path (Split-Path $bash)) 'usr\bin')
& uv run --no-project --python 3.12 "$bootstrapRoot\bootstrap.py"
if ($LASTEXITCODE -ne 0) { throw 'Toolchain bootstrap failed' }
Get-Content (Join-Path $env:USERPROFILE '.local\share\garden-tools\paths') | ForEach-Object { $env:PATH = "$_;$env:PATH" }
