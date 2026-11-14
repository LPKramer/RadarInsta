# Publica o painel no GitHub Pages (https://lpkramer.github.io/RadarRep/) de uma vez só: consolida as notícias (a mesma
# notícia, com a DeepSeek), gera o site na pasta do RadarRep (ramo gh-pages), confere que ele saiu inteiro e só então
# faz o commit e o push. Se algo falhar, nada é publicado.
# Uso (numa janela do PowerShell, na pasta do projeto):
#   powershell -ExecutionPolicy Bypass -File .\publicar-site.ps1             gera, confere e publica
#   powershell -ExecutionPolicy Bypass -File .\publicar-site.ps1 -SemPush     gera e confere, sem publicar
# A geração leva alguns minutos sem mostrar nada: espere a mensagem final.
param([string]$Pasta = 'C:\Users\Luiz\Downloads\RadarRep\site', [switch]$SemPush)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$repositorio = 'https://github.com/LPKramer/RadarRep.git'

function Parar([string]$motivo) {
    Write-Host "NADA FOI PUBLICADO: $motivo" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $Pasta '.git'))) { Parar "a pasta $Pasta não é o repositório do site." }
$ramo = git -C $Pasta branch --show-current
if ($ramo -ne 'gh-pages') { Parar "a pasta $Pasta está no ramo '$ramo', e o site fica no gh-pages." }

$inicio = Get-Date
"$(Get-Date -Format 'HH:mm') Consolidando as notícias e gerando o site em $Pasta (alguns minutos)..."
& $python -m radar site --saida $Pasta
if ($LASTEXITCODE -ne 0) { Parar 'a geração do site falhou (veja a mensagem acima).' }

# O site saiu inteiro? Uma ficha por menção, a página de contas, a busca e as fileiras.
$status = Get-Content (Join-Path $Pasta 'api\status.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$fichas = @(Get-ChildItem (Join-Path $Pasta 'api\post') -File -ErrorAction SilentlyContinue).Count
$fileiras = @(Get-ChildItem (Join-Path $Pasta 'api\linha') -File -ErrorAction SilentlyContinue).Count
$faltam = @('api\itens.json', 'api\contas.json', 'api\busca.json', 'api\inicio.json') |
    Where-Object { -not (Test-Path (Join-Path $Pasta $_)) }
if ($fichas -ne $status.mencoes -or $fileiras -eq 0 -or $faltam) {
    Parar "o site saiu incompleto ($fichas fichas para $($status.mencoes) menções, $fileiras fileiras; faltam: $($faltam -join ', '))."
}
"Site conferido: $($status.mencoes) menções, $fichas fichas e $fileiras fileiras, em $([int]((Get-Date) - $inicio).TotalMinutes) min."
if ($SemPush) { 'Sem publicar (-SemPush).'; exit }

git -C $Pasta add -A
if (-not (git -C $Pasta status --porcelain)) { 'Nada mudou desde a última publicação.'; exit }
git -C $Pasta commit -q -m 'Dados atualizados'
if ($LASTEXITCODE -ne 0) { Parar 'o commit falhou.' }
git -C $Pasta push $repositorio gh-pages
if ($LASTEXITCODE -ne 0) { Parar 'o push falhou; o commit ficou só no PC. Rode o script de novo.' }
'Publicado. O GitHub Pages atualiza em 1 a 2 minutos: https://lpkramer.github.io/RadarRep/'
