"""
whatsapp_bot.py — Lógica do bot WhatsApp via Twilio.

Funcionalidades:
  1. Boas-vindas: ao concluir o cadastro, envia confirmação + como usar o Meu RU.
  2. Confirmação de presença: 1h antes do RU abrir, pergunta se o aluno vai à refeição.
  3. Pesquisa de satisfação: 30 min após o aluno entrar no RU, envia
     mensagem perguntando nota de 1 a 5.
  4. Alerta de pico: detecta movimento intenso e avisa os admins.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from config import (
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_API_KEY, TWILIO_API_SECRET,
    TWILIO_WHATSAPP_FROM, ADMIN_WHATSAPP_NUMEROS, LIMIAR_PICO, JANELA_PICO_MIN,
    INTERVALO_ALERTA_PICO_MIN,
)
from database import SessionLocal
from models import (
    Aluno, HistoricoRefeicao, TipoRefeicao,
    SatisfacaoEnvio, SatisfacaoResposta, PicoMovimento, ConfirmacaoPresenca,
    Cardapio, SugestaoAvulsa,
)

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _twilio_habilitado() -> bool:
    return bool(TWILIO_ACCOUNT_SID and (TWILIO_AUTH_TOKEN or (TWILIO_API_KEY and TWILIO_API_SECRET)))


def _get_twilio_client():
    from twilio.rest import Client
    if TWILIO_AUTH_TOKEN:
        return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return Client(TWILIO_API_KEY, TWILIO_API_SECRET, TWILIO_ACCOUNT_SID)


def _formatar_numero(telefone: str) -> str:
    digits = "".join(c for c in telefone if c.isdigit())
    if not digits.startswith("55"):
        digits = "55" + digits
    return f"whatsapp:+{digits}"


def _enviar_mensagem(para: str, corpo: str) -> bool:
    if not _twilio_habilitado():
        logger.info("[BOT-SIMULADO] Para %s: %s", para, corpo)
        return True
    try:
        client = _get_twilio_client()
        client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=para, body=corpo)
        return True
    except Exception as exc:
        logger.error("Erro ao enviar WhatsApp para %s: %s", para, exc)
        return False


# ── Boas-vindas (enviada uma vez, ao concluir o cadastro) ───────────────────

def enviar_boas_vindas(aluno: Aluno) -> None:
    """Confirma o cadastro por WhatsApp e explica como usar o Meu RU."""
    if not aluno.telefone:
        return
    numero = _formatar_numero(aluno.telefone)
    primeiro_nome = aluno.nome.split()[0]
    corpo = (
        f"✅ Cadastro confirmado, {primeiro_nome}! Bem-vindo(a) ao Meu RU 🍽️\n\n"
        "Aqui vai um resumo rápido de como funciona:\n\n"
        "⏰ *Horários*\n"
        "Almoço: 11h às 14h\n"
        "Jantar: 19h às 21h\n\n"
        "📋 *Cardápio do dia* — digite *CARDÁPIO* aqui ou veja no seu painel, na aba Dashboard.\n\n"
        "💳 *Créditos* — recarregue via Pix ou cartão na aba Recarga.\n\n"
        "📱 *Como usar* — na hora da refeição, abra seu painel > QR Code e "
        "mostre a tela pro atendente escanear. Simples assim, sem fila de caderno!\n\n"
        "📊 *Histórico* — acompanhe suas refeições e recargas na aba Histórico.\n\n"
        "Qualquer dúvida, é só chamar por aqui. Bom apetite! 😋"
    )
    _enviar_mensagem(numero, corpo)


# ── Cardápio via WhatsApp (aluno pergunta "cardápio", "menu", etc.) ─────────

def _texto_pede_cardapio(texto: str) -> bool:
    normalizado = texto.strip().lower().strip(".!?")
    return "cardapio" in normalizado or "cardápio" in normalizado


def _texto_pede_amanha(texto: str) -> bool:
    normalizado = texto.strip().lower()
    return "amanha" in normalizado or "amanhã" in normalizado


def _formatar_cardapio_refeicao(cardapio: "Cardapio | None", emoji: str, label: str) -> str:
    if not cardapio or not (cardapio.prato_principal or cardapio.arroz or cardapio.feijao):
        return f"{emoji} *{label}*: ainda não cadastrado."
    linhas = [f"{emoji} *{label}*"]
    if cardapio.prato_principal:
        linhas.append(cardapio.prato_principal)
    if cardapio.arroz:
        linhas.append(f"🍚 {cardapio.arroz}")
    if cardapio.feijao:
        linhas.append(f"🫘 {cardapio.feijao}")
    if cardapio.legumes:
        linhas.append(f"🥦 {cardapio.legumes}")
    if cardapio.salada:
        linhas.append(f"🥗 {cardapio.salada}")
    if cardapio.sobremesa:
        linhas.append(f"🍮 {cardapio.sobremesa}")
    if cardapio.vegetariano:
        linhas.append(f"🌱 {cardapio.vegetariano}")
    if cardapio.observacao:
        linhas.append(f"_{cardapio.observacao}_")
    return "\n".join(linhas)


def _processar_pedido_cardapio(aluno: Aluno, texto: str, db: Session) -> bool:
    """Se o aluno pediu o cardápio, responde com o de hoje e retorna True."""
    if not _texto_pede_cardapio(texto):
        return False

    from zoneinfo import ZoneInfo
    hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()

    if _texto_pede_amanha(texto):
        amanha = hoje + timedelta(days=1)
        almoco_amanha = db.query(Cardapio).filter(
            Cardapio.data == amanha, Cardapio.tipo == TipoRefeicao.almoco
        ).first()
        corpo = (
            f"📋 *Cardápio do almoço de amanhã ({amanha.strftime('%d/%m')})*\n\n"
            f"{_formatar_cardapio_refeicao(almoco_amanha, '🌞', 'Almoço')}"
        )
    else:
        almoco = db.query(Cardapio).filter(
            Cardapio.data == hoje, Cardapio.tipo == TipoRefeicao.almoco
        ).first()
        jantar = db.query(Cardapio).filter(
            Cardapio.data == hoje, Cardapio.tipo == TipoRefeicao.jantar
        ).first()
        corpo = (
            f"📋 *Cardápio de hoje ({hoje.strftime('%d/%m')})*\n\n"
            f"{_formatar_cardapio_refeicao(almoco, '🌞', 'Almoço')}\n\n"
            f"{_formatar_cardapio_refeicao(jantar, '🌙', 'Jantar')}"
        )

    _enviar_mensagem(_formatar_numero(aluno.telefone), corpo)
    return True


# ── Demais opções do bot (espelham o menu do widget do site) ────────────────

_TEXTO_HORARIOS = (
    "⏰ *Horários do RU*\n\n"
    "🌞 Almoço: 11h às 14h\n"
    "🌙 Jantar: 19h às 21h\n\n"
    "Segunda a sexta, exceto feriados."
)

_TEXTO_CODIGO_ACESSO = (
    "📱 *Código de acesso*\n\n"
    "No seu painel do Meu RU, abra *QR Code* — ele gera um código que se renova "
    "sozinho a cada poucos segundos.\n\n"
    "Mostre a tela pro atendente na entrada do RU: ele lê o código e sua "
    "refeição já é registrada na hora. Cada código vale só uma vez, então "
    "print ou compartilhar não funciona. 🔒"
)

_TEXTO_RECARGA = (
    "💳 *Como recarregar*\n\n"
    "No seu painel do Meu RU, toque em *Recarregar créditos* e escolha um valor "
    "entre R$ 1,00 e R$ 500,00.\n\n"
    "💠 Pix: gera QR code e código copia-e-cola, saldo entra na hora que "
    "aprovar.\n"
    "💳 Cartão: crédito ou débito, aprovação na hora."
)

_TEXTO_MENU_PRINCIPAL = (
    "👋 Oi! No que posso te ajudar? Manda uma das palavras abaixo:\n\n"
    "🌞 *CARDÁPIO* — almoço e jantar de hoje\n"
    "📅 *CARDÁPIO AMANHÃ* — almoço de amanhã\n"
    "⏰ *HORÁRIOS* — horários do RU\n"
    "🧾 *HISTÓRICO* — suas últimas refeições\n"
    "📱 *CÓDIGO* — como usar o código de acesso\n"
    "💳 *RECARGA* — como recarregar créditos\n"
    "⭐ *AVALIAÇÃO* — avaliar sua última refeição\n"
    "💡 *SUGESTÃO* — mandar uma sugestão pro RU\n"
    "💬 *ATENDENTE* — falar com alguém do RU"
)


def _texto_pede_menu_principal(texto: str) -> bool:
    normalizado = texto.strip().lower().strip(".!?")
    if normalizado in {"menu", "oi", "ola", "olá", "opcoes", "opções", "ajuda"}:
        return True
    # Mensagem padrão do botão "Falar com o RU" (widget do site)
    return "vim pelo site" in normalizado and "meu ru" in normalizado


def _texto_pede_horarios(texto: str) -> bool:
    normalizado = texto.strip().lower()
    return "horario" in normalizado or "horário" in normalizado


def _texto_pede_historico(texto: str) -> bool:
    normalizado = texto.strip().lower()
    return "historico" in normalizado or "histórico" in normalizado or "ultimas refeic" in normalizado or "últimas refeiç" in normalizado


def _texto_pede_codigo_acesso(texto: str) -> bool:
    normalizado = texto.strip().lower()
    return "codigo" in normalizado or "código" in normalizado or "qr code" in normalizado or "qrcode" in normalizado


def _texto_pede_recarga(texto: str) -> bool:
    normalizado = texto.strip().lower()
    return "recarg" in normalizado or "credito" in normalizado or "crédito" in normalizado

def _texto_pede_atendente(texto: str) -> bool:
    normalizado = texto.strip().lower().strip(".!?")
    return normalizado in {"atendente", "suporte", "humano"} or "falar com" in normalizado


def _texto_pede_sugestao(texto: str) -> bool:
    normalizado = texto.strip().lower().strip(".!?")
    return normalizado in {"sugestao", "sugestão", "sugerir"}


def _texto_pede_avaliacao(texto: str) -> bool:
    normalizado = texto.strip().lower().strip(".!?")
    return normalizado in {"avaliacao", "avaliação", "avaliar"}


def _processar_pedido_horarios(aluno: Aluno, texto: str) -> bool:
    if not _texto_pede_horarios(texto):
        return False
    _enviar_mensagem(_formatar_numero(aluno.telefone), _TEXTO_HORARIOS)
    return True


def _processar_pedido_historico(aluno: Aluno, texto: str, db: Session) -> bool:
    if not _texto_pede_historico(texto):
        return False

    refeicoes = (
        db.query(HistoricoRefeicao)
        .filter(HistoricoRefeicao.aluno_id == aluno.id)
        .order_by(HistoricoRefeicao.data_hora.desc())
        .limit(3)
        .all()
    )
    if not refeicoes:
        corpo = "Você ainda não registrou nenhuma refeição por aqui. 🍽️"
    else:
        linhas = ["🧾 *Últimas refeições*", ""]
        for r in refeicoes:
            emoji = "🌞" if r.tipo == TipoRefeicao.almoco else "🌙"
            label = "Almoço" if r.tipo == TipoRefeicao.almoco else "Jantar"
            custo = float(r.creditos_utilizados)
            custo_str = f"R$ {custo:.2f}".replace(".", ",") if custo > 0 else "gratuita"
            data_str = r.data_hora.strftime("%d/%m às %H:%M")
            linhas.append(f"{emoji} {label} — {data_str} · {custo_str}")
        corpo = "\n".join(linhas)

    _enviar_mensagem(_formatar_numero(aluno.telefone), corpo)
    return True


def _processar_pedido_codigo_acesso(aluno: Aluno, texto: str) -> bool:
    if not _texto_pede_codigo_acesso(texto):
        return False
    _enviar_mensagem(_formatar_numero(aluno.telefone), _TEXTO_CODIGO_ACESSO)
    return True


def _processar_pedido_recarga(aluno: Aluno, texto: str) -> bool:
    if not _texto_pede_recarga(texto):
        return False
    _enviar_mensagem(_formatar_numero(aluno.telefone), _TEXTO_RECARGA)
    return True


def _processar_pedido_atendente(aluno: Aluno, texto: str) -> bool:
    if not _texto_pede_atendente(texto):
        return False

    numero = _formatar_numero(aluno.telefone)
    _enviar_mensagem(
        numero,
        "💬 Escreva o que você está enfrentando por email:\n\n"
        "📧 meuru.ufcat@gmail.com\n"
        "https://mail.google.com/mail/?view=cm&fs=1&to=meuru.ufcat@gmail.com&su=Suporte%20-%20Meu%20RU",
    )
    if ADMIN_WHATSAPP_NUMEROS:
        primeiro_nome = aluno.nome.split()[0]
        aviso = f"💬 *{primeiro_nome}* pediu para falar com um atendente."
        for admin_numero in ADMIN_WHATSAPP_NUMEROS:
            _enviar_mensagem(_formatar_numero(admin_numero), aviso)
    return True


def _processar_pedido_sugestao(aluno: Aluno, texto: str, db: Session) -> bool:
    if not _texto_pede_sugestao(texto):
        return False
    db.add(SugestaoAvulsa(aluno_id=aluno.id))
    db.commit()
    _enviar_mensagem(
        _formatar_numero(aluno.telefone),
        "💡 Pode escrever sua sugestão na próxima mensagem — vamos ler com atenção!",
    )
    return True


def _processar_pedido_avaliacao(aluno: Aluno, texto: str, db: Session) -> bool:
    if not _texto_pede_avaliacao(texto):
        return False

    numero = _formatar_numero(aluno.telefone)
    refeicao = (
        db.query(HistoricoRefeicao)
        .outerjoin(SatisfacaoEnvio, SatisfacaoEnvio.refeicao_id == HistoricoRefeicao.id)
        .filter(HistoricoRefeicao.aluno_id == aluno.id, SatisfacaoEnvio.id.is_(None))
        .order_by(HistoricoRefeicao.data_hora.desc())
        .first()
    )
    if not refeicao:
        _enviar_mensagem(numero, "Você ainda não tem nenhuma refeição pra avaliar (ou já avaliou todas)! 😊")
        return True

    envio = SatisfacaoEnvio(
        aluno_id=aluno.id,
        refeicao_id=refeicao.id,
        enviado_em=datetime.utcnow(),
        etapa="comida",
    )
    db.add(envio)
    db.commit()
    _enviar_mensagem(numero, _PERGUNTA_COMIDA)
    return True


def _processar_menu_principal(aluno: Aluno, texto: str) -> bool:
    if not _texto_pede_menu_principal(texto):
        return False
    _enviar_mensagem(_formatar_numero(aluno.telefone), _TEXTO_MENU_PRINCIPAL)
    return True


# ── Confirmação de presença (1h antes da abertura) ──────────────────────────

_PERGUNTA_PRESENCA = {
    TipoRefeicao.almoco: "🍽️ Vai almoçar no RU hoje? Responda *SIM* ou *NÃO* pra gente se organizar.",
    TipoRefeicao.jantar: "🌙 Vai jantar no RU hoje? Responda *SIM* ou *NÃO* pra gente se organizar.",
}

_AFIRMATIVOS = {"sim", "s", "yes", "y"}
_NEGATIVOS = {"nao", "não", "n", "no"}


def enviar_alerta_whatsapp(mensagem: str) -> int:
    """Envia `mensagem` por WhatsApp para todos os alunos ativos com telefone.

    Usado pelos alertas automáticos de abertura/fechamento do RU (scheduler.py),
    que hoje já disparam por email — isso adiciona o mesmo aviso no WhatsApp.
    """
    db: Session = SessionLocal()
    try:
        alunos = db.query(Aluno).filter(
            Aluno.ativo == True, Aluno.telefone.isnot(None)
        ).all()
        enviados = 0
        for aluno in alunos:
            if _enviar_mensagem(_formatar_numero(aluno.telefone), mensagem):
                enviados += 1
        logger.info("Alerta WhatsApp enviado para %d aluno(s).", enviados)
        return enviados
    except Exception:
        logger.exception("Erro ao enviar alerta WhatsApp em massa")
        return 0
    finally:
        db.close()


def enviar_confirmacoes_presenca(tipo: TipoRefeicao, data_referencia: date) -> None:
    """Pergunta a todos os alunos ativos com telefone se vão à refeição de hoje."""
    db: Session = SessionLocal()
    try:
        alunos = db.query(Aluno).filter(
            Aluno.ativo == True, Aluno.telefone.isnot(None)
        ).all()
        pergunta = _PERGUNTA_PRESENCA[tipo]
        enviados = 0
        for aluno in alunos:
            ja_existe = db.query(ConfirmacaoPresenca).filter(
                ConfirmacaoPresenca.aluno_id == aluno.id,
                ConfirmacaoPresenca.tipo == tipo,
                ConfirmacaoPresenca.data == data_referencia,
            ).first()
            if ja_existe:
                continue
            numero = _formatar_numero(aluno.telefone)
            if _enviar_mensagem(numero, pergunta):
                db.add(ConfirmacaoPresenca(
                    aluno_id=aluno.id, tipo=tipo, data=data_referencia,
                ))
                db.commit()
                enviados += 1
        logger.info("Confirmação de presença (%s) enviada para %d aluno(s).", tipo.value, enviados)
    except Exception:
        logger.exception("Erro ao enviar confirmações de presença")
        db.rollback()
    finally:
        db.close()


def _processar_resposta_presenca(aluno: Aluno, texto: str, db: Session) -> bool:
    """
    Se houver confirmação de presença pendente e a resposta for SIM/NÃO,
    registra e retorna True (mensagem consumida). Caso contrário, retorna
    False para o chamador seguir tentando outros fluxos (ex: satisfação).
    """
    pendente = (
        db.query(ConfirmacaoPresenca)
        .filter(ConfirmacaoPresenca.aluno_id == aluno.id, ConfirmacaoPresenca.respondido == False)
        .order_by(ConfirmacaoPresenca.enviado_em.desc())
        .first()
    )
    if not pendente:
        return False

    normalizado = texto.strip().lower().strip(".!?")
    if normalizado in _AFIRMATIVOS:
        vai = True
    elif normalizado in _NEGATIVOS:
        vai = False
    else:
        return False

    pendente.vai = vai
    pendente.respondido = True
    pendente.respondido_em = datetime.utcnow()
    db.commit()

    numero = _formatar_numero(aluno.telefone)
    if vai:
        _enviar_mensagem(numero, "Show, te esperamos! 🍽️")
    else:
        _enviar_mensagem(numero, "Combinado, obrigado por avisar! 👍")
    return True


# ── Pesquisa de satisfação (3 etapas: comida → serviço → sugestão) ───────────

_PERGUNTA_COMIDA = (
    "⏰ Hora de avaliar!\n\n"
    "Qual o nível da *comida* de hoje?\n\n"
    "1 - Péssima\n"
    "2 - Ruim\n"
    "3 - Regular\n"
    "4 - Boa\n"
    "5 - Excelente"
)

_PERGUNTA_SERVICO = (
    "Valeu! E o *atendimento/serviço*, como foi?\n\n"
    "1 - Péssimo\n"
    "2 - Ruim\n"
    "3 - Regular\n"
    "4 - Bom\n"
    "5 - Excelente"
)

_PERGUNTA_SUGESTAO = (
    "Por último: tem alguma *dica pra gente melhorar*? 💡\n\n"
    "Pode escrever livremente, ou responder \"não\" se não tiver sugestão."
)


async def agendar_pesquisa_satisfacao(aluno_id: int, refeicao_id: int, delay_segundos: int = 1800):
    """Aguarda `delay_segundos` e então inicia a pesquisa (etapa 'comida')."""
    await asyncio.sleep(delay_segundos)

    db: Session = SessionLocal()
    try:
        aluno = db.query(Aluno).filter(Aluno.id == aluno_id, Aluno.ativo == True).first()
        if not aluno or not aluno.telefone:
            return

        ja_enviado = db.query(SatisfacaoEnvio).filter(
            SatisfacaoEnvio.refeicao_id == refeicao_id
        ).first()
        if ja_enviado:
            return

        numero = _formatar_numero(aluno.telefone)
        corpo = f"Olá, {aluno.nome.split()[0]}! 👋\n\n{_PERGUNTA_COMIDA}"

        if _enviar_mensagem(numero, corpo):
            envio = SatisfacaoEnvio(
                aluno_id=aluno_id,
                refeicao_id=refeicao_id,
                enviado_em=datetime.utcnow(),
                etapa="comida",
            )
            db.add(envio)
            db.commit()
            logger.info("Pesquisa enviada para aluno_id=%s refeicao_id=%s", aluno_id, refeicao_id)
    except Exception as exc:
        logger.error("Erro ao enviar pesquisa satisfacao: %s", exc)
        db.rollback()
    finally:
        db.close()


def processar_resposta_satisfacao(from_number: str, body: str, db: Session):
    """
    Recebe a mensagem do aluno e avança a pesquisa conforme a etapa pendente:
    comida (nota 1-5) → serviço (nota 1-5) → sugestão (texto livre) → concluída.
    """
    texto = body.strip()

    # Localiza o aluno pelo número (últimos 10 dígitos para flexibilidade)
    digits = "".join(c for c in from_number if c.isdigit())
    sufixo = digits[-10:]

    aluno = db.query(Aluno).filter(
        Aluno.telefone.ilike(f"%{sufixo}")
    ).first()
    if not aluno:
        return

    envio = (
        db.query(SatisfacaoEnvio)
        .filter(SatisfacaoEnvio.aluno_id == aluno.id, SatisfacaoEnvio.respondido == False)
        .order_by(SatisfacaoEnvio.enviado_em.desc())
        .first()
    )

    # Etapa de sugestão aceita texto livre — precisa ser checada antes dos
    # comandos por palavra-chave, senão uma sugestão que mencione "horário"
    # ou "atendente" seria sequestrada pelo comando errado em vez de salva.
    if envio and envio.etapa == "sugestao":
        numero = _formatar_numero(aluno.telefone)
        if texto.lower() not in {"nao", "não", "n", "não.", "nao."}:
            envio.resposta.sugestao = texto[:500]
        envio.resposta.respondido_em = datetime.utcnow()
        envio.respondido = True
        db.commit()
        _enviar_mensagem(numero, "Obrigado pelo seu feedback! 😊 Sua opinião nos ajuda a melhorar o RU.")
        logger.info("Pesquisa satisfacao concluída: aluno_id=%s envio_id=%s", aluno.id, envio.id)
        return

    # Sugestão avulsa pendente também aceita texto livre — mesma prioridade
    # da etapa de sugestão da pesquisa, por exatamente o mesmo motivo.
    sugestao_pendente = (
        db.query(SugestaoAvulsa)
        .filter(SugestaoAvulsa.aluno_id == aluno.id, SugestaoAvulsa.texto.is_(None))
        .order_by(SugestaoAvulsa.criado_em.desc())
        .first()
    )
    if sugestao_pendente:
        sugestao_pendente.texto = texto[:500]
        sugestao_pendente.respondido_em = datetime.utcnow()
        db.commit()
        _enviar_mensagem(_formatar_numero(aluno.telefone), "Sugestão registrada, muito obrigado! 🙌")
        return

    if _processar_pedido_cardapio(aluno, texto, db):
        return
    if _processar_pedido_horarios(aluno, texto):
        return
    if _processar_pedido_historico(aluno, texto, db):
        return
    if _processar_pedido_codigo_acesso(aluno, texto):
        return
    if _processar_pedido_recarga(aluno, texto):
        return
    if _processar_pedido_sugestao(aluno, texto, db):
        return
    if _processar_pedido_avaliacao(aluno, texto, db):
        return
    if _processar_pedido_atendente(aluno, texto):
        return
    if _processar_menu_principal(aluno, texto):
        return

    if _processar_resposta_presenca(aluno, texto, db):
        return

    if not envio:
        return

    numero = _formatar_numero(aluno.telefone)

    if envio.etapa == "comida":
        if texto not in {"1", "2", "3", "4", "5"}:
            return
        resposta = SatisfacaoResposta(
            envio_id=envio.id,
            aluno_id=aluno.id,
            nota=int(texto),
            nota_comida=int(texto),
            respondido_em=datetime.utcnow(),
        )
        db.add(resposta)
        envio.etapa = "servico"
        db.commit()
        _enviar_mensagem(numero, _PERGUNTA_SERVICO)
        return

    if envio.etapa == "servico":
        if texto not in {"1", "2", "3", "4", "5"}:
            return
        envio.resposta.nota_servico = int(texto)
        envio.etapa = "sugestao"
        db.commit()
        _enviar_mensagem(numero, _PERGUNTA_SUGESTAO)
        return


# ── Pico de movimento ─────────────────────────────────────────────────────────

def verificar_e_registrar_pico(db: Session):
    """
    Conta entradas na janela de tempo. Se >= LIMIAR_PICO e não há alerta
    recente, registra o pico e notifica os admins via WhatsApp.
    """
    janela_inicio = datetime.utcnow() - timedelta(minutes=JANELA_PICO_MIN)
    count = db.query(HistoricoRefeicao).filter(
        HistoricoRefeicao.data_hora >= janela_inicio
    ).count()

    if count < LIMIAR_PICO:
        return

    # Evita alertas duplicados dentro do intervalo mínimo
    ultimo = (
        db.query(PicoMovimento)
        .order_by(PicoMovimento.registrado_em.desc())
        .first()
    )
    if ultimo:
        segundos_desde_ultimo = (datetime.utcnow() - ultimo.registrado_em).total_seconds()
        if segundos_desde_ultimo < INTERVALO_ALERTA_PICO_MIN * 60:
            return

    pico = PicoMovimento(
        acessos_na_janela=count,
        registrado_em=datetime.utcnow(),
        alerta_enviado=bool(ADMIN_WHATSAPP_NUMEROS),
    )
    db.add(pico)
    db.commit()

    if not ADMIN_WHATSAPP_NUMEROS:
        logger.warning("Pico detectado (%s acessos) mas ADMIN_WHATSAPP_NUMEROS não configurado.", count)
        return

    mensagem = (
        f"⚠️ *PICO DE MOVIMENTO — RU*\n\n"
        f"*{count}* entradas nos últimos *{JANELA_PICO_MIN} min*.\n"
        "Considere orientar os alunos a aguardarem ou redistribuir o fluxo."
    )
    for numero in ADMIN_WHATSAPP_NUMEROS:
        _enviar_mensagem(_formatar_numero(numero), mensagem)

    logger.info("Alerta de pico enviado: %s acessos", count)


def obter_status_pico(db: Session) -> dict:
    """Retorna situação atual de movimento para exibição no dashboard."""
    janela_inicio = datetime.utcnow() - timedelta(minutes=JANELA_PICO_MIN)
    count = db.query(HistoricoRefeicao).filter(
        HistoricoRefeicao.data_hora >= janela_inicio
    ).count()
    return {
        "acessos": count,
        "limiar": LIMIAR_PICO,
        "janela_min": JANELA_PICO_MIN,
        "em_pico": count >= LIMIAR_PICO,
    }
