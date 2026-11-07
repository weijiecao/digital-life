<#
.SYNOPSIS
  一键把本插件装进 AstrBot：复制插件 → 写配置 → （可选）装语义依赖 → （可选）下载模型。

.DESCRIPTION
  目标人群是已经在用 AstrBot 的人：默认只做“装进去 + 打开开关”，语义依赖与模型按需再装。
  不修改 AstrBot 源码，不改系统 PATH/注册表，不碰你现有的机器人配置。

  交互模式下会问你三件事：AstrBot 根目录、允许的 QQ 号、要不要现在装语义依赖/模型。
  参数给全就完全不问，适合远程/批处理。

.PARAMETER AstrBotRoot
  AstrBot 根目录（含 data\plugins 或 main.py）。不填会先尝试 C:\AstrBot 等常见路径，再询问。

.PARAMETER QQ
  允许采集的 QQ 号，多个用英文逗号。不填会询问。

.PARAMETER Preset
  self（推荐：采集+整理+受限召回）/ off（装但不启用）/ debug（检索实验开关全开）。

.PARAMETER ModelDir
  已有的 BGE（SentenceTransformer 格式）目录；与 -WithModel 二选一。

.PARAMETER WithDeps
  装语义依赖：torch(CPU 轮子) + sentence-transformers，装进 AstrBot 自己的解释器。

.PARAMETER WithModel
  从 ModelScope 下载 bge-small-zh-v1.5（约 92 MB）到 <AstrBot根>\data\models\bge-small-zh-v1.5。

.PARAMETER SkipDeps
.PARAMETER SkipModel
  明确跳过，不再询问。

.PARAMETER Force
  重建配置（默认保留原配置，只补关键开关）。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -AstrBotRoot "C:\AstrBot" -QQ 123456 -WithDeps -WithModel
#>
[CmdletBinding()]
param(
    [string]$AstrBotRoot = '',
    [string]$QQ = '',
    [ValidateSet('off', 'self', 'debug')]
    [string]$Preset = 'self',
    [string]$ModelDir = '',
    [switch]$WithDeps,
    [switch]$SkipDeps,
    [switch]$WithModel,
    [switch]$SkipModel,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$PluginName = 'astrbot_plugin_digital_memory_store'
$Source = $PSScriptRoot
$Mirror = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$TorchIndex = 'https://download.pytorch.org/whl/cpu'

function Fail($message) {
    Write-Host ''
    Write-Host "[失败] $message" -ForegroundColor Red
    exit 1
}
function Ok($message) { Write-Host "  [OK] $message" -ForegroundColor Green }
function Note($message) { Write-Host "  [i]  $message" -ForegroundColor DarkGray }
function Warn($message) { Write-Host "  [!]  $message" -ForegroundColor Yellow }

Write-Host ''
Write-Host '=== 数字生命记忆库 · 一键安装 ===' -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $Source 'metadata.yaml'))) {
    Fail "install.ps1 必须和 metadata.yaml 在同一目录（解压后的插件根）。当前：$Source"
}
Ok "插件源：$Source"

# ---------- 1. 定位 AstrBot ----------
$isAstrBotRoot = {
    param($path)
    if (-not $path -or -not (Test-Path $path)) { return $false }
    return (Test-Path (Join-Path $path 'data')) -or (Test-Path (Join-Path $path 'main.py')) -or
           (Test-Path (Join-Path $path 'astrbot'))
}

if (-not $AstrBotRoot) {
    $candidates = @()
    if ($env:ASTRBOT_ROOT) { $candidates += $env:ASTRBOT_ROOT }
    $candidates += 'C:\AstrBot', 'D:\AstrBot', (Join-Path $HOME 'AstrBot'), (Join-Path $HOME 'astrbot'), (Get-Location).Path
    $found = $candidates | Where-Object { & $isAstrBotRoot $_ } | Select-Object -First 1
    if ($found) {
        $AstrBotRoot = $found
        Note "自动识别 AstrBot 根：$AstrBotRoot"
    } else {
        Write-Host ''
        Write-Host '没自动找到 AstrBot 根目录（应包含 data\plugins 或 main.py）。' -ForegroundColor Yellow
        Write-Host '示例：C:\AstrBot、D:\AstrBot、C:\Users\你\Desktop\AstrBot'
        $AstrBotRoot = Read-Host '请输入 AstrBot 根目录'
    }
}
$AstrBotRoot = $AstrBotRoot.Trim('"').TrimEnd('\')
if (-not (& $isAstrBotRoot $AstrBotRoot)) {
    Fail "这个目录不像 AstrBot 根目录：$AstrBotRoot（应包含 data\plugins 或 main.py）"
}
Ok "AstrBot 根：$AstrBotRoot"

# ---------- 2. QQ 白名单 ----------
if (-not $QQ) {
    Write-Host ''
    Write-Host '只有白名单 QQ 的私聊会被采集（多个用英文逗号）。' -ForegroundColor Yellow
    $QQ = Read-Host '请输入允许的 QQ 号'
}
$QQ = ($QQ -replace '\s', '').Trim(',')
if ($QQ -notmatch '^[0-9]+(,[0-9]+)*$') {
    Fail "QQ 号格式不对：'$QQ'（只要数字，多个用英文逗号分隔）"
}
Ok "白名单：$QQ"

# ---------- 3. 依赖 / 模型：先问清楚 ----------
$pythonCandidates = @(
    (Join-Path $AstrBotRoot '.venv\Scripts\python.exe'),
    (Join-Path $AstrBotRoot 'venv\Scripts\python.exe'),
    (Join-Path $AstrBotRoot 'python\python.exe')
)
$Py = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

$needDeps = $false
if (-not $SkipDeps) {
    if ($WithDeps) { $needDeps = $true }
    elseif ($Py) {
        $probe = & $Py -c "import importlib.util as u; print(int(all(u.find_spec(m) for m in ('torch','sentence_transformers'))))" 2>$null
        if ("$probe".Trim() -eq '0') {
            Write-Host ''
            Write-Host '语义依赖未安装（torch + sentence-transformers，约 1 GB，仅影响语义召回）。' -ForegroundColor Yellow
            $answer = Read-Host '现在安装吗？[y/N]'
            $needDeps = ($answer -match '^(y|yes|是)$')
        } else { Note '语义依赖已就绪' }
    } else {
        Warn "没在 $AstrBotRoot 找到 .venv，无法自动装依赖（记录/查看/备份不受影响）"
    }
}

$needModel = $false
if (-not $SkipModel) {
    if ($WithModel) { $needModel = $true }
    elseif (-not $ModelDir) {
        Write-Host ''
        Write-Host '本地语义模型 bge-small-zh-v1.5 约 92 MB（ModelScope）；没模型也能用，只是没有语义召回。' -ForegroundColor Yellow
        $answer = Read-Host '现在下载模型吗？[y/N]'
        $needModel = ($answer -match '^(y|yes|是)$')
    }
}

# ---------- 4. 复制插件 ----------
$pluginsDir = Join-Path $AstrBotRoot 'data\plugins'
$target = Join-Path $pluginsDir $PluginName
$backupRoot = Join-Path $AstrBotRoot 'data\plugins_backup'
if (-not (Test-Path $pluginsDir)) { New-Item -ItemType Directory -Force -Path $pluginsDir | Out-Null }
if (Test-Path $target) {
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $backup = Join-Path $backupRoot ("{0}_{1}" -f $PluginName, (Get-Date -Format 'yyyyMMdd_HHmmss'))
    Move-Item $target $backup
    Note "旧版本已备份：$backup"
}
# 升级前同时备份数据目录；数据库与插件目录分离，不能只备份插件。
$dataDir = Join-Path $AstrBotRoot ("data\plugin_data\$PluginName")
if (Test-Path $dataDir) {
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $dataBackup = Join-Path $backupRoot ("dlm_data_{0}" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
    Copy-Item $dataDir $dataBackup -Recurse -Force
    Note "数据目录已备份：$dataBackup（请保留到升级验证完成）"
}
New-Item -ItemType Directory -Force -Path $target | Out-Null
Get-ChildItem $Source -Recurse -File |
    Where-Object { $_.FullName -notmatch '__pycache__' -and $_.Extension -ne '.pyc' } |
    ForEach-Object {
        $rel = $_.FullName.Substring($Source.Length + 1)
        $dst = Join-Path $target $rel
        New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
        Copy-Item $_.FullName $dst -Force
    }
Ok "插件已复制：$target"

# ---------- 5. 装依赖（CPU 版 torch） ----------
if ($needDeps -and $Py) {
    Write-Host ''
    Write-Host '--- 安装语义依赖（torch CPU + sentence-transformers）---' -ForegroundColor Cyan
    Note "目标解释器：$Py"
    & $Py -m pip install --index-url $TorchIndex torch
    if ($LASTEXITCODE -ne 0) { Warn 'torch 安装失败，语义召回将不可用（其余功能正常）' }
    else {
        & $Py -m pip install -i $Mirror 'sentence-transformers>=2.7,<6'
        if ($LASTEXITCODE -ne 0) { Warn 'sentence-transformers 安装失败，语义召回将不可用' }
        else { Ok '语义依赖安装完成' }
    }
}

# ---------- 6. 下载模型 ----------
if ($needModel) {
    if (-not $ModelDir) { $ModelDir = Join-Path $AstrBotRoot 'data\models\bge-small-zh-v1.5' }
    Write-Host ''
    Write-Host "--- 下载 BGE 模型到 $ModelDir ---" -ForegroundColor Cyan
    $base = 'https://www.modelscope.cn/models/AI-ModelScope/bge-small-zh-v1.5/resolve/master'
    $files = @('config.json', 'config_sentence_transformers.json', 'modules.json', 'sentence_bert_config.json',
               'special_tokens_map.json', 'tokenizer.json', 'tokenizer_config.json', 'vocab.txt',
               '1_Pooling/config.json', 'model.safetensors')
    New-Item -ItemType Directory -Force -Path $ModelDir, (Join-Path $ModelDir '1_Pooling') | Out-Null
    $bad = @()
    foreach ($f in $files) {
        $out = Join-Path $ModelDir $f
        if (Test-Path $out) { continue }
        try {
            Invoke-WebRequest -Uri "$base/$f" -OutFile $out -UseBasicParsing -TimeoutSec 300
            Note "下载 $f"
        } catch { $bad += $f }
    }
    $weights = Join-Path $ModelDir 'model.safetensors'
    if ((Test-Path $weights) -and ((Get-Item $weights).Length / 1MB) -gt 50) {
        Ok ("模型就绪：{0}（{1:N1} MB）" -f $ModelDir, ((Get-Item $weights).Length / 1MB))
    } else {
        Warn "模型不完整（缺：$($bad -join ', ')）；已写入 model_dir，加载失败时插件会自动降级"
    }
}
$ModelDir = $ModelDir.Trim('"').TrimEnd('\')
if ($ModelDir -and -not (Test-Path (Join-Path $ModelDir 'config.json'))) {
    Warn "model_dir 不像 SentenceTransformer 目录（缺 config.json）：$ModelDir"
}

# ---------- 7. 写配置 ----------
$configDir = Join-Path $AstrBotRoot 'data\config'
$configPath = Join-Path $configDir ($PluginName + '_config.json')
if (-not (Test-Path $configDir)) { New-Item -ItemType Directory -Force -Path $configDir | Out-Null }

$schema = Get-Content (Join-Path $Source '_conf_schema.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$presetPath = Join-Path $Source ('presets\' + $Preset + '.json')
if (-not (Test-Path $presetPath)) { Fail "找不到预设文件：$presetPath" }
# 注意：PowerShell 变量名大小写不敏感，这里必须叫 $presetObj，否则会覆盖参数 $Preset
$presetObj = Get-Content $presetPath -Raw -Encoding UTF8 | ConvertFrom-Json

$config = [ordered]@{}
if ((Test-Path $configPath) -and -not $Force) {
    $existing = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($p in $existing.PSObject.Properties) { $config[$p.Name] = $p.Value }
    Note '保留已有配置，只补关键开关'
} else {
    foreach ($p in $schema.PSObject.Properties) {
        $default = $p.Value.default
        if ($p.Value.type -eq 'list' -and $null -eq $default) { $default = @() }
        $config[$p.Name] = $default
    }
    foreach ($p in $presetObj.PSObject.Properties) { $config[$p.Name] = $p.Value }
}
if ($Preset -ne 'off') {
    $config['enabled'] = $true
    $config['automatic_memory_enabled'] = $true
}

$merged = @()
if ($config['allowed_sender_ids']) {
    $merged += ([string]$config['allowed_sender_ids']).Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}
$merged += $QQ.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
$config['allowed_sender_ids'] = (($merged | Select-Object -Unique) -join ',')

if ($ModelDir) { $config['model_dir'] = $ModelDir }

$json = $config | ConvertTo-Json -Depth 10
[IO.File]::WriteAllText($configPath, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
Ok "配置已写入：$configPath"

# ---------- 8. 收尾 ----------
Write-Host ''
Write-Host '=== 装好了，下一步 ===' -ForegroundColor Cyan
Write-Host '  1) 重启 AstrBot（必须，否则插件不会重新加载）'
Write-Host '  2) 重启后打开 WebUI 插件页 → 数字生命记忆库 → panel 查看采集/备份'
Write-Host '     v1.0.18 起聊天里没有 /dlm 命令；不要向用户暴露记忆系统'
Write-Host '  3) WebUI 插件页 → 数字生命记忆库 → panel：存储/备份/容量'
Write-Host ''
Write-Host "  配置：$configPath"
$dataDir = Join-Path $AstrBotRoot ("data\plugin_data\$PluginName")
Write-Host "  数据：$dataDir（明文，注意权限）"
if (-not $config['model_dir']) {
    Write-Host ''
    Write-Host '  需要语义召回时：重跑本脚本加 -WithModel；或在 AstrBot 解释器里 pip install -r requirements.txt 再填 model_dir' -ForegroundColor Yellow
}
Write-Host ''
exit 0
