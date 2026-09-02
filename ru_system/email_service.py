"""
Serviço de envio de emails via API HTTP da Brevo.
Se BREVO_API_KEY não estiver configurada, imprime o link no terminal (modo desenvolvimento).

Usa a API HTTP (não SMTP): conexões SMTP diretas costumam ser bloqueadas ou mal
roteadas em PaaS gratuitos (ex.: Render), travando o worker por minutos até dar
timeout. A chamada HTTP abaixo usa timeout curto e nunca bloqueia o site.
"""
import logging

import requests

from config import BREVO_API_KEY, EMAIL_FROM, EMAIL_FROM_NOME, BASE_URL

logger = logging.getLogger(__name__)

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
_TIMEOUT_SEGUNDOS = 8


def _enviar_via_resend(destinatarios: list[str], assunto: str, html: str) -> bool:
    try:
        resp = requests.post(
            BREVO_URL,
            headers={"api-key": BREVO_API_KEY, "accept": "application/json", "content-type": "application/json"},
            json={
                "sender": {"name": EMAIL_FROM_NOME, "email": EMAIL_FROM},
                "to": [{"email": d} for d in destinatarios],
                "subject": assunto,
                "htmlContent": html,
            },
            timeout=_TIMEOUT_SEGUNDOS,
        )
        if resp.status_code >= 400:
            logger.error("Brevo recusou o envio (%s): %s", resp.status_code, resp.text)
            return False
        return True
    except requests.RequestException as exc:
        logger.error("Erro de rede ao chamar a API da Brevo: %s", exc)
        return False


def _template_recuperacao(link: str, nome: str = "") -> str:
    saudacao = f"Olá{', ' + nome.split()[0] if nome else ''}!"
    return f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head><meta charset="UTF-8"></head>
    <body style="font-family: Arial, sans-serif; background: #1F0900; padding: 32px 16px;">
        <div style="max-width: 520px; margin: 0 auto;">

            <div style="text-align: center; margin-bottom: 28px;">
                <p style="font-size: 28px; font-weight: 900; color: #ffffff; letter-spacing: 6px; margin: 0;">MEU RU</p>
                <p style="color: #F4792088; font-size: 13px; margin: 4px 0 0;">Restaurante Universitário · UFCAT</p>
            </div>

            <div style="background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 8px 32px rgba(0,0,0,0.4);">
                <div style="height: 4px; background: linear-gradient(90deg, #F47920, #E03D3D);"></div>
                <div style="padding: 36px 32px;">
                    <h2 style="color: #111; font-size: 20px; margin: 0 0 8px;">Recuperação de Senha</h2>
                    <p style="color: #555; font-size: 14px; margin: 0 0 24px;">{saudacao}</p>
                    <p style="color: #555; font-size: 14px; line-height: 1.6; margin: 0 0 32px;">
                        Recebemos uma solicitação para redefinir a senha da sua conta.<br>
                        Clique no botão abaixo para criar uma nova senha:
                    </p>
                    <div style="text-align: center; margin-bottom: 32px;">
                        <a href="{link}"
                           style="background: linear-gradient(135deg, #F47920, #E03D3D); color: #fff;
                                  padding: 14px 32px; border-radius: 10px; text-decoration: none;
                                  font-weight: bold; font-size: 15px; display: inline-block;">
                            Redefinir Senha
                        </a>
                    </div>
                    <p style="color: #999; font-size: 13px; margin: 0 0 16px;">
                        Este link é válido por <strong>1 hora</strong>. Se você não solicitou a redefinição,
                        ignore este e-mail — sua senha permanece a mesma.
                    </p>
                    <p style="color: #bbb; font-size: 12px; margin: 0; word-break: break-all;">
                        Se o botão não funcionar, copie e cole este link no navegador:<br>
                        <a href="{link}" style="color: #F47920;">{link}</a>
                    </p>
                </div>
            </div>

            <p style="text-align: center; color: #F4792044; font-size: 11px; margin-top: 24px;">
                Sistema de Créditos · Restaurante Universitário UFCAT
            </p>
        </div>
    </body>
    </html>
    """


def _template_boas_vindas(nome: str, matricula: str) -> str:
    primeiro_nome = nome.split()[0] if nome else "Aluno"
    return f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head><meta charset="UTF-8"></head>
    <body style="font-family: Arial, sans-serif; background: #1A1A1A; padding: 32px 16px;">
        <div style="max-width: 520px; margin: 0 auto;">

            <div style="text-align: center; margin-bottom: 28px;">
                <p style="font-size: 28px; font-weight: 900; color: #ffffff; letter-spacing: 6px; margin: 0;">MEU RU</p>
                <p style="color: #F4792088; font-size: 13px; margin: 4px 0 0;">Restaurante Universitário · UFCAT</p>
            </div>

            <div style="background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 8px 32px rgba(0,0,0,0.4);">
                <div style="height: 4px; background: linear-gradient(90deg, #F47920, #E03D3D);"></div>
                <div style="padding: 36px 32px;">
                    <h2 style="color: #111; font-size: 20px; margin: 0 0 16px;">Cadastro realizado com sucesso! 🎉</h2>
                    <p style="color: #555; font-size: 14px; line-height: 1.6; margin: 0 0 20px;">
                        Olá, <strong>{primeiro_nome}</strong>! Sua conta no sistema de créditos do RU foi criada.
                    </p>

                    <div style="background: #FFF7F0; border: 1px solid #F4792033; border-radius: 10px; padding: 16px 20px; margin-bottom: 24px;">
                        <p style="margin: 0 0 6px; font-size: 13px; color: #999; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;">Seus dados de acesso</p>
                        <p style="margin: 0 0 4px; font-size: 14px; color: #333;"><strong>Matrícula:</strong> {matricula}</p>
                    </div>

                    <p style="color: #555; font-size: 14px; line-height: 1.6; margin: 0 0 28px;">
                        Agora você pode acompanhar seu saldo de créditos, histórico de refeições e muito mais pelo portal.
                    </p>

                    <div style="text-align: center; margin-bottom: 28px;">
                        <a href="{BASE_URL}/login"
                           style="background: linear-gradient(135deg, #F47920, #E03D3D); color: #fff;
                                  padding: 14px 32px; border-radius: 10px; text-decoration: none;
                                  font-weight: bold; font-size: 15px; display: inline-block;">
                            Acessar o Portal
                        </a>
                    </div>

                    <p style="color: #bbb; font-size: 12px; margin: 0; text-align: center;">
                        Se você não realizou este cadastro, entre em contato com a administração do RU.
                    </p>
                </div>
            </div>

            <p style="text-align: center; color: #F4792044; font-size: 11px; margin-top: 24px;">
                Sistema de Créditos · Restaurante Universitário UFCAT
            </p>
        </div>
    </body>
    </html>
    """


def _template_avaliacao(nome: str, tipo_refeicao: str, token: str) -> str:
    primeiro_nome = nome.split()[0] if nome else "Aluno"
    estrelas = "".join(
        f"""<a href="{BASE_URL}/avaliacao-email/{token}?nota={n}"
              style="display: inline-block; width: 44px; height: 44px; line-height: 44px;
                     margin: 0 4px; border-radius: 50%; background: #FFF7F0;
                     color: #F47920; font-size: 20px; text-decoration: none; font-weight: bold;">
              {n}★</a>"""
        for n in range(1, 6)
    )
    return f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head><meta charset="UTF-8"></head>
    <body style="font-family: Arial, sans-serif; background: #1A1A1A; padding: 32px 16px;">
        <div style="max-width: 520px; margin: 0 auto;">

            <div style="text-align: center; margin-bottom: 28px;">
                <p style="font-size: 28px; font-weight: 900; color: #ffffff; letter-spacing: 6px; margin: 0;">MEU RU</p>
                <p style="color: #F4792088; font-size: 13px; margin: 4px 0 0;">Restaurante Universitário · UFCAT</p>
            </div>

            <div style="background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 8px 32px rgba(0,0,0,0.4);">
                <div style="height: 4px; background: linear-gradient(90deg, #F47920, #E03D3D);"></div>
                <div style="padding: 36px 32px; text-align: center;">
                    <h2 style="color: #111; font-size: 20px; margin: 0 0 8px;">Como foi o {tipo_refeicao} de hoje?</h2>
                    <p style="color: #555; font-size: 14px; margin: 0 0 28px;">
                        Olá, {primeiro_nome}! Sua opinião ajuda a melhorar o RU. Clique numa nota de 1 a 5:
                    </p>
                    <div style="margin-bottom: 8px;">{estrelas}</div>
                    <p style="color: #bbb; font-size: 12px; margin: 24px 0 0;">
                        Leva 1 clique. Não precisa fazer login.
                    </p>
                </div>
            </div>

            <p style="text-align: center; color: #F4792044; font-size: 11px; margin-top: 24px;">
                Sistema de Créditos · Restaurante Universitário UFCAT
            </p>
        </div>
    </body>
    </html>
    """


def _template_alerta(mensagem: str) -> str:
    linhas = mensagem.replace("\n", "<br>")
    return f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head><meta charset="UTF-8"></head>
    <body style="font-family: Arial, sans-serif; background: #1A1A1A; padding: 32px 16px;">
        <div style="max-width: 520px; margin: 0 auto;">
            <div style="text-align: center; margin-bottom: 28px;">
                <p style="font-size: 28px; font-weight: 900; color: #ffffff; letter-spacing: 6px; margin: 0;">MEU RU</p>
                <p style="color: #F4792088; font-size: 13px; margin: 4px 0 0;">Restaurante Universitário · UFCAT</p>
            </div>
            <div style="background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 8px 32px rgba(0,0,0,0.4);">
                <div style="height: 4px; background: linear-gradient(90deg, #F47920, #E03D3D);"></div>
                <div style="padding: 36px 32px;">
                    <h2 style="color: #111; font-size: 20px; margin: 0 0 20px;">Aviso do Restaurante Universitário</h2>
                    <p style="color: #333; font-size: 15px; line-height: 1.7; margin: 0 0 28px;">{linhas}</p>
                    <div style="text-align: center;">
                        <a href="{BASE_URL}/login"
                           style="background: linear-gradient(135deg, #F47920, #E03D3D); color: #fff;
                                  padding: 12px 28px; border-radius: 10px; text-decoration: none;
                                  font-weight: bold; font-size: 14px; display: inline-block;">
                            Acessar o Portal
                        </a>
                    </div>
                </div>
            </div>
            <p style="text-align: center; color: #F4792044; font-size: 11px; margin-top: 24px;">
                Sistema de Créditos · Restaurante Universitário UFCAT
            </p>
        </div>
    </body>
    </html>
    """


def enviar_email_alerta(email: str, assunto: str, mensagem: str) -> bool:
    return enviar_emails_alerta_lote([email], assunto, mensagem) == 1


def enviar_emails_alerta_lote(emails: list[str], assunto: str, mensagem: str) -> int:
    """Envia o mesmo alerta para vários destinatários."""
    if not emails:
        return 0
    if not BREVO_API_KEY:
        logger.info("[DEV] Email alerta simulado para %d destinatários: %s", len(emails), mensagem)
        return len(emails)
    html = _template_alerta(mensagem)
    return sum(1 for email in emails if _enviar_via_resend([email], assunto, html))


def enviar_email_boas_vindas(email: str, nome: str, matricula: str) -> bool:
    """
    Envia email de confirmação de cadastro ao novo aluno.
    Em modo dev (sem BREVO_API_KEY), imprime no terminal.
    """
    if not BREVO_API_KEY:
        print("\n" + "="*60)
        print("📧 [MODO DEV] Email de boas-vindas")
        print(f"   Para: {email}  |  Nome: {nome}  |  Matrícula: {matricula}")
        print("="*60 + "\n")
        logger.info("[DEV] Email de boas-vindas simulado para %s", email)
        return True

    sucesso = _enviar_via_resend(
        [email], "Bem-vindo ao RU — Cadastro confirmado!", _template_boas_vindas(nome, matricula)
    )
    if sucesso:
        logger.info("Email de boas-vindas enviado para %s", email)
    return sucesso


def enviar_email_recuperacao(email: str, token: str, nome: str = "") -> bool:
    """
    Envia email de recuperação de senha.
    Retorna True se enviado com sucesso.
    Em modo dev (sem BREVO_API_KEY), imprime o link no terminal.
    """
    link = f"{BASE_URL}/recuperar-senha/{token}"

    if not BREVO_API_KEY:
        print("\n" + "="*60)
        print("📧 [MODO DEV] Email de recuperação de senha")
        print(f"   Para: {email}")
        print(f"   Link: {link}")
        print("="*60 + "\n")
        logger.info(f"[DEV] Link de recuperação para {email}: {link}")
        return True

    sucesso = _enviar_via_resend([email], "Recuperação de Senha — RU", _template_recuperacao(link, nome))
    if sucesso:
        logger.info(f"Email de recuperação enviado para {email}")
    return sucesso


def enviar_email_avaliacao(email: str, nome: str, tipo_refeicao: str, token: str) -> bool:
    """
    Envia o lembrete de avaliação (5 estrelas clicáveis, sem precisar logar).
    Em modo dev (sem BREVO_API_KEY), imprime o link no terminal.
    """
    if not BREVO_API_KEY:
        print("\n" + "="*60)
        print("📧 [MODO DEV] Email de avaliação de refeição")
        print(f"   Para: {email}")
        print(f"   Link (nota 5 de exemplo): {BASE_URL}/avaliacao-email/{token}?nota=5")
        print("="*60 + "\n")
        logger.info("[DEV] Link de avaliação para %s", email)
        return True

    sucesso = _enviar_via_resend(
        [email], f"Como foi o {tipo_refeicao} de hoje? — RU", _template_avaliacao(nome, tipo_refeicao, token)
    )
    if sucesso:
        logger.info("Email de avaliação enviado para %s", email)
    return sucesso
