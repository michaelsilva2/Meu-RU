"""
scheduler.py — Alertas automáticos de abertura e fechamento do RU.

Horários (horário de Brasília, seg–sex, exceto feriados):
  10:00 → Pergunta por WhatsApp se o aluno vai almoçar hoje
  11:00 → RU abriu para o almoço  + cardápio do dia
  13:30 → RU fecha o almoço em 30 min
  18:00 → Pergunta por WhatsApp se o aluno vai jantar hoje
  19:00 → RU abriu para o jantar  + cardápio da noite
  20:30 → RU fecha o jantar em 30 min
"""
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.exc import IntegrityError

from config import FERIADOS_NACIONAIS
from database import SessionLocal
from models import (
    Aluno, Cardapio, TipoRefeicao, HistoricoRecarga, StatusRecarga, AcessoQRCode,
    HistoricoRefeicao, SatisfacaoEnvio,
)
from email_service import enviar_emails_alerta_lote, enviar_email_avaliacao
from whatsapp_bot import enviar_confirmacoes_presenca, enviar_alerta_whatsapp
from auth import criar_token_jwt
import payments

logger = logging.getLogger(__name__)
BR_TZ = ZoneInfo("America/Sao_Paulo")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hoje_e_feriado() -> bool:
    return datetime.now(BR_TZ).date() in FERIADOS_NACIONAIS


def _get_emails() -> list[str]:
    db = SessionLocal()
    try:
        return [a.email for a in db.query(Aluno).filter(Aluno.ativo == True).all() if a.email]
    finally:
        db.close()


def _get_cardapio(tipo: TipoRefeicao) -> Cardapio | None:
    db = SessionLocal()
    try:
        hoje = datetime.now(BR_TZ).date()
        return db.query(Cardapio).filter(
            Cardapio.data == hoje,
            Cardapio.tipo == tipo,
        ).first()
    finally:
        db.close()


def _texto_cardapio(cardapio: Cardapio | None, refeicao: str) -> str:
    if not cardapio:
        return f"O cardápio do {refeicao} de hoje ainda não foi cadastrado."
    linhas = []
    if cardapio.prato_principal:
        linhas.append(f"🍽️ Prato principal: {cardapio.prato_principal}")
    if cardapio.arroz:
        linhas.append(f"🍚 Arroz: {cardapio.arroz}")
    if cardapio.feijao:
        linhas.append(f"🫘 Feijão: {cardapio.feijao}")
    if cardapio.legumes:
        linhas.append(f"🥦 Legumes: {cardapio.legumes}")
    if cardapio.salada:
        linhas.append(f"🥗 Salada: {cardapio.salada}")
    if cardapio.sobremesa:
        linhas.append(f"🍮 Sobremesa: {cardapio.sobremesa}")
    if cardapio.vegetariano:
        linhas.append(f"🌱 Vegetariano: {cardapio.vegetariano}")
    if cardapio.observacao:
        linhas.append(f"ℹ️ {cardapio.observacao}")
    return "\n".join(linhas) if linhas else "Cardápio disponível no restaurante."


def _disparar(emails: list[str], assunto: str, mensagem: str):
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, enviar_emails_alerta_lote, emails, assunto, mensagem)


def _disparar_whatsapp(mensagem: str):
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, enviar_alerta_whatsapp, mensagem)


# ── Jobs ──────────────────────────────────────────────────────────────────────

async def confirmar_presenca_almoco():
    if _hoje_e_feriado():
        return
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, enviar_confirmacoes_presenca, TipoRefeicao.almoco, datetime.now(BR_TZ).date())


async def confirmar_presenca_jantar():
    if _hoje_e_feriado():
        return
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, enviar_confirmacoes_presenca, TipoRefeicao.jantar, datetime.now(BR_TZ).date())


async def alerta_abertura_almoco():
    if _hoje_e_feriado():
        logger.info("Feriado — alerta almoço cancelado.")
        return
    cardapio = _get_cardapio(TipoRefeicao.almoco)
    menu = _texto_cardapio(cardapio, "almoço")
    mensagem = f"🍽️ O RU está aberto para o almoço!\n\n⏰ Horário: 11h às 14h\n\n{menu}"
    mensagem_whatsapp = (
        "🍽️ O RU está aberto para o almoço!\n\n⏰ Horário: 11h às 14h\n\n"
        "📋 Digite *CARDÁPIO* para ver o cardápio de hoje."
    )
    emails = _get_emails()
    _disparar(emails, "🍽️ RU aberto — Almoço", mensagem)
    _disparar_whatsapp(mensagem_whatsapp)
    logger.info("Alerta abertura almoço disparado para %d alunos.", len(emails))


async def alerta_fechamento_almoco():
    if _hoje_e_feriado():
        return
    mensagem = "⏰ O RU fecha para o almoço em 30 minutos (às 14h).\n\nSe ainda não almoçou, venha logo!"
    emails = _get_emails()
    _disparar(emails, "⏰ RU fecha em 30 min — Almoço", mensagem)
    _disparar_whatsapp(mensagem)
    logger.info("Alerta fechamento almoço disparado.")


async def alerta_abertura_jantar():
    if _hoje_e_feriado():
        logger.info("Feriado — alerta jantar cancelado.")
        return
    cardapio = _get_cardapio(TipoRefeicao.jantar)
    menu = _texto_cardapio(cardapio, "jantar")
    mensagem = f"🌙 O RU está aberto para o jantar!\n\n⏰ Horário: 19h às 21h\n\n{menu}"
    mensagem_whatsapp = (
        "🌙 O RU está aberto para o jantar!\n\n⏰ Horário: 19h às 21h\n\n"
        "📋 Digite *CARDÁPIO* para ver o cardápio de hoje."
    )
    emails = _get_emails()
    _disparar(emails, "🌙 RU aberto — Jantar", mensagem)
    _disparar_whatsapp(mensagem_whatsapp)
    logger.info("Alerta abertura jantar disparado para %d alunos.", len(emails))


async def alerta_fechamento_jantar():
    if _hoje_e_feriado():
        return
    mensagem = "⏰ O RU fecha para o jantar em 30 minutos (às 21h).\n\nÚltima chance de jantar hoje!"
    emails = _get_emails()
    _disparar(emails, "⏰ RU fecha em 30 min — Jantar", mensagem)
    _disparar_whatsapp(mensagem)
    logger.info("Alerta fechamento jantar disparado.")


# ── Pagamentos Pix: fallback de confirmação + expiração ────────────────────────

async def confirmar_pagamentos_pendentes():
    """
    Fallback para o caso do aluno fechar a tela antes do polling do frontend
    aprovar a cobrança sozinho. Garante que toda cobrança Pix simulada acabe
    confirmada (ou expirada) mesmo sem ninguém olhando a tela.
    """
    db = SessionLocal()
    try:
        agora = datetime.utcnow()
        pendentes = db.query(HistoricoRecarga).filter(
            HistoricoRecarga.status == StatusRecarga.pendente,
            HistoricoRecarga.gateway_payment_id.isnot(None),
            HistoricoRecarga.expira_em > agora,
        ).all()
        for recarga in pendentes:
            try:
                payments.confirmar_pix_simulado(db, recarga)
            except Exception:
                logger.exception("Falha ao confirmar pagamento pendente id=%s", recarga.gateway_payment_id)
    finally:
        db.close()


async def expirar_pix_pendentes():
    """Marca como expiradas as cobranças Pix que passaram do prazo sem pagamento."""
    db = SessionLocal()
    try:
        agora = datetime.utcnow()
        expirados = db.query(HistoricoRecarga).filter(
            HistoricoRecarga.status == StatusRecarga.pendente,
            HistoricoRecarga.expira_em.isnot(None),
            HistoricoRecarga.expira_em <= agora,
        ).all()
        for recarga in expirados:
            recarga.status = StatusRecarga.expirado
            recarga.atualizado_em = agora
        if expirados:
            db.commit()
            logger.info("%d cobrança(s) Pix expirada(s).", len(expirados))
    finally:
        db.close()


# ── QR code de acesso: limpeza de tokens antigos ────────────────────────────

async def limpar_qrcodes_antigos():
    """Apaga tokens de QR vencidos há mais de 1h — cada tela de QR gera um novo a cada ~20s."""
    db = SessionLocal()
    try:
        limite = datetime.utcnow() - timedelta(hours=1)
        removidos = db.query(AcessoQRCode).filter(AcessoQRCode.expira_em < limite).delete()
        if removidos:
            db.commit()
            logger.info("%d token(s) de QR code antigo(s) removido(s).", removidos)
    finally:
        db.close()


# ── Lembrete de avaliação por e-mail (30 min após a refeição) ─────────────────
# Roda a cada 5 min e varre uma janela de 30-40 min atrás (em vez de um sleep
# de 30 min por refeição) para sobreviver a reinícios do processo — um sleep
# em memória seria perdido se o worker reiniciar antes de completar.

async def enviar_lembretes_avaliacao_email():
    db = SessionLocal()
    try:
        agora = datetime.utcnow()
        refeicoes = (
            db.query(HistoricoRefeicao)
            .join(Aluno, Aluno.id == HistoricoRefeicao.aluno_id)
            .outerjoin(SatisfacaoEnvio, SatisfacaoEnvio.refeicao_id == HistoricoRefeicao.id)
            .filter(
                HistoricoRefeicao.data_hora <= agora - timedelta(minutes=30),
                HistoricoRefeicao.data_hora >= agora - timedelta(minutes=40),
                SatisfacaoEnvio.id.is_(None),
                Aluno.ativo == True,
                Aluno.email.isnot(None),
            )
            .all()
        )
        loop = asyncio.get_event_loop()
        for refeicao in refeicoes:
            aluno = refeicao.aluno
            envio = SatisfacaoEnvio(
                aluno_id=aluno.id,
                refeicao_id=refeicao.id,
                enviado_em=agora,
                etapa="email",
            )
            db.add(envio)
            try:
                db.flush()
            except IntegrityError:
                # Corrida com o bot do WhatsApp (mesma refeição, aluno tem telefone
                # e email): quem chegou primeiro no unique(refeicao_id) leva a pesquisa.
                db.rollback()
                continue

            token = criar_token_jwt({"sub": str(envio.id), "tipo": "avaliacao_email"}, horas=48)
            tipo_texto = "almoço" if refeicao.tipo == TipoRefeicao.almoco else "jantar"

            enviado = await loop.run_in_executor(
                None, enviar_email_avaliacao, aluno.email, aluno.nome, tipo_texto, token
            )
            if enviado:
                db.commit()
            else:
                db.rollback()
                logger.warning(
                    "Falha ao enviar lembrete de avaliação para aluno_id=%s refeicao_id=%s — tenta de novo no próximo ciclo",
                    aluno.id, refeicao.id,
                )
    finally:
        db.close()


# ── Criação do scheduler ──────────────────────────────────────────────────────

def criar_scheduler() -> AsyncIOScheduler:
    # misfire_grace_time padrão do APScheduler é 1s — no free tier do Render
    # os jobs consistentemente disparam ~1.09s atrasados (jitter do event loop),
    # o que fazia TODO job de intervalo ser pulado (nunca executado) silenciosamente.
    scheduler = AsyncIOScheduler(timezone=BR_TZ, job_defaults={"misfire_grace_time": 60})
    scheduler.add_job(
        confirmar_presenca_almoco,
        CronTrigger(hour=10, minute=0, day_of_week="mon-fri", timezone=BR_TZ),
        id="confirmar_presenca_almoco",
    )
    scheduler.add_job(
        confirmar_presenca_jantar,
        CronTrigger(hour=18, minute=0, day_of_week="mon-fri", timezone=BR_TZ),
        id="confirmar_presenca_jantar",
    )
    scheduler.add_job(
        alerta_abertura_almoco,
        CronTrigger(hour=11, minute=0, day_of_week="mon-fri", timezone=BR_TZ),
        id="abertura_almoco",
    )
    scheduler.add_job(
        alerta_fechamento_almoco,
        CronTrigger(hour=13, minute=30, day_of_week="mon-fri", timezone=BR_TZ),
        id="fechamento_almoco",
    )
    scheduler.add_job(
        alerta_abertura_jantar,
        CronTrigger(hour=19, minute=0, day_of_week="mon-fri", timezone=BR_TZ),
        id="abertura_jantar",
    )
    scheduler.add_job(
        alerta_fechamento_jantar,
        CronTrigger(hour=20, minute=30, day_of_week="mon-fri", timezone=BR_TZ),
        id="fechamento_jantar",
    )
    scheduler.add_job(
        confirmar_pagamentos_pendentes,
        IntervalTrigger(minutes=2),
        id="confirmar_pagamentos_pendentes",
    )
    scheduler.add_job(
        expirar_pix_pendentes,
        IntervalTrigger(minutes=5),
        id="expirar_pix_pendentes",
    )
    scheduler.add_job(
        limpar_qrcodes_antigos,
        IntervalTrigger(minutes=30),
        id="limpar_qrcodes_antigos",
    )
    scheduler.add_job(
        enviar_lembretes_avaliacao_email,
        IntervalTrigger(minutes=5),
        id="enviar_lembretes_avaliacao_email",
    )
    return scheduler
