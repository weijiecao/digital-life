<#
.SYNOPSIS
  自检：确认这套环境真的能跑（Python/依赖/AstrBot 版本/模型/实例/端口）。

.DESCRIPTION
  只读检查，不修改任何东西。全部通过时退出码 0，否则 1 并列出失败项。
  模型加载检查约 10–15 秒（真实加载一次 BGE），可用 -SkipModelLoad 跳过。

.PARAMETER Root
  仓库根，默认本脚本上一级。

.PARAMETER ModelDir
  模型目录，默认 testbed\models\bge-small-zh-v1.5。

.PARAMETER SkipModelLoad
  只检查文件是否存在，不实际加载模型。

.EXAMPLE
  .\env\verify_env.ps1
  .\env\verify_env.ps1 -SkipModelLoad
#>
[CmdletBinding()]
param(
    [string]$Root = (Split-Path $PSScriptRoot -Parent),
    [string]$ModelDir = '',
    [switch]$SkipModelLoad
)

$ErrorActionPreference = 'Continue'
$results = New-Object System.Collections.Generic.List[object]
function Add-Check($name, $ok, $detail) {
    $script:results.Add([pscustomobject]@{ Check = $name; OK = [bool]$ok; Detail = $detail })
}

if (-not $ModelDir) { $ModelDir = Join-Path $Root 'testbed\models\bge-small-zh-v1.5' }
$Py = Join-Path $Root 'testbed\.venv\Scripts\python.exe'

# 1) 文件布局
foreach ($rel in @('dev\astrbot_plugin_digital_memory_store\metadata.yaml',
                   'testbed\AstrBot-4.29.3\main.py',
                   'testbed\harness\real_chain.py',
                   'env\requirements.lock.txt')) {
    Add-Check "存在 $rel" (Test-Path (Join-Path $Root $rel)) (Join-Path $Root $rel)
}

# 2) venv 与 Python
Add-Check 'venv python 存在' (Test-Path $Py) $Py
if (Test-Path $Py) {
    $v = & $Py -c "import sys;print(sys.version.split()[0])" 2>&1
    Add-Check 'Python 版本' ($v -match '^3\.(10|11|12)\.') $v
} else {
    Add-Check 'Python 版本' $false '未找到 venv'
}

# 3) 依赖版本
if (Test-Path $Py) {
    $probe = @'
import importlib, json, sys
out = {}
for mod, attr in (("torch", "__version__"), ("sentence_transformers", "__version__"),
                  ("transformers", "__version__"), ("numpy", "__version__")):
    try:
        m = importlib.import_module(mod)
        out[mod] = getattr(m, attr, "?")
    except Exception as exc:
        out[mod] = "MISSING: %s" % exc
print(json.dumps(out))
'@
    $probeFile = Join-Path $env:TEMP 'dlm_env_probe.py'
    Set-Content -LiteralPath $probeFile -Value $probe -Encoding utf8
    $json = & $Py $probeFile 2>&1
    Remove-Item $probeFile -Force -ErrorAction SilentlyContinue
    try {
        $deps = $json | ConvertFrom-Json
        foreach ($k in 'torch', 'sentence_transformers', 'transformers', 'numpy') {
            Add-Check "依赖 $k" ($deps.$k -notmatch 'MISSING') $deps.$k
        }
    } catch {
        Add-Check '依赖探测' $false ($json -join ' ')
    }
}

# 4) AstrBot 版本锚点
$pyproj = Join-Path $Root 'testbed\AstrBot-4.29.3\pyproject.toml'
if (Test-Path $pyproj) {
    $verLine = (Select-String -Path $pyproj -Pattern '^version\s*=' | Select-Object -First 1).Line
    Add-Check 'AstrBot 版本 4.29.3' ($verLine -match '4\.29\.3') $verLine
}

# 5) 模型
Add-Check '模型 config.json' (Test-Path (Join-Path $ModelDir 'config.json')) $ModelDir
$weights = Join-Path $ModelDir 'model.safetensors'
if (Test-Path $weights) {
    $mb = [math]::Round((Get-Item $weights).Length / 1MB, 1)
    Add-Check '模型权重 >50MB' ($mb -gt 50) "$mb MB"
} else {
    Add-Check '模型权重 >50MB' $false '缺 model.safetensors'
}
if ((Test-Path $Py) -and -not $SkipModelLoad -and (Test-Path (Join-Path $ModelDir 'config.json'))) {
    $load = @'
import sys, time
from sentence_transformers import SentenceTransformer
t0 = time.perf_counter()
m = SentenceTransformer(sys.argv[1], device="cpu")
v = m.encode(["自检：今天下雨"], normalize_embeddings=True)
print("dim=%d load=%.1fs" % (len(v[0]), time.perf_counter() - t0))
'@
    $loadFile = Join-Path $env:TEMP 'dlm_model_probe.py'
    Set-Content -LiteralPath $loadFile -Value $load -Encoding utf8
    $out = & $Py $loadFile $ModelDir 2>&1
    Remove-Item $loadFile -Force -ErrorAction SilentlyContinue
    Add-Check 'BGE 实际加载' ($LASTEXITCODE -eq 0 -and ($out -join ' ') -match 'dim=512') (($out | Select-Object -Last 2) -join ' | ')
}

# 6) 实例与插件
$inst = Join-Path $Root 'testbed\instance\data'
$plugCfg = Join-Path $inst 'config\astrbot_plugin_digital_memory_store_config.json'
Add-Check '实例 cmd_config.json' (Test-Path (Join-Path $inst 'cmd_config.json')) $inst
if (Test-Path $plugCfg) {
    try {
        $pc = [IO.File]::ReadAllText($plugCfg, [Text.Encoding]::UTF8) | ConvertFrom-Json
        $okCfg = $true
        try { $null = $pc.enabled } catch { $okCfg = $false }
        Add-Check '插件配置可解析' $okCfg ("enabled=$($pc.enabled) 白名单=$($pc.allowed_sender_ids) model_dir=$($pc.model_dir)")
    } catch {
        Add-Check '插件配置可解析' $false $_.Exception.Message
    }
} else {
    Add-Check '插件配置可解析' $false '未生成（先跑 env\setup.ps1）'
}
Add-Check '实例内插件已同步' (Test-Path (Join-Path $inst 'plugins\astrbot_plugin_digital_memory_store\main.py')) ''

# 7) 端口占用（mock provider 9101 / aiocqhttp 6201 应当空闲，除非已在运行）
foreach ($port in 9101, 6201) {
    $busy = $null -ne (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    Add-Check "端口 $port" $true $(if ($busy) { '已在监听（可能已启动，正常）' } else { '空闲' })
}

# 输出
Write-Host ''
$results | Format-Table -AutoSize Check, OK, Detail | Out-String -Width 160 | Write-Host
$bad = @($results | Where-Object { -not $_.OK })
if ($bad.Count -eq 0) {
    Write-Host ("全部通过（{0} 项）" -f $results.Count) -ForegroundColor Green
    exit 0
}
Write-Host ("失败 {0} 项：" -f $bad.Count) -ForegroundColor Red
$bad | ForEach-Object { Write-Host ("  - {0}: {1}" -f $_.Check, $_.Detail) -ForegroundColor Red }
exit 1
