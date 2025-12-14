import os
import logging
import sqlite3
import datetime
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton, 
                           InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, FSInputFile)

# --- KONFIGURATSIYA (Global va Environment) ---
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DB_NAME = os.getenv("DB_NAME", "bot_database_uzcoin_pro.db")

# Karta ma'lumotlari (Environmentdan yoki default)
CARD_UZS = os.getenv("CARD_UZS", "666")
CARD_NAME = os.getenv("CARD_NAME", "Bot Admin")
CARD_VISA = os.getenv("CARD_VISA", "4000 0000 0000 0000")

# Logging
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- BAZA BILAN ISHLASH ---
def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    """SQLite3 baza bilan ishlash uchun yordamchi funksiya."""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if commit: conn.commit()
            if fetchone: return cursor.fetchone()
            if fetchall: return cursor.fetchall()
            return None
    except Exception as e:
        logging.error(f"Bazada xatolik: {e}")
        return None

def init_db():
    """Baza jadvalarini yaratish va migratsiyalarni bajarish."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                          (id INTEGER PRIMARY KEY, 
                           balance REAL DEFAULT 0.0,
                           status_level INTEGER DEFAULT 0,
                           status_expire TEXT,
                           referrer_id INTEGER,
                           joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS config 
                          (key TEXT PRIMARY KEY, value TEXT)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS projects 
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                           name TEXT, 
                           price REAL, 
                           description TEXT,
                           media_id TEXT,
                           media_type TEXT,
                           file_id TEXT)''')
        conn.commit()
    
    # Migratsiyalar (qo'shimcha ustunlar bo'lsa)
    columns = ["description", "media_id", "media_type", "file_id"]
    for col in columns:
        try: db_query(f"ALTER TABLE projects ADD COLUMN {col} TEXT", commit=True)
        except: pass
    
    try: db_query("ALTER TABLE users ADD COLUMN status_level INTEGER DEFAULT 0", commit=True)
    except: pass
    try: db_query("ALTER TABLE users ADD COLUMN referrer_id INTEGER", commit=True)
    except: pass
    try: db_query("ALTER TABLE users ADD COLUMN joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP", commit=True)
    except: pass

init_db()

# --- SOZLAMALARNI BOSHQARISH ---
def get_config(key, default_value=None):
    """Bazadan sozlamani olish. Agar bo'lmasa, default qiymat bilan saqlab qaytarish."""
    res = db_query("SELECT value FROM config WHERE key = ?", (key,), fetchone=True)
    if res: return res[0]
    if default_value is not None:
        db_query("INSERT INTO config (key, value) VALUES (?, ?)", (key, str(default_value)), commit=True)
        return str(default_value)
    return None

def set_config(key, value):
    """Bazaga sozlamani yozish."""
    db_query("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)), commit=True)

# Valyuta nomlarini yuklash
def get_currency_names():
    """Dinamik valyuta nomlarini yuklaydi."""
    global CURRENCY_NAME, CURRENCY_SYMBOL
    CURRENCY_NAME = get_config("currency_name", "UzCoin")
    CURRENCY_SYMBOL = get_config("currency_symbol", "🪙 UZC")
    # Bu funksiya bot ishga tushganda yoki sozlamalar o'zgarganda chaqiriladi
    return CURRENCY_NAME, CURRENCY_SYMBOL

# Birinchi yuklash
CURRENCY_NAME, CURRENCY_SYMBOL = get_currency_names()

# Status darajalari: 0=Start, 1=Silver, 2=Gold, 3=Platinum (Rebranding)
STATUS_DATA = {
    0: {"name": "👤 Start", "limit": 30, "price_month": 0},
    1: {"name": "🥈 Silver", "limit": 100, "desc": "✅ Clicker (Pul ishlash)\n✅ Limit: 100 UZC"},
    2: {"name": "🥇 Gold", "limit": 1000, "desc": "✅ Loyihalar 50% chegirma\n✅ Limit: 1000 UZC"},
    3: {"name": "💎 Platinum", "limit": 100000, "desc": "✅ Hammasi TEKIN (Xizmatlar ham)\n✅ Limit: Cheksiz"}
}

def get_dynamic_prices():
    """Barcha dinamik narxlar va mukofotlarni yuklaydi."""
    return {
        "price_web": float(get_config("price_web", 50.0)),
        "price_apk": float(get_config("price_apk", 100.0)),
        "price_bot": float(get_config("price_bot", 30.0)),
        "ref_reward": float(get_config("ref_reward", 1.0)),
        "click_reward": float(get_config("click_reward", 0.05)),
        # Status narxlari (Oyiga)
        "status_price_1": float(get_config("status_price_1", 20.0)),  # Silver
        "status_price_2": float(get_config("status_price_2", 50.0)), # Gold
        "status_price_3": float(get_config("status_price_3", 200.0)) # Platinum
    }

def get_coin_rates():
    """Valyuta konvertatsiya kurslarini yuklaydi."""
    return {
        "rate_uzs": float(get_config("rate_uzs", 1000.0)), # 1 UZC = 1000 so'm
        "rate_usd": float(get_config("rate_usd", 0.1))
    }

def get_text(key, default):
    """Matn sozlamalarini yuklaydi."""
    return get_config(f"text_{key}", default).replace("\\n", "\n")

def get_user_data(user_id):
    """Foydalanuvchi balans, status va muddati haqida ma'lumotni yuklaydi."""
    res = db_query("SELECT balance, status_level, status_expire FROM users WHERE id = ?", (user_id,), fetchone=True)
    if not res: return None
    
    balance, level, expire = res
    if expire:
        expire_dt = datetime.datetime.strptime(expire, "%Y-%m-%d %H:%M:%S")
        if datetime.datetime.now() > expire_dt:
            db_query("UPDATE users SET status_level = 0, status_expire = NULL WHERE id = ?", (user_id,), commit=True)
            level = 0
            expire = None
    return {"balance": balance, "level": level, "expire": expire}

def format_num(num):
    """Raqamlarni professional formatlash."""
    return f"{float(num):.2f}".rstrip('0').rstrip('.')

# --- STATES ---
class AdminState(StatesGroup):
    edit_balance_id = State()
    edit_balance_amount = State()
    add_proj_name = State()
    add_proj_price = State()
    add_proj_desc = State()
    add_proj_media = State()
    add_proj_file = State()
    change_config_key = State() # Umumiy konfig kalitini saqlash
    change_config_value = State()
    edit_text_key = State()
    edit_text_val = State()
    broadcast_msg = State()

class OrderService(StatesGroup):
    waiting_for_desc = State()

class FillBalance(StatesGroup):
    choosing_currency = State()
    waiting_for_amount = State()
    waiting_for_receipt = State()

class MoneyTransfer(StatesGroup):
    waiting_for_recipient = State()
    waiting_for_amount = State()
    confirm = State()

# --- KEYBOARDS ---
def main_menu(user_id):
    # Professional menyu
    kb = [
        [KeyboardButton(text="👤 Kabinet"), KeyboardButton(text="🌟 Statuslar")],
        [KeyboardButton(text="🛠 Xizmatlar"), KeyboardButton(text="📂 Loyihalar")],
        [KeyboardButton(text="💳 Hisobni to'ldirish"), KeyboardButton(text="💸 Pul ishlash")],
        [KeyboardButton(text="🏆 Top Foydalanuvchilar")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚫 Bekor qilish")]], resize_keyboard=True)

# --------------------------------------------------------------------------------
# --- 🔥 MUHIM FIX: BEKOR QILISH HANDLERI (ENG TEPADA) ---
# --------------------------------------------------------------------------------
@dp.message(F.text == "🚫 Bekor qilish", StateFilter("*"))
async def cancel_all_handler(message: types.Message, state: FSMContext):
    """Har qanday jarayonni bekor qilish."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Bosh menyudasiz.", reply_markup=main_menu(message.from_user.id))
        return

    await state.clear()
    await message.answer("🚫 Jarayon bekor qilindi.", reply_markup=main_menu(message.from_user.id))

# --- START VA REFERAL ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    referrer_id = None
    args = command.args
    
    if args and args.isdigit():
        referrer_id = int(args)
        if referrer_id == message.from_user.id: referrer_id = None
    
    if not db_query("SELECT id FROM users WHERE id = ?", (message.from_user.id,), fetchone=True):
        # Foydalanuvchini bazaga qo'shish
        db_query("INSERT INTO users (id, balance, referrer_id) VALUES (?, 0.0, ?)", 
                 (message.from_user.id, referrer_id), commit=True)
        
        if referrer_id:
            reward = get_dynamic_prices()['ref_reward']
            db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (reward, referrer_id), commit=True)
            try:
                await bot.send_message(referrer_id, f"🎉 Sizda yangi referal! +{format_num(reward)} {CURRENCY_SYMBOL}")
            except: pass

    welcome_text = get_text("welcome", 
                            f"👋 **Assalomu alaykum, {message.from_user.full_name}!**\n\n"
                            f"🤖 **SULTANOV Official Bot**ga xush kelibsiz.\n" # Bot nomi
                            f"Bu yerda siz xizmatlardan foydalanishingiz va {CURRENCY_NAME} ishlashingiz mumkin.")
    
    await message.answer(welcome_text, reply_markup=main_menu(message.from_user.id), parse_mode="Markdown")

# --- KABINET ---
@dp.message(F.text == "👤 Kabinet")
async def kabinet(message: types.Message):
    data = get_user_data(message.from_user.id)
    status_name = STATUS_DATA[data['level']]['name']
    limit = STATUS_DATA[data['level']]['limit']
    
    msg = (f"🆔 Sizning ID: `{message.from_user.id}`\n"
           f"💰 Balans: **{format_num(data['balance'])} {CURRENCY_SYMBOL}**\n"
           f"📊 Status: {status_name}\n"
           f"💳 O'tkazma limiti: {limit} {CURRENCY_SYMBOL}")
    
    if data['expire']:
        msg += f"\n⏳ Tugash vaqti: `{data['expire']}`"
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💸 Do'stga o'tkazish", callback_data="transfer_start")]])
    await message.answer(msg, reply_markup=kb, parse_mode="Markdown")

# --- PUL ISHLASH ---
@dp.message(F.text == "💸 Pul ishlash")
async def earn_money(message: types.Message):
    user = get_user_data(message.from_user.id)
    prices = get_dynamic_prices()
    bot_username = (await bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start={message.from_user.id}"
    
    msg = (f"🔗 **Referal havolangiz:**\n`{ref_link}`\n\n"
           f"👤 Har bir taklif uchun: **{format_num(prices['ref_reward'])} {CURRENCY_SYMBOL}**\n"
           f"ℹ️ Do'stingiz botga kirib start bossa kifoya.")
    
    kb_rows = []
    if user['level'] >= 1:
        msg += f"\n\n🥈 **Silver Clicker** faol!\nHar bosishda: {format_num(prices['click_reward'])} {CURRENCY_SYMBOL}"
        kb_rows.append([InlineKeyboardButton(text=f"👆 {CURRENCY_NAME} ISHLASH", callback_data="clicker_process")])
    else:
        msg += f"\n\n🔒 **Clicker** yopiq. Kamida Silver status oling!"
        kb_rows.append([InlineKeyboardButton(text="🥈 Status sotib olish", callback_data="open_status_shop")])
        
    await message.answer(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="Markdown")

@dp.callback_query(F.data == "clicker_process")
async def process_click(callback: types.CallbackQuery):
    user = get_user_data(callback.from_user.id)
    if user['level'] < 1:
        return await callback.answer("Faqat Silver va yuqori statusdagilar uchun!", show_alert=True)
    
    reward = get_dynamic_prices()['click_reward']
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (reward, callback.from_user.id), commit=True)
    await callback.answer(f"+{format_num(reward)} {CURRENCY_SYMBOL}", cache_time=1)

# --- STATUSLAR DOKONI (O'zgartirishsiz) ---

# --- TOP USERLAR (O'zgartirishsiz) ---

# --- LOYIHALAR (O'zgartirishsiz) ---

# --- XIZMATLAR (O'zgartirishsiz) ---

# --- PUL O'TKAZISH (O'zgartirishsiz) ---

# --- HISOB TO'LDIRISH (O'zgartirishsiz) ---

# --- ADMIN PANEL ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    kb = [
        [InlineKeyboardButton(text="➕ Loyiha Qo'shish", callback_data="adm_add_proj"),
         InlineKeyboardButton(text="💵 Narxlar va Sozlamalar", callback_data="adm_prices")],
        [InlineKeyboardButton(text="🪙 Valyuta Sozlamalari", callback_data="adm_currency_settings"),
         InlineKeyboardButton(text="📢 Broadcast (Xabar)", callback_data="adm_broadcast")]
    ]
    await message.answer("🔐 **Admin Panel v4.0 (Pro)**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# --- Yangi: Valyuta sozlamalari ---
@dp.callback_query(F.data == "adm_currency_settings")
async def adm_currency_settings(callback: types.CallbackQuery):
    cname, csym = get_currency_names()
    kb = [
        [InlineKeyboardButton(text=f"Nom: {cname}", callback_data="set_currency_name")],
        [InlineKeyboardButton(text=f"Belgi: {csym}", callback_data="set_currency_symbol")],
    ]
    await callback.message.edit_text("🪙 **Valyuta nomini sozlash:**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# --- Umumiy Sozlamani O'zgartirish Funksiyalari (Narxlar va Valyuta uchun) ---
@dp.callback_query(F.data.startswith("set_"))
async def adm_set_val_start(callback: types.CallbackQuery, state: FSMContext):
    key = callback.data.replace("set_", "")
    current_value = get_config(key, "Noma'lum")
    
    await state.update_data(conf_key=key)
    
    prompt = f"Yangi qiymatni kiriting (Hozirgi: **{current_value}**):\n"
    if key.startswith("status_price") or key.startswith("price") or key.endswith("reward") or key.startswith("rate"):
        prompt = f"Yangi **raqamli** qiymatni kiriting (Hozirgi: **{current_value}**):"
    
    await callback.message.answer(prompt, reply_markup=cancel_kb(), parse_mode="Markdown")
    await state.set_state(AdminState.change_config_value)

@dp.message(AdminState.change_config_value)
async def adm_save_val(message: types.Message, state: FSMContext):
    data = await state.get_data()
    key = data['conf_key']
    
    val = message.text.strip()
    
    # Raqam tekshiruvi (faqat narxlar va kurslar uchun)
    if key.startswith("status_price") or key.startswith("price") or key.endswith("reward") or key.startswith("rate"):
        try:
            val = float(val)
        except ValueError:
            return await message.answer("⚠️ Iltimos, **faqat to'g'ri raqam** yozing.")

    set_config(key, val)
    
    # Valyuta nomlari o'zgargan bo'lsa, global o'zgaruvchilarni yangilash
    if key in ["currency_name", "currency_symbol"]:
        get_currency_names() 
        await message.answer(f"✅ Saqlandi! Yangi {key.replace('currency_', '')}: **{val}**\nBotning asosiy valyutasi yangilandi.", 
                             reply_markup=main_menu(message.from_user.id), parse_mode="Markdown")
    else:
        await message.answer("✅ Saqlandi!", reply_markup=main_menu(message.from_user.id))
        
    await state.clear()


# --- Broadcast (Xabar tarqatish) - O'zgartirishsiz ---
@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📢 Barcha foydalanuvchilarga yuboriladigan xabarni (rasm/video/matn) yuboring:", reply_markup=cancel_kb())
    await state.set_state(AdminState.broadcast_msg)

@dp.message(AdminState.broadcast_msg)
async def adm_broadcast_send(message: types.Message, state: FSMContext):
    users = db_query("SELECT id FROM users", fetchall=True)
    count = 0
    await message.answer(f"⏳ Xabar {len(users)} ta foydalanuvchiga yuborilmoqda...")
    
    for user_row in users:
        try:
            await message.copy_to(chat_id=user_row[0])
            count += 1
            await asyncio.sleep(0.05)
        except: pass
        
    await message.answer(f"✅ Xabar {count} ta foydalanuvchiga yetib bordi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

# --- Loyiha qo'shish (O'zgartirishsiz) ---
@dp.callback_query(F.data == "adm_add_proj")
async def adm_add_proj_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Loyiha nomini yozing:", reply_markup=cancel_kb())
    await state.set_state(AdminState.add_proj_name)

@dp.message(AdminState.add_proj_name)
async def adm_p_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(f"💰 Narxini kiriting ({CURRENCY_SYMBOL}):")
    await state.set_state(AdminState.add_proj_price)

@dp.message(AdminState.add_proj_price)
async def adm_p_price(message: types.Message, state: FSMContext):
    try:
        val = float(message.text)
    except: return await message.answer("⚠️ Raqam yozing!")
    await state.update_data(price=val)
    await message.answer("📝 Loyiha haqida batafsil ma'lumot (Description):")
    await state.set_state(AdminState.add_proj_desc)

@dp.message(AdminState.add_proj_desc)
async def adm_p_desc(message: types.Message, state: FSMContext):
    await state.update_data(desc=message.text)
    await message.answer("🖼 Rasm yoki Video yuboring (Yoki 'skip' deb yozing):")
    await state.set_state(AdminState.add_proj_media)

@dp.message(AdminState.add_proj_media)
async def adm_p_media(message: types.Message, state: FSMContext):
    mid, mtype = None, None
    if message.photo:
        mid, mtype = message.photo[-1].file_id, "photo"
    elif message.video:
        mid, mtype = message.video.file_id, "video"
    elif message.text and message.text.lower() != 'skip':
        return await message.answer("⚠️ Rasm, video yoki 'skip' yozing.")
        
    await state.update_data(mid=mid, mtype=mtype)
    await message.answer("📁 Endi asosiy faylni (ZIP/RAR/TXT) yuboring:")
    await state.set_state(AdminState.add_proj_file)

@dp.message(AdminState.add_proj_file)
async def adm_p_file(message: types.Message, state: FSMContext):
    if not message.document: return await message.answer("⚠️ Fayl yuborishingiz shart!")
    data = await state.get_data()
    
    db_query("INSERT INTO projects (name, price, description, media_id, media_type, file_id) VALUES (?,?,?,?,?,?)",
             (data['name'], data['price'], data['desc'], data['mid'], data['mtype'], message.document.file_id), commit=True)
    
    await message.answer("✅ Loyiha bazaga qo'shildi!", reply_markup=main_menu(message.from_user.id))
    await state.clear()

# --- Narxlar menyusi (Valyuta sozlamasini chaqirish o'chib qolmasligi uchun alohida) ---
@dp.callback_query(F.data == "adm_prices")
async def adm_prices_list(callback: types.CallbackQuery):
    p = get_dynamic_prices()
    kb = [
        [InlineKeyboardButton(text=f"Ref Bonus ({p['ref_reward']})", callback_data="set_ref_reward"),
         InlineKeyboardButton(text=f"Click ({p['click_reward']})", callback_data="set_click_reward")],
        [InlineKeyboardButton(text=f"Silver ({p['status_price_1']})", callback_data="set_status_price_1"),
         InlineKeyboardButton(text=f"Gold ({p['status_price_2']})", callback_data="set_status_price_2")],
        [InlineKeyboardButton(text=f"Platinum ({p['status_price_3']})", callback_data="set_status_price_3")]
    ]
    await callback.message.edit_text("⚙️ **Narxlarni sozlash:**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# --- Boshqa keraksiz admin funksiyalari olib tashlandi ---

async def main():
    get_currency_names() # Bot ishga tushganda valyutani yangilash
    bot_info = await bot.get_me()
    print(f"Bot nomi: SULTANOV (@{bot_info.username})")
    print(f"Valyuta: {CURRENCY_NAME} ({CURRENCY_SYMBOL})")
    print("Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
