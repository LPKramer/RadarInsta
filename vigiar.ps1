# Vigilância contínua do radar numa janela própria, sem depender do VS Code nem do Claude.
# Uso (numa janela do PowerShell): powershell -ExecutionPolicy Bypass -File .\vigiar.ps1 -Horas 24
# Para parar: Ctrl+C nesta janela, ou em outra: python -m radar vigiar --parar
# -Contas conta2,conta3 limita às contas dadas (sem ela: todas as logadas, e as novas entram sozinhas).
# -Volta faz uma volta só: passa uma vez por todas as páginas e termina (-Horas vira o limite). É o que o agendar.ps1
# roda duas vezes por dia.
param([double]$Horas = 24, [string]$Contas = '', [switch]$Volta)

Set-Location $PSScriptRoot
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$host.UI.RawUI.WindowTitle = 'Radar - vigilancia (Ctrl+C para parar)'
$log = Join-Path $PSScriptRoot 'data\vigia.log'

function Registrar([string]$linha) {
    $linha
    Add-Content -Path $log -Value $linha -Encoding UTF8
}

$quando = Get-Date -Format 'yyyy-MM-dd HH:mm'
# Duas vigias ao mesmo tempo disputariam os perfis do Edge das contas: a segunda não sobe.
$rodando = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $_.CommandLine -match '-m radar vigiar' -and $_.CommandLine -notmatch '--parar|--dry-run' }
if ($rodando) {
    Registrar "=== $quando vigia$(if ($Volta) { ' (volta)' }) não subiu: já há uma vigia rodando (processo $($rodando[0].ProcessId))"
    exit
}
Registrar "=== $quando vigia$(if ($Volta) { ' (volta)' } else { " por $Horas h" })"

$extra = @()
if ($Contas) { $extra += @('--contas', $Contas) }
if ($Volta) { $extra += '--volta' }
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -m radar vigiar --horas $Horas @extra 2>&1 | ForEach-Object {
    Registrar "$_"
}
