import os
import logging
import sqlite3
import datetime
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton, 
                           InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, FSInputFile)

# --- KONFIGURATSIYA ---
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DB_NAME = os.getenv("DB_NAME", "bot_database_v5_ultimate.db")

CURRENCY_NAME = os.getenv("CURRENCY_NAME", "SULTANCOIN")
CURRENCY_SYMBOL = os.getenv("CURRENCY_SYMBOL", "SC")

# Karta ma'lumotlari
CARD_UZS = os.getenv("CARD_UZS", "0000 0000 0000 0000")
CARD_NAME = os.getenv("CARD_NAME", "Ism Familiya")
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
        print(f"Bazada xatolik: {e}")
        return None

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # Users jadvali: balance REAL (float) ga o'zgardi, status_level qo'shildi
        cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                          (id INTEGER PRIMARY KEY, 
                           balance REAL DEFAULT 0.0,
                           status_level INTEGER DEFAULT 0,
                           status_expire TEXT,
                           referrer_id INTEGER)''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS config 
                          (key TEXT PRIMARY KEY, value TEXT)''')
        
        # Projects jadvali: description va media_id qo'shildi
        cursor.execute('''CREATE TABLE IF NOT EXISTS projects 
                          (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                           name TEXT, 
                           price REAL, 
                           description TEXT,
                           media_id TEXT,
                           media_type TEXT,
                           file_id TEXT)''')
        conn.commit()
        
    # Migratsiya (Eski bazani yangisiga moslash uchun ustunlar qo'shish)
    try:
        db_query("ALTER TABLE projects ADD COLUMN description TEXT", commit=True)
    except: pass
    try:
        db_query("ALTER TABLE projects ADD COLUMN media_id TEXT", commit=True)
    except: pass
    try:
        db_query("ALTER TABLE projects ADD COLUMN media_type TEXT", commit=True)
    except: pass
    try:
        db_query("ALTER TABLE users ADD COLUMN status_level INTEGER DEFAULT 0", commit=True)
    except: pass
    try:
        db_query("ALTER TABLE users ADD COLUMN referrer_id INTEGER", commit=True)
    except: pass

init_db()

# --- KONFIGURATSIYA VA STATUSLAR ---
def get_config(key, default_value):
    res = db_query("SELECT value FROM config WHERE key = ?", (key,), fetchone=True)
    if res: return res[0]
    db_query("INSERT INTO config (key, value) VALUES (?, ?)", (key, str(default_value)), commit=True)
    return str(default_value)

def set_config(key, value):
    db_query("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)), commit=True)

# Status darajalari: 0=Oddiy, 1=PRO, 2=Premium, 3=King
STATUS_DATA = {
    0: {"name": "👤 Oddiy", "limit": 30, "price_month": 0, "price_year": 0},
    1: {"name": "💎 PRO", "limit": 100, "desc": "✅ Clicker (Pul ishlash)\n✅ Limit: 100 SC"},
    2: {"name": "🔥 Premium", "limit": 1000, "desc": "✅ Loyihalar TEKIN\n✅ Limit: 1000 SC"},
    3: {"name": "👑 KING", "limit": 100000, "desc": "✅ Hammasi TEKIN (Xizmatlar ham)\n✅ Limit: 100,000 SC"}
}

def get_dynamic_prices():
    return {
        "web": float(get_config("price_web", 50.0)),
        "apk": float(get_config("price_apk", 100.0)),
        "bot": float(get_config("price_bot", 30.0)),
        "ref_reward": float(get_config("ref_reward", 1.0)),
        "click_reward": float(get_config("click_reward", 0.000001)),
        # Status narxlari (Oyiga)
        "pro_price": float(get_config("status_price_1", 20.0)),
        "prem_price": float(get_config("status_price_2", 50.0)),
        "king_price": float(get_config("status_price_3", 200.0))
    }

def get_coin_rates():
    return {
        "uzs": float(get_config("rate_uzs", 5000.0)),
        "usd": float(get_config("rate_usd", 0.5))
    }

def get_text(key, default):
    return get_config(f"text_{key}", default).replace("\\n", "\n")

# --- YORDAMCHI FUNKSIYALAR ---
def get_user_data(user_id):
    res = db_query("SELECT balance, status_level, status_expire FROM users WHERE id = ?", (user_id,), fetchone=True)
    if not res: return None
    
    balance, level, expire = res
    # Status muddatini tekshirish
    if expire:
        expire_dt = datetime.datetime.strptime(expire, "%Y-%m-%d %H:%M:%S")
        if datetime.datetime.now() > expire_dt:
            db_query("UPDATE users SET status_level = 0, status_expire = NULL WHERE id = ?", (user_id,), commit=True)
            level = 0
            expire = None
    return {"balance": balance, "level": level, "expire": expire}

def format_num(num):
    # 0.000001 kabi sonlarni to'g'ri chiqarish, ortiqcha nollarni olib tashlash
    return f"{float(num):.8f}".rstrip('0').rstrip('.')

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
    user = get_user_data(user_id)
    level = user['level'] if user else 0
    
    kb = [
        [KeyboardButton(text="👤 Kabinet"), KeyboardButton(text="🌟 Statuslar")],
        [KeyboardButton(text="🛠 Xizmatlar"), KeyboardButton(text="📂 Loyihalar")],
        [KeyboardButton(text="💰 Hisobni to'ldirish"), KeyboardButton(text="💸 Pul ishlash")],
        [KeyboardButton(text="🏆 Top Userlar")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚫 Bekor qilish")]], resize_keyboard=True)

# --- START VA REFERAL ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    referrer_id = None
    args = command.args
    
    # Referal tekshiruvi
    if args and args.isdigit():
        referrer_id = int(args)
        if referrer_id == message.from_user.id: referrer_id = None
    
    # Userni bazaga qo'shish
    if not db_query("SELECT id FROM users WHERE id = ?", (message.from_user.id,), fetchone=True):
        db_query("INSERT INTO users (id, balance, referrer_id) VALUES (?, 0.0, ?)", 
                 (message.from_user.id, referrer_id), commit=True)
        
        # Referal mukofoti
        if referrer_id:
            reward = get_dynamic_prices()['ref_reward']
            db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (reward, referrer_id), commit=True)
            try:
                await bot.send_message(referrer_id, f"🎉 Sizda yangi referal! +{format_num(reward)} {CURRENCY_SYMBOL}")
            except: pass

    welcome_text = get_text("welcome", f"👋 Assalomu alaykum, {message.from_user.full_name}!\nBotga xush kelibsiz.")
    await message.answer(welcome_text, reply_markup=main_menu(message.from_user.id))

# --- KABINET ---
@dp.message(F.text == "👤 Kabinet")
async def kabinet(message: types.Message):
    data = get_user_data(message.from_user.id)
    status_name = STATUS_DATA[data['level']]['name']
    limit = STATUS_DATA[data['level']]['limit']
    
    msg = (f"🆔 ID: `{message.from_user.id}`\n"
           f"💰 Balans: **{format_num(data['balance'])} {CURRENCY_SYMBOL}**\n"
           f"📊 Status: {status_name}\n"
           f"💳 O'tkazma limiti: {limit} SC")
    
    if data['expire']:
        msg += f"\n⏳ Status tugash vaqti: `{data['expire']}`"
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💸 Pul o'tkazish", callback_data="transfer_start")]])
    await message.answer(msg, reply_markup=kb, parse_mode="Markdown")

# --- PUL ISHLASH (REFERAL & CLICKER) ---
@dp.message(F.text == "💸 Pul ishlash")
async def earn_money(message: types.Message):
    user = get_user_data(message.from_user.id)
    prices = get_dynamic_prices()
    
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start={message.from_user.id}"
    
    msg = (f"🔗 **Sizning referal havolangiz:**\n`{ref_link}`\n\n"
           f"Har bir taklif uchun: **{format_num(prices['ref_reward'])} {CURRENCY_SYMBOL}**")
    
    kb_rows = []
    # Faqat PRO va undan yuqori statuslar uchun Clicker
    if user['level'] >= 1:
        msg += f"\n\n💎 **PRO Clicker** faol!\nHar bosishda: {format_num(prices['click_reward'])} SC"
        kb_rows.append([InlineKeyboardButton(text="👆 CLICK ME", callback_data="clicker_process")])
    else:
        msg += "\n\n🔒 **Clicker** yopiq. PRO status oling!"
        kb_rows.append([InlineKeyboardButton(text="💎 PRO sotib olish", callback_data="open_status_shop")])
        
    await message.answer(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="Markdown")

@dp.callback_query(F.data == "clicker_process")
async def process_click(callback: types.CallbackQuery):
    user = get_user_data(callback.from_user.id)
    if user['level'] < 1:
        return await callback.answer("Faqat PRO statusdagilar uchun!", show_alert=True)
    
    reward = get_dynamic_prices()['click_reward']
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (reward, callback.from_user.id), commit=True)
    
    # Vizual effekt uchun (har doim ham edit qilish shart emas, limit tufayli)
    # Ammo user bilsin deb kichik alert beramiz
    await callback.answer(f"+{format_num(reward)} SC", cache_time=1)

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
        [InlineKeyboardButton(text=f"💎 PRO ({prices['pro_price']} SC)", callback_data="buy_status_1")],
        [InlineKeyboardButton(text=f"🔥 Premium ({prices['prem_price']} SC)", callback_data="buy_status_2")],
        [InlineKeyboardButton(text=f"👑 KING ({prices['king_price']} SC)", callback_data="buy_status_3")]
    ]
    
    info = (f"**STATUSLAR NARXI (1 OY):**\n\n"
            f"💎 **PRO** - {prices['pro_price']} SC\n{STATUS_DATA[1]['desc']}\n\n"
            f"🔥 **PREMIUM** - {prices['prem_price']} SC\n{STATUS_DATA[2]['desc']}\n\n"
            f"👑 **KING** - {prices['king_price']} SC\n{STATUS_DATA[3]['desc']}")
    
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
        return await callback.answer("Sizda bu status (yoki yuqorirog'i) mavjud!", show_alert=True)
    
    if user['balance'] < cost:
        return await callback.answer("Mablag' yetarli emas!", show_alert=True)
    
    expire_date = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    
    db_query("UPDATE users SET balance = balance - ?, status_level = ?, status_expire = ? WHERE id = ?", 
             (cost, lvl, expire_date, callback.from_user.id), commit=True)
    
    await callback.message.answer(f"🎉 Tabriklaymiz! Siz **{STATUS_DATA[lvl]['name']}** statusini sotib oldingiz!")
    await callback.message.delete()

# --- TOP USERLAR ---
@dp.message(F.text == "🏆 Top Userlar")
async def top_users(message: types.Message):
    users = db_query("SELECT id, balance, status_level FROM users ORDER BY balance DESC LIMIT 10", fetchall=True)
    msg = "🏆 **TOP 10 BOY FOYDALANUVCHILAR:**\n\n"
    
    for idx, (uid, bal, lvl) in enumerate(users, 1):
        # Ism o'rniga ID ishlatamiz maxfiylik uchun, yoki user linki
        badge = ""
        if lvl == 1: badge = "💎"
        elif lvl == 2: badge = "🔥"
        elif lvl == 3: badge = "👑"
        
        msg += f"{idx}. {badge} ID: `{uid}` - **{format_num(bal)} {CURRENCY_SYMBOL}**\n"
        
    await message.answer(msg, parse_mode="Markdown")

# --- LOYIHALAR BO'LIMI (YANGILANGAN) ---
@dp.message(F.text == "📂 Loyihalar")
async def show_projects(message: types.Message):
    projs = db_query("SELECT id, name FROM projects", fetchall=True)
    if not projs: return await message.answer("Hozircha loyihalar yo'q.")
    
    kb = []
    for pid, name in projs:
        kb.append([InlineKeyboardButton(text=name, callback_data=f"view_proj_{pid}")])
    await message.answer("Qiziqtirgan loyihani tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("view_proj_"))
async def view_project(callback: types.CallbackQuery):
    pid = int(callback.data.split("_")[-1])
    proj = db_query("SELECT name, price, description, media_id, media_type FROM projects WHERE id = ?", (pid,), fetchone=True)
    
    if not proj: return await callback.answer("Loyiha topilmadi.")
    name, price, desc, mid, mtype = proj
    
    user = get_user_data(callback.from_user.id)
    # Narx logikasi
    is_free = (user['level'] >= 2) # Premium va King ga tekin
    final_price_text = "TEKIN (Status)" if is_free else f"{format_num(price)} {CURRENCY_SYMBOL}"
    
    caption = f"📂 **{name}**\n\n📝 {desc}\n\n💰 Narxi: {final_price_text}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Sotib olish / Yuklash", callback_data=f"buy_proj_{pid}")]])
    
    if mid:
        if mtype == 'video':
            await callback.message.answer_video(mid, caption=caption, reply_markup=kb, parse_mode="Markdown")
        elif mtype == 'photo':
            await callback.message.answer_photo(mid, caption=caption, reply_markup=kb, parse_mode="Markdown")
        else:
            await callback.message.answer(caption, reply_markup=kb, parse_mode="Markdown")
    else:
        await callback.message.answer(caption, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_proj_"))
async def buy_project_process(callback: types.CallbackQuery):
    pid = int(callback.data.split("_")[-1])
    proj = db_query("SELECT price, file_id, name FROM projects WHERE id = ?", (pid,), fetchone=True)
    if not proj: return
    price, file_id, name = proj
    
    user = get_user_data(callback.from_user.id)
    is_free = (user['level'] >= 2) # Premium va King
    
    if not is_free:
        if user['balance'] < price:
            return await callback.answer("Mablag' yetarli emas!", show_alert=True)
        db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (price, callback.from_user.id), commit=True)
        await callback.message.answer(f"💰 -{format_num(price)} {CURRENCY_SYMBOL} yechildi.")
    
    await callback.message.answer_document(file_id, caption=f"✅ {name} fayli.\nRahmat!")
    await callback.answer()

# --- XIZMATLAR ---
@dp.message(F.text == "🛠 Xizmatlar")
async def services_menu(message: types.Message):
    prices = get_dynamic_prices()
    kb = [
        [InlineKeyboardButton(text=f"🌐 Web ({prices['web']} SC)", callback_data="serv_web")],
        [InlineKeyboardButton(text=f"📱 APK ({prices['apk']} SC)", callback_data="serv_apk")],
        [InlineKeyboardButton(text=f"🤖 Bot ({prices['bot']} SC)", callback_data="serv_bot")]
    ]
    await message.answer("Xizmat turini tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("serv_"))
async def service_select(callback: types.CallbackQuery, state: FSMContext):
    stype = callback.data.split("_")[1]
    prices = get_dynamic_prices()
    cost = prices.get(stype, 0)
    
    user = get_user_data(callback.from_user.id)
    # King status (level 3) ga xizmatlar tekin
    if user['level'] == 3:
        cost = 0
        await callback.message.answer("👑 King Status: Xizmat siz uchun bepul!")
    elif user['balance'] < cost:
        return await callback.answer("Mablag' yetarli emas!", show_alert=True)
        
    await state.update_data(stype=stype, cost=cost)
    await callback.message.answer("Texnik topshiriqni batafsil yozib yuboring:", reply_markup=cancel_kb())
    await state.set_state(OrderService.waiting_for_desc)

@dp.message(OrderService.waiting_for_desc)
async def service_confirm(message: types.Message, state: FSMContext):
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
    
    await message.answer("Buyurtma qabul qilindi! Admin tez orada aloqaga chiqadi.", reply_markup=main_menu(message.from_user.id))
    await state.clear()

# --- PUL O'TKAZISH (LIMITLAR BILAN) ---
@dp.callback_query(F.data == "transfer_start")
async def transfer_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Qabul qiluvchi ID raqamini kiriting:", reply_markup=cancel_kb())
    await state.set_state(MoneyTransfer.waiting_for_recipient)

@dp.message(MoneyTransfer.waiting_for_recipient)
async def transfer_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Raqam bo'lsin!")
    rid = int(message.text)
    if not db_query("SELECT id FROM users WHERE id = ?", (rid,), fetchone=True):
        return await message.answer("User topilmadi!")
        
    await state.update_data(rid=rid)
    user = get_user_data(message.from_user.id)
    limit = STATUS_DATA[user['level']]['limit']
    
    await message.answer(f"Qancha o'tkazmoqchisiz?\nSizning limit: {limit} SC", reply_markup=cancel_kb())
    await state.set_state(MoneyTransfer.waiting_for_amount)

@dp.message(MoneyTransfer.waiting_for_amount)
async def transfer_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
    except:
        return await message.answer("Raqam kiriting (masalan 10 yoki 0.5)")
        
    if amount <= 0: return await message.answer("Musbat son kiriting.")
    
    user = get_user_data(message.from_user.id)
    limit = STATUS_DATA[user['level']]['limit']
    
    if amount > limit:
        return await message.answer(f"Sizning statusingiz bo'yicha limit: {limit} SC.\nLimitni oshirish uchun status sotib oling!")
        
    if user['balance'] < amount:
        return await message.answer("Balans yetarli emas!")
        
    data = await state.get_data()
    db_query("UPDATE users SET balance = balance - ? WHERE id = ?", (amount, message.from_user.id), commit=True)
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, data['rid']), commit=True)
    
    await message.answer("✅ O'tkazma muvaffaqiyatli bajarildi!", reply_markup=main_menu(message.from_user.id))
    try: await bot.send_message(data['rid'], f"📥 Sizga +{format_num(amount)} SC kelib tushdi! (User: {message.from_user.id})")
    except: pass
    await state.clear()

# --- ADMIN PANEL (KENGAYTIRILGAN) ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    kb = [
        [InlineKeyboardButton(text="➕ Loyiha Qo'shish", callback_data="adm_add_proj"),
         InlineKeyboardButton(text="💵 Narxlar va Sozlamalar", callback_data="adm_prices")],
        [InlineKeyboardButton(text="📝 Matnlarni Tahrirlash", callback_data="adm_texts")],
        [InlineKeyboardButton(text="✏️ User Balansi", callback_data="adm_edit_bal")]
    ]
    await message.answer("🔐 Admin Panel v2.0", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# 1. Loyiha qo'shish (Wizard)
@dp.callback_query(F.data == "adm_add_proj")
async def adm_add_proj_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Loyiha nomini yozing:", reply_markup=cancel_kb())
    await state.set_state(AdminState.add_proj_name)

@dp.message(AdminState.add_proj_name)
async def adm_p_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Narxini kiriting (SC):")
    await state.set_state(AdminState.add_proj_price)

@dp.message(AdminState.add_proj_price)
async def adm_p_price(message: types.Message, state: FSMContext):
    try:
        val = float(message.text)
    except: return await message.answer("Raqam yozing!")
    await state.update_data(price=val)
    await message.answer("Loyiha haqida tavsif (Description) yozing:")
    await state.set_state(AdminState.add_proj_desc)

@dp.message(AdminState.add_proj_desc)
async def adm_p_desc(message: types.Message, state: FSMContext):
    await state.update_data(desc=message.text)
    await message.answer("Rasm yoki Video yuboring (Ixtiyoriy, 'skip' deb yozsangiz o'tkazib yuboriladi):")
    await state.set_state(AdminState.add_proj_media)

@dp.message(AdminState.add_proj_media)
async def adm_p_media(message: types.Message, state: FSMContext):
    mid, mtype = None, None
    if message.photo:
        mid, mtype = message.photo[-1].file_id, "photo"
    elif message.video:
        mid, mtype = message.video.file_id, "video"
    elif message.text and message.text.lower() != 'skip':
        return await message.answer("Rasm, video yoki 'skip' yozing.")
        
    await state.update_data(mid=mid, mtype=mtype)
    await message.answer("Endi asosiy faylni (zip, rar, txt) yuboring:")
    await state.set_state(AdminState.add_proj_file)

@dp.message(AdminState.add_proj_file)
async def adm_p_file(message: types.Message, state: FSMContext):
    if not message.document: return await message.answer("Fayl yuboring!")
    data = await state.get_data()
    
    db_query("INSERT INTO projects (name, price, description, media_id, media_type, file_id) VALUES (?,?,?,?,?,?)",
             (data['name'], data['price'], data['desc'], data['mid'], data['mtype'], message.document.file_id), commit=True)
    
    await message.answer("✅ Loyiha qo'shildi!", reply_markup=main_menu(message.from_user.id))
    await state.clear()

# 2. Narxlar
@dp.callback_query(F.data == "adm_prices")
async def adm_prices_list(callback: types.CallbackQuery):
    p = get_dynamic_prices()
    kb = [
        [InlineKeyboardButton(text=f"Ref Bonus ({p['ref_reward']})", callback_data="set_ref_reward"),
         InlineKeyboardButton(text=f"Click ({p['click_reward']})", callback_data="set_click_reward")],
        [InlineKeyboardButton(text=f"PRO ({p['pro_price']})", callback_data="set_status_price_1"),
         InlineKeyboardButton(text=f"Premium ({p['prem_price']})", callback_data="set_status_price_2")],
        [InlineKeyboardButton(text=f"KING ({p['king_price']})", callback_data="set_status_price_3")]
    ]
    await callback.message.edit_text("Nimani o'zgartiramiz?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("set_"))
async def adm_set_val(callback: types.CallbackQuery, state: FSMContext):
    key = callback.data.replace("set_", "")
    await state.update_data(conf_key=key)
    await callback.message.answer(f"Yangi qiymatni yozing ({key}):", reply_markup=cancel_kb())
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
        await message.answer("Raqam yozing.")

# 3. Matnlarni tahrirlash
@dp.callback_query(F.data == "adm_texts")
async def adm_texts(callback: types.CallbackQuery):
    kb = [
        [InlineKeyboardButton(text="Welcome Text", callback_data="txt_welcome")]
    ]
    await callback.message.edit_text("Qaysi matnni o'zgartirasiz?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("txt_"))
async def adm_txt_edit(callback: types.CallbackQuery, state: FSMContext):
    key = callback.data.replace("txt_", "text_")
    await state.update_data(txt_key=key)
    curr = get_config(key, "Mavjud emas")
    await callback.message.answer(f"Hozirgi:\n{curr}\n\nYangisini yozing:", reply_markup=cancel_kb())
    await state.set_state(AdminState.edit_text_val)

@dp.message(AdminState.edit_text_val)
async def adm_txt_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    set_config(data['txt_key'], message.text) # \n larni o'zi handle qiladi
    await message.answer("✅ Matn yangilandi!", reply_markup=main_menu(message.from_user.id))
    await state.clear()

# --- HISOB TO'LDIRISH (ESKISI BILAN BIR XIL, LEKIN FLOAT) ---
@dp.message(F.text == "💰 Hisobni to'ldirish")
async def topup_start(message: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🇺🇿 UZS (Humo/Uzcard)"), KeyboardButton(text="🇺🇸 USD (Visa)")],
        [KeyboardButton(text="🚫 Bekor qilish")]
    ], resize_keyboard=True)
    await message.answer("Valyutani tanlang:", reply_markup=kb)
    await state.set_state(FillBalance.choosing_currency)

@dp.message(FillBalance.choosing_currency)
async def topup_curr(message: types.Message, state: FSMContext):
    rates = get_coin_rates()
    if "UZS" in message.text:
        curr, rate, card = "UZS", rates['uzs'], CARD_UZS
    elif "USD" in message.text:
        curr, rate, card = "USD", rates['usd'], CARD_VISA
    else: return await message.answer("Tanlang!")
    
    await state.update_data(curr=curr, rate=rate)
    await message.answer(f"Karta: `{card}`\nKurs: 1 SC = {rate} {curr}\nQancha SC olmoqchisiz?", reply_markup=cancel_kb(), parse_mode="Markdown")
    await state.set_state(FillBalance.waiting_for_amount)

@dp.message(FillBalance.waiting_for_amount)
async def topup_amt(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text)
    except: return await message.answer("Raqam yozing!")
    
    data = await state.get_data()
    total = amt * data['rate']
    txt = f"{total} so'm" if data['curr'] == "UZS" else f"{total} $"
    
    await state.update_data(amt=amt, txt=txt)
    await message.answer(f"To'lov miqdori: **{txt}**\nChekni yuboring:", parse_mode="Markdown")
    await state.set_state(FillBalance.waiting_for_receipt)

@dp.message(FillBalance.waiting_for_receipt, F.photo)
async def topup_rec(message: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tasdiq", callback_data=f"p_ok:{message.from_user.id}:{data['amt']}"),
         InlineKeyboardButton(text="❌ Rad", callback_data=f"p_no:{message.from_user.id}")]
    ])
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, 
                         caption=f"To'lov!\nUser: {message.from_user.id}\nSC: {data['amt']}\nPul: {data['txt']}", reply_markup=kb)
    await message.answer("Adminga yuborildi.")
    await state.clear()

@dp.callback_query(F.data.startswith("p_ok:"))
async def approve_pay(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    uid, amt = int(parts[1]), float(parts[2])
    db_query("UPDATE users SET balance = balance + ? WHERE id = ?", (amt, uid), commit=True)
    await bot.send_message(uid, f"✅ Hisobingizga +{amt} SC qo'shildi!")
    await callback.message.delete()

@dp.callback_query(F.data.startswith("p_no:"))
async def reject_pay(callback: types.CallbackQuery):
    uid = int(callback.data.split(":")[1])
    await bot.send_message(uid, "❌ To'lovingiz rad etildi.")
    await callback.message.delete()

# --- GENERIC CANCEL ---
@dp.message(F.text == "🚫 Bekor qilish")
async def cancel_all(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=main_menu(message.from_user.id))

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
