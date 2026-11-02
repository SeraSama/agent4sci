# 一键构建 Windows 安装包（x64）：后端（PyInstaller）+ uv + Electron → desktop\release\Agent4Sci-Setup-<版本>.exe
#
# 在 PowerShell 中于仓库根目录运行：
#   powershell -ExecutionPolicy Bypass -File packaging\build-win.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\build-win.ps1 -SkipTests
#
# 缺少 uv 或 Node.js 时会询问是否自动安装（uv 用官方安装脚本，Node.js 用 winget）。首次构建需要联网。
param(
    [switch]$SkipTests,
    [switch]$Yes  # 自动同意安装缺少的工具
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $Root
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Has($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Confirm-Install($what) {
    if ($Yes) { return $true }
    $answer = Read-Host "缺少 $what，现在自动安装吗？[Y/n]"
    return ($answer -eq '' -or $answer -match '^[Yy]')
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Run($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "命令失败（退出码 $LASTEXITCODE）：$exe $($arguments -join ' ')" }
}

# ---- 前置工具 -------------------------------------------------------------------------------
if (-not (Has 'uv')) {
    if (Confirm-Install 'uv（Python 包管理器）') {
        Step '安装 uv'
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        Refresh-Path
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    }
    if (-not (Has 'uv')) { throw '需要 uv：https://docs.astral.sh/uv/getting-started/installation/' }
}
if (-not (Has 'node') -or -not (Has 'npm')) {
    if ((Has 'winget') -and (Confirm-Install 'Node.js LTS')) {
        Step '安装 Node.js LTS（winget）'
        winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
        Refresh-Path
    }
    if (-not (Has 'node')) { throw '需要 Node.js 20 或更新版本：https://nodejs.org/' }
}
$nodeMajor = [int]((node --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 20) { throw "Node.js 版本过低（$(node --version)），需要 20 或更新版本" }
if (-not (Has 'git')) { Write-Host '提示：未找到 git。构建不需要它，但应用里的工作区版本记录需要 git（https://git-scm.com/）。' -ForegroundColor Yellow }

# ---- 后端 -------------------------------------------------------------------------------------
Step 'Python 依赖（uv sync）'
Run uv @('sync', '--group', 'build')

if (-not $SkipTests) {
    Step '后端测试'
    & uv run pytest -q
    if ($LASTEXITCODE -ne 0) {
        Write-Warning '后端测试没有全部通过（见上方输出）。继续构建；如果安装包运行异常，请把测试输出反馈给开发者。'
    }
}

if (-not (Test-Path 'desktop\build\icon.ico')) {
    Step '生成图标'
    Run uv @('run', 'python', 'packaging\make_icons.py')
}

Step '打包后端（PyInstaller）'
if (Test-Path 'build\pyinstaller') { Remove-Item -Recurse -Force 'build\pyinstaller' }
Run uv @('run', 'pyinstaller', 'packaging\backend.spec', '--noconfirm', '--log-level', 'WARN',
         '--distpath', 'build\pyinstaller\dist', '--workpath', 'build\pyinstaller\work')

Step '随包附带 uv'
Run uv @('run', 'python', 'packaging\fetch_uv.py', '--target', 'win-x64')

Step '冒烟测试：后端能否启动'
$backend = Join-Path $Root 'build\pyinstaller\dist\backend\agent4sci-server.exe'
$tmp = Join-Path $env:TEMP ("a4s-smoke-" + [guid]::NewGuid())
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $backend
$psi.Arguments = '--port 0 --demo --exit-on-stdin-close'
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardInput = $true
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.EnvironmentVariables['A4S_DATA_DIR'] = $tmp
$psi.EnvironmentVariables['A4S_SECRETS'] = 'memory'
$proc = [System.Diagnostics.Process]::Start($psi)
$ready = $proc.StandardOutput.ReadLine()
$proc.StandardInput.Close()
if (-not $proc.WaitForExit(15000)) { $proc.Kill() }
if ($ready -notmatch '"ready"') { throw "后端启动失败：$ready" }
Write-Host "后端就绪：$ready"

# ---- 桌面应用 -------------------------------------------------------------------------------
Step '前端依赖（npm ci）'
Push-Location desktop
try {
    Run npm @('ci', '--no-audit', '--no-fund')
    if (-not $SkipTests) {
        Run npm @('run', 'typecheck')
        Run npm @('test')
    }
    Step '构建 Electron 应用与 NSIS 安装包'
    Run npm @('run', 'build')
    if (Test-Path 'release') { Remove-Item -Recurse -Force 'release' }
    # A real code-signing certificate (CSC_LINK / WIN_CSC_LINK) is used when present; without one the
    # installer is unsigned and SmartScreen warns the people you hand it to.
    if (-not $env:CSC_LINK -and -not $env:WIN_CSC_LINK) { $env:CSC_IDENTITY_AUTO_DISCOVERY = 'false' }
    Run npx @('electron-builder', '--win', 'nsis', '--x64', '--publish', 'never')
}
finally {
    Pop-Location
}

Step '完成'
Get-ChildItem 'desktop\release\*.exe' | ForEach-Object { Write-Host ("{0}  ({1:N1} MB)" -f $_.FullName, ($_.Length / 1MB)) }
Write-Host "`n未签名的安装包首次运行时，Windows SmartScreen 可能提示“已保护你的电脑”：点“更多信息”→“仍要运行”。"
