# Descoberta das rádios do TO numa janela própria, com uma conta fora da vigilância (radios.py). Continua de onde a
# anterior parou; no fim, a DeepSeek classifica as contas novas e a planilha data/radios_propostas.xlsx é refeita.
# Uso (numa janela do PowerShell): powershell -ExecutionPolicy Bypass -File .\descobrir-radios.ps1 -Orcamento 200
param([int]$Orcamento = 200, [string]$Conta = 'principal')

Set-Location $PSScriptRoot
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$host.UI.RawUI.WindowTitle = 'Radar - descoberta das radios'
$log = Join-Path $PSScriptRoot 'data\radios.log'

& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -m radar radios-propor --descobrir --conta $Conta --orcamento $Orcamento 2>&1 | ForEach-Object {
    $linha = "$_"
    $linha
    Add-Content -Path $log -Value $linha -Encoding UTF8
}
