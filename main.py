import os
import json
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import pymongo
import threading
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# Load env
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise SystemExit("BOT_TOKEN not set in .env")
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
    """Toshkent vaqtini qaytaradi"""
    return datetime.now(TASHKENT_TZ)

def format_tashkent_time(dt=None):
    """Toshkent vaqtini formatlab qaytaradi"""
    if dt is None:
        dt = get_tashkent_time()
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def format_uptime(seconds):
    """Uptime ni formatlab qaytaradi"""
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

# MongoDB connection status
mongo_connected = False
users_col = channels_col = admins_col = messages_col = None

try:
    mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    mongo_client.admin.command('ping')
    mongo_connected = True
    db = mongo_client[MONGO_DB]
    users_col = db['users']
    channels_col = db['channels']
    admins_col = db['admins']
    messages_col = db['messages']
except Exception:
    mongo_connected = False

# folders & legacy files
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
    
    # Load users
    try:
        if mongo_connected and users_col is not None:
            for doc in users_col.find():
                uid = str(doc.get('id') or doc.get('_id'))
                data['users'][uid] = {
                    'id': int(doc.get('id')) if doc.get('id') is not None else int(uid),
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

    # Load channels
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

    # Load admins
    try:
        if mongo_connected and admins_col is not None:
            for doc in admins_col.find():
                aid = doc.get('admin_id')
                if aid is not None:
                    try:
                        data['admins'].append(int(aid))
                    except Exception:
                        pass
        else:
            data['admins'] = safe_load_json(ADMINS_FILE, DEFAULT_DATA['admins'])
    except Exception:
        data['admins'] = safe_load_json(ADMINS_FILE, DEFAULT_DATA['admins'])

    # Load messages
    try:
        if mongo_connected and messages_col is not None:
            for m in messages_col.find().sort('date', -1).limit(100):
                data['messages'].append({
                    'user_id': m.get('user_id'),
                    'message_id': m.get('message_id'),
                    'text': m.get('text'),
                    'date': m.get('date').strftime('%Y-%m-%d %H:%M:%S') if isinstance(m.get('date'), datetime) else m.get('date')
                })
        else:
            data['messages'] = safe_load_json(MESSAGES_FILE, DEFAULT_DATA['messages'])[-100:]
    except Exception:
        data['messages'] = []

    # Asosiy adminni qo'shish
    if MAIN_ADMIN and MAIN_ADMIN not in data['admins']:
        data['admins'].append(MAIN_ADMIN)
        try:
            if mongo_connected and admins_col is not None:
                admins_col.update_one({'admin_id': MAIN_ADMIN}, {'$set': {'admin_id': MAIN_ADMIN}}, upsert=True)
        except Exception:
            pass

    return data

def save_data(data):
    # Users ni saqlash
    try:
        if mongo_connected and users_col is not None:
            for uid, u in data['users'].items():
                users_col.update_one({'id': int(u['id'])}, {'$set': {
                    'id': int(u['id']),
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

    # Channels ni saqlash
    try:
        if mongo_connected and channels_col is not None:
            for key, c in data['channels'].items():
                if c.get('id'):
                    channels_col.update_one({'id': c['id']}, {'$set': {
                        'id': c['id'],
                        'name': c.get('name', key),
                        'added_by': c.get('added_by'),
                        'added_date': c.get('added_date')
                    }}, upsert=True)
                else:
                    channels_col.update_one({'username': c.get('username', key)}, {'$set': {
                        'username': c.get('username', key),
                        'name': c.get('name', key),
                        'added_by': c.get('added_by'),
                        'added_date': c.get('added_date')
                    }}, upsert=True)
        save_json(data['channels'], CHANNELS_FILE)
    except Exception:
        save_json(data['channels'], CHANNELS_FILE)

    # Admins ni saqlash
    try:
        if mongo_connected and admins_col is not None:
            admins_col.delete_many({})
            for a in data['admins']:
                admins_col.insert_one({'admin_id': int(a)})
        save_json(data['admins'], ADMINS_FILE)
    except Exception:
        save_json(data['admins'], ADMINS_FILE)

    # Messages ni saqlash
    try:
        save_json(data.get('messages', [])[-200:], MESSAGES_FILE)
    except Exception:
        pass

def send_message(chat_id, text, reply_markup=None):
    try:
        url = BASE_URL + "sendMessage"
        payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
        if reply_markup:
            payload['reply_markup'] = json.dumps(reply_markup)
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception:
        return False

def forward_message(chat_id, from_chat_id, message_id):
    try:
        url = BASE_URL + "forwardMessage"
        payload = {'chat_id': chat_id, 'from_chat_id': from_chat_id, 'message_id': message_id}
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception:
        return False

def get_updates(offset=None):
    try:
        url = BASE_URL + "getUpdates"
        params = {'timeout': 30}
        if offset is not None:
            params['offset'] = offset
        response = requests.get(url, params=params, timeout=35)
        if response.status_code != 200:
            return []
        return response.json().get('result', [])
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

def user_menu(is_admin: bool = False):
    buttons = ["📢 Bizning kanallar", "💸 Donat", "ℹ️ Yordam"]
    if is_admin:
        buttons.append("🔙 Admin paneli")
    return create_keyboard(buttons)

def admin_menu():
    buttons = ["📊 Statistika", "👥 Userlar ro'yxati", "📣 Hammaga xabar", "👨‍💻 Adminlar", "📢 Kanallar", "🔙 Foydalanuvchi menyusi"]
    return create_keyboard(buttons, row_width=2)

def admins_management_menu():
    buttons = ["➕ Admin qo'shish", "➖ Admin o'chirish", "📋 Adminlar ro'yxati", "🔙 Admin paneli"]
    return create_keyboard(buttons, row_width=2)

def channels_management_menu():
    buttons = ["➕ Kanal qo'shish", "➖ Kanal o'chirish", "📋 Kanallar ro'yxati", "🔙 Admin paneli"]
    return create_keyboard(buttons, row_width=2)

def get_stats(data):
    try:
        if mongo_connected and users_col is not None:
            total_users = users_col.count_documents({})
        else:
            total_users = len(data['users'])
    except Exception:
        total_users = len(data['users'])
    
    try:
        if mongo_connected and messages_col is not None:
            total_messages = messages_col.count_documents({})
        else:
            total_messages = len(data.get('messages', []))
    except Exception:
        total_messages = len(data.get('messages', []))
    
    try:
        if mongo_connected and admins_col is not None:
            total_admins = admins_col.count_documents({})
        else:
            total_admins = len(data['admins'])
    except Exception:
        total_admins = len(data['admins'])
    
    try:
        if mongo_connected and channels_col is not None:
            total_channels = channels_col.count_documents({})
        else:
            total_channels = len(data['channels'])
    except Exception:
        total_channels = len(data['channels'])
    
    # Faol foydalanuvchilar (oxirgi 7 kun ichida faol bo'lganlar)
    try:
        active_users = 0
        one_week_ago = get_tashkent_time() - timedelta(days=7)
        
        if mongo_connected and users_col is not None:
            for user in users_col.find():
                last_active = user.get('last_active', '')
                if last_active:
                    try:
                        if isinstance(last_active, datetime):
                            user_time = last_active.replace(tzinfo=TASHKENT_TZ)
                        else:
                            user_time = datetime.strptime(last_active, '%Y-%m-%d %H:%M:%S').replace(tzinfo=TASHKENT_TZ)
                        
                        if user_time >= one_week_ago:
                            active_users += 1
                    except Exception:
                        continue
        else:
            for user in data['users'].values():
                last_active = user.get('last_active', '')
                if last_active:
                    try:
                        user_time = datetime.strptime(last_active, '%Y-%m-%d %H:%M:%S').replace(tzinfo=TASHKENT_TZ)
                        if user_time >= one_week_ago:
                            active_users += 1
                    except Exception:
                        continue
    except Exception:
        active_users = 0

    # Oxirgi yangilanish vaqti (MongoDB dan)
    last_update_time = "Noma'lum"
    try:
        if mongo_connected and messages_col is not None:
            last_message = messages_col.find_one(sort=[('date', -1)])
            if last_message and last_message.get('date'):
                last_update_time = last_message['date'].strftime('%Y-%m-%d %H:%M:%S')
        elif data.get('messages'):
            last_message = max(data['messages'], key=lambda x: x.get('date', ''))
            last_update_time = last_message.get('date', 'Noma\'lum')
    except Exception:
        pass

    # Bot uptime
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
        # f"📝 <b>Oxirgi yangilanish:</b> {last_update_time}\n"
        # f"💾 <b>Ma'lumotlar manbai:</b> {'MongoDB' if mongo_connected else 'JSON fayllar'}\n"
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
            requests.post(f"{BASE_URL}sendDocument", params=params, files=files, timeout=20)
            
        try:
            os.remove(filename)
        except:
            pass
            
    except Exception:
        send_message(chat_id, "❌ Foydalanuvchilar ro'yxatini yuborishda xatolik yuz berdi!")

def broadcast_message(chat_id, text, data):
    try:
        try:
            if mongo_connected and users_col is not None:
                total = users_col.count_documents({})
            else:
                total = len(data['users'])
        except Exception:
            total = len(data['users'])
        
        send_message(chat_id, f"📣 Xabar {total} foydalanuvchiga yuborilmoqda...")
        success = 0
        failed = 0
        
        for user_id in list(data['users'].keys()):
            try:
                if int(user_id) not in data['admins']:
                    if send_message(int(user_id), text):
                        success += 1
                    else:
                        failed += 1
                    time.sleep(0.05)
            except Exception:
                failed += 1
        
        send_message(chat_id, f"📣 Xabar yuborish yakunlandi!\n\n✅ Muvaffaqiyatli: {success}\n❌ Xatolar: {failed}", reply_markup=admin_menu())
    except Exception:
        send_message(chat_id, "❌ Xabar tarqatishda xatolik yuz berdi!")

# Simple HTTP server for health checks
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'CoderMrx Bot is running!')
        elif self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass

def run_health_server(port):
    try:
        server = HTTPServer(('0.0.0.0', port), HealthHandler)
        server.serve_forever()
    except Exception:
        pass

def load_next_offset():
    try:
        with open(LAST_OFFSET_FILE, 'r') as f:
            v = f.read().strip()
            return int(v) if v else None
    except Exception:
        return None

def save_next_offset(offset):
    try:
        with open(LAST_OFFSET_FILE, 'w') as f:
            f.write(str(int(offset)) if offset is not None else '')
    except Exception:
        pass

def ensure_no_webhook():
    try:
        requests.get(BASE_URL + "deleteWebhook", timeout=5)
    except Exception:
        pass

# Track forwarded messages to avoid duplicates
forwarded_messages = set()

def process_message(update, data):
    try:
        message = update.get('message') or {}
        chat_id = message.get('chat', {}).get('id')
        user_id = message.get('from', {}).get('id')
        text = (message.get('text') or '').strip()
        message_id = message.get('message_id')

        if not user_id:
            return data

        msg_identifier = f"{chat_id}_{message_id}"
        if msg_identifier in forwarded_messages:
            return data
        
        user_id_str = str(user_id)
        
        # User ma'lumotlarini yangilash
        current_time = format_tashkent_time()
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
        try:
            if mongo_connected and messages_col is not None:
                msg_doc = {
                    'user_id': user_id, 
                    'message_id': message_id, 
                    'text': text, 
                    'date': get_tashkent_time(),
                    'chat_id': chat_id
                }
                messages_col.insert_one(msg_doc)
            
            data['messages'].append({
                'user_id': user_id, 
                'message_id': message_id, 
                'text': text, 
                'date': current_time,
                'chat_id': chat_id
            })
        except Exception:
            data['messages'].append({
                'user_id': user_id, 
                'message_id': message_id, 
                'text': text, 
                'date': current_time,
                'chat_id': chat_id
            })

        # restart (admin only)
        if text == "/restart" and user_id in data['admins']:
            send_message(chat_id, "🔄 Bot restart qilinmoqda...")
            save_data(data)
            threading.Timer(1.0, lambda: os._exit(0)).start()
            return data

        # start
        if text == "/start":
            if user_id in data['admins']:
                send_message(chat_id, "👋 Admin paneliga xush kelibsiz!", reply_markup=admin_menu())
            else:
                send_message(chat_id, "👋 Botimizga xush kelibsiz! Savollaringiz bo'lsa yozib qoldiring va biz tez orada siz bilan bog'lanamiz", reply_markup=user_menu())
            save_data(data)
            return data

        if text == "🔙 Foydalanuvchi menyusi":
            send_message(chat_id, "Asosiy menyu:", reply_markup=user_menu(is_admin=(user_id in data['admins'])))
            save_data(data)
            return data

        if text == "🔙 Admin paneli" and user_id in data['admins']:
            user_data = data['users'][user_id_str]
            for key in ['awaiting_broadcast', 'awaiting_channel_add', 'awaiting_admin_add', 'awaiting_admin_remove', 'awaiting_channel_remove']:
                user_data.pop(key, None)
            send_message(chat_id, "Admin paneliga qaytildi:", reply_markup=admin_menu())
            save_data(data)
            return data

        if text == "📢 Bizning kanallar":
            channels = "\n".join([f"📢 {channel.get('name', channel_id)} - @{channel_id}" for channel_id, channel in data['channels'].items()])
            send_message(chat_id, f"📢 Bizning kanallar:\n\n{channels or 'Hozircha kanallar mavjud emas'}")
            return data

        if text == "💸 Donat":
            send_message(chat_id, "💸 Bizni qo'llab-quvvatlang:\n\n🔹 Donat link: https://tirikchilik.uz/codermrx\n")
            return data

        if text == "ℹ️ Yordam":
            send_message(chat_id, "ℹ️ Yordam:\n\nAgar savollaringiz bo'lsa, @codermrxbot ga yozishingiz mumkin.")
            return data

        # Admin actions
        if user_id in data['admins']:
            if text == "📊 Statistika":
                send_message(chat_id, get_stats(data))
                return data
            if text == "👥 Userlar ro'yxati":
                export_users_to_excel(chat_id, data)
                return data
            if text == "📣 Hammaga xabar":
                send_message(chat_id, "📣 Hammaga yuboriladigan xabarni yozing yoki Bekor qilishni bosing:", reply_markup=create_keyboard(["Bekor qilish", "🔙 Admin paneli"]))
                data['users'][user_id_str]['awaiting_broadcast'] = True
                save_data(data)
                return data
            if text == "👨‍💻 Adminlar":
                send_message(chat_id, "Adminlar boshqaruvi:", reply_markup=admins_management_menu())
                return data
            if text == "📢 Kanallar":
                send_message(chat_id, "Kanallar boshqaruvi:", reply_markup=channels_management_menu())
                return data
            if text == "➕ Admin qo'shish":
                send_message(chat_id, "Yangi admin ID sini yuboring:", reply_markup=create_keyboard(["Bekor qilish", "🔙 Admin paneli"]))
                data['users'][user_id_str]['awaiting_admin_add'] = True
                save_data(data)
                return data
            if text == "➖ Admin o'chirish":
                send_message(chat_id, "O'chiriladigan admin ID sini yuboring:", reply_markup=create_keyboard(["Bekor qilish", "🔙 Admin paneli"]))
                data['users'][user_id_str]['awaiting_admin_remove'] = True
                save_data(data)
                return data
            if text == "📋 Adminlar ro'yxati":
                admins_list = "\n".join([f"👤 {data['users'].get(str(a), {}).get('first_name','Nomalum')} (ID: {a})" for a in data['admins']])
                send_message(chat_id, f"Adminlar ro'yxati:\n\n{admins_list}", reply_markup=admin_menu())
                return data
            if text == "➕ Kanal qo'shish":
                send_message(chat_id, "Kanal qo'shish format:\nKanal nomi | username (username @sizsiz) yoki\nKanal nomi | id\nMisol:\nKanalim | mychannel", reply_markup=create_keyboard(["Bekor qilish", "🔙 Admin paneli"]))
                data['users'][user_id_str]['awaiting_channel_add'] = True
                save_data(data)
                return data
            if text == "➖ Kanal o'chirish":
                send_message(chat_id, "O'chiriladigan kanal username yoki id sini yuboring:", reply_markup=create_keyboard(["Bekor qilish", "🔙 Admin paneli"]))
                data['users'][user_id_str]['awaiting_channel_remove'] = True
                save_data(data)
                return data
            if text == "📋 Kanallar ro'yxati":
                channels_list = "\n".join([f"📢 {c.get('name', k)} ({c.get('username', k)})" for k, c in data['channels'].items()])
                send_message(chat_id, f"Kanallar ro'yxati:\n\n{channels_list or 'Kanallar mavjud emas'}", reply_markup=admin_menu())
                return data

            # awaiting handlers
            user_data = data['users'][user_id_str]
            
            if user_data.get('awaiting_broadcast'):
                if text in ("Bekor qilish", "🔙 Admin paneli"):
                    user_data.pop('awaiting_broadcast', None)
                    send_message(chat_id, "Hammaga xabar bekor qilindi.", reply_markup=admin_menu())
                    save_data(data)
                    return data
                user_data.pop('awaiting_broadcast', None)
                broadcast_message(chat_id, text, data)
                save_data(data)
                return data

            if user_data.get('awaiting_admin_add'):
                if text in ("Bekor qilish", "🔙 Admin paneli"):
                    user_data.pop('awaiting_admin_add', None)
                    send_message(chat_id, "Amal bekor qilindi.", reply_markup=admin_menu())
                    save_data(data)
                    return data
                user_data.pop('awaiting_admin_add', None)
                try:
                    new_admin = int(text)
                    if new_admin not in data['admins']:
                        data['admins'].append(new_admin)
                        if mongo_connected and users_col is not None: 
                            users_col.update_one({'id': new_admin}, {'$set': {'is_admin': True}}, upsert=True)
                        if mongo_connected and admins_col is not None: 
                            admins_col.update_one({'admin_id': new_admin}, {'$set': {'admin_id': new_admin}}, upsert=True)
                        send_message(chat_id, f"✅ {new_admin} admin qilindi", reply_markup=admin_menu())
                    else:
                        send_message(chat_id, "⚠️ Bu foydalanuvchi allaqachon admin", reply_markup=admin_menu())
                except ValueError:
                    send_message(chat_id, "❌ Noto'g'ri ID format", reply_markup=admin_menu())
                save_data(data)
                return data

            if user_data.get('awaiting_admin_remove'):
                if text in ("Bekor qilish", "🔙 Admin paneli"):
                    user_data.pop('awaiting_admin_remove', None)
                    send_message(chat_id, "Amal bekor qilindi.", reply_markup=admin_menu())
                    save_data(data)
                    return data
                user_data.pop('awaiting_admin_remove', None)
                try:
                    rem_admin = int(text)
                    if rem_admin in data['admins'] and rem_admin != MAIN_ADMIN:
                        data['admins'].remove(rem_admin)
                        if mongo_connected and users_col is not None: 
                            users_col.update_one({'id': rem_admin}, {'$set': {'is_admin': False}})
                        if mongo_connected and admins_col is not None: 
                            admins_col.delete_one({'admin_id': rem_admin})
                        send_message(chat_id, f"✅ {rem_admin} adminlikdan olindi", reply_markup=admin_menu())
                    else:
                        send_message(chat_id, "❌ Admin topilmadi yoki asosiy adminni o'chirib bo'lmaydi", reply_markup=admin_menu())
                except ValueError:
                    send_message(chat_id, "❌ Noto'g'ri ID format", reply_markup=admin_menu())
                save_data(data)
                return data

            if user_data.get('awaiting_channel_add'):
                if text in ("Bekor qilish", "🔙 Admin paneli"):
                    user_data.pop('awaiting_channel_add', None)
                    send_message(chat_id, "Kanal qo'shish bekor qilindi.", reply_markup=admin_menu())
                    save_data(data)
                    return data
                parts = [p.strip() for p in text.split('|', 1)]
                if len(parts) != 2 or not parts[0] or not parts[1]:
                    send_message(chat_id, "Noto'g'ri format. Iltimos: Kanal nomi | username yoki Kanal nomi | id", reply_markup=create_keyboard(["Bekor qilish", "🔙 Admin paneli"]))
                    return data
                name, ident = parts
                current_time = format_tashkent_time()
                if ident.isdigit():
                    key = ident
                    channel_doc = {'id': int(ident), 'name': name, 'added_by': user_id, 'added_date': current_time}
                    if mongo_connected and channels_col is not None: 
                        channels_col.update_one({'id': channel_doc['id']}, {'$set': channel_doc}, upsert=True)
                else:
                    username = ident.lstrip('@')
                    key = username
                    channel_doc = {'username': username, 'name': name, 'added_by': user_id, 'added_date': current_time}
                    if mongo_connected and channels_col is not None: 
                        channels_col.update_one({'username': username}, {'$set': channel_doc}, upsert=True)
                data['channels'][str(key)] = channel_doc
                user_data.pop('awaiting_channel_add', None)
                send_message(chat_id, f"✅ Kanal qo'shildi: {name} ({ident})", reply_markup=admin_menu())
                save_data(data)
                return data

            if user_data.get('awaiting_channel_remove'):
                if text in ("Bekor qilish", "🔙 Admin paneli"):
                    user_data.pop('awaiting_channel_remove', None)
                    send_message(chat_id, "Amal bekor qilindi.", reply_markup=admin_menu())
                    save_data(data)
                    return data
                user_data.pop('awaiting_channel_remove', None)
                ch_ident = text.strip().lstrip('@')
                removed = False
                if ch_ident.isdigit():
                    for k, v in list(data['channels'].items()):
                        if str(v.get('id', '')) == ch_ident or k == ch_ident:
                            del data['channels'][k]
                            if mongo_connected and channels_col is not None: 
                                channels_col.delete_one({'id': int(ch_ident)})
                            removed = True
                else:
                    if ch_ident in data['channels']:
                        del data['channels'][ch_ident]
                        if mongo_connected and channels_col is not None: 
                            channels_col.delete_one({'username': ch_ident})
                        removed = True
                if removed:
                    send_message(chat_id, f"✅ {ch_ident} o'chirildi", reply_markup=admin_menu())
                else:
                    send_message(chat_id, "❌ Kanal topilmadi", reply_markup=admin_menu())
                save_data(data)
                return data

        # Non-admin xabarlarni adminlarga yuborish
        if user_id not in data['admins'] and text and not text.startswith('/'):
            forwarded_messages.add(msg_identifier)
            if data['admins']:
                admin_id = data['admins'][0]
                if forward_message(admin_id, chat_id, message_id):
                    user_info = data['users'][user_id_str]
                    send_message(admin_id,
                                 f"📨 Yangi xabar!\n👤: {user_info.get('first_name','')} {user_info.get('last_name','')}\n"
                                 f"📱: @{user_info.get('username','noma`lum')}\n🆔: {user_id}\n📝: {text[:200]}")
            
            send_message(chat_id, "✅ Xabaringiz qabul qilindi! Tez orada javob beramiz.")

        save_data(data)
        return data

    except Exception:
        return data

# Keep-alive funksiyasi
def keep_alive_ping():
    keep_alive_url = os.environ.get('KEEP_ALIVE_URL')
    if not keep_alive_url:
        return
    
    try:
        keep_alive_interval = int(os.environ.get('KEEP_ALIVE_INTERVAL', '300'))
    except Exception:
        keep_alive_interval = 300
    
    def ping_loop():
        while True:
            try:
                requests.get(keep_alive_url, timeout=10)
            except Exception:
                pass
            time.sleep(keep_alive_interval)
    
    t = threading.Thread(target=ping_loop, daemon=True)
    t.start()

def main():
    data = load_data()

    ensure_no_webhook()
    next_offset = load_next_offset()

    # Health server
    try:
        port = int(os.environ.get('PORT', '8000'))
    except Exception:
        port = 8000
    t = threading.Thread(target=run_health_server, args=(port,), daemon=True)
    t.start()

    keep_alive_ping()

    print("✅ Bot ishga tushdi...")
    print(f"🕒 Bot ishga tushgan vaqt: {BOT_START_TIME.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"💾 Ma'lumotlar manbai: {'MongoDB' if mongo_connected else 'JSON fayllar'}")
    
    while True:
        try:
            updates = get_updates(next_offset)
            for update in updates:
                uid = update.get('update_id')
                if uid is None:
                    continue
                
                if next_offset is not None and uid < next_offset:
                    continue
                
                data = process_message(update, data)
                next_offset = uid + 1
                save_next_offset(next_offset)
            
            save_data(data)
            time.sleep(0.2)
        except Exception:
            time.sleep(3)

if __name__ == '__main__':
    main()