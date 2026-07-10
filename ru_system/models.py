"""
Modelos ORM do SQLAlchemy — mapeamento das tabelas do banco de dados.
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Numeric, Date, Text,
    ForeignKey, Enum as SAEnum, UniqueConstraint
)
from sqlalchemy.orm import relationship
from database import Base
import enum


# ─── Enumerações ────────────────────────────────────────────────────────────

class RoleAdmin(str, enum.Enum):
    super_admin = "super_admin"
    admin = "admin"
    operador = "operador"


class TipoRefeicao(str, enum.Enum):
    almoco = "almoco"
    jantar = "jantar"


class CategoriaAluno(str, enum.Enum):
    bolsista   = "bolsista"    # isento — R$ 0,00
    subsidiado = "subsidiado"  # R$ 4,00
    integral   = "integral"    # R$ 6,00
    externo    = "externo"     # R$ 16,00


class StatusRecarga(str, enum.Enum):
    aprovado  = "aprovado"    # padrão: cobre linhas antigas e ajustes manuais do admin
    pendente  = "pendente"    # cobrança Pix criada, aguardando pagamento
    rejeitado = "rejeitado"
    expirado  = "expirado"
    cancelado = "cancelado"


class TipoRecarga(str, enum.Enum):
    recarga = "recarga"
    remocao = "remocao"
    ajuste = "ajuste"


class NivelDesperdicio(str, enum.Enum):
    baixo = "baixo"
    medio = "medio"
    alto  = "alto"


# ─── Tabelas ─────────────────────────────────────────────────────────────────

class Aluno(Base):
    __tablename__ = "alunos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    matricula = Column(String(20), unique=True, nullable=False, index=True)
    nome = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, nullable=False)
    senha_hash = Column(String(255), nullable=False)
    creditos = Column(Numeric(10, 2), default=0.0, nullable=False)
    telefone  = Column(String(20), nullable=True)
    categoria = Column(SAEnum(CategoriaAluno), default=CategoriaAluno.integral, nullable=False)
    primeiro_acesso = Column(Boolean, default=True, nullable=False)
    ativo = Column(Boolean, default=True, nullable=False)
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    cpf = Column(String(14), nullable=True)                # pedido no 1º pagamento por cartão
    mp_customer_id = Column(String(64), nullable=True)      # cliente no Mercado Pago (cartões salvos)

    # Relacionamentos
    refeicoes = relationship("HistoricoRefeicao", back_populates="aluno", lazy="dynamic")
    recargas = relationship("HistoricoRecarga", back_populates="aluno", lazy="dynamic")


class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, nullable=False)
    senha_hash = Column(String(255), nullable=False)
    role = Column(SAEnum(RoleAdmin), default=RoleAdmin.operador, nullable=False)
    ativo = Column(Boolean, default=True, nullable=False)
    criado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    criado_por = Column(Integer, ForeignKey("admins.id"), nullable=True)

    # Relacionamentos
    recargas_feitas = relationship("HistoricoRecarga", back_populates="admin", lazy="dynamic")
    refeicoes_registradas = relationship("HistoricoRefeicao", back_populates="registrado_por_admin", lazy="dynamic")
    sub_admins = relationship("Admin", backref="criador", remote_side=[id])


class HistoricoRefeicao(Base):
    __tablename__ = "historico_refeicoes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    aluno_id = Column(Integer, ForeignKey("alunos.id"), nullable=False)
    tipo = Column(SAEnum(TipoRefeicao), nullable=False)
    creditos_utilizados = Column(Numeric(10, 2), nullable=False)
    data_hora = Column(DateTime, default=datetime.utcnow, nullable=False)
    registrado_por = Column(Integer, ForeignKey("admins.id"), nullable=True)  # null = autoatendimento

    # Relacionamentos
    aluno = relationship("Aluno", back_populates="refeicoes")
    registrado_por_admin = relationship("Admin", back_populates="refeicoes_registradas")


class HistoricoRecarga(Base):
    __tablename__ = "historico_recargas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    aluno_id = Column(Integer, ForeignKey("alunos.id"), nullable=False)
    admin_id = Column(Integer, ForeignKey("admins.id"), nullable=True)   # null = recarga pelo próprio aluno
    tipo = Column(SAEnum(TipoRecarga), nullable=False)
    valor = Column(Numeric(10, 2), nullable=False)
    observacao = Column(String(500), nullable=True)
    data_hora = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ── Pagamento via Pix (Mercado Pago) ──────────────────────────────────
    status = Column(SAEnum(StatusRecarga), default=StatusRecarga.aprovado, nullable=False)
    metodo_pagamento = Column(String(20), nullable=True)          # "pix" | null/"manual" p/ ajustes do admin
    gateway_payment_id = Column(String(64), nullable=True)        # id do pagamento no Mercado Pago
    external_reference = Column(String(36), nullable=True)        # UUID gerado por nós, correlaciona com o webhook
    pix_copia_cola = Column(String(1000), nullable=True)
    pix_qr_base64 = Column(Text, nullable=True)
    expira_em = Column(DateTime, nullable=True)
    atualizado_em = Column(DateTime, nullable=True)

    # Relacionamentos
    aluno = relationship("Aluno", back_populates="recargas")
    admin = relationship("Admin", back_populates="recargas_feitas")


class TokenRecuperacao(Base):
    __tablename__ = "tokens_recuperacao"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(150), nullable=False)
    token = Column(String(64), unique=True, nullable=False)
    expira_em = Column(DateTime, nullable=False)
    usado = Column(Boolean, default=False, nullable=False)


class SatisfacaoEnvio(Base):
    """Rastreia cada pesquisa de satisfação enviada por WhatsApp após uma refeição."""
    __tablename__ = "satisfacao_envios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    aluno_id = Column(Integer, ForeignKey("alunos.id"), nullable=False)
    refeicao_id = Column(Integer, ForeignKey("historico_refeicoes.id"), nullable=False, unique=True)
    enviado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    respondido = Column(Boolean, default=False, nullable=False)

    aluno = relationship("Aluno")
    refeicao = relationship("HistoricoRefeicao")
    resposta = relationship("SatisfacaoResposta", back_populates="envio", uselist=False)


class SatisfacaoResposta(Base):
    """Armazena a nota (1-5) respondida pelo aluno via WhatsApp."""
    __tablename__ = "satisfacao_respostas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    envio_id = Column(Integer, ForeignKey("satisfacao_envios.id"), nullable=False, unique=True)
    aluno_id = Column(Integer, ForeignKey("alunos.id"), nullable=False)
    nota = Column(Integer, nullable=False)
    respondido_em = Column(DateTime, nullable=False)

    envio = relationship("SatisfacaoEnvio", back_populates="resposta")
    aluno = relationship("Aluno")


class DesperdícioAlimento(Base):
    """Registro de desperdício percebido pelo admin ao final de cada refeição."""
    __tablename__ = "desperdicios_alimento"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    tipo           = Column(SAEnum(TipoRefeicao), nullable=False)
    nivel          = Column(SAEnum(NivelDesperdicio), nullable=False)
    observacao     = Column(String(300), nullable=True)
    registrado_em  = Column(DateTime, default=datetime.utcnow, nullable=False)
    registrado_por = Column(Integer, ForeignKey("admins.id"), nullable=True)

    admin = relationship("Admin")


class PicoMovimento(Base):
    """Registra eventos de pico de movimento no RU."""
    __tablename__ = "picos_movimento"

    id = Column(Integer, primary_key=True, autoincrement=True)
    acessos_na_janela = Column(Integer, nullable=False)
    registrado_em = Column(DateTime, default=datetime.utcnow, nullable=False)
    alerta_enviado = Column(Boolean, default=False, nullable=False)


class Cardapio(Base):
    """Cardápio do dia para almoço ou jantar."""
    __tablename__ = "cardapios"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    data            = Column(Date, nullable=False)
    tipo            = Column(SAEnum(TipoRefeicao), nullable=False)
    prato_principal = Column(String(200), nullable=True)
    acompanhamentos = Column(String(500), nullable=True)
    sobremesa       = Column(String(200), nullable=True)
    observacao      = Column(String(300), nullable=True)
    criado_em       = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("data", "tipo", name="uq_cardapio_data_tipo"),
    )
