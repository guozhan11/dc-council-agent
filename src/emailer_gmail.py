import smtplib
from email.message import EmailMessage
from typing import Optional


def send_email_gmail_smtp(
    smtp_username: str,
    smtp_app_password: str,
    from_email: str,
    to_email: str,
    subject: str,
    html_content: str,
    text_content: Optional[str] = None,
    from_name: Optional[str] = None,
) -> None:
    msg = EmailMessage()
    msg["To"] = to_email
    msg["Subject"] = subject

    # "From" header: include friendly name if provided
    if from_name:
        msg["From"] = f"{from_name} <{from_email}>"
    else:
        msg["From"] = from_email

    # Body
    if text_content:
        msg.set_content(text_content)
        msg.add_alternative(html_content, subtype="html")
    else:
        # If no plain text provided, still include a basic text fallback
        msg.set_content("This email contains HTML content. Please view it in an HTML-capable client.")
        msg.add_alternative(html_content, subtype="html")

    # Send
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(smtp_username, smtp_app_password)
        smtp.send_message(msg)