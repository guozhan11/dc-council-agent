import requests
from typing import Optional


def send_email_sendgrid(
    api_key: str,
    from_email: str,
    from_name: str,
    to_email: str,
    subject: str,
    html_content: str,
    text_content: Optional[str] = None,
    unsubscribe_url: Optional[str] = None,
) -> None:
    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    content = [{"type": "text/html", "value": html_content}]
    if text_content:
        content.insert(0, {"type": "text/plain", "value": text_content})

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email, "name": from_name},
        "reply_to": {"email": from_email, "name": from_name},
        "subject": subject,
        "content": content,
    }

    if unsubscribe_url:
        payload["headers"] = {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"SendGrid error {resp.status_code}: {resp.text}")