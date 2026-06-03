# =============================================================================
# Electron 集成 Smoke Test (Windows)
# 验证：NSIS 安装后，在端口被占用的情况下，应用能自动换端口并正常启动
# 流程：占 8765 → 静默安装 NSIS → 启动安装后的应用 → 读 port.json → 健康检查 → 卸载
# 用法：从项目根目录运行，需要先完成 electron-builder --win
# =============================================================================

$ErrorActionPreference = "Stop"

function Write-Ok   { param($msg) Write-Host "✓ $msg" -ForegroundColor Green }
function Write-Fail { param($msg) Write-Host "✗ $msg" -ForegroundColor Red; exit 1 }
function Write-Step { param($msg) Write-Host "→ $msg" -ForegroundColor Yellow }

$ProjectRoot  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AppName      = "牙科设备推荐Agent"
# Electron userData 目录用 package.json 的 name 字段，不是 productName
$AppPkgName   = "dental-recommend-agent"
$OccupiedPort = 8765

# ---------- 定位 NSIS 安装包 ----------
$InstallerExe = Get-ChildItem "$ProjectRoot\release\*Setup*.exe" | Select-Object -First 1
if (-not $InstallerExe) {
    Write-Fail "未找到 NSIS 安装包（请先运行 electron-builder --win）"
}
Write-Ok "找到安装包: $($InstallerExe.FullName)"

# ---------- 准备 ----------
# NSIS 非 oneClick 默认装到 $LOCALAPPDATA\Programs\<productName>
$InstallDir     = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
$InstalledExe   = Join-Path $InstallDir "$AppName.exe"
$UninstallExe   = Join-Path $InstallDir "Uninstall $AppName.exe"
$UserDataDir    = Join-Path $env:APPDATA $AppPkgName
$BackendDataDir = Join-Path $UserDataDir "backend-data"
$PortJson       = Join-Path $BackendDataDir "port.json"

$AppProcess         = $null
$PortBlockerProcess = $null

function Cleanup {
    if ($AppProcess -and -not $AppProcess.HasExited) {
        Stop-Process -Id $AppProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($PortBlockerProcess -and -not $PortBlockerProcess.HasExited) {
        Stop-Process -Id $PortBlockerProcess.Id -Force -ErrorAction SilentlyContinue
    }
    # 静默卸载
    if (Test-Path $UninstallExe) {
        Write-Step "卸载应用 ..."
        Start-Process -FilePath $UninstallExe -ArgumentList "/S" -Wait -WindowStyle Hidden -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    # 清理 port.json
    if (Test-Path $PortJson) {
        Remove-Item -Path $PortJson -Force -ErrorAction SilentlyContinue
    }
}

try {
    # ---------- 1. 占住首选端口 ----------
    Write-Step "占用端口 $OccupiedPort ..."

    $BlockerScript = @"
import socket, sys, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
s.bind(('127.0.0.1', $OccupiedPort))
s.listen(1)
sys.stdout.write('ready\n')
sys.stdout.flush()
time.sleep(300)
"@
    $PortBlockerProcess = Start-Process -FilePath "python" -ArgumentList "-c", $BlockerScript `
        -PassThru -WindowStyle Hidden

    # 等待端口占用者就绪
    $ready = $false
    for ($i = 0; $i -lt 10; $i++) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $tcp.Connect("127.0.0.1", $OccupiedPort)
            $tcp.Close()
            $ready = $true
            break
        } catch {
            Start-Sleep -Milliseconds 300
        }
    }
    if (-not $ready) { Write-Fail "无法确认端口 $OccupiedPort 已被占用" }
    Write-Ok "端口 $OccupiedPort 已被占用 (PID $($PortBlockerProcess.Id))"

    # ---------- 2. 清理旧的 port.json ----------
    if (Test-Path $BackendDataDir) {
        Remove-Item -Path $PortJson -Force -ErrorAction SilentlyContinue
    } else {
        New-Item -ItemType Directory -Path $BackendDataDir -Force | Out-Null
    }

    # ---------- 3. 静默安装 NSIS ----------
    Write-Step "静默安装 NSIS 安装包 ..."

    # 先卸载旧版本（如果存在）
    if (Test-Path $UninstallExe) {
        Start-Process -FilePath $UninstallExe -ArgumentList "/S" -Wait -WindowStyle Hidden -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }

    # 安装
    $installResult = Start-Process -FilePath $InstallerExe.FullName -ArgumentList "/S" -Wait -PassThru -WindowStyle Hidden
    if ($installResult.ExitCode -ne 0) {
        Write-Fail "NSIS 安装失败，exit code: $($installResult.ExitCode)"
    }

    # 等待安装完成
    Start-Sleep -Seconds 3

    if (-not (Test-Path $InstalledExe)) {
        Write-Fail "安装后未找到应用: $InstalledExe"
    }
    Write-Ok "应用已安装到: $InstallDir"

    # ---------- 4. 启动安装后的应用 ----------
    Write-Step "启动安装后的应用 ..."

    $AppProcess = Start-Process -FilePath $InstalledExe -PassThru -WindowStyle Hidden
    Write-Ok "应用已启动 (PID $($AppProcess.Id))"

    # ---------- 5. 等待 port.json ----------
    Write-Step "等待 port.json 出现 (最多 45 秒) ..."

    $found = $false
    for ($i = 0; $i -lt 90; $i++) {
        if (Test-Path $PortJson) { $found = $true; break }
        if ($AppProcess.HasExited) {
            Write-Fail "应用已退出 (exit code $($AppProcess.ExitCode))，port.json 未生成"
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $found) { Write-Fail "port.json 未在 45 秒内出现" }
    Write-Ok "port.json 已生成"

    # ---------- 6. 验证端口已切换 ----------
    $PortInfo   = Get-Content $PortJson -Raw | ConvertFrom-Json
    $ActualPort = $PortInfo.port
    Write-Step "后端实际端口: $ActualPort"

    if ($ActualPort -eq $OccupiedPort) {
        Write-Fail "端口未切换: port.json 仍为 $OccupiedPort，应为其他端口"
    }
    Write-Ok "端口已切换: $OccupiedPort → $ActualPort"

    # ---------- 7. 健康检查 ----------
    $HealthUrl = "http://127.0.0.1:${ActualPort}/api/health"
    Write-Step "健康检查: $HealthUrl ..."

    $passed = $false
    for ($i = 0; $i -lt 10; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                $body = $response.Content | ConvertFrom-Json
                if ($body.status -eq "ok") {
                    $passed = $true
                    break
                }
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    if (-not $passed) { Write-Fail "健康检查失败: $HealthUrl" }
    Write-Ok "健康检查通过: $($response.Content)"

    # ---------- 8. 验证渲染层加载 ----------
    $RendererMarker = Join-Path $BackendDataDir "renderer-ready"
    Write-Step "等待渲染层加载标记 (最多 30 秒) ..."

    $markerFound = $false
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-Path $RendererMarker) { $markerFound = $true; break }
        if ($AppProcess.HasExited) {
            Write-Fail "应用已退出，renderer-ready 未生成"
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $markerFound) { Write-Fail "renderer-ready 未在 30 秒内出现" }
    $markerContent = Get-Content $RendererMarker -Raw
    Write-Ok "渲染层已加载 (marker: $markerContent)"

    # 停止应用，进入卸载流程
    Stop-Process -Id $AppProcess.Id -Force -ErrorAction SilentlyContinue
    $AppProcess = $null
    Start-Sleep -Seconds 2

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host " Electron 集成 smoke test 全部通过" -ForegroundColor Green
    Write-Host "（NSIS 安装 + 端口碰撞 + 后端健康 + 渲染层加载）" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green

} finally {
    Cleanup
}
