import os
import json
import time
import requests
import smtplib
import ssl
from datetime import datetime
from typing import Dict, Optional, Any, List
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import pandas as pd
from flask import Flask, request, jsonify

# ------------------------------------------------------------------
# CONFIGURAÇÕES GLOBAIS
# ------------------------------------------------------------------
APP_ID            = os.getenv(\"MERCADO_LIVRE_APP_ID\")
CLIENT_SECRET     = os.getenv(\"MERCADO_LIVRE_CLIENT_SECRET\")
GOOGLE_API_KEY    = os.getenv(\"GOOGLE_API_KEY\")
EMAIL_SENDER      = os.getenv(\"EMAIL_SENDER\")
EMAIL_RECEIVER    = os.getenv(\"EMAIL_RECEIVER\")
EMAIL_APP_PASS    = os.getenv(\"EMAIL_APP_PASSWORD\")

STORE_NAME        = \"Riomar Equipesca\"
CONFIDENCE_THRESH = 8
MEMORY_TTL_SEC    = 300
PORT              = int(os.getenv(\"PORT\", 10000))

# ------------------------------------------------------------------
# ESTRUTURAS & CACHE
# ------------------------------------------------------------------
@dataclass
class ProductContext:
    id: str
    title: str
    description: str
    attributes: Dict[str, Any]

processed_cache: Dict[str, float] = {}
greeting_cache: Dict[str, str] = {}

# ------------------------------------------------------------------
# CARREGAMENTO DE DADOS
# ------------------------------------------------------------------
def load_knowledge() -> pd.DataFrame:
    try:
        df = pd.read_excel(\"catalogo_produtos.xlsx\", dtype={\"Código do anúncio\": str})
        print(f\"[INFO] Cat Excel carregado: {len(df)} produtos.\")
        return df
    except Exception as e:
        print(f\"[ERRO] Falha ao carregar catálogo: {e}\")
        return pd.DataFrame()

def load_canned() -> List[Dict[str, Any]]:
    try:
        with open(\"canned_responses.json\", encoding=\"utf-8\") as f:
            return json.load(f)
    except Exception as e:
        print(f\"[ERRO] Falha ao carregar canned: {e}\")
        return []

knowledge_df  = load_knowledge()
canned_rules  = load_canned()

# ------------------------------------------------------------------
# UTILITÁRIOS
# ------------------------------------------------------------------
def get_greeting() -> str:
    now = datetime.utcnow()
    key = f\"{now.hour:02d}:{now.minute // 30:02d}\"
    if key in greeting_cache:
        return greeting_cache[key]
    h = (now.hour - 3) % 24
    if 5 <= h < 12:
        g = \"Olá! Bom dia. \"
    elif 12 <= h < 18:
        g = \"Olá! Boa tarde. \"
    else:
        g = \"Olá! Boa noite. \"
    greeting_cache[key] = g
    return g

def extract_json_payload(text: str) -> Optional[Dict[str, Any]]:
    try:
        start, end = text.find(\"{\"), text.rfind(\"}\") + 1
        return json.loads(text[start:end]) if start != -1 and end != 0 else None
    except Exception:
        return None

# ------------------------------------------------------------------
# RESPOSTAS PRONTAS & GENÉRICAS
# ------------------------------------------------------------------
def find_canned_answer(question: str) -> Optional[str]:
    q = question.lower()
    for rule in canned_rules:
        if any(k.lower() in q for k in rule.get(\"keywords\", [])):
            return rule.get(\"response\")
    return None

def is_generic_description_query(question: str) -> bool:
    generics = {
        \"vantagem\", \"vantagens\", \"benefício\", \"benefícios\",
        \"característica\", \"características\", \"descrição\",
        \"detalhes\", \"qualidades\", \"informações\"
    }
    return any(g in question.lower() for g in generics)

# ------------------------------------------------------------------
# E-MAIL
# ------------------------------------------------------------------
def send_email(question: str, answer: str) -> None:
    if not all([EMAIL_SENDER, EMAIL_RECEIVER, EMAIL_APP_PASS]):
        print(\"[AVISO] Credenciais de e-mail incompletas.\")
        return
    msg            = MIMEMultipart(\"alternative\")
    msg[\"Subject\"] = f\"Robô ML respondeu: {question[:30]}...\"
    msg[\"From\"]    = EMAIL_SENDER
    msg[\"To\"]      = EMAIL_RECEIVER

    html = f\"\"\"\\
    <html>
    <body>
      <p><strong>Pergunta:</strong></p>
      <p style='border-left:3px solid #ccc;padding-left:8px;'>{question}</p>
      <p><strong>Resposta:</strong></p>
      <p style='border-left:3px solid #007bff;padding-left:8px;'>{answer.replace(chr(10),'<br>')}</p>
    </body>
    </html>\"\"\"
    msg.attach(MIMEText(html, \"html\"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(\"smtp.gmail.com\", 465, context=context) as server:
            server.login(EMAIL_SENDER, EMAIL_APP_PASS)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(\"[INFO] E-mail de notificação enviado.\")
    except Exception as e:
        print(f\"[ERRO] Falha ao enviar e-mail: {e}\")

# ------------------------------------------------------------------
# INTELECTO (IA)
# ------------------------------------------------------------------
def ask_ai(question: str, context: ProductContext) -> Optional[Dict[str, Any]]:
    prompt = f\"\"\"\\
Você é um especialista em produtos da loja {STORE_NAME}.
Dados do produto:
{json.dumps(context.attributes, ensure_ascii=False)}

Descrição textual:
{context.description}

Pergunta do cliente:
\"{question}\"

Instruções:
1. Se souber a resposta exata, devolva um JSON com:
   {{\"status\":\"ANSWER_FOUND\",\"confidence_score\":9,\"answer_text\":\"sua resposta sucinta\"}}
2. Se não souber, devolva:
   {{\"status\":\"INFORMATION_NOT_FOUND\",\"confidence_score\":0}}
3. Não invente dados. Respeite limite de 400 caracteres.
\"\"\"
    url     = f\"https://generativelanguage.googleapis.com/v1/models/gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}\"
    payload = {\"contents\": [{\"parts\": [{\"text\": prompt}]}]}
    headers = {\"Content-Type\": \"application/json\"}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        txt = resp.json()[\"candidates\"][0][\"content\"][\"parts\"][0][\"text\"]
        return extract_json_payload(txt)
    except Exception as e:
        print(f\"[ERRO] IA falhou: {e}\")
        return None

# ------------------------------------------------------------------
# MERCADO LIVRE – TOKEN
# ------------------------------------------------------------------
def ml_token() -> Optional[str]:
    url  = \"https://api.mercadolibre.com/oauth/token\"
    data = {
        \"grant_type\": \"client_credentials\",
        \"client_id\": APP_ID,
        \"client_secret\": CLIENT_SECRET
    }
    hdrs = {\"Content-Type\": \"application/x-www-form-urlencoded\"}
    try:
        r = requests.post(url, headers=hdrs, data=data, timeout=10)
        r.raise_for_status()
        return r.json()[\"access_token\"]
    except Exception as e:
        print(f\"[ERRO] Falha ao obter token: {e}\")
        return None

# ------------------------------------------------------------------
# MERCADO LIVRE – QUESTÃO
# ------------------------------------------------------------------
def fetch_question(resource: str, token: str) -> Optional[Dict[str, Any]]:
    url  = f\"https://api.mercadolibre.com{resource}\"
    hdrs = {\"Authorization\": f\"Bearer {token}\"}
    try:
        r = requests.get(url, headers=hdrs, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f\"[ERRO] Falha ao buscar questão: {e}\")
        return None

# ------------------------------------------------------------------
# MERCADO LIVRE – RESPONDER
# ------------------------------------------------------------------
def reply_question(qid: str, text: str, token: str) -> bool:
    url   = \"https://api.mercadolibre.com/answers\"
    hdrs  = {\"Authorization\": f\"Bearer {token}\", \"Content-Type\": \"application/json\"}
    data  = {\"question_id\": qid, \"text\": text}
    try:
        r = requests.post(url, headers=hdrs, json=data, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f\"[ERRO] Falha ao responder: {e}\")
        return False

# ------------------------------------------------------------------
# CONTEXTUALIZAÇÃO DO PRODUTO
# ------------------------------------------------------------------
def build_context(item_id: str) -> Optional[ProductContext]:
    if knowledge_df.empty:
        return None
    row = knowledge_df[knowledge_df[\"Código do anúncio\"] == item_id]
    if row.empty:
        return None
    row = row.iloc[0].fillna(\"\")
    return ProductContext(
        id=item_id,
        title=row.get(\"Título\", \"\"),
        description=row.get(\"Descrição\", \"\"),
        attributes=row.to_dict()
    )

# ------------------------------------------------------------------
# FLUXO PRINCIPAL
# ------------------------------------------------------------------
def process_question(resource: str) -> Dict[str, Any]:
    token = ml_token()
    if not token:
        return {\"status\": \"error_token\"}

    q = fetch_question(resource, token)
    if not q or q.get(\"status\") != \"UNANSWERED\":
        return {\"status\": \"ignored\"}

    qid   = q[\"id\"]
    text  = q[\"text\"]
    item  = q[\"item_id\"]

    # cache
    now = time.time()
    if qid in processed_cache and now - processed_cache[qid] < MEMORY_TTL_SEC:
        return {\"status\": \"duplicate\"}
    processed_cache[qid] = now

    # Plano A – resposta pronta
    canned = find_canned_answer(text)
    if canned:
        if reply_question(qid, canned, token):
            send_email(text, canned)
        return {\"status\": \"canned\"}

    # Plano B – contexto do produto
    ctx = build_context(item)
    if not ctx:
        return {\"status\": \"no_context\"}

    # Plano C – descrição genérica
    if is_generic_description_query(text):
        ans = f\"{get_greeting()}Claro! Seguem os detalhes do produto:\\n\\n{ctx.description}\\n\\nAguardamos sua compra!\\nEquipe {STORE_NAME}\"
        if reply_question(qid, ans, token):
            send_email(text, ans)
        return {\"status\": \"desc_generic\"}

    # Plano D – IA
    ia = ask_ai(text, ctx)
    if ia and ia.get(\"status\") == \"ANSWER_FOUND\" and ia.get(\"confidence_score\", 0) >= CONFIDENCE_THRESH:
        ans = f\"{get_greeting()}{ia['answer_text']}\\n\\nAguardamos sua compra!\\nEquipe {STORE_NAME}\"
        if reply_question(qid, ans, token):
            send_email(text, ans)
        return {\"status\": \"ai_success\"}
    return {\"status\": \"needs_human\"}

# ------------------------------------------------------------------
# FLASK
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route(\"/notifications\", methods=[\"POST\"])
def notifications():
    data = request.get_json(silent=True) or {}
    if data.get(\"topic\") != \"questions\":
        return jsonify({\"status\": \"ignored\"}), 200
    res = process_question(data.get(\"resource\", \"\"))
    return jsonify(res), 200

@app.route(\"/\", methods=[\"GET\"])
def health():
    return jsonify({\"status\": \"running\", \"version\": \"v14.0\", \"store\": STORE_NAME}), 200

if __name__ == \"__main__\":
    app.run(host=\"0.0.0.0\", port=PORT)
