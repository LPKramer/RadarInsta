# Vigilância contínua do radar numa janela própria, sem depender do VS Code nem do Claude.
# Uso (numa janela do PowerShell): powershell -ExecutionPolicy Bypass -File .\vigiar.ps1 -Horas 24
# Para parar: Ctrl+C nesta janela, ou em outra: python -m radar vigiar --parar
# -Contas conta2,conta3 limita às contas dadas (sem ela: todas as logadas, e as novas entram sozinhas).
param([double]$Horas = 24, [string]$Contas = '')

Set-Location $PSScriptRoot
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$host.UI.RawUI.WindowTitle = 'Radar - vigilancia (Ctrl+C para parar)'
$log = Join-Path $PSScriptRoot 'data\vigia.log'

$extra = @()
if ($Contas) { $extra = @('--contas', $Contas) }
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -m radar vigiar --horas $Horas @extra 2>&1 | ForEach-Object {
    $linha = "$_"
    $linha
    Add-Content -Path $log -Value $linha -Encoding UTF8
}
