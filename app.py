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

# -----------------------------------------------------------
# CONFIGURAÇÕES
# -----------------------------------------------------------
app = Flask(__name__)
PORT = int(os.environ.get("PORT", 5000))
STORE_NAME = os.environ.get("STORE_NAME", "Loja")
SHEET_URL = os.environ.get("SHEET_URL", "")
CACHE_TTL_MINUTES = 5
EMAIL_FROM = os.environ.get("EMAIL_FROM", "loja@example.com")
EMAIL_TO = os.environ.get("EMAIL_TO", "suporte@example.com")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.example.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro-exp:generateContent?key={GEMINI_KEY}"

# -----------------------------------------------------------
# CACHE
# -----------------------------------------------------------
CACHE_FILE = "cache.json"
from typing import Dict, Any

def load_cache():
    global QNA_CACHE
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                QNA_CACHE = json.load(f)
        except Exception:
            QNA_CACHE = {}

def save_cache():
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(QNA_CACHE, f, ensure_ascii=False, indent=2)

# -----------------------------------------------------------
# UTILS
# -----------------------------------------------------------
def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def send_email(subj: str, body: str):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg["Subject"] = subj
        msg.attach(MIMEText(body, "plain", "utf-8"))
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
            s.starttls(context=context)
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        log("Email enviado")
    except Exception as e:
        log(f"Falha ao enviar email: {e}")

# -----------------------------------------------------------
# GEMINI
# -----------------------------------------------------------
def ask_gemini(prompt: str) -> str:
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 300}
    }
    try:
        r = requests.post(GEMINI_URL, json=payload, timeout=15)
        r.raise_for_status()
        cand = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return cand.strip()
    except Exception as e:
        log(f"Gemini falhou: {e}")
        return ""

# -----------------------------------------------------------
# SHEET
# -----------------------------------------------------------
def load_excel():
    # Simula 1a linha como cabeçalho: mlb, titulo, preco, disponivel, mensagem
    if SHEET_URL.endswith(".csv"):
        import pandas as pd
        df = pd.read_csv(SHEET_URL)
    elif SHEET_URL.endswith(".xlsx"):
        import pandas as pd
        df = pd.read_excel(SHEET_URL)
    else:
        return []
    df.columns = [c.lower() for c in df.columns]
    return df

# -----------------------------------------------------------
# RESPOSTAS
# -----------------------------------------------------------
def personalized_answer(question: str, mlb: str, row: Dict[str, str]) -> str:
    prompt = (
        f"Você é atendente de e-commerce. "
        f"O cliente perguntou: '{question}'\n"
        f"Dados do anúncio MLB {mlb}: titulo='{row.get('titulo','')}', "
        f"preço='{row.get('preco','')}', disponível='{row.get('disponivel','')}'. "
        f"Crie uma resposta cordial, curta e objetiva."
    )
    gem = ask_gemini(prompt)
    return gem or row.get("mensagem", "Não conseguimos localizar a resposta.")

def reply_uncle_cell(mlb: str, question: str) -> str:
    df = load_excel()
    if df.empty:
        return None
    row = df[df["mlb"] == mlb]
    if row.empty:
        return None
    data = row.iloc[0].to_dict()
    answer = personalized_answer(question, mlb, data)
    return answer

# -----------------------------------------------------------
# WEBHOOK
# -----------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def handle_notification():
    data = request.get_json(force=True)
    if not data:
        log("Payload vazio")
        return jsonify({"error": "no payload"}), 400

    topic = data.get("topic", "")
    resource = data.get("resource", "")
    if "orders" not in topic:
        return jsonify({"status": "ignored"}), 200

    # extrai número do pedido
    order_id = resource.split("/")[-1]
    log(f"Pedido {order_id}")

    # obtém MLB e pergunta da API do Mercado Livre
    try:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        r = requests.get(f"https://api.mercadolibre.com{resource}", headers=headers)
        r.raise_for_status()
        order = r.json()
    except Exception as e:
        log(f"Erro ao buscar pedido: {e}")
        return jsonify({"error": "ml-api"}), 502

    # encontra MLB e mensagem do comprador
    mlb = None
    question = ""
    for item in order.get("order_items", []):
        mlb = item.get("item", {}).get("id")
        break
    for msg in order.get("messages", []):
        if msg.get("from", {}).get("role") == "buyer":
            question = msg.get("text", "")
            break
    if not mlb or not question:
        return jsonify({"status": "no question/mlb"}), 200

    # 1) consulta cache
    cache_key = f"{mlb}:{question}"
    now = datetime.utcnow()
    cached = QNA_CACHE.get(cache_key)
    if cached:
        if now.timestamp() - cached["ts"] < CACHE_TTL_MINUTES * 60:
            log("Respondeu via cache")
            return jsonify({"answer": cached["answer"], "source": "cache"}), 200
        else:
            del QNA_CACHE[cache_key]

    # 2) procura no excel
    answer = reply_uncle_cell(mlb, question)
    if not answer:
        # 3) email
        body = f"MLB {mlb} não localizado.\nPergunta: {question}\nPedido: {order_id}"
        send_email("Resposta não encontrada", body)
        return jsonify({"status": "not found, email sent"}), 200

    # guarda cache
    QNA_CACHE[cache_key] = {"answer": answer, "ts": now.timestamp()}
    save_cache()
    log("Respondeu via Excel + Gemini")
    return jsonify({"answer": answer, "source": "sheet+gemini"}), 200

# -----------------------------------------------------------
# HEALTH
# -----------------------------------------------------------
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "running",
        "version": "v16.0",
        "store": STORE_NAME
    }), 200

# -----------------------------------------------------------
# STARTUP
# -----------------------------------------------------------
if __name__ == "__main__":
    load_cache()
    app.run(host="0.0.0.0", port=PORT)
