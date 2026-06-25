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
    "aluno":      _D("6.00"),
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

# ── Pico de movimento ────────────────────────────────────────────────────────
# Quantidade mínima de entradas na janela para considerar pico
LIMIAR_PICO: int = int(os.getenv("LIMIAR_PICO", "15"))
# Janela de tempo (em minutos) para contar as entradas
JANELA_PICO_MIN: int = int(os.getenv("JANELA_PICO_MIN", "10"))
# Intervalo mínimo entre alertas de pico (em minutos) para evitar spam
INTERVALO_ALERTA_PICO_MIN: int = int(os.getenv("INTERVALO_ALERTA_PICO_MIN", "15"))
