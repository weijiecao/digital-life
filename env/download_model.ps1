<#
.SYNOPSIS
  下载 BGE 中文小模型 bge-small-zh-v1.5（默认 ModelScope 源，国内可直连）。

.DESCRIPTION
  HuggingFace 直连在国内常不可达；ModelScope 的 AI-ModelScope/bge-small-zh-v1.5 可直连。
  只取 sentence-transformers 加载所需的 11 个文件（约 92 MB），不含 PyTorch 权重副本。
  已存在的文件默认跳过，加 -Force 重新下载。

.PARAMETER Dest
  模型落地目录，默认 testbed\models\bge-small-zh-v1.5。

.PARAMETER Force
  重新下载已存在的文件。

.EXAMPLE
  .\env\download_model.ps1
  .\env\download_model.ps1 -Dest D:\models\bge-small-zh-v1.5
#>
[CmdletBinding()]
param(
    [string]$Dest = (Join-Path (Split-Path $PSScriptRoot -Parent) 'testbed\models\bge-small-zh-v1.5'),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Base = 'https://www.modelscope.cn/models/AI-ModelScope/bge-small-zh-v1.5/resolve/master'
$Files = @(
    'config.json',
    'config_sentence_transformers.json',
    'modules.json',
    'sentence_bert_config.json',
    'special_tokens_map.json',
    'tokenizer.json',
    'tokenizer_config.json',
    'vocab.txt',
    '1_Pooling/config.json',
    'model.safetensors'
)

New-Item -ItemType Directory -Force -Path $Dest, (Join-Path $Dest '1_Pooling') | Out-Null
$failed = @()
foreach ($f in $Files) {
    $out = Join-Path $Dest $f
    if ((Test-Path $out) -and -not $Force) {
        Write-Host "  [skip] $f"
        continue
    }
    $url = "$Base/$f"
    Write-Host "  [get ] $f"
    try {
        Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing -TimeoutSec 300
    } catch {
        $failed += $f
        Write-Host "  [fail] $f : $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

$weights = Join-Path $Dest 'model.safetensors'
if (-not (Test-Path $weights)) { $failed += 'model.safetensors' }
else {
    $mb = [math]::Round((Get-Item $weights).Length / 1MB, 1)
    if ($mb -lt 50) { $failed += "model.safetensors(${mb}MB 偏小)" }
    else { Write-Host "  [ok  ] model.safetensors ${mb} MB" -ForegroundColor Green }
}

if ($failed.Count -gt 0) {
    Write-Host "`n下载未完成：$($failed -join ', ')" -ForegroundColor Red
    Write-Host '可重试本脚本（已下载文件会跳过）；若 ModelScope 也不可达，请手动放置同名文件。'
    exit 1
}

Write-Host "`n模型就绪：$Dest" -ForegroundColor Green
Write-Host '提示：modules.json 里的 2_Normalize 目录上游未提供，sentence-transformers 5.x 可正常加载。'
exit 0
