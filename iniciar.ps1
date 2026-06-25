# Script de inicializacao do projeto MeuRU
# Inicia o servidor FastAPI em background (sem terminal) e abre no Opera GX

$machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
$userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
$env:PATH = "$machinePath;$userPath"

# Encerra instancia anterior se estiver rodando
$processos = Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -like "*uvicorn*main:app*" }
if ($processos) {
    $processos | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "Instancia anterior encerrada." -ForegroundColor Yellow
    Start-Sleep -Seconds 1
}

$ruPath = "$PSScriptRoot\ru_system"

Write-Host "Iniciando servidor MeuRU em background..." -ForegroundColor Green
Start-Process "wscript.exe" -ArgumentList "`"$PSScriptRoot\servidor_background.vbs`"" -WindowStyle Hidden

Write-Host "Aguardando servidor subir..." -ForegroundColor Yellow
Start-Sleep -Seconds 3

Write-Host "Abrindo Opera GX..." -ForegroundColor Green
Start-Process "C:\Users\Michael\AppData\Local\Programs\Opera GX\opera.exe" -ArgumentList "http://localhost:8000"

Write-Host "Servidor rodando em background em http://localhost:8000" -ForegroundColor Cyan
Write-Host "Para parar: Get-WmiObject Win32_Process | Where-Object { `$_.CommandLine -like '*uvicorn*' } | ForEach-Object { Stop-Process -Id `$_.ProcessId -Force }" -ForegroundColor DarkGray
