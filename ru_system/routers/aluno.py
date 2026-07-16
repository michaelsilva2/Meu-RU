"""
Rotas da área do aluno: dashboard, histórico e exportação CSV.
"""
import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Request, Depends, Query, HTTPException, Form
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, extract

from database import get_db
from models import (
    Aluno, HistoricoRefeicao, HistoricoRecarga, TipoRefeicao, Cardapio, CartaoSalvo,
    SugestaoAvulsa, SatisfacaoEnvio, SatisfacaoResposta,
)
from auth import obter_aluno_atual, gerar_csrf_token, verificar_csrf
from whatsapp_bot import obter_status_pico
from config import (
    PRECOS_REFEICAO, RECARGA_VALOR_MIN, RECARGA_VALOR_MAX,
    QRCODE_TTL_SEGUNDOS,
)
from refeicoes import registrar_refeicao, RefeicaoError
import qrcode_acesso
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

    cartoes_salvos = db.query(CartaoSalvo).filter(CartaoSalvo.aluno_id == aluno.id).order_by(CartaoSalvo.criado_em.desc()).all()

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
        "cpf_cadastrado": bool(aluno.cpf),
        "cartoes_salvos": cartoes_salvos,
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

    recarga = payments.confirmar_pix_simulado(db, recarga)
    aluno = db.query(Aluno).filter(Aluno.id == int(payload["sub"])).first()

    return JSONResponse({
        "status": recarga.status.value,
        "valor": f"{float(recarga.valor):.2f}",
        "creditos_atual": f"{float(aluno.creditos):.2f}" if aluno else None,
    })


# ─── Recarga pelo aluno (Cartão simulado) ───────────────────────────────────

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


@router.post("/recarregar-cartao")
async def recarregar_cartao_post(request: Request, db: Session = Depends(get_db)):
    """
    Recebe os dados do formulário de cartão (número, nome, validade, cvv,
    tipo) e simula a autorização. Diferente do Pix, resolve na hora — a
    resposta já vem com o status final (aprovado ou rejeitado).
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

    dados_cartao = {
        "numero": body.get("numero"),
        "nome": body.get("nome"),
        "validade": body.get("validade"),
        "cvv": body.get("cvv"),
        "tipo": body.get("tipo"),
    }

    try:
        recarga = payments.criar_pagamento_cartao(db, aluno, valor_decimal, dados_cartao)
    except payments.PagamentoError:
        return JSONResponse({"erro": "dados_cartao_invalidos"}, status_code=400)

    db.refresh(aluno)
    return JSONResponse({
        "status": recarga.status.value,
        "referencia": recarga.external_reference,
        "valor": f"{float(recarga.valor):.2f}",
        "creditos_atual": f"{float(aluno.creditos):.2f}",
    })


@router.post("/recarregar-cartao-salvo")
async def recarregar_cartao_salvo_post(request: Request, db: Session = Depends(get_db)):
    """Paga com um cartão já salvo — sem pedir número/validade/CVV de novo."""
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

    cartao = db.query(CartaoSalvo).filter(
        CartaoSalvo.id == body.get("cartao_id"),
        CartaoSalvo.aluno_id == aluno.id,
    ).first()
    if not cartao:
        return JSONResponse({"erro": "cartao_nao_encontrado"}, status_code=404)

    try:
        valor_decimal = Decimal(str(body.get("valor", "")).replace(",", "."))
        if valor_decimal < RECARGA_VALOR_MIN or valor_decimal > RECARGA_VALOR_MAX:
            raise ValueError
    except (InvalidOperation, ValueError):
        return JSONResponse({"erro": "valor_invalido"}, status_code=400)

    recarga = payments.pagar_com_cartao_salvo(db, aluno, valor_decimal, cartao)

    db.refresh(aluno)
    return JSONResponse({
        "status": recarga.status.value,
        "referencia": recarga.external_reference,
        "valor": f"{float(recarga.valor):.2f}",
        "creditos_atual": f"{float(aluno.creditos):.2f}",
    })


@router.post("/cartao-salvo/remover")
async def remover_cartao_salvo_post(request: Request, db: Session = Depends(get_db)):
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return JSONResponse({"erro": "sessao_expirada"}, status_code=401)

    body = await request.json()
    try:
        verificar_csrf(request, {"csrf_token": body.get("csrf_token")})
    except HTTPException as exc:
        return JSONResponse({"erro": "csrf_invalido"}, status_code=exc.status_code)

    cartao = db.query(CartaoSalvo).filter(
        CartaoSalvo.id == body.get("cartao_id"),
        CartaoSalvo.aluno_id == int(payload["sub"]),
    ).first()
    if not cartao:
        return JSONResponse({"erro": "cartao_nao_encontrado"}, status_code=404)

    db.delete(cartao)
    db.commit()
    return JSONResponse({"ok": True})


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

    try:
        registrar_refeicao(db, aluno, tipo_enum)
    except RefeicaoError as exc:
        return RedirectResponse(url=f"/aluno/dashboard?erro={exc.motivo}", status_code=303)

    return RedirectResponse(url="/aluno/dashboard?msg=refeicao_ok", status_code=303)


# ─── QR code de acesso ──────────────────────────────────────────────────────

@router.get("/qrcode", response_class=HTMLResponse)
async def qrcode_pagina(request: Request, db: Session = Depends(get_db)):
    """Tela cheia com o QR code dinâmico — pra mostrar na entrada do RU."""
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)

    aluno = _get_aluno(payload, db)
    return templates.TemplateResponse(request, "aluno/qrcode.html", {
        "aluno": aluno,
        "ttl_segundos": QRCODE_TTL_SEGUNDOS,
    })


@router.get("/qrcode/imagem")
async def qrcode_imagem(request: Request, db: Session = Depends(get_db)):
    """
    Gera um token novo a cada chamada e devolve o PNG do QR correspondente.
    O front-end chama isso a cada `ttl_segundos` pra manter o código sempre
    fresco — nunca serve uma imagem em cache.
    """
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return JSONResponse({"erro": "nao_autenticado"}, status_code=401)

    aluno = _get_aluno(payload, db)
    acesso = qrcode_acesso.gerar_token(db, aluno)
    png = qrcode_acesso.gerar_png(acesso.token)

    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


# ─── Cardápio do dia (JSON para o widget) ──────────────────────────────────

@router.get("/cardapio-hoje")
async def cardapio_hoje(request: Request, dia: str = "hoje", db: Session = Depends(get_db)):
    try:
        obter_aluno_atual(request)
    except Exception:
        return JSONResponse({"erro": "não autorizado"}, status_code=401)

    from zoneinfo import ZoneInfo
    BR_TZ = ZoneInfo("America/Sao_Paulo")
    data_ref = datetime.now(BR_TZ).date()
    if dia == "amanha":
        data_ref += timedelta(days=1)

    resultado = {}
    for tipo in (TipoRefeicao.almoco, TipoRefeicao.jantar):
        c = db.query(Cardapio).filter(
            Cardapio.data == data_ref,
            Cardapio.tipo == tipo,
        ).first()
        if c:
            resultado[tipo.value] = {
                "prato_principal": c.prato_principal,
                "arroz": c.arroz,
                "feijao": c.feijao,
                "legumes": c.legumes,
                "salada": c.salada,
                "sobremesa": c.sobremesa,
                "vegetariano": c.vegetariano,
                "observacao": c.observacao,
            }

    return JSONResponse({"data": str(data_ref), "cardapio": resultado})


# ─── Últimas refeições (JSON para o widget do bot) ─────────────────────────

@router.get("/historico-recente")
async def historico_recente(request: Request, db: Session = Depends(get_db)):
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return JSONResponse({"erro": "não autorizado"}, status_code=401)

    aluno = _get_aluno(payload, db)
    refeicoes = (
        db.query(HistoricoRefeicao)
        .filter(HistoricoRefeicao.aluno_id == aluno.id)
        .order_by(HistoricoRefeicao.data_hora.desc())
        .limit(3)
        .all()
    )
    return JSONResponse({
        "refeicoes": [
            {
                "tipo": r.tipo.value,
                "data_hora": r.data_hora.isoformat(),
                "custo": str(r.creditos_utilizados),
            }
            for r in refeicoes
        ]
    })


# ─── Sugestão e avaliação (widget do bot) ──────────────────────────────────

@router.post("/sugestao")
async def enviar_sugestao_avulsa(request: Request, db: Session = Depends(get_db)):
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return JSONResponse({"erro": "sessao_expirada"}, status_code=401)

    body = await request.json()
    try:
        verificar_csrf(request, {"csrf_token": body.get("csrf_token")})
    except HTTPException as exc:
        return JSONResponse({"erro": "csrf_invalido"}, status_code=exc.status_code)

    texto = (body.get("sugestao") or "").strip()
    if not texto:
        return JSONResponse({"erro": "sugestao_vazia"}, status_code=400)

    aluno = _get_aluno(payload, db)
    db.add(SugestaoAvulsa(aluno_id=aluno.id, texto=texto[:500], respondido_em=datetime.utcnow()))
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/avaliacao-pendente")
async def avaliacao_pendente(request: Request, db: Session = Depends(get_db)):
    """Indica se há refeição registrada ainda sem avaliação."""
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return JSONResponse({"erro": "não autorizado"}, status_code=401)

    aluno = _get_aluno(payload, db)
    refeicao = (
        db.query(HistoricoRefeicao)
        .outerjoin(SatisfacaoEnvio, SatisfacaoEnvio.refeicao_id == HistoricoRefeicao.id)
        .filter(HistoricoRefeicao.aluno_id == aluno.id, SatisfacaoEnvio.id.is_(None))
        .order_by(HistoricoRefeicao.data_hora.desc())
        .first()
    )
    return JSONResponse({"pendente": refeicao is not None})


@router.post("/avaliacao")
async def enviar_avaliacao(request: Request, db: Session = Depends(get_db)):
    try:
        payload = obter_aluno_atual(request)
    except HTTPException:
        return JSONResponse({"erro": "sessao_expirada"}, status_code=401)

    body = await request.json()
    try:
        verificar_csrf(request, {"csrf_token": body.get("csrf_token")})
    except HTTPException as exc:
        return JSONResponse({"erro": "csrf_invalido"}, status_code=exc.status_code)

    try:
        nota_comida = int(body.get("nota_comida"))
        nota_servico = int(body.get("nota_servico"))
        assert nota_comida in range(1, 6) and nota_servico in range(1, 6)
    except (TypeError, ValueError, AssertionError):
        return JSONResponse({"erro": "notas_invalidas"}, status_code=400)

    sugestao = (body.get("sugestao") or "").strip()[:500] or None

    aluno = _get_aluno(payload, db)
    refeicao = (
        db.query(HistoricoRefeicao)
        .outerjoin(SatisfacaoEnvio, SatisfacaoEnvio.refeicao_id == HistoricoRefeicao.id)
        .filter(HistoricoRefeicao.aluno_id == aluno.id, SatisfacaoEnvio.id.is_(None))
        .order_by(HistoricoRefeicao.data_hora.desc())
        .first()
    )
    if not refeicao:
        return JSONResponse({"erro": "nada_para_avaliar"}, status_code=400)

    agora = datetime.utcnow()
    envio = SatisfacaoEnvio(
        aluno_id=aluno.id,
        refeicao_id=refeicao.id,
        enviado_em=agora,
        respondido=True,
        etapa="sugestao",
    )
    db.add(envio)
    db.flush()
    db.add(SatisfacaoResposta(
        envio_id=envio.id,
        aluno_id=aluno.id,
        nota=nota_comida,
        nota_comida=nota_comida,
        nota_servico=nota_servico,
        sugestao=sugestao,
        respondido_em=agora,
    ))
    db.commit()
    return JSONResponse({"ok": True})
