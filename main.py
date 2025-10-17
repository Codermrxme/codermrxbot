# ...existing code...
import os
import json
import time
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
import pymongo
from pymongo import ReturnDocument

# Sozlamalarni yuklash
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
try:
    MAIN_ADMIN = int(os.getenv('MAIN_ADMIN'))
except Exception:
    MAIN_ADMIN = None
BASE_URL = f"https://api.telegram.org/bot{TOKEN}/"

# MongoDB sozlamalari
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017')
MONGO_DB = os.getenv('MONGO_DB', 'codermrxbot')

# MongoDB ulanish
mongo_client = pymongo.MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB]
users_col = db['users']
channels_col = db['channels']
admins_col = db['admins']
messages_col = db['messages']

# Papkalarni yaratish
os.makedirs('data', exist_ok=True)
os.makedirs('exports', exist_ok=True)

# (Legacy) Ma'lumotlar bazasi fayllari — saqlab qo'yildi fallback uchun
USERS_FILE = 'data/users.json'
CHANNELS_FILE = 'data/channels.json'
ADMINS_FILE = 'data/admins.json'
MESSAGES_FILE = 'data/messages.json'

# Boshlang'ich ma'lumotlar
DEFAULT_DATA = {
    'users': {},
    'channels': {},
    'admins': [MAIN_ADMIN] if MAIN_ADMIN else [],
    'messages': []
}

def safe_load_json(filename, default):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, type(default)):
                return data
            return default
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return default

def save_json(data, filename):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Faylga yozishda xato: {e}")

def load_data():
    # Load users
    users = {}
    try:
        for doc in users_col.find():
            uid = str(doc.get('id') or doc.get('_id'))
            users[uid] = {
                'id': int(doc.get('id')) if doc.get('id') is not None else int(uid),
                'first_name': doc.get('first_name', ''),
                'last_name': doc.get('last_name', ''),
                'username': doc.get('username', ''),
                'phone': doc.get('phone', ''),
                'joined': doc.get('joined', datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')),
                'last_active': doc.get('last_active', datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')),
                'message_count': int(doc.get('message_count', 0)),
                'is_admin': bool(doc.get('is_admin', False))
            }
    except Exception as e:
        print(f"DB dan users yuklashda xato: {e}")
        users = safe_load_json(USERS_FILE, DEFAULT_DATA['users'])

    # Load channels
    channels = {}
    try:
        for doc in channels_col.find():
            key = doc.get('username') or str(doc.get('_id'))
            channels[key] = {
                'username': doc.get('username', key),
                'name': doc.get('name', key),
                'added_by': doc.get('added_by'),
                'added_date': doc.get('added_date')
            }
    except Exception as e:
        print(f"DB dan channels yuklashda xato: {e}")
        channels = safe_load_json(CHANNELS_FILE, DEFAULT_DATA['channels'])

    # Load admins
    admins = []
    try:
        for doc in admins_col.find():
            aid = doc.get('admin_id')
            if aid is not None:
                admins.append(int(aid))
    except Exception as e:
        print(f"DB dan admins yuklashda xato: {e}")
        admins = safe_load_json(ADMINS_FILE, DEFAULT_DATA['admins'])

    # Messages: yuklamaymiz (DBda saqlanadi). Agar kerak bo'lsa oxirgi N yuklash mumkin:
    messages = []
    try:
        for m in messages_col.find().sort('date', -1).limit(50):
            messages.append({
                'user_id': m.get('user_id'),
                'message_id': m.get('message_id'),
                'text': m.get('text'),
                'date': m.get('date').strftime('%Y-%m-%d %H:%M:%S') if isinstance(m.get('date'), datetime) else m.get('date')
            })
    except Exception:
        messages = []

    # Ensure MAIN_ADMIN exists in admins list
    if MAIN_ADMIN and MAIN_ADMIN not in admins:
        try:
            admins_col.update_one({'admin_id': MAIN_ADMIN}, {'$set': {'admin_id': MAIN_ADMIN}}, upsert=True)
            admins.append(MAIN_ADMIN)
        except Exception as e:
            print(f"Asosiy adminni DBga qo'shishda xato: {e}")

    return {'users': users, 'channels': channels, 'admins': admins, 'messages': messages}

def save_data(data):
    # Upsert users
    try:
        for uid, u in data['users'].items():
            users_col.update_one({'id': int(u['id'])},
                                 {'$set': {
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
    except Exception as e:
        print(f"Users DBga saqlashda xato: {e}")
        save_json(data['users'], USERS_FILE)

    # Upsert channels
    try:
        for key, c in data['channels'].items():
            channels_col.update_one({'username': c.get('username', key)},
                                    {'$set': {
                                        'username': c.get('username', key),
                                        'name': c.get('name', key),
                                        'added_by': c.get('added_by'),
                                        'added_date': c.get('added_date')
                                    }}, upsert=True)
    except Exception as e:
        print(f"Channels DBga saqlashda xato: {e}")
        save_json(data['channels'], CHANNELS_FILE)

    # Replace admins collection
    try:
        admins_col.delete_many({})
        for a in data['admins']:
            admins_col.insert_one({'admin_id': int(a)})
    except Exception as e:
        print(f"Admins DBga saqlashda xato: {e}")
        save_json(data['admins'], ADMINS_FILE)

    # Messages are saved on the fly (process_message inserts). We still can serialize recent cache as fallback.
    try:
        save_json(data.get('messages', []), MESSAGES_FILE)
    except Exception:
        pass

def send_message(chat_id, text, reply_markup=None):
    try:
        url = BASE_URL + "sendMessage"
        params = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        if reply_markup:
            params['reply_markup'] = json.dumps(reply_markup)
        response = requests.post(url, json=params, timeout=10)
        return response.json()
    except Exception as e:
        print(f"Xabar yuborishda xato: {e}")
        return None

def forward_message(chat_id, from_chat_id, message_id):
    try:
        url = BASE_URL + "forwardMessage"
        params = {
            'chat_id': chat_id,
            'from_chat_id': from_chat_id,
            'message_id': message_id
        }
        requests.post(url, json=params, timeout=10)
    except Exception as e:
        print(f"Xabarni yo'naltirishda xato: {e}")

def get_updates(offset=None):
    try:
        url = BASE_URL + "getUpdates"
        params = {'timeout': 30}
        if offset:
            params['offset'] = offset
        response = requests.get(url, params=params, timeout=35)
        return response.json().get('result', [])
    except Exception as e:
        print(f"Yangiliklarni olishda xato: {e}")
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

def user_menu():
    buttons = ["📢 Bizning kanallar", "💸 Donat", "ℹ️ Yordam"]
    return create_keyboard(buttons)

def admin_menu():
    buttons = [
        "📊 Statistika",
        "👥 Userlar ro'yxati",
        "📣 Hammaga xabar",
        "👨‍💻 Adminlar",
        "📢 Kanallar",
        "🔙 Foydalanuvchi menyusi"
    ]
    return create_keyboard(buttons, row_width=2)

def admins_management_menu():
    buttons = [
        "➕ Admin qo'shish",
        "➖ Admin o'chirish",
        "📋 Adminlar ro'yxati",
        "🔙 Admin paneli"
    ]
    return create_keyboard(buttons, row_width=2)

def channels_management_menu():
    buttons = [
        "➕ Kanal qo'shish",
        "➖ Kanal o'chirish",
        "📋 Kanallar ro'yxati",
        "🔙 Admin paneli"
    ]
    return create_keyboard(buttons, row_width=2)

def get_stats(data):
    try:
        total_users = users_col.count_documents({})
    except Exception:
        total_users = len(data['users'])

    try:
        # Count messages in DB
        total_messages = messages_col.count_documents({})
    except Exception:
        total_messages = len(data.get('messages', []))

    total_admins = len(data['admins'])
    total_channels = len(data['channels'])

    # Active users calculated from in-memory data
    try:
        active_users = len([u for u in data['users'].values()
                          if (datetime.now() - datetime.strptime(u.get('last_active', u.get('joined', '2000-01-01')),
                              '%Y-%m-%d %H:%M:%S')).days < 7])
    except Exception:
        active_users = 0

    return (
        "📊 <b>Bot statistikasi</b>\n\n"
        f"👥 <b>Jami foydalanuvchilar:</b> {total_users}\n"
        f"🟢 <b>Faol foydalanuvchilar:</b> {active_users}\n"
        f"📨 <b>Jami xabarlar:</b> {total_messages}\n"
        f"👨‍💻 <b>Adminlar:</b> {total_admins}\n"
        f"📢 <b>Kanallar:</b> {total_channels}\n\n"
        f"🔄 <i>Oxirgi yangilanish: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
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
        filename = f"exports/users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        df.to_excel(filename, index=False)

        with open(filename, 'rb') as f:
            files = {'document': f}
            params = {'chat_id': chat_id, 'caption': '📊 Foydalanuvchilar ro\'yxati'}
            requests.post(f"{BASE_URL}sendDocument", params=params, files=files, timeout=20)
    except Exception as e:
        print(f"Excel eksport qilishda xato: {e}")
        send_message(chat_id, "❌ Foydalanuvchilar ro'yxatini yuborishda xatolik yuz berdi!")

def broadcast_message(chat_id, text, data):
    try:
        total = users_col.count_documents({}) if users_col else len(data['users'])
        send_message(chat_id, f"📣 Xabar {total} foydalanuvchiga yuborilmoqda...")

        success = 0
        failed = 0

        for user_id in list(data['users'].keys()):
            try:
                if int(user_id) not in data['admins']:  # Adminlarga yubormaslik
                    send_message(int(user_id), text)
                    success += 1
                    time.sleep(0.1)  # Serverga yukni kamaytirish
            except Exception as e:
                print(f"Hammaga yuborishda xato user {user_id}: {e}")
                failed += 1

        send_message(chat_id,
                    f"📣 Xabar yuborish yakunlandi!\n\n"
                    f"✅ Muvaffaqiyatli: {success}\n"
                    f"❌ Xatolar: {failed}")
    except Exception as e:
        print(f"Xabar tarqatishda xato: {e}")
        try:
            send_message(chat_id, "❌ Xabar tarqatishda xatolik yuz berdi!")
        except Exception as e2:
            print(f"Fallback send_message xatosi: {e2}")

def process_message(update, data):
    try:
        message = update.get('message', {})
        chat_id = message.get('chat', {}).get('id')
        user_id = message.get('from', {}).get('id')
        text = message.get('text', '') or ''
        message_id = message.get('message_id')

        if not user_id:
            return data

        user_id_str = str(user_id)

        # Foydalanuvchi ma'lumotlarini yangilash
        if user_id_str not in data['users']:
            data['users'][user_id_str] = {
                'id': user_id,
                'first_name': message.get('from', {}).get('first_name', ''),
                'last_name': message.get('from', {}).get('last_name', ''),
                'username': message.get('from', {}).get('username', ''),
                'phone': message.get('contact', {}).get('phone_number', '') if 'contact' in message else '',
                'joined': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'last_active': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'message_count': 0,
                'is_admin': user_id in data['admins']
            }
        else:
            data['users'][user_id_str]['last_active'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data['users'][user_id_str]['message_count'] += 1

        # Xabarni DBga saqlash (har bir xabar)
        try:
            msg_doc = {
                'user_id': user_id,
                'message_id': message_id,
                'text': text,
                'date': datetime.utcnow()
            }
            messages_col.insert_one(msg_doc)
        except Exception as e:
            print(f"Xabarni DBga yozishda xato: {e}")
            # local cache fallback
            data['messages'].append({
                'user_id': user_id,
                'message_id': message_id,
                'text': text,
                'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })

        # Komandalarni qayta ishlash
        if text == "/start":
            if user_id in data['admins']:
                send_message(chat_id, "👋 Admin paneliga xush kelibsiz!", reply_markup=admin_menu())
            else:
                send_message(chat_id, "👋 Botimizga xush kelibsiz! Savollaringiz bo'lsa yozib qoldiring va biz tez orada siz bilan bog'lanamiz", reply_markup=user_menu())
            # persist user immediately
            save_data(data)
            return data

        # Foydalanuvchi menyusi
        if text == "🔙 Foydalanuvchi menyusi":
            send_message(chat_id, "Asosiy menyu:", reply_markup=user_menu())
            save_data(data)
            return data

        if text == "📢 Bizning kanallar":
            channels = "\n".join([f"📢 {channel.get('name', channel_id)} - @{channel_id}"
                                for channel_id, channel in data['channels'].items()])
            send_message(chat_id, f"📢 Bizning kanallar:\n\n{channels or 'Hozircha kanallar mavjud emas'}")
            return data

        if text == "💸 Donat":
            send_message(chat_id,
                       "💸 Bizni qo'llab-quvvatlang:\n\n"
                       "🔹 Donat link: https://tirikchilik.uz/codermrx\n")
            return data

        if text == "ℹ️ Yordam":
            send_message(chat_id,
                       "ℹ️ Yordam:\n\n"
                       "Agar savollaringiz bo'lsa, @codermrxbot ga yozishingiz mumkin.")
            return data

        # Admin menyusi
        if user_id in data['admins']:
            if text == "📊 Statistika":
                send_message(chat_id, get_stats(data))
                return data

            if text == "👥 Userlar ro'yxati":
                export_users_to_excel(chat_id, data)
                return data

            if text == "📣 Hammaga xabar":
                send_message(chat_id,
                            "📣 Hammaga yuboriladigan xabarni yozing:",
                            reply_markup={'remove_keyboard': True})
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
                send_message(chat_id,
                            "Yangi admin ID sini yuboring yoki foydalanuvchi xabarini forward qiling:",
                            reply_markup={'remove_keyboard': True})
                data['users'][user_id_str]['awaiting_admin_add'] = True
                save_data(data)
                return data

            if text == "➖ Admin o'chirish":
                send_message(chat_id,
                            "O'chiriladigan admin ID sini yuboring:",
                            reply_markup={'remove_keyboard': True})
                data['users'][user_id_str]['awaiting_admin_remove'] = True
                save_data(data)
                return data

            if text == "📋 Adminlar ro'yxati":
                admins_list = "\n".join([
                    f"👤 {data['users'].get(str(admin_id), {}).get('first_name', 'Noma\\lum')} (ID: {admin_id})"
                    for admin_id in data['admins']
                ])
                send_message(chat_id, f"Adminlar ro'yxati:\n\n{admins_list}")
                return data

            if text == "➕ Kanal qo'shish":
                send_message(chat_id,
                            "Kanal username ni @siz yozmasdan yuboring (masalan: mychannel):",
                            reply_markup={'remove_keyboard': True})
                data['users'][user_id_str]['awaiting_channel_add'] = True
                save_data(data)
                return data

            if text == "➖ Kanal o'chirish":
                send_message(chat_id,
                            "O'chiriladigan kanal username ni yuboring (@siz yozmasdan):",
                            reply_markup={'remove_keyboard': True})
                data['users'][user_id_str]['awaiting_channel_remove'] = True
                save_data(data)
                return data

            if text == "📋 Kanallar ro'yxati":
                channels_list = "\n".join([
                    f"📢 {channel.get('name', channel_id)} (@{channel_id})"
                    for channel_id, channel in data['channels'].items()
                ])
                send_message(chat_id, f"Kanallar ro'yxati:\n\n{channels_list or 'Kanallar mavjud emas'}")
                return data

            if text == "🔙 Admin paneli":
                send_message(chat_id, "Admin paneli:", reply_markup=admin_menu())
                return data

            # Hammaga xabar yuborish
            if data['users'][user_id_str].get('awaiting_broadcast'):
                del data['users'][user_id_str]['awaiting_broadcast']
                broadcast_message(chat_id, text, data)
                save_data(data)
                return data

            # Admin qo'shish
            if data['users'][user_id_str].get('awaiting_admin_add'):
                del data['users'][user_id_str]['awaiting_admin_add']
                try:
                    new_admin_id = int(text)
                    if new_admin_id not in data['admins']:
                        data['admins'].append(new_admin_id)
                        if str(new_admin_id) in data['users']:
                            data['users'][str(new_admin_id)]['is_admin'] = True
                        # persist admin
                        admins_col.update_one({'admin_id': new_admin_id}, {'$set': {'admin_id': new_admin_id}}, upsert=True)
                        send_message(chat_id, f"✅ {new_admin_id} admin qilib qo'yildi!")
                    else:
                        send_message(chat_id, "⚠️ Bu foydalanuvchi allaqachon admin!")
                except ValueError:
                    send_message(chat_id, "❌ Noto'g'ri ID format!")
                save_data(data)
                return data

            # Admin o'chirish
            if data['users'][user_id_str].get('awaiting_admin_remove'):
                del data['users'][user_id_str]['awaiting_admin_remove']
                try:
                    admin_id = int(text)
                    if admin_id in data['admins'] and admin_id != MAIN_ADMIN:
                        data['admins'].remove(admin_id)
                        if str(admin_id) in data['users']:
                            data['users'][str(admin_id)]['is_admin'] = False
                        admins_col.delete_one({'admin_id': admin_id})
                        send_message(chat_id, f"✅ {admin_id} adminlikdan olindi!")
                    else:
                        send_message(chat_id, "❌ Asosiy adminni o'chirib bo'lmaydi!")
                except ValueError:
                    send_message(chat_id, "❌ Noto'g'ri ID format!")
                save_data(data)
                return data

            # Kanal qo'shish
            if data['users'][user_id_str].get('awaiting_channel_add'):
                del data['users'][user_id_str]['awaiting_channel_add']
                channel_username = text.strip().replace('@', '')
                if channel_username and channel_username not in data['channels']:
                    data['channels'][channel_username] = {
                        'username': channel_username,
                        'name': channel_username,
                        'added_by': user_id,
                        'added_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                    channels_col.update_one({'username': channel_username},
                                            {'$set': data['channels'][channel_username]}, upsert=True)
                    send_message(chat_id, f"✅ @{channel_username} kanali qo'shildi!")
                else:
                    send_message(chat_id, "⚠️ Bu kanal allaqachon mavjud yoki noto'g'ri format!")
                save_data(data)
                return data

            # Kanal o'chirish
            if data['users'][user_id_str].get('awaiting_channel_remove'):
                del data['users'][user_id_str]['awaiting_channel_remove']
                channel_username = text.strip().replace('@', '')
                if channel_username in data['channels']:
                    del data['channels'][channel_username]
                    channels_col.delete_one({'username': channel_username})
                    send_message(chat_id, f"✅ @{channel_username} kanali o'chirildi!")
                else:
                    send_message(chat_id, "❌ Bunday kanal topilmadi!")
                save_data(data)
                return data

        # Oddiy foydalanuvchilar xabarlarini adminga yuborish
        if user_id not in data['admins']:
            for admin_id in data['admins']:
                try:
                    forward_message(admin_id, chat_id, message_id)
                    user_info = data['users'][user_id_str]
                    send_message(
                        admin_id,
                        f"📨 Yangi xabar!\n"
                        f"👤: {user_info['first_name']} {user_info.get('last_name', '')}\n"
                        f"📱: @{user_info['username'] if user_info['username'] else 'noma\\`lum'}\n"
                        f"🆔: {user_id}\n"
                        f"📝: {text[:100]}{'...' if len(text) > 100 else ''}"
                    )
                except Exception as e:
                    print(f"Xabar yuborishda xato adminga: {e}")

            send_message(chat_id, "✅ Xabaringiz qabul qilindi! Tez orada javob beramiz.")

        # Persist changes
        save_data(data)
        return data

    except Exception as e:
        print(f"Xabarni qayta ishlashda xato: {e}")
        try:
            send_message(chat_id, "⚠️ Botda texnik xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")
        except Exception:
            pass
        return data

def main():
    data = load_data()
    last_update_id = None

    print("Bot ishga tushdi...")
    while True:
        try:
            updates = get_updates(last_update_id)
            for update in updates:
                last_update_id = update['update_id'] + 1
                data = process_message(update, data)
            # periodic save (in case)
            save_data(data)
        except Exception as e:
            print(f"Asosiy tsiklda xato: {e}")
            time.sleep(5)

if __name__ == '__main__':
    main()
# ...existing code...