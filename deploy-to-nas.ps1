<#
  deploy-to-nas.ps1 - 从 Windows 本机把 Alpha 一键部署到 NAS（Docker，宿主机端口 923）

  用法（密码登录）：
    powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy-to-nas.ps1 `
        -NasHost 192.168.31.94 -NasUser 江流儿 -NasPort 22 -NasPath /volume1/docker/Alpha `
        -Password '你的密码' -WithDb

  参数：
    -NasHost     NAS 内网 IP 或主机名（必填）
    -NasUser     SSH 用户名（群晖一般是 admin）
    -NasPort     SSH 端口，默认 22
    -NasPath     NAS 上的 Alpha 目录（群晖 docker 常见 /volume1/docker/Alpha）
    -Password    SSH 密码（不给则用已配置的密钥）
    -WriteToken  写操作令牌，不给则自动生成随机串
    -WithDb      连同本机 218MB 的 market_kline.db 一起迁移（省一次 17 分钟全量同步）
    -SkipBuild   只上传代码，不在 NAS 上重新构建镜像
    -UseSudo     docker 命令用 sudo 执行（用户不在 docker 组时需要）

  说明：
    - 群晖 DSM 默认不开放 sftp 子系统，scp 必须加 -O（老协议）才能传文件。
    - 部署目录里会把 docker-compose.nas.yml 复制成 docker-compose.yml，
      方便群晖 Container Manager 的「项目」功能识别。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$NasHost,
    [string]$NasUser = 'admin',
    [int]$NasPort = 22,
    [string]$NasPath = '/volume1/docker/Alpha',
    [string]$Password = '',
    [string]$WriteToken = '',
    [switch]$WithDb,
    [switch]$SkipBuild,
    [switch]$UseSudo
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$hostPort = 923

function Say([string]$m) { Write-Host "[nas] $m" }

# ── 打包（排除无关目录）──────────────────────────────────────
Say "packing source from $root ..."
$tmp = Join-Path $env:TEMP ("alpha-nas-" + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $tmp | Out-Null
$tar = Join-Path $tmp 'alpha.tar.gz'

$excludes = @('--exclude=.venv', '--exclude=.git', '--exclude=data', '--exclude=logs',
              '--exclude=__pycache__', '--exclude=.pytest_cache', '--exclude=.run',
              '--exclude=.cursor', '--exclude=.env', '--exclude=*.pyc')
$tarArgs = @('-czf', $tar) + $excludes + @('-C', $root, '.')
& tar @tarArgs
if ($LASTEXITCODE -ne 0) { throw "tar failed ($LASTEXITCODE)" }
$sizeMb = [math]::Round((Get-Item $tar).Length / 1MB, 1)
Say "package: $tar ($sizeMb MB)"

if (-not $WriteToken) {
    $bytes = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $WriteToken = -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

# ── SSH 密码交互（Windows OpenSSH 走 SSH_ASKPASS）────────────
$askpass = Join-Path $tmp 'askpass.cmd'
if ($Password) {
    "@echo off`r`necho $Password" | Set-Content -Path $askpass -Encoding ASCII
    $env:SSH_ASKPASS = $askpass
    $env:SSH_ASKPASS_REQUIRE = 'force'
    $env:DISPLAY = 'codex'
    $env:GIT_ASKPASS = $askpass
} else {
    Say "no password given, using ssh key"
}

$sshArgs = @('-p', "$NasPort", '-o', 'StrictHostKeyChecking=accept-new',
             '-o', 'ServerAliveInterval=15', '-o', 'ConnectTimeout=15')
$target = "$NasUser@$NasHost"
# -O: 群晖没有 sftp 子系统，必须用老 scp 协议
$scpArgs = @('-O', '-P', "$NasPort", '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=15')

function Invoke-Ssh([string]$cmd, [switch]$AllowFail) {
    & ssh @sshArgs $target $cmd
    if ($LASTEXITCODE -ne 0 -and -not $AllowFail) { throw "ssh command failed ($LASTEXITCODE): $cmd" }
}

# docker 命令前缀：需要 sudo 时把密码喂给 sudo -S
$dockerCmd = 'docker'
if ($UseSudo) {
    $pw64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("$Password`n"))
    $dockerCmd = "echo $pw64 | base64 -d | sudo -S -p '' docker"
}

try {
    # ── 连通性与环境探测 ────────────────────────────────────
    Say "probing $target ..."
    Invoke-Ssh "uname -m; id -un; docker --version; (docker compose version || docker-compose --version)"

    Say "creating directory $NasPath ..."
    Invoke-Ssh "mkdir -p '$NasPath'"

    Say "uploading package ..."
    & scp @scpArgs $tar "${target}:/tmp/alpha-nas.tar.gz"
    if ($LASTEXITCODE -ne 0) { throw "scp failed ($LASTEXITCODE)" }

    Say "extracting into $NasPath ..."
    Invoke-Ssh "cd '$NasPath' && tar -xzf /tmp/alpha-nas.tar.gz && rm -f /tmp/alpha-nas.tar.gz && ls"

    # ── compose 文件名（群晖「项目」功能要求 docker-compose.yml）──
    Invoke-Ssh "cd '$NasPath' && [ -f docker-compose.yml ] && mv -f docker-compose.yml docker-compose.upstream.yml; cp -f docker-compose.nas.yml docker-compose.yml; ls docker-compose*"

    # ── .env ────────────────────────────────────────────────
    Say "writing .env ..."
    $tokenB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($WriteToken))
    Invoke-Ssh "cd '$NasPath' && cp -f .env.nas.template .env && sed -i \"s|^ALPHA_WRITE_TOKEN=.*|ALPHA_WRITE_TOKEN=$(echo $tokenB64 | base64 -d)|\" .env && grep -c '^ALPHA_WRITE_TOKEN=' .env"

    # ── 可选：迁移本机 K 线库 ────────────────────────────────
    if ($WithDb) {
        $db = Join-Path $root 'data\market_kline.db'
        if (Test-Path $db) {
            Say "checkpointing local db (WAL) ..."
            $py = Join-Path $root '.venv\Scripts\python.exe'
            if (Test-Path $py) {
                & $py -c "import sqlite3;c=sqlite3.connect('data/market_kline.db',timeout=10);c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.close();print('ckpt ok')"
            }
            Say "uploading market_kline.db ($([math]::Round((Get-Item $db).Length/1MB,1)) MB) ..."
            & scp @scpArgs $db "${target}:$NasPath/import_market_kline.db"
            if ($LASTEXITCODE -ne 0) { throw "scp db failed ($LASTEXITCODE)" }
            Say "importing db into named volume (container stopped first) ..."
            Invoke-Ssh @"
cd '$NasPath'
$dockerCmd compose -f docker-compose.yml up -d >/dev/null 2>&1 || true
$dockerCmd stop alpha >/dev/null 2>&1 || true
$dockerCmd exec alpha rm -f /app/data/market_kline.db /app/data/market_kline.db-wal /app/data/market_kline.db-shm >/dev/null 2>&1 || true
$dockerCmd cp '$NasPath/import_market_kline.db' alpha:/app/data/market_kline.db
rm -f import_market_kline.db
"@
        } else {
            Say "WARNING: data\market_kline.db not found, skipping db migration"
        }
    }

    # ── 构建与启动 ──────────────────────────────────────────
    if ($SkipBuild) {
        Say "starting container (no rebuild) ..."
        Invoke-Ssh "cd '$NasPath' && mkdir -p logs && $dockerCmd compose -f docker-compose.yml up -d"
    } else {
        Say "building image and starting (this can take a while: torch CPU wheel ~200MB) ..."
        if ($UseSudo) {
            Invoke-Ssh "cd '$NasPath' && chmod +x deploy-nas.sh && echo $([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("$Password`n"))) | base64 -d | sudo -S -p '' bash deploy-nas.sh"
        } else {
            Invoke-Ssh "cd '$NasPath' && chmod +x deploy-nas.sh && ./deploy-nas.sh"
        }
    }

    # ── 健康检查 ────────────────────────────────────────────
    Say "waiting for http://${NasHost}:$hostPort/ ..."
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 5
        try {
            $r = Invoke-WebRequest "http://${NasHost}:$hostPort/" -UseBasicParsing -TimeoutSec 10
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch { }
    }
    if ($ok) {
        Say "DEPLOYED OK -> http://${NasHost}:$hostPort/"
        Say "write token: $WriteToken"
    } else {
        Say "not healthy yet, recent logs:"
        Invoke-Ssh "cd '$NasPath' && $dockerCmd compose -f docker-compose.yml logs --tail 60"
        throw "service did not become healthy"
    }
} finally {
    Remove-Item Env:SSH_ASKPASS -ErrorAction SilentlyContinue
    Remove-Item Env:SSH_ASKPASS_REQUIRE -ErrorAction SilentlyContinue
    Remove-Item Env:DISPLAY -ErrorAction SilentlyContinue
    Remove-Item Env:GIT_ASKPASS -ErrorAction SilentlyContinue
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}
