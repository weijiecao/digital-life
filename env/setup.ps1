<#
.SYNOPSIS
  一键搭建“数字生命”可复现环境（AstrBot 4.29.3 + Python 3.12 + BGE 本地模型）。

.DESCRIPTION
  只做“搭环境 + 生成实例”，不改系统设置、不写注册表、不装全局包：
    1. 在 testbed\.venv 建虚拟环境（已存在则复用）
    2. 下载并校验 AstrBot v4.29.3 源码（sha256 见 env\astrbot.pin.json）
    3. 安装 AstrBot 依赖、torch(CPU)、以及 env\requirements.lock.txt 锁定版本
    4. 下载 BGE 模型 bge-small-zh-v1.5（ModelScope）
    5. 生成 testbed\instance（webchat + mock provider + 插件配置模板）
  全程约 10–15 分钟、占约 1.6 GB（主要是 torch 与模型）。

.PARAMETER Root
  仓库根目录，默认是本脚本上一级。

.PARAMETER Python
  用于创建 venv 的 Python（3.10–3.12，推荐 3.12）。

.PARAMETER Force
  已存在的实例/配置也重新生成（会覆盖 testbed\instance\data 下的配置，不动数据库）。

.PARAMETER SkipVenv
.PARAMETER SkipDeps
.PARAMETER SkipAstrBot
.PARAMETER SkipModel
  跳过对应步骤（调试或分步执行用）。

.EXAMPLE
  .\env\setup.ps1
  .\env\setup.ps1 -SkipModel -SkipAstrBot
#>
[CmdletBinding()]
param(
    [string]$Root = (Split-Path $PSScriptRoot -Parent),
    [string]$Python = 'python',
    [switch]$Force,
    [switch]$SkipVenv,
    [switch]$SkipDeps,
    [switch]$SkipAstrBot,
    [switch]$SkipModel
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Mirror     = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$TorchIndex = 'https://download.pytorch.org/whl/cpu'
$Testbed    = Join-Path $Root 'testbed'
$Venv       = Join-Path $Testbed '.venv'
$Py         = Join-Path $Venv 'Scripts\python.exe'
$AstrBotDir = Join-Path $Testbed 'AstrBot-4.29.3'
$ModelDir   = Join-Path $Testbed 'models\bge-small-zh-v1.5'
$Instance   = Join-Path $Testbed 'instance'
$EnvDir     = Join-Path $Root 'env'
$PluginSrc  = Join-Path $Root 'dev\astrbot_plugin_digital_memory_store'

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "  [OK] $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  [!]  $text" -ForegroundColor Yellow }

# ---------- 0. 前置检查 ----------
Step '0/6 前置检查'
if (-not (Test-Path $Root)) { throw "仓库根不存在：$Root" }
if ($Python -eq 'python') {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { throw '找不到 python，请用 -Python 指定 python.exe 的完整路径' }
    $Python = $cmd.Source
}
$ver = & $Python -c "import sys;print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]'3.10' -or [version]$ver -gt [version]'3.12') {
    Warn "当前 Python $ver；锁定清单在 3.12.10 上验证，其他版本可能装不上 torch/sentence-transformers"
} else { Ok "Python $ver ($Python)" }
foreach ($p in @($Testbed, $PluginSrc, (Join-Path $EnvDir 'requirements.lock.txt'))) {
    if (-not (Test-Path $p)) { throw "缺少必需路径：$p" }
}
Ok "仓库根：$Root"

# ---------- 1. venv ----------
Step '1/6 虚拟环境 testbed\.venv'
if ($SkipVenv) { Warn '按参数跳过' }
elseif (Test-Path $Py) { Ok '已存在，复用' }
else {
    & $Python -m venv $Venv
    Ok "已创建 $Venv"
}

# ---------- 2. AstrBot 源码 ----------
Step '2/6 AstrBot 源码（v4.29.3）'
$pin = Get-Content (Join-Path $EnvDir 'astrbot.pin.json') -Raw -Encoding utf8 | ConvertFrom-Json
if ($SkipAstrBot) { Warn '按参数跳过' }
elseif (Test-Path (Join-Path $AstrBotDir 'main.py')) { Ok '已存在，复用' }
else {
    $zip = Join-Path $Testbed 'downloads\AstrBot-v4.29.3.zip'
    New-Item -ItemType Directory -Force -Path (Split-Path $zip -Parent) | Out-Null
    if (-not (Test-Path $zip)) {
        Write-Host "  下载 $($pin.source)"
        Invoke-WebRequest -Uri $pin.source -OutFile $zip -UseBasicParsing
    }
    $sha = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($sha -ne $pin.sha256) { throw "AstrBot 压缩包校验失败：期望 $($pin.sha256)，实际 $sha" }
    Ok "校验通过 $sha"
    Expand-Archive -LiteralPath $zip -DestinationPath $Testbed -Force
    if (-not (Test-Path $AstrBotDir)) { throw "解压后找不到 $AstrBotDir" }
    Ok '已解压'
}

# ---------- 3. 依赖 ----------
Step '3/6 Python 依赖'
if ($SkipDeps) { Warn '按参数跳过' }
else {
    & $Py -m pip install --upgrade pip -i $Mirror
    if (-not $SkipAstrBot) {
        Write-Host '  安装 AstrBot 依赖…'
        & $Py -m pip install -i $Mirror -r (Join-Path $AstrBotDir 'requirements.txt')
    }
    Write-Host '  安装 torch (CPU 轮子)…'
    & $Py -m pip install --index-url $TorchIndex torch
    Write-Host '  按锁定清单补齐其余依赖…'
    & $Py -m pip install -i $Mirror --extra-index-url $TorchIndex -r (Join-Path $EnvDir 'requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，见上面的 pip 输出' }
    Ok '依赖安装完成'
}

# ---------- 4. 模型 ----------
Step '4/6 本地语义模型（可选）'
if ($SkipModel) { Warn '按参数跳过（无模型时语义召回不可用，插件会自检提示）' }
else {
    & (Join-Path $EnvDir 'download_model.ps1') -Dest $ModelDir
    if ($LASTEXITCODE -ne 0) { throw '模型下载失败' }
    Ok "模型目录：$ModelDir"
}

# ---------- 5. 实例与插件 ----------
Step '5/6 实例目录 testbed\instance'
$dataDir = Join-Path $Instance 'data'
$cfgDir  = Join-Path $dataDir 'config'
$plugDir = Join-Path $dataDir 'plugins\astrbot_plugin_digital_memory_store'
New-Item -ItemType Directory -Force -Path $dataDir, $cfgDir, (Split-Path $plugDir -Parent) | Out-Null

$cmdCfg   = Join-Path $dataDir 'cmd_config.json'
$plugCfg  = Join-Path $cfgDir 'astrbot_plugin_digital_memory_store_config.json'
if ((Test-Path $cmdCfg) -and -not $Force) {
    Warn "已存在 cmd_config.json，保留（要重建加 -Force）"
} else {
    Copy-Item (Join-Path $EnvDir 'configs\cmd_config.template.json') $cmdCfg -Force
    Ok 'cmd_config.json（webchat + mock provider + aiocqhttp 6201）'
}
if ((Test-Path $plugCfg) -and -not $Force) {
    Warn "已存在插件配置，保留（要重建加 -Force）"
} else {
    Copy-Item (Join-Path $EnvDir 'configs\plugin_config.template.json') $plugCfg -Force
    Ok '插件配置（白名单与 model_dir 待设置）'
}

# 把 model_dir 指向实际下载位置（模板里留空）
$enc = New-Object System.Text.UTF8Encoding($false)
$plugObj = [IO.File]::ReadAllText($plugCfg, [Text.Encoding]::UTF8) | ConvertFrom-Json
if ($plugObj.model_dir -ne $ModelDir -and (Test-Path $ModelDir)) {
    $plugObj.model_dir = $ModelDir
    [IO.File]::WriteAllText($plugCfg, ($plugObj | ConvertTo-Json -Depth 20) + "`n", $enc)
    Ok "model_dir → $ModelDir"
}

# 同步插件源码（不碰 __pycache__，也不带内部文档）
$ExcludeDocs = @('测试前功能清单.md', '项目节点进度.md')
if (Test-Path $plugDir) { Remove-Item $plugDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $plugDir | Out-Null
Get-ChildItem $PluginSrc -Recurse -File |
    Where-Object {
        $_.FullName -notmatch '__pycache__' -and
        $_.Extension -ne '.pyc' -and
        $ExcludeDocs -notcontains $_.Name
    } |
    ForEach-Object {
        $rel = $_.FullName.Substring($PluginSrc.Length + 1)
        $dst = Join-Path $plugDir $rel
        New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
        Copy-Item $_.FullName $dst -Force
    }
Ok "插件已同步到 instance\data\plugins"

# ---------- 6. 自检 ----------
Step '6/6 自检'
& (Join-Path $EnvDir 'verify_env.ps1') -Root $Root -ModelDir $ModelDir

$next = @"

下一步：
  1) 启动 mock provider（另开一个窗口）：
     .\testbed\.venv\Scripts\python.exe .\testbed\harness\mock_openai.py --port 9101 --log .\testbed\logs\llm_requests.jsonl
  2) 跑真实链路探针：
     .\testbed\.venv\Scripts\python.exe .\testbed\harness\real_chain.py --out .\dev\reports\real_env.json
  3) 跑离线回归：
     cd dev; .\..\testbed\.venv\Scripts\python.exe -m unittest -q test_store test_plugin test_delivery
"@
Write-Host $next -ForegroundColor Gray
