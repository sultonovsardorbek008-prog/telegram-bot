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
DB_NAME = "bot_database_uzcoin_pro.db"

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

init_db()

# --- SOZLAMALAR ---
def get_config(key, default_value=None):
    res = db_query("SELECT value FROM config WHERE key = ?", (key,), fetchone=True)
    if res: return res[0]
    if default_value is not None:
        db_query("INSERT INTO config (key, value) VALUES (?, ?)", (key, str(default_value)), commit=True)
        return str(default_value)
    return None

CURRENCY_NAME = get_config("currency_name", "UzCoin")
CURRENCY_SYMBOL = get_config("currency_symbol", "🪙")

STATUS_DATA = {
    0: {"name": "👤 Start", "limit": 30, "bonus_mult": 1.0, "click": 0.01},
    1: {"name": "🥈 Silver", "limit": 100, "bonus_mult": 1.5, "click": 0.05},
    2: {"name": "🥇 Gold", "limit": 1000, "bonus_mult": 2.5, "click": 0.15},
    3: {"name": "💎 Platinum", "limit": 100000, "bonus_mult": 5.0, "click": 0.50}
}

# --- FSM STATES ---
class AdminState(StatesGroup):
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
    amount = State()
    receipt = State()

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
# --- ASOSIY HANDLERLAR ---
# --------------------------------------------------------------------------------

@dp.message(F.text == "🚫 Bekor qilish", StateFilter("*"))
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🚫 Jarayon bekor qilindi.", reply_markup=main_menu(message.from_user.id))

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
    
    await message.answer(f"👋 **{CURRENCY_NAME} Pro** botiga xush kelibsiz!\nBu yerda siz pul ishlab, loyihalarni sotib olishingiz mumkin.", reply_markup=main_menu(user_id))

# --- KABINET ---
@dp.message(F.text == "👤 Kabinet")
async def kabinet(message: types.Message):
    user = db_query("SELECT balance, status_level FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    status_info = STATUS_DATA[user[1]]
    text = (f"👤 **Sizning Kabinetingiz**\n\n"
            f"🆔 ID: `{message.from_user.id}`\n"
            f"💰 Balans: **{user[0]:.2f} {CURRENCY_SYMBOL}**\n"
            f"📊 Status: **{status_info['name']}**\n"
            f"📈 Bonus koeffitsiyenti: x{status_info['bonus_mult']}")
    await message.answer(text, parse_mode="Markdown")

# --- STATUSLAR ---
@dp.message(F.text == "🌟 Statuslar")
async def status_menu(message: types.Message):
    s1 = float(get_config("status_price_1", 25.0))
    s2 = float(get_config("status_price_2", 75.0))
    s3 = float(get_config("status_price_3", 200.0))
    
    msg = (f"🌟 **Status Darajalari**\n\n"
           f"🥈 **Silver** - {s1} {CURRENCY_SYMBOL}\n(Klik: 0.05, Bonus: x1.5)\n\n"
           f"🥇 **Gold** - {s2} {CURRENCY_SYMBOL}\n(Klik: 0.15, Bonus: x2.5)\n\n"
           f"💎 **Platinum** - {s3} {CURRENCY_SYMBOL}\n(Klik: 0.50, Bonus: x5.0)")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🥈 Silver", callback_data="buy_status_1"),
         InlineKeyboardButton(text="🥇 Gold", callback_data="buy_status_2")],
        [InlineKeyboardButton(text="💎 Platinum", callback_data="buy_status_3")]
    ])
    await message.answer(msg, reply_markup=kb)

@dp.callback_query(F.data.startswith("buy_status_"))
async def process_buy_status(callback: types.CallbackQuery):
    lvl = int(callback.data.split("_")[2])
    price = float(get_config(f"status_price_{lvl}", 25.0 if lvl==1 else 75.0 if lvl==2 else 200.0))
    user_id = callback.from_user.id
    user = db_query("SELECT balance, status_level FROM users WHERE id = ?", (user_id,), fetchone=True)
    
    if user[1] >= lvl: return await callback.answer("Sizda bu status yoki undan yuqorisi bor!", show_alert=True)
    if user[0] < price: return await callback.answer("Mablag' yetarli emas!", show_alert=True)
    
    db_query("UPDATE users SET balance = balance - ?, status_level = ? WHERE id = ?", (price, lvl, user_id), commit=True)
    add_transaction(user_id, -price, "STATUS", f"Sotib olindi: {STATUS_DATA[lvl]['name']}")
    await callback.message.answer(f"🎉 Tabriklaymiz! Siz **{STATUS_DATA[lvl]['name']}** statusiga ega bo'ldingiz!")
    await callback.answer()

# --- HISOB (TO'LDIRISH VA YECHISH) ---
@dp.message(F.text == "💳 Hisob")
async def account_menu(message: types.Message):
    user = db_query("SELECT balance FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ To'ldirish", callback_data="deposit"),
         InlineKeyboardButton(text="➖ Pul yechish", callback_data="withdraw")]
    ])
    await message.answer(f"💰 Balansingiz: **{user[0]:.2f} {CURRENCY_SYMBOL}**\nNima qilamiz?", reply_markup=kb)

@dp.callback_query(F.data == "deposit")
async def deposit_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("💸 Qancha to'ldirmoqchisiz? (Miqdorni yozing):", reply_markup=cancel_kb())
    await state.set_state(FillBalance.amount)

@dp.message(FillBalance.amount)
async def deposit_amount(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Faqat raqam yozing!")
    await state.update_data(amt=message.text)
    await message.answer(f"💳 To'lovni amalga oshiring:\n\nKarta: `{CARD_UZS}`\nIsm: {CARD_NAME}\n\nTo'lovdan so'ng chekni (rasm) yuboring.")
    await state.set_state(FillBalance.receipt)

@dp.message(FillBalance.receipt, F.photo)
async def deposit_receipt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"adm_dep_ok_{user_id}_{data['amt']}"),
         InlineKeyboardButton(text="❌ Rad etish", callback_data=f"adm_dep_no_{user_id}")]
    ])
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"💰 Depozit: {data['amt']} {CURRENCY_SYMBOL}\nUser: {user_id}", reply_markup=admin_kb)
    await message.answer("✅ Chek adminga yuborildi. Tasdiqlashni kuting.", reply_markup=main_menu(user_id))
    await state.clear()

@dp.callback_query(F.data == "withdraw")
async def withdraw_start(callback: types.CallbackQuery, state: FSMContext):
    user = db_query("SELECT balance FROM users WHERE id = ?", (callback.from_user.id,), fetchone=True)
    if user[0] < 10: return await callback.answer("Minimal yechish 10!", show_alert=True)
    await callback.message.answer("💸 Yechish miqdorini yozing:", reply_markup=cancel_kb())
    await state.set_state(WithdrawState.amount)

@dp.message(WithdrawState.amount)
async def withdraw_amt(message: types.Message, state: FSMContext):
    user = db_query("SELECT balance FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    if not message.text.isdigit() or float(message.text) > user[0]: return await message.answer("Xato miqdor!")
    await state.update_data(amt=message.text)
    await message.answer("💳 Karta raqamingiz va ism sharifingizni yozing:")
    await state.set_state(WithdrawState.details)

@dp.message(WithdrawState.details)
async def withdraw_final(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (data['amt'], user_id), commit=True)
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ To'landi", callback_data=f"adm_wd_ok_{user_id}_{data['amt']}"),
         InlineKeyboardButton(text="❌ Rad (Qaytarish)", callback_data=f"adm_wd_no_{user_id}_{data['amt']}")]
    ])
    await bot.send_message(ADMIN_ID, f"📤 Yechish so'rovi:\nSumma: {data['amt']}\nRekvizit: {message.text}\nUser: {user_id}", reply_markup=admin_kb)
    await message.answer("✅ So'rovingiz qabul qilindi.", reply_markup=main_menu(user_id))
    await state.clear()

# --- PUL ISHLASH (KLIKER) ---
@dp.callback_query(F.data == "clicker_run")
async def clicker_logic(callback: types.CallbackQuery):
    user = db_query("SELECT status_level FROM users WHERE id = ?", (callback.from_user.id,), fetchone=True)
    reward = STATUS_DATA[user[0]]['click']
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (reward, callback.from_user.id), commit=True)
    await callback.answer(f"+{reward} {CURRENCY_SYMBOL} qo'shildi!", show_alert=False)

# --- G'ILDIRAK (LUCKY WHEEL) ---
@dp.message(F.text == "🎡 G'ildirak")
async def wheel_start(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Aylantirish (2 UZC)", callback_data="spin_wheel")]])
    await message.answer("🎡 **Omadli G'ildirak**\n\nTikish: 2 UZC\nYutuqlar: 0.1 dan 10 gacha!", reply_markup=kb)

@dp.callback_query(F.data == "spin_wheel")
async def spin_logic(callback: types.CallbackQuery):
    user = db_query("SELECT balance FROM users WHERE id = ?", (callback.from_user.id,), fetchone=True)
    if user[0] < 2: return await callback.answer("Mablag' yetarli emas!", show_alert=True)
    
    db_query("UPDATE users SET balance = balance - 2 WHERE id = ?", (callback.from_user.id,), commit=True)
    prizes = [0.1, 0.5, 1, 2, 3, 5, 10]
    weights = [40, 30, 15, 8, 4, 2, 1]
    win = random.choices(prizes, weights=weights)[0]
    
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (win, callback.from_user.id), commit=True)
    add_transaction(callback.from_user.id, win-2, "WHEEL", f"Yutuq: {win}")
    await callback.message.edit_text(f"🎰 G'ildirak aylandi...\n\nNatija: **{win} {CURRENCY_SYMBOL}**!")

# --- TARIX ---
@dp.message(F.text == "📜 Tarix")
async def history_show(message: types.Message):
    txs = db_query("SELECT amount, type, timestamp FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 5", (message.from_user.id,), fetchall=True)
    if not txs: return await message.answer("Hali operatsiyalar yo'q.")
    res = "📜 **Oxirgi 5 ta tranzaksiya:**\n\n"
    for tx in txs:
        res += f"🔹 {tx[2][:16]} | {tx[0]} | {tx[1]}\n"
    await message.answer(res)

# --- TOP FOYDALANUVCHILAR ---
@dp.message(F.text == "🏆 Top Foydalanuvchilar")
async def top_users(message: types.Message):
    users = db_query("SELECT id, balance FROM users ORDER BY balance DESC LIMIT 10", fetchall=True)
    res = "🏆 **Top 10 Foydalanuvchi:**\n\n"
    for i, u in enumerate(users, 1):
        res += f"{i}. ID: `{u[0]}` - **{u[1]:.2f} {CURRENCY_SYMBOL}**\n"
    await message.answer(res, parse_mode="Markdown")

# --- XIZMATLAR ---
@dp.message(F.text == "🛠 Xizmatlar")
async def services_menu(message: types.Message):
    await message.answer("🛠 **Bot Xizmatlari:**\n\n1. Bot yaratish\n2. Reklama\n3. VIP kanal\n\nBatafsil ma'lumot uchun adminga yozing.")

# --- ADMIN CALLBACKS (TASDIQLASH) ---
@dp.callback_query(F.data.startswith("adm_dep_ok_"))
async def adm_dep_ok(callback: types.CallbackQuery):
    _, _, _, uid, amt = callback.data.split("_")
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, uid), commit=True)
    add_transaction(uid, amt, "DEPOSIT", "Admin tasdiqladi")
    try: await bot.send_message(uid, f"✅ Hisobingiz {amt} {CURRENCY_SYMBOL} ga to'ldirildi!")
    except: pass
    await callback.message.edit_caption(caption="✅ Tasdiqlandi!")

@dp.callback_query(F.data.startswith("adm_wd_ok_"))
async def adm_wd_ok(callback: types.CallbackQuery):
    _, _, _, uid, amt = callback.data.split("_")
    try: await bot.send_message(uid, f"✅ Yechish so'rovingiz bajarildi! {amt} {CURRENCY_SYMBOL} yuborildi.")
    except: pass
    await callback.message.edit_text("✅ To'landi!")

@dp.callback_query(F.data.startswith("adm_wd_no_"))
async def adm_wd_no(callback: types.CallbackQuery):
    _, _, _, uid, amt = callback.data.split("_")
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, uid), commit=True)
    try: await bot.send_message(uid, f"❌ Yechish so'rovi rad etildi. Pul balansga qaytarildi.")
    except: pass
    await callback.message.edit_text("❌ Rad etildi!")

async def main():
    print("Bot ishlamoqda...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
