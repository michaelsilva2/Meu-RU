"""
main.py — Entry point da aplicação RU.
Execute com: uvicorn main:app --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from database import engine, Base, SessionLocal
from config import SECRET_KEY

# Importa todos os modelos para criar tabelas
import models  # noqa: F401

# Routers
from routers import auth_routes, aluno, admin
from routers import webhook

from scheduler import criar_scheduler

logger = logging.getLogger(__name__)

# ─── Criação das tabelas ──────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)

# ─── Migração segura: adiciona coluna telefone se não existir (SQLite) ───
def _migrar_colunas_alunos():
    from sqlalchemy import text, inspect
    with engine.connect() as conn:
        inspector = inspect(engine)
        colunas = [c["name"] for c in inspector.get_columns("alunos")]
        if "telefone" not in colunas:
            conn.execute(text("ALTER TABLE alunos ADD COLUMN telefone VARCHAR(20)"))
        if "categoria" not in colunas:
            conn.execute(text("ALTER TABLE alunos ADD COLUMN categoria VARCHAR(20) NOT NULL DEFAULT 'integral'"))
        if "cpf" not in colunas:
            conn.execute(text("ALTER TABLE alunos ADD COLUMN cpf VARCHAR(14)"))
        conn.commit()

_migrar_colunas_alunos()


# ─── Migração segura: colunas de pagamento Pix em historico_recargas ─────
def _migrar_colunas_historico_recargas():
    from sqlalchemy import text, inspect
    with engine.connect() as conn:
        inspector = inspect(engine)
        colunas = [c["name"] for c in inspector.get_columns("historico_recargas")]

        novas_colunas = {
            "status": "VARCHAR(20) NOT NULL DEFAULT 'aprovado'",
            "metodo_pagamento": "VARCHAR(20)",
            "gateway_payment_id": "VARCHAR(64)",
            "external_reference": "VARCHAR(36)",
            "pix_copia_cola": "VARCHAR(1000)",
            "pix_qr_base64": "TEXT",
            "expira_em": "DATETIME",
            "atualizado_em": "DATETIME",
        }
        for nome, tipo_sql in novas_colunas.items():
            if nome not in colunas:
                conn.execute(text(f"ALTER TABLE historico_recargas ADD COLUMN {nome} {tipo_sql}"))

        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_historico_recargas_gateway_payment_id "
            "ON historico_recargas(gateway_payment_id)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_historico_recargas_external_reference "
            "ON historico_recargas(external_reference)"
        ))
        conn.commit()

_migrar_colunas_historico_recargas()


# ─── Migração segura: pesquisa de satisfação em etapas (comida/serviço/sugestão) ─
def _migrar_colunas_satisfacao():
    from sqlalchemy import text, inspect
    with engine.connect() as conn:
        inspector = inspect(engine)

        colunas_envio = [c["name"] for c in inspector.get_columns("satisfacao_envios")]
        if "etapa" not in colunas_envio:
            conn.execute(text("ALTER TABLE satisfacao_envios ADD COLUMN etapa VARCHAR(20) NOT NULL DEFAULT 'comida'"))

        colunas_resposta = [c["name"] for c in inspector.get_columns("satisfacao_respostas")]
        if "nota_comida" not in colunas_resposta:
            conn.execute(text("ALTER TABLE satisfacao_respostas ADD COLUMN nota_comida INTEGER NOT NULL DEFAULT 0"))
        if "nota_servico" not in colunas_resposta:
            conn.execute(text("ALTER TABLE satisfacao_respostas ADD COLUMN nota_servico INTEGER"))
        if "sugestao" not in colunas_resposta:
            conn.execute(text("ALTER TABLE satisfacao_respostas ADD COLUMN sugestao VARCHAR(500)"))
        conn.commit()

_migrar_colunas_satisfacao()


# ─── Migração segura: legumes/salada em cardapios ────────────────────────
def _migrar_colunas_cardapios():
    from sqlalchemy import text, inspect
    with engine.connect() as conn:
        inspector = inspect(engine)
        colunas = [c["name"] for c in inspector.get_columns("cardapios")]
        if "legumes" not in colunas:
            conn.execute(text("ALTER TABLE cardapios ADD COLUMN legumes VARCHAR(200)"))
        if "salada" not in colunas:
            conn.execute(text("ALTER TABLE cardapios ADD COLUMN salada VARCHAR(200)"))
        if "arroz" not in colunas:
            conn.execute(text("ALTER TABLE cardapios ADD COLUMN arroz VARCHAR(200)"))
        if "feijao" not in colunas:
            conn.execute(text("ALTER TABLE cardapios ADD COLUMN feijao VARCHAR(200)"))
        if "vegetariano" not in colunas:
            conn.execute(text("ALTER TABLE cardapios ADD COLUMN vegetariano VARCHAR(200)"))
        conn.commit()

_migrar_colunas_cardapios()


# ─── Migração segura: itens em desperdicios_alimento ─────────────────────
def _migrar_colunas_desperdicios():
    from sqlalchemy import text, inspect
    with engine.connect() as conn:
        inspector = inspect(engine)
        colunas = [c["name"] for c in inspector.get_columns("desperdicios_alimento")]
        if "itens" not in colunas:
            conn.execute(text("ALTER TABLE desperdicios_alimento ADD COLUMN itens VARCHAR(300)"))
        conn.commit()

_migrar_colunas_desperdicios()

# ─── Lifespan (scheduler) ─────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = criar_scheduler()
    scheduler.start()
    logger.info("Scheduler de alertas iniciado.")
    yield
    scheduler.shutdown(wait=False)
    logger.info("Scheduler encerrado.")

# ─── App FastAPI ──────────────────────────────────────────────────────────
app = FastAPI(
    title="RU — Sistema de Créditos",
    description="Gerenciamento de créditos do Restaurante Universitário",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

# ─── Middlewares ──────────────────────────────────────────────────────────
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=86400,   # 24h
)

# ─── Arquivos estáticos ───────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── Templates ───────────────────────────────────────────────────────────
templates = Jinja2Templates(directory="templates")

# ─── Routers ─────────────────────────────────────────────────────────────
app.include_router(auth_routes.router)
app.include_router(aluno.router)
app.include_router(admin.router)
app.include_router(webhook.router)


# ─── Handler de erros 401/403 ────────────────────────────────────────────
@app.exception_handler(401)
async def nao_autenticado(request: Request, exc):
    if request.url.path.startswith("/admin"):
        return RedirectResponse(url="/admin/login", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.exception_handler(403)
async def acesso_negado(request: Request, exc):
    """
    CSRF inválido normalmente é sessão/aba desatualizada, não um ataque real —
    manda de volta pra uma página válida com um aviso em vez do JSON cru.
    """
    from urllib.parse import quote
    detalhe = getattr(exc, "detail", "") or "Acesso negado"

    if "CSRF" in detalhe and request.method == "POST":
        msg = quote("Sua sessão expirou. Tente novamente.")
        if request.url.path.startswith("/admin"):
            destino = "/admin/dashboard" if request.cookies.get("admin_token") else "/admin/login"
        else:
            destino = "/aluno/dashboard" if request.cookies.get("aluno_token") else "/login"
        return RedirectResponse(url=f"{destino}?erro={msg}", status_code=303)

    return JSONResponse({"detail": detalhe}, status_code=403)


@app.exception_handler(404)
async def nao_encontrado(request: Request, exc):
    return templates.TemplateResponse(request, "404.html", {}, status_code=404)


@app.exception_handler(429)
async def muitas_tentativas(request: Request, exc):
    from config import RATE_LIMIT_JANELA_MINUTOS
    return templates.TemplateResponse(
        request, "429.html",
        {"minutos": RATE_LIMIT_JANELA_MINUTOS},
        status_code=429,
    )
