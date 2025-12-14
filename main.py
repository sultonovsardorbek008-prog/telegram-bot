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
# Environment variable'larni yuklashda xatolik bo'lmasligi uchun default qiymatlar qo'shildi
API_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN") 
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789")) # O'zingizning admin ID raqamingizni kiriting
DB_NAME = "bot_database_uzcoin_pro.db"

# Karta ma'lumotlari
CARD_UZS = os.getenv("CARD_UZS", "8600 0000 0000 0000")
CARD_NAME = os.getenv("CARD_NAME", "Bot Admin")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- BAZA BILAN ISHLASH ---
def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    """SQLite bazasi bilan ishlash uchun yordamchi funksiya."""
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
    """Tranzaksiya qo'shish."""
    db_query("INSERT INTO transactions (user_id, amount, type, description) VALUES (?, ?, ?, ?)",
             (user_id, amount, tx_type, description), commit=True)

def init_db():
    """Baza jadvallarini yaratish."""
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
    """Konfiguratsiya qiymatini olish/o'rnatish."""
    res = db_query("SELECT value FROM config WHERE key = ?", (key,), fetchone=True)
    if res: return res[0]
    if default_value is not None:
        db_query("INSERT INTO config (key, value) VALUES (?, ?)", (key, str(default_value)), commit=True)
        return str(default_value)
    return None

def set_config(key, value):
    """Konfiguratsiya qiymatini yangilash."""
    db_query("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)), commit=True)

CURRENCY_NAME = get_config("currency_name", "UzCoin")
CURRENCY_SYMBOL = get_config("currency_symbol", "🪙")
UZS_TO_UZC_RATE = float(get_config("uzc_rate", 1000)) # 1 UZC = 1000 UZS

STATUS_DATA = {
    0: {"name": "👤 Start", "limit": 30, "bonus_mult": 1.0, "click": 0.01},
    1: {"name": "🥈 Silver", "limit": 100, "bonus_mult": 1.5, "click": 0.05},
    2: {"name": "🥇 Gold", "limit": 1000, "bonus_mult": 2.5, "click": 0.15},
    3: {"name": "💎 Platinum", "limit": 100000, "bonus_mult": 5.0, "click": 0.50}
}
# Status narxlari configdan olinadi
STATUS_PRICES = {
    1: float(get_config("status_price_1", 25.0)),
    2: float(get_config("status_price_2", 75.0)),
    3: float(get_config("status_price_3", 200.0)),
}

# --- FSM STATES ---
class AdminState(StatesGroup):
    broadcast_msg = State()
    
    # Loyihalar
    add_proj_name = State()
    add_proj_price = State()
    add_proj_desc = State()
    add_proj_media = State()
    add_proj_file = State()
    
    # Xizmatlar
    add_service_name = State()
    add_service_desc = State()
    add_service_contact = State()
    
    # Narxlar
    set_price_key = State()
    set_price_value = State()

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

def admin_menu():
    kb = [
        [KeyboardButton(text="💰 Narxlarni Boshqarish"), KeyboardButton(text="📢 Xabar yuborish")],
        [KeyboardButton(text="➕ Loyiha Qo'shish"), KeyboardButton(text="📝 Loyihalarni O'chirish")],
        [KeyboardButton(text="➕ Xizmat Qo'shish"), KeyboardButton(text="❌ Xizmatni O'chirish")],
        [KeyboardButton(text="⬅️ Bosh Menyuga")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def price_management_menu():
    kb = [
        [InlineKeyboardButton(text="Status Silver Narxi", callback_data="adm_set_price_status_price_1")],
        [InlineKeyboardButton(text="Status Gold Narxi", callback_data="adm_set_price_status_price_2")],
        [InlineKeyboardButton(text="Status Platinum Narxi", callback_data="adm_set_price_status_price_3")],
        [InlineKeyboardButton(text=f"1 UZC kursi ({UZS_TO_UZC_RATE} UZS)", callback_data="adm_set_price_uzc_rate")],
        [InlineKeyboardButton(text=f"Referal Mukofoti ({get_config('ref_reward', 1.0)} UZC)", callback_data="adm_set_price_ref_reward")],
        [InlineKeyboardButton(text="⬅️ Admin Panelga Qaytish", callback_data="adm_back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


# --------------------------------------------------------------------------------
# --- ASOSIY HANDLERLAR ---
# --------------------------------------------------------------------------------

@dp.message(F.text == "🚫 Bekor qilish", StateFilter("*"))
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        await message.answer("🚫 Jarayon bekor qilindi.", reply_markup=admin_menu())
    else:
        await message.answer("🚫 Jarayon bekor qilindi.", reply_markup=main_menu(user_id))

@dp.message(F.text == "⬅️ Bosh Menyuga", StateFilter("*"))
async def back_to_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Bosh menyu:", reply_markup=main_menu(message.from_user.id))

@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    if not db_query("SELECT id FROM users WHERE id = ?", (user_id,), fetchone=True):
        # Referal tekshiruvi va mukofot
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
    user_id = message.from_user.id
    user = db_query("SELECT balance, status_level, joined_at, referrer_id FROM users WHERE id = ?", (user_id,), fetchone=True)
    ref_count = db_query("SELECT COUNT(id) FROM users WHERE referrer_id = ?", (user_id,), fetchone=True)[0]
    status_info = STATUS_DATA[user[1]]
    
    ref_text = f"\n🔗 Referal link: `https://t.me/{bot.me.username}?start={user_id}`"
    
    text = (f"👤 **Sizning Kabinetingiz**\n\n"
            f"🆔 ID: `{user_id}`\n"
            f"💰 Balans: **{user[0]:.2f} {CURRENCY_SYMBOL}**\n"
            f"📊 Status: **{status_info['name']}**\n"
            f"📈 Bonus koeffitsiyenti: x{status_info['bonus_mult']}\n"
            f"👥 Referallar soni: **{ref_count}**\n"
            f"🗓 A'zo bo'lgan: {user[2][:10]}"
            f"{ref_text}")
    await message.answer(text, parse_mode="Markdown")

# --- STATUSLAR ---
@dp.message(F.text == "🌟 Statuslar")
async def status_menu(message: types.Message):
    global STATUS_PRICES
    STATUS_PRICES = {
        1: float(get_config("status_price_1", 25.0)),
        2: float(get_config("status_price_2", 75.0)),
        3: float(get_config("status_price_3", 200.0)),
    }
    
    msg = (f"🌟 **Status Darajalari**\n\n"
           f"🥈 **Silver** - {STATUS_PRICES[1]} {CURRENCY_SYMBOL}\n(Klik: {STATUS_DATA[1]['click']}, Bonus: x{STATUS_DATA[1]['bonus_mult']})\n\n"
           f"🥇 **Gold** - {STATUS_PRICES[2]} {CURRENCY_SYMBOL}\n(Klik: {STATUS_DATA[2]['click']}, Bonus: x{STATUS_DATA[2]['bonus_mult']})\n\n"
           f"💎 **Platinum** - {STATUS_PRICES[3]} {CURRENCY_SYMBOL}\n(Klik: {STATUS_DATA[3]['click']}, Bonus: x{STATUS_DATA[3]['bonus_mult']})")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🥈 Silver", callback_data="buy_status_1"),
         InlineKeyboardButton(text="🥇 Gold", callback_data="buy_status_2")],
        [InlineKeyboardButton(text="💎 Platinum", callback_data="buy_status_3")]
    ])
    await message.answer(msg, reply_markup=kb)

@dp.callback_query(F.data.startswith("buy_status_"))
async def process_buy_status(callback: types.CallbackQuery):
    lvl = int(callback.data.split("_")[2])
    price = STATUS_PRICES[lvl]
    user_id = callback.from_user.id
    user = db_query("SELECT balance, status_level FROM users WHERE id = ?", (user_id,), fetchone=True)
    
    if user[1] >= lvl: return await callback.answer("Sizda bu status yoki undan yuqorisi bor!", show_alert=True)
    if user[0] < price: return await callback.answer("Mablag' yetarli emas!", show_alert=True)
    
    db_query("UPDATE users SET balance = balance - ?, status_level = ? WHERE id = ?", (price, lvl, user_id), commit=True)
    add_transaction(user_id, -price, "STATUS", f"Sotib olindi: {STATUS_DATA[lvl]['name']}")
    await callback.message.answer(f"🎉 Tabriklaymiz! Siz **{STATUS_DATA[lvl]['name']}** statusiga ega bo'ldingiz!")
    await callback.answer()

# --- HISOB (TO'LDIRISH VA YECHISH) --- (o'zgartirishsiz qoldi)

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
    try:
        amount = float(message.text)
        if amount <= 0: return await message.answer("Miqdor musbat bo'lishi kerak!")
    except ValueError:
        return await message.answer("Faqat raqam yozing!")

    await state.update_data(amt=amount)
    
    # UZS hisobi
    uzs_amount = amount * UZS_TO_UZC_RATE
    
    await message.answer(f"✅ Siz {amount:.2f} {CURRENCY_SYMBOL} olish uchun {uzs_amount:.2f} UZS to'lashingiz kerak.\n\n"
                         f"💳 To'lovni amalga oshiring:\n\nKarta: `{CARD_UZS}`\nIsm: {CARD_NAME}\n\nTo'lovdan so'ng chekni (rasm) yuboring.")
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
    min_withdraw = float(get_config("min_withdraw", 10.0))
    if user[0] < min_withdraw: return await callback.answer(f"Minimal yechish {min_withdraw} {CURRENCY_SYMBOL}!", show_alert=True)
    await callback.message.answer("💸 Yechish miqdorini yozing:", reply_markup=cancel_kb())
    await state.set_state(WithdrawState.amount)

@dp.message(WithdrawState.amount)
async def withdraw_amt(message: types.Message, state: FSMContext):
    user = db_query("SELECT balance FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    try:
        amount = float(message.text)
        min_withdraw = float(get_config("min_withdraw", 10.0))
        if amount < min_withdraw or amount > user[0]: 
            return await message.answer(f"Xato miqdor! Minimal: {min_withdraw}, Balansingiz: {user[0]:.2f}")
    except ValueError:
        return await message.answer("Faqat raqam yozing!")

    await state.update_data(amt=amount)
    await message.answer("💳 Karta raqamingiz va ism sharifingizni yozing:")
    await state.set_state(WithdrawState.details)

@dp.message(WithdrawState.details)
async def withdraw_final(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    
    # Balansdan ayirish
    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (data['amt'], user_id), commit=True)
    
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ To'landi", callback_data=f"adm_wd_ok_{user_id}_{data['amt']}"),
         InlineKeyboardButton(text="❌ Rad (Qaytarish)", callback_data=f"adm_wd_no_{user_id}_{data['amt']}")]
    ])
    await bot.send_message(ADMIN_ID, f"📤 Yechish so'rovi:\nSumma: {data['amt']} {CURRENCY_SYMBOL}\nRekvizit: {message.text}\nUser: {user_id}", reply_markup=admin_kb)
    await message.answer("✅ So'rovingiz qabul qilindi. Balansingizdan yechib olindi.", reply_markup=main_menu(user_id))
    await state.clear()

# --- PUL ISHLASH (KLIKER) ---
@dp.message(F.text == "💸 Pul ishlash")
async def money_earn_menu(message: types.Message):
    user = db_query("SELECT status_level FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    current_status = STATUS_DATA[user[0]]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Tanga yig'ish (+{current_status['click']} {CURRENCY_SYMBOL})", callback_data="clicker_run")]
    ])
    await message.answer(f"💸 **Pul ishlash bo'limi**\n\n"
                         f"Joriy Status: **{current_status['name']}**\n"
                         f"Har bir bosish: **{current_status['click']} {CURRENCY_SYMBOL}**", reply_markup=kb)

@dp.callback_query(F.data == "clicker_run")
async def clicker_logic(callback: types.CallbackQuery):
    user = db_query("SELECT status_level FROM users WHERE id = ?", (callback.from_user.id,), fetchone=True)
    reward = STATUS_DATA[user[0]]['click']
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (reward, callback.from_user.id), commit=True)
    add_transaction(callback.from_user.id, reward, "CLICKER", "Tanga yig'ish")
    await callback.answer(f"+{reward} {CURRENCY_SYMBOL} qo'shildi!", show_alert=False)

# --- G'ILDIRAK (LUCKY WHEEL) --- (o'zgartirishsiz qoldi)
@dp.message(F.text == "🎡 G'ildirak")
async def wheel_start(message: types.Message):
    wheel_cost = float(get_config("wheel_cost", 2.0))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔄 Aylantirish ({wheel_cost} {CURRENCY_SYMBOL})", callback_data="spin_wheel")]])
    await message.answer(f"🎡 **Omadli G'ildirak**\n\nTikish: {wheel_cost} {CURRENCY_SYMBOL}\nYutuqlar: 0.1 dan 10 gacha!", reply_markup=kb)

@dp.callback_query(F.data == "spin_wheel")
async def spin_logic(callback: types.CallbackQuery):
    wheel_cost = float(get_config("wheel_cost", 2.0))
    user = db_query("SELECT balance FROM users WHERE id = ?", (callback.from_user.id,), fetchone=True)
    if user[0] < wheel_cost: return await callback.answer("Mablag' yetarli emas!", show_alert=True)
    
    # Tikish
    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (wheel_cost, callback.from_user.id), commit=True)
    add_transaction(callback.from_user.id, -wheel_cost, "WHEEL", "Tikish")

    prizes = [0.1, 0.5, 1, 2, 3, 5, 10]
    weights = [40, 30, 15, 8, 4, 2, 1]
    win = random.choices(prizes, weights=weights)[0]
    
    # Yutuqni qo'shish
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (win, callback.from_user.id), commit=True)
    add_transaction(callback.from_user.id, win, "WHEEL", f"Yutuq: {win}")
    
    result_text = f"🎰 G'ildirak aylandi...\n\nNatija: **{win} {CURRENCY_SYMBOL}**!"
    if win > wheel_cost: result_text += "\n🎉 Siz yutdingiz!"
    elif win == wheel_cost: result_text += "\n🤔 Durrang!"
    else: result_text += "\n😔 Yutqazdingiz."
    
    await callback.message.edit_text(result_text)
    await callback.answer()

# --- TARIX --- (o'zgartirishsiz qoldi)
@dp.message(F.text == "📜 Tarix")
async def history_show(message: types.Message):
    txs = db_query("SELECT amount, type, timestamp FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 10", (message.from_user.id,), fetchall=True)
    if not txs: return await message.answer("Hali operatsiyalar yo'q.")
    res = "📜 **Oxirgi 10 ta tranzaksiya:**\n\n"
    for tx in txs:
        sign = "+" if tx[0] > 0 else ""
        res += f"🔹 {tx[2][:16]} | {sign}{tx[0]:.2f} {CURRENCY_SYMBOL} | {tx[1]}\n"
    await message.answer(res)

# --- TOP FOYDALANUVCHILAR --- (o'zgartirishsiz qoldi)
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
    services = db_query("SELECT name, description, contact FROM services", fetchall=True)
    
    if not services:
        return await message.answer("🛠 Hozircha hech qanday xizmatlar qo'shilmagan.")
        
    res = "🛠 **Bot Xizmatlari:**\n\n"
    for name, desc, contact in services:
        res += f"**{name}**\n"
        res += f" - Tavsif: *{desc}*\n"
        res += f" - Murojaat: `{contact}`\n\n"
        
    await message.answer(res, parse_mode="Markdown")

# --- LOYIHALAR (USERS) ---
@dp.message(F.text == "📂 Loyihalar")
async def projects_menu(message: types.Message):
    projects = db_query("SELECT id, name, price, description, media_type FROM projects", fetchall=True)
    
    if not projects:
        return await message.answer("📂 Hozircha hech qanday loyihalar mavjud emas.")
        
    kb = []
    res = "📂 **Mavjud Loyihalar Ro'yxati:**\n\n"
    for proj_id, name, price, desc, media_type in projects:
        res += f"🔹 **{name}** | Narxi: **{price:.2f} {CURRENCY_SYMBOL}**\n"
        kb.append([InlineKeyboardButton(text=f"📂 {name}", callback_data=f"show_proj_{proj_id}")])
    
    await message.answer(res, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("show_proj_"))
async def show_project_details(callback: types.CallbackQuery):
    proj_id = int(callback.data.split("_")[2])
    proj = db_query("SELECT name, price, description, media_id, media_type FROM projects WHERE id = ?", (proj_id,), fetchone=True)
    
    if not proj: return await callback.answer("Loyihaning ma'lumotlari topilmadi.", show_alert=True)
    
    name, price, desc, media_id, media_type = proj
    
    text = (f"✨ **{name}** Loyihasi\n\n"
            f"💰 Narxi: **{price:.2f} {CURRENCY_SYMBOL}**\n"
            f"📝 Tavsif:\n{desc}\n\n"
            f"Sotib olasizmi?")
            
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Sotib Olish ({price:.2f} {CURRENCY_SYMBOL})", callback_data=f"buy_proj_{proj_id}")]
    ])
    
    if media_id and media_type:
        if media_type in ['photo', 'video']:
            await bot.send_photo(callback.from_user.id, media_id, caption=text, reply_markup=kb, parse_mode="Markdown")
        elif media_type == 'video':
            await bot.send_video(callback.from_user.id, media_id, caption=text, reply_markup=kb, parse_mode="Markdown")
        else:
            # Agar media bo'lsa-yu, turi noto'g'ri bo'lsa, oddiy xabar
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_proj_"))
async def buy_project(callback: types.CallbackQuery):
    proj_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    
    proj = db_query("SELECT name, price, file_id FROM projects WHERE id = ?", (proj_id,), fetchone=True)
    user = db_query("SELECT balance FROM users WHERE id = ?", (user_id,), fetchone=True)
    
    if not proj: return await callback.answer("Loyihaning ma'lumotlari topilmadi.", show_alert=True)
    name, price, file_id = proj
    
    if user[0] < price: return await callback.answer("Mablag' yetarli emas!", show_alert=True)
    
    # Tranzaksiya
    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (price, user_id), commit=True)
    add_transaction(user_id, -price, "PROJECT_BUY", f"Loyihani sotib olish: {name}")
    
    # Faylni yuborish
    if file_id:
        try:
            await bot.send_document(user_id, file_id, caption=f"🎉 Tabriklaymiz! Siz **{name}** loyihasini sotib oldingiz.")
        except Exception as e:
            logging.error(f"Fayl yuborishda xatolik: {e}")
            await bot.send_message(user_id, f"🎉 Loyiha sotib olindi, ammo faylni yuborishda xatolik yuz berdi. Adminga murojaat qiling.")
    else:
        await bot.send_message(user_id, f"🎉 Tabriklaymiz! Siz **{name}** loyihasini sotib oldingiz.\nLekin loyiha fayli yuklanmagan. Adminga murojaat qiling.")

    await callback.message.edit_text(f"✅ **{name}** loyihasi muvaffaqiyatli sotib olindi!")
    await callback.answer()

# --------------------------------------------------------------------------------
# --- ADMIN HANDLERLAR ---
# --------------------------------------------------------------------------------

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def cmd_admin(message: types.Message):
    await message.answer("👑 **Admin Panel**ga xush kelibsiz!", reply_markup=admin_menu())

# --- ADMIN: NARXLARNI BOSHQARISH ---
@dp.message(F.text == "💰 Narxlarni Boshqarish", F.from_user.id == ADMIN_ID)
async def admin_price_management(message: types.Message):
    global UZS_TO_UZC_RATE
    UZS_TO_UZC_RATE = float(get_config("uzc_rate", 1000)) # Yangilash
    await message.answer("💰 **Narxlarni boshqarish menyusi.** O'zgartirmoqchi bo'lgan narxni tanlang:", reply_markup=price_management_menu())

@dp.callback_query(F.data.startswith("adm_set_price_"), F.from_user.id == ADMIN_ID)
async def admin_start_set_price(callback: types.CallbackQuery, state: FSMContext):
    price_key = callback.data.split("_", 3)[3]
    current_value = get_config(price_key)
    
    if price_key == "uzc_rate":
        text = f"1 UZC ning UZS dagi yangi kursini kiriting (hozir: {current_value} UZS):"
    elif price_key == "ref_reward":
        text = f"Referal mukofotining yangi miqdorini UzCoin da kiriting (hozir: {current_value} {CURRENCY_SYMBOL}):"
    else:
        status_name = STATUS_DATA[int(price_key.split('_')[-1])]['name']
        text = f"**{status_name}** statusining yangi narxini UzCoin da kiriting (hozir: {current_value} {CURRENCY_SYMBOL}):"
        
    await state.update_data(price_key=price_key)
    await callback.message.edit_text(text, reply_markup=cancel_kb())
    await state.set_state(AdminState.set_price_value)
    await callback.answer()

@dp.message(AdminState.set_price_value, F.from_user.id == ADMIN_ID)
async def admin_final_set_price(message: types.Message, state: FSMContext):
    data = await state.get_data()
    price_key = data['price_key']
    
    try:
        new_value = float(message.text)
        if new_value <= 0: raise ValueError
    except ValueError:
        return await message.answer("Noto'g'ri qiymat. Faqat musbat raqam kiriting.")
        
    set_config(price_key, new_value)
    
    if price_key == "uzc_rate":
        global UZS_TO_UZC_RATE
        UZS_TO_UZC_RATE = new_value
        msg = f"✅ 1 {CURRENCY_NAME} kursi muvaffaqiyatli **{new_value} UZS** qilib o'rnatildi!"
    elif price_key == "ref_reward":
        msg = f"✅ Referal mukofoti muvaffaqiyatli **{new_value} {CURRENCY_SYMBOL}** qilib o'rnatildi!"
    else:
        status_name = STATUS_DATA[int(price_key.split('_')[-1])]['name']
        global STATUS_PRICES
        STATUS_PRICES[int(price_key.split('_')[-1])] = new_value
        msg = f"✅ **{status_name}** statusining yangi narxi **{new_value} {CURRENCY_SYMBOL}** qilib o'rnatildi!"

    await state.clear()
    await message.answer(msg, reply_markup=admin_menu())

# --- ADMIN: LOYIHA QO'SHISH ---

@dp.message(F.text == "➕ Loyiha Qo'shish", F.from_user.id == ADMIN_ID)
async def admin_add_proj_start(message: types.Message, state: FSMContext):
    await message.answer("✍️ Loyihaning **nomini** kiriting:", reply_markup=cancel_kb())
    await state.set_state(AdminState.add_proj_name)

@dp.message(AdminState.add_proj_name, F.from_user.id == ADMIN_ID)
async def admin_add_proj_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("💰 Loyihaning **narxini** UzCoin da kiriting (faqat raqam):")
    await state.set_state(AdminState.add_proj_price)

@dp.message(AdminState.add_proj_price, F.from_user.id == ADMIN_ID)
async def admin_add_proj_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text)
        if price <= 0: raise ValueError
    except ValueError:
        return await message.answer("Noto'g'ri narx formati. Faqat musbat raqam kiriting.")
        
    await state.update_data(price=price)
    await message.answer("📝 Loyihaning **tavsifini** kiriting:")
    await state.set_state(AdminState.add_proj_desc)

@dp.message(AdminState.add_proj_desc, F.from_user.id == ADMIN_ID)
async def admin_add_proj_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("🖼 Loyihaning **rasm/videosini** yuboring (yoki keyingi qadamga o'tish uchun 'o'tkazish' deb yozing):")
    await state.set_state(AdminState.add_proj_media)

@dp.message(AdminState.add_proj_media, F.from_user.id == ADMIN_ID, F.text | F.photo | F.video)
async def admin_add_proj_media(message: types.Message, state: FSMContext):
    media_id = None
    media_type = None
    
    if message.text and message.text.lower() in ['o\'tkazish', 'otkazish']:
        pass
    elif message.photo:
        media_id = message.photo[-1].file_id
        media_type = 'photo'
    elif message.video:
        media_id = message.video.file_id
        media_type = 'video'
    
    await state.update_data(media_id=media_id, media_type=media_type)
    await message.answer("📄 Loyihaning **asosiy faylini** (dokument, zip, ...) yuboring:")
    await state.set_state(AdminState.add_proj_file)

@dp.message(AdminState.add_proj_file, F.from_user.id == ADMIN_ID, F.document)
async def admin_add_proj_file_final(message: types.Message, state: FSMContext):
    data = await state.get_data()
    file_id = message.document.file_id
    
    db_query("INSERT INTO projects (name, price, description, media_id, media_type, file_id) VALUES (?, ?, ?, ?, ?, ?)",
             (data['name'], data['price'], data['description'], data['media_id'], data['media_type'], file_id), commit=True)
             
    await state.clear()
    await message.answer(f"✅ Loyiha **{data['name']}** muvaffaqiyatli qo'shildi!", reply_markup=admin_menu())

# --- ADMIN: LOYIHANI O'CHIRISH ---
@dp.message(F.text == "📝 Loyihalarni O'chirish", F.from_user.id == ADMIN_ID)
async def admin_delete_proj_list(message: types.Message):
    projects = db_query("SELECT id, name, price FROM projects", fetchall=True)
    if not projects: return await message.answer("Loyihalar yo'q.")
    
    kb = []
    for proj_id, name, price in projects:
        kb.append([InlineKeyboardButton(text=f"❌ {name} ({price} {CURRENCY_SYMBOL})", callback_data=f"adm_del_proj_{proj_id}")])
        
    await message.answer("O'chirmoqchi bo'lgan loyihani tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("adm_del_proj_"), F.from_user.id == ADMIN_ID)
async def admin_delete_proj_confirm(callback: types.CallbackQuery):
    proj_id = int(callback.data.split("_")[3])
    proj = db_query("SELECT name FROM projects WHERE id = ?", (proj_id,), fetchone=True)
    if not proj: return await callback.answer("Loyihaning ma'lumotlari topilmadi.", show_alert=True)
    name = proj[0]
    
    db_query("DELETE FROM projects WHERE id = ?", (proj_id,), commit=True)
    await callback.message.edit_text(f"✅ Loyiha **{name}** muvaffaqiyatli o'chirildi!", reply_markup=None)
    await callback.answer()

# --- ADMIN: XIZMAT QO'SHISH ---

@dp.message(F.text == "➕ Xizmat Qo'shish", F.from_user.id == ADMIN_ID)
async def admin_add_service_start(message: types.Message, state: FSMContext):
    await message.answer("✍️ Xizmatning **nomini** kiriting (masalan: Bot yaratish):", reply_markup=cancel_kb())
    await state.set_state(AdminState.add_service_name)

@dp.message(AdminState.add_service_name, F.from_user.id == ADMIN_ID)
async def admin_add_service_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("📝 Xizmatning **tavsifini** kiriting:")
    await state.set_state(AdminState.add_service_desc)

@dp.message(AdminState.add_service_desc, F.from_user.id == ADMIN_ID)
async def admin_add_service_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("📞 Murojaat uchun **kontaktni** (username yoki ID) kiriting:")
    await state.set_state(AdminState.add_service_contact)

@dp.message(AdminState.add_service_contact, F.from_user.id == ADMIN_ID)
async def admin_add_service_contact_final(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    db_query("INSERT INTO services (name, description, contact) VALUES (?, ?, ?)",
             (data['name'], data['description'], message.text), commit=True)
             
    await state.clear()
    await message.answer(f"✅ Xizmat **{data['name']}** muvaffaqiyatli qo'shildi!", reply_markup=admin_menu())

# --- ADMIN: XIZMATNI O'CHIRISH ---

@dp.message(F.text == "❌ Xizmatni O'chirish", F.from_user.id == ADMIN_ID)
async def admin_delete_service_list(message: types.Message):
    services = db_query("SELECT id, name FROM services", fetchall=True)
    if not services: return await message.answer("Xizmatlar yo'q.")
    
    kb = []
    for service_id, name in services:
        kb.append([InlineKeyboardButton(text=f"❌ {name}", callback_data=f"adm_del_service_{service_id}")])
        
    await message.answer("O'chirmoqchi bo'lgan xizmatni tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("adm_del_service_"), F.from_user.id == ADMIN_ID)
async def admin_delete_service_confirm(callback: types.CallbackQuery):
    service_id = int(callback.data.split("_")[3])
    service = db_query("SELECT name FROM services WHERE id = ?", (service_id,), fetchone=True)
    if not service: return await callback.answer("Xizmatning ma'lumotlari topilmadi.", show_alert=True)
    name = service[0]
    
    db_query("DELETE FROM services WHERE id = ?", (service_id,), commit=True)
    await callback.message.edit_text(f"✅ Xizmat **{name}** muvaffaqiyatli o'chirildi!", reply_markup=None)
    await callback.answer()

# --- ADMIN: XABAR YUBORISH --- (eski koddagi kabi saqlanib qoldi)
@dp.message(F.text == "📢 Xabar yuborish", F.from_user.id == ADMIN_ID)
async def admin_broadcast_start(message: types.Message, state: FSMContext):
    await message.answer("📢 Barcha foydalanuvchilarga yuboriladigan xabarni kiriting (matn yoki media + izoh):", reply_markup=cancel_kb())
    await state.set_state(AdminState.broadcast_msg)

@dp.message(AdminState.broadcast_msg, F.from_user.id == ADMIN_ID)
async def admin_broadcast_send(message: types.Message, state: FSMContext):
    await state.clear()
    users = db_query("SELECT id FROM users", fetchall=True)
    sent_count = 0
    
    for user_data in users:
        user_id = user_data[0]
        try:
            if message.text:
                await bot.send_message(user_id, message.html_text, parse_mode='HTML')
            elif message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption or "", parse_mode='HTML')
            elif message.video:
                await bot.send_video(user_id, message.video.file_id, caption=message.caption or "", parse_mode='HTML')
            # Boshqa media turlarini ham qo'shish mumkin
            sent_count += 1
            await asyncio.sleep(0.05) # Flood limitini buzmaslik uchun
        except Exception as e:
            logging.error(f"Xabar yuborishda xatolik: {user_id}, {e}")
            
    await message.answer(f"✅ Xabar yuborish yakunlandi. {len(users)} tadan {sent_count} tasiga yuborildi.", reply_markup=admin_menu())


# --- ADMIN CALLBACKS (TASDIQLASH) --- (o'zgartirishsiz qoldi)

@dp.callback_query(F.data.startswith("adm_dep_ok_"), F.from_user.id == ADMIN_ID)
async def adm_dep_ok(callback: types.CallbackQuery):
    _, _, _, uid, amt = callback.data.split("_")
    uid = int(uid)
    amt = float(amt)
    
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, uid), commit=True)
    add_transaction(uid, amt, "DEPOSIT", "Admin tasdiqladi")
    try: await bot.send_message(uid, f"✅ Hisobingiz {amt:.2f} {CURRENCY_SYMBOL} ga to'ldirildi!")
    except: pass
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ Tasdiqlandi!")
    await callback.answer()

@dp.callback_query(F.data.startswith("adm_dep_no_"), F.from_user.id == ADMIN_ID)
async def adm_dep_no(callback: types.CallbackQuery):
    _, _, _, uid = callback.data.split("_")
    try: await bot.send_message(int(uid), "❌ Hisobni to'ldirish so'rovi rad etildi. Chekni tekshiring.")
    except: pass
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ Rad etildi!")
    await callback.answer()

@dp.callback_query(F.data.startswith("adm_wd_ok_"), F.from_user.id == ADMIN_ID)
async def adm_wd_ok(callback: types.CallbackQuery):
    _, _, _, uid, amt = callback.data.split("_")
    uid = int(uid)
    amt = float(amt)
    
    try: await bot.send_message(uid, f"✅ Yechish so'rovingiz bajarildi! {amt:.2f} {CURRENCY_SYMBOL} yuborildi.")
    except: pass
    await callback.message.edit_text(callback.message.text + "\n\n✅ To'landi!")
    await callback.answer()

@dp.callback_query(F.data.startswith("adm_wd_no_"), F.from_user.id == ADMIN_ID)
async def adm_wd_no(callback: types.CallbackQuery):
    _, _, _, uid, amt = callback.data.split("_")
    uid = int(uid)
    amt = float(amt)
    
    # Pulni balansga qaytarish
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, uid), commit=True)
    add_transaction(uid, amt, "WITHDRAW_REVERT", "Admin rad etdi")
    
    try: await bot.send_message(uid, f"❌ Yechish so'rovi rad etildi. Pul balansga qaytarildi: {amt:.2f} {CURRENCY_SYMBOL}.")
    except: pass
    await callback.message.edit_text(callback.message.text + "\n\n❌ Rad etildi (pul qaytarildi)!")
    await callback.answer()

# --- Xato handler (Loyihani faylsiz yuborishga urinish)
@dp.message(AdminState.add_proj_file, F.from_user.id == ADMIN_ID, ~F.document)
async def admin_add_proj_file_error(message: types.Message):
    await message.answer("Iltimos, loyihaning **asosiy faylini (dokument)** yuboring.")
    
async def main():
    print("Bot ishlamoqda...")
    # Sozlamalarni tekshirish (uzc_rate va ref_reward yaratilganligini ta'minlaydi)
    get_config("uzc_rate", 1000)
    get_config("ref_reward", 1.0)
    get_config("min_withdraw", 10.0)
    get_config("wheel_cost", 2.0)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
