from flask_mail import Message
from config import Config
from extensions import mail


def _html_wrapper(title: str, content: str) -> str:
    return f"""
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#0f0a1e;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f0a1e;padding:40px 20px;">
    <tr>
      <td align="center">
        <table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;">

          <!-- Header -->
          <tr>
            <td align="center" style="padding-bottom:28px;">
              <table cellpadding="0" cellspacing="0">
                <tr>
                  <td style="background:#ffffff;border-radius:14px;padding:10px;width:48px;height:48px;text-align:center;vertical-align:middle;box-shadow:0 0 0 2px rgba(124,58,237,0.4),0 0 20px rgba(124,58,237,0.3);">
                    <span style="font-size:26px;font-weight:900;color:#7c3aed;font-family:Georgia,serif;">N</span>
                  </td>
                  <td style="padding-left:12px;vertical-align:middle;">
                    <div style="font-size:22px;font-weight:700;color:#ffffff;font-family:Georgia,serif;letter-spacing:-0.5px;">Najahi</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.4);letter-spacing:0.5px;margin-top:2px;">Plateforme scolaire marocaine</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Card -->
          <tr>
            <td style="background:rgba(255,255,255,0.055);border:1px solid rgba(255,255,255,0.09);border-radius:24px;padding:40px 36px;backdrop-filter:blur(20px);">
              {content}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td align="center" style="padding-top:24px;">
              <p style="color:rgba(255,255,255,0.25);font-size:12px;margin:0;">
                © 2026 Najahi · Plateforme scolaire marocaine<br/>
                Si tu n'es pas à l'origine de cette demande, ignore cet email.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def send_verification_email(to_email: str, code: str):
    content = f"""
      <div style="text-align:center;margin-bottom:28px;">
        <div style="width:72px;height:72px;border-radius:50%;background:rgba(124,58,237,0.15);border:2px solid rgba(124,58,237,0.4);display:inline-flex;align-items:center;justify-content:center;margin-bottom:18px;">
          <span style="font-size:32px;">✉️</span>
        </div>
        <h1 style="color:#ffffff;font-size:24px;font-weight:700;margin:0 0 8px;font-family:Georgia,serif;">Vérifie ton email</h1>
        <p style="color:rgba(255,255,255,0.5);font-size:14px;margin:0;line-height:1.6;">
          Bienvenue sur Najahi ! Entre ce code pour activer ton compte.
        </p>
      </div>

      <div style="background:rgba(124,58,237,0.12);border:1.5px solid rgba(124,58,237,0.3);border-radius:16px;padding:24px;text-align:center;margin-bottom:24px;">
        <p style="color:rgba(255,255,255,0.5);font-size:12px;margin:0 0 10px;letter-spacing:1px;text-transform:uppercase;">Ton code de vérification</p>
        <div style="font-size:42px;font-weight:700;color:#a78bfa;letter-spacing:12px;font-family:'Courier New',monospace;">{code}</div>
        <p style="color:rgba(255,255,255,0.3);font-size:11px;margin:10px 0 0;">Expire dans 15 minutes</p>
      </div>

      <p style="color:rgba(255,255,255,0.4);font-size:13px;text-align:center;line-height:1.6;margin:0;">
        Ce code est valable une seule fois.<br/>Ne le partage avec personne.
      </p>
    """
    msg = Message(
        subject="Najahi — Vérifie ton adresse email",
        recipients=[to_email],
        html=_html_wrapper("Vérification email", content),
        body=f"Ton code de vérification Najahi est : {code}\nIl expire dans 15 minutes."
    )
    mail.send(msg)


def send_reset_password_email(to_email: str, reset_token: str):
    reset_link = f"{Config.FRONTEND_URL}/reset-password?token={reset_token}"
    content = f"""
      <div style="text-align:center;margin-bottom:28px;">
        <div style="width:72px;height:72px;border-radius:50%;background:rgba(124,58,237,0.15);border:2px solid rgba(124,58,237,0.4);display:inline-flex;align-items:center;justify-content:center;margin-bottom:18px;">
          <span style="font-size:32px;">🔑</span>
        </div>
        <h1 style="color:#ffffff;font-size:24px;font-weight:700;margin:0 0 8px;font-family:Georgia,serif;">Réinitialise ton mot de passe</h1>
        <p style="color:rgba(255,255,255,0.5);font-size:14px;margin:0;line-height:1.6;">
          Tu as demandé à réinitialiser ton mot de passe Najahi.<br/>Clique sur le bouton ci-dessous.
        </p>
      </div>

      <div style="text-align:center;margin-bottom:28px;">
        <a href="{reset_link}"
          style="display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#7c3aed,#a78bfa);color:#ffffff;text-decoration:none;border-radius:12px;font-size:15px;font-weight:600;font-family:'Segoe UI',Arial,sans-serif;box-shadow:0 4px 20px rgba(124,58,237,0.4);">
          Réinitialiser mon mot de passe →
        </a>
      </div>

      <div style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:16px;margin-bottom:20px;">
        <p style="color:rgba(255,255,255,0.4);font-size:12px;margin:0 0 8px;">Ou copie ce lien dans ton navigateur :</p>
        <p style="color:#a78bfa;font-size:11px;margin:0;word-break:break-all;">{reset_link}</p>
      </div>

      <div style="display:flex;align-items:center;gap:8px;background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.2);border-radius:10px;padding:12px 16px;">
        <span style="font-size:16px;">⚠️</span>
        <p style="color:rgba(245,158,11,0.9);font-size:12px;margin:0;line-height:1.5;">
          Ce lien expire dans <strong>30 minutes</strong>. Si tu n'as pas fait cette demande, ignore cet email — ton compte est en sécurité.
        </p>
      </div>
    """
    msg = Message(
        subject="Najahi — Réinitialisation de ton mot de passe",
        recipients=[to_email],
        html=_html_wrapper("Réinitialisation mot de passe", content),
        body=f"Réinitialise ton mot de passe Najahi ici : {reset_link}\nCe lien expire dans 30 minutes."
    )
    mail.send(msg)