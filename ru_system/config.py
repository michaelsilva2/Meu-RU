"""
Configurações da aplicação carregadas do arquivo .env
"""
import os
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env (se existir)
load_dotenv()

# Chave secreta para assinar tokens JWT
SECRET_KEY = os.getenv("SECRET_KEY", "chave_dev_insegura_mude_em_producao_123456789")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# Expirações dos tokens (em horas)
EXPIRACAO_TOKEN_ALUNO_HORAS = 8
EXPIRACAO_TOKEN_ADMIN_HORAS = 12
EXPIRACAO_TOKEN_RECUPERACAO_HORAS = 1

# URL base da aplicação
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

# Banco de dados
# Para migrar para PostgreSQL: DATABASE_URL=postgresql://user:pass@host:5432/ru_db
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ru.db")

# Email (SMTP)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "noreply@ru.edu.br")

# Chave para sessões / CSRF (itsdangerous)
SESSION_SECRET_KEY = os.getenv("SECRET_KEY", SECRET_KEY)

# ── Tabela de preços por categoria ──────────────────────────────────────────
from decimal import Decimal as _D
PRECOS_REFEICAO: dict = {
    "bolsista":   _D("0.00"),
    "subsidiado": _D("4.00"),
    "integral":   _D("6.00"),
    "externo":    _D("16.00"),
}

# ── Feriados nacionais 2026 (formato YYYY-MM-DD) ─────────────────────────────
from datetime import date as _date
FERIADOS_NACIONAIS: set[_date] = {
    _date(2026, 1,  1),   # Ano Novo
    _date(2026, 2, 16),   # Carnaval (segunda)
    _date(2026, 2, 17),   # Carnaval (terça)
    _date(2026, 4,  3),   # Sexta-Feira Santa
    _date(2026, 4, 21),   # Tiradentes
    _date(2026, 5,  1),   # Dia do Trabalho
    _date(2026, 6,  4),   # Corpus Christi
    _date(2026, 9,  7),   # Independência
    _date(2026, 10, 12),  # N.Sra. Aparecida
    _date(2026, 11,  2),  # Finados
    _date(2026, 11, 15),  # Proclamação da República
    _date(2026, 11, 20),  # Consciência Negra
    _date(2026, 12, 25),  # Natal
}

# Rate limiting: máx tentativas de login por IP
RATE_LIMIT_MAX_TENTATIVAS = 5
RATE_LIMIT_JANELA_MINUTOS = 15

# ── QR code de acesso ────────────────────────────────────────────────────────
# Curto o suficiente pra inviabilizar print/reenvio, longo o suficiente pra dar
# tempo de escanear na fila.
QRCODE_TTL_SEGUNDOS = int(os.getenv("QRCODE_TTL_SEGUNDOS", "20"))

# ── WhatsApp / Twilio ────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_API_KEY       = os.getenv("TWILIO_API_KEY", "")
TWILIO_API_SECRET    = os.getenv("TWILIO_API_SECRET", "")
# Sandbox: "whatsapp:+14155238886"  |  Produção: seu número aprovado
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

# Números de WhatsApp dos admins para receber alertas de pico (separados por vírgula)
# Ex: ADMIN_WHATSAPP_NUMEROS=11999990000,11988880000
_numeros_raw = os.getenv("ADMIN_WHATSAPP_NUMEROS", "")
ADMIN_WHATSAPP_NUMEROS: list[str] = [n.strip() for n in _numeros_raw.split(",") if n.strip()]

# ── Pagamentos (Pix / cartão simulados) ─────────────────────────────────────
# Não há gateway real: o Pix e o cartão são simulados localmente em payments.py.

# Minutos até uma cobrança Pix expirar se não for paga
PIX_EXPIRACAO_MINUTOS = int(os.getenv("PIX_EXPIRACAO_MINUTOS", "30"))

# Segundos até uma cobrança Pix pendente se aprovar sozinha (simula o tempo
# de um aluno abrir o app do banco e escanear o QR code)
PAGAMENTO_SIMULADO_DELAY_SEGUNDOS = int(os.getenv("PAGAMENTO_SIMULADO_DELAY_SEGUNDOS", "8"))

# Limites de valor para recarga self-service do aluno
RECARGA_VALOR_MIN = _D(os.getenv("RECARGA_VALOR_MIN", "1.00"))
RECARGA_VALOR_MAX = _D(os.getenv("RECARGA_VALOR_MAX", "500.00"))

# ── Pico de movimento ────────────────────────────────────────────────────────
# Quantidade mínima de entradas na janela para considerar pico
LIMIAR_PICO: int = int(os.getenv("LIMIAR_PICO", "15"))
# Janela de tempo (em minutos) para contar as entradas
JANELA_PICO_MIN: int = int(os.getenv("JANELA_PICO_MIN", "20"))
# Intervalo mínimo entre alertas de pico (em minutos) para evitar spam
INTERVALO_ALERTA_PICO_MIN: int = int(os.getenv("INTERVALO_ALERTA_PICO_MIN", "15"))

# ── Google Gemini (leitura do cardápio a partir de foto/story) ──────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
