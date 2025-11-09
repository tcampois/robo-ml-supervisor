# CÓDIGO MESTRE v13.0 (Sua Lógica + Minha Blindagem de Diagnóstico)
from flask import Flask, request, jsonify
import os
import requests
import json
import time
import pandas as pd
from datetime import datetime
import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- CONFIGURAÇÃO ---
APP_ID = os.environ.get('MERCADO_LIVRE_APP_ID')
CLIENT_SECRET = os.environ.get('MERCADO_LIVRE_CLIENT_SECRET')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
CONFIDENCE_THRESHOLD = 8
YOUR_STORE_NAME = "Riomar Equipesca"
EMAIL_SENDER = os.environ.get('EMAIL_SENDER')
EMAIL_RECEIVER = os.environ.get('EMAIL_RECEIVER')
EMAIL_APP_PASSWORD = os.environ.get('EMAIL_APP_PASSWORD')

# Sua nova lista de palavras-chave para o Plano B
GENERIC_DESCRIPTION_KEYWORDS = [
    'vantagem', 'vantagens', 'benefício', 'benefícios', 'característica', 
    'características', 'descrição', 'detalhes', 'qualidades', 'informações'
]

# --- CARREGAMENTO DOS BANCOS DE DADOS ---
KNOWLEDGE_DF = None; CANNED_RESPONSES = []
try:
    # Usando a sua correção do nome da coluna. Perfeito.
    KNOWLEDGE_DF = pd.read_excel('catalogo_produtos.xlsx', dtype={'Código do anúncio': str})
    print(f">>> Banco de Dados Excel v13.0 carregado. {len(KNOWLEDGE_DF)} produtos.")
except Exception as e:
    print(f"### ERRO CRÍTICO NO EXCEL: {e} ###")
try:
    with open('canned_responses.json', 'r', encoding='utf-8') as f:
        CANNED_RESPONSES = json.load(f)
        print(f">>> Livro de Regras v13.0 carregado. {len(CANNED_RESPONSES)} regras.")
except Exception as e:
    print(f"### ERRO CRÍTICO NO JSON: {e} ###")

PROCESSED_QUESTIONS = {}; MEMORY_DURATION_SECONDS = 300
app = Flask(__name__)

# --- FUNÇÕES DE LÓGICA (Com Blindagem Adicional) ---

def send_notification_email(question_text, answer_text):
    if not all([EMAIL_SENDER, EMAIL_RECEIVER, EMAIL_APP_PASSWORD]): print(">>> AVISO: Credenciais de e-mail não configuradas."); return
    message = MIMEMultipart("alternative"); message["Subject"] = f"Robô ML Respondeu: \"{question_text[:30]}...\""; message["From"] = EMAIL_SENDER; message["To"] = EMAIL_RECEIVER
    html = f"""<html><body><p><strong>Uma resposta automática foi enviada.</strong></p><hr><p><strong>Pergunta:</strong></p><p style="padding: 10px; border-left: 3px solid #ccc;">{question_text}</p><p><strong>Resposta:</strong></p><p style="padding: 10px; border-left: 3px solid #007bff;">{answer_text.replace(os.linesep, '<br>')}</p></body></html>"""; message.attach(MIMEText(html, "html"))
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server: server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD); server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, message.as_string()); print(">>> NOTIFICAÇÃO POR E-MAIL ENVIADA!")
    except Exception as e: print(f"### FALHA NO ENVIO DE E-MAIL: {e} ###")

def check_for_canned_response(question_text):
    normalized_question = question_text.lower()
    for rule in CANNED_RESPONSES:
        for keyword in rule['keywords']:
            if keyword.lower() in normalized_question: print(f">>> PLANO A ACIONADO! Keyword: '{keyword}', Regra: '{rule['name']}'."); return rule['response']
    return None

def check_for_generic_description_query(question_text):
    normalized_question = question_text.lower()
    for keyword in GENERIC_DESCRIPTION_KEYWORDS:
        if keyword in normalized_question: print(f">>> PLANO B ACIONADO! Keyword genérica: '{keyword}'."); return True
    return False

def get_time_based_greeting():
    current_hour = datetime.utcnow().hour - 3;
    if current_hour < 0: current_hour += 24
    if 5 <= current_hour < 12: return "Olá! Bom dia. "
    elif 12 <= current_hour < 18: return "Olá! Boa tarde. "
    else: return "Olá! Boa noite. "

def extract_json_from_ia_response(text):
    try: json_start = text.find('{'); json_end = text.rfind('}') + 1; return json.loads(text[json_start:json_end]) if json_start != -1 and json_end != -1 else None
    except Exception: return None

def get_reply_logic(question_text, product_data):
    print(">>> CÉREBRO DE IA (PLANO C) ACIONADO...")
    description_text = product_data.pop('Descrição', 'Nenhuma descrição textual fornecida.') # Usando a coluna 'Descrição'
    structured_data_text = "\n".join([f"{key}: {value}" for key, value in product_data.items()])
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GOOGLE_API_KEY}"; headers = {'Content-Type': 'application/json'};
    prompt = f"""# MISSÃO\nVocê é um Perito Verificador. Sua missão é responder à pergunta e AVALIAR se a resposta foi encontrada. Sua resposta DEVE SER um objeto JSON válido.\n# FONTES DE DADOS\n---\n# DADOS ESTRUTURADOS: {structured_data_text}\n# DESCRIÇÃO TEXTUAL: {description_text}\n---\n# PERGUNTA DO CLIENTE\n"{question_text}"\n# INSTRUÇÕES\n1. Leia a pergunta e as fontes de dados CUIDADOSAMENTE.\n2. Se encontrar a resposta EXATA, defina "status" como "ANSWER_FOUND".\n3. Se NÃO encontrar, defina "status" como "INFORMATION_NOT_FOUND".\n4. Avalie sua confiança (0 a 10) no "confidence_score".\n5. Formate a saída EXCLUSIVAMENTE como JSON.\n# SAÍDA JSON OBRIGATÓRIA:\n"""; payload = {"contents": [{"parts": [{"text": prompt}]}]};
    try: response = requests.post(url, headers=headers, data=json.dumps(payload)); response.raise_for_status(); ia_response_string = response.json()['candidates'][0]['content']['parts'][0]['text']; print(f">>> IA RETORNOU: {ia_response_string}"); return extract_json_from_ia_response(ia_response_string)
    except Exception as e: print(f"### ERRO NA CHAMADA À IA: {e} ###"); return None

def get_product_context_from_dataframe(item_id, dataframe):
    if dataframe is None: print("### ERRO: DataFrame de conhecimento não está carregado."); return None
    try:
        product_row = dataframe[dataframe['Código do anúncio'] == item_id]
        if not product_row.empty: return product_row.iloc[0].dropna().to_dict()
        return None
    except KeyError: print(f"### ERRO DE LÓGICA: A coluna 'Código do anúncio' não foi encontrada no Excel."); return None

# --- FUNÇÕES DE API (COM BLINDAGEM DE DIAGNÓSTICO) ---
def get_access_token():
    url = "https://api.mercadolibre.com/oauth/token"; payload = {'grant_type': 'client_credentials', 'client_id': APP_ID, 'client_secret': CLIENT_SECRET};
    try: response = requests.post(url, headers={'accept': 'application/json', 'content-type': 'application/x-www-form-urlencoded'}, data=payload); response.raise_for_status(); return response.json().get('access_token')
    except requests.exceptions.RequestException as e: print(f"### ERRO DE API (get_access_token): {e} ###"); return None

def get_question_details(resource_id, token):
    url = f"https://api.mercadolibre.com{resource_id}";
    try: response = requests.get(url, headers={'Authorization': f'Bearer {token}'}); response.raise_for_status(); return response.json()
    except requests.exceptions.RequestException as e: print(f"### ERRO DE API (get_question_details): {e} ###"); return None

def post_answer(question_id, answer_text, token):
    url = f"https://api.mercadolibre.com/answers"; payload = json.dumps({"question_id": question_id, "text": answer_text});
    try: response = requests.post(url, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}, data=payload); response.raise_for_status(); print(f"+++ RESPOSTA ENVIADA COM SUCESSO! +++"); return True
    except requests.exceptions.RequestException as e: print(f"### ERRO DE API (post_answer): {e.response.text} ###"); return False

# ==============================================================================
# ==============================================================================
@app.route('/notifications', methods=['POST'])
def handle_notification():
    global PROCESSED_QUESTIONS; notification_data = request.get_json(); print("\n" + "="*60); current_time = time.time(); PROCESSED_QUESTIONS = {qid: t for qid, t in PROCESSED_QUESTIONS.items() if current_time - t < MEMORY_DURATION_SECONDS}
    if notification_data.get('topic') == 'questions':
        resource_id = notification_data.get('resource');
        if not resource_id: return jsonify({"status": "ignored_no_resource"}), 200
        
        question_id_from_resource = resource_id.split('/')[-1]
        if question_id_from_resource in PROCESSED_QUESTIONS: print(f"--- NOTIFICAÇÃO DUPLICADA... IGNORANDO. ---"); return jsonify({"status": "ignored_duplicate"}), 200
        
        PROCESSED_QUESTIONS[question_id_from_resource] = current_time; print(f"--- INICIANDO FLUXO DE RESPOSTA PARA: {resource_id} ---")
        
        access_token = get_access_token()
        if not access_token:
            print("### FALHA CRÍTICA: Não foi possível obter o Access Token. Verifique as credenciais MERCADO_LIVRE_APP_ID e MERCADO_LIVRE_CLIENT_SECRET nas Variáveis de Ambiente da Render."); return jsonify({"status": "error_token"}), 200

        question_details = get_question_details(resource_id, access_token)
        if not question_details or question_details.get('status') != 'UNANSWERED':
            print("### AVISO: Não foi possível obter detalhes ou a pergunta já foi respondida/deletada. Encerrando fluxo."); return jsonify({"status": "error_question_details"}), 200

        question_text = question_details.get('text', ''); item_id = question_details.get('item_id')
        if not item_id: print(f"--- ERRO: A notificação {resource_id} não contém um 'item_id'."); return jsonify({"status": "error_no_item_id"}), 200
        
        print(f"TEXTO DA PERGUNTA: '{question_text}' | NO PRODUTO: {item_id}")

        # --- FLUXO 1: RESPOSTA PRONTA (PLANO A) ---
        canned_answer = check_for_canned_response(question_text)
        if canned_answer:
            if post_answer(question_details.get('id'), canned_answer, access_token): send_notification_email(question_text, canned_answer)
            return jsonify({"status": "success_canned"}), 200

        product_data_dict = get_product_context_from_dataframe(item_id, KNOWLEDGE_DF)
        if not product_data_dict:
            print(f">>> FALHA: Produto {item_id} não encontrado no banco de dados Excel. Encerrando fluxo."); return jsonify({"status": "error_product_not_found"}), 200

        # --- FLUXO 1.5: PERGUNTAS GENÉRICAS SOBRE DESCRIÇÃO (PLANO B) ---
        if check_for_generic_description_query(question_text):
            if 'Descrição' in product_data_dict:
                description = product_data_dict['Descrição']; greeting = get_time_based_greeting(); closing = f"\n\nAguardamos sua compra!\nEquipe {YOUR_STORE_NAME}";
                final_answer_text = f"{greeting}Claro! Seguem os detalhes do produto: \n\n{description}{closing}"
                if post_answer(question_details.get('id'), final_answer_text, access_token): send_notification_email(question_text, final_answer_text)
                return jsonify({"status": "success_description_fallback"}), 200
            else: print(f">>> PLANO B FALHOU: Pergunta genérica, mas produto {item_id} não tem coluna 'Descrição' no Excel.")

        # --- FLUXO 2: RESPOSTA DA IA (PLANO C) ---
        print(">>> Nenhum plano anterior funcionou. Acionando fluxo de IA (Plano C)...")
        ia_response_data = get_reply_logic(question_text, product_data_dict)
        if ia_response_data and isinstance(ia_response_data, dict):
            status = ia_response_data.get('status'); confidence = ia_response_data.get('confidence_score', 0); core_answer = ia_response_data.get('answer_text')
            if status == "ANSWER_FOUND" and confidence >= CONFIDENCE_THRESHOLD:
                greeting = get_time_based_greeting(); closing = f"\n\nAguardamos sua compra!\nEquipe {YOUR_STORE_NAME}"; final_answer_text = f"{greeting}{core_answer}{closing}"
                print(f">>> DISCERNIMENTO (Status: {status}, Confiança: {confidence}/{CONFIDENCE_THRESHOLD}). Enviando resposta da IA.")
                if post_answer(question_details.get('id'), final_answer_text, access_token): send_notification_email(question_text, final_answer_text)
            else: print(f">>> DISCERNIMENTO DA IA (Status: {status}, Confiança: {confidence}). Nenhuma ação será tomada.")
        else: print(">>> FLUXO DA IA NÃO RETORNOU DADOS VÁLIDOS. Nenhuma ação será tomada.")

    return jsonify({"status": "success"}), 200

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "running", "service": f"Mercado Livre IA Responder v13.0 - Supervisor ({YOUR_STORE_NAME})"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000)) # Render usa a variável PORT
    app.run(host='0.0.0.0', port=port)
