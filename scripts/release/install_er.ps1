param(
    [string]$Package = $PSScriptRoot,
    [string]$ErHome = (Join-Path $env:USERPROFILE '.ercode'),
    [string]$BinDir = (Join-Path $env:USERPROFILE '.local\bin')
)
$ErrorActionPreference = 'Stop'
$ErHome = [IO.Path]::GetFullPath($ErHome)
$BinDir = [IO.Path]::GetFullPath($BinDir)
$Package = (Resolve-Path -LiteralPath $Package).Path
$Metadata = Get-Content -Raw -LiteralPath (Join-Path $Package 'codex-package.json') | ConvertFrom-Json
if ($Metadata.entrypoint -ne 'bin/er.exe') { throw 'Expected a Windows er package.' }
foreach ($Relative in @('bin/er.exe', 'bin/codex-code-mode-host.exe', 'bin/codex-exec-server.exe', 'codex-path/rg.exe', 'codex-resources/codex-command-runner.exe', 'codex-resources/codex-windows-sandbox-setup.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Package $Relative) -PathType Leaf)) { throw "Missing package file: $Relative" }
}
$Marker = ':: ercode private package launcher'
$Command = Join-Path $BinDir 'er.cmd'
if (Test-Path -LiteralPath $Command) {
    if (-not (Test-Path -LiteralPath $Command -PathType Leaf)) { throw "Refusing to replace $Command" }
    if ((Get-Content -LiteralPath $Command -TotalCount 1) -ne $Marker) { throw "Refusing to overwrite an unrelated command: $Command" }
}
$ReleaseRoot = Join-Path $ErHome 'packages\standalone\releases'
$Destination = Join-Path $ReleaseRoot ('er-' + [guid]::NewGuid().ToString('N'))
$TemporaryLauncher = Join-Path $BinDir ('.er-' + [guid]::NewGuid().ToString('N') + '.cmd')
New-Item -ItemType Directory -Force -Path $Destination, $BinDir | Out-Null
try {
    Get-ChildItem -LiteralPath $Package -Force | Copy-Item -Destination $Destination -Recurse -Force
    $Executable = Join-Path $Destination 'bin\er.exe'
    & $Executable --version
    if ($LASTEXITCODE -ne 0) { throw "Installed er failed to start ($LASTEXITCODE)." }
    $EscapedExecutable = $Executable.Replace('%', '%%')
    $Launcher = @(
        $Marker,
        '@echo off',
        'setlocal DisableDelayedExpansion',
        'for /f "tokens=2 delims=:" %%a in (''chcp'') do set "_ER_OLD_CP=%%a"',
        'chcp 65001 >nul',
        ('"' + $EscapedExecutable + '" %*'),
        'set "_ER_EXIT=%errorlevel%"',
        'chcp %_ER_OLD_CP% >nul',
        'exit /b %_ER_EXIT%'
    ) -join "`r`n"
    [IO.File]::WriteAllText($TemporaryLauncher, $Launcher + "`r`n", [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $TemporaryLauncher -Destination $Command -Force
} catch {
    Remove-Item -LiteralPath $Destination -Recurse -Force -ErrorAction SilentlyContinue
    throw
} finally {
    Remove-Item -LiteralPath $TemporaryLauncher -Force -ErrorAction SilentlyContinue
}
Write-Host "Installed $Command"
Write-Host "Add $BinDir to your user PATH to run er. Existing Codex installations are untouched."
