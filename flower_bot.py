# bot.py
# Minimal Telegram flower shop bot (aiogram v3.13)
# Sends album of bouquets (no captions) + separate numbered list with prices.
# Run: python3 bot.py

import asyncio, os, re, uuid, logging
from dataclasses import dataclass
from datetime import datetime
import aiosqlite

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto, LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN", "")


def _parse_ids(env_value: str):
    ids = []
    for part in env_value.split(","):
        part = part.split("#", 1)[0].strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


ADMIN_IDS = _parse_ids(os.getenv("ADMIN_IDS", ""))
if not BOT_TOKEN:
    raise SystemExit("Please set BOT_TOKEN in .env")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(); router = Router(); dp.include_router(router)

DB_PATH = "flower_shop.db"
SIZES = ["small","medium","big"]
HUMAN_SIZE = {"small":"Small","medium":"Medium","big":"Big"}

@dataclass
class Bouquet:
    id: int; number: int; size: str; title: str; price_u: int; file_id: str; in_stock: bool

CREATE_SQL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS bouquets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  number INTEGER NOT NULL,
  size TEXT NOT NULL CHECK(size IN ('small','medium','big')),
  title TEXT NOT NULL,
  price_u INTEGER NOT NULL,
  file_id TEXT NOT NULL,
  in_stock INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_b_unique ON bouquets(size, number);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  bouquet_id INTEGER NOT NULL,
  address TEXT NOT NULL,
  delivery_time TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending_payment',
  total_u INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(bouquet_id) REFERENCES bouquets(id)
);
"""

class OrderStates(StatesGroup):
    waiting_bouquet_number = State()
    waiting_address = State()
    waiting_time = State()

def main_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="Ассортимент"); kb.button(text="Мои заказы")
    if ADMIN_IDS: kb.button(text="Админ")
    kb.adjust(2); return kb.as_markup(resize_keyboard=True)

def size_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in SIZES: kb.button(text=HUMAN_SIZE[s], callback_data=f"size:{s}")
    kb.adjust(3); return kb.as_markup()

def numbers_keyboard(nums: list[int]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for n in nums: kb.button(text=str(n), callback_data=f"pick:{n}")
    kb.adjust(5); return kb.as_markup()

# --- DB helpers (new connection each time to avoid threading bug) ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL); await db.commit()

async def get_in_stock_by_size(size: str) -> list[Bouquet]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM bouquets WHERE size=? AND in_stock=1 ORDER BY number", (size,))
        rows = await cur.fetchall()
    return [Bouquet(id=r["id"], number=r["number"], size=r["size"], title=r["title"],
                    price_u=r["price_u"], file_id=r["file_id"], in_stock=bool(r["in_stock"])) for r in rows]

async def get_bouquet_by_size_and_number(size: str, number: int) -> Bouquet|None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM bouquets WHERE size=? AND number=?", (size, number))
        r = await cur.fetchone()
    if not r: return None
    return Bouquet(id=r["id"], number=r["number"], size=r["size"], title=r["title"],
                   price_u=r["price_u"], file_id=r["file_id"], in_stock=bool(r["in_stock"]))

async def create_order(user_id: int, bouquet_id: int, total_u: int, address: str, delivery_time: str) -> str:
    order_id = str(uuid.uuid4())[:8]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO orders(id,user_id,bouquet_id,address,delivery_time,total_u,created_at) VALUES(?,?,?,?,?,?,?)",
                         (order_id, user_id, bouquet_id, address, delivery_time, total_u, datetime.utcnow().isoformat()))
        await db.commit()
    return order_id

async def list_user_orders(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT o.id,o.status,o.total_u,o.created_at,b.title,b.size,b.number FROM orders o JOIN bouquets b ON b.id=o.bouquet_id WHERE o.user_id=? ORDER BY o.created_at DESC",(user_id,))
        rows = await cur.fetchall()
    return [dict(r) for r in rows]

# --- Handlers ---
@router.message(CommandStart())
async def start(m: Message):
    await m.answer("Привет! Я бот цветочного магазина. Выберите 👉 <b>Ассортимент</b>.", reply_markup=main_menu())

@router.message(F.text == "Ассортимент")
async def show_sizes(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Выберите размер букета:")
    await m.answer("Размеры:", reply_markup=size_keyboard())

@router.callback_query(F.data.startswith("size:"))
async def picked_size(cb: CallbackQuery, state: FSMContext):
    size = cb.data.split(":",1)[1]
    await state.update_data(size=size)
    items = await get_in_stock_by_size(size)
    if not items:
        await cb.message.edit_text(f"Пока нет букетов размера {HUMAN_SIZE[size]} в наличии."); return await cb.answer()

    # Album without captions
    media = [InputMediaPhoto(media=x.file_id) for x in items[:10]]
    try: await cb.message.edit_text("Букеты в наличии:")
    except Exception: await cb.message.answer("Букеты в наличии:")
    await cb.message.answer_media_group(media)

    # Separate list with titles + prices
    await cb.message.answer("\n".join([f"№{x.number} — {x.title} — ${x.price_u}" for x in items[:10]]))

    await cb.message.answer("Нажмите номер букетa:", reply_markup=numbers_keyboard([x.number for x in items[:30]]))
    await state.set_state(OrderStates.waiting_bouquet_number); await cb.answer()

@router.callback_query(OrderStates.waiting_bouquet_number, F.data.startswith("pick:"))
async def picked_number(cb: CallbackQuery, state: FSMContext):
    num = int(cb.data.split(":",1)[1])
    size = (await state.get_data()).get("size")
    item = await get_bouquet_by_size_and_number(size, num)
    if not item: return await cb.answer("Нет такого номера", show_alert=True)
    await state.update_data(bouquet_id=item.id, bouquet_title=item.title, price_u=item.price_u)
    await cb.message.answer(f"Вы выбрали: <b>№{item.number}</b> — {item.title}\nРазмер: {HUMAN_SIZE[item.size]}\nЦена: ${item.price_u}\n\nОтправьте адрес доставки:")
    await state.set_state(OrderStates.waiting_address); await cb.answer()

TIME_RE = re.compile(r"(сегодня|завтра)?\s*([0-2]?\d:[0-5]\d)", re.IGNORECASE)

@router.message(OrderStates.waiting_address)
async def got_address(m: Message, state: FSMContext):
    addr = m.text.strip()
    if len(addr) < 5: return await m.answer("Пожалуйста, укажите полный адрес.")
    await state.update_data(address=addr)
    await m.answer("Укажите желаемое время доставки (например: сегодня 18:30):")
    await state.set_state(OrderStates.waiting_time)

@router.message(OrderStates.waiting_time)
async def got_time(m: Message, state: FSMContext):
    t = m.text.strip()
    if not TIME_RE.search(t): return await m.answer("Укажите время в формате HH:MM (можно 'сегодня'/'завтра').")
    data = await state.get_data()
    kb = InlineKeyboardBuilder()
    if PROVIDER_TOKEN:
        kb.button(text="Оплатить в Telegram", callback_data="pay:invoice")
    else:
        kb.button(text="Оплатить картой (ссылка)", url="https://example-pay.page.link/checkout")
        kb.button(text="Подтвердить без оплаты (тест)", callback_data="pay:test")
    kb.button(text="⬅️ Назад", callback_data="pay:back")
    await state.update_data(delivery_time=t)
    await m.answer((f"<b>Проверим заказ:</b>\n"
                    f"Букет: {data['bouquet_title']}\n"
                    f"Сумма: ${data['price_u']}\n"
                    f"Адрес: {data['address']}\n"
                    f"Доставка: {t}\n\nЕсли всё верно — жмите оплату."),
                   reply_markup=kb.as_markup())

@router.callback_query(F.data == "pay:back")
async def pay_back(cb: CallbackQuery, state: FSMContext):
    await cb.message.answer("Ок, открыл выбор размеров заново.", reply_markup=size_keyboard())
    await state.clear(); await cb.answer()

@router.callback_query(F.data == "pay:test")
async def pay_test(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    order_id = await create_order(cb.from_user.id, d["bouquet_id"], d["price_u"], d["address"], d["delivery_time"])
    await cb.message.answer(f"Заказ <b>#{order_id}</b> оформлен. Статус: <b>ожидает оплаты</b> (тест).", reply_markup=main_menu())
    await state.clear(); await cb.answer()

@router.callback_query(F.data == "pay:invoice")
async def pay_invoice(cb: CallbackQuery, state: FSMContext):
    if not PROVIDER_TOKEN: return await cb.answer("Платёжный провайдер не настроен", show_alert=True)
    d = await state.get_data()
    prices = [LabeledPrice(label=d["bouquet_title"], amount=d["price_u"]*100)]
    await bot.send_invoice(chat_id=cb.from_user.id, title=d["bouquet_title"],
                           description=f"Доставка: {d['delivery_time']}\nАдрес: {d['address']}",
                           payload=f"order:{cb.from_user.id}", provider_token=PROVIDER_TOKEN,
                           currency="USD", prices=prices, need_name=True, need_phone_number=True,
                           need_shipping_address=False, start_parameter="flower_order")
    await cb.answer()

@router.message(F.successful_payment)
async def paid(m: Message, state: FSMContext):
    d = await state.get_data()
    if not d: return await m.answer("Спасибо за оплату! Ваш заказ обрабатывается.")
    order_id = await create_order(m.from_user.id, d["bouquet_id"], d["price_u"], d["address"], d["delivery_time"])
    await m.answer(f"Оплата получена! Заказ <b>#{order_id}</b> принят.", reply_markup=main_menu())
    await state.clear()

@router.message(F.text == "Мои заказы")
async def my_orders(m: Message):
    rows = await list_user_orders(m.from_user.id)
    if not rows: return await m.answer("У вас пока нет заказов.")
    await m.answer("\n\n".join([f"#<b>{r['id']}</b> — {r['title']} ({HUMAN_SIZE[r['size']]}, №{r['number']})\nСтатус: {r['status']} • Сумма: ${r['total_u']} • {r['created_at'][:16]}" for r in rows[:10]]))

# --- Admin ---
@router.message(F.text == "Админ")
async def admin(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    kb = ReplyKeyboardBuilder(); kb.button(text="➕ Добавить букет"); kb.button(text="📦 Список букетов"); kb.button(text="⬅️ В меню"); kb.adjust(2)
    await m.answer("Админ‑панель:", reply_markup=kb.as_markup(resize_keyboard=True))

class AdminStates(StatesGroup):
    add_wait_size = State(); add_wait_number = State(); add_wait_title = State(); add_wait_price = State(); add_wait_photo = State()

@router.message(F.text == "⬅️ В меню")
async def back_menu(m: Message): await m.answer("Главное меню:", reply_markup=main_menu())

@router.message(F.text == "📦 Список букетов")
async def admin_list(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    out = []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM bouquets ORDER BY size, number")
        for r in await cur.fetchall():
            mark = "✅" if r["in_stock"] else "❌"
            out.append(f"{mark} {r['size'].upper()} №{r['number']} — {r['title']} — ${r['price_u']} (id:{r['id']})")
    await m.answer("\n".join(out) if out else "Каталог пуст.")

@router.message(F.text == "➕ Добавить букет")
async def admin_add_start(m: Message, state: FSMContext):
    if m.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardBuilder()
    for s in SIZES: kb.button(text=HUMAN_SIZE[s], callback_data=f"admin:add:size:{s}")
    kb.adjust(3); await m.answer("Выберите размер нового букета:", reply_markup=kb.as_markup())
    await state.set_state(AdminStates.add_wait_size)

@router.callback_query(AdminStates.add_wait_size, F.data.startswith("admin:add:size:"))
async def admin_add_size(cb: CallbackQuery, state: FSMContext):
    size = cb.data.split(":")[-1]; await state.update_data(size=size)
    await cb.message.answer("Укажите номер букетa (целое число):")
    await state.set_state(AdminStates.add_wait_number); await cb.answer()

@router.message(AdminStates.add_wait_number)
async def admin_add_number(m: Message, state: FSMContext):
    try: number = int(m.text.strip())
    except: return await m.answer("Введите номер цифрами.")
    await state.update_data(number=number); await m.answer("Название/описание букета (кратко):")
    await state.set_state(AdminStates.add_wait_title)

@router.message(AdminStates.add_wait_title)
async def admin_add_title(m: Message, state: FSMContext):
    await state.update_data(title=m.text.strip()); await m.answer("Цена, $ (целое число):")
    await state.set_state(AdminStates.add_wait_price)

@router.message(AdminStates.add_wait_price)
async def admin_add_price(m: Message, state: FSMContext):
    try: price = int(m.text.strip())
    except: return await m.answer("Введите цену числом, $.")
    await state.update_data(price_u=price); await m.answer("Пришлите фото букетa одним изображением:")
    await state.set_state(AdminStates.add_wait_photo)

@router.message(AdminStates.add_wait_photo, F.photo)
async def admin_add_photo(m: Message, state: FSMContext):
    d = await state.get_data(); file_id = m.photo[-1].file_id
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO bouquets(number,size,title,price_u,file_id,in_stock) VALUES(?,?,?,?,?,1)", (d["number"], d["size"], d["title"], d["price_u"], file_id))
            await db.commit()
        except aiosqlite.IntegrityError: return await m.answer("Букет с таким номером уже есть в этом размере.")
    await m.answer("Добавлено!", reply_markup=main_menu()); await state.clear()

@router.message(Command("toggle"))
async def toggle_item(m: Message, command: CommandObject):
    if m.from_user.id not in ADMIN_IDS: return
    if not command.args or not command.args.strip().isdigit(): return await m.answer("Использование: /toggle <id>")
    item_id = int(command.args.strip())
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT in_stock FROM bouquets WHERE id=?", (item_id,)); row = await cur.fetchone()
        if not row: return await m.answer("Не найдено.")
        new_val = 0 if row[0] else 1
        await db.execute("UPDATE bouquets SET in_stock=? WHERE id=?", (new_val, item_id)); await db.commit()
    await m.answer(f"in_stock переключен на {new_val} для id={item_id}")

@router.message(Command("seed"))
async def seed(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany("INSERT OR IGNORE INTO bouquets(number,size,title,price_u,file_id,in_stock) VALUES(?,?,?,?,?,1)",
            [(1,'small','Bouquet of Peonies',45,'AgACAgIAAxkBAAIBQ2ZfXXXXXXX1',1),
             (2,'small','Bouquet of Spray Roses',60,'AgACAgIAAxkBAAIBQmZfXXXXXXX2',1),
             (3,'medium','Bouquet of Garden Roses',75,'AgACAgIAAxkBAAIBRWZfXXXXXXX3',1)])
        await db.commit()
    await m.answer("Demo bouquets added. Replace file_id with real photos.")

async def on_startup(): await init_db()
async def main(): await on_startup(); await dp.start_polling(bot)
if __name__ == "__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): pass
