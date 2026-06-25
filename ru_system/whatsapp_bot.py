"""
whatsapp_bot.py — Lógica do bot WhatsApp via Twilio.

Funcionalidades:
  1. Pesquisa de satisfação: 30 min após o aluno entrar no RU, envia
     mensagem perguntando nota de 1 a 5.
  2. Alerta de pico: detecta movimento intenso e avisa os admins.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from config import (
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_API_KEY, TWILIO_API_SECRET,
    TWILIO_WHATSAPP_FROM, ADMIN_WHATSAPP_NUMEROS, LIMIAR_PICO, JANELA_PICO_MIN,
    INTERVALO_ALERTA_PICO_MIN,
)
from database import SessionLocal
from models import (
    Aluno, HistoricoRefeicao,
    SatisfacaoEnvio, SatisfacaoResposta, PicoMovimento,
)

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _twilio_habilitado() -> bool:
    return bool(TWILIO_ACCOUNT_SID and (TWILIO_AUTH_TOKEN or (TWILIO_API_KEY and TWILIO_API_SECRET)))


def _get_twilio_client():
    from twilio.rest import Client
    if TWILIO_AUTH_TOKEN:
        return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return Client(TWILIO_API_KEY, TWILIO_API_SECRET, TWILIO_ACCOUNT_SID)


def _formatar_numero(telefone: str) -> str:
    digits = "".join(c for c in telefone if c.isdigit())
    if not digits.startswith("55"):
        digits = "55" + digits
    return f"whatsapp:+{digits}"


def _enviar_mensagem(para: str, corpo: str) -> bool:
    if not _twilio_habilitado():
        logger.info("[BOT-SIMULADO] Para %s: %s", para, corpo)
        return True
    try:
        client = _get_twilio_client()
        client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=para, body=corpo)
        return True
    except Exception as exc:
        logger.error("Erro ao enviar WhatsApp para %s: %s", para, exc)
        return False


# ── Pesquisa de satisfação ────────────────────────────────────────────────────

async def agendar_pesquisa_satisfacao(aluno_id: int, refeicao_id: int, delay_segundos: int = 1800):
    """Aguarda `delay_segundos` e então envia a pesquisa de satisfação."""
    await asyncio.sleep(delay_segundos)

    db: Session = SessionLocal()
    try:
        aluno = db.query(Aluno).filter(Aluno.id == aluno_id, Aluno.ativo == True).first()
        if not aluno or not aluno.telefone:
            return

        ja_enviado = db.query(SatisfacaoEnvio).filter(
            SatisfacaoEnvio.refeicao_id == refeicao_id
        ).first()
        if ja_enviado:
            return

        numero = _formatar_numero(aluno.telefone)
        corpo = (
            f"Olá, {aluno.nome.split()[0]}! 👋\n\n"
            "Como foi sua refeição no RU hoje?\n\n"
            "Responda com um número:\n"
            "1 - Péssima\n"
            "2 - Ruim\n"
            "3 - Regular\n"
            "4 - Boa\n"
            "5 - Excelente"
        )

        if _enviar_mensagem(numero, corpo):
            envio = SatisfacaoEnvio(
                aluno_id=aluno_id,
                refeicao_id=refeicao_id,
                enviado_em=datetime.utcnow(),
            )
            db.add(envio)
            db.commit()
            logger.info("Pesquisa enviada para aluno_id=%s refeicao_id=%s", aluno_id, refeicao_id)
    except Exception as exc:
        logger.error("Erro ao enviar pesquisa satisfacao: %s", exc)
        db.rollback()
    finally:
        db.close()


def processar_resposta_satisfacao(from_number: str, body: str, db: Session):
    """Recebe mensagem do aluno e, se for nota 1-5, salva e agradece."""
    nota_str = body.strip()
    if nota_str not in {"1", "2", "3", "4", "5"}:
        return

    # Localiza o aluno pelo número (últimos 10 dígitos para flexibilidade)
    digits = "".join(c for c in from_number if c.isdigit())
    sufixo = digits[-10:]

    aluno = db.query(Aluno).filter(
        Aluno.telefone.ilike(f"%{sufixo}")
    ).first()
    if not aluno:
        return

    envio = (
        db.query(SatisfacaoEnvio)
        .filter(SatisfacaoEnvio.aluno_id == aluno.id, SatisfacaoEnvio.respondido == False)
        .order_by(SatisfacaoEnvio.enviado_em.desc())
        .first()
    )
    if not envio:
        return

    envio.respondido = True
    resposta = SatisfacaoResposta(
        envio_id=envio.id,
        aluno_id=aluno.id,
        nota=int(nota_str),
        respondido_em=datetime.utcnow(),
    )
    db.add(resposta)
    db.commit()

    numero = _formatar_numero(aluno.telefone)
    _enviar_mensagem(numero, "Obrigado pelo seu feedback! 😊 Sua opinião nos ajuda a melhorar o RU.")
    logger.info("Resposta satisfacao salva: aluno_id=%s nota=%s", aluno.id, nota_str)


# ── Pico de movimento ─────────────────────────────────────────────────────────

def verificar_e_registrar_pico(db: Session):
    """
    Conta entradas na janela de tempo. Se >= LIMIAR_PICO e não há alerta
    recente, registra o pico e notifica os admins via WhatsApp.
    """
    janela_inicio = datetime.utcnow() - timedelta(minutes=JANELA_PICO_MIN)
    count = db.query(HistoricoRefeicao).filter(
        HistoricoRefeicao.data_hora >= janela_inicio
    ).count()

    if count < LIMIAR_PICO:
        return

    # Evita alertas duplicados dentro do intervalo mínimo
    ultimo = (
        db.query(PicoMovimento)
        .order_by(PicoMovimento.registrado_em.desc())
        .first()
    )
    if ultimo:
        segundos_desde_ultimo = (datetime.utcnow() - ultimo.registrado_em).total_seconds()
        if segundos_desde_ultimo < INTERVALO_ALERTA_PICO_MIN * 60:
            return

    pico = PicoMovimento(
        acessos_na_janela=count,
        registrado_em=datetime.utcnow(),
        alerta_enviado=bool(ADMIN_WHATSAPP_NUMEROS),
    )
    db.add(pico)
    db.commit()

    if not ADMIN_WHATSAPP_NUMEROS:
        logger.warning("Pico detectado (%s acessos) mas ADMIN_WHATSAPP_NUMEROS não configurado.", count)
        return

    mensagem = (
        f"⚠️ *PICO DE MOVIMENTO — RU*\n\n"
        f"*{count}* entradas nos últimos *{JANELA_PICO_MIN} min*.\n"
        "Considere orientar os alunos a aguardarem ou redistribuir o fluxo."
    )
    for numero in ADMIN_WHATSAPP_NUMEROS:
        _enviar_mensagem(_formatar_numero(numero), mensagem)

    logger.info("Alerta de pico enviado: %s acessos", count)


def obter_status_pico(db: Session) -> dict:
    """Retorna situação atual de movimento para exibição no dashboard."""
    janela_inicio = datetime.utcnow() - timedelta(minutes=JANELA_PICO_MIN)
    count = db.query(HistoricoRefeicao).filter(
        HistoricoRefeicao.data_hora >= janela_inicio
    ).count()
    return {
        "acessos": count,
        "limiar": LIMIAR_PICO,
        "janela_min": JANELA_PICO_MIN,
        "em_pico": count >= LIMIAR_PICO,
    }
