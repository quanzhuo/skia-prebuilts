[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Executable,
    [string[]]$Arguments = @(),
    [ValidateSet('x64', 'arm64')][string]$TargetCpu = 'x64'
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$hostCpu = if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq 'Arm64') { 'arm64' } else { 'x64' }
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
if (!(Test-Path -LiteralPath $vswhere)) { throw 'vswhere.exe was not found.' }
$component = if ($TargetCpu -eq 'arm64') { 'Microsoft.VisualStudio.Component.VC.Tools.ARM64' } else { 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64' }
$vsPath = & $vswhere -latest -products '*' -requires $component -property installationPath
if (!$vsPath) { throw 'A Visual Studio C++ installation is required.' }
Import-Module (Join-Path $vsPath 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
Enter-VsDevShell -VsInstallPath $vsPath -SkipAutomaticLocation -DevCmdArguments "-arch=$TargetCpu -host_arch=$hostCpu"
foreach ($name in @('cl.exe', 'link.exe')) {
    $selected = (Get-Command $name -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $prefix = Join-Path $vsPath 'VC/Tools/MSVC/'
    if (!$selected.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
        $selected -notmatch "[\\/]bin[\\/]Host$hostCpu[\\/]$TargetCpu[\\/]") {
        throw "Expected MSVC $name for $TargetCpu, found $selected"
    }
}
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}
