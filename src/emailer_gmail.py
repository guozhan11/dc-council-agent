import smtplib
from email.message import EmailMessage
from typing import Optional


def send_email_gmail_smtp(
    smtp_username: str,
    smtp_app_password: str,
    from_email: str,
    from_name: str,
    to_email: str,
    subject: str,
    html_content: str,
    text_content: Optional[str] = None,
) -> None:
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject

    if text_content:
        msg.set_content(text_content)
    else:
        msg.set_content("Your email client does not support HTML.")

    msg.add_alternative(html_content, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(smtp_username, smtp_app_password)
        smtp.send_message(msg)
