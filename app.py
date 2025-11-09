import os
import json
import time
import requests
import smtplib
import ssl
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from dataclasses import dataclass, asdict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import Flask, request, jsonify
import pandas as pd

# ------------------------------------------------------------------
# CONFIGURAÇÕES
# ------------------------------------------------------------------
APP_ID            = os.getenv("MERCADO_LIVRE_APP_ID")
CLIENT_SECRET     = os.getenv("MERCADO_LIVRE_CLIENT_SECRET")
STORE_NAME        = os.getenv("STORE_NAME", "LojaSemNome")

# E-mail
EMAIL_FROM        = os.getenv("EMAIL_FROM")
EMAIL_TO          = os.getenv("EMAIL_TO")
EMAIL_SMTP        = os.getenv("EMAIL_SMTP", "smtp.gmail.com")
EMAIL_PORT        = int(os.getenv("EMAIL_PORT", "465"))
EMAIL_USER        = os.getenv("EMAIL_USER")
EMAIL_PASS        = os.getenv("EMAIL_PASS")

PORT              = int(os.getenv("PORT", "5000"))

# ------------------------------------------------------------------
# CACHE & ESTRUTURAS
# ------------------------------------------------------------------
CACHE_FILE        = "question_cache.json"
CACHE_EXPIRY      = 60 * 60 * 6   # 6h

@dataclass
class CacheItem:
    question_id: str
    answered : bool
    timestamp: float

cache: Dict[str, CacheItem] = {}

# ------------------------------------------------------------------
# UTILS
# ------------------------------------------------------------------
def log(msg: str):
    print(f"[{datetime.utcnow().isoformat()}] {msg}")

def load_cache():
    global cache
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            cache = {k: CacheItem(**v) for k, v in data.items()}
    except Exception:
        cache = {}

def save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({k: asdict(v) for k, v in cache.items()}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"Erro ao salvar cache: {e}")

# E-mail
def send_email(subject: str, body: str):
    if not EMAIL_FROM or not EMAIL_TO:
        return
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(EMAIL_SMTP, EMAIL_PORT, context=context) as s:
            s.login(EMAIL_USER, EMAIL_PASS)
            s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        log("E-mail enviado")
    except Exception as e:
        log(f"Erro ao enviar e-mail: {e}")

# ------------------------------------------------------------------
# MERCADO LIVRE
# ------------------------------------------------------------------
TOKEN_URL      = "https://api.mercadolibre.com/oauth/token"
QUESTIONS_URL  = "https://api.mercadolibre.com/questions"

def get_token() -> Optional[str]:
    payload = {
        "grant_type": "client_credentials",
        "client_id": APP_ID,
        "client_secret": CLIENT_SECRET
    }
    try:
        r = requests.post(TOKEN_URL, data=payload, timeout=10)
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        log(f"Erro ao obter token: {e}")
        return None

def get_question(question_id: str) -> Optional[Dict]:
    token = get_token()
    if not token:
        return None
    url = f"{QUESTIONS_URL}/{questionid}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"Erro ao obter pergunta {question_id}: {e}")
        return None

def answer_question_ml(questionid: str, text: str) -> bool:
    token = get_token()
    if not token:
        return False
    url = f"{QUESTIONS_URL}/{questionid}/answer"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"question_id": questionid, "text": text}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log(f"Erro ao responder pergunta {questionid}: {e}")
        return False

# ------------------------------------------------------------------
# LÓGICA DE RESPOSTA
# ------------------------------------------------------------------
def build_answer(question_text: str, title: str) -> str:
    """
    Gera uma resposta genérica, mas contextualizada.
    Pode ser trocada por IA externa.
    """
    q = question_text.lower()
    if any(k in q for k in ("tamanho", "medida", "numeração")):
        return f"Olá! Consulte a tabela de tamanhos na descrição do anúncio. Qualquer dúvida estamos à disposição!"
    if "cor" in q or "disponível" in q:
        return f"Todas as cores/disponibilidade constam no título e nas imagens do anúncio. Obrigado pelo contato!"
    if any(k in q for k in ("frete", "envio", "entrega")):
        return f"Olá! O frete e prazo aparecem automaticamente ao inserir seu CEP. Esperamos sua compra!"
    return f"Olá! Obrigado pelo interesse no produto: {title}. Estamos à disposição!"

# ------------------------------------------------------------------
# PROCESSADOR DE NOTIFICAÇÃO
# ------------------------------------------------------------------
def handle_notification(resource: str):
    """
    resource = https://api.mercadolibre.com/questions/123456
    """
    if not resource:
        return {"status": "ignored", "reason": "resource empty"}

    questionid = resource.split("/")[-1]
    if not questionid.isdigit():
        return {"status": "ignored", "reason": "invalid questionid"}

    cachekey = f"{STORE_NAME}_{questionid}"
    item = cache.get(cachekey)
    if item and item.answered:
        return {"status": "ignored", "reason": "already answered"}
    # expira cache
    if item and (time.time() - item.timestamp) > CACHE_EXPIRY:
        del cache[cachekey]

    q_data = get_question(questionid)
    if not q_data:
        return {"status": "error", "reason": "fetch question failed"}

    if q_data.get("status") != "UNANSWERED":
        return {"status": "ignored", "reason": "question answered or closed"}

    answer_text = build_answer(
        q_data.get("text", ""),
        q_data.get("item", {}).get("title", "")
    )
    ok = answer_question_ml(questionid, answer_text)

    if ok:
        cache[cachekey] = CacheItem(questionid, True, time.time())
        save_cache()
        send_email(
            subject=f"[{STORE_NAME}] Pergunta respondida",
            body=f"Pergunta: {q_data.get('text', '')}\nResposta: {answer_text}"
        )
        return {"status": "answered", "question_id": questionid}
    return {"status": "error", "reason": "answer failed"}

# ------------------------------------------------------------------
# FLASK
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route("/notifications", methods=["POST"])
def notifications():
    data = request.get_json(silent=True) or {}
    if data.get("topic") != "questions":
        return jsonify({"status": "ignored"}), 200
    res = handle_notification(data.get("resource", ""))
    return jsonify(res), 200

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "running",
        "version": "v15.0",
        "store": STORE_NAME
    }), 200

# ------------------------------------------------------------------
# STARTUP
# ------------------------------------------------------------------
if __name__ == "__main__":
    load_cache()
    app.run(host="0.0.0.0", port=PORT)
