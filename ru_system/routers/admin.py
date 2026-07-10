"""
Rotas da área administrativa: dashboard, gerenciamento de alunos,
histórico geral, recargas/remoções e gerenciamento de admins.
"""
import asyncio
import base64
import csv
import io
import json
import logging
from datetime import datetime, timedelta, date
from decimal import Decimal

from fastapi import APIRouter, Request, Depends, Query, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, JSONResponse

logger = logging.getLogger(__name__)
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from database import get_db
from models import (
    Aluno, Admin, HistoricoRefeicao, HistoricoRecarga,
    TipoRefeicao, TipoRecarga, StatusRecarga, RoleAdmin, CategoriaAluno,
    SatisfacaoEnvio, SatisfacaoResposta, PicoMovimento,
    DesperdícioAlimento, NivelDesperdicio, Cardapio,
)
from config import PRECOS_REFEICAO
from auth import (
    obter_admin_atual, exigir_super_admin,
    hash_senha, gerar_csrf_token, verificar_csrf
)
from whatsapp_bot import (
    agendar_pesquisa_satisfacao,
    verificar_e_registrar_pico,
    obter_status_pico,
    _enviar_mensagem,
    _formatar_numero,
)
from email_service import enviar_emails_alerta_lote

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")

ITENS_POR_PAGINA_ALUNOS = 20
ITENS_POR_PAGINA_HISTORICO = 50


def _get_admin(payload: dict, db: Session) -> Admin:
    admin = db.query(Admin).filter(
        Admin.id == int(payload["sub"]),
        Admin.ativo == True
    ).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin não encontrado")
    return admin


def _redir_login():
    return RedirectResponse(url="/admin/login", status_code=302)


# ─── Dashboard ─────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    admin = _get_admin(payload, db)
    agora = datetime.utcnow()
    hoje = agora.date()

    total_alunos_ativos = db.query(Aluno).filter(Aluno.ativo == True).count()

    refeicoes_hoje = db.query(HistoricoRefeicao).filter(
        func.date(HistoricoRefeicao.data_hora) == hoje
    ).count()

    recargas_hoje = db.query(HistoricoRecarga).filter(
        func.date(HistoricoRecarga.data_hora) == hoje,
        HistoricoRecarga.tipo == TipoRecarga.recarga,
        HistoricoRecarga.status == StatusRecarga.aprovado,
    ).count()

    total_creditos = db.query(func.sum(Aluno.creditos)).filter(Aluno.ativo == True).scalar() or 0

    BR = timedelta(hours=-3)
    ultimas_recargas_raw = db.query(HistoricoRecarga, Aluno).join(
        Aluno, HistoricoRecarga.aluno_id == Aluno.id
    ).filter(
        HistoricoRecarga.status == StatusRecarga.aprovado,
    ).order_by(desc(HistoricoRecarga.data_hora)).limit(10).all()

    ultimas_recargas = [
        {
            "tipo": r.tipo,
            "aluno_nome": a.nome.split()[0],
            "aluno_id": a.id,
            "data_hora": r.data_hora + BR,
            "valor": r.valor,
        }
        for r, a in ultimas_recargas_raw
    ]

    labels_dias = []
    dados_dias = []
    for i in range(6, -1, -1):
        dia = (agora - timedelta(days=i)).date()
        count = db.query(HistoricoRefeicao).filter(
            func.date(HistoricoRefeicao.data_hora) == dia
        ).count()
        labels_dias.append(dia.strftime("%d/%m"))
        dados_dias.append(count)

    desperdicios_recentes = (
        db.query(DesperdícioAlimento)
        .order_by(desc(DesperdícioAlimento.registrado_em))
        .limit(5)
        .all()
    )

    status_pico = obter_status_pico(db)

    csrf = gerar_csrf_token()
    resposta = templates.TemplateResponse(request, "admin/dashboard.html", {
        "admin": admin,
        "total_alunos_ativos": total_alunos_ativos,
        "refeicoes_hoje": refeicoes_hoje,
        "recargas_hoje": recargas_hoje,
        "total_creditos": f"{float(total_creditos):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "ultimas_recargas": ultimas_recargas,
        "labels_dias": labels_dias,
        "dados_dias": dados_dias,
        "desperdicios_recentes": desperdicios_recentes,
        "status_pico": status_pico,
        "csrf_token": csrf,
        "BR": timedelta(hours=-3),
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


# ─── Registrar Desperdício ────────────────────────────────────────────────────

@router.post("/registrar-desperdicio")
async def registrar_desperdicio(
    request: Request,
    tipo: str = Form(...),
    nivel: str = Form(...),
    observacao: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    verificar_csrf(request, {"csrf_token": csrf_token})
    admin = _get_admin(payload, db)

    try:
        tipo_enum  = TipoRefeicao(tipo)
        nivel_enum = NivelDesperdicio(nivel)
    except ValueError:
        return RedirectResponse(url="/admin/dashboard?erro=dados_invalidos", status_code=303)

    desperdicio = DesperdícioAlimento(
        tipo=tipo_enum,
        nivel=nivel_enum,
        observacao=observacao.strip() or None,
        registrado_em=datetime.utcnow(),
        registrado_por=admin.id,
    )
    db.add(desperdicio)
    db.commit()

    return RedirectResponse(url="/admin/dashboard?sucesso=desperdicio_ok", status_code=303)


# ─── Listagem de Alunos ────────────────────────────────────────────────────

@router.get("/alunos", response_class=HTMLResponse)
async def listar_alunos(
    request: Request,
    pagina: int = Query(1, ge=1),
    busca: str = Query(""),
    filtro: str = Query("todos"),
    db: Session = Depends(get_db)
):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    admin = _get_admin(payload, db)
    query = db.query(Aluno)

    if busca.strip():
        termo = f"%{busca.strip()}%"
        query = query.filter(
            (Aluno.nome.ilike(termo)) | (Aluno.matricula.ilike(termo))
        )

    if filtro == "ativos":
        query = query.filter(Aluno.ativo == True)
    elif filtro == "inativos":
        query = query.filter(Aluno.ativo == False)

    query = query.order_by(Aluno.nome)
    total = query.count()
    total_paginas = max(1, (total + ITENS_POR_PAGINA_ALUNOS - 1) // ITENS_POR_PAGINA_ALUNOS)
    pagina = min(pagina, total_paginas)
    offset = (pagina - 1) * ITENS_POR_PAGINA_ALUNOS
    alunos = query.offset(offset).limit(ITENS_POR_PAGINA_ALUNOS).all()

    return templates.TemplateResponse(request, "admin/alunos.html", {
        "admin": admin,
        "alunos": alunos,
        "pagina": pagina,
        "total_paginas": total_paginas,
        "total": total,
        "busca": busca,
        "filtro": filtro,
    })


# ─── Perfil do Aluno ────────────────────────────────────────────────────────

@router.get("/alunos/{aluno_id}", response_class=HTMLResponse)
async def perfil_aluno(
    request: Request,
    aluno_id: int,
    pagina_ref: int = Query(1, ge=1),
    pagina_rec: int = Query(1, ge=1),
    db: Session = Depends(get_db)
):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    admin = _get_admin(payload, db)
    aluno = db.query(Aluno).filter(Aluno.id == aluno_id).first()
    if not aluno:
        raise HTTPException(status_code=404, detail="Aluno não encontrado")

    ITEMS = 10
    q_ref = db.query(HistoricoRefeicao).filter(
        HistoricoRefeicao.aluno_id == aluno_id
    ).order_by(desc(HistoricoRefeicao.data_hora))
    total_ref = q_ref.count()
    total_pag_ref = max(1, (total_ref + ITEMS - 1) // ITEMS)
    pagina_ref = min(pagina_ref, total_pag_ref)
    refeicoes = q_ref.offset((pagina_ref - 1) * ITEMS).limit(ITEMS).all()

    q_rec = db.query(HistoricoRecarga).filter(
        HistoricoRecarga.aluno_id == aluno_id
    ).order_by(desc(HistoricoRecarga.data_hora))
    total_rec = q_rec.count()
    total_pag_rec = max(1, (total_rec + ITEMS - 1) // ITEMS)
    pagina_rec = min(pagina_rec, total_pag_rec)
    recargas = q_rec.offset((pagina_rec - 1) * ITEMS).limit(ITEMS).all()

    csrf = gerar_csrf_token()
    resposta = templates.TemplateResponse(request, "admin/aluno_detalhe.html", {
        "admin": admin,
        "aluno": aluno,
        "refeicoes": refeicoes,
        "recargas": recargas,
        "pagina_ref": pagina_ref,
        "total_pag_ref": total_pag_ref,
        "pagina_rec": pagina_rec,
        "total_pag_rec": total_pag_rec,
        "csrf_token": csrf,
        "creditos_formatado": f"{float(aluno.creditos):.2f}".replace(".", ","),
        "sucesso": request.query_params.get("sucesso"),
        "erro": request.query_params.get("erro"),
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


# ─── Recarregar Créditos ───────────────────────────────────────────────────

@router.post("/alunos/{aluno_id}/recarregar")
async def recarregar_creditos(
    request: Request,
    aluno_id: int,
    valor: str = Form(...),
    observacao: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    verificar_csrf(request, {"csrf_token": csrf_token})
    admin = _get_admin(payload, db)
    aluno = db.query(Aluno).filter(Aluno.id == aluno_id).first()
    if not aluno:
        raise HTTPException(status_code=404, detail="Aluno não encontrado")

    try:
        valor_decimal = Decimal(valor.replace(",", "."))
        if valor_decimal <= 0:
            raise ValueError("Valor deve ser positivo")
        if valor_decimal > 9999:
            raise ValueError("Valor máximo é R$ 9.999,99")
    except Exception:
        return RedirectResponse(
            url=f"/admin/alunos/{aluno_id}?erro=Valor+inválido",
            status_code=303
        )

    aluno.creditos = Decimal(str(aluno.creditos)) + valor_decimal

    recarga = HistoricoRecarga(
        aluno_id=aluno_id,
        admin_id=admin.id,
        tipo=TipoRecarga.recarga,
        valor=valor_decimal,
        observacao=observacao.strip() or None,
        data_hora=datetime.utcnow()
    )
    db.add(recarga)
    db.commit()

    return RedirectResponse(
        url=f"/admin/alunos/{aluno_id}?sucesso=Créditos+adicionados+com+sucesso",
        status_code=303
    )


# ─── Editar Dados do Aluno ────────────────────────────────────────────────────

@router.post("/alunos/{aluno_id}/editar")
async def editar_aluno(
    request: Request,
    aluno_id: int,
    nome: str = Form(...),
    email: str = Form(...),
    telefone: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    verificar_csrf(request, {"csrf_token": csrf_token})

    aluno = db.query(Aluno).filter(Aluno.id == aluno_id).first()
    if not aluno:
        raise HTTPException(status_code=404)

    nome = nome.strip()
    email = email.strip().lower()
    telefone = telefone.strip() or None

    if len(nome) < 3:
        return RedirectResponse(
            url=f"/admin/alunos/{aluno_id}?erro=Nome+deve+ter+pelo+menos+3+caracteres",
            status_code=303,
        )

    if "@" not in email or "." not in email:
        return RedirectResponse(
            url=f"/admin/alunos/{aluno_id}?erro=E-mail+inválido",
            status_code=303,
        )

    email_em_uso = db.query(Aluno).filter(
        Aluno.email == email,
        Aluno.id != aluno_id
    ).first()
    if email_em_uso:
        return RedirectResponse(
            url=f"/admin/alunos/{aluno_id}?erro=E-mail+já+em+uso+por+outro+aluno",
            status_code=303,
        )

    aluno.nome = nome
    aluno.email = email
    aluno.telefone = telefone
    db.commit()

    return RedirectResponse(
        url=f"/admin/alunos/{aluno_id}?sucesso=Dados+atualizados+com+sucesso",
        status_code=303,
    )


# ─── Remover Créditos ──────────────────────────────────────────────────────

@router.post("/alunos/{aluno_id}/remover-creditos")
async def remover_creditos(
    request: Request,
    aluno_id: int,
    valor: str = Form(...),
    observacao: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    verificar_csrf(request, {"csrf_token": csrf_token})
    admin = _get_admin(payload, db)
    aluno = db.query(Aluno).filter(Aluno.id == aluno_id).first()
    if not aluno:
        raise HTTPException(status_code=404, detail="Aluno não encontrado")

    if not observacao.strip():
        return RedirectResponse(
            url=f"/admin/alunos/{aluno_id}?erro=Observação+é+obrigatória",
            status_code=303
        )

    try:
        valor_decimal = Decimal(valor.replace(",", "."))
        if valor_decimal <= 0:
            raise ValueError()
    except Exception:
        return RedirectResponse(
            url=f"/admin/alunos/{aluno_id}?erro=Valor+inválido",
            status_code=303
        )

    saldo_atual = Decimal(str(aluno.creditos))
    if valor_decimal > saldo_atual:
        return RedirectResponse(
            url=f"/admin/alunos/{aluno_id}?erro=Saldo+insuficiente",
            status_code=303
        )

    aluno.creditos = saldo_atual - valor_decimal

    remocao = HistoricoRecarga(
        aluno_id=aluno_id,
        admin_id=admin.id,
        tipo=TipoRecarga.remocao,
        valor=valor_decimal,
        observacao=observacao.strip(),
        data_hora=datetime.utcnow()
    )
    db.add(remocao)
    db.commit()

    return RedirectResponse(
        url=f"/admin/alunos/{aluno_id}?sucesso=Créditos+removidos+com+sucesso",
        status_code=303
    )


# ─── Toggle Ativo Aluno ────────────────────────────────────────────────────

@router.post("/alunos/{aluno_id}/toggle-ativo")
async def toggle_ativo_aluno(
    request: Request,
    aluno_id: int,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    verificar_csrf(request, {"csrf_token": csrf_token})
    aluno = db.query(Aluno).filter(Aluno.id == aluno_id).first()
    if not aluno:
        raise HTTPException(status_code=404)

    aluno.ativo = not aluno.ativo
    db.commit()

    status_msg = "Conta+ativada" if aluno.ativo else "Conta+desativada"
    return RedirectResponse(
        url=f"/admin/alunos/{aluno_id}?sucesso={status_msg}",
        status_code=303
    )


# ─── Alterar Categoria do Aluno ───────────────────────────────────────────────

@router.post("/alunos/{aluno_id}/categoria")
async def alterar_categoria(
    request: Request,
    aluno_id: int,
    categoria: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    verificar_csrf(request, {"csrf_token": csrf_token})

    aluno = db.query(Aluno).filter(Aluno.id == aluno_id).first()
    if not aluno:
        raise HTTPException(status_code=404)

    try:
        aluno.categoria = CategoriaAluno(categoria)
    except ValueError:
        return RedirectResponse(
            url=f"/admin/alunos/{aluno_id}?erro=Categoria+inválida",
            status_code=303,
        )

    db.commit()
    return RedirectResponse(
        url=f"/admin/alunos/{aluno_id}?sucesso=Categoria+atualizada",
        status_code=303,
    )


# ─── Histórico Geral ───────────────────────────────────────────────────────

@router.get("/historico", response_class=HTMLResponse)
async def historico_geral(
    request: Request,
    pagina: int = Query(1, ge=1),
    tipo: str = Query("todos"),
    data_inicio: str = Query(""),
    data_fim: str = Query(""),
    aluno_busca: str = Query(""),
    exportar: str = Query(""),
    db: Session = Depends(get_db)
):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    admin = _get_admin(payload, db)

    query = db.query(HistoricoRefeicao, Aluno).join(
        Aluno, HistoricoRefeicao.aluno_id == Aluno.id
    )

    if tipo == "almoco":
        query = query.filter(HistoricoRefeicao.tipo == TipoRefeicao.almoco)
    elif tipo == "jantar":
        query = query.filter(HistoricoRefeicao.tipo == TipoRefeicao.jantar)

    if data_inicio:
        try:
            query = query.filter(
                HistoricoRefeicao.data_hora >= datetime.strptime(data_inicio, "%Y-%m-%d")
            )
        except ValueError:
            pass

    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            query = query.filter(HistoricoRefeicao.data_hora <= dt_fim)
        except ValueError:
            pass

    if aluno_busca.strip():
        termo = f"%{aluno_busca.strip()}%"
        query = query.filter(
            (Aluno.nome.ilike(termo)) | (Aluno.matricula.ilike(termo))
        )

    query = query.order_by(desc(HistoricoRefeicao.data_hora))

    if exportar == "csv":
        registros = query.all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Data/Hora", "Matrícula", "Aluno", "Tipo", "Créditos"])
        for ref, al in registros:
            writer.writerow([
                ref.data_hora.strftime("%d/%m/%Y %H:%M"),
                al.matricula,
                al.nome,
                "Almoço" if ref.tipo == TipoRefeicao.almoco else "Jantar",
                f"R$ {float(ref.creditos_utilizados):.2f}".replace(".", ",")
            ])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=historico_refeicoes.csv"}
        )

    total = query.count()
    total_paginas = max(1, (total + ITENS_POR_PAGINA_HISTORICO - 1) // ITENS_POR_PAGINA_HISTORICO)
    pagina = min(pagina, total_paginas)
    registros = query.offset((pagina - 1) * ITENS_POR_PAGINA_HISTORICO).limit(ITENS_POR_PAGINA_HISTORICO).all()

    return templates.TemplateResponse(request, "admin/historico.html", {
        "admin": admin,
        "registros": registros,
        "pagina": pagina,
        "total_paginas": total_paginas,
        "total": total,
        "tipo": tipo,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "aluno_busca": aluno_busca,
    })


# ─── Gerenciamento de Admins (apenas super_admin) ─────────────────────────

@router.get("/admins", response_class=HTMLResponse)
async def listar_admins(request: Request, db: Session = Depends(get_db)):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    exigir_super_admin(payload)
    admin = _get_admin(payload, db)
    admins = db.query(Admin).order_by(Admin.nome).all()

    csrf = gerar_csrf_token()
    resposta = templates.TemplateResponse(request, "admin/admins.html", {
        "admin": admin,
        "admins": admins,
        "csrf_token": csrf,
        "sucesso": request.query_params.get("sucesso"),
        "erro": request.query_params.get("erro"),
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


@router.post("/admins/criar")
async def criar_admin(
    request: Request,
    nome: str = Form(...),
    email: str = Form(...),
    senha: str = Form(...),
    role: str = Form("operador"),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    exigir_super_admin(payload)
    verificar_csrf(request, {"csrf_token": csrf_token})

    if len(senha) < 8:
        return RedirectResponse(
            url="/admin/admins?erro=Senha+deve+ter+pelo+menos+8+caracteres",
            status_code=303
        )

    existente = db.query(Admin).filter(Admin.email == email.strip().lower()).first()
    if existente:
        return RedirectResponse(
            url="/admin/admins?erro=Email+já+cadastrado",
            status_code=303
        )

    try:
        role_enum = RoleAdmin(role)
    except ValueError:
        role_enum = RoleAdmin.operador

    novo_admin = Admin(
        nome=nome.strip(),
        email=email.strip().lower(),
        senha_hash=hash_senha(senha),
        role=role_enum,
        ativo=True,
        criado_por=int(payload["sub"])
    )
    db.add(novo_admin)
    db.commit()

    return RedirectResponse(
        url="/admin/admins?sucesso=Admin+criado+com+sucesso",
        status_code=303
    )


@router.post("/admins/{admin_id}/toggle-ativo")
async def toggle_ativo_admin(
    request: Request,
    admin_id: int,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    exigir_super_admin(payload)
    verificar_csrf(request, {"csrf_token": csrf_token})

    if admin_id == int(payload["sub"]):
        return RedirectResponse(
            url="/admin/admins?erro=Você+não+pode+desativar+sua+própria+conta",
            status_code=303
        )

    admin_alvo = db.query(Admin).filter(Admin.id == admin_id).first()
    if not admin_alvo:
        raise HTTPException(status_code=404)

    admin_alvo.ativo = not admin_alvo.ativo
    db.commit()

    status_msg = "Admin+ativado" if admin_alvo.ativo else "Admin+desativado"
    return RedirectResponse(url=f"/admin/admins?sucesso={status_msg}", status_code=303)


# ─── Registrar Entrada no RU ──────────────────────────────────────────────────

@router.post("/registrar-entrada/{aluno_id}")
async def registrar_entrada(
    request: Request,
    aluno_id: int,
    tipo: str = Form("almoco"),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Registra a entrada do aluno no RU (debita créditos), detecta pico de
    movimento e agenda a pesquisa de satisfação para 30 minutos depois.
    """
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    verificar_csrf(request, {"csrf_token": csrf_token})
    admin = _get_admin(payload, db)

    aluno = db.query(Aluno).filter(Aluno.id == aluno_id, Aluno.ativo == True).first()
    if not aluno:
        raise HTTPException(status_code=404, detail="Aluno não encontrado")

    try:
        tipo_enum = TipoRefeicao(tipo)
    except ValueError:
        tipo_enum = TipoRefeicao.almoco

    custo = PRECOS_REFEICAO.get(aluno.categoria.value, Decimal("6.00"))
    if custo > 0 and Decimal(str(aluno.creditos)) < custo:
        return RedirectResponse(
            url=f"/admin/alunos/{aluno_id}?erro=Saldo+insuficiente",
            status_code=303,
        )

    if custo > 0:
        aluno.creditos = Decimal(str(aluno.creditos)) - custo

    refeicao = HistoricoRefeicao(
        aluno_id=aluno_id,
        tipo=tipo_enum,
        creditos_utilizados=custo,
        data_hora=datetime.utcnow(),
        registrado_por=admin.id,
    )
    db.add(refeicao)
    db.commit()
    db.refresh(refeicao)

    # Verifica pico e notifica admins se necessário
    verificar_e_registrar_pico(db)

    # Agenda pesquisa de satisfação para 30 min depois (se aluno tem telefone)
    if aluno.telefone:
        asyncio.create_task(agendar_pesquisa_satisfacao(aluno.id, refeicao.id))

    return RedirectResponse(
        url=f"/admin/alunos/{aluno_id}?sucesso=Entrada+registrada+com+sucesso",
        status_code=303,
    )


# ─── Satisfação — painel de resultados ───────────────────────────────────────

@router.get("/satisfacao", response_class=HTMLResponse)
async def painel_satisfacao(request: Request, db: Session = Depends(get_db)):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    admin = _get_admin(payload, db)

    respostas = (
        db.query(SatisfacaoResposta, Aluno)
        .join(Aluno, SatisfacaoResposta.aluno_id == Aluno.id)
        .order_by(SatisfacaoResposta.respondido_em.desc())
        .limit(100)
        .all()
    )

    total_enviados = db.query(SatisfacaoEnvio).count()
    total_respondidos = db.query(SatisfacaoEnvio).filter(SatisfacaoEnvio.respondido == True).count()

    media = None
    if total_respondidos:
        soma = db.query(func.sum(SatisfacaoResposta.nota)).scalar() or 0
        media = round(soma / total_respondidos, 2)

    picos = (
        db.query(PicoMovimento)
        .order_by(PicoMovimento.registrado_em.desc())
        .limit(20)
        .all()
    )

    status_pico = obter_status_pico(db)

    csrf = gerar_csrf_token()
    BR = timedelta(hours=-3)
    resposta = templates.TemplateResponse(request, "admin/satisfacao.html", {
        "admin": admin,
        "respostas": respostas,
        "total_enviados": total_enviados,
        "total_respondidos": total_respondidos,
        "media": media,
        "picos": picos,
        "status_pico": status_pico,
        "BR": BR,
        "csrf_token": csrf,
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


# ─── Alerta de Pico Manual ────────────────────────────────────────────────────

async def _enviar_emails_background(emails: list[str], texto: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, enviar_emails_alerta_lote, emails, "📢 Aviso do RU", texto)


@router.post("/alerta-pico")
async def enviar_alerta_pico_manual(
    request: Request,
    mensagem: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Admin dispara manualmente um alerta de pico para todos os alunos com telefone."""
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    verificar_csrf(request, {"csrf_token": csrf_token})

    texto = mensagem.strip() or (
        "⚠️ O RU está com movimento intenso no momento. "
        "Se possível, aguarde um pouco antes de vir para evitar filas."
    )

    todos_alunos = db.query(Aluno).filter(Aluno.ativo == True).all()

    emails = [a.email for a in todos_alunos if a.email]
    numeros = [a.telefone for a in todos_alunos if a.telefone]

    # Envia WhatsApp de forma síncrona (rápido, poucos números cadastrados)
    whatsapp_ok = sum(
        1 for tel in numeros
        if _enviar_mensagem(_formatar_numero(tel), texto)
    )

    # Dispara o envio em massa de emails em background (não trava a resposta)
    asyncio.create_task(_enviar_emails_background(emails, texto))

    return RedirectResponse(
        url=f"/admin/satisfacao?sucesso=Alerta+disparado+para+{len(emails)}+emails+e+{whatsapp_ok}+WhatsApp",
        status_code=303,
    )


# ─── Cardápio ─────────────────────────────────────────────────────────────────

@router.post("/cardapio/ler-story")
async def ler_cardapio_story(
    request: Request,
    imagem: UploadFile = File(...),
    tipo: str = Form("almoco"),
    csrf_token: str = Form(...),
):
    try:
        obter_admin_atual(request)
    except HTTPException:
        return JSONResponse({"erro": "Não autorizado"}, status_code=401)

    verificar_csrf(request, {"csrf_token": csrf_token})

    try:
        import anthropic
        dados_imagem = await imagem.read()
        imagem_b64 = base64.standard_b64encode(dados_imagem).decode("utf-8")
        media_type = imagem.content_type or "image/jpeg"
        if media_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            media_type = "image/jpeg"

        refeicao_label = "almoço" if tipo == "almoco" else "jantar"
        client = anthropic.Anthropic()
        resposta = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": imagem_b64},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Esta é uma imagem do cardápio do Restaurante Universitário para o {refeicao_label}.\n\n"
                            "Itens FIXOS que sempre estão presentes (não precisa extrair): "
                            "arroz branco, arroz integral, feijão, fruta da época (sobremesa).\n\n"
                            "Extraia APENAS o que muda:\n"
                            "- prato_principal: proteína principal\n"
                            "- opcao_vegetariana: opção sem carne (vazio se não aparecer)\n"
                            "- acomp_extra: acompanhamentos além dos fixos (vazio se não houver)\n"
                            "- observacao: informações especiais (vazio se não houver)\n\n"
                            "Responda SOMENTE com JSON válido sem markdown:\n"
                            '{"prato_principal":"","opcao_vegetariana":"","acomp_extra":"","observacao":""}'
                        ),
                    },
                ],
            }],
        )

        texto = resposta.content[0].text.strip()
        if "```" in texto:
            partes = texto.split("```")
            texto = partes[1] if len(partes) > 1 else partes[0]
            if texto.startswith("json"):
                texto = texto[4:]
        texto = texto.strip()

        dados = json.loads(texto)
        prato   = dados.get("prato_principal", "").strip()
        veg     = dados.get("opcao_vegetariana", "").strip()
        extra   = dados.get("acomp_extra", "").strip()
        obs_raw = dados.get("observacao", "").strip()

        acomp_parts = ["Arroz branco, arroz integral, feijão"]
        if extra:
            acomp_parts.append(extra)
        acompanhamentos = " · ".join(acomp_parts)

        observacao = f"Vegetariana: {veg}" if veg else ""
        if obs_raw:
            observacao = f"{observacao} | {obs_raw}" if observacao else obs_raw

        return JSONResponse({
            "prato_principal": prato,
            "acompanhamentos": acompanhamentos,
            "sobremesa": "Fruta da época",
            "observacao": observacao,
        })

    except json.JSONDecodeError:
        return JSONResponse({"erro": "Não foi possível ler o cardápio. Tente com uma foto mais nítida."}, status_code=422)
    except Exception as exc:
        logger.error("Erro ao ler story cardápio: %s", exc)
        return JSONResponse({"erro": f"Erro: {exc}"}, status_code=500)


@router.get("/cardapio", response_class=HTMLResponse)
async def pagina_cardapio(request: Request, db: Session = Depends(get_db)):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    admin = _get_admin(payload, db)

    from zoneinfo import ZoneInfo
    BR_TZ = ZoneInfo("America/Sao_Paulo")
    hoje = datetime.now(BR_TZ).date()

    # Busca cardápios dos próximos 7 dias
    from datetime import timedelta as td
    datas = [hoje + td(days=i) for i in range(7)]
    cardapios_raw = db.query(Cardapio).filter(
        Cardapio.data >= hoje,
        Cardapio.data <= hoje + td(days=6),
    ).all()

    # Monta dict {data: {tipo: cardapio}}
    por_data: dict = {}
    for c in cardapios_raw:
        por_data.setdefault(c.data, {})[c.tipo.value] = c

    csrf = gerar_csrf_token()
    resposta = templates.TemplateResponse(request, "admin/cardapio.html", {
        "admin": admin,
        "hoje": hoje,
        "datas": datas,
        "por_data": por_data,
        "csrf_token": csrf,
        "sucesso": request.query_params.get("sucesso"),
        "erro": request.query_params.get("erro"),
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


@router.post("/cardapio")
async def salvar_cardapio(
    request: Request,
    data: str = Form(...),
    tipo: str = Form(...),
    prato_principal: str = Form(""),
    acompanhamentos: str = Form(""),
    sobremesa: str = Form(""),
    observacao: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        payload = obter_admin_atual(request)
    except HTTPException:
        return _redir_login()

    verificar_csrf(request, {"csrf_token": csrf_token})

    try:
        data_obj = date.fromisoformat(data)
        tipo_enum = TipoRefeicao(tipo)
    except (ValueError, KeyError):
        return RedirectResponse(url="/admin/cardapio?erro=Dados+inválidos", status_code=303)

    cardapio = db.query(Cardapio).filter(
        Cardapio.data == data_obj,
        Cardapio.tipo == tipo_enum,
    ).first()

    if cardapio:
        cardapio.prato_principal = prato_principal.strip() or None
        cardapio.acompanhamentos = acompanhamentos.strip() or None
        cardapio.sobremesa       = sobremesa.strip() or None
        cardapio.observacao      = observacao.strip() or None
    else:
        cardapio = Cardapio(
            data=data_obj,
            tipo=tipo_enum,
            prato_principal=prato_principal.strip() or None,
            acompanhamentos=acompanhamentos.strip() or None,
            sobremesa=sobremesa.strip() or None,
            observacao=observacao.strip() or None,
        )
        db.add(cardapio)

    db.commit()
    return RedirectResponse(url="/admin/cardapio?sucesso=Cardápio+salvo+com+sucesso", status_code=303)
