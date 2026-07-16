"""
qrcode_acesso.py — QR code dinâmico de curta duração para validar a entrada
do aluno no RU.

Fluxo:
  1. gerar_token()   → aluno abre /aluno/qrcode, front-end pede um token novo
                        a cada QRCODE_TTL_SEGUNDOS e renderiza o PNG.
  2. gerar_png()      → desenha o QR (texto puro: só o token).
  3. validar_e_consumir() → scanner do admin lê o QR e consome o token;
                        idempotente, então dois scans da mesma câmera não
                        registram a refeição duas vezes.
"""
import io
import secrets
from datetime import datetime, timedelta, timezone

import qrcode
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import QRCODE_TTL_SEGUNDOS
from models import AcessoQRCode, Aluno


def gerar_token(db: Session, aluno: Aluno) -> AcessoQRCode:
    acesso = AcessoQRCode(
        aluno_id=aluno.id,
        token=secrets.token_urlsafe(24),
        criado_em=datetime.utcnow(),
        expira_em=datetime.utcnow() + timedelta(seconds=QRCODE_TTL_SEGUNDOS),
    )
    db.add(acesso)
    db.commit()
    db.refresh(acesso)
    return acesso


def gerar_png(token: str) -> bytes:
    img = qrcode.make(token, box_size=10, border=2)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def validar_e_consumir(db: Session, token: str, admin_id: int) -> AcessoQRCode | None:
    """
    Retorna o AcessoQRCode consumido se o token for válido, não-expirado e
    ainda não usado; None caso contrário. O UPDATE condicional evita corrida
    entre dois scans simultâneos do mesmo token (mesmo padrão usado em
    payments._aprovar_recarga_idempotente).
    """
    acesso = db.query(AcessoQRCode).filter(AcessoQRCode.token == token).first()
    if not acesso:
        return None
    if acesso.usado or acesso.expira_em < datetime.utcnow():
        return None

    resultado = db.execute(
        text(
            "UPDATE acessos_qrcode SET usado = 1, usado_em = :agora, validado_por = :admin_id "
            "WHERE id = :id AND usado = 0"
        ),
        {"agora": datetime.utcnow(), "admin_id": admin_id, "id": acesso.id},
    )
    if resultado.rowcount != 1:
        db.rollback()
        return None

    db.commit()
    db.refresh(acesso)
    return acesso


def tipo_refeicao_atual() -> str:
    """Sugere almoco/jantar pro scanner com base no horário de Brasília."""
    from zoneinfo import ZoneInfo
    hora = datetime.now(ZoneInfo("America/Sao_Paulo")).hour
    return "almoco" if 6 <= hora < 17 else "jantar"
