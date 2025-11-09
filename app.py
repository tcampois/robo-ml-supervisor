from flask import Flask, request, jsonify
import os
import requests
import json
import time
import pandas as pd
from datetime import datetime
import smtplib, ssl # Novas importações para e-mail
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- CONFIGURAÇÃO ---
APP_ID = os.environ.get('MERCADO_LIVRE_APP_ID')
CLIENT_SECRET = os.environ.get('MERCADO_LIVRE_CLIENT_SECRET')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
CONFIDENCE_THRESHOLD = 8
YOUR_STORE_NAME = "Riomar Equipesca"

# Novas configurações de e-mail
EMAIL_SENDER = os.environ.get('EMAIL_SENDER')
EMAIL_RECEIVER = os.environ.get('EMAIL_RECEIVER')
EMAIL_APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')

# --- CARREGAMENTO DOS BANCOS DE DADOS ---
KNOWLEDGE_DF = None; CANNED_RESPONSES = []
try: KNOWLEDGE_DF = pd.read_excel('catalogo_produtos.xlsx', dtype={'ITEM_ID': str}); print(f">>> Banco de Dados Excel v12.0 carregado. {len(KNOWLEDGE_DF)} produtos.")
except Exception as e: print(f"### ERRO no Excel: {e} ###")
try:
    with open('canned_responses.json', 'r', encoding='utf-8') as f: CANNED_RESPONSES = json.load(f); print(f">>> Livro de Regras v12.0 carregado. {len(CANNED_RESPONSES)} regras.")
except Exception as e: print(f"### ERRO no JSON de regras: {e} ###")

PROCESSED_QUESTIONS = {}; MEMORY_DURATION_SECONDS = 300
app = Flask(__name__)

# ==============================================================================
# ==============================================================================
def send_notification_email(question_text, answer_text):
    if not all([EMAIL_SENDER, EMAIL_RECEIVER, EMAIL_APP_PASSWORD]):
        print(">>> AVISO: Credenciais de e-mail não configuradas. Notificação não enviada.")
        return

    message = MIMEMultipart("alternative")
    message["Subject"] = f"Robô ML Respondeu: \"{question_text[:30]}...\""
    message["From"] = EMAIL_SENDER
    message["To"] = EMAIL_RECEIVER

    # Corpo do e-mail em HTML para melhor formatação
    html = f"""
    <html>
      <body>
        <p><strong>Uma resposta automática foi enviada no Mercado Livre.</strong></p>
        <hr>
        <p><strong>Pergunta do Cliente:</strong></p>
        <p style="padding: 10px; border-left: 3px solid #ccc;">{question_text}</p>
        <p><strong>Resposta do Robô:</strong></p>
        <p style="padding: 10px; border-left: 3px solid #007bff;">{answer_text.replace(os.linesep, '<br>')}</p>
      </body>
    </html>
    """
    message.attach(MIMEText(html, "html"))

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, message.as_string())
        print(">>> NOTIFICAÇÃO POR E-MAIL ENVIADA COM SUCESSO!")
    except Exception as e:
        print(f"### FALHA AO ENVIAR E-MAIL DE NOTIFICAÇÃO: {e} ###")


# --- DEMAIS FUNÇÕES DE LÓGICA (v11.1 - inalteradas) ---
def check_for_canned_response(question_text): #...
    normalized_question = question_text.lower()
    for rule in CANNED_RESPONSES:
        for keyword in rule['keywords']:
            if keyword.lower() in normalized_question:
                print(f">>> REGRA RÁPIDA ACIONADA! Keyword: '{keyword}', Regra: '{rule['name']}'.")
                return rule['response']
    return None

def get_time_based_greeting(): # ...
    current_hour = datetime.utcnow().hour - 3;
    if current_hour < 0: current_hour += 24
    if 5 <= current_hour < 12: return "Olá! Bom dia. "
    elif 12 <= current_hour < 18: return "Olá! Boa tarde. "
    else: return "Olá! Boa noite. "

def extract_json_from_ia_response(text): # ...
    try: json_start = text.find('{'); json_end = text.rfind('}') + 1; return json.loads(text[json_start:json_end]) if json_start != -1 and json_end != -1 else None
    except Exception: return None

def get_reply_logic(question_text, product_data): # ...
    print(">>> CÉREBRO DE IA v12.0 (Com Discernimento) ACIONADO...")
    # ... (código interno da função é o mesmo da v11.1)
    description_text = product_data.pop('DESCRIPTION', 'Nenhuma descrição fornecida.'); structured_data_text = "\n".join([f"{key.replace('_', ' ').title()}: {value}" for key, value in product_data.items()]); url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GOOGLE_API_KEY}"; headers = {'Content-Type': 'application/json'};
    prompt = f"""
    # MISSÃO
    Você é um Perito Verificador. Sua missão é responder à pergunta e AVALIAR se a resposta foi encontrada. Sua resposta DEVE SER um objeto JSON válido.
    # FONTES DE DADOS
    ---
    # DADOS ESTRUTURADOS: {structured_data_text}
    # DESCRIÇÃO COMPLETA: {description_text}
    ---
    # PERGUNTA DO CLIENTE
    "{question_text}"
    # INSTRUÇÕES DE EXECUÇÃO
    1. Leia a pergunta e as fontes de dados CUIDADOSAMENTE.
    2. Se você encontrar a resposta EXATA nos dados, formule a resposta e defina o campo "status" como "ANSWER_FOUND".
    3. Se a informação NÃO ESTIVER nos dados, defina o "status" como "INFORMATION_NOT_FOUND" e o "answer_text" como uma breve explicação interna.
    4. Avalie sua confiança (de 0 a 10) no "confidence_score".
    5. Formate sua saída EXCLUSIVAMENTE como um objeto JSON com as chaves "status", "confidence_score" e "answer_text".
    # SAÍDA JSON OBRIGATÓRIA:
    """; payload = {"contents": [{"parts": [{"text": prompt}]}]};
    try: response = requests.post(url, headers=headers, data=json.dumps(payload)); response.raise_for_status(); ia_response_string = response.json()['candidates'][0]['content']['parts'][0]['text']; print(f">>> IA (Com Discernimento) RETORNOU STRING: {ia_response_string}"); return extract_json_from_ia_response(ia_response_string)
    except Exception as e: print(f"### ERRO NA CHAMADA À IA: {e} ###"); return None

def get_product_context_from_dataframe(item_id, dataframe): #...
    if dataframe is None: return None; product_row = dataframe[dataframe['ITEM_ID'] == item_id]; return product_row.iloc[0].dropna().to_dict() if not product_row.empty else None

def get_access_token(): #...
    url = "https://api.mercadolibre.com/oauth/token"; payload = {'grant_type': 'client_credentials', 'client_id': APP_ID, 'client_secret': CLIENT_SECRET}; response = requests.post(url, headers={'accept': 'application/json', 'content-type': 'application/x-www-form-urlencoded'}, data=payload); return response.json().get('access_token') if response.status_code == 200 else None
def get_question_details(resource_id, token): #...
    url = f"https://api.mercadolibre.com{resource_id}"; response = requests.get(url, headers={'Authorization': f'Bearer {token}'}); return response.json() if response.status_code == 200 else None
def post_answer(question_id, answer_text, token): #...
    url = f"https://api.mercadolibre.com/answers"; payload = json.dumps({"question_id": question_id, "text": answer_text}); response = requests.post(url, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}, data=payload);
    if response.status_code in [200, 201]: print(f"+++ RESPOSTA ENVIADA COM SUCESSO! +++"); return True
    return False

# ==============================================================================
# ==============================================================================
@app.route('/notifications', methods=['POST'])
def handle_notification():
    # ... (lógica inicial inalterada)
    global PROCESSED_QUESTIONS; notification_data = request.get_json(); print("\n" + "="*60); current_time = time.time(); PROCESSED_QUESTIONS = {qid: t for qid, t in PROCESSED_QUESTIONS.items() if current_time - t < MEMORY_DURATION_SECONDS}
    if notification_data.get('topic') == 'questions':
        resource_id = notification_data.get('resource'); question_id_from_resource = resource_id.split('/')[-1]
        if question_id_from_resource in PROCESSED_QUESTIONS: print(f"--- NOTIFICAÇÃO DUPLICADA... IGNORANDO. ---"); return jsonify({"status": "ignored_duplicate"}), 200
        PROCESSED_QUESTIONS[question_id_from_resource] = current_time; print(f"--- INICIANDO FLUXO DE RESPOSTA PARA: {resource_id} ---")
        access_token = get_access_token()
        if access_token:
            question_details = get_question_details(resource_id, access_token)
            if question_details and question_details.get('status') == 'UNANSWERED':
                question_text = question_details.get('text', ''); item_id = question_details.get('item_id')
                if not item_id: return jsonify({"status": "error_no_item_id"}), 200
                print(f"TEXTO DA PERGUNTA: '{question_text}' | NO PRODUTO: {item_id}")

                # FLUXO 1: RESPOSTA PRONTA
                canned_answer = check_for_canned_response(question_text)
                if canned_answer:
                    if post_answer(question_details.get('id'), canned_answer, access_token):
                        send_notification_email(question_text, canned_answer) # Dispara o e-mail
                    return jsonify({"status": "success_canned"}), 200

                # FLUXO 2: RESPOSTA DA IA
                print(">>> Nenhuma regra rápida encontrada. Acionando fluxo de IA...")
                product_data_dict = get_product_context_from_dataframe(item_id, KNOWLEDGE_DF)
                ia_response_data = None
                if product_data_dict: ia_response_data = get_reply_logic(question_text, product_data_dict)
                else: print(f">>> FALHA: Produto {item_id} não encontrado no banco de dados Excel.")

                if ia_response_data and isinstance(ia_response_data, dict):
                    status = ia_response_data.get('status'); confidence = ia_response_data.get('confidence_score', 0); core_answer = ia_response_data.get('answer_text')
                    if status == "ANSWER_FOUND" and confidence >= CONFIDENCE_THRESHOLD:
                        greeting = get_time_based_greeting(); closing = f"\n\nAguardamos sua compra!\nEquipe {YOUR_STORE_NAME}"; final_answer_text = f"{greeting}{core_answer}{closing}"
                        print(f">>> DISCERNIMENTO (Status: {status}, Confiança: {confidence}/{CONFIDENCE_THRESHOLD}). Enviando resposta.")
                        if post_answer(question_details.get('id'), final_answer_text, access_token):
                            send_notification_email(question_text, final_answer_text) # Dispara o e-mail
                    else: print(f">>> DISCERNIMENTO (Status: {status}, Confiança: {confidence}). Nenhuma ação.")
                else: print(">>> FLUXO DA IA NÃO RETORNOU DADOS VÁLIDOS. Nenhuma ação será tomada.")

    return jsonify({"status": "success"}), 200

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "running", "service": f"Mercado Livre IA Responder v12.0 - Supervisor ({YOUR_STORE_NAME})"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)