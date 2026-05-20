from keep_alive import keep_alive
import os
import sqlite3
from datetime import datetime, date
from dotenv import load_dotenv
import pandas as pd

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================
# LOAD ENV
# =========================

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# =========================
# CONFIG
# =========================

OPEN_HOUR = 8
CLOSE_HOUR = 17

PRODUCTS = [
    ("فطور", "🍳"),
    ("غداء", "🍽"),
    ("عشاء", "🥫"),
    ("شاي", "☕️"),
    ("ماء", "💧"),
]

# =========================
# DATABASE
# =========================

conn = sqlite3.connect("orders.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    order_name TEXT,
    product TEXT,
    quantity INTEGER,
    datetime TEXT
)
""")

conn.commit()




# =========================
# MEMORY
# =========================

user_carts = {}
waiting_for_name = {}

# =========================
# HELPERS
# =========================

def is_open():
    now = datetime.now().hour
    return OPEN_HOUR <= now < CLOSE_HOUR

def today():
    return date.today().isoformat()

# =========================
# KEYBOARD
# =========================

def build_keyboard(user_id):
    cart = user_carts.get(user_id, {})
    text = "🛒 طلبك:\n\n"
    keyboard = []

    for product, emoji in PRODUCTS:
        qty = cart.get(product, 0)
        text += f"{emoji} {product}: {qty}\n"


        keyboard.append([
            InlineKeyboardButton(f"{emoji} {product}", callback_data="noop"),
        ])

        keyboard.append([
            InlineKeyboardButton("-1", callback_data=f"minus:{product}"),
            InlineKeyboardButton("+1", callback_data=f"plus1:{product}"),
            InlineKeyboardButton("+5", callback_data=f"plus5:{product}"),
        ])
    keyboard.append([
        InlineKeyboardButton("✅ تأكيد الطلب", callback_data="confirm")
    ])

    keyboard.append([
        InlineKeyboardButton("🗑 حذف", callback_data="clear")
    ])

    return text, InlineKeyboardMarkup(keyboard)

# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    if not is_open():
        await update.message.reply_text("⛔ الطلبات مغلقة (8ص - 5م)")
        return

    # ✅ CHECK IF ALREADY ORDERED
    cursor.execute("""
    SELECT product, quantity
    FROM orders
    WHERE user_id=? AND DATE(datetime)=?
    """, (user_id, today()))

    rows = cursor.fetchall()

    if rows:
        text = "📦 طلبك اليوم:\n\n"

        for product, qty in rows:
            text += f"{product}: {qty}\n"

        await update.message.reply_text(text)
        return

    # ✅ normal flow
    user_carts[user_id] = {}

    text, kb = build_keyboard(user_id)
    await update.message.reply_text(text, reply_markup=kb)
    
# =========================
# BUTTONS
# =========================

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if not is_open():
        await query.answer("⛔ مغلق الآن", show_alert=True)
        return

    if user_id not in user_carts:
        user_carts[user_id] = {}

    cart = user_carts[user_id]
    data = query.data

    if data.startswith("plus1:"):
        p = data.split(":")[1]
        cart[p] = cart.get(p, 0) + 1

    elif data.startswith("plus5:"):
        p = data.split(":")[1]
        cart[p] = cart.get(p, 0) + 5

    elif data.startswith("minus:"):
        p = data.split(":")[1]
        if cart.get(p, 0) > 0:
            cart[p] -= 1

    elif data == "clear":
        user_carts[user_id] = {}

    elif data == "confirm":

        if not any(cart.values()):
            await query.answer("السلة فارغة", show_alert=True)
            return

        cursor.execute("""
        SELECT COUNT(*) FROM orders
        WHERE user_id=? AND DATE(datetime)=?
        """, (user_id, today()))

        if cursor.fetchone()[0] > 0:
            await query.edit_message_text("❌ لقد قمت بإرسال طلبك اليوم بالفعل")
            return

        waiting_for_name[user_id] = True
        await query.edit_message_text("📝 اكتب اسم الطلب (إجباري):")
        return

    text, kb = build_keyboard(user_id)
    await query.edit_message_text(text, reply_markup=kb)

# =========================
# RECEIVE NAME
# =========================
async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in waiting_for_name:
        return

    order_name = update.message.text
    cart = user_carts.get(user_id, {})

    username = update.effective_user.username or "NoUsername"

    # ✅ SAVE TO DATABASE
    for product, qty in cart.items():
        if qty > 0:
            cursor.execute("""
            INSERT INTO orders (user_id, username, order_name, product, quantity, datetime)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                username,
                order_name,
                product,
                qty,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ))

    conn.commit()

    # =========================
    # ✅ SEND TO ADMIN (INSIDE FUNCTION)
    # =========================
    admin_text = f"📥 طلب جديد:\n\n"
    admin_text += f"👤 @{username}\n"
    admin_text += f"🧾 {order_name}\n"
    admin_text += f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"

    for p, q in cart.items():
        if q > 0:
            admin_text += f"{p}: {q}\n"

    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text)
    except Exception as e:
        print(f"Failed to notify admin: {e}")

    # =========================
    # ✅ USER CONFIRMATION
    # =========================
    summary = f"✅ تم تسجيل: {order_name}\n\n"
    for p, q in cart.items():
        if q > 0:
            summary += f"{p}: {q}\n"

    user_carts[user_id] = {}
    waiting_for_name.pop(user_id)

    await update.message.reply_text(summary)

# =========================
# SUMMARY
# =========================

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    cursor.execute("""
    SELECT product, SUM(quantity)
    FROM orders
    WHERE DATE(datetime)=?
    GROUP BY product
    """, (today(),))

    rows = cursor.fetchall()

    text = "📊 ملخص اليوم:\n\n"
    for p, total in rows:
        text += f"{p}: {total}\n"

    await update.message.reply_text(text)
# =========================
# DETAILS (NEW)
# =========================
async def details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    cursor.execute("""
    SELECT order_name, username, product, quantity, datetime
    FROM orders
    WHERE DATE(datetime)=?
    ORDER BY datetime
    """, (today(),))

    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("لا توجد طلبات اليوم")
        return

    orders = {}

    for order_name, username, product, quantity, dt in rows:
        key = f"{order_name} (@{username}) | {dt}"

        if key not in orders:
            orders[key] = []

        orders[key].append((product, quantity))

    text = "📋 تفاصيل الطلبات:\n\n"

    for order_name, items in orders.items():
        text += f"🧾 {order_name}\n"
        for product, qty in items:
            text += f"  - {product}: {qty}\n"
        text += "\n"

    if len(text) > 4000:
        await update.message.reply_text("البيانات كبيرة، استخدم /export")
        return

    await update.message.reply_text(text)

# =========================
# EXPORT
# =========================

async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    cursor.execute("""
    SELECT order_name, username, product, quantity, datetime
    FROM orders
    WHERE DATE(datetime)=?
    """, (today(),))

    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("لا يوجد بيانات")
        return

    df = pd.DataFrame(rows, columns=[
    "Order Name",
    "Username",
    "Product",
    "Quantity",
    "Timestamp"
])
    file_name = "orders.xlsx"
    df.to_excel(file_name, index=False)

    await update.message.reply_document(open(file_name, "rb"))

# =========================
# RESET
# =========================

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    cursor.execute("DELETE FROM orders")
    conn.commit()

    await update.message.reply_text("🗑 تم تصفير اليوم")

# =========================
# MAIN
# =========================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("details", details))  # 👈 NEW
    app.add_handler(CommandHandler("export", export_excel))
    app.add_handler(CommandHandler("reset", reset))

    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT, receive_name))

    print("Bot running...")
    keep_alive()
    app.run_polling()

if __name__ == "__main__":
    main()
