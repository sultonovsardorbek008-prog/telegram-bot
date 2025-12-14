import os
import logging
import sqlite3
import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton, 
                           InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove)

# --- KONFIGURATSIYA (SOZLAMALAR) ---
# --- KONFIGURATSIYA (ENV ORQALI) ---
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

DB_NAME = os.getenv("DB_NAME", "bot_database_v4_pro.db")

CURRENCY_NAME = os.getenv("CURRENCY_NAME", "SULTANCOIN")
CURRENCY_SYMBOL = os.getenv("CURRENCY_SYMBOL", "SC")

CARD_UZS = os.getenv("CARD_UZS")
CARD_NAME = os.getenv("CARD_NAME")
CARD_VISA = os.getenv("CARD_VISA")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- BAZA BILAN ISHLASH UCHUN XAVFSIZ YORDAMCHI FUNKSIYA ---
# Bu funksiya har safar bazaga yangi ulanish ochadi va yopadi.
# Bu global kursor muammosini hal qiladi.
def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            
            if commit:
                conn.commit()
                
            if fetchone:
                return cursor.fetchone()
            if fetchall:
                return cursor.fetchall()
            return None
    except Exception as e:
        print(f"Bazada xatolik: {e}")
        return None

# --- BAZANI YARATISH ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                          (id INTEGER PRIMARY KEY, 
                           balance INTEGER DEFAULT 0,
                           is_pro INTEGER DEFAULT 0,
                           pro_expire_date TEXT)''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS config 
                          (key TEXT PRIMARY KEY, value TEXT)''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS projects 
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                           name TEXT, 
                           price INTEGER, 
                           file_id TEXT)''')
        conn.commit()

# Dastur ishga tushganda bazani tekshirish
init_db()

# --- KONFIGURATSIYA FUNKSIYALARI ---
def get_config(key, default_value):
    res = db_query("SELECT value FROM config WHERE key = ?", (key,), fetchone=True)
    if res:
        return res[0]
    else:
        db_query("INSERT INTO config (key, value) VALUES (?, ?)", (key, str(default_value)), commit=True)
        return default_value

def set_config(key, value):
    db_query("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)), commit=True)

# --- DINAMIK QIYMATLAR ---
def get_prices():
    return {
        "web": int(get_config("price_web", 50)),
        "apk": int(get_config("price_apk", 100)),
        "bot": int(get_config("price_bot", 30))
    }

def get_pro_prices():
    return {
        "1_month": int(get_config("pro_1_month", 20)),
        "1_year": int(get_config("pro_1_year", 110))
    }

def get_coin_rates():
    return {
        "uzs": int(get_config("rate_uzs", 5000)),
        "usd": float(get_config("rate_usd", 0.5))
    }

# --- STATES (HOLATLAR) ---
class OrderService(StatesGroup):
    waiting_for_desc = State()

class FillBalance(StatesGroup):
    choosing_currency = State()
    waiting_for_amount = State()
    waiting_for_receipt = State()

class MoneyTransfer(StatesGroup):
    waiting_for_recipient_id = State()
    waiting_for_amount = State()
    confirming_transfer = State()

class AdminState(StatesGroup):
    main = State()
    editing_balance_id = State()
    editing_balance_amount = State()
    add_project_name = State()
    add_project_price = State()
    add_project_file = State()
    change_price_value = State()

# --- YORDAMCHI FUNKSIYALAR ---
def check_pro_status(user_id):
    data = db_query("SELECT is_pro, pro_expire_date FROM users WHERE id = ?", (user_id,), fetchone=True)
    if data and data[0] == 1:
        if data[1]: 
            expire_date = datetime.datetime.strptime(data[1], "%Y-%m-%d %H:%M:%S")
            if datetime.datetime.now() > expire_date:
                db_query("UPDATE users SET is_pro = 0, pro_expire_date = NULL WHERE id = ?", (user_id,), commit=True)
                return False
        return True
    return False

def get_user_balance(user_id):
    res = db_query("SELECT balance FROM users WHERE id = ?", (user_id,), fetchone=True)
    return res[0] if res else 0

def user_exists(user_id):
    res = db_query("SELECT id FROM users WHERE id = ?", (user_id,), fetchone=True)
    return res is not None

# --- KEYBOARDS ---
def main_menu():
    kb = [
        [KeyboardButton(text="👤 Kabinet"), KeyboardButton(text="💎 PRO Status")],
        [KeyboardButton(text="🛠 Buyurtma berish"), KeyboardButton(text="📂 Tayyor Loyihalar")],
        [KeyboardButton(text="💰 Hisobni to'ldirish")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def cancel_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚫 Bekor qilish")]], resize_keyboard=True)

def admin_main_menu():
    kb = [
        [InlineKeyboardButton(text="➕ Loyiha Qo'shish", callback_data="admin_add_proj"),
         InlineKeyboardButton(text="✏️ Balansni Tahrirlash", callback_data="admin_edit_balance")],
        [InlineKeyboardButton(text="💵 Narxlarni O'zgartirish", callback_data="admin_change_prices")],
        [InlineKeyboardButton(text="🗑 Loyihani O'chirish", callback_data="admin_del_proj_list")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def services_menu():
    prices = get_prices()
    kb = [
        [InlineKeyboardButton(text=f"🌐 Web Sayt ({prices['web']} {CURRENCY_SYMBOL})", callback_data="buy_web")],
        [InlineKeyboardButton(text=f"📱 APK Yaratish ({prices['apk']} {CURRENCY_SYMBOL})", callback_data="buy_apk")],
        [InlineKeyboardButton(text=f"🤖 Telegram Bot ({prices['bot']} {CURRENCY_SYMBOL})", callback_data="buy_bot")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def ready_projects_menu(is_pro):
    kb = []
    projects = db_query("SELECT id, name, price FROM projects", fetchall=True)
    
    if not projects:
        return None 

    for proj in projects:
        pid, name, price = proj
        price_text = "TEKIN (PRO)" if is_pro else f"{price} {CURRENCY_SYMBOL}"
        kb.append([InlineKeyboardButton(text=f"{name} - {price_text}", callback_data=f"getproj_{pid}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- ADMIN PANEL LOGIKASI ---

@dp.message(Command("admin"))
async def admin_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return 
    await message.answer("🔐 **Admin Panelga Xush Kelibsiz!**", reply_markup=admin_main_menu(), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_edit_balance")
async def admin_edit_balance_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Foydalanuvchi ID raqamini kiriting:", reply_markup=cancel_keyboard())
    await state.set_state(AdminState.editing_balance_id)
    await callback.answer()

@dp.message(AdminState.editing_balance_id)
async def admin_get_user_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("ID raqam bo'lishi kerak!")
    user_id = int(message.text)
    
    current = get_user_balance(user_id)
    await message.answer(f"👤 User: {user_id}\nJoriy balans: {current} {CURRENCY_SYMBOL}\n\nYangi balansni kiriting (Mavjudini o'chirib yozadi!):")
    
    await state.update_data(target_id=user_id)
    await state.set_state(AdminState.editing_balance_amount)

@dp.message(AdminState.editing_balance_amount)
async def admin_set_balance(message: types.Message, state: FSMContext):
    try:
        amount = int(message.text)
    except ValueError:
        return await message.answer("Raqam kiriting!")
    
    data = await state.get_data()
    target_id = data['target_id']
    
    db_query("INSERT OR IGNORE INTO users (id, balance) VALUES (?, 0)", (target_id,), commit=True)
    db_query("UPDATE users SET balance = ? WHERE id = ?", (amount, target_id), commit=True)
    
    await message.answer(f"✅ User {target_id} balansi {amount} {CURRENCY_SYMBOL} ga o'zgartirildi.", reply_markup=main_menu())
    await state.clear()

# --- ADMIN: LOYIHA QO'SHISH ---
@dp.callback_query(F.data == "admin_add_proj")
async def admin_add_proj_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🆕 Yangi loyiha nomini kiriting:", reply_markup=cancel_keyboard())
    await state.set_state(AdminState.add_project_name)
    await callback.answer()

@dp.message(AdminState.add_project_name)
async def admin_proj_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(f"Nomi: {message.text}\n\nEndi narxini kiriting ({CURRENCY_SYMBOL}):")
    await state.set_state(AdminState.add_project_price)

@dp.message(AdminState.add_project_price)
async def admin_proj_price(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Narx raqam bo'lishi kerak!")
    await state.update_data(price=int(message.text))
    await message.answer("Endi faylni yoki havolani yuboring (File yoki Text):")
    await state.set_state(AdminState.add_project_file)

@dp.message(AdminState.add_project_file)
async def admin_proj_file(message: types.Message, state: FSMContext):
    file_data = ""
    if message.document: file_data = message.document.file_id
    elif message.video: file_data = message.video.file_id
    elif message.photo: file_data = message.photo[-1].file_id
    elif message.text: file_data = message.text
    else: return await message.answer("Fayl yoki matn yuboring!")
        
    data = await state.get_data()
    db_query("INSERT INTO projects (name, price, file_id) VALUES (?, ?, ?)", 
             (data['name'], data['price'], file_data), commit=True)
    
    await message.answer("✅ Loyiha qo'shildi!", reply_markup=main_menu())
    await state.clear()

# --- ADMIN: LOYIHA O'CHIRISH ---
@dp.callback_query(F.data == "admin_del_proj_list")
async def admin_del_list(callback: types.CallbackQuery):
    projects = db_query("SELECT id, name FROM projects", fetchall=True)
    kb = []
    if projects:
        for p in projects:
            kb.append([InlineKeyboardButton(text=f"🗑 {p[1]}", callback_data=f"admin_del_p_{p[0]}")])
    kb.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_back")])
    await callback.message.edit_text("O'chirmoqchi bo'lgan loyihani tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("admin_del_p_"))
async def admin_delete_proj(callback: types.CallbackQuery):
    pid = callback.data.split("_")[-1]
    db_query("DELETE FROM projects WHERE id = ?", (pid,), commit=True)
    await callback.answer("Loyiha o'chirildi!", show_alert=True)
    await admin_del_list(callback)

# --- ADMIN: NARXLAR ---
@dp.callback_query(F.data == "admin_change_prices")
async def admin_prices_menu(callback: types.CallbackQuery):
    prices = get_prices()
    rates = get_coin_rates()
    kb = [
        [InlineKeyboardButton(text=f"Web ({prices['web']})", callback_data="set_price_web"),
         InlineKeyboardButton(text=f"APK ({prices['apk']})", callback_data="set_price_apk")],
        [InlineKeyboardButton(text=f"Bot ({prices['bot']})", callback_data="set_price_bot")],
        [InlineKeyboardButton(text=f"1 SC = {rates['uzs']} so'm", callback_data="set_rate_uzs")],
        [InlineKeyboardButton(text=f"1 USD = ? SC", callback_data="set_rate_usd")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_back")]
    ]
    await callback.message.edit_text("Narxni tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("set_"))
async def admin_ask_new_price(callback: types.CallbackQuery, state: FSMContext):
    key_map = {"set_price_web": "price_web", "set_price_apk": "price_apk", "set_price_bot": "price_bot", "set_rate_uzs": "rate_uzs", "set_rate_usd": "rate_usd"}
    config_key = key_map.get(callback.data)
    await state.update_data(config_key=config_key)
    await callback.message.answer(f"Yangi qiymatni kiriting ({config_key}):", reply_markup=cancel_keyboard())
    await state.set_state(AdminState.change_price_value)
    await callback.answer()

@dp.message(AdminState.change_price_value)
async def admin_save_new_price(message: types.Message, state: FSMContext):
    try:
        value = float(message.text) # Float deb tekshiramiz
    except:
        return await message.answer("Raqam kiriting.")
    data = await state.get_data()
    set_config(data['config_key'], value)
    await message.answer(f"✅ Yangilandi!", reply_markup=main_menu())
    await state.clear()

@dp.callback_query(F.data == "admin_back")
async def back_to_admin(callback: types.CallbackQuery):
    await callback.message.edit_text("Admin Panel:", reply_markup=admin_main_menu())

# --- USER SIDE HANDLERS ---

@dp.message(F.text == "🚫 Bekor qilish")
async def cancel_process(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=main_menu())

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    db_query("INSERT OR IGNORE INTO users (id, balance) VALUES (?, ?)", (message.from_user.id, 0), commit=True)
    await message.answer(f"ASOSIY MENYU", 
                         reply_markup=main_menu(), parse_mode="Markdown")

@dp.message(F.text == "👤 Kabinet")
async def kabinet(message: types.Message):
    is_pro = check_pro_status(message.from_user.id)
    balance = get_user_balance(message.from_user.id)
    
    res = db_query("SELECT pro_expire_date FROM users WHERE id = ?", (message.from_user.id,), fetchone=True)
    expire = res[0] if is_pro and res else "Yo'q"
    status_text = "💎 **PRO**" if is_pro else "👤 **Oddiy**"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💸 Pul o'tkazish", callback_data="transfer_money")]])
    msg = (f"🆔 ID: `{message.from_user.id}`\n💰 Balans: **{balance} {CURRENCY_SYMBOL}**\n📊 Status: {status_text}\n")
    if is_pro: msg += f"⏳ Tugash: `{expire}`"
    
    await message.answer(msg, reply_markup=kb, parse_mode="Markdown")

# --- PUL O'TKAZISH ---
@dp.callback_query(F.data == "transfer_money")
async def start_transfer(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Qabul qiluvchi ID raqamini yozing:", reply_markup=cancel_keyboard())
    await state.set_state(MoneyTransfer.waiting_for_recipient_id)
    await callback.answer()

@dp.message(MoneyTransfer.waiting_for_recipient_id)
async def process_recipient_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Faqat raqam!")
    recipient_id = int(message.text)
    
    if recipient_id == message.from_user.id: return await message.answer("O'zingizga pul o'tkaza olmaysiz!")
    if not user_exists(recipient_id): return await message.answer("Bunday foydalanuvchi topilmadi!")
    
    await state.update_data(recipient_id=recipient_id)
    await message.answer(f"Summani kiriting ({CURRENCY_SYMBOL}):")
    await state.set_state(MoneyTransfer.waiting_for_amount)

@dp.message(MoneyTransfer.waiting_for_amount)
async def process_transfer_amount(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Butun son kiriting!")
    amount = int(message.text)
    if amount <= 0: return await message.answer("Musbat son kiriting!")
        
    commission = int(amount * 0.01)
    if commission < 1: commission = 1
    total = amount + commission
    
    if get_user_balance(message.from_user.id) < total:
        return await message.answer(f"Mablag' yetarli emas! Jami kerak: {total}")

    await state.update_data(amount=amount, total_deduct=total)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha", callback_data="confirm_yes"), InlineKeyboardButton(text="❌ Yo'q", callback_data="confirm_no")]
    ])
    await message.answer(f"Yuborilmoqda: {amount}\nKomissiya: {commission}\nJami: {total}\nTasdiqlaysizmi?", reply_markup=kb)
    await state.set_state(MoneyTransfer.confirming_transfer)

@dp.callback_query(MoneyTransfer.confirming_transfer, F.data == "confirm_yes")
async def execute_transfer(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sid, rid, amt, tot = callback.from_user.id, data['recipient_id'], data['amount'], data['total_deduct']
    
    if get_user_balance(sid) < tot:
        await callback.answer("Mablag' yetarli emas!", show_alert=True)
        return await state.clear()

    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (tot, sid), commit=True)
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, rid), commit=True)
    
    await callback.message.edit_text(f"✅ O'tkazildi!")
    try: await bot.send_message(rid, f"📥 +{amt} {CURRENCY_SYMBOL} kelib tushdi! (ID: {sid})")
    except: pass
    await state.clear()

@dp.callback_query(MoneyTransfer.confirming_transfer, F.data == "confirm_no")
async def cancel_transfer(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("❌ Bekor qilindi.")
    await state.clear()

# --- HISOB TO'LDIRISH (MUAMMO BO'LGAN QISM) ---
@dp.message(F.text == "💰 Hisobni to'ldirish")
async def topup_start(message: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🇺🇿 UZS (Humo/Uzcard)"), KeyboardButton(text="🇺🇸 USD (Visa)")],
        [KeyboardButton(text="🚫 Bekor qilish")]
    ], resize_keyboard=True)
    await message.answer("Valyutani tanlang:", reply_markup=kb)
    await state.set_state(FillBalance.choosing_currency)

@dp.message(FillBalance.choosing_currency)
async def topup_currency(message: types.Message, state: FSMContext):
    rates = get_coin_rates()
    if "UZS" in message.text:
        curr, rate, card = "UZS", rates['uzs'], f"Humo/Uzcard: `{CARD_UZS}`"
        rate_text = f"1 SC = {rate} so'm"
    elif "USD" in message.text:
        curr, rate, card = "USD", rates['usd'], f"Visa: `{CARD_VISA}`"
        rate_text = f"1 USD = {int(1/rate) if rate>0 else 0} SC"
    else: return await message.answer("Tanlang!")

    await state.update_data(currency=curr, rate=rate)
    await message.answer(f"{card}\n{CARD_NAME}\n\nKurs: {rate_text}\nQancha **{CURRENCY_NAME}** olmoqchisiz?", 
                         reply_markup=cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(FillBalance.waiting_for_amount)

@dp.message(FillBalance.waiting_for_amount)
async def topup_amount(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Raqam yozing!")
    coins = int(message.text)
    data = await state.get_data()
    
    pay_val = coins * data['rate']
    pay_text = f"{pay_val} so'm" if data['currency'] == "UZS" else f"{pay_val} $"
    
    await state.update_data(coins=coins, total_pay=pay_text)
    await message.answer(f"To'lov: **{pay_text}**\nChekni yuboring:", reply_markup=cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(FillBalance.waiting_for_receipt)

@dp.message(FillBalance.waiting_for_receipt, F.photo)
async def topup_receipt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"pay_approve:{message.from_user.id}:{data['coins']}"),
         InlineKeyboardButton(text="❌ Rad etish", callback_data=f"pay_reject:{message.from_user.id}")]
    ])
    await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id, 
                         caption=f"🔔 **To'lov**\nUser: `{message.from_user.id}`\nMiqdor: {data['coins']} SC\nTo'lov: {data['total_pay']}", 
                         reply_markup=kb, parse_mode="Markdown")
    await message.answer("Adminga yuborildi, kuting...", reply_markup=main_menu())
    await state.clear()

# --- TUZATILGAN QISM: TO'LOVNI TASDIQLASH ---
@dp.callback_query(F.data.startswith("pay_approve:"))
async def approve_payment(callback: types.CallbackQuery):
    try:
        # Ma'lumotlarni ajratib olamiz
        parts = callback.data.split(":")
        user_id = int(parts[1]) # MUHIM: int() ga o'tkazish
        amount = int(parts[2])  # MUHIM: int() ga o'tkazish

        # Avval user bazada borligini tekshiramiz, bo'lmasa yaratamiz
        db_query("INSERT OR IGNORE INTO users (id, balance) VALUES (?, 0)", (user_id,), commit=True)
        
        # Balansni yangilash
        db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id), commit=True)
        
        await bot.send_message(user_id, f"✅ To'lov tasdiqlandi! +{amount} {CURRENCY_SYMBOL} qo'shildi.")
        await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ TASDIQLANDI")
        
    except Exception as e:
        await callback.answer(f"Xatolik: {e}", show_alert=True)
        logging.error(f"Approval Error: {e}")

@dp.callback_query(F.data.startswith("pay_reject:"))
async def reject_payment(callback: types.CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    await bot.send_message(user_id, "❌ To'lov rad etildi.")
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ RAD ETILDI")

# --- PRO STATUS ---
@dp.message(F.text == "💎 PRO Status")
async def pro_handler(message: types.Message):
    if check_pro_status(message.from_user.id):
        await message.answer("Sizda allaqachon PRO status mavjud!")
    else:
        prices = get_pro_prices()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"1 Oy - {prices['1_month']} SC", callback_data="buypro_1_month")],
            [InlineKeyboardButton(text=f"1 Yil - {prices['1_year']} SC", callback_data="buypro_1_year")]
        ])
        await message.answer("💎 PRO Status sotib olish:", reply_markup=kb)

@dp.callback_query(F.data.startswith("buypro_"))
async def buy_pro(callback: types.CallbackQuery):
    plan = callback.data.replace("buypro_", "")
    price = get_pro_prices()[plan]
    days = 30 if plan == "1_month" else 365
    
    if get_user_balance(callback.from_user.id) < price:
        return await callback.answer("Mablag' yetarli emas!", show_alert=True)

    expire_str = (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    db_query("UPDATE users SET balance = balance - ?, is_pro = 1, pro_expire_date = ? WHERE id = ?", 
             (price, expire_str, callback.from_user.id), commit=True)
    
    await callback.message.answer(f"🎉 PRO status faollashdi! Tugash: {expire_str}")
    await callback.message.delete()

# --- TAYYOR LOYIHALAR ---
@dp.message(F.text == "📂 Tayyor Loyihalar")
async def ready_projects(message: types.Message):
    kb = ready_projects_menu(check_pro_status(message.from_user.id))
    if not kb: await message.answer("Loyihalar yo'q.")
    else: await message.answer("📂 Loyihani tanlang:", reply_markup=kb)

@dp.callback_query(F.data.startswith("getproj_"))
async def get_project(callback: types.CallbackQuery):
    pid = callback.data.split("_")[1]
    res = db_query("SELECT name, price, file_id FROM projects WHERE id = ?", (pid,), fetchone=True)
    if not res: return await callback.answer("Loyiha topilmadi!", show_alert=True)

    name, price, file_id = res
    uid = callback.from_user.id
    is_pro = check_pro_status(uid)

    if not is_pro:
        if get_user_balance(uid) < price: return await callback.answer("Pul yetmaydi!", show_alert=True)
        db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (price, uid), commit=True)
        msg_text = f"✅ Sotib olindi: -{price}"
    else:
        msg_text = "💎 PRO bilan bepul olindi!"

    await callback.message.answer(msg_text)
    if file_id.startswith("http"): await callback.message.answer(f"📥 {name}\nLINK: {file_id}")
    else: 
        try: await callback.message.answer_document(file_id, caption=f"📥 {name}")
        except: await callback.message.answer(f"File ID xato: {file_id}")
    await callback.answer()

# --- BUYURTMA ---
@dp.message(F.text == "🛠 Buyurtma berish")
async def custom_services(message: types.Message):
    await message.answer("Xizmat turini tanlang:", reply_markup=services_menu())

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy_service(callback: types.CallbackQuery, state: FSMContext):
    service = callback.data.split("_")[1]
    price = get_prices().get(service, 0)
    
    if get_user_balance(callback.from_user.id) < price:
        await callback.answer("Mablag' yetarli emas!", show_alert=True)
    else:
        await state.update_data(chosen_service=service, price=price)
        await callback.message.answer("Texnik topshiriqni yozing:", reply_markup=cancel_keyboard())
        await state.set_state(OrderService.waiting_for_desc)
    await callback.answer()

@dp.message(OrderService.waiting_for_desc)
async def finish_order(message: types.Message, state: FSMContext):
    data = await state.get_data()
    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (data['price'], message.from_user.id), commit=True)
    
    await bot.send_message(ADMIN_ID, f"🚀 BUYURTMA!\nUser: {message.from_user.id}\nXizmat: {data['chosen_service']}\nMatn: {message.text}")
    await message.answer("Buyurtma qabul qilindi!", reply_markup=main_menu())
    await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot to'xtatildi.")
