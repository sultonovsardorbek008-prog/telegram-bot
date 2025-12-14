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

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- BAZA BILAN ISHLASH (YAXSHILANGAN) ---
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
    """Har bir pul harakatini qayd etish."""
    db_query("INSERT INTO transactions (user_id, amount, type, description) VALUES (?, ?, ?, ?)",
             (user_id, amount, tx_type, description), commit=True)

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # Foydalanuvchilar
        cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                          (id INTEGER PRIMARY KEY, balance REAL DEFAULT 0.0,
                           status_level INTEGER DEFAULT 0, status_expire TEXT,
                           referrer_id INTEGER, joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           last_daily_claim TEXT)''')
        # Tranzaksiyalar
        cursor.execute('''CREATE TABLE IF NOT EXISTS transactions 
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
                           amount REAL, type TEXT, description TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        # Pul yechish so'rovlari
        cursor.execute('''CREATE TABLE IF NOT EXISTS withdrawals 
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
                           amount REAL, details TEXT, status TEXT DEFAULT 'pending')''')
        # Config va Loyihalar (Eski jadvallar)
        cursor.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price REAL, description TEXT, media_id TEXT, media_type TEXT, file_id TEXT)')
        conn.commit()
    
    # Migratsiyalar
    cols = [("users", "last_daily_claim", "TEXT"), ("users", "status_level", "INTEGER DEFAULT 0")]
    for table, col, dtype in cols:
        try: db_query(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}", commit=True)
        except: pass

init_db()

# --- DINAMIK SOZLAMALAR ---
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

STATUS_DATA = {
    0: {"name": "👤 Start", "limit": 30, "bonus_mult": 1.0},
    1: {"name": "🥈 Silver", "limit": 100, "bonus_mult": 1.5},
    2: {"name": "🥇 Gold", "limit": 1000, "bonus_mult": 2.0},
    3: {"name": "💎 Platinum", "limit": 100000, "bonus_mult": 5.0}
}

# --- STATES ---
class WithdrawState(StatesGroup):
    amount = State()
    details = State()

class AdminState(StatesGroup):
    change_config_value = State()
    broadcast_msg = State()
    add_proj_name = State()
    add_proj_price = State()
    add_proj_desc = State()
    add_proj_media = State()
    add_proj_file = State()

# --- KEYBOARDS ---
def main_menu(user_id):
    kb = [
        [KeyboardButton(text="👤 Kabinet"), KeyboardButton(text="🌟 Statuslar")],
        [KeyboardButton(text="🛠 Xizmatlar"), KeyboardButton(text="📂 Loyihalar")],
        [KeyboardButton(text="💳 Hisob"), KeyboardButton(text="💸 Pul ishlash")],
        [KeyboardButton(text="🎡 Omadli G'ildirak"), KeyboardButton(text="📜 Tarix")],
        [KeyboardButton(text="🏆 Top Foydalanuvchilar")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --------------------------------------------------------------------------------
# --- ASOSIY FUNKSIYALAR ---
# --------------------------------------------------------------------------------

@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    if not db_query("SELECT id FROM users WHERE id = ?", (user_id,), fetchone=True):
        ref_id = int(command.args) if command.args and command.args.isdigit() else None
        db_query("INSERT INTO users (id, referrer_id) VALUES (?, ?)", (user_id, ref_id), commit=True)
        if ref_id:
            reward = float(get_config("ref_reward", 1.0))
            db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (reward, ref_id), commit=True)
            add_transaction(ref_id, reward, "REFERRAL", f"Yangi do'st: {user_id}")
            try: await bot.send_message(ref_id, f"🎉 Referal bonus: +{reward} {CURRENCY_SYMBOL}")
            except: pass
    
    await message.answer(f"👋 Xush kelibsiz! Botimizda xizmatlardan foydalaning va {CURRENCY_NAME} ishlang.", 
                         reply_markup=main_menu(user_id))

# --- KUNLIK BONUS ---
@dp.message(F.text == "🎁 Kunlik Bonus") # "Pul ishlash" ichida bo'lishi ham mumkin
async def daily_bonus(message: types.Message):
    user_id = message.from_user.id
    user = db_query("SELECT last_daily_claim, status_level FROM users WHERE id = ?", (user_id,), fetchone=True)
    
    now = datetime.datetime.now()
    if user[0]:
        last_claim = datetime.datetime.strptime(user[0], "%Y-%m-%d %H:%M:%S")
        if (now - last_claim).total_seconds() < 86400:
            remains = datetime.timedelta(seconds=86400 - (now - last_claim).total_seconds())
            return await message.answer(f"⏳ Bonusni olib bo'lgansiz! \nYana `{str(remains).split('.')[0]}` vaqtdan keyin keling.")

    base_bonus = random.uniform(0.1, 0.5)
    multiplier = STATUS_DATA[user[1]]['bonus_mult']
    total_bonus = round(base_bonus * multiplier, 2)
    
    db_query("UPDATE users SET balance = balance + ?, last_daily_claim = ? WHERE id = ?", 
             (total_bonus, now.strftime("%Y-%m-%d %H:%M:%S"), user_id), commit=True)
    add_transaction(user_id, total_bonus, "BONUS", "Kunlik bonus")
    
    await message.answer(f"🎁 Tabriklaymiz! Bugungi bonus: **{total_bonus} {CURRENCY_SYMBOL}**\nStatus koeffitsiyenti: x{multiplier}")

# --- OMADLI G'ILDIRAK (WHEEL OF FORTUNE) ---
@dp.message(F.text == "🎡 Omadli G'ildirak")
async def wheel_menu(message: types.Message):
    price = float(get_config("wheel_price", 2.0))
    await message.answer(f"🎡 **Omadli G'ildirak**\n\nUrinish narxi: **{price} {CURRENCY_SYMBOL}**\n\nYutuqlar: 0.5 dan 10 {CURRENCY_SYMBOL} gacha yoki omad kutilmoqda!", 
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Aylantirish", callback_data="spin_wheel")]]))

@dp.callback_query(F.data == "spin_wheel")
async def spin_process(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    balance = db_query("SELECT balance FROM users WHERE id = ?", (user_id,), fetchone=True)[0]
    price = float(get_config("wheel_price", 2.0))
    
    if balance < price:
        return await callback.answer("❌ Mablag' yetarli emas!", show_alert=True)
    
    # Tikish
    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (price, user_id), commit=True)
    
    # Tasodifiy yutuq (Ehtimollik: 60% kichik, 30% o'rta, 10% katta)
    prizes = [0.5, 1.0, 1.5, 2.0, 5.0, 10.0, 0.0]
    weights = [30, 25, 15, 15, 10, 3, 2]
    win = random.choices(prizes, weights=weights)[0]
    
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (win, user_id), commit=True)
    add_transaction(user_id, win - price, "WHEEL", "G'ildirak o'yini")
    
    await callback.message.edit_text(f"🎰 G'ildirak aylanmoqda...\n\nNatija: **{win} {CURRENCY_SYMBOL}**! " + 
                                     ("🎊" if win > price else "😢"), reply_markup=None)

# --- TRANZAKSIYALAR TARIXI ---
@dp.message(F.text == "📜 Tarix")
async def show_history(message: types.Message):
    txs = db_query("SELECT amount, type, timestamp FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 10", 
                   (message.from_user.id,), fetchall=True)
    if not txs:
        return await message.answer("Sizda hali tranzaksiyalar mavjud emas.")
    
    res = "📜 **Oxirgi 10 ta harakat:**\n\n"
    for amt, ttype, date in txs:
        sign = "+" if amt > 0 else ""
        res += f"▫️ `{date[5:16]}` | **{sign}{amt}** | {ttype}\n"
    await message.answer(res, parse_mode="Markdown")

# --- PUL YECHISH (WITHDRAWAL) ---
@dp.message(F.text == "💳 Hisob")
async def account_menu(message: types.Message):
    bal = db_query("SELECT balance FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)[0]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ To'ldirish", callback_data="fill_bal"),
         InlineKeyboardButton(text="➖ Pul yechish", callback_data="withdraw_bal")]
    ])
    await message.answer(f"💰 Balansingiz: **{bal:.2f} {CURRENCY_SYMBOL}**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "withdraw_bal")
async def withdraw_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("💸 Qancha yechmoqchisiz? (Minimal: 10)")
    await state.set_state(WithdrawState.amount)

@dp.message(WithdrawState.amount)
async def withdraw_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        bal = db_query("SELECT balance FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)[0]
        if amount < 10 or amount > bal:
            return await message.answer("❌ Mablag' yetarli emas yoki minimal miqdordan kam.")
        
        await state.update_data(amount=amount)
        await message.answer("💳 Karta raqamingiz va ism sharifingizni yozing:")
        await state.set_state(WithdrawState.details)
    except: await message.answer("Faqat raqam yozing!")

@dp.message(WithdrawState.details)
async def withdraw_final(message: types.Message, state: FSMContext):
    data = await state.get_data()
    amount = data['amount']
    details = message.text
    
    # Pulni bloklash
    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (amount, message.from_user.id), commit=True)
    db_query("INSERT INTO withdrawals (user_id, amount, details) VALUES (?, ?, ?)", 
             (message.from_user.id, amount, details), commit=True)
    
    # Adminga xabar
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"adm_wd_approve_{message.from_user.id}_{amount}"),
         InlineKeyboardButton(text="❌ Rad etish", callback_data=f"adm_wd_reject_{message.from_user.id}_{amount}")]
    ])
    await bot.send_message(ADMIN_ID, f"🔔 **Yangi Pul Yechish So'rovi!**\n\nUser: `{message.from_user.id}`\nMiqdor: {amount} {CURRENCY_SYMBOL}\nRekvizit: {details}", 
                           reply_markup=admin_kb)
    
    await message.answer("✅ So'rov adminga yuborildi. Tekshirilgandan so'ng pulingiz tushib keladi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

# --- ADMIN ANALITIKA ---
@dp.callback_query(F.data == "adm_stats")
async def admin_stats(callback: types.CallbackQuery):
    total_users = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    active_24 = db_query("SELECT COUNT(*) FROM users WHERE joined_at > datetime('now', '-1 day')", fetchone=True)[0]
    total_bal = db_query("SELECT SUM(balance) FROM users", fetchone=True)[0]
    
    msg = (f"📊 **Bot Analitikasi**\n\n"
           f"👥 Jami foydalanuvchilar: {total_users}\n"
           f"📈 Oxirgi 24 soatda: {active_24}\n"
           f"💰 Umumiy foydalanuvchi balanslari: {total_bal:.2f} {CURRENCY_SYMBOL}")
    await callback.message.edit_text(msg, reply_markup=None)

# --- ADMIN APPROVAL HANDLERS ---
@dp.callback_query(F.data.startswith("adm_wd_"))
async def admin_withdraw_decision(callback: types.CallbackQuery):
    _, _, action, uid, amt = callback.data.split("_")
    uid, amt = int(uid), float(amt)
    
    if action == "approve":
        db_query("UPDATE withdrawals SET status = 'paid' WHERE user_id = ? AND amount = ? AND status = 'pending'", (uid, amt), commit=True)
        add_transaction(uid, -amt, "WITHDRAW", "Muvaffaqiyatli yechildi")
        await bot.send_message(uid, f"✅ Pul yechish so'rovingiz tasdiqlandi! {amt} {CURRENCY_SYMBOL} o'tkazib berildi.")
    else:
        db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, uid), commit=True)
        db_query("UPDATE withdrawals SET status = 'rejected' WHERE user_id = ? AND amount = ? AND status = 'pending'", (uid, amt), commit=True)
        await bot.send_message(uid, f"❌ Pul yechish so'rovingiz rad etildi. Mablag' balansingizga qaytarildi.")
    
    await callback.message.edit_text(f"Bajarildi: {action.upper()}")

# --- ASOSIY ISHGA TUSHIRISH ---
async def main():
    print("Bot ishga tushmoqda...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

