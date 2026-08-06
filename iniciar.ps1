# Script de inicializacao do projeto MeuRU
# Inicia o servidor FastAPI em background (sem terminal) e abre no navegador
# Portatil: nao depende de caminhos fixos de usuario/PC

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

# Garante que as dependencias Python estao instaladas (necessario em PC novo)
python -c "import fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Dependencias nao encontradas, instalando via pip..." -ForegroundColor Yellow
    python -m pip install -r "$ruPath\requirements.txt"
}

Write-Host "Iniciando servidor MeuRU em background..." -ForegroundColor Green
Start-Process "wscript.exe" -ArgumentList "`"$PSScriptRoot\servidor_background.vbs`"" -WindowStyle Hidden

Write-Host "Aguardando servidor subir..." -ForegroundColor Yellow
Start-Sleep -Seconds 3

Write-Host "Abrindo navegador..." -ForegroundColor Green
$operaPaths = @(
    "$env:LOCALAPPDATA\Programs\Opera GX\opera.exe",
    "$env:ProgramFiles\Opera GX\opera.exe"
)
$opera = $operaPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($opera) {
    Start-Process $opera -ArgumentList "http://localhost:8000"
} else {
    # Sem Opera GX instalado: abre no navegador padrao do sistema
    Start-Process "http://localhost:8000"
}

Write-Host "Servidor rodando em background em http://localhost:8000" -ForegroundColor Cyan
Write-Host "Para parar: Get-WmiObject Win32_Process | Where-Object { `$_.CommandLine -like '*uvicorn*' } | ForEach-Object { Stop-Process -Id `$_.ProcessId -Force }" -ForegroundColor DarkGray
