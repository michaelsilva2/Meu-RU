# MeuRU — Sistema de Créditos do Restaurante Universitário

## Ao abrir este projeto, SEMPRE:
1. Atualizar PATH com variáveis do sistema
2. Iniciar o servidor: `cd ru_system && python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000`
3. Abrir no Opera GX: `C:\Users\Michael\AppData\Local\Programs\Opera GX\opera.exe http://localhost:8000`

Ou executar o script: `.\iniciar.ps1`

## Salvar versão
Executar: `.\salvar_versao.ps1 "descricao da mudanca"`

## Estrutura
- `ru_system/` — backend FastAPI (Python)
- `ru_system/main.py` — entry point, rodar com uvicorn
- `ru_system/routers/` — rotas (admin, aluno, auth, webhook)
- `ru_system/templates/` — HTML (Jinja2)
- `ru_system/whatsapp_bot.py` — bot WhatsApp via Twilio
- `ru_system/.env` — variáveis de ambiente (não commitar)
- `iniciar.ps1` — inicia servidor + abre Opera GX
- `salvar_versao.ps1` — salva versão no git com tag

## Credenciais de teste
- Admin: admin@ru.edu.br / Admin@2024
- Aluno: matrícula 2024001 / Aluno@2024

## Bot WhatsApp (Twilio)
Configurar no `.env`:
- TWILIO_ACCOUNT_SID
- TWILIO_AUTH_TOKEN
- TWILIO_WHATSAPP_FROM
- ADMIN_WHATSAPP_NUMEROS (números dos admins, sem +55, separados por vírgula)

Webhook Twilio: POST https://<dominio>/webhook/whatsapp
