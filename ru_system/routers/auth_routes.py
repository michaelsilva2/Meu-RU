"""
Rotas de autenticação: login unificado (aluno + admin), primeiro acesso e recuperação de senha.
"""
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import Aluno, Admin, TokenRecuperacao, SatisfacaoEnvio, SatisfacaoResposta
from auth import (
    hash_senha, verificar_senha,
    criar_token_aluno, criar_token_admin,
    verificar_rate_limit, registrar_tentativa_login, limpar_tentativas_login,
    gerar_csrf_token, verificar_csrf, decodificar_token
)
from email_service import enviar_email_recuperacao, enviar_email_boas_vindas
from whatsapp_bot import enviar_boas_vindas as enviar_whatsapp_boas_vindas
from config import EXPIRACAO_TOKEN_RECUPERACAO_HORAS

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    return forwarded.split(",")[0].strip() if forwarded else request.client.host


# ─── Raiz ──────────────────────────────────────────────────────────────────

@router.get("/")
async def raiz(request: Request):
    if request.cookies.get("aluno_token"):
        return RedirectResponse(url="/aluno/dashboard", status_code=302)
    if request.cookies.get("admin_token"):
        return RedirectResponse(url="/admin/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


# ─── LOGIN UNIFICADO (aluno e admin na mesma página) ─────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    # Redireciona se já logado
    if request.cookies.get("aluno_token"):
        return RedirectResponse(url="/aluno/dashboard", status_code=302)
    if request.cookies.get("admin_token"):
        return RedirectResponse(url="/admin/dashboard", status_code=302)

    csrf = gerar_csrf_token()
    resposta = templates.TemplateResponse(request, "login.html", {
        "csrf_token": csrf,
        "erro": request.query_params.get("erro"),
        "msg": request.query_params.get("msg"),
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


@router.post("/login")
async def login_post(
    request: Request,
    identificador: str = Form(...),   # matrícula OU e-mail
    senha: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    ip = _ip(request)
    verificar_rate_limit(ip)
    verificar_csrf(request, {"csrf_token": csrf_token})

    identificador = identificador.strip()

    def _erro(msg: str):
        csrf = gerar_csrf_token()
        resposta = templates.TemplateResponse(request, "login.html", {
            "csrf_token": csrf,
            "erro": msg,
            "identificador": identificador,
        }, status_code=401)
        resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
        return resposta

    # ── Detecta se é admin (contém @) ou aluno ────────────────────────────
    if "@" in identificador:
        # Tentativa de login como ADMIN
        admin = db.query(Admin).filter(
            Admin.email == identificador.lower(),
            Admin.ativo == True
        ).first()

        if not admin or not verificar_senha(senha, admin.senha_hash):
            registrar_tentativa_login(ip)
            return _erro("E-mail ou senha incorretos")

        limpar_tentativas_login(ip)
        token = criar_token_admin(admin.id, admin.email, admin.role.value)
        resposta = RedirectResponse(url="/admin/dashboard", status_code=303)
        resposta.set_cookie(
            key="admin_token", value=token,
            httponly=True, max_age=12 * 3600, samesite="lax"
        )
        return resposta

    else:
        # Tentativa de login como ALUNO (matrícula)
        aluno = db.query(Aluno).filter(
            Aluno.matricula == identificador,
            Aluno.ativo == True
        ).first()

        if not aluno or not verificar_senha(senha, aluno.senha_hash):
            registrar_tentativa_login(ip)
            return _erro("Matrícula ou senha incorretos")

        limpar_tentativas_login(ip)
        token = criar_token_aluno(aluno.id, aluno.matricula)

        if aluno.primeiro_acesso:
            resposta = RedirectResponse(url="/primeiro-acesso", status_code=303)
        else:
            resposta = RedirectResponse(url="/aluno/dashboard", status_code=303)

        resposta.set_cookie(
            key="aluno_token", value=token,
            httponly=True, max_age=8 * 3600, samesite="lax"
        )
        return resposta


# Redireciona /admin/login para a página unificada
@router.get("/admin/login")
async def admin_login_redir():
    return RedirectResponse(url="/login", status_code=302)


# ─── PRIMEIRO ACESSO — Cadastro completo de dados ─────────────────────────

@router.get("/primeiro-acesso", response_class=HTMLResponse)
async def primeiro_acesso_get(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("aluno_token")
    if not token:
        return RedirectResponse(url="/login", status_code=302)

    try:
        payload = decodificar_token(token)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)

    aluno = db.query(Aluno).filter(Aluno.id == int(payload["sub"])).first()
    if not aluno or not aluno.primeiro_acesso:
        return RedirectResponse(url="/aluno/dashboard", status_code=302)

    csrf = gerar_csrf_token()
    resposta = templates.TemplateResponse(request, "primeiro_acesso.html", {
        "csrf_token": csrf,
        "aluno": aluno,
        "erros": [],
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


@router.post("/primeiro-acesso")
async def primeiro_acesso_post(
    request: Request,
    nome: str = Form(...),
    email: str = Form(...),
    nova_senha: str = Form(...),
    confirmar_senha: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    verificar_csrf(request, {"csrf_token": csrf_token})

    token = request.cookies.get("aluno_token")
    if not token:
        return RedirectResponse(url="/login", status_code=302)

    try:
        payload = decodificar_token(token)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=302)

    aluno_id = int(payload["sub"])
    aluno = db.query(Aluno).filter(Aluno.id == aluno_id).first()
    if not aluno:
        return RedirectResponse(url="/login", status_code=302)

    # ── Validações ────────────────────────────────────────────────────────
    erros = []

    nome = nome.strip()
    email = email.strip().lower()

    if len(nome) < 3:
        erros.append("Nome deve ter pelo menos 3 caracteres")

    if "@" not in email or "." not in email:
        erros.append("E-mail inválido")
    else:
        # Verifica se e-mail já está em uso por outro aluno
        email_em_uso = db.query(Aluno).filter(
            Aluno.email == email,
            Aluno.id != aluno_id
        ).first()
        if email_em_uso:
            erros.append("Este e-mail já está em uso")

    if len(nova_senha) < 8:
        erros.append("Senha deve ter pelo menos 8 caracteres")
    elif not any(c.isupper() for c in nova_senha):
        erros.append("Senha deve conter pelo menos 1 letra maiúscula")
    elif not any(c.isdigit() for c in nova_senha):
        erros.append("Senha deve conter pelo menos 1 número")

    if nova_senha != confirmar_senha:
        erros.append("As senhas não coincidem")

    if erros:
        csrf = gerar_csrf_token()
        resposta = templates.TemplateResponse(request, "primeiro_acesso.html", {
            "csrf_token": csrf,
            "aluno": aluno,
            "erros": erros,
            "form_nome": nome,
            "form_email": email,
        }, status_code=422)
        resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
        return resposta

    # ── Salva os dados ────────────────────────────────────────────────────
    aluno.nome = nome
    aluno.email = email
    aluno.senha_hash = hash_senha(nova_senha)
    aluno.primeiro_acesso = False
    db.commit()

    return RedirectResponse(url="/aluno/dashboard", status_code=303)


# ─── Recuperar Senha ───────────────────────────────────────────────────────

@router.get("/recuperar-senha", response_class=HTMLResponse)
async def recuperar_senha_get(request: Request):
    csrf = gerar_csrf_token()
    resposta = templates.TemplateResponse(request, "recuperar_senha.html", {
        "csrf_token": csrf,
        "enviado": False,
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


@router.post("/recuperar-senha")
async def recuperar_senha_post(
    request: Request,
    email: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    verificar_csrf(request, {"csrf_token": csrf_token})

    aluno = db.query(Aluno).filter(
        Aluno.email == email.strip().lower(),
        Aluno.ativo == True
    ).first()

    if aluno:
        token = secrets.token_hex(32)
        expira = datetime.utcnow() + timedelta(hours=EXPIRACAO_TOKEN_RECUPERACAO_HORAS)

        db.query(TokenRecuperacao).filter(
            TokenRecuperacao.email == email.strip().lower(),
            TokenRecuperacao.usado == False
        ).update({"usado": True})

        novo_token = TokenRecuperacao(
            email=email.strip().lower(),
            token=token,
            expira_em=expira,
            usado=False
        )
        db.add(novo_token)
        db.commit()
        enviar_email_recuperacao(email.strip().lower(), token, aluno.nome)

    csrf = gerar_csrf_token()
    resposta = templates.TemplateResponse(request, "recuperar_senha.html", {
        "csrf_token": csrf,
        "enviado": True,
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


@router.get("/recuperar-senha/{token}", response_class=HTMLResponse)
async def recuperar_senha_token_get(request: Request, token: str, db: Session = Depends(get_db)):
    registro = db.query(TokenRecuperacao).filter(
        TokenRecuperacao.token == token,
        TokenRecuperacao.usado == False
    ).first()

    csrf = gerar_csrf_token()
    valido = registro is not None and registro.expira_em > datetime.utcnow()

    resposta = templates.TemplateResponse(request, "recuperar_senha_form.html", {
        "csrf_token": csrf,
        "token": token,
        "valido": valido,
        "erro": None,
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


@router.post("/recuperar-senha/{token}")
async def recuperar_senha_token_post(
    request: Request,
    token: str,
    nova_senha: str = Form(...),
    confirmar_senha: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    verificar_csrf(request, {"csrf_token": csrf_token})

    registro = db.query(TokenRecuperacao).filter(
        TokenRecuperacao.token == token,
        TokenRecuperacao.usado == False
    ).first()

    if not registro or registro.expira_em <= datetime.utcnow():
        csrf = gerar_csrf_token()
        resposta = templates.TemplateResponse(request, "recuperar_senha_form.html", {
            "csrf_token": csrf,
            "token": token,
            "valido": False,
            "erro": "Link inválido ou expirado",
        })
        resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
        return resposta

    erros = []
    if len(nova_senha) < 8:
        erros.append("Senha deve ter pelo menos 8 caracteres")
    if not any(c.isupper() for c in nova_senha):
        erros.append("Deve conter pelo menos 1 letra maiúscula")
    if not any(c.isdigit() for c in nova_senha):
        erros.append("Deve conter pelo menos 1 número")
    if nova_senha != confirmar_senha:
        erros.append("As senhas não coincidem")

    if erros:
        csrf = gerar_csrf_token()
        resposta = templates.TemplateResponse(request, "recuperar_senha_form.html", {
            "csrf_token": csrf,
            "token": token,
            "valido": True,
            "erro": " | ".join(erros),
        })
        resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
        return resposta

    aluno = db.query(Aluno).filter(
        Aluno.email == registro.email,
        Aluno.ativo == True
    ).first()

    if aluno:
        aluno.senha_hash = hash_senha(nova_senha)
        aluno.primeiro_acesso = False

    registro.usado = True
    db.commit()

    return RedirectResponse(url="/login?msg=senha_redefinida", status_code=303)


# ─── AVALIAÇÃO POR E-MAIL (1 clique, sem login) ───────────────────────────
# Link com token JWT próprio (tipo "avaliacao_email") mandado pelo lembrete
# 30 min após a refeição — ver scheduler.enviar_lembretes_avaliacao_email.

def _envio_do_token(token: str, db: Session) -> SatisfacaoEnvio | None:
    try:
        payload = decodificar_token(token)
    except HTTPException:
        return None
    if payload.get("tipo") != "avaliacao_email":
        return None
    return db.query(SatisfacaoEnvio).filter(SatisfacaoEnvio.id == int(payload["sub"])).first()


@router.get("/avaliacao-email/{token}", response_class=HTMLResponse)
async def avaliacao_email_get(request: Request, token: str, nota: int | None = None, db: Session = Depends(get_db)):
    envio = _envio_do_token(token, db)
    if not envio:
        return templates.TemplateResponse(request, "avaliacao_email.html", {"status": "invalido"})

    if envio.respondido:
        return templates.TemplateResponse(request, "avaliacao_email.html", {"status": "ja_respondido", "token": token})

    if nota is None or nota not in range(1, 6):
        return templates.TemplateResponse(request, "avaliacao_email.html", {"status": "invalido"})

    agora = datetime.utcnow()
    db.add(SatisfacaoResposta(
        envio_id=envio.id,
        aluno_id=envio.aluno_id,
        nota=nota,
        nota_comida=nota,
        nota_servico=nota,
        respondido_em=agora,
    ))
    envio.respondido = True
    db.commit()

    return templates.TemplateResponse(request, "avaliacao_email.html", {"status": "sucesso", "nota": nota, "token": token})


@router.get("/avaliacao-email/{token}/comentario", response_class=HTMLResponse)
async def avaliacao_email_comentario_get(request: Request, token: str, db: Session = Depends(get_db)):
    envio = _envio_do_token(token, db)
    if not envio or not envio.respondido:
        return templates.TemplateResponse(request, "avaliacao_email_comentario.html", {"status": "invalido"})

    csrf = gerar_csrf_token()
    resposta = templates.TemplateResponse(request, "avaliacao_email_comentario.html", {
        "status": "form", "token": token, "csrf_token": csrf,
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


@router.post("/avaliacao-email/{token}/comentario")
async def avaliacao_email_comentario_post(
    request: Request,
    token: str,
    comentario: str = Form(""),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    verificar_csrf(request, {"csrf_token": csrf_token})

    envio = _envio_do_token(token, db)
    if not envio or not envio.resposta:
        return templates.TemplateResponse(request, "avaliacao_email_comentario.html", {"status": "invalido"})

    comentario = comentario.strip()
    if not comentario:
        csrf = gerar_csrf_token()
        resposta = templates.TemplateResponse(request, "avaliacao_email_comentario.html", {
            "status": "form", "token": token, "csrf_token": csrf,
            "erro": "Escreva um comentário antes de enviar.",
        }, status_code=422)
        resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
        return resposta

    envio.resposta.sugestao = comentario[:500]
    db.commit()
    return templates.TemplateResponse(request, "avaliacao_email_comentario.html", {"status": "obrigado"})


# ─── CADASTRO DE NOVO ALUNO ───────────────────────────────────────────────

@router.get("/cadastro", response_class=HTMLResponse)
async def cadastro_get(request: Request):
    if request.cookies.get("aluno_token"):
        return RedirectResponse(url="/aluno/dashboard", status_code=302)
    csrf = gerar_csrf_token()
    resposta = templates.TemplateResponse(request, "cadastro.html", {
        "csrf_token": csrf,
        "erros": [],
    })
    resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
    return resposta


@router.post("/cadastro")
async def cadastro_post(
    request: Request,
    matricula: str = Form(...),
    nome: str = Form(...),
    email: str = Form(...),
    telefone: str = Form(""),
    nova_senha: str = Form(...),
    confirmar_senha: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    verificar_csrf(request, {"csrf_token": csrf_token})

    matricula = matricula.strip()
    nome = nome.strip()
    email = email.strip().lower()

    erros = []

    if len(matricula) < 3:
        erros.append("Matrícula deve ter pelo menos 3 caracteres")
    if len(nome) < 3:
        erros.append("Nome deve ter pelo menos 3 caracteres")
    if "@" not in email or "." not in email:
        erros.append("E-mail inválido")
    if len(nova_senha) < 8:
        erros.append("Senha deve ter pelo menos 8 caracteres")
    elif not any(c.isupper() for c in nova_senha):
        erros.append("Senha deve conter pelo menos 1 letra maiúscula")
    elif not any(c.isdigit() for c in nova_senha):
        erros.append("Senha deve conter pelo menos 1 número")
    if nova_senha != confirmar_senha:
        erros.append("As senhas não coincidem")

    if not erros:
        if db.query(Aluno).filter(Aluno.matricula == matricula).first():
            erros.append("Esta matrícula já está cadastrada")
        if db.query(Aluno).filter(Aluno.email == email).first():
            erros.append("Este e-mail já está em uso")

    if erros:
        csrf = gerar_csrf_token()
        resposta = templates.TemplateResponse(request, "cadastro.html", {
            "csrf_token": csrf,
            "erros": erros,
            "form_matricula": matricula,
            "form_nome": nome,
            "form_email": email,
        }, status_code=422)
        resposta.set_cookie("csrf_token", csrf, httponly=False, samesite="lax")
        return resposta

    novo_aluno = Aluno(
        matricula=matricula,
        nome=nome,
        email=email,
        telefone=telefone.strip() or None,
        senha_hash=hash_senha(nova_senha),
        creditos=0.0,
        primeiro_acesso=False,
        ativo=True,
    )
    db.add(novo_aluno)
    db.commit()
    db.refresh(novo_aluno)

    enviar_email_boas_vindas(novo_aluno.email, novo_aluno.nome, novo_aluno.matricula)
    enviar_whatsapp_boas_vindas(novo_aluno)

    token = criar_token_aluno(novo_aluno.id, novo_aluno.matricula)
    resposta = RedirectResponse(url="/aluno/dashboard", status_code=303)
    resposta.set_cookie(
        key="aluno_token", value=token,
        httponly=True, max_age=8 * 3600, samesite="lax"
    )
    return resposta


# ─── Logouts ──────────────────────────────────────────────────────────────

@router.post("/aluno/logout")
async def logout_aluno():
    resposta = RedirectResponse(url="/login", status_code=303)
    resposta.delete_cookie("aluno_token")
    return resposta


@router.post("/admin/logout")
async def logout_admin():
    resposta = RedirectResponse(url="/login", status_code=303)
    resposta.delete_cookie("admin_token")
    return resposta
