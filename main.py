import os
import threading
from flask import Flask

import os
import telebot
from telebot import types
import sqlite3
from datetime import datetime, date

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable not set")

bot = telebot.TeleBot(TOKEN)

DB_PATH = "warehouse.db"

ADMIN_IDS = {975183266}
ALLOWED_USERS = {975183266}

# --------- DB ----------
def db():
    return sqlite3.connect(DB_PATH)

def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        qty INTEGER NOT NULL DEFAULT 0,
        exp_date TEXT,
        min_qty INTEGER NOT NULL DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS movements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        mtype TEXT NOT NULL,               -- IN / OUT / WRITE_OFF
        qty INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        comment TEXT
    )
    """)

    con.commit()
    con.close()

def log_movement(product_id: int, mtype: str, qty: int, comment: str = ""):
    con = db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO movements(product_id, mtype, qty, created_at, comment) VALUES(?,?,?,?,?)",
        (product_id, mtype, qty, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), comment)
    )
    con.commit()
    con.close()

# --------- UI ----------
def main_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("➕ Тауар қосу"),
        types.KeyboardButton("📦 Қойма тізімі"),
        types.KeyboardButton("❌ Тауар өшіру"),
        types.KeyboardButton("⏰ Мерзім тексеру"),
        types.KeyboardButton("➖ Сату тіркеу"),
        types.KeyboardButton("📊 Статистика"),
        # ЖАҢА БАТЫРМАЛАР:
        types.KeyboardButton("➕ Кіріс тіркеу"),
        types.KeyboardButton("🗑️ Списание"),
        types.KeyboardButton("🔎 Іздеу"),
        types.KeyboardButton("✏️ Тауар өңдеу"),
        types.KeyboardButton("⚠️ Аз қалды"),
        types.KeyboardButton("🧾 Журнал"),
    )
    return kb

# --------- STATES (simple dict) ----------
# user_state[user_id] = {"step": "...", "data": {...}}
user_state = {}

def set_state(uid, step, data=None):
    user_state[uid] = {"step": step, "data": data or {}}

def get_state(uid):
    return user_state.get(uid, {"step": None, "data": {}})

def clear_state(uid):
    if uid in user_state:
        del user_state[uid]

# --------- HELPERS ----------
def find_product_by_id(pid: int):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, name, qty, exp_date, min_qty FROM products WHERE id=?", (pid,))
    row = cur.fetchone()
    con.close()
    return row

def find_products_like(q: str):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, name, qty, exp_date, min_qty FROM products WHERE name LIKE ? ORDER BY id DESC", (f"%{q}%",))
    rows = cur.fetchall()
    con.close()
    return rows

def list_products(limit=50):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, name, qty, exp_date, min_qty FROM products ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    con.close()
    return rows

def text_products(rows):
    if not rows:
        return "Қойма бос."
    t = "Қоймадағы тауарлар:\n\n"
    for pid, name, qty, exp, minq in rows:
        exp_show = exp if exp else "—"
        t += f"ID:{pid} | {name} — {qty} дана — {exp_show} | min:{minq}\n"
    return t

@bot.message_handler(commands=["start"])
def start(message):
    uid = message.from_user.id

    if uid not in ALLOWED_USERS:
        bot.send_message(
            message.chat.id,
            "⛔ Бұл ботқа қол жеткізу шектеулі.\n"
            "Қойма есебі тек уәкілетті пайдаланушыларға арналған."
        )
        return

    init_db()
    bot.send_message(
        message.chat.id,
        "✅ Қойма есебі жүйесіне қош келдіңіз!",
        reply_markup=main_kb()
    )


# =========================================================
# 1) ТАУАР ҚОСУ (сенде бар болса — қалдыруға болады)
# =========================================================
@bot.message_handler(func=lambda m: m.text == "➕ Тауар қосу")
def add_product_start(message):
    set_state(message.from_user.id, "ADD_NAME")
    bot.send_message(message.chat.id, "Тауар атауын енгізіңіз:")

@bot.message_handler(func=lambda m: get_state(m.from_user.
id)["step"] == "ADD_NAME")
def add_product_name(message):
    st = get_state(message.from_user.id)
    st["data"]["name"] = message.text.strip()
    set_state(message.from_user.id, "ADD_QTY", st["data"])
    bot.send_message(message.chat.id, "Санын енгізіңіз (мысалы: 10):")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "ADD_QTY")
def add_product_qty(message):
    try:
        qty = int(message.text.strip())
        if qty < 0:
            raise ValueError
    except:
        bot.send_message(message.chat.id, "⚠️ Сан дұрыс емес. Мысалы: 10")
        return

    st = get_state(message.from_user.id)
    st["data"]["qty"] = qty
    set_state(message.from_user.id, "ADD_EXP", st["data"])
    bot.send_message(message.chat.id, "Мерзімі (YYYY-MM-DD) немесе '-' деп жіберіңіз:")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "ADD_EXP")
def add_product_exp(message):
    exp = message.text.strip()
    if exp == "-":
        exp = None
    else:
        # жеңіл тексеріс
        try:
            datetime.strptime(exp, "%Y-%m-%d")
        except:
            bot.send_message(message.chat.id, "⚠️ Дата форматы қате. Мысалы: 2026-02-17 немесе '-'")
            return

    st = get_state(message.from_user.id)
    st["data"]["exp_date"] = exp
    set_state(message.from_user.id, "ADD_MIN", st["data"])
    bot.send_message(message.chat.id, "Min саны (аз қалды ескерту үшін). Мысалы 5. Егер керек болмаса 0:")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "ADD_MIN")
def add_product_min(message):
    try:
        minq = int(message.text.strip())
        if minq < 0:
            raise ValueError
    except:
        bot.send_message(message.chat.id, "⚠️ Min саны қате. Мысалы: 5 немесе 0")
        return

    st = get_state(message.from_user.id)["data"]
    name, qty, exp = st["name"], st["qty"], st["exp_date"]

    con = db()
    cur = con.cursor()
    cur.execute("INSERT INTO products(name, qty, exp_date, min_qty) VALUES(?,?,?,?)", (name, qty, exp, minq))
    con.commit()
    con.close()

    clear_state(message.from_user.id)
    bot.send_message(message.chat.id, f"✅ Қосылды: {name} — {qty} дана | min:{minq}", reply_markup=main_kb())

# =========================================================
# 2) ҚОЙМА ТІЗІМІ
# =========================================================
@bot.message_handler(func=lambda m: m.text == "📦 Қойма тізімі")
def show_list(message):
    rows = list_products(100)
    bot.send_message(message.chat.id, text_products(rows), reply_markup=main_kb())

# =========================================================
# 3) ТАУАР ӨШІРУ
# =========================================================
@bot.message_handler(func=lambda m: m.text == "❌ Тауар өшіру")
def delete_start(message):
    set_state(message.from_user.id, "DEL_ID")
    bot.send_message(message.chat.id, "Өшіретін тауар ID енгізіңіз:")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "DEL_ID")
def delete_by_id(message):
    try:
        pid = int(message.text.strip())
    except:
        bot.send_message(message.chat.id, "⚠️ ID сан болуы керек.")
        return

    prod = find_product_by_id(pid)
    if not prod:
        bot.send_message(message.chat.id, "❌ Ондай ID табылмады.")
        clear_state(message.from_user.id)
        return

    # растау
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Иә, өшірем", callback_data=f"del_yes:{pid}"),
        types.InlineKeyboardButton("❌ Жоқ", callback_data="del_no")
    )
    bot.send_message(message.chat.id, f"Өшіру керек пе?\nID:{prod[0]} | {prod[1]}", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("del_yes:"))
def del_yes(call):
    pid = int(call.data.split(":")[1])
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM products WHERE id=?", (pid,))
    con.commit()
    con.close()
    bot.answer_callback_query(call.id, "Өшірілді ✅")
    bot.send_message(call.message.chat.id, "✅ Тауар өшірілді.", reply_markup=main_kb())
    clear_state(call.
from_user.id)

@bot.callback_query_handler(func=lambda c: c.data == "del_no")
def del_no(call):
    bot.answer_callback_query(call.id, "Болды")
    bot.send_message(call.message.chat.id, "Өшіру тоқтатылды.", reply_markup=main_kb())
    clear_state(call.from_user.id)

# =========================================================
# 4) МЕРЗІМ ТЕКСЕРУ (30 күн ішінде бітетіндер)
# =========================================================
@bot.message_handler(func=lambda m: m.text == "⏰ Мерзім тексеру")
def check_exp(message):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, name, qty, exp_date FROM products WHERE exp_date IS NOT NULL")
    rows = cur.fetchall()
    con.close()

    today = date.today()
    near = []
    for pid, name, qty, exp in rows:
        try:
            d = datetime.strptime(exp, "%Y-%m-%d").date()
            days = (d - today).days
            if days <= 30:
                near.append((pid, name, qty, exp, days))
        except:
            pass

    if not near:
        bot.send_message(message.chat.id, "✅ 30 күн ішінде мерзімі бітетін тауар жоқ.")
        return

    text = "⏰ Мерзімі жақын тауарлар (30 күн):\n\n"
    for pid, name, qty, exp, days in sorted(near, key=lambda x: x[4]):
        text += f"ID:{pid} | {name} — {qty} дана — {exp} (қалды {days} күн)\n"
    bot.send_message(message.chat.id, text)

# =========================================================
# 5) САТУ ТІРКЕУ (OUT)
# =========================================================
@bot.message_handler(func=lambda m: m.text == "➖ Сату тіркеу")
def sale_start(message):
    set_state(message.from_user.id, "OUT_ID")
    bot.send_message(message.chat.id, "Сатылатын тауар ID енгізіңіз:")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "OUT_ID")
def sale_id(message):
    try:
        pid = int(message.text.strip())
    except:
        bot.send_message(message.chat.id, "⚠️ ID сан болуы керек.")
        return

    prod = find_product_by_id(pid)
    if not prod:
        bot.send_message(message.chat.id, "❌ Ондай ID табылмады.")
        clear_state(message.from_user.id)
        return

    set_state(message.from_user.id, "OUT_QTY", {"pid": pid})
    bot.send_message(message.chat.id, f"{prod[1]} сатылатын санын енгізіңіз (қалдық: {prod[2]}):")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "OUT_QTY")
def sale_qty(message):
    st = get_state(message.from_user.id)["data"]
    pid = st["pid"]

    try:
        qty = int(message.text.strip())
        if qty <= 0:
            raise ValueError
    except:
        bot.send_message(message.chat.id, "⚠️ Сан қате. Мысалы: 2")
        return

    prod = find_product_by_id(pid)
    if not prod:
        bot.send_message(message.chat.id, "❌ Тауар табылмады.")
        clear_state(message.from_user.id)
        return

    if qty > prod[2]:
        bot.send_message(message.chat.id, f"⚠️ Қалдық жетпейді. Қоймада {prod[2]} ғана бар.")
        return

    con = db()
    cur = con.cursor()
    cur.execute("UPDATE products SET qty = qty - ? WHERE id=?", (qty, pid))
    con.commit()
    con.close()

    log_movement(pid, "OUT", qty, "Сату")
    clear_state(message.from_user.id)
    bot.send_message(message.chat.id, f"✅ Сатылды: {prod[1]} — {qty} дана", reply_markup=main_kb())

# =========================================================
# 6) СТАТИСТИКА (қарапайым)
# =========================================================
@bot.message_handler(func=lambda m: m.text == "📊 Статистика")
def stats(message):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*), COALESCE(SUM(qty),0) FROM products")
    total_products, total_qty = cur.fetchone()

    cur.execute("SELECT COUNT(*) FROM movements")
    moves = cur.fetchone()[0]

    con.close()
    bot.send_message(
        message.chat.id,
        f"📊 Статистика:\n"
        f"• Тауар түрі: {total_products}\n"
        f"• Жалпы саны: {total_qty}\n"
        f"• Операциялар (журнал): {moves}"
    )

# =========================================================
# ===================== ЖАҢА ФУНКЦИЯЛАР =====================
# =========================================================

# 7) КІРІС ТІРКЕУ (IN)
@bot.message_handler(func=lambda m: m.text == "➕ Кіріс тіркеу")
def income_start(message):
    set_state(message.from_user.id, "IN_ID")
    bot.send_message(message.chat.id, "Кіріс болатын тауар ID енгізіңіз:")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "IN_ID")
def income_id(message):
    try:
        pid = int(message.text.strip())
    except:
        bot.send_message(message.chat.id, "⚠️ ID сан болуы керек.")
        return

    prod = find_product_by_id(pid)
    if not prod:
        bot.send_message(message.chat.id, "❌ Ондай ID табылмады.")
        clear_state(message.from_user.id)
        return

    set_state(message.from_user.id, "IN_QTY", {"pid": pid})
    bot.send_message(message.chat.id, f"{prod[1]} кіріс санын енгізіңіз:")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "IN_QTY")
def income_qty(message):
    st = get_state(message.from_user.id)["data"]
    pid = st["pid"]

    try:
        qty = int(message.text.strip())
        if qty <= 0:
            raise ValueError
    except:
        bot.send_message(message.chat.id, "⚠️ Сан қате. Мысалы: 20")
        return

    prod = find_product_by_id(pid)
    if not prod:
        bot.send_message(message.chat.id, "❌ Тауар табылмады.")
        clear_state(message.from_user.id)
        return

    con = db()
    cur = con.cursor()
    cur.execute("UPDATE products SET qty = qty + ? WHERE id=?", (qty, pid))
    con.commit()
    con.close()

    log_movement(pid, "IN", qty, "Кіріс")
    clear_state(message.from_user.id)
    bot.send_message(message.chat.id, f"✅ Кіріс тіркелді: {prod[1]} — +{qty} дана", reply_markup=main_kb())

# 8) СПИСАНИЕ (WRITE_OFF)
@bot.message_handler(func=lambda m: m.text == "🗑️ Списание")
def writeoff_start(message):
    set_state(message.from_user.id, "WO_ID")
    bot.send_message(message.chat.id, "Списание болатын тауар ID енгізіңіз:")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "WO_ID")
def writeoff_id(message):
    try:
        pid = int(message.text.strip())
    except:
        bot.send_message(message.chat.id, "⚠️ ID сан болуы керек.")
        return

    prod = find_product_by_id(pid)
    if not prod:
        bot.send_message(message.chat.id, "❌ Ондай ID табылмады.")
        clear_state(message.from_user.id)
        return

    set_state(message.from_user.id, "WO_QTY", {"pid": pid})
    bot.send_message(message.chat.id, f"{prod[1]} списание санын енгізіңіз (қалдық: {prod[2]}):")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "WO_QTY")
def writeoff_qty(message):
    st = get_state(message.from_user.id)["data"]
    pid = st["pid"]

    try:
        qty = int(message.text.strip())
        if qty <= 0:
            raise ValueError
    except:
        bot.send_message(message.chat.id, "⚠️ Сан қате.")
        return

    prod = find_product_by_id(pid)
    if not prod:
        bot.send_message(message.chat.id, "❌ Тауар табылмады.")
        clear_state(message.from_user.id)
        return

    if qty > prod[2]:
        bot.send_message(message.chat.id, f"⚠️ Қалдық жетпейді. Қоймада {prod[2]} ғана бар.")
        return

    set_state(message.from_user.id, "WO_REASON", {"pid": pid, "qty": qty})
    bot.send_message(message.chat.id, "Себебін жазыңыз (мыс: бұзылды/мерзімі өтті):")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "WO_REASON")
def writeoff_reason(message):
    st = get_state(message.from_user.id)["data"]
    pid = st["pid"]
    qty = st["qty"]
    reason = message.text.strip()

    prod = find_product_by_id(pid)
    if not prod:
        bot.send_message(message.chat.id, "❌ Тауар табылмады.")
        clear_state(message.from_user.id)
        return

    con = db()
    cur = con.cursor()
    cur.execute("UPDATE products SET qty = qty - ? WHERE id = ?", (qty, pid))
    con.commit()
    con.close()

    log_movement(pid, "WRITE_OFF", qty, reason)
    clear_state(message.from_user.id)

    bot.send_message(
        message.chat.id,
        f"✅ Списание: {prod[1]} — {qty} дана\nСебеп: {reason}",
        reply_markup=main_kb()
    )

# 9) ІЗДЕУ 🔎 (атау бойынша)
@bot.message_handler(func=lambda m: m.text == "🔎 Іздеу")
def search_start(message):
    set_state(message.from_user.id, "SEARCH_Q")
    bot.send_message(message.chat.id, "Іздеу сөзін енгізіңіз (мыс: пепси):")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "SEARCH_Q")
def search_query(message):
    q = message.text.strip()
    rows = find_products_like(q)
    clear_state(message.from_user.id)
    bot.send_message(message.chat.id, "🔎 Нәтиже:\n\n" + text_products(rows), reply_markup=main_kb())

# 10) ТАУАР ӨҢДЕУ ✏️ (атау/мерзім/min)
@bot.message_handler(func=lambda m: m.text == "✏️ Тауар өңдеу")
def edit_start(message):
    set_state(message.from_user.id, "EDIT_ID")
    bot.send_message(message.chat.id, "Өңдейтін тауар ID енгізіңіз:")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "EDIT_ID")
def edit_id(message):
    try:
        pid = int(message.text.strip())
    except:
        bot.send_message(message.chat.id, "⚠️ ID сан болуы керек.")
        return

    prod = find_product_by_id(pid)
    if not prod:
        bot.send_message(message.chat.id, "❌ Ондай тауар жоқ.")
        clear_state(message.from_user.id)
        return

    set_state(message.from_user.id, "EDIT_MENU", {"pid": pid})
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("📝 Атауын өзгерту", callback_data=f"edit_name:{pid}"),
        types.InlineKeyboardButton("⏰ Мерзімін өзгерту", callback_data=f"edit_exp:{pid}"),
        types.InlineKeyboardButton("⚠️ Min санын өзгерту", callback_data=f"edit_min:{pid}"),
    )
    bot.send_message(message.chat.id, f"Таңдаңыз:\nID:{prod[0]} | {prod[1]}", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("edit_name:"))
def edit_name_cb(call):
    pid = int(call.data.split(":")[1])
    set_state(call.from_user.id, "EDIT_NAME", {"pid": pid})
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "Жаңа атауын енгізіңіз:")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "EDIT_NAME")
def edit_name_save(message):
    pid = get_state(message.from_user.id)["data"]["pid"]
    new_name = message.text.strip()

    con = db()
    cur = con.cursor()
    cur.execute("UPDATE products SET name=? WHERE id=?", (new_name, pid))
    con.commit()
    con.close()

    clear_state(message.from_user.id)
    bot.send_message(message.chat.id, "✅ Атауы жаңартылды.", reply_markup=main_kb())

@bot.callback_query_handler(func=lambda c: c.data.startswith("edit_exp:"))
def edit_exp_cb(call):
    pid = int(call.data.split(":")[1])
    set_state(call.from_user.id, "EDIT_EXP", {"pid": pid})
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "Жаңа мерзім (YYYY-MM-DD) немесе '-' :")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "EDIT_EXP")
def edit_exp_save(message):
    pid = get_state(message.from_user.id)["data"]["pid"]
    exp = message.text.strip()
    if exp == "-":
        exp = None
    else:
        try:
            datetime.strptime(exp, "%Y-%m-%d")
        except:
            bot.send_message(message.chat.id, "⚠️ Формат қате. Мысалы: 2026-03-24 немесе '-'")
            return

    con = db()
    cur = con.cursor()
    cur.execute("UPDATE products SET exp_date=? WHERE id=?", (exp, pid))
    con.commit()
    con.close()

    clear_state(message.from_user.id)
    bot.send_message(message.chat.id, "✅ Мерзім жаңартылды.", reply_markup=main_kb())

@bot.callback_query_handler(func=lambda c: c.data.startswith("edit_min:"))
def edit_min_cb(call):
    pid = int(call.data.split(":")[1])
    set_state(call.from_user.id, "EDIT_MIN", {"pid": pid})
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "Жаңа min саны (мыс: 5 немесе 0):")

@bot.message_handler(func=lambda m: get_state(m.from_user.id)["step"] == "EDIT_MIN")
def edit_min_save(message):
    pid = get_state(message.from_user.id)["data"]["pid"]
    try:
        minq = int(message.text.strip())
        if minq < 0:
            raise ValueError
    except:
        bot.send_message(message.chat.id, "⚠️ Min саны қате.")
        return

    con = db()
    cur = con.cursor()
    cur.execute("UPDATE products SET min_qty=? WHERE id=?", (minq, pid))
    con.commit()
    con.close()

    clear_state(message.from_user.id)
    bot.send_message(message.chat.id, "✅ Min саны жаңартылды.", reply_markup=main_kb())

# 11) АЗ ҚАЛДЫ ⚠️
@bot.message_handler(func=lambda m: m.text == "⚠️ Аз қалды")
def low_stock(message):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, name, qty, min_qty FROM products WHERE qty <= min_qty AND min_qty > 0 ORDER BY qty ASC")
    rows = cur.fetchall()
    con.close()

    if not rows:
        bot.send_message(message.chat.id, "✅ Аз қалған тауар жоқ (немесе min қойылмаған).")
        return

    text = "⚠️ Аз қалған тауарлар:\n\n"
    for pid, name, qty, minq in rows:
        text += f"ID:{pid} | {name} — {qty} дана (min:{minq})\n"
    bot.send_message(message.chat.id, text)

# 12) ЖУРНАЛ 🧾 (соңғы 30 операция)
@bot.message_handler(func=lambda m: m.text == "🧾 Журнал")
def journal(message):
    con = db()
    cur = con.cursor()
    cur.execute("""
    SELECT m.id, p.name, m.mtype, m.qty, m.created_at, m.comment
    FROM movements m
    JOIN products p ON p.id = m.product_id
    ORDER BY m.id DESC
    LIMIT 30
    """)
    rows = cur.fetchall()
    con.close()

    if not rows:
        bot.send_message(message.chat.id, "Журнал бос.")
        return

    text = "🧾 Соңғы операциялар (30):\n\n"
    for mid, pname, mtype, qty, created_at, comment in rows:
        text += f"#{mid} | {mtype} | {pname} | {qty} дана | {created_at}"
        if comment:
            text += f" | {comment}"
        text += "\n"
    bot.send_message(message.chat.id, text)

# =====================================
# ❌ Белгісіз мәтін / қолдау таппайды
# =====================================
@bot.message_handler(func=lambda message: True)
def unsupported_message(message):
    bot.send_message(
        message.chat.id,
        "⚠️ Қате енгізу\n"
        "Кешіріңіз, енгізілген сұраныс жүйе тарапынан қолдау таппайды.\n\n"
        "📌 Қойма есебі операцияларын орындау үшін төмендегі мәзір батырмаларын пайдаланыңыз.",
        reply_markup=main_kb()
    )


# ================= WEB (Flask) =================

import threading
from flask import Flask

app = Flask(__name__)

@app.get("/")
def home():
    return "Bot is running", 200


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ================= RUN =================

def start_bot():
    print("Bot is running...")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    init_db()

    # Telegram ботты бөлек потокта іске қосамыз
    bot_thread = threading.Thread(target=start_bot)
    bot_thread.start()

    # Flask серверді негізгі потокта іске қосамыз
    run_web()