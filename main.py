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
API_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN") 
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
DB_NAME = "bot_database_uzcoin_pro.db"

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
        cursor.execute('''CREATE TABLE IF NOT EXISTS services 
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT, contact TEXT)''')
        conn.commit()

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

CURRENCY_NAME = get_config("currency_name", "UzCoin")
CURRENCY_SYMBOL = get_config("currency_symbol", "🪙")
UZS_TO_UZC_RATE = float(get_config("uzc_rate", 1000))

STATUS_DATA = {
    0: {"name": "👤 Start", "limit": 30, "bonus_mult": 1.0, "click": 0.01},
    1: {"name": "🥈 Silver", "limit": 100, "bonus_mult": 1.5, "click": 0.05},
    2: {"name": "🥇 Gold", "limit": 1000, "bonus_mult": 2.5, "click": 0.15},
    3: {"name": "💎 Platinum", "limit": 100000, "bonus_mult": 5.0, "click": 0.50}
}

STATUS_PRICES = {
    1: float(get_config("status_price_1", 25.0)),
    2: float(get_config("status_price_2", 75.0)),
    3: float(get_config("status_price_3", 200.0)),
}

# --- FSM STATES ---
class AdminState(StatesGroup):
    broadcast_msg = State()
    add_proj_name = State()
    add_proj_price = State()
    add_proj_desc = State()
    add_proj_media = State()
    add_proj_file = State()
    add_service_name = State()
    add_service_desc = State()
    add_service_contact = State()
    set_price_key = State()
    set_price_value = State()

class WithdrawState(StatesGroup):
    amount = State()
    details = State()

class FillBalance(StatesGroup):
    amount = State()
    receipt = State()

# YANGI: Pul o'tkazish uchun state
class TransferState(StatesGroup):
    user_id = State()
    amount = State()

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

def admin_menu():
    kb = [
        [KeyboardButton(text="💰 Narxlarni Boshqarish"), KeyboardButton(text="📢 Xabar yuborish")],
        [KeyboardButton(text="➕ Loyiha Qo'shish"), KeyboardButton(text="📝 Loyihalarni O'chirish")],
        [KeyboardButton(text="➕ Xizmat Qo'shish"), KeyboardButton(text="❌ Xizmatni O'chirish")],
        [KeyboardButton(text="⬅️ Bosh Menyuga")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# ... (price_management_menu va boshqa keyboardlar avvalgidek qoldi)

# --------------------------------------------------------------------------------
# --- ASOSIY HANDLERLAR ---
# --------------------------------------------------------------------------------

# ... (cancel_handler, back_to_main_menu, cmd_start va boshqalar avvalgidek)

# --- KABINET (YANGILANGAN: Pul o'tkazish tugmasi qo'shildi) ---
@dp.message(F.text == "👤 Kabinet")
async def kabinet(message: types.Message):
    user_id = message.from_user.id
    user = db_query("SELECT balance, status_level, joined_at, referrer_id FROM users WHERE id = ?", (user_id,), fetchone=True)
    if not user:
        await message.answer("Ma'lumotlar topilmadi. /start buyrug'ini bosing.")
        return
    
    ref_count = db_query("SELECT COUNT(id) FROM users WHERE referrer_id = ?", (user_id,), fetchone=True)
    ref_count = ref_count[0] if ref_count else 0
    status_info = STATUS_DATA[user[1]]
    
    ref_text = f"\n🔗 Referal link: `https://t.me/{(await bot.get_me()).username}?start={user_id}`"
    
    text = (f"👤 **Sizning Kabinetingiz**\n\n"
            f"🆔 ID: `{user_id}`\n"
            f"💰 Balans: **{user[0]:.2f} {CURRENCY_SYMBOL}**\n"
            f"📊 Status: **{status_info['name']}**\n"
            f"📈 Bonus koeffitsiyenti: x{status_info['bonus_mult']}\n"
            f"👥 Referallar soni: **{ref_count}**\n"
            f"🗓 A'zo bo'lgan: {user[2][:10]}"
            f"{ref_text}")

    # YANGI: Pul o'tkazish tugmasi
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Pul o'tkazish", callback_data="transfer_start")]
    ])
    
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

# YANGI: Pul o'tkazish handlerlari
@dp.callback_query(F.data == "transfer_start")
async def transfer_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TransferState.user_id)
    await callback.message.answer("📤 Pul o'tkazmoqchi bo'lgan foydalanuvchining **ID** raqamini kiriting:", reply_markup=cancel_kb())
    await callback.answer()

@dp.message(TransferState.user_id)
async def transfer_user_id(message: types.Message, state: FSMContext):
    try:
        target_id = int(message.text)
        if target_id == message.from_user.id:
            await message.answer("❌ O'zingizga pul o'tkaza olmaysiz!")
            return
        if not db_query("SELECT id FROM users WHERE id = ?", (target_id,), fetchone=True):
            await message.answer("❌ Bunday foydalanuvchi topilmadi!")
            return
    except ValueError:
        await message.answer("❌ ID faqat raqamlardan iborat bo'lishi kerak!")
        return
    
    await state.update_data(target_id=target_id)
    balance = db_query("SELECT balance FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)[0]
    await message.answer(f"💸 Qancha miqdorni o'tkazmoqchisiz?\nBalansingiz: {balance:.2f} {CURRENCY_SYMBOL}", reply_markup=cancel_kb())
    await state.set_state(TransferState.amount)

@dp.message(TransferState.amount)
async def transfer_amount(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_id = data['target_id']
    
    try:
        amount = float(message.text)
        if amount <= 0:
            await message.answer("❌ Miqdor musbat bo'lishi kerak!")
            return
    except ValueError:
        await message.answer("❌ Faqat raqam kiriting!")
        return
    
    sender_balance = db_query("SELECT balance FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)[0]
    if sender_balance < amount:
        await message.answer("❌ Balansingizda yetarli mablag' yo'q!")
        return
    
    # Transfer amalga oshiriladi
    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (amount, message.from_user.id), commit=True)
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, target_id), commit=True)
    
    add_transaction(message.from_user.id, -amount, "TRANSFER", f"O'tkazildi: {target_id} ga")
    add_transaction(target_id, amount, "TRANSFER", f"Keldi: {message.from_user.id} dan")
    
    await message.answer(f"✅ {amount:.2f} {CURRENCY_SYMBOL} muvaffaqiyatli {target_id} ga o'tkazildi!", reply_markup=main_menu(message.from_user.id))
    
    try:
        await bot.send_message(target_id, f"💰 Sizga **{message.from_user.id}** dan {amount:.2f} {CURRENCY_SYMBOL} o'tkazildi!")
    except:
        pass
    
    await state.clear()

# ... (qolgan barcha handlerlar avvalgidek qoldi: statuslar, hisob, pul ishlash, g'ildirak, tarix, top, xizmatlar, loyihalar, admin panel va h.k.)

async def main():
    print("Bot ishlamoqda...")
    get_config("uzc_rate", 1000)
    get_config("ref_reward", 1.0)
    get_config("min_withdraw", 10.0)
    get_config("wheel_cost", 2.0)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
