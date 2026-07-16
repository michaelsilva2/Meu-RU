"""
routers/webhook.py — Webhooks de serviços externos (Twilio).

WhatsApp (Twilio):
  Webhook URL: https://<seu-dominio>/webhook/whatsapp
  Método: HTTP POST
"""
import logging

from fastapi import APIRouter, Form, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from database import get_db
from whatsapp_bot import processar_resposta_satisfacao

router = APIRouter(prefix="/webhook", tags=["webhook"])
logger = logging.getLogger(__name__)


@router.post("/whatsapp", response_class=PlainTextResponse)
async def receber_mensagem_whatsapp(
    From: str = Form(...),
    Body: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Endpoint chamado pelo Twilio sempre que um aluno envia mensagem.
    Resposta vazia = Twilio não envia nenhum reply automático (o bot
    já responde diretamente via API no processar_resposta_satisfacao).
    """
    logger.info("Mensagem recebida de %s: %s", From, Body)
    processar_resposta_satisfacao(From, Body, db)
    return ""
