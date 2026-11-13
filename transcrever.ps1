# Transcrição dos vídeos das rádios numa janela própria, em prioridade baixa, ao lado da vigilância (que enche a fila).
# Uso (numa janela do PowerShell): powershell -ExecutionPolicy Bypass -File .\transcrever.ps1 -Horas 24
# Para parar: Ctrl+C nesta janela.
param([double]$Horas = 24, [string]$Modelo = '')

Set-Location $PSScriptRoot
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$host.UI.RawUI.WindowTitle = 'Radar - transcricao das radios (Ctrl+C para parar)'
# O python herda a prioridade: a vigilância e o uso do PC têm a vez.
(Get-Process -Id $PID).PriorityClass = 'BelowNormal'
$log = Join-Path $PSScriptRoot 'data\transcricao.log'

$extra = @()
if ($Modelo) { $extra = @('--modelo', $Modelo) }
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -m radar transcrever --horas $Horas @extra 2>&1 | ForEach-Object {
    $linha = "$_"
    $linha
    Add-Content -Path $log -Value $linha -Encoding UTF8
}
