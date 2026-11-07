#requires -Version 5.1
<#
.SYNOPSIS
  一键配置已有 AstrBot 的数字生命记忆库环境（v1.0.19）。

.DESCRIPTION
  只处理 astrbot_plugin_digital_memory_store：
  - 定位 AstrBot 根目录与它自己的 Python；
  - 检查/安装 CPU torch + sentence-transformers；
  - 检查/下载 BGE 模型；
  - 合并插件配置（保留旧键，只补 enabled/automatic/白名单/model_dir）；
  - 最后做一次静态自检。
  不会重装 AstrBot，不会动其他插件，不会自动重启机器人。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\configure_env_1.0.19.ps1 `
    -AstrBotRoot "C:\AstrBot" -QQ "123456" `
    -WithDeps -ModelDir "D:\models\bge-small-zh-v1.5"

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\configure_env_1.0.19.ps1 `
    -AstrBotRoot "C:\AstrBot" -QQ "123456" -WithDeps -WithModel

.EXAMPLE
  # 只改配置，不装依赖/模型（记录、查看、备份、删除仍可用）
  powershell -NoProfile -ExecutionPolicy Bypass -File .\configure_env_1.0.19.ps1 `
    -AstrBotRoot "C:\AstrBot" -QQ "123456" -SkipDeps -SkipModel
#>

[CmdletBinding()]
param(
    [string]$AstrBotRoot = '',
    [string]$QQ = '',
    [string]$ModelDir = '',
    [string]$PythonPath = '',
    [string]$PluginName = 'astrbot_plugin_digital_memory_store',
    [switch]$WithDeps,
    [switch]$SkipDeps,
    [switch]$WithModel,
    [switch]$SkipModel,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$script:Warnings = New-Object System.Collections.Generic.List[string]

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host "== $Text ==" -ForegroundColor Cyan
}
function Write-Ok([string]$Text) { Write-Host "[OK]   $Text" -ForegroundColor Green }
function Write-Warn([string]$Text) {
    Write-Host "[WARN] $Text" -ForegroundColor Yellow
    $script:Warnings.Add($Text) | Out-Null
}
function Fail([string]$Text) { throw $Text }

function Read-Json([string]$Path) {
    $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    return ($raw | ConvertFrom-Json)
}
function Write-JsonNoBom([string]$Path, $Object) {
    $json = $Object | ConvertTo-Json -Depth 20
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json + "`r`n", $enc)
}
function Get-ObjectMap($Object) {
    $map = @{}
    if ($null -eq $Object) { return $map }
    foreach ($p in $Object.PSObject.Properties) { $map[$p.Name] = $p.Value }
    return $map
}

function Resolve-AstrBotRoot([string]$Given) {
    if ($Given) {
        if (-not (Test-Path $Given)) { Fail "AstrBot 根目录不存在：$Given" }
        return (Resolve-Path $Given).Path.TrimEnd('\')
    }
    $candidates = @(
        'C:\AstrBot',
        'D:\AstrBot',
        (Join-Path $env:USERPROFILE 'AstrBot')
    )
    foreach ($c in $candidates) {
        if ((Test-Path (Join-Path $c 'data\plugins')) -or (Test-Path (Join-Path $c 'main.py'))) {
            return (Resolve-Path $c).Path.TrimEnd('\')
        }
    }
    $entered = Read-Host '请输入 AstrBot 根目录（包含 data\plugins 或 main.py）'
    if (-not $entered) { Fail '未提供 AstrBot 根目录' }
    if (-not (Test-Path $entered)) { Fail "AstrBot 根目录不存在：$entered" }
    return (Resolve-Path $entered).Path.TrimEnd('\')
}

function Resolve-Python([string]$Root, [string]$Given) {
    if ($Given) {
        if (-not (Test-Path $Given)) { Fail "指定的 Python 不存在：$Given" }
        return (Resolve-Path $Given).Path
    }
    $candidates = @(
        (Join-Path $Root '.venv\Scripts\python.exe'),
        (Join-Path $Root 'venv\Scripts\python.exe'),
        (Join-Path $Root 'python\python.exe')
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return (Resolve-Path $c).Path }
    }
    Fail "没有在 $Root 找到 AstrBot 的 Python（.venv\Scripts\python.exe）。请用 -PythonPath 指定运行 AstrBot 的解释器。"
}

function Test-Import([string]$Py, [string]$Modules) {
    & $Py -c "import $Modules" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Ensure-Dependencies([string]$Py, [bool]$Skip) {
    Write-Step 'Python 语义依赖'
    Write-Host "解释器：$Py"
    $torch = Test-Import $Py 'torch'
    $st = Test-Import $Py 'sentence_transformers'
    Write-Host ("torch={0}  sentence-transformers={1}" -f $torch, $st)
    if ($torch -and $st) { Write-Ok '语义依赖已存在，跳过安装'; return $true }
    if ($Skip) {
        Write-Warn '缺少语义依赖，已按 -SkipDeps 跳过；语义召回不可用，记录/查看/备份/删除仍可用'
        return $false
    }
    if ($DryRun) {
        Write-Host '[DryRun] 将安装 torch CPU + sentence-transformers'
        return $false
    }
    if (-not $torch) {
        Write-Host '安装 CPU 版 torch...'
        & $Py -m pip install --index-url https://download.pytorch.org/whl/cpu torch
        if ($LASTEXITCODE -ne 0) { Write-Warn 'torch 安装失败；语义召回将不可用' }
    }
    if (-not $st) {
        Write-Host '安装 sentence-transformers...'
        & $Py -m pip install 'sentence-transformers>=2.7,<6'
        if ($LASTEXITCODE -ne 0) { Write-Warn 'sentence-transformers 安装失败；语义召回将不可用' }
    }
    $torch2 = Test-Import $Py 'torch'
    $st2 = Test-Import $Py 'sentence_transformers'
    if ($torch2 -and $st2) { Write-Ok '语义依赖安装完成'; return $true }
    Write-Warn '依赖仍未就绪；插件仍能记录/查看/备份/删除，但语义召回不可用'
    return $false
}

function Get-DefaultModelDir([string]$Root) {
    return (Join-Path $Root 'data\models\bge-small-zh-v1.5')
}

function Test-ModelDir([string]$Path) {
    if (-not $Path) { return $false }
    $config = Join-Path $Path 'config.json'
    return ((Test-Path $Path) -and (Test-Path $config))
}

function Ensure-Model([string]$Root, [string]$Given, [bool]$Download, [bool]$Skip) {
    Write-Step 'BGE 模型'
    if ($Given) {
        if (Test-ModelDir $Given) {
            $resolved = (Resolve-Path $Given).Path
            Write-Ok "使用已有模型：$resolved"
            return $resolved
        }
        Write-Warn "ModelDir 无效（缺 config.json）：$Given"
    }
    $default = Get-DefaultModelDir $Root
    if (Test-ModelDir $default) {
        $resolved = (Resolve-Path $default).Path
        Write-Ok "使用默认模型目录：$resolved"
        return $resolved
    }
    if ($Skip) {
        Write-Warn '未找到模型，已按 -SkipModel 跳过；语义召回不可用'
        return ''
    }
    if (-not $Download) {
        Write-Warn "未找到模型。可加 -WithModel 下载，或用 -ModelDir 指向已有目录。"
        return ''
    }
    if ($DryRun) {
        Write-Host "[DryRun] 将下载模型到 $default"
        return $default
    }
    Write-Host "从 ModelScope 下载 bge-small-zh-v1.5 到 $default ..."
    $base = 'https://www.modelscope.cn/models/AI-ModelScope/bge-small-zh-v1.5/resolve/master'
    $files = @(
        'config.json', 'config_sentence_transformers.json', 'modules.json',
        'sentence_bert_config.json', 'special_tokens_map.json', 'tokenizer.json',
        'tokenizer_config.json', 'vocab.txt', '1_Pooling/config.json',
        'model.safetensors'
    )
    New-Item -ItemType Directory -Force -Path $default, (Join-Path $default '1_Pooling') | Out-Null
    foreach ($f in $files) {
        $dst = Join-Path $default $f
        $parent = Split-Path $dst -Parent
        New-Item -ItemType Directory -Force $parent | Out-Null
        try {
            Invoke-WebRequest "$base/$f" -OutFile $dst -UseBasicParsing
        } catch {
            Write-Warn "下载失败：$f；可稍后手动补到 $default"
        }
    }
    if (Test-ModelDir $default) {
        Write-Ok "模型已就绪：$default"
        return (Resolve-Path $default).Path
    }
    Write-Warn "模型未完整：$default；插件会退化为字面/背景路径"
    return ''
}

function Ensure-Config([string]$Root, [string]$Plugin, [string]$ModelEffective, [string]$AllowedQQ) {
    Write-Step '插件配置'
    $schemaPath = Join-Path $Plugin '_conf_schema.json'
    if (-not (Test-Path $schemaPath)) { Fail "缺少 _conf_schema.json：$schemaPath" }
    $schema = Read-Json $schemaPath

    $configDir = Join-Path $Root 'data\config'
    New-Item -ItemType Directory -Force $configDir | Out-Null
    $configPath = Join-Path $configDir ($PluginName + '_config.json')
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    if (Test-Path $configPath) {
        Copy-Item $configPath "$configPath.bak_$stamp" -Force
        Write-Ok "已备份旧配置：$configPath.bak_$stamp"
    }

    $config = @{}
    # 1) schema 默认值
    foreach ($p in $schema.PSObject.Properties) {
        $v = $p.Value
        $default = $null
        if ($null -ne $v.PSObject.Properties['default']) { $default = $v.default }
        elseif ($v.type -eq 'bool') { $default = $false }
        elseif ($v.type -eq 'int') { $default = 0 }
        elseif ($v.type -eq 'float') { $default = 0.0 }
        elseif ($v.type -eq 'list') { $default = @() }
        else { $default = '' }
        $config[$p.Name] = $default
    }
    # 2) self 预设
    $preset = Join-Path $Plugin 'presets\self.json'
    if (Test-Path $preset) {
        foreach ($k in (Get-ObjectMap (Read-Json $preset)).GetEnumerator()) {
            $config[$k.Key] = $k.Value
        }
    }
    # 3) 旧配置覆盖
    if (Test-Path $configPath) {
        foreach ($k in (Get-ObjectMap (Read-Json $configPath)).GetEnumerator()) {
            $config[$k.Key] = $k.Value
        }
    }

    $config['enabled'] = $true
    $config['automatic_memory_enabled'] = $true
    if ($AllowedQQ.Trim()) { $config['allowed_sender_ids'] = $AllowedQQ.Trim() }
    elseif (-not ([string]$config['allowed_sender_ids']).Trim()) {
        Fail '没有白名单 QQ。请用 -QQ 123456 传入，或先在 WebUI 填写 allowed_sender_ids。'
    }
    if ($ModelEffective) { $config['model_dir'] = $ModelEffective }
    elseif (-not $config.ContainsKey('model_dir')) { $config['model_dir'] = '' }

    if ($DryRun) {
        Write-Host "[DryRun] 将写入配置：$configPath"
        Write-Host ("enabled={0} automatic={1} model_dir={2}" -f $config['enabled'], $config['automatic_memory_enabled'], $config['model_dir'])
        return $true
    }
    Write-JsonNoBom $configPath $config
    Write-Ok "配置已写入：$configPath"
    return $true
}

try {
    if ($WithDeps -and $SkipDeps) { Fail '不能同时使用 -WithDeps 和 -SkipDeps' }
    if ($WithModel -and $SkipModel) { Fail '不能同时使用 -WithModel 和 -SkipModel' }

    Write-Step '定位 AstrBot'
    $root = Resolve-AstrBotRoot $AstrBotRoot
    Write-Ok "AstrBot 根：$root"

    $plugin = Join-Path $root ("data\plugins\" + $PluginName)
    if (-not (Test-Path $plugin)) {
        Fail "插件未安装：$plugin。请先把 1.0.19 包解压/安装到 data\plugins，再运行本脚本。"
    }
    $meta = Join-Path $plugin 'metadata.yaml'
    $version = ''
    if (Test-Path $meta) {
        foreach ($line in [System.IO.File]::ReadAllLines($meta, [System.Text.Encoding]::UTF8)) {
            if ($line -match '^\s*version:\s*(.+)\s*$') { $version = $Matches[1].Trim(); break }
        }
    }
    Write-Ok "插件目录：$plugin"
    Write-Ok "插件版本：$version"

    if (-not $QQ.Trim()) {
        $existingConfig = Join-Path $root ('data\config\' + $PluginName + '_config.json')
        if (Test-Path $existingConfig) {
            $existing = Read-Json $existingConfig
            if ($existing.PSObject.Properties['allowed_sender_ids']) {
                $QQ = [string]$existing.allowed_sender_ids
                if ($QQ.Trim()) { Write-Ok "沿用已有白名单：$QQ" }
            }
        }
    }
    if (-not $QQ.Trim()) {
        $QQ = Read-Host '请输入白名单 QQ（多个用英文逗号）'
    }
    if (-not $QQ.Trim()) { Fail '白名单 QQ 不能为空' }

    $py = Resolve-Python $root $PythonPath
    Write-Ok "Python：$py"

    $depsOk = Ensure-Dependencies $py ([bool]$SkipDeps)
    $model = Ensure-Model $root $ModelDir ([bool]$WithModel) ([bool]$SkipModel)

    Ensure-Config $root $plugin $model $QQ | Out-Null

    Write-Step '自检'
    if (-not (Test-Path (Join-Path $plugin 'main.py'))) { Write-Warn '缺少 main.py' }
    if (-not (Test-Path (Join-Path $plugin '_conf_schema.json'))) { Write-Warn '缺少 _conf_schema.json' }
    $configPath = Join-Path $root 'data\config\astrbot_plugin_digital_memory_store_config.json'
    if (Test-Path $configPath) {
        $cfg = Read-Json $configPath
        $schemaPath = Join-Path $plugin '_conf_schema.json'
        $keys = @((Read-Json $schemaPath).PSObject.Properties).Count
        Write-Ok "配置可读：$configPath（schema $keys keys）"
    } else {
        Write-Warn '配置文件未找到'
    }
    if (-not $depsOk) { Write-Warn '语义依赖未就绪：只影响语义召回' }
    if (-not $model) { Write-Warn '模型未就绪：只影响语义召回' }

    Write-Step '完成'
    Write-Host '下一步：重启 AstrBot，然后打开 WebUI 插件页 → 数字生命记忆库 → panel 查看统计。'
    Write-Host 'v1.0.19 起聊天里没有 /dlm 命令；不要向用户暴露记忆系统。'
    if ($script:Warnings.Count -gt 0) {
        Write-Host ''
        Write-Host ('有 {0} 条警告：' -f $script:Warnings.Count) -ForegroundColor Yellow
        foreach ($w in $script:Warnings) { Write-Host ('  - ' + $w) -ForegroundColor Yellow }
        exit 2
    }
    exit 0
}
catch {
    Write-Host ''
    Write-Host ('[FAIL] ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
