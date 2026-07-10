"""
payments.py — Integração com Mercado Pago (Pix) para recarga de créditos.

Fluxo:
  1. criar_cobranca_pix()      → gera uma cobrança Pix pendente
  2. buscar_pagamento()        → consulta o status real de um pagamento na API do MP
  3. validar_assinatura_webhook() → valida a notificação recebida em /webhook/mercadopago
  4. confirmar_pagamento()     → aplica o crédito de forma idempotente

Nunca confiar no corpo do webhook para status/valor — sempre buscar o
pagamento na API do Mercado Pago antes de creditar (buscar_pagamento).
"""
import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import mercadopago
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import (
    BASE_URL,
    MERCADOPAGO_ACCESS_TOKEN,
    MERCADOPAGO_WEBHOOK_SECRET,
    PIX_EXPIRACAO_MINUTOS,
)
from models import Aluno, HistoricoRecarga, StatusRecarga, TipoRecarga

logger = logging.getLogger(__name__)

_sdk: mercadopago.SDK | None = None


def _get_sdk() -> mercadopago.SDK:
    global _sdk
    if _sdk is None:
        _sdk = mercadopago.SDK(MERCADOPAGO_ACCESS_TOKEN)
    return _sdk


class PagamentoError(Exception):
    pass


def criar_cobranca_pix(db: Session, aluno: Aluno, valor: Decimal) -> HistoricoRecarga:
    """
    Cria uma cobrança Pix no Mercado Pago e uma linha `pendente` em
    HistoricoRecarga. Não credita nada — o crédito só acontece quando o
    pagamento for confirmado (via webhook ou poller).
    """
    referencia = str(uuid.uuid4())
    expira_em = datetime.now(timezone.utc) + timedelta(minutes=PIX_EXPIRACAO_MINUTOS)

    nome_partes = aluno.nome.strip().split(" ", 1)
    primeiro_nome = nome_partes[0]
    sobrenome = nome_partes[1] if len(nome_partes) > 1 else primeiro_nome

    payment_data = {
        "transaction_amount": float(valor),
        "description": "Recarga de créditos — Meu RU",
        "payment_method_id": "pix",
        "payer": {
            "email": aluno.email,
            "first_name": primeiro_nome,
            "last_name": sobrenome,
        },
        "external_reference": referencia,
        "notification_url": f"{BASE_URL}/webhook/mercadopago",
        "date_of_expiration": expira_em.isoformat(timespec="milliseconds"),
    }

    resultado = _get_sdk().payment().create(
        payment_data,
        request_options=mercadopago.config.RequestOptions(
            custom_headers={"X-Idempotency-Key": referencia},
        ),
    )

    if resultado.get("status") not in (200, 201):
        logger.error("Falha ao criar cobrança Pix: %s", resultado)
        raise PagamentoError("Não foi possível gerar a cobrança Pix agora.")

    resposta = resultado["response"]
    transacao = resposta.get("point_of_interaction", {}).get("transaction_data", {})

    recarga = HistoricoRecarga(
        aluno_id=aluno.id,
        admin_id=None,
        tipo=TipoRecarga.recarga,
        valor=valor,
        observacao="Recarga Pix iniciada pelo aluno",
        status=StatusRecarga.pendente,
        metodo_pagamento="pix",
        gateway_payment_id=str(resposta["id"]),
        external_reference=referencia,
        pix_copia_cola=transacao.get("qr_code"),
        pix_qr_base64=transacao.get("qr_code_base64"),
        expira_em=expira_em.replace(tzinfo=None),
        atualizado_em=datetime.utcnow(),
    )
    db.add(recarga)
    db.commit()
    db.refresh(recarga)
    return recarga


def obter_ou_criar_customer(db: Session, aluno: Aluno) -> str:
    """
    Retorna o customer_id do aluno no Mercado Pago, criando-o se ainda não
    existir. Necessário pro Card Payment Brick listar/salvar cartões.
    """
    if aluno.mp_customer_id:
        return aluno.mp_customer_id

    resultado = _get_sdk().customer().create({"email": aluno.email})
    if resultado.get("status") not in (200, 201):
        # Cliente já existe no MP pra esse email (comum em reprocessos/dev) — busca em vez de falhar.
        if resultado.get("status") == 400 and "already exists" in str(resultado.get("response", "")).lower():
            busca = _get_sdk().customer().search({"email": aluno.email})
            resultados = busca.get("response", {}).get("results", [])
            if resultados:
                aluno.mp_customer_id = resultados[0]["id"]
                db.commit()
                return aluno.mp_customer_id
        logger.error("Falha ao criar customer no Mercado Pago: %s", resultado)
        raise PagamentoError("Não foi possível preparar o pagamento por cartão agora.")

    aluno.mp_customer_id = resultado["response"]["id"]
    db.commit()
    return aluno.mp_customer_id


def criar_pagamento_cartao(db: Session, aluno: Aluno, valor: Decimal, dados_brick: dict) -> HistoricoRecarga:
    """
    Cria um pagamento por cartão via Card Payment Brick. Diferente do Pix,
    resolve na hora — por isso já chama confirmar_pagamento() em seguida
    pra aplicar o crédito imediatamente se aprovado.
    """
    if not aluno.cpf:
        raise PagamentoError("CPF necessário para pagamento por cartão.")

    customer_id = obter_ou_criar_customer(db, aluno)
    referencia = str(uuid.uuid4())

    payment_data = {
        "transaction_amount": float(valor),
        "token": dados_brick["token"],
        "description": "Recarga de créditos — Meu RU",
        "installments": 1,
        "payment_method_id": dados_brick["payment_method_id"],
        "issuer_id": dados_brick.get("issuer_id"),
        "external_reference": referencia,
        "notification_url": f"{BASE_URL}/webhook/mercadopago",
        "payer": {
            "type": "customer",
            "id": customer_id,
            "email": aluno.email,
            "identification": {"type": "CPF", "number": aluno.cpf},
        },
    }

    resultado = _get_sdk().payment().create(
        payment_data,
        request_options=mercadopago.config.RequestOptions(
            custom_headers={"X-Idempotency-Key": referencia},
        ),
    )
    if resultado.get("status") not in (200, 201):
        logger.error("Falha ao criar pagamento por cartão: %s", resultado)
        raise PagamentoError("Não foi possível processar o pagamento por cartão agora.")

    resposta = resultado["response"]

    recarga = HistoricoRecarga(
        aluno_id=aluno.id,
        admin_id=None,
        tipo=TipoRecarga.recarga,
        valor=valor,
        observacao="Recarga por cartão iniciada pelo aluno",
        status=StatusRecarga.pendente,
        metodo_pagamento="cartao",
        gateway_payment_id=str(resposta["id"]),
        external_reference=referencia,
        atualizado_em=datetime.utcnow(),
    )
    db.add(recarga)
    db.commit()
    db.refresh(recarga)

    # Cartão resolve na hora (approved/rejected) — confirma já, reaproveitando
    # a mesma função idempotente usada pelo webhook e pelo poller do Pix.
    confirmar_pagamento(db, recarga.gateway_payment_id)
    db.refresh(recarga)
    return recarga


def buscar_pagamento(payment_id: str) -> dict:
    """Busca o status/valor autoritativo de um pagamento direto na API do MP."""
    resultado = _get_sdk().payment().get(payment_id)
    if resultado.get("status") != 200:
        raise PagamentoError(f"Pagamento {payment_id} não encontrado no Mercado Pago.")
    return resultado["response"]


def validar_assinatura_webhook(x_signature: str | None, x_request_id: str | None, data_id: str | None) -> bool:
    """
    Valida o header `x-signature` enviado pelo Mercado Pago:
    formato "ts=<timestamp>,v1=<hmac_sha256_hex>", calculado sobre o
    manifesto "id:<data_id>;request-id:<x_request_id>;ts:<ts>;".
    """
    if not (x_signature and x_request_id and data_id and MERCADOPAGO_WEBHOOK_SECRET):
        return False

    partes = dict(p.split("=", 1) for p in x_signature.split(",") if "=" in p)
    ts = partes.get("ts")
    v1 = partes.get("v1")
    if not ts or not v1:
        return False

    manifesto = f"id:{data_id.lower()};request-id:{x_request_id};ts:{ts};"
    esperado = hmac.new(
        MERCADOPAGO_WEBHOOK_SECRET.encode("utf-8"),
        manifesto.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return secrets.compare_digest(esperado, v1)


_STATUS_MP_PARA_INTERNO = {
    "approved": StatusRecarga.aprovado,
    "rejected": StatusRecarga.rejeitado,
    "cancelled": StatusRecarga.cancelado,
}


def confirmar_pagamento(db: Session, payment_id: str) -> None:
    """
    Aplica o resultado autoritativo de um pagamento de forma idempotente.
    Chamado tanto pelo webhook quanto pelo job de polling — pode ser
    chamado múltiplas vezes para o mesmo payment_id sem duplicar crédito.
    """
    pagamento = buscar_pagamento(payment_id)
    status_mp = pagamento.get("status")
    novo_status = _STATUS_MP_PARA_INTERNO.get(status_mp)
    if novo_status is None:
        # "in_process", "pending" etc. — nada a fazer ainda
        return

    recarga = db.query(HistoricoRecarga).filter(
        HistoricoRecarga.gateway_payment_id == str(payment_id),
    ).first()
    if not recarga:
        logger.warning("Webhook para payment_id desconhecido: %s", payment_id)
        return

    if recarga.status != StatusRecarga.pendente:
        return  # já processado — idempotente

    # UPDATE condicional: só aplica se ainda estiver pendente, evita corrida
    # entre webhook e poller processando o mesmo pagamento ao mesmo tempo.
    resultado = db.execute(
        text(
            "UPDATE historico_recargas SET status = :novo_status, atualizado_em = :agora "
            "WHERE id = :id AND status = 'pendente'"
        ),
        {"novo_status": novo_status.value, "agora": datetime.utcnow(), "id": recarga.id},
    )
    if resultado.rowcount != 1:
        db.rollback()
        return

    if novo_status == StatusRecarga.aprovado:
        aluno = db.query(Aluno).filter(Aluno.id == recarga.aluno_id).first()
        if aluno:
            aluno.creditos = Decimal(str(aluno.creditos)) + Decimal(str(recarga.valor))

    db.commit()
