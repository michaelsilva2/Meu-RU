"""
payments.py — Simulador de pagamentos (Pix e cartão) para recarga de créditos.

Não existe integração com nenhum gateway real aqui — tudo (QR code Pix,
aprovação, recusa) é gerado e decidido localmente. O objetivo é representar
o fluxo de um pagamento de verdade (mesmo formato de BR Code do Pix, mesmo
tempo de espera até confirmar, mesma possibilidade de cartão ser recusado),
sem mover dinheiro nem depender de credenciais de produção.

Fluxo:
  1. criar_cobranca_pix()      → gera um Pix "pendente" com QR/copia-e-cola
  2. confirmar_pix_simulado()  → aprova sozinho depois de alguns segundos
     (chamado a cada consulta de status do frontend e pelo job de fallback)
  3. criar_pagamento_cartao()  → simula uma autorização de cartão na hora
"""
import base64
import io
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import qrcode
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import PIX_EXPIRACAO_MINUTOS, PAGAMENTO_SIMULADO_DELAY_SEGUNDOS
from models import Aluno, CartaoSalvo, HistoricoRecarga, StatusRecarga, TipoRecarga


class PagamentoError(Exception):
    pass


# ─── Pix simulado ────────────────────────────────────────────────────────────

def _crc16_ccitt(payload: str) -> str:
    """CRC16/CCITT-FALSE — o mesmo checksum usado no BR Code (Pix) real."""
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return format(crc, "04X")


def _tlv(id_campo: str, valor_campo: str) -> str:
    return f"{id_campo}{len(valor_campo):02d}{valor_campo}"


def _gerar_pix_copia_cola(valor: Decimal, referencia: str) -> str:
    """
    Monta um payload no formato BR Code (EMV) real — visualmente idêntico a
    um Pix de verdade — mas com uma chave fictícia, que não corresponde a
    nenhuma conta bancária existente.
    """
    conta = _tlv("00", "BR.GOV.BCB.PIX") + _tlv("01", "00000000000")
    payload = (
        _tlv("00", "01")
        + _tlv("26", conta)
        + _tlv("52", "0000")
        + _tlv("53", "986")
        + _tlv("54", f"{valor:.2f}")
        + _tlv("58", "BR")
        + _tlv("59", "MEU RU")
        + _tlv("60", "GOIANIA")
        + _tlv("62", _tlv("05", referencia.replace("-", "")[:25]))
    )
    payload_com_marcador_crc = payload + "6304"
    return payload_com_marcador_crc + _crc16_ccitt(payload_com_marcador_crc)


def _gerar_qr_base64(conteudo: str) -> str:
    imagem = qrcode.make(conteudo)
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def criar_cobranca_pix(db: Session, aluno: Aluno, valor: Decimal) -> HistoricoRecarga:
    """Cria uma cobrança Pix "pendente" com QR code e copia-e-cola simulados."""
    referencia = str(uuid.uuid4())
    agora = datetime.utcnow()
    expira_em = agora + timedelta(minutes=PIX_EXPIRACAO_MINUTOS)
    copia_cola = _gerar_pix_copia_cola(valor, referencia)

    recarga = HistoricoRecarga(
        aluno_id=aluno.id,
        admin_id=None,
        tipo=TipoRecarga.recarga,
        valor=valor,
        observacao="Recarga Pix iniciada pelo aluno (simulada)",
        status=StatusRecarga.pendente,
        metodo_pagamento="pix",
        gateway_payment_id=f"SIM-{referencia}",
        external_reference=referencia,
        pix_copia_cola=copia_cola,
        pix_qr_base64=_gerar_qr_base64(copia_cola),
        data_hora=agora,
        expira_em=expira_em,
        atualizado_em=agora,
    )
    db.add(recarga)
    db.commit()
    db.refresh(recarga)
    return recarga


def confirmar_pix_simulado(db: Session, recarga: HistoricoRecarga) -> HistoricoRecarga:
    """
    Aprova sozinha uma cobrança Pix pendente depois de
    PAGAMENTO_SIMULADO_DELAY_SEGUNDOS — representa o tempo que um aluno
    levaria pra abrir o app do banco e escanear o QR code. Chamado tanto
    pelo polling do frontend quanto pelo job de fallback do scheduler, e é
    seguro de chamar mais de uma vez para a mesma recarga.
    """
    if recarga.status != StatusRecarga.pendente:
        return recarga

    agora = datetime.utcnow()
    if recarga.expira_em and agora >= recarga.expira_em:
        db.execute(
            text(
                "UPDATE historico_recargas SET status = :status, atualizado_em = :agora "
                "WHERE id = :id AND status = 'pendente'"
            ),
            {"status": StatusRecarga.expirado.value, "agora": agora, "id": recarga.id},
        )
        db.commit()
        db.refresh(recarga)
        return recarga

    if agora - recarga.data_hora < timedelta(seconds=PAGAMENTO_SIMULADO_DELAY_SEGUNDOS):
        return recarga

    _aprovar_recarga_idempotente(db, recarga)
    db.refresh(recarga)
    return recarga


def _aprovar_recarga_idempotente(db: Session, recarga: HistoricoRecarga) -> bool:
    """
    UPDATE condicional: só aplica o crédito se a recarga ainda estiver
    pendente, evitando duplicar crédito caso o polling do frontend e o job
    de fallback processem a mesma recarga ao mesmo tempo.
    """
    resultado = db.execute(
        text(
            "UPDATE historico_recargas SET status = :status, atualizado_em = :agora "
            "WHERE id = :id AND status = 'pendente'"
        ),
        {"status": StatusRecarga.aprovado.value, "agora": datetime.utcnow(), "id": recarga.id},
    )
    if resultado.rowcount != 1:
        db.rollback()
        return False

    aluno = db.query(Aluno).filter(Aluno.id == recarga.aluno_id).first()
    if aluno:
        aluno.creditos = Decimal(str(aluno.creditos)) + Decimal(str(recarga.valor))
    db.commit()
    return True


# ─── Cartão simulado ─────────────────────────────────────────────────────────

def _luhn_valido(numero: str) -> bool:
    if not numero.isdigit() or not (13 <= len(numero) <= 19):
        return False
    soma = 0
    dobrar = False
    for digito in reversed(numero):
        d = int(digito)
        if dobrar:
            d *= 2
            if d > 9:
                d -= 9
        soma += d
        dobrar = not dobrar
    return soma % 10 == 0


def _validade_valida(validade: str) -> bool:
    m = re.match(r"^(\d{2})/(\d{2})$", validade or "")
    if not m:
        return False
    mes, ano = int(m.group(1)), 2000 + int(m.group(2))
    if not (1 <= mes <= 12):
        return False
    proximo_mes = datetime(ano + (mes // 12), (mes % 12) + 1, 1)
    return proximo_mes > datetime.utcnow()


def _detectar_bandeira(numero: str) -> str:
    if numero.startswith("4"):
        return "Visa"
    if numero[:2] in {"51", "52", "53", "54", "55"} or 2221 <= int(numero[:4]) <= 2720:
        return "Mastercard"
    if numero[:2] in {"34", "37"}:
        return "Amex"
    return "Cartão"


def _salvar_cartao(db: Session, aluno: Aluno, numero: str, nome: str, tipo: str) -> None:
    """Guarda (ou atualiza) a versão mascarada do cartão pra reuso futuro."""
    ultimos_digitos = numero[-4:]
    bandeira = _detectar_bandeira(numero)

    existente = db.query(CartaoSalvo).filter(
        CartaoSalvo.aluno_id == aluno.id,
        CartaoSalvo.ultimos_digitos == ultimos_digitos,
        CartaoSalvo.bandeira == bandeira,
        CartaoSalvo.tipo == tipo,
    ).first()
    if existente:
        existente.nome_impresso = nome
        db.commit()
        return

    db.add(CartaoSalvo(
        aluno_id=aluno.id,
        ultimos_digitos=ultimos_digitos,
        bandeira=bandeira,
        nome_impresso=nome,
        tipo=tipo,
    ))
    db.commit()


def pagar_com_cartao_salvo(db: Session, aluno: Aluno, valor: Decimal, cartao: CartaoSalvo) -> HistoricoRecarga:
    """
    Paga com um cartão já salvo — sem pedir número/validade/CVV de novo,
    igual qualquer carteira digital real. Cartões salvos cujos últimos 4
    dígitos forem '0000' continuam sendo recusados, pela mesma convenção
    usada em criar_pagamento_cartao.
    """
    referencia = str(uuid.uuid4())
    aprovado = cartao.ultimos_digitos != "0000"

    recarga = HistoricoRecarga(
        aluno_id=aluno.id,
        admin_id=None,
        tipo=TipoRecarga.recarga,
        valor=valor,
        observacao=f"Recarga com cartão salvo ({cartao.bandeira} final {cartao.ultimos_digitos}, {cartao.tipo}) (simulada)",
        status=StatusRecarga.pendente,
        metodo_pagamento="cartao",
        gateway_payment_id=f"SIM-{referencia}",
        external_reference=referencia,
        atualizado_em=datetime.utcnow(),
    )
    db.add(recarga)
    db.commit()
    db.refresh(recarga)

    if aprovado:
        _aprovar_recarga_idempotente(db, recarga)
    else:
        recarga.status = StatusRecarga.rejeitado
        recarga.atualizado_em = datetime.utcnow()
        db.commit()

    db.refresh(recarga)
    return recarga


def criar_pagamento_cartao(db: Session, aluno: Aluno, valor: Decimal, dados_cartao: dict) -> HistoricoRecarga:
    """
    Simula uma autorização de cartão (crédito ou débito) na hora — sem
    contato com nenhuma operadora real. Cartões terminados em 0000 são
    recusados de propósito, representando também o caminho de pagamento
    negado (mesma convenção usada por cartões de teste de gateways reais).
    """
    if not aluno.cpf:
        raise PagamentoError("CPF necessário para pagamento por cartão.")

    numero = re.sub(r"\D", "", str(dados_cartao.get("numero", "")))
    if not _luhn_valido(numero):
        raise PagamentoError("Número de cartão inválido.")

    if not _validade_valida(dados_cartao.get("validade")):
        raise PagamentoError("Validade do cartão inválida ou vencida.")

    cvv = re.sub(r"\D", "", str(dados_cartao.get("cvv", "")))
    if not (3 <= len(cvv) <= 4):
        raise PagamentoError("CVV inválido.")

    if not str(dados_cartao.get("nome", "")).strip():
        raise PagamentoError("Nome impresso no cartão é obrigatório.")

    tipo_cartao = "debito" if dados_cartao.get("tipo") == "debito" else "credito"
    referencia = str(uuid.uuid4())
    aprovado = not numero.endswith("0000")

    recarga = HistoricoRecarga(
        aluno_id=aluno.id,
        admin_id=None,
        tipo=TipoRecarga.recarga,
        valor=valor,
        observacao=f"Recarga por cartão de {tipo_cartao} iniciada pelo aluno (simulada)",
        status=StatusRecarga.pendente,
        metodo_pagamento="cartao",
        gateway_payment_id=f"SIM-{referencia}",
        external_reference=referencia,
        atualizado_em=datetime.utcnow(),
    )
    db.add(recarga)
    db.commit()
    db.refresh(recarga)

    if aprovado:
        _aprovar_recarga_idempotente(db, recarga)
        _salvar_cartao(db, aluno, numero, str(dados_cartao.get("nome", "")).strip(), tipo_cartao)
    else:
        recarga.status = StatusRecarga.rejeitado
        recarga.atualizado_em = datetime.utcnow()
        db.commit()

    db.refresh(recarga)
    return recarga
