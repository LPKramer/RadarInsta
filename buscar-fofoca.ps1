# Busca de páginas de fofoca em todos os municípios do TO, numa janela própria.
# Uso: powershell -ExecutionPolicy Bypass -File .\buscar-fofoca.ps1 -Conta principal
# Usa uma conta que não esteja na vigilância. Para parar: Ctrl+C; rodando de novo, continua de onde parou.
param([string]$Conta = 'principal')

Set-Location $PSScriptRoot
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$host.UI.RawUI.WindowTitle = 'Radar - busca de fofoca (Ctrl+C para parar)'
$log = Join-Path $PSScriptRoot 'data\fofoca.log'

& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -m radar buscar-fofoca --conta $Conta 2>&1 | ForEach-Object {
    $linha = "$_"
    $linha
    Add-Content -Path $log -Value $linha -Encoding UTF8
}
