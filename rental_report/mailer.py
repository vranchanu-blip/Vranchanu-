import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def send(cfg: dict, subject: str, body: str, attachments: list[Path]) -> None:
    m = cfg["email"]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = m.get("from") or m["user"]
    msg["To"] = ", ".join(m["to"]) if isinstance(m["to"], list) else m["to"]
    if m.get("cc"):
        msg["Cc"] = ", ".join(m["cc"]) if isinstance(m["cc"], list) else m["cc"]
    msg.set_content(body)
    for p in attachments:
        ctype = "application/xml" if p.suffix == ".xml" else \
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        main, sub = ctype.split("/")
        msg.add_attachment(p.read_bytes(), maintype=main, subtype=sub, filename=p.name)
    password = os.environ.get(m.get("password_env", "REPORT_SMTP_PASSWORD"), m.get("password", ""))
    with smtplib.SMTP(m.get("host", "smtp.gmail.com"), int(m.get("port", 587)), timeout=60) as s:
        s.starttls()
        s.login(m["user"], password)
        s.send_message(msg)
