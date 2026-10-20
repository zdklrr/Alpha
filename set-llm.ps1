<#
  set-llm.ps1 - Configure any OpenAI-compatible LLM endpoint for Alpha.

  Writes OPENAI_API_KEY / OPENAI_BASE_URL / HERMES_MODEL into .env and can
  restart the service and run a connectivity self-check.

  Usage (ASCII only on purpose: PowerShell 5.1 cannot parse UTF-8-no-BOM
  scripts containing non-ASCII characters):

    .\set-llm.ps1 -Provider deepseek -ApiKey sk-xxxx
    .\set-llm.ps1 -Provider qwen -ApiKey sk-xxxx -Model qwen-plus
    .\set-llm.ps1 -Provider lmstudio -Model my-local-model -Restart -Test
    .\set-llm.ps1 -Provider custom -BaseUrl http://127.0.0.1:8000/v1 -ApiKey local -Model my-model

  Providers:
    deepseek    -> https://api.deepseek.com/v1                (model deepseek-chat)
    qwen        -> https://dashscope.aliyuncs.com/compatible-mode/v1 (model qwen-plus)
    glm         -> https://open.bigmodel.cn/api/paas/v4        (model glm-4-flash)
    siliconflow -> https://api.siliconflow.cn/v1               (model Qwen/Qwen2.5-7B-Instruct)
    openai      -> https://api.openai.com/v1                   (model gpt-4o-mini)
    lmstudio    -> http://127.0.0.1:1234/v1                    (model local-model)
    llamacpp    -> http://127.0.0.1:8080/v1                    (model local-model)
    vllm        -> http://127.0.0.1:8000/v1                    (model local-model)
    custom      -> you provide -BaseUrl and -Model
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('deepseek', 'qwen', 'glm', 'siliconflow', 'openai', 'lmstudio', 'llamacpp', 'vllm', 'custom')]
    [string]$Provider,

    [string]$ApiKey = '',
    [string]$BaseUrl = '',
    [string]$Model = '',

    [switch]$Restart,
    [switch]$Test
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $root '.env'

$defaults = @{
    deepseek    = @{ base = 'https://api.deepseek.com/v1';                       model = 'deepseek-chat' }
    qwen        = @{ base = 'https://dashscope.aliyuncs.com/compatible-mode/v1'; model = 'qwen-plus' }
    glm         = @{ base = 'https://open.bigmodel.cn/api/paas/v4';             model = 'glm-4-flash' }
    siliconflow = @{ base = 'https://api.siliconflow.cn/v1';                    model = 'Qwen/Qwen2.5-7B-Instruct' }
    openai      = @{ base = 'https://api.openai.com/v1';                        model = 'gpt-4o-mini' }
    lmstudio    = @{ base = 'http://127.0.0.1:1234/v1';                         model = 'local-model' }
    llamacpp    = @{ base = 'http://127.0.0.1:8080/v1';                         model = 'local-model' }
    vllm        = @{ base = 'http://127.0.0.1:8000/v1';                         model = 'local-model' }
    custom      = @{ base = '';                                                 model = '' }
}

$cfg = $defaults[$Provider]
$finalBase = if ($BaseUrl) { $BaseUrl.Trim() } else { $cfg.base }
$finalModel = if ($Model) { $Model.Trim() } else { $cfg.model }

if (-not $finalBase) {
    Write-Output "[set-llm] ERROR: provider '$Provider' needs -BaseUrl"
    exit 1
}
if (-not $finalModel) {
    Write-Output "[set-llm] ERROR: provider '$Provider' needs -Model"
    exit 1
}
$finalBase = $finalBase.TrimEnd('/')
if (-not $ApiKey) {
    if ($Provider -in @('lmstudio', 'llamacpp', 'vllm')) {
        $ApiKey = 'local'
    } else {
        Write-Output "[set-llm] ERROR: provider '$Provider' needs -ApiKey (use '' for local servers without auth)"
        exit 1
    }
}

if (-not (Test-Path $envFile)) {
    $template = Join-Path $root '.env.example'
    if (Test-Path $template) {
        Copy-Item $template $envFile
        Write-Output "[set-llm] created .env from .env.example"
    } else {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($envFile, '', $utf8NoBom)
        Write-Output "[set-llm] created empty .env"
    }
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$lines = [System.IO.File]::ReadAllLines($envFile, [System.Text.Encoding]::UTF8)

function Set-EnvLine {
    param([string[]]$Lines, [string]$Key, [string]$Value)
    $pattern = '^\s*' + [regex]::Escape($Key) + '\s*='
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match $pattern) {
            $Lines[$i] = "$Key=$Value"
            return ,$Lines
        }
    }
    return ,($Lines + "$Key=$Value")
}

$lines = Set-EnvLine -Lines $lines -Key 'OPENAI_API_KEY' -Value $ApiKey
$lines = Set-EnvLine -Lines $lines -Key 'OPENAI_BASE_URL' -Value $finalBase
$lines = Set-EnvLine -Lines $lines -Key 'HERMES_MODEL' -Value $finalModel

[System.IO.File]::WriteAllLines($envFile, $lines, $utf8NoBom)
Write-Output "[set-llm] provider  : $Provider"
Write-Output "[set-llm] base url  : $finalBase"
Write-Output "[set-llm] model     : $finalModel"
Write-Output "[set-llm] api key   : $(if ($ApiKey.Length -gt 8) { $ApiKey.Substring(0,4) + '...' + $ApiKey.Substring($ApiKey.Length-4) } else { '****' })"
Write-Output "[set-llm] updated   : $envFile"

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = 'python'
}

if ($Test) {
    Write-Output '[set-llm] running connectivity check ...'
    & $python (Join-Path $root 'scripts\test_llm.py')
    Write-Output "[set-llm] test exit code: $LASTEXITCODE"
}

if ($Restart) {
    Write-Output '[set-llm] restarting service ...'
    & (Join-Path $root 'restart.ps1')
}

Write-Output '[set-llm] done. Restart the service (.\restart.ps1) to apply the new settings.'
exit 0
