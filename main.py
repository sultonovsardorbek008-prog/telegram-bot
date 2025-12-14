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

# --- KONFIGURATSIYA ---
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DB_NAME = os.getenv("DB_NAME", "bot_database_uzcoin_pro.db")

# REBRANDING: UzCoin
CURRENCY_NAME = os.getenv("CURRENCY_NAME", "UzCoin")
CURRENCY_SYMBOL = os.getenv("CURRENCY_SYMBOL", "🪙 UZC")

# Karta ma'lumotlari (Environmentdan yoki default)
CARD_UZS = os.getenv("CARD_UZS", "8600 0000 0000 0000")
CARD_NAME = os.getenv("CARD_NAME", "Bot Admin")
CARD_VISA = os.getenv("CARD_VISA", "4000 0000 0000 0000")

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

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                          (id INTEGER PRIMARY KEY, 
                           balance REAL DEFAULT 0.0,
                           status_level INTEGER DEFAULT 0,
                           status_expire TEXT,
                           referrer_id INTEGER,
                           joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''') # joined_at qo'shildi

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
    
    # Migratsiyalar (xatolik bo'lmasligi uchun)
    columns = ["description", "media_id", "media_type"]
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

# --- SOZLAMALAR ---
def get_config(key, default_value):
    res = db_query("SELECT value FROM config WHERE key = ?", (key,), fetchone=True)
    if res: return res[0]
    db_query("INSERT INTO config (key, value) VALUES (?, ?)", (key, str(default_value)), commit=True)
    return str(default_value)

def set_config(key, value):
    db_query("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)), commit=True)

# Status darajalari: 0=Start, 1=Silver, 2=Gold, 3=Platinum (Rebranding)
STATUS_DATA = {
    0: {"name": "👤 Start", "limit": 30, "price_month": 0},
    1: {"name": "🥈 Silver", "limit": 100, "desc": "✅ Clicker (Pul ishlash)\n✅ Limit: 100 UZC"},
    2: {"name": "🥇 Gold", "limit": 1000, "desc": "✅ Loyihalar 50% chegirma\n✅ Limit: 1000 UZC"},
    3: {"name": "💎 Platinum", "limit": 100000, "desc": "✅ Hammasi TEKIN (Xizmatlar ham)\n✅ Limit: Cheksiz"}
}

def get_dynamic_prices():
    return {
        "web": float(get_config("price_web", 50.0)),
        "apk": float(get_config("price_apk", 100.0)),
        "bot": float(get_config("price_bot", 30.0)),
        "ref_reward": float(get_config("ref_reward", 1.0)),
        "click_reward": float(get_config("click_reward", 0.05)),
        # Status narxlari (Oyiga)
        "pro_price": float(get_config("status_price_1", 20.0)),  # Silver
        "prem_price": float(get_config("status_price_2", 50.0)), # Gold
        "king_price": float(get_config("status_price_3", 200.0)) # Platinum
    }

def get_coin_rates():
    return {
        "uzs": float(get_config("rate_uzs", 1000.0)), # 1 UZC = 1000 so'm
        "usd": float(get_config("rate_usd", 0.1))
    }

def get_text(key, default):
    return get_config(f"text_{key}", default).replace("\\n", "\n")

def get_user_data(user_id):
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
    change_config_value = State()
    edit_text_key = State()
    edit_text_val = State()
    broadcast_msg = State() # Yangi xususiyat: Xabar tarqatish

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
    """
    Har qanday holatda (State) '🚫 Bekor qilish' bosilsa, shu yerga tushadi.
    Bu kodni eng tepaga qo'yish shart.
    """
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
                            f"🤖 **UzCoin Official Bot**ga xush kelibsiz.\n"
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

# --- STATUSLAR DOKONI ---
@dp.message(F.text == "🌟 Statuslar")
async def status_shop(message: types.Message):
    await show_status_menu(message)

@dp.callback_query(F.data == "open_status_shop")
async def cb_status_shop(callback: types.CallbackQuery):
    await show_status_menu(callback.message)

async def show_status_menu(message: types.Message):
    prices = get_dynamic_prices()
    kb = [
        [InlineKeyboardButton(text=f"🥈 Silver ({prices['pro_price']} UZC)", callback_data="buy_status_1")],
        [InlineKeyboardButton(text=f"🥇 Gold ({prices['prem_price']} UZC)", callback_data="buy_status_2")],
        [InlineKeyboardButton(text=f"💎 Platinum ({prices['king_price']} UZC)", callback_data="buy_status_3")]
    ]
    
    info = (f"**🌟 STATUSLAR VA IMKONIYATLAR:**\n\n"
            f"🥈 **SILVER** - {prices['pro_price']} {CURRENCY_SYMBOL}\n{STATUS_DATA[1]['desc']}\n\n"
            f"🥇 **GOLD** - {prices['prem_price']} {CURRENCY_SYMBOL}\n{STATUS_DATA[2]['desc']}\n\n"
            f"💎 **PLATINUM** - {prices['king_price']} {CURRENCY_SYMBOL}\n{STATUS_DATA[3]['desc']}")
    
    if isinstance(message, types.CallbackQuery):
        await message.message.edit_text(info, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")
    else:
        await message.answer(info, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("buy_status_"))
async def buy_status_handler(callback: types.CallbackQuery):
    lvl = int(callback.data.split("_")[-1])
    prices = get_dynamic_prices()
    price_map = {1: prices['pro_price'], 2: prices['prem_price'], 3: prices['king_price']}
    cost = price_map[lvl]
    
    user = get_user_data(callback.from_user.id)
    
    if user['level'] >= lvl:
        return await callback.answer("Sizda allaqachon bu yoki undan yuqori status bor!", show_alert=True)
    
    if user['balance'] < cost:
        return await callback.answer(f"Hisobingizda mablag' yetarli emas! Kerak: {cost} {CURRENCY_SYMBOL}", show_alert=True)
    
    expire_date = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    
    db_query("UPDATE users SET balance = balance - ?, status_level = ?, status_expire = ? WHERE id = ?", 
             (cost, lvl, expire_date, callback.from_user.id), commit=True)
    
    await callback.message.delete()
    await callback.message.answer(f"🎉 **Tabriklaymiz!**\nSiz **{STATUS_DATA[lvl]['name']}** statusini sotib oldingiz!\nBarcha imkoniyatlar ochildi.")

# --- TOP USERLAR ---
@dp.message(F.text == "🏆 Top Foydalanuvchilar")
async def top_users(message: types.Message):
    users = db_query("SELECT id, balance, status_level FROM users ORDER BY balance DESC LIMIT 10", fetchall=True)
    msg = f"🏆 **{CURRENCY_NAME} MILLIONERLARI:**\n\n"
    
    for idx, (uid, bal, lvl) in enumerate(users, 1):
        badge = ""
        if lvl == 1: badge = "🥈"
        elif lvl == 2: badge = "🥇"
        elif lvl == 3: badge = "💎"
        
        # ID ni qisman yashirish (Professionalism)
        hidden_id = str(uid)[:4] + "..." + str(uid)[-2:]
        msg += f"{idx}. {badge} ID: `{hidden_id}` — **{format_num(bal)} {CURRENCY_SYMBOL}**\n"
        
    await message.answer(msg, parse_mode="Markdown")

# --- LOYIHALAR ---
@dp.message(F.text == "📂 Loyihalar")
async def show_projects(message: types.Message):
    projs = db_query("SELECT id, name FROM projects", fetchall=True)
    if not projs: return await message.answer("📂 Hozircha loyihalar yuklanmagan.")
    
    kb = []
    for pid, name in projs:
        kb.append([InlineKeyboardButton(text=f"📁 {name}", callback_data=f"view_proj_{pid}")])
    await message.answer("📥 Kerakli loyihani tanlang va yuklab oling:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("view_proj_"))
async def view_project(callback: types.CallbackQuery):
    pid = int(callback.data.split("_")[-1])
    proj = db_query("SELECT name, price, description, media_id, media_type FROM projects WHERE id = ?", (pid,), fetchone=True)
    
    if not proj: return await callback.answer("Loyiha topilmadi.", show_alert=True)
    name, price, desc, mid, mtype = proj
    
    user = get_user_data(callback.from_user.id)
    # Gold (2) statusga 50% chegirma, Platinum (3) ga tekin
    discount = 0
    if user['level'] == 2: discount = 0.5
    elif user['level'] == 3: discount = 1.0
    
    final_price = price * (1 - discount)
    
    price_text = f"{format_num(price)} {CURRENCY_SYMBOL}"
    if discount > 0:
        price_text = f"~{format_num(price)}~ -> **{format_num(final_price)} {CURRENCY_SYMBOL}**"
        if final_price == 0: price_text = "**TEKIN (Status)**"
    
    caption = f"📂 **{name}**\n\n📝 {desc}\n\n💰 Narxi: {price_text}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Sotib olish / Yuklash", callback_data=f"buy_proj_{pid}")]])
    
    try:
        if mid:
            if mtype == 'video':
                await callback.message.answer_video(mid, caption=caption, reply_markup=kb, parse_mode="Markdown")
            elif mtype == 'photo':
                await callback.message.answer_photo(mid, caption=caption, reply_markup=kb, parse_mode="Markdown")
            else:
                await callback.message.answer(caption, reply_markup=kb, parse_mode="Markdown")
        else:
            await callback.message.answer(caption, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        await callback.message.answer(caption, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_proj_"))
async def buy_project_process(callback: types.CallbackQuery):
    pid = int(callback.data.split("_")[-1])
    proj = db_query("SELECT price, file_id, name FROM projects WHERE id = ?", (pid,), fetchone=True)
    if not proj: return
    price, file_id, name = proj
    
    user = get_user_data(callback.from_user.id)
    discount = 0
    if user['level'] == 2: discount = 0.5
    elif user['level'] == 3: discount = 1.0
    
    final_price = price * (1 - discount)
    
    if user['balance'] < final_price:
        return await callback.answer(f"Mablag' yetarli emas! Kerak: {final_price} {CURRENCY_SYMBOL}", show_alert=True)
        
    if final_price > 0:
        db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (final_price, callback.from_user.id), commit=True)
        await callback.message.answer(f"✅ Xarid amalga oshdi! Hisobdan {format_num(final_price)} {CURRENCY_SYMBOL} yechildi.")
    
    await callback.message.answer_document(file_id, caption=f"✅ **{name}**\n\nFaylni muvaffaqiyatli yuklab oldingiz!")
    await callback.answer()

# --- XIZMATLAR ---
@dp.message(F.text == "🛠 Xizmatlar")
async def services_menu(message: types.Message):
    prices = get_dynamic_prices()
    kb = [
        [InlineKeyboardButton(text=f"🌐 Web Sayt ({prices['web']} UZC)", callback_data="serv_web")],
        [InlineKeyboardButton(text=f"📱 Android Ilova ({prices['apk']} UZC)", callback_data="serv_apk")],
        [InlineKeyboardButton(text=f"🤖 Telegram Bot ({prices['bot']} UZC)", callback_data="serv_bot")]
    ]
    await message.answer("🛠 **Buyurtma turini tanlang:**\nBiz sifatli IT xizmatlarini taklif etamiz.", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("serv_"))
async def service_select(callback: types.CallbackQuery, state: FSMContext):
    stype = callback.data.split("_")[1]
    prices = get_dynamic_prices()
    cost = prices.get(stype, 0)
    
    user = get_user_data(callback.from_user.id)
    # Platinum status (level 3) ga xizmatlar tekin
    if user['level'] == 3:
        cost = 0
        await callback.message.answer("💎 **Platinum Status:** Xizmat siz uchun bepul!")
    elif user['balance'] < cost:
        return await callback.answer(f"Hisobingizda {cost} {CURRENCY_SYMBOL} mavjud emas!", show_alert=True)
        
    await state.update_data(stype=stype, cost=cost)
    await callback.message.answer("📝 **Texnik topshiriqni yozib qoldiring:**\n(Loyiha haqida qisqacha ma'lumot)", reply_markup=cancel_kb())
    await state.set_state(OrderService.waiting_for_desc)

@dp.message(OrderService.waiting_for_desc)
async def service_confirm(message: types.Message, state: FSMContext):
    # Bekor qilish handler endi tepada, bu yerga kelmaydi agar bekor qilinsa
    data = await state.get_data()
    cost = data['cost']
    
    if cost > 0:
        db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (cost, message.from_user.id), commit=True)
        
    await bot.send_message(ADMIN_ID, 
                           f"🛠 **YANGI BUYURTMA**\n"
                           f"👤 User: `{message.from_user.id}`\n"
                           f"🧩 Tur: {data['stype']}\n"
                           f"💰 To'landi: {cost}\n"
                           f"📝 Matn: {message.text}")
    
    await message.answer("✅ **Buyurtmangiz qabul qilindi!**\nAdminlarimiz tez orada siz bilan bog'lanishadi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

# --- PUL O'TKAZISH ---
@dp.callback_query(F.data == "transfer_start")
async def transfer_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🆔 Qabul qiluvchining ID raqamini kiriting:", reply_markup=cancel_kb())
    await state.set_state(MoneyTransfer.waiting_for_recipient)

@dp.message(MoneyTransfer.waiting_for_recipient)
async def transfer_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): 
        return await message.answer("⚠️ Iltimos, faqat raqamlardan iborat ID kiriting!")
    
    rid = int(message.text)
    if rid == message.from_user.id:
        return await message.answer("⚠️ O'zingizga pul o'tkaza olmaysiz!")

    if not db_query("SELECT id FROM users WHERE id = ?", (rid,), fetchone=True):
        return await message.answer("⚠️ Bunday ID ga ega foydalanuvchi topilmadi!")
        
    await state.update_data(rid=rid)
    user = get_user_data(message.from_user.id)
    limit = STATUS_DATA[user['level']]['limit']
    
    await message.answer(f"💰 Qancha **{CURRENCY_NAME}** o'tkazmoqchisiz?\n"
                         f"Sizning balansingiz: {format_num(user['balance'])}\n"
                         f"O'tkazma limiti: {limit} UZC", reply_markup=cancel_kb())
    await state.set_state(MoneyTransfer.waiting_for_amount)

@dp.message(MoneyTransfer.waiting_for_amount)
async def transfer_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
    except ValueError:
        return await message.answer("⚠️ Iltimos, to'g'ri raqam kiriting (masalan: 10 yoki 5.5)!")
        
    if amount <= 0: return await message.answer("⚠️ Miqdor musbat bo'lishi kerak!")
    
    user = get_user_data(message.from_user.id)
    limit = STATUS_DATA[user['level']]['limit']
    
    if amount > limit:
        return await message.answer(f"⚠️ Limitdan oshdingiz! Sizning limit: {limit} {CURRENCY_SYMBOL}.\nLimitni oshirish uchun status sotib oling.")
        
    if user['balance'] < amount:
        return await message.answer("⚠️ Hisobingizda yetarli mablag' yo'q!")
        
    data = await state.get_data()
    rid = data['rid']
    
    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (amount, message.from_user.id), commit=True)
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, rid), commit=True)
    
    await message.answer(f"✅ **Muvaffaqiyatli!**\n`{rid}` ID ga {format_num(amount)} {CURRENCY_SYMBOL} o'tkazildi.", reply_markup=main_menu(message.from_user.id))
    try: await bot.send_message(rid, f"📥 **Sizga pul kelib tushdi!**\n+{format_num(amount)} {CURRENCY_SYMBOL}\nKimdan: ID `{message.from_user.id}`")
    except: pass
    await state.clear()

# --- ADMIN PANEL ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    kb = [
        [InlineKeyboardButton(text="➕ Loyiha Qo'shish", callback_data="adm_add_proj"),
         InlineKeyboardButton(text="💵 Narxlar va Sozlamalar", callback_data="adm_prices")],
        [InlineKeyboardButton(text="✏️ User Balansi", callback_data="adm_edit_bal"),
         InlineKeyboardButton(text="📢 Broadcast (Xabar)", callback_data="adm_broadcast")]
    ]
    await message.answer("🔐 **Admin Panel v3.0 (Pro)**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# Broadcast (Xabar tarqatish) - YANGI
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
            await asyncio.sleep(0.05) # Telegram limitlariga tushmaslik uchun
        except: pass
        
    await message.answer(f"✅ Xabar {count} ta foydalanuvchiga yetib bordi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

# Loyiha qo'shish
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

# Narxlar
@dp.callback_query(F.data == "adm_prices")
async def adm_prices_list(callback: types.CallbackQuery):
    p = get_dynamic_prices()
    kb = [
        [InlineKeyboardButton(text=f"Ref Bonus ({p['ref_reward']})", callback_data="set_ref_reward"),
         InlineKeyboardButton(text=f"Click ({p['click_reward']})", callback_data="set_click_reward")],
        [InlineKeyboardButton(text=f"Silver ({p['pro_price']})", callback_data="set_status_price_1"),
         InlineKeyboardButton(text=f"Gold ({p['prem_price']})", callback_data="set_status_price_2")],
        [InlineKeyboardButton(text=f"Platinum ({p['king_price']})", callback_data="set_status_price_3")]
    ]
    await callback.message.edit_text("⚙️ **Narxlarni sozlash:**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("set_"))
async def adm_set_val(callback: types.CallbackQuery, state: FSMContext):
    key = callback.data.replace("set_", "")
    await state.update_data(conf_key=key)
    await callback.message.answer(f"Yangi qiymatni yozing (Hozirgi: {key}):", reply_markup=cancel_kb())
    await state.set_state(AdminState.change_config_value)

@dp.message(AdminState.change_config_value)
async def adm_save_val(message: types.Message, state: FSMContext):
    try:
        val = float(message.text)
        data = await state.get_data()
        set_config(data['conf_key'], val)
        await message.answer("✅ Saqlandi!", reply_markup=main_menu(message.from_user.id))
        await state.clear()
    except:
        await message.answer("⚠️ Iltimos, raqam yozing.")

# --- HISOB TO'LDIRISH ---
@dp.message(F.text == "💳 Hisobni to'ldirish")
async def topup_start(message: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🇺🇿 UZS (Humo/Uzcard)"), KeyboardButton(text="🇺🇸 USD (Visa)")],
        [KeyboardButton(text="🚫 Bekor qilish")]
    ], resize_keyboard=True)
    await message.answer("To'lov valyutasini tanlang:", reply_markup=kb)
    await state.set_state(FillBalance.choosing_currency)

@dp.message(FillBalance.choosing_currency)
async def topup_curr(message: types.Message, state: FSMContext):
    rates = get_coin_rates()
    
    # Text checking
    if "UZS" in message.text:
        curr, rate, card, holder = "UZS", rates['uzs'], CARD_UZS, CARD_NAME
    elif "USD" in message.text:
        curr, rate, card, holder = "USD", rates['usd'], CARD_VISA, "Visa Holder"
    else: 
        return await message.answer("⚠️ Iltimos, tugmalardan birini tanlang!")
    
    await state.update_data(curr=curr, rate=rate)
    msg = (f"💳 **To'lov ma'lumotlari:**\n\n"
           f"Karta: `{card}`\n"
           f"Ega: **{holder}**\n\n"
           f"📈 Kurs: 1 {CURRENCY_SYMBOL} = {rate} {curr}\n"
           f"👇 Qancha **{CURRENCY_NAME}** sotib olmoqchisiz? (Raqam yozing)")
    
    await message.answer(msg, reply_markup=cancel_kb(), parse_mode="Markdown")
    await state.set_state(FillBalance.waiting_for_amount)

@dp.message(FillBalance.waiting_for_amount)
async def topup_amt(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text)
    except: return await message.answer("⚠️ Iltimos, raqam yozing!")
    
    if amt <= 0: return await message.answer("⚠️ Musbat son yozing!")

    data = await state.get_data()
    total = amt * data['rate']
    txt = f"{total:,.0f} so'm" if data['curr'] == "UZS" else f"{total:.2f} $"
    
    await state.update_data(amt=amt, txt=txt)
    await message.answer(f"💵 To'lov miqdori: **{txt}**\n\nTo'lovni amalga oshirib, chekni (skrinshot) shu yerga yuboring:", parse_mode="Markdown")
    await state.set_state(FillBalance.waiting_for_receipt)

@dp.message(FillBalance.waiting_for_receipt, F.photo)
async def topup_rec(message: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"p_ok:{message.from_user.id}:{data['amt']}"),
         InlineKeyboardButton(text="❌ Rad etish", callback_data=f"p_no:{message.from_user.id}")]
    ])
    
    # Adminga yuborish
    caption = (f"📥 **YANGI TO'LOV!**\n\n"
               f"👤 User: `{message.from_user.id}`\n"
               f"💎 So'raldi: {data['amt']} {CURRENCY_SYMBOL}\n"
               f"💵 To'lov: {data['txt']}")
    
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=caption, reply_markup=kb, parse_mode="Markdown")
    
    await message.answer("✅ Chek qabul qilindi! Admin tasdiqlagach hisobingiz to'ldiriladi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

@dp.callback_query(F.data.startswith("p_ok:"))
async def approve_pay(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    uid, amt = int(parts[1]), float(parts[2])
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, uid), commit=True)
    try:
        await bot.send_message(uid, f"✅ **To'lov tasdiqlandi!**\nHisobingizga +{amt} {CURRENCY_SYMBOL} qo'shildi.")
    except: pass
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ TASDIQLANDI")

@dp.callback_query(F.data.startswith("p_no:"))
async def reject_pay(callback: types.CallbackQuery):
    uid = int(callback.data.split(":")[1])
    try:
        await bot.send_message(uid, "❌ To'lovingiz rad etildi. Iltimos, admin bilan bog'laning.")
    except: pass
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ RAD ETILDI")

async def main():
    print(f"Bot ishga tushdi... {CURRENCY_NAME}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
