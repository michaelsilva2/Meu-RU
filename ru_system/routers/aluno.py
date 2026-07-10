"""
Rotas da área do aluno: dashboard, histórico e exportação CSV.
"""
import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Request, Depends, Query, HTTPException, Form
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, extract

from database import get_db
from models import Aluno, HistoricoRefeicao, HistoricoRecarga, TipoRefeicao, Cardapio
from auth import obter_aluno_atual, gerar_csrf_token, verificar_csrf
from whatsapp_bot import obter_status_pico
from config import PRECOS_REFEICAO, RECARGA_VALOR_MIN, RECARGA_VALOR_MAX, MERCADOPAGO_PUBLIC_KEY
import payments
import re

router = APIRouter(prefix="/aluno")
templates = Jinja2Templates(directory="templates")

ITENS_POR_PAGINA = 20


def _get_aluno(payload: dict, db: Session) -> Aluno:
    aluno = db.query(Aluno).filter(
        Aluno.id == int(payload["sub"]),
        Aluno.ativo == True
    ).first()
    if not aluno:
        raise HTTPException(status_code=404, detail="Aluno não encontrado")
    return aluno


# ─── Dashboard ─────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)

    aluno = _get_aluno(payload, db)

    agora = datetime.utcnow()
    refeicoes_mes = db.query(HistoricoRefeicao).filter(
        HistoricoRefeicao.aluno_id == aluno.id,
        extract("month", HistoricoRefeicao.data_hora) == agora.month,
        extract("year", HistoricoRefeicao.data_hora) == agora.year
    ).count()

    from collections import defaultdict
    ultimas_refeicoes = db.query(HistoricoRefeicao).filter(
        HistoricoRefeicao.aluno_id == aluno.id
    ).order_by(HistoricoRefeicao.data_hora.desc()).limit(100).all()

    semanas: dict = defaultdict(int)
    semana_labels: dict = {}
    for r in ultimas_refeicoes:
        chave = r.data_hora.strftime("%Y-W%W")
        semanas[chave] += 1
        if chave not in semana_labels:
            segunda = r.data_hora - timedelta(days=r.data_hora.weekday())
            semana_labels[chave] = segunda.strftime("%d/%m")

    semanas_ordenadas = sorted(semanas.items())[-8:]
    labels_semanas = [semana_labels[s[0]] for s in semanas_ordenadas]
    dados_semanas = [s[1] for s in semanas_ordenadas]

    ultimas_5 = db.query(HistoricoRefeicao).filter(
        HistoricoRefeicao.aluno_id == aluno.id
    ).order_by(HistoricoRefeicao.data_hora.desc()).limit(5).all()

    custo_refeicao = PRECOS_REFEICAO.get(aluno.categoria.value, Decimal("6.00"))
    saldo_baixo = Decimal(str(aluno.creditos)) < custo_refeicao

    csrf = gerar_csrf_token()
    resposta = templates.TemplateResponse(request, "aluno/dashboard.html", {
        "aluno": aluno,
        "refeicoes_mes": refeicoes_mes,
        "labels_semanas": labels_semanas,
        "dados_semanas": dados_semanas,
        "ultimas_refeicoes": ultimas_5,
        "creditos_formatado": f"{float(aluno.creditos):.2f}".replace(".", ","),
        "custo_refeicao": f"{float(custo_refeicao):.2f}".replace(".", ","),
        "saldo_baixo": saldo_baixo,
        "csrf_token": csrf,
        "status_pico": obter_status_pico(db),
        "mp_public_key": MERCADOPAGO_PUBLIC_KEY,
        "cpf_cadastrado": bool(aluno.cpf),
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


# ─── Histórico ─────────────────────────────────────────────────────────────

@router.get("/historico", response_class=HTMLResponse)
async def historico(
    request: Request,
    pagina: int = Query(1, ge=1),
    tipo: str = Query("todos"),
    data_inicio: str = Query(""),
    data_fim: str = Query(""),
    exportar: str = Query(""),
    db: Session = Depends(get_db)
):
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)

    aluno = _get_aluno(payload, db)

    query = db.query(HistoricoRefeicao).filter(
        HistoricoRefeicao.aluno_id == aluno.id
    )

    if tipo == "almoco":
        query = query.filter(HistoricoRefeicao.tipo == TipoRefeicao.almoco)
    elif tipo == "jantar":
        query = query.filter(HistoricoRefeicao.tipo == TipoRefeicao.jantar)

    if data_inicio:
        try:
            dt_inicio = datetime.strptime(data_inicio, "%Y-%m-%d")
            query = query.filter(HistoricoRefeicao.data_hora >= dt_inicio)
        except ValueError:
            pass

    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            query = query.filter(HistoricoRefeicao.data_hora <= dt_fim)
        except ValueError:
            pass

    query = query.order_by(HistoricoRefeicao.data_hora.desc())

    if exportar == "csv":
        registros = query.all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Data/Hora", "Tipo", "Créditos Utilizados"])
        for r in registros:
            writer.writerow([
                r.data_hora.strftime("%d/%m/%Y %H:%M"),
                "Almoço" if r.tipo == TipoRefeicao.almoco else "Jantar",
                f"R$ {float(r.creditos_utilizados):.2f}".replace(".", ",")
            ])
        output.seek(0)
        nome_arquivo = f"historico_refeicoes_{aluno.matricula}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={nome_arquivo}"}
        )

    total = query.count()
    total_paginas = max(1, (total + ITENS_POR_PAGINA - 1) // ITENS_POR_PAGINA)
    pagina = min(pagina, total_paginas)
    offset = (pagina - 1) * ITENS_POR_PAGINA
    registros = query.offset(offset).limit(ITENS_POR_PAGINA).all()

    csrf = gerar_csrf_token()
    resposta = templates.TemplateResponse(request, "aluno/historico.html", {
        "aluno": aluno,
        "registros": registros,
        "pagina": pagina,
        "total_paginas": total_paginas,
        "total": total,
        "tipo": tipo,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "itens_por_pagina": ITENS_POR_PAGINA,
        "csrf_token": csrf,
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


# ─── Recarga pelo aluno (Pix via Mercado Pago) ──────────────────────────────

@router.post("/recarregar")
async def recarregar_post(
    request: Request,
    valor: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Cria uma cobrança Pix pendente e devolve o QR code / copia-e-cola.
    Não credita nada aqui — o crédito só acontece quando o pagamento é
    confirmado (webhook do Mercado Pago ou o job de polling de fallback).
    """
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return JSONResponse({"erro": "sessao_expirada"}, status_code=401)

    try:
        verificar_csrf(request, {"csrf_token": csrf_token})
    except HTTPException as exc:
        return JSONResponse({"erro": "csrf_invalido"}, status_code=exc.status_code)

    aluno = db.query(Aluno).filter(
        Aluno.id == int(payload["sub"]),
        Aluno.ativo == True
    ).first()
    if not aluno:
        return JSONResponse({"erro": "sessao_expirada"}, status_code=401)

    try:
        valor_decimal = Decimal(valor.replace(",", "."))
        if valor_decimal < RECARGA_VALOR_MIN or valor_decimal > RECARGA_VALOR_MAX:
            raise ValueError
    except (InvalidOperation, ValueError):
        return JSONResponse({"erro": "valor_invalido"}, status_code=400)

    try:
        recarga = payments.criar_cobranca_pix(db, aluno, valor_decimal)
    except payments.PagamentoError:
        return JSONResponse({"erro": "gateway_indisponivel"}, status_code=502)

    return JSONResponse({
        "referencia": recarga.external_reference,
        "valor": f"{float(recarga.valor):.2f}",
        "qr_base64": recarga.pix_qr_base64,
        "copia_cola": recarga.pix_copia_cola,
        "expira_em": recarga.expira_em.isoformat() if recarga.expira_em else None,
    })


@router.get("/recarga-status/{referencia}")
async def recarga_status(referencia: str, request: Request, db: Session = Depends(get_db)):
    """Consultado pelo frontend (polling) enquanto aguarda a confirmação do Pix."""
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return JSONResponse({"erro": "sessao_expirada"}, status_code=401)

    recarga = db.query(HistoricoRecarga).filter(
        HistoricoRecarga.external_reference == referencia,
        HistoricoRecarga.aluno_id == int(payload["sub"]),
    ).first()
    if not recarga:
        return JSONResponse({"erro": "nao_encontrada"}, status_code=404)

    aluno = db.query(Aluno).filter(Aluno.id == int(payload["sub"])).first()

    return JSONResponse({
        "status": recarga.status.value,
        "valor": f"{float(recarga.valor):.2f}",
        "creditos_atual": f"{float(aluno.creditos):.2f}" if aluno else None,
    })


# ─── Recarga pelo aluno (Cartão via Mercado Pago Card Payment Brick) ────────

_CPF_RE = re.compile(r"^\d{11}$")


@router.post("/cpf")
async def salvar_cpf(request: Request, db: Session = Depends(get_db)):
    """Salva o CPF do aluno, pedido antes do primeiro pagamento por cartão."""
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return JSONResponse({"erro": "sessao_expirada"}, status_code=401)

    body = await request.json()
    try:
        verificar_csrf(request, {"csrf_token": body.get("csrf_token")})
    except HTTPException as exc:
        return JSONResponse({"erro": "csrf_invalido"}, status_code=exc.status_code)

    cpf_limpo = re.sub(r"\D", "", str(body.get("cpf", "")))
    if not _CPF_RE.match(cpf_limpo):
        return JSONResponse({"erro": "cpf_invalido"}, status_code=400)

    aluno = db.query(Aluno).filter(Aluno.id == int(payload["sub"]), Aluno.ativo == True).first()
    if not aluno:
        return JSONResponse({"erro": "sessao_expirada"}, status_code=401)

    aluno.cpf = cpf_limpo
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/mp-customer-id")
async def obter_mp_customer_id(request: Request, db: Session = Depends(get_db)):
    """Retorna (criando se necessário) o customer_id do aluno no Mercado Pago."""
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return JSONResponse({"erro": "sessao_expirada"}, status_code=401)

    aluno = db.query(Aluno).filter(Aluno.id == int(payload["sub"]), Aluno.ativo == True).first()
    if not aluno:
        return JSONResponse({"erro": "sessao_expirada"}, status_code=401)

    try:
        customer_id = payments.obter_ou_criar_customer(db, aluno)
    except payments.PagamentoError:
        return JSONResponse({"erro": "gateway_indisponivel"}, status_code=502)

    return JSONResponse({"customer_id": customer_id})


@router.post("/recarregar-cartao")
async def recarregar_cartao_post(request: Request, db: Session = Depends(get_db)):
    """
    Recebe os dados tokenizados pelo Card Payment Brick (onSubmit) e cria o
    pagamento. Diferente do Pix, resolve na hora — a resposta já vem com o
    status final (ou 'pendente', se o emissor exigir revisão extra).
    """
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return JSONResponse({"erro": "sessao_expirada"}, status_code=401)

    body = await request.json()
    try:
        verificar_csrf(request, {"csrf_token": body.get("csrf_token")})
    except HTTPException as exc:
        return JSONResponse({"erro": "csrf_invalido"}, status_code=exc.status_code)

    aluno = db.query(Aluno).filter(Aluno.id == int(payload["sub"]), Aluno.ativo == True).first()
    if not aluno:
        return JSONResponse({"erro": "sessao_expirada"}, status_code=401)

    if not aluno.cpf:
        return JSONResponse({"erro": "cpf_necessario"}, status_code=400)

    try:
        valor_decimal = Decimal(str(body.get("valor", "")).replace(",", "."))
        if valor_decimal < RECARGA_VALOR_MIN or valor_decimal > RECARGA_VALOR_MAX:
            raise ValueError
    except (InvalidOperation, ValueError):
        return JSONResponse({"erro": "valor_invalido"}, status_code=400)

    if not body.get("token") or not body.get("payment_method_id"):
        return JSONResponse({"erro": "dados_cartao_invalidos"}, status_code=400)

    try:
        recarga = payments.criar_pagamento_cartao(db, aluno, valor_decimal, body)
    except payments.PagamentoError:
        return JSONResponse({"erro": "gateway_indisponivel"}, status_code=502)

    db.refresh(aluno)
    return JSONResponse({
        "status": recarga.status.value,
        "referencia": recarga.external_reference,
        "valor": f"{float(recarga.valor):.2f}",
        "creditos_atual": f"{float(aluno.creditos):.2f}",
    })


# ─── Registrar Refeição pelo aluno ────────────────────────────────────────────

@router.post("/registrar-refeicao")
async def registrar_refeicao_post(
    request: Request,
    tipo: str = Form("almoco"),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)

    verificar_csrf(request, {"csrf_token": csrf_token})

    aluno = db.query(Aluno).filter(
        Aluno.id == int(payload["sub"]),
        Aluno.ativo == True
    ).first()
    if not aluno:
        return RedirectResponse(url="/login", status_code=302)

    try:
        tipo_enum = TipoRefeicao(tipo)
    except ValueError:
        tipo_enum = TipoRefeicao.almoco

    hoje_inicio = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    refeicoes_hoje = db.query(HistoricoRefeicao).filter(
        HistoricoRefeicao.aluno_id == aluno.id,
        HistoricoRefeicao.data_hora >= hoje_inicio,
    ).count()
    if refeicoes_hoje >= 2:
        return RedirectResponse(url="/aluno/dashboard?erro=limite_diario", status_code=303)

    custo = PRECOS_REFEICAO.get(aluno.categoria.value, Decimal("6.00"))
    if custo > 0 and Decimal(str(aluno.creditos)) < custo:
        return RedirectResponse(url="/aluno/dashboard?erro=saldo_insuficiente", status_code=303)

    if custo > 0:
        aluno.creditos = Decimal(str(aluno.creditos)) - custo

    refeicao = HistoricoRefeicao(
        aluno_id=aluno.id,
        tipo=tipo_enum,
        creditos_utilizados=custo,
        data_hora=datetime.utcnow(),
        registrado_por=None,
    )
    db.add(refeicao)
    db.commit()

    return RedirectResponse(url="/aluno/dashboard?msg=refeicao_ok", status_code=303)


# ─── Cardápio do dia (JSON para o widget) ──────────────────────────────────

@router.get("/cardapio-hoje")
async def cardapio_hoje(request: Request, db: Session = Depends(get_db)):
    try:
        obter_aluno_atual(request)
    except Exception:
        return JSONResponse({"erro": "não autorizado"}, status_code=401)

    from zoneinfo import ZoneInfo
    BR_TZ = ZoneInfo("America/Sao_Paulo")
    hoje = datetime.now(BR_TZ).date()

    resultado = {}
    for tipo in (TipoRefeicao.almoco, TipoRefeicao.jantar):
        c = db.query(Cardapio).filter(
            Cardapio.data == hoje,
            Cardapio.tipo == tipo,
        ).first()
        if c:
            resultado[tipo.value] = {
                "prato_principal": c.prato_principal,
                "acompanhamentos": c.acompanhamentos,
                "sobremesa": c.sobremesa,
                "observacao": c.observacao,
            }

    return JSONResponse({"data": str(hoje), "cardapio": resultado})
