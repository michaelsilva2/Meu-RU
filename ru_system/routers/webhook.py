"""
routers/webhook.py — Webhooks de serviços externos (Twilio, Mercado Pago).

WhatsApp (Twilio):
  Webhook URL: https://<seu-dominio>/webhook/whatsapp
  Método: HTTP POST

Mercado Pago (Pix):
  Webhook URL: https://<seu-dominio>/webhook/mercadopago
  Configurado no painel do Mercado Pago > Suas integrações > Webhooks
"""
import logging

from fastapi import APIRouter, Form, Depends, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from whatsapp_bot import processar_resposta_satisfacao
import payments

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


@router.post("/mercadopago")
async def receber_notificacao_mercadopago(request: Request, db: Session = Depends(get_db)):
    """
    Notificação de pagamento do Mercado Pago. Aceita tanto o formato novo
    (corpo JSON `{"type": "payment", "data": {"id": "..."}}`) quanto o
    formato legado (`?topic=payment&id=...`).

    Sempre revalida o pagamento diretamente na API do MP antes de creditar
    — nunca confia no corpo da notificação.
    """
    params = request.query_params
    payment_id = params.get("data.id") or params.get("id")

    if not payment_id:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if body.get("type") == "payment":
            payment_id = str(body.get("data", {}).get("id", ""))

    if not payment_id:
        return JSONResponse({"ignorado": True}, status_code=200)

    assinatura_ok = payments.validar_assinatura_webhook(
        x_signature=request.headers.get("x-signature"),
        x_request_id=request.headers.get("x-request-id"),
        data_id=payment_id,
    )
    if not assinatura_ok:
        logger.warning("Webhook Mercado Pago com assinatura inválida (payment_id=%s)", payment_id)
        return JSONResponse({"erro": "assinatura_invalida"}, status_code=401)

    try:
        payments.confirmar_pagamento(db, payment_id)
    except Exception:
        logger.exception("Erro ao processar webhook do Mercado Pago (payment_id=%s)", payment_id)
        # Sempre 200 aqui (após assinatura validada) para evitar retries em loop do MP.

    return JSONResponse({"ok": True}, status_code=200)
