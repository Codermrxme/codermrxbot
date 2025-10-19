import os
import json
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import pymongo
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging

# Log sozlamalari - faqat muhim loglar
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Load env
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    print("❌ BOT_TOKEN not set")
    exit(1)

try:
    MAIN_ADMIN = int(os.getenv('MAIN_ADMIN')) if os.getenv('MAIN_ADMIN') else None
except Exception:
    MAIN_ADMIN = None

BASE_URL = f"https://api.telegram.org/bot{TOKEN}/"

# Mongo settings
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017')
MONGO_DB = os.getenv('MONGO_DB', 'codermrxbot')

# Toshkent vaqti (UTC+5)
TASHKENT_TZ = timezone(timedelta(hours=5))

def get_tashkent_time():
    return datetime.now(TASHKENT_TZ)

def format_tashkent_time(dt=None):
    if dt is None:
        dt = get_tashkent_time()
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def format_uptime(seconds):
    days = seconds // (24 * 3600)
    seconds %= (24 * 3600)
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    
    parts = []
    if days > 0:
        parts.append(f"{days} kun")
    if hours > 0:
        parts.append(f"{hours} soat")
    if minutes > 0:
        parts.append(f"{minutes} daqiqa")
    if seconds > 0 or not parts:
        parts.append(f"{seconds} soniya")
    
    return " ".join(parts)

# Bot ishga tushgan vaqti
BOT_START_TIME = get_tashkent_time()

# Global o'zgaruvchilar
mongo_connected = False
users_col = channels_col = None

# MongoDB ulanish
def init_mongodb():
    global mongo_connected, users_col, channels_col
    try:
        mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        mongo_client.admin.command('ping')
        mongo_connected = True
        db = mongo_client[MONGO_DB]
        users_col = db['users']
        channels_col = db['channels']
        print("✅ MongoDB ga ulandi")
    except Exception:
        mongo_connected = False
        print("❌ MongoDB ga ulanmadi")

# Fayl tizimi
os.makedirs('data', exist_ok=True)
os.makedirs('exports', exist_ok=True)

USERS_FILE = 'data/users.json'
CHANNELS_FILE = 'data/channels.json'
ADMINS_FILE = 'data/admins.json'
MESSAGES_FILE = 'data/messages.json'
LAST_OFFSET_FILE = 'data/last_offset.txt'

DEFAULT_DATA = {
    'users': {},
    'channels': {},
    'admins': [MAIN_ADMIN] if MAIN_ADMIN else [],
    'messages': []
}

def safe_load_json(filename, default):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

def save_json(data, filename):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def load_data():
    data = {'users': {}, 'channels': {}, 'admins': [], 'messages': []}
    
    # Users
    try:
        if mongo_connected and users_col is not None:
            for doc in users_col.find():
                uid = str(doc.get('id') or doc.get('_id'))
                data['users'][uid] = {
                    'id': int(uid),
                    'first_name': doc.get('first_name', ''),
                    'last_name': doc.get('last_name', ''),
                    'username': doc.get('username', ''),
                    'phone': doc.get('phone', ''),
                    'joined': doc.get('joined', format_tashkent_time()),
                    'last_active': doc.get('last_active', format_tashkent_time()),
                    'message_count': int(doc.get('message_count', 0)),
                    'is_admin': bool(doc.get('is_admin', False))
                }
        else:
            data['users'] = safe_load_json(USERS_FILE, DEFAULT_DATA['users'])
    except Exception:
        data['users'] = safe_load_json(USERS_FILE, DEFAULT_DATA['users'])

    # Channels
    try:
        if mongo_connected and channels_col is not None:
            for doc in channels_col.find():
                key = doc.get('username') or str(doc.get('_id'))
                data['channels'][key] = {
                    'username': doc.get('username', key),
                    'name': doc.get('name', key),
                    'added_by': doc.get('added_by'),
                    'added_date': doc.get('added_date')
                }
        else:
            data['channels'] = safe_load_json(CHANNELS_FILE, DEFAULT_DATA['channels'])
    except Exception:
        data['channels'] = safe_load_json(CHANNELS_FILE, DEFAULT_DATA['channels'])

    # Admins
    data['admins'] = safe_load_json(ADMINS_FILE, DEFAULT_DATA['admins'])
    
    # Messages
    data['messages'] = safe_load_json(MESSAGES_FILE, [])[-100:]

    if MAIN_ADMIN and MAIN_ADMIN not in data['admins']:
        data['admins'].append(MAIN_ADMIN)

    return data

def save_data(data):
    # Users
    try:
        if mongo_connected and users_col is not None:
            for uid, u in data['users'].items():
                users_col.update_one({'id': int(uid)}, {'$set': {
                    'id': int(uid),
                    'first_name': u.get('first_name', ''),
                    'last_name': u.get('last_name', ''),
                    'username': u.get('username', ''),
                    'phone': u.get('phone', ''),
                    'joined': u.get('joined', ''),
                    'last_active': u.get('last_active', ''),
                    'message_count': int(u.get('message_count', 0)),
                    'is_admin': bool(u.get('is_admin', False))
                }}, upsert=True)
        save_json(data['users'], USERS_FILE)
    except Exception:
        save_json(data['users'], USERS_FILE)

    # Channels
    try:
        if mongo_connected and channels_col is not None:
            for key, c in data['channels'].items():
                channels_col.update_one({'username': c.get('username', key)}, {'$set': {
                    'username': c.get('username', key),
                    'name': c.get('name', key),
                    'added_by': c.get('added_by'),
                    'added_date': c.get('added_date')
                }}, upsert=True)
        save_json(data['channels'], CHANNELS_FILE)
    except Exception:
        save_json(data['channels'], CHANNELS_FILE)

    # Admins
    save_json(data['admins'], ADMINS_FILE)
    
    # Messages
    save_json(data.get('messages', [])[-200:], MESSAGES_FILE)

def send_message(chat_id, text, reply_markup=None):
    try:
        url = BASE_URL + "sendMessage"
        payload = {
            'chat_id': chat_id, 
            'text': text, 
            'parse_mode': 'HTML',
            'disable_web_page_preview': True
        }
        if reply_markup:
            payload['reply_markup'] = json.dumps(reply_markup)
        
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception:
        return False

def get_updates(offset=None):
    try:
        url = BASE_URL + "getUpdates"
        params = {
            'timeout': 60,
            'limit': 100,
        }
        if offset is not None:
            params['offset'] = offset
            
        response = requests.get(url, params=params, timeout=65)
        if response.status_code == 200:
            return response.json().get('result', [])
        return []
    except Exception:
        return []

def create_keyboard(buttons, row_width=2):
    keyboard = []
    row = []
    for button in buttons:
        row.append({'text': button})
        if len(row) == row_width:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return {'keyboard': keyboard, 'resize_keyboard': True}

def user_menu(is_admin=False):
    buttons = ["📢 Bizning kanallar", "💸 Donat", "ℹ️ Yordam"]
    if is_admin:
        buttons.append("🔙 Admin paneli")
    return create_keyboard(buttons)

def admin_menu():
    buttons = ["📊 Statistika", "👥 Userlar ro'yxati", "📣 Hammaga xabar", "👨‍💻 Adminlar", "📢 Kanallar", "🔙 Foydalanuvchi menyusi"]
    return create_keyboard(buttons, 2)

def admins_management_menu():
    buttons = ["➕ Admin qo'shish", "➖ Admin o'chirish", "📋 Adminlar ro'yxati", "🔙 Admin paneli"]
    return create_keyboard(buttons, 2)

def channels_management_menu():
    buttons = ["➕ Kanal qo'shish", "➖ Kanal o'chirish", "📋 Kanallar ro'yxati", "🔙 Admin paneli"]
    return create_keyboard(buttons, 2)

def get_stats(data):
    total_users = len(data['users'])
    total_messages = len(data.get('messages', []))
    total_admins = len(data['admins'])
    total_channels = len(data['channels'])
    
    # Faol foydalanuvchilar (oxirgi 7 kun)
    active_users = 0
    one_week_ago = get_tashkent_time() - timedelta(days=7)
    
    for user in data['users'].values():
        last_active = user.get('last_active', '')
        if last_active:
            try:
                user_time = datetime.strptime(last_active, '%Y-%m-%d %H:%M:%S').replace(tzinfo=TASHKENT_TZ)
                if user_time >= one_week_ago:
                    active_users += 1
            except Exception:
                continue

    # Uptime hisoblash
    current_time = get_tashkent_time()
    uptime_seconds = int((current_time - BOT_START_TIME).total_seconds())
    uptime_str = format_uptime(uptime_seconds)

    return (
        "📊 <b>Bot statistikasi</b>\n\n"
        f"👥 <b>Jami foydalanuvchilar:</b> {total_users}\n"
        f"🟢 <b>Faol foydalanuvchilar:</b> {active_users}\n"
        f"📨 <b>Jami xabarlar:</b> {total_messages}\n"
        f"👨‍💻 <b>Adminlar:</b> {total_admins}\n"
        f"📢 <b>Kanallar:</b> {total_channels}\n\n"
        f"🕒 <b>Bot ishga tushgan vaqti:</b> {BOT_START_TIME.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"⏱️ <b>Ishlash vaqti:</b> {uptime_str}\n"
        f"💾 <b>Ma'lumotlar manbai:</b> {'MongoDB' if mongo_connected else 'JSON fayllar'}\n"
        f"🌏 <b>Mintaqa:</b> Toshkent (UTC+5)"
    )

def export_users_to_excel(chat_id, data):
    try:
        if not data['users']:
            send_message(chat_id, "❌ Foydalanuvchilar mavjud emas!")
            return
        
        users_list = []
        for user_id, user in data['users'].items():
            users_list.append({
                'ID': user_id,
                'Ism': user.get('first_name', ''),
                'Familiya': user.get('last_name', ''),
                'Username': f"@{user.get('username', '')}" if user.get('username') else '',
                'Telefon': user.get('phone', ''),
                'Qo\'shilgan sana': user.get('joined', ''),
                'Oxirgi faollik': user.get('last_active', ''),
                'Xabarlar soni': user.get('message_count', 0),
                'Admin': 'Ha' if int(user_id) in data['admins'] else 'Yo\'q'
            })
        
        df = pd.DataFrame(users_list)
        filename = f"exports/users_{get_tashkent_time().strftime('%Y%m%d_%H%M%S')}.xlsx"
        df.to_excel(filename, index=False)
        
        with open(filename, 'rb') as f:
            files = {'document': f}
            params = {'chat_id': chat_id, 'caption': '📊 Foydalanuvchilar ro\'yxati'}
            requests.post(f"{BASE_URL}sendDocument", params=params, files=files, timeout=30)
            
        try:
            os.remove(filename)
        except:
            pass
            
    except Exception:
        send_message(chat_id, "❌ Foydalanuvchilar ro'yxatini yuborishda xatolik yuz berdi!")

def broadcast_message(chat_id, text, data):
    try:
        total_users = len(data['users'])
        send_message(chat_id, f"📣 Xabar {total_users} foydalanuvchiga yuborilmoqda...")
        
        success = 0
        failed = 0
        
        for user_id in list(data['users'].keys()):
            try:
                if int(user_id) not in data['admins']:
                    if send_message(int(user_id), text):
                        success += 1
                    else:
                        failed += 1
                    time.sleep(0.1)
            except Exception:
                failed += 1
        
        send_message(chat_id, 
                    f"📣 Xabar yuborish yakunlandi!\n\n"
                    f"✅ Muvaffaqiyatli: {success}\n"
                    f"❌ Xatolar: {failed}", 
                    reply_markup=admin_menu())
    except Exception:
        send_message(chat_id, "❌ Xabar tarqatishda xatolik yuz berdi!")

# Soddalashtirilgan Health server
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/', '/health', '/status']:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # HTTP loglarini o'chirish

def run_health_server():
    port = int(os.environ.get('PORT', 8000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    print(f"🔄 Health server {port}-portda ishga tushdi")
    server.serve_forever()

def load_next_offset():
    try:
        with open(LAST_OFFSET_FILE, 'r') as f:
            offset = f.read().strip()
            return int(offset) if offset else None
    except:
        return None

def save_next_offset(offset):
    try:
        with open(LAST_OFFSET_FILE, 'w') as f:
            f.write(str(offset))
    except Exception:
        pass

def ensure_no_webhook():
    try:
        requests.get(BASE_URL + "deleteWebhook", timeout=5)
    except Exception:
        pass

# Render URL siz o'zini ping qilish
def self_ping():
    """Bot o'ziga har 5 minutda so'rov yuboradi"""
    def ping_loop():
        time.sleep(30)  # Bot to'liq yuklanishini kutadi
        
        # Render external hostname ni olish
        render_hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
        
        while True:
            try:
                # Agar Render hostname mavjud bo'lsa, o'sha URL ga so'rov yuboradi
                if render_hostname:
                    url = f"https://{render_hostname}"
                    response = requests.get(url, timeout=10)
                    print(f"🔄 Self-ping: {url} → {response.status_code}")
                else:
                    # Agar hostname bo'lmasa, local health check qiladi
                    port = os.environ.get('PORT', 8000)
                    response = requests.get(f"http://localhost:{port}/health", timeout=5)
                    print(f"🔄 Local ping: {response.status_code}")
                    
            except Exception as e:
                print(f"🔄 Ping xatosi: {e}")
            
            # Har 5 minutda (300 soniya)
            time.sleep(300)
    
    # Ping ni background da ishga tushirish
    ping_thread = threading.Thread(target=ping_loop, daemon=True)
    ping_thread.start()
    print("✅ Self-ping funksiyasi ishga tushdi")

# Asosiy message processor
def process_message(update, data):
    try:
        message = update.get('message') or {}
        chat_id = message.get('chat', {}).get('id')
        user_id = message.get('from', {}).get('id')
        text = (message.get('text') or '').strip()

        if not user_id:
            return data

        user_id_str = str(user_id)
        current_time = format_tashkent_time()
        
        # User ma'lumotlarini yangilash
        if user_id_str not in data['users']:
            data['users'][user_id_str] = {
                'id': user_id,
                'first_name': message.get('from', {}).get('first_name', ''),
                'last_name': message.get('from', {}).get('last_name', ''),
                'username': message.get('from', {}).get('username', ''),
                'phone': message.get('contact', {}).get('phone_number', '') if 'contact' in message else '',
                'joined': current_time,
                'last_active': current_time,
                'message_count': 1,
                'is_admin': user_id in data['admins']
            }
        else:
            data['users'][user_id_str]['last_active'] = current_time
            data['users'][user_id_str]['message_count'] = data['users'][user_id_str].get('message_count', 0) + 1

        # Xabarni saqlash
        data['messages'].append({
            'user_id': user_id,
            'text': text,
            'date': current_time
        })

        # Command processing
        if text == "/start":
            if user_id in data['admins']:
                send_message(chat_id, "👋 Admin paneliga xush kelibsiz!", admin_menu())
            else:
                send_message(chat_id, 
                            "👋 Botimizga xush kelibsiz! Savollaringiz bo'lsa yozib qoldiring va biz tez orada siz bilan bog'lanamiz", 
                            user_menu())

        elif text == "🔙 Foydalanuvchi menyusi":
            send_message(chat_id, "Asosiy menyu:", user_menu(is_admin=(user_id in data['admins'])))

        elif text == "🔙 Admin paneli" and user_id in data['admins']:
            send_message(chat_id, "Admin paneliga qaytildi:", admin_menu())

        elif text == "📢 Bizning kanallar":
            channels = "\n".join([f"📢 {channel.get('name', channel_id)}" for channel_id, channel in data['channels'].items()])
            send_message(chat_id, f"📢 Bizning kanallar:\n\n{channels or 'Hozircha kanallar mavjud emas'}")

        elif text == "💸 Donat":
            send_message(chat_id, "💸 Bizni qo'llab-quvvatlang:\n\n🔹 Donat link: https://tirikchilik.uz/codermrx\n")

        elif text == "ℹ️ Yordam":
            send_message(chat_id, "ℹ️ Yordam:\n\nAgar savollaringiz bo'lsa, @codermrxbot ga yozishingiz mumkin.")

        # Admin commands
        elif user_id in data['admins']:
            if text == "📊 Statistika":
                send_message(chat_id, get_stats(data))
            elif text == "👥 Userlar ro'yxati":
                export_users_to_excel(chat_id, data)
            elif text == "👨‍💻 Adminlar":
                send_message(chat_id, "Adminlar boshqaruvi:", admins_management_menu())
            elif text == "📢 Kanallar":
                send_message(chat_id, "Kanallar boshqaruvi:", channels_management_menu())
            elif text == "📋 Adminlar ro'yxati":
                admins_list = "\n".join([f"👤 {data['users'].get(str(a), {}).get('first_name','Nomalum')} (ID: {a})" for a in data['admins']])
                send_message(chat_id, f"Adminlar ro'yxati:\n\n{admins_list}")
            elif text == "📋 Kanallar ro'yxati":
                channels_list = "\n".join([f"📢 {c.get('name', k)}" for k, c in data['channels'].items()])
                send_message(chat_id, f"Kanallar ro'yxati:\n\n{channels_list or 'Kanallar mavjud emas'}")

        save_data(data)
        return data
        
    except Exception:
        return data

def main():
    print("🚀 Bot ishga tushmoqda...")
    
    # MongoDB ni ishga tushirish
    init_mongodb()
    
    # Webhook ni o'chirish
    ensure_no_webhook()
    
    # Health server ni ishga tushirish
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Self-ping ni ishga tushirish
    self_ping()
    
    # Ma'lumotlarni yuklash
    data = load_data()
    next_offset = load_next_offset()
    
    print(f"✅ Bot ishga tushdi: {format_tashkent_time()}")
    print(f"📊 Userlar: {len(data['users'])}, Kanallar: {len(data['channels'])}")
    
    # Asosiy loop
    while True:
        try:
            updates = get_updates(next_offset)
            
            for update in updates:
                update_id = update.get('update_id')
                if update_id is not None:
                    if next_offset is None or update_id >= next_offset:
                        data = process_message(update, data)
                        next_offset = update_id + 1
                        save_next_offset(next_offset)
            
            time.sleep(1)
            
        except Exception as e:
            print(f"Xato: {e}")
            time.sleep(5)

if __name__ == '__main__':
    main()