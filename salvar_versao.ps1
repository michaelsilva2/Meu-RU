# Script para salvar versao do projeto MeuRU no git
# Uso: .\salvar_versao.ps1 "descricao da versao"

param(
    [string]$mensagem = "versao automatica"
)

$machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
$userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
$env:PATH = "$machinePath;$userPath"

Set-Location $PSScriptRoot

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$tag = Get-Date -Format "v-yyyy-MM-dd-HHmm"

git add -A
git commit -m "[$timestamp] $mensagem"
git tag $tag

Write-Host "Versao salva localmente com tag: $tag" -ForegroundColor Green
Write-Host "Para ver todas as versoes: git tag" -ForegroundColor DarkGray
Write-Host "Para voltar a uma versao: git checkout <tag>" -ForegroundColor DarkGray
