# =============================================================================
# SERVICIO DE CORREOS Y NOTIFICACIONES - CONFYTICKETS
# =============================================================================

import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "seguridad@confytickets.com"))
SMTP_TLS = os.getenv("SMTP_TLS", "true").lower() in ("true", "1", "yes")

def render_reset_code_html(user_name: str, code: str, expires_minutes: int = 15) -> str:
    """Genera plantilla HTML minimalista y elegante para envío de código OTP."""
    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Código de Recuperación - ConfyTickets</title>
      <style>
        body {{
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
          background-color: #F9F7F5;
          margin: 0;
          padding: 24px;
          color: #3E2B1E;
        }}
        .container {{
          max-width: 480px;
          margin: 0 auto;
          background: #ffffff;
          border-radius: 16px;
          border: 1px solid #E3DAD2;
          padding: 32px 24px;
          box-shadow: 0 4px 12px rgba(62, 43, 30, 0.05);
        }}
        .header {{
          text-align: center;
          margin-bottom: 24px;
        }}
        .badge {{
          display: inline-block;
          background-color: #5D4432;
          color: #E9E3DD;
          padding: 6px 14px;
          border-radius: 20px;
          font-size: 12px;
          font-weight: 700;
          letter-spacing: 0.5px;
          text-transform: uppercase;
        }}
        h2 {{
          color: #3E2B1E;
          font-size: 20px;
          margin: 16px 0 8px 0;
          font-weight: 800;
        }}
        p {{
          font-size: 14px;
          line-height: 1.5;
          color: #786558;
          margin: 8px 0;
        }}
        .code-box {{
          background-color: #F9F7F5;
          border: 2px dashed #D0C6BE;
          border-radius: 12px;
          padding: 20px;
          text-align: center;
          margin: 24px 0;
        }}
        .code {{
          font-family: 'Courier New', Courier, monospace;
          font-size: 36px;
          font-weight: 900;
          color: #8C4B13;
          letter-spacing: 8px;
          margin: 0;
        }}
        .code-sub {{
          font-size: 11px;
          color: #A6978C;
          margin-top: 8px;
          font-weight: 600;
          text-transform: uppercase;
        }}
        .footer {{
          border-top: 1px solid #E3DAD2;
          padding-top: 20px;
          margin-top: 24px;
          font-size: 11px;
          color: #A6978C;
          text-align: center;
          line-height: 1.4;
        }}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <span class="badge">ConfyTickets Seguridad</span>
          <h2>Recuperación de Contraseña</h2>
          <p>Hola <strong>{user_name}</strong>, hemos recibido una solicitud para restablecer la contraseña de tu cuenta.</p>
        </div>

        <div class="code-box">
          <div class="code">{code}</div>
          <div class="code-sub">Válido por los próximos {expires_minutes} minutos</div>
        </div>

        <p>Ingresa este código de 6 dígitos en la pantalla de verificación para autorizar el cambio de tu contraseña.</p>
        
        <p style="font-size: 12px; color: #8C4B13; background: #FFF8F0; padding: 10px 14px; border-radius: 8px; border: 1px solid #F5E2D0;">
          <strong>Importante:</strong> Si tú no solicitaste este cambio, puedes ignorar este mensaje de forma segura. Tu contraseña actual no será modificada.
        </p>

        <div class="footer">
          ConfyTickets — Plataforma de Fidelización y Punto de Venta.<br>
          Este es un correo automático de seguridad, por favor no respondas a este mensaje.
        </div>
      </div>
    </body>
    </html>
    """

def send_password_reset_email(email: str, code: str, user_name: str, expires_minutes: int = 15) -> Dict[str, Any]:
    """
    Despacha el correo con el código OTP de recuperación.
    Si hay servidor SMTP configurado lo envía por red; de lo contrario lo registra en logs
    y devuelve dev_code para entornos de prueba.
    """
    subject = f"ConfyTickets: Código de Recuperación [{code}]"
    html_content = render_reset_code_html(user_name, code, expires_minutes)

    # Imprimir en logs del backend
    logger.info(f"[EMAIL_SERVICE] Código de recuperación para {email}: {code}")
    print(f"\n========================================================")
    print(f"📧 [CONFYTICKETS EMAIL] Para: {email}")
    print(f"🔑 CÓDIGO DE RECUPERACIÓN: {code}")
    print(f"⏱️  Vigencia: {expires_minutes} minutos")
    print(f"========================================================\n")

    # Si hay configuración SMTP real
    if SMTP_HOST and SMTP_USER and SMTP_PASSWORD:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = SMTP_FROM
            msg["To"] = email

            part_text = MIMEText(f"Tu código de recuperación es: {code}. Válido por {expires_minutes} minutos.", "plain", "utf-8")
            part_html = MIMEText(html_content, "html", "utf-8")
            msg.attach(part_text)
            msg.attach(part_html)

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                if SMTP_TLS:
                    server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, [email], msg.as_string())
            
            logger.info(f"[EMAIL_SERVICE] Correo SMTP enviado con éxito a {email}")
            return {"sent": True, "method": "smtp", "dev_code": None}
        except Exception as e:
            logger.error(f"[EMAIL_SERVICE] Error enviando correo SMTP: {str(e)}")
            # Fallback en caso de fallo SMTP: permitir continuar en dev
            return {"sent": False, "method": "dev_fallback", "error": str(e), "dev_code": code}

    # Modo Desarrollo / Sin SMTP configurado
    return {"sent": True, "method": "simulated", "dev_code": code}
