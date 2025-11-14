# Versão 5.0 - Maturação Estratégica
import requests
import time
import os
import json
import schedule
from flask import Flask, request
import threading
from datetime import datetime, timezone, timedelta
import traceback

# --- CONFIGURAÇÕES GLOBAIS ---
MEU_CLIENT_ID = os.environ.get('MEU_CLIENT_ID')
MEU_CLIENT_SECRET = os.environ.get('MEU_CLIENT_SECRET')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_IDS_STR = os.environ.get('TELEGRAM_CHAT_IDS', '')
TELEGRAM_CHAT_IDS = TELEGRAM_CHAT_IDS_STR.split(',') if TELEGRAM_CHAT_IDS_STR else []
DEBUG_CHAT_ID = '8411108712'

ACCOUNTS_CONFIG = {
    323091477: {"client_id": MEU_CLIENT_ID, "client_secret": MEU_CLIENT_SECRET, "refresh_token": os.environ.get('REFRESH_TOKEN_323091477')},
    268181565: {"client_id": MEU_CLIENT_ID, "client_secret": MEU_CLIENT_SECRET, "refresh_token": os.environ.get('REFRESH_TOKEN_268181565')},
    702192285: {"client_id": MEU_CLIENT_ID, "client_secret": MEU_CLIENT_SECRET, "refresh_token": os.environ.get('REFRESH_TOKEN_702192285')},
    75080160: {"client_id": MEU_CLIENT_ID, "client_secret": MEU_CLIENT_SECRET, "refresh_token": os.environ.get('REFRESH_TOKEN_75080160')}
}

CUTOFF_DATE = datetime.now(timezone.utc)
PROCESSED_ORDER_IDS = set()
PROCESSED_IDS_LOCK = threading.Lock()
LEDGER_FILE = "daily_ledger.json"
COMMAND_QUEUE_FILE = "command_queue.json"

SELLER_NICKNAMES = {
    323091477: "EQUIPESCAFORTE",
    268181565: "PORTE FORTE",
    702192285: "PESCA E LAZER",
    75080160: "PESCA_CAMPING"
}

SELLER_EMOJIS = {
    323091477: "🐟",
    268181565: "💪",
    702192285: "☀️",
    75080160: "🏕️"
}

class CommandQueue:
    def __init__(self, filename):
        self.filename = filename
        self._lock = threading.Lock()
        self._ensure_file_exists()
    def _ensure_file_exists(self):
        with self._lock:
            if not os.path.exists(self.filename):
                with open(self.filename, 'w') as f: json.dump([], f)
    def _read_queue(self):
        try:
            with open(self.filename, 'r') as f: return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError): return []
    def add_to_queue(self, item):
        with self._lock:
            queue = self._read_queue()
            queue.append(item)
            with open(self.filename, 'w') as f: json.dump(queue, f, indent=2)
            print(f"   - Ordem adicionada à Fila de Comando: {item['order_id']}")
    def peek_next_item(self):
        with self._lock:
            queue = self._read_queue()
            return queue[0] if queue else None
    def get_next_item(self):
        with self._lock:
            queue = self._read_queue()
            if not queue: return None
            item = queue.pop(0)
            with open(self.filename, 'w') as f: json.dump(queue, f, indent=2)
            return item

class DailyLedger:
    def __init__(self, filename):
        self.filename = filename
        self._lock = threading.Lock()
        self._ensure_file_exists()
    def _ensure_file_exists(self):
        with self._lock:
            if not os.path.exists(self.filename):
                with open(self.filename, 'w') as f: json.dump([], f)
    def record_sale(self, seller_id, gross_value, net_value):
        with self._lock:
            records = self._read_records()
            records.append({"timestamp": datetime.now(timezone.utc).isoformat(), "seller_id": seller_id, "gross": gross_value, "net": net_value})
            with open(self.filename, 'w') as f: json.dump(records, f, indent=2)
        print(f"   - Venda registrada no livro-caixa: {self.filename}")
    def _read_records(self):
        try:
            with open(self.filename, 'r') as f: return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError): return []
    def get_records_for_period(self, start_date, end_date):
        records = self._read_records()
        return [r for r in records if start_date <= datetime.fromisoformat(r['timestamp']) < end_date]

class MeliManager:
    API_URL = "https://api.mercadolibre.com"
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.access_token, self.expires_at = None, 0
        self._lock = threading.Lock()
    def _refresh_token(self):
        seller_nickname = SELLER_NICKNAMES.get(int(self.refresh_token.split('-')[-1]), "ID Desconhecido")
        print(f"--- Renovando token para a conta: {seller_nickname} ---")
        url = f"{self.API_URL}/oauth/token"
        payload = {'grant_type': 'refresh_token', 'client_id': self.client_id, 'client_secret': self.client_secret, 'refresh_token': self.refresh_token}
        headers = {'accept': 'application/json', 'content-type': 'application/x-www-form-urlencoded'}
        try:
            response = requests.post(url, data=payload, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            self.access_token = data['access_token']
            self.refresh_token = data.get('refresh_token', self.refresh_token)
            self.expires_at = time.time() + data['expires_in'] - 60
            print(f">>> Token para {seller_nickname} renovado com sucesso!")
        except requests.exceptions.RequestException as e:
            print(f"!!! Erro crítico ao renovar o token para {seller_nickname}: {e}")
            raise
    def get_access_token(self) -> str:
        with self._lock:
            if not self.access_token or time.time() >= self.expires_at: self._refresh_token()
            return self.access_token

class MultiMeliManager:
    def __init__(self, accounts_config: dict):
        self.managers = {str(seller_id): MeliManager(c['client_id'], c['client_secret'], c['refresh_token']) for seller_id, c in accounts_config.items() if c.get('refresh_token')}
        print(f"Comandante de Frota iniciado com {len(self.managers)} contas sob vigilância.")
    def get_manager_for_seller(self, seller_id: int):
        return self.managers.get(str(seller_id))

class TelegramNotifier:
    API_URL = "https://api.telegram.org/bot"
    def __init__(self, bot_token: str, chat_ids: list[str]):
        if not bot_token or "COLE_SEU" in bot_token: raise ValueError("Token do Bot do Telegram não foi preenchido!")
        if not chat_ids: raise ValueError("A lista de Chat IDs do Telegram está vazia!")
        self.bot_token, self.chat_ids = bot_token, chat_ids
    def send_message(self, text: str):
        print(f"Enviando mensagem para {len(self.chat_ids)} destinatário(s)...")
        for chat_id in self.chat_ids:
            url = f"{self.API_URL}{self.bot_token}/sendMessage"
            payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
            try:
                response = requests.post(url, json=payload, timeout=10)
                response.raise_for_status()
                print(f"  ✅ Mensagem enviada com sucesso para o ID: {chat_id}")
            except requests.exceptions.RequestException as e:
                print(f"  !!! FALHA ao enviar para o ID {chat_id}: {e}")
                raise

app = Flask(__name__)

@app.route("/ml-notifications", methods=['POST'])
def handle_ml_notification():
    notification_data = request.json
    seller_id = notification_data.get('user_id')
    if not seller_id: return "OK (sem user_id)", 200
    
    topic = notification_data.get('topic')
    if topic != 'payments': return "OK (not a payment)", 200

    resource_path = notification_data.get('resource')
    if not resource_path: return "OK (no resource)", 200

    try:
        payment_id = int(resource_path.split('/')[-1])
        manager = multi_manager.get_manager_for_seller(seller_id)
        if not manager: return "OK (vendedor não gerenciado)", 200
        
        token = manager.get_access_token()
        headers = {'Authorization': f'Bearer {token}'}
        payment_response = requests.get(f"{MeliManager.API_URL}{resource_path}", headers=headers)
        payment_response.raise_for_status()
        payment_data = payment_response.json()

        if payment_data.get('status') == 'approved' and payment_data.get('order_id'):
            order_id = payment_data.get('order_id')
            
            with PROCESSED_IDS_LOCK:
                if order_id in PROCESSED_ORDER_IDS:
                    print(f"   - Venda duplicada (ID: {order_id}) já na fila ou processada. Ignorando.")
                    return "OK (duplicate)", 200
            
            command_queue.add_to_queue({
                "seller_id": seller_id,
                "order_id": order_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
    except Exception as e:
        print(f"!!! ERRO NA TRIAGEM: Falha ao adicionar à fila. Erro: {e}")

    return "OK", 200

def process_command_queue():
    MINIMUM_AGE = timedelta(minutes=5)
    
    while True:
        item_to_process = None
        
        # --- LÓGICA DE MATURAÇÃO ESTRATÉGICA ---
        next_item = command_queue.peek_next_item()
        if next_item:
            item_timestamp = datetime.fromisoformat(next_item['timestamp'])
            item_age = datetime.now(timezone.utc) - item_timestamp
            
            if item_age >= MINIMUM_AGE:
                # A ordem está madura, pode ser processada.
                item_to_process = command_queue.get_next_item()
                print(f"\n\n--- 🕵️ Ordem {item_to_process['order_id']} madura. Autorizando processamento. ---")
            else:
                # A ordem é muito recente. Aguarda.
                wait_time = (MINIMUM_AGE - item_age).total_seconds()
                print(f"   - Próxima ordem {next_item['order_id']} muito recente. Maturando por mais {int(wait_time)}s...")
        
        if not item_to_process:
            # Fila vazia ou a próxima ordem ainda não está madura.
            time.sleep(30)
            continue

        # --- INÍCIO DO PROCESSAMENTO DA ORDEM MADURA ---
        seller_id = item_to_process['seller_id']
        order_id = item_to_process['order_id']
        
        print(f"--- ⚙️ Processando Ordem da Fila de Comando: {order_id} ---")

        try:
            with PROCESSED_IDS_LOCK:
                if order_id in PROCESSED_ORDER_IDS:
                    print(f"   - Venda duplicada (ID: {order_id}) já na lista final. Ignorando.")
                    continue
                PROCESSED_ORDER_IDS.add(order_id)

            manager = multi_manager.get_manager_for_seller(seller_id)
            if not manager:
                print(f"   - ERRO: Gerente para vendedor {seller_id} não encontrado.")
                continue
            
            token = manager.get_access_token()
            headers = {'Authorization': f'Bearer {token}'}

            order_details_url = f"{MeliManager.API_URL}/orders/{order_id}"
            order_response = None
            max_retries = 3
            retry_delay = 15 

            for attempt in range(max_retries):
                try:
                    print(f"   - Tentativa {attempt + 1}/{max_retries} para buscar detalhes da venda {order_id}...")
                    order_response = requests.get(order_details_url, headers=headers, timeout=15)
                    order_response.raise_for_status()
                    print(f"   - Detalhes da venda {order_id} obtidos com sucesso.")
                    break 
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 404 and attempt < max_retries - 1:
                        print(f"   - AVISO: Venda {order_id} não encontrada (404). Aguardando {retry_delay}s para nova tentativa.")
                        time.sleep(retry_delay)
                    else:
                        print(f"   - ERRO FINAL: Não foi possível obter detalhes da venda {order_id} após {max_retries} tentativas.")
                        raise 
            
            if not order_response:
                print(f"   - ERRO GRAVE: A resposta da venda {order_id} é nula mesmo após as tentativas.")
                continue

            order_data = order_response.json()

            date_iso_format = order_data.get('date_created', '')
            if not date_iso_format: continue
            sale_datetime_obj = datetime.fromisoformat(date_iso_format.replace('Z', '+00:00'))
            if sale_datetime_obj < CUTOFF_DATE:
                print(f"   - Venda antiga (anterior à inicialização) ignorada. ID: {order_id}")
                continue
            
            print("   - Venda nova e única. Processando com precisão financeira absoluta...")

            total_amount = order_data.get('total_amount', 0)
            shipping_cost = 0.0
            
            mercadolibre_total_fee = 0.0
            fee_details_list = []

            shipping_id = order_data.get('shipping', {}).get('id')
            if shipping_id:
                costs_url = f"{MeliManager.API_URL}/shipments/{shipping_id}/costs"
                costs_response = requests.get(costs_url, headers=headers)
                if costs_response.status_code == 200:
                    costs_data = costs_response.json()
                    for sender in costs_data.get('senders', []):
                        if sender.get('user_id') == seller_id:
                            shipping_cost += sender.get('cost') or 0.0
            
            detailed_fees = order_data.get('fees', [])
            for fee_component in detailed_fees:
                fee_type = fee_component.get('type', 'desconhecida')
                fee_amount = fee_component.get('amount') or 0.0
                fee_cost = abs(fee_amount)
                mercadolibre_total_fee += fee_cost
                
                fee_name_map = {"listing_fee": "Tarifa de Venda", "fixed_fee": "Custo Fixo", "shipping_fee": "Custo de Envio (Tarifa)", "handling_fee": "Taxa de Manuseio"}
                fee_name = fee_name_map.get(fee_type, fee_type.replace('_', ' ').title())
                fee_details_list.append(f"   <em>- {fee_name}: R$ {fee_cost:.2f}</em>")

            imposto_valor = total_amount * 0.0715
            valor_liquido = total_amount - mercadolibre_total_fee - shipping_cost - imposto_valor
            
            ledger.record_sale(seller_id, total_amount, valor_liquido)

            seller_nickname = SELLER_NICKNAMES.get(seller_id, f"ID {seller_id}")
            seller_emoji = SELLER_EMOJIS.get(seller_id, "🏪")
            buyer_info = order_data.get('buyer', {})
            full_buyer_name = f"{buyer_info.get('first_name', '')} {buyer_info.get('last_name', '')}".strip() or buyer_info.get('nickname', 'N/A')
            sale_datetime_str = sale_datetime_obj.strftime('%d/%m/%Y às %H:%M')
            order_item = order_data.get('order_items', [{}])[0]
            item_info = order_item.get('item', {})
            mlb_id = item_info.get('id', 'N/A')
            shipping_info = order_data.get('shipping', {})
            logistic_type = shipping_info.get('logistic_type')
            shipping_mode = "Mercado Envios (FULL)" if logistic_type == 'fulfillment' else "Mercado Envios (Empresa)"

            message = (
                f"💰 <b>NOVA VENDA APROVADA</b> 💰\n\n"
                f"🏪 <b>Vendedor:</b> {seller_emoji} <b>{seller_nickname}</b>\n"
                f"🗓️ <b>Data:</b> {sale_datetime_str}\n\n"
                f"👤 <b>Comprador:</b> {full_buyer_name}\n"
                f"📦 <b>Produto:</b> {item_info.get('title', 'N/A')}\n"
                f"🆔 <b>MLB:</b> {mlb_id}\n"
                f"🧾 <b>ID Venda:</b> {order_id}\n"
                f"🚚 <b>Envio:</b> {shipping_mode}\n\n"
                f"💵 <b>Valor Total:</b> R$ {total_amount:.2f}\n"
                f"💸 <b>Tarifa Total ML:</b> -R$ {mercadolibre_total_fee:.2f}\n"
            )
            if fee_details_list:
                message += "\n".join(fee_details_list) + "\n"

            if shipping_cost > 0:
                message += f"🚛 <b>Custo de Envio (Etiqueta):</b> -R$ {shipping_cost:.2f}\n"
            
            message += (
                f"📉 <b>Imposto (7,15%):</b> -R$ {imposto_valor:.2f}\n"
                f"✅ <b>Valor Líquido Final:</b> R$ {valor_liquido:.2f}"
            )
            
            telegram_notifier.send_message(message)
            print("   - ✅ Notificação de venda enviada com sucesso via Telegram.")

        except Exception as e:
            print(f"!!! FALHA CRÍTICA AO PROCESSAR A FILA. Erro: {e}")
            error_details = traceback.format_exc()
            print(error_details)
            error_message_for_debug = (
                f"🚨 <b>ALERTA DE FALHA - ALMIRANTE v5.0 (FILA)</b> 🚨\n\n"
                f"Ocorreu um erro ao tentar processar uma venda da fila de comando.\n\n"
                f"<b>ID da Venda:</b> {order_id}\n"
                f"<b>Erro:</b>\n<pre>{str(e)}</pre>\n\n"
                f"<b>Detalhes Técnicos:</b>\n<pre>{error_details}</pre>"
            )
            try:
                debug_notifier = TelegramNotifier(bot_token=TELEGRAM_BOT_TOKEN, chat_ids=[DEBUG_CHAT_ID])
                debug_notifier.send_message(error_message_for_debug)
                print(f"   - ✅ Mensagem de DEBUG da Caixa-Preta enviada para o ID {DEBUG_CHAT_ID}.")
            except Exception as debug_e:
                print(f"!!! FALHA CATASTRÓFICA: Não foi possível enviar nem a mensagem de DEBUG. Erro: {debug_e}")

def send_daily_report():
    print("\n\n--- ⚙️  Gerando Relatório Diário... ---")
    today = datetime.now(timezone.utc).date()
    start_of_day = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    end_of_day = start_of_day + timedelta(days=1)
    records = ledger.get_records_for_period(start_of_day, end_of_day)
    if not records:
        print("--- 📪  Nenhuma venda registrada hoje. Relatório não enviado. ---")
        return
    total_gross = sum(r['gross'] for r in records)
    total_net = sum(r['net'] for r in records)
    total_units = len(records)
    total_deductions = total_gross - total_net
    profit_percentage = (total_deductions / total_gross * 100) if total_gross > 0 else 0
    message = (
        f"📊 <b>RELATÓRIO DIÁRIO DE VENDAS</b> 📊\n"
        f"<em>Data: {today.strftime('%d/%m/%Y')}</em>\n\n"
        f"📦 <b>Unidades Vendidas:</b> {total_units}\n\n"
        f"💵 <b>Faturamento Bruto:</b> R$ {total_gross:.2f}\n"
        f"✅ <b>Faturamento Líquido:</b> R$ {total_net:.2f}\n\n"
        f"📉 <b>Total de Custos (Tarifa+Imp):</b> R$ {total_deductions:.2f}\n"
        f"💡 <b>Percentual de Custo:</b> {profit_percentage:.2f}%"
    )
    telegram_notifier.send_message(message)
    print("--- ✅  Relatório Diário enviado com sucesso! ---\n")

def send_monthly_report():
    print("\n\n--- ⚙️  Verificando se é fim de mês para Relatório Mensal... ---")
    now = datetime.now(timezone.utc)
    is_last_day = (now + timedelta(days=1)).day == 1
    if not is_last_day:
        print("--- 📪  Não é o último dia do mês. Relatório mensal não gerado. ---")
        return
    print("--- ⚙️  É o último dia do mês! Gerando Relatório Mensal... ---")
    start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    end_of_month = (start_of_month + timedelta(days=32)).replace(day=1)
    records = ledger.get_records_for_period(start_of_month, end_of_month)
    if not records:
        print("--- 📪  Nenhuma venda registrada no mês. Relatório não enviado. ---")
        return
    total_gross = sum(r['gross'] for r in records)
    total_net = sum(r['net'] for r in records)
    total_units = len(records)
    total_deductions = total_gross - total_net
    profit_percentage = (total_deductions / total_gross * 100) if total_gross > 0 else 0
    message = (
        f"🏆 <b>RELATÓRIO MENSAL CONSOLIDADO</b> 🏆\n"
        f"<em>Mês de Referência: {now.strftime('%B de %Y')}</em>\n\n"
        f"📦 <b>Total de Unidades Vendidas:</b> {total_units}\n\n"
        f"💵 <b>Faturamento Bruto Total:</b> R$ {total_gross:.2f}\n"
        f"✅ <b>Faturamento Líquido Total:</b> R$ {total_net:.2f}\n\n"
        f"📉 <b>Total de Custos (Tarifa+Imp):</b> R$ {total_deductions:.2f}\n"
        f"💡 <b>Percentual de Custo Total:</b> {profit_percentage:.2f}%"
    )
    telegram_notifier.send_message(message)
    print("--- ✅  Relatório Mensal enviado com sucesso! ---\n")

def run_scheduler():
    schedule.every().day.at("23:59").do(send_daily_report)
    schedule.every().day.at("23:58").do(send_monthly_report)
    while True:
        schedule.run_pending()
        time.sleep(1)

def run_app():
    port = int(os.environ.get('PORT', 10000))
    app.run(port=port, host='0.0.0.0')

if __name__ == "__main__":
    if not all([MEU_CLIENT_ID, MEU_CLIENT_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS]):
        print("!!! ERRO CRÍTICO: Variáveis de ambiente essenciais não foram configuradas.")
        exit(1)

    command_queue = CommandQueue(COMMAND_QUEUE_FILE)
    ledger = DailyLedger(LEDGER_FILE)
    multi_manager = MultiMeliManager(ACCOUNTS_CONFIG)
    telegram_notifier = TelegramNotifier(bot_token=TELEGRAM_BOT_TOKEN, chat_ids=TELEGRAM_CHAT_IDS)
    
    queue_processor_thread = threading.Thread(target=process_command_queue)
    queue_processor_thread.daemon = True
    queue_processor_thread.start()

    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()

    print("======================================================================")
    print("  Almirante Estratégico ATIVADO! (v5.0 - Maturação Estratégica)")
    print(f"  Linha do tempo definida. Ignorando vendas anteriores a: {CUTOFF_DATE.strftime('%d/%m/%Y %H:%M:%S')}")
    print("  General de Logística inspecionando a fila a cada 30s.")
    print("  Motor de relatórios diários e mensais engajado.")
    print("  Servidor web (Triage) iniciando para receber notificações...")
    print("======================================================================")
    
    run_app()