# Agenda a vigia no Agendador de Tarefas do Windows: duas voltas por dia por todas as páginas monitoradas, com todas as
# contas do Instagram logadas (uma conta logada depois entra na volta seguinte). Cada volta é o vigiar.ps1 -Volta.
# Uso (numa janela do PowerShell, na pasta do projeto):
#   powershell -ExecutionPolicy Bypass -File .\agendar.ps1                          cria ou atualiza (07:00 e 19:00)
#   powershell -ExecutionPolicy Bypass -File .\agendar.ps1 -Horarios 06:30,18:30    outros horários
#   powershell -ExecutionPolicy Bypass -File .\agendar.ps1 -Estado                  última e próxima volta
#   powershell -ExecutionPolicy Bypass -File .\agendar.ps1 -Desligar                desliga e para a volta em curso
#   powershell -ExecutionPolicy Bypass -File .\agendar.ps1 -Ligar                   religa
# A tarefa roda com você logado no Windows, porque o Edge precisa da área de trabalho. Se o PC estiver desligado ou sem
# ninguém logado na hora, a volta roda assim que possível; se a anterior ainda estiver rodando, a nova não abre.
param([string[]]$Horarios = @('07:00', '19:00'), [double]$LimiteHoras = 6, [switch]$Estado, [switch]$Desligar,
      [switch]$Ligar)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$nome = 'Radar - vigia'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

function Mostrar-Estado {
    $tarefa = Get-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue
    if (-not $tarefa) { 'Sem agenda. Para criar: .\agendar.ps1'; return }
    $info = $tarefa | Get-ScheduledTaskInfo
    $horas = $tarefa.Triggers | ForEach-Object { ([datetime]$_.StartBoundary).ToString('HH:mm') }
    "Agenda '$nome': $($tarefa.State), voltas às $($horas -join ' e ')"
    if ($info.LastRunTime -and $info.LastRunTime.Year -gt 2000) {
        "Última volta: $($info.LastRunTime.ToString('dd/MM HH:mm')) (código $($info.LastTaskResult))"
    }
    if ($info.NextRunTime) { "Próxima volta: $($info.NextRunTime.ToString('dd/MM HH:mm'))" }
}

if ($Estado) { Mostrar-Estado; exit }
if ($Desligar) {
    Disable-ScheduledTask -TaskName $nome | Out-Null
    & $python -m radar vigiar --parar
    'Agenda desligada. Para religar: .\agendar.ps1 -Ligar'
    exit
}
if ($Ligar) { Enable-ScheduledTask -TaskName $nome | Out-Null; Mostrar-Estado; exit }

# O caminho com espaço ("Inova IA") quebra o -File: a pasta de trabalho é a do projeto e o .ps1 vai relativo.
$acao = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $PSScriptRoot `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File vigiar.ps1 -Volta -Horas $LimiteHoras"
$gatilhos = @($Horarios | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours ($LimiteHoras + 1))
$usuario = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilhos -Settings $ajustes -Principal $usuario `
    -Description 'Radar de Reputação: uma volta da vigia por todas as páginas monitoradas (vigiar.ps1 -Volta).' `
    -Force | Out-Null
Mostrar-Estado
