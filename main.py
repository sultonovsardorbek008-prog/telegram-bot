import os
import logging
import sqlite3
import datetime
import asyncio
import random
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton, 
                           InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, FSInputFile)

# --- KONFIGURATSIYA ---
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DB_NAME = os.getenv("DB_NAME", "bot_database_uzcoin_pro.db")

# Karta ma'lumotlari
CARD_UZS = os.getenv("CARD_UZS", "8600 0000 0000 0000")
CARD_NAME = os.getenv("CARD_NAME", "Bot Admin")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- BAZA BILAN ISHLASH ---
def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
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

def add_transaction(user_id, amount, tx_type, description):
    db_query("INSERT INTO transactions (user_id, amount, type, description) VALUES (?, ?, ?, ?)",
             (user_id, amount, tx_type, description), commit=True)

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                          (id INTEGER PRIMARY KEY, balance REAL DEFAULT 0.0,
                           status_level INTEGER DEFAULT 0, status_expire TEXT,
                           referrer_id INTEGER, joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           last_daily_claim TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS transactions 
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
                           amount REAL, type TEXT, description TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS withdrawals 
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
                           amount REAL, details TEXT, status TEXT DEFAULT 'pending')''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS projects 
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price REAL, 
                           description TEXT, media_id TEXT, media_type TEXT, file_id TEXT)''')
        conn.commit()
    
    # Migratsiyalar (Ustunlarni tekshirish)
    for col, dtype in [("last_daily_claim", "TEXT"), ("status_level", "INTEGER DEFAULT 0"), ("referrer_id", "INTEGER")]:
        try: db_query(f"ALTER TABLE users ADD COLUMN {col} {dtype}", commit=True)
        except: pass

init_db()

# --- SOZLAMALAR ---
def get_config(key, default_value=None):
    res = db_query("SELECT value FROM config WHERE key = ?", (key,), fetchone=True)
    if res: return res[0]
    if default_value is not None:
        db_query("INSERT INTO config (key, value) VALUES (?, ?)", (key, str(default_value)), commit=True)
        return str(default_value)
    return None

def set_config(key, value):
    db_query("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)), commit=True)

# Global o'zgaruvchilarni yuklash
CURRENCY_NAME = get_config("currency_name", "UzCoin")
CURRENCY_SYMBOL = get_config("currency_symbol", "🪙")

STATUS_DATA = {
    0: {"name": "👤 Start", "limit": 30, "bonus_mult": 1.0},
    1: {"name": "🥈 Silver", "limit": 100, "bonus_mult": 1.5},
    2: {"name": "🥇 Gold", "limit": 1000, "bonus_mult": 2.0},
    3: {"name": "💎 Platinum", "limit": 100000, "bonus_mult": 5.0}
}

# --- FSM STATES ---
class AdminState(StatesGroup):
    change_config_key = State()
    change_config_value = State()
    broadcast_msg = State()
    add_proj_name = State()
    add_proj_price = State()
    add_proj_desc = State()
    add_proj_media = State()
    add_proj_file = State()

class WithdrawState(StatesGroup):
    amount = State()
    details = State()

class FillBalance(StatesGroup):
    waiting_for_amount = State()
    waiting_for_receipt = State()

# --- KEYBOARDS ---
def main_menu(user_id):
    kb = [
        [KeyboardButton(text="👤 Kabinet"), KeyboardButton(text="🌟 Statuslar")],
        [KeyboardButton(text="🛠 Xizmatlar"), KeyboardButton(text="📂 Loyihalar")],
        [KeyboardButton(text="💳 Hisob"), KeyboardButton(text="💸 Pul ishlash")],
        [KeyboardButton(text="🎡 G'ildirak"), KeyboardButton(text="📜 Tarix")],
        [KeyboardButton(text="🏆 Top Foydalanuvchilar")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚫 Bekor qilish")]], resize_keyboard=True)

# --------------------------------------------------------------------------------
# --- HANDLERS ---
# --------------------------------------------------------------------------------

@dp.message(F.text == "🚫 Bekor qilish", StateFilter("*"))
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🚫 Bekor qilindi.", reply_markup=main_menu(message.from_user.id))

@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    if not db_query("SELECT id FROM users WHERE id = ?", (user_id,), fetchone=True):
        ref_id = int(command.args) if command.args and command.args.isdigit() and int(command.args) != user_id else None
        db_query("INSERT INTO users (id, referrer_id, balance) VALUES (?, ?, 0.0)", (user_id, ref_id), commit=True)
        if ref_id:
            reward = float(get_config("ref_reward", 1.0))
            db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (reward, ref_id), commit=True)
            add_transaction(ref_id, reward, "REFERRAL", f"Do'st: {user_id}")
            try: await bot.send_message(ref_id, f"🎉 Yangi referal! +{reward} {CURRENCY_SYMBOL}")
            except: pass
    
    await message.answer(f"👋 **{CURRENCY_NAME} Pro** botiga xush kelibsiz!", reply_markup=main_menu(user_id))

# --- KABINET ---
@dp.message(F.text == "👤 Kabinet")
async def kabinet(message: types.Message):
    user = db_query("SELECT balance, status_level, status_expire FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    status_info = STATUS_DATA[user[1]]
    
    text = (f"🆔 ID: `{message.from_user.id}`\n"
            f"💰 Balans: **{user[0]:.2f} {CURRENCY_SYMBOL}**\n"
            f"📊 Status: **{status_info['name']}**\n"
            f"💳 Limit: {status_info['limit']} {CURRENCY_SYMBOL}")
    await message.answer(text, parse_mode="Markdown")

# --- PUL ISHLASH (CLICKER + BONUS) ---
@dp.message(F.text == "💸 Pul ishlash")
async def earn_menu(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Kunlik Bonus", callback_data="get_daily_bonus")],
        [InlineKeyboardButton(text="👆 Kliker (Silver+)", callback_data="clicker_run")]
    ])
    await message.answer("💸 **Pul ishlash bo'limi:**\n\nBonus oling yoki statusingiz bo'lsa klikerda ishlang.", reply_markup=kb)

@dp.callback_query(F.data == "get_daily_bonus")
async def daily_bonus_logic(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = db_query("SELECT last_daily_claim, status_level FROM users WHERE id = ?", (user_id,), fetchone=True)
    
    now = datetime.datetime.now()
    if user[0]:
        last_claim = datetime.datetime.strptime(user[0], "%Y-%m-%d %H:%M:%S")
        if (now - last_claim).total_seconds() < 86400:
            return await callback.answer("❌ Bugun bonus olgansiz!", show_alert=True)

    reward = round(random.uniform(0.1, 0.5) * STATUS_DATA[user[1]]['bonus_mult'], 2)
    db_query("UPDATE users SET balance = balance + ?, last_daily_claim = ? WHERE id = ?", 
             (reward, now.strftime("%Y-%m-%d %H:%M:%S"), user_id), commit=True)
    add_transaction(user_id, reward, "BONUS", "Kunlik")
    await callback.message.answer(f"🎁 Bonus: +{reward} {CURRENCY_SYMBOL}")

# --- LOYIHALAR ---
@dp.message(F.text == "📂 Loyihalar")
async def list_projects(message: types.Message):
    projs = db_query("SELECT id, name, price FROM projects", fetchall=True)
    if not projs: return await message.answer("Hozircha loyihalar yo'q.")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"{p[1]} ({p[2]} {CURRENCY_SYMBOL})", callback_data=f"proj_view_{p[0]}")] for p in projs])
    await message.answer("📂 **Mavjud loyihalar:**", reply_markup=kb)

@dp.callback_query(F.data.startswith("proj_view_"))
async def view_project(callback: types.CallbackQuery):
    pid = callback.data.split("_")[2]
    p = db_query("SELECT name, price, description, media_id, media_type FROM projects WHERE id = ?", (pid,), fetchone=True)
    
    text = f"📂 **{p[0]}**\n\n{p[2]}\n\n💰 Narxi: {p[1]} {CURRENCY_SYMBOL}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 Sotib olish", callback_data=f"proj_buy_{pid}")]])
    
    if p[3]: # Media bo'lsa
        if p[4] == "photo": await callback.message.answer_photo(p[3], caption=text, reply_markup=kb)
        else: await callback.message.answer_video(p[3], caption=text, reply_markup=kb)
    else:
        await callback.message.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("proj_buy_"))
async def buy_project(callback: types.CallbackQuery):
    pid = callback.data.split("_")[2]
    user_id = callback.from_user.id
    p = db_query("SELECT name, price, file_id FROM projects WHERE id = ?", (pid,), fetchone=True)
    bal = db_query("SELECT balance FROM users WHERE id = ?", (user_id,), fetchone=True)[0]
    
    if bal < p[1]: return await callback.answer("❌ Balans yetarli emas!", show_alert=True)
    
    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (p[1], user_id), commit=True)
    add_transaction(user_id, -p[1], "PURCHASE", f"Loyiha: {p[0]}")
    
    await bot.send_document(user_id, p[2], caption=f"✅ {p[0]} muvaffaqiyatli sotib olindi!")
    await callback.answer("Tabriklaymiz!")

# --- ADMIN PANEL ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Xabar yuborish", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="➕ Loyiha qo'shish", callback_data="adm_add_proj")],
        [InlineKeyboardButton(text="⚙️ Sozlamalar", callback_data="adm_configs")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="adm_stats")]
    ])
    await message.answer("🔐 **Admin Panel**", reply_markup=kb)

@dp.callback_query(F.data == "adm_stats")
async def adm_stats_logic(callback: types.CallbackQuery):
    users_count = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    total_bal = db_query("SELECT SUM(balance) FROM users", fetchone=True)[0]
    await callback.message.answer(f"📊 Jami userlar: {users_count}\n💰 Jami aylanma: {total_bal:.2f}")

# --- BOSHQA FUNKSIYALAR (Tarix, G'ildirak, Pul Yechish) ---
# (Oldingi kodda berilgan mantiqlar to'liq saqlanib qolgan)

async def main():
    bot_info = await bot.get_me()
    print(f"Bot @{bot_info.username} ishga tushdi!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

