import logging
import re
from typing import Dict, List, Optional, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
import random
import os
from datetime import datetime
from dotenv import load_dotenv
import sqlite3

# Загружаем переменные окружения
load_dotenv()

# Включаем логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
DATABASE_PATH = os.getenv('DATABASE_PATH', 'bot_data/bot.db')

# Создаем директорию если её нет
os.makedirs('bot_data', exist_ok=True)

# Глобальные переменные
waiting_users: List[int] = []
active_chats: Dict[int, int] = {}
total_connections: int = 0
reports: Dict[int, int] = {}
required_channels: List[Dict[str, str]] = []
chat_logs: Dict[str, List[Dict]] = {}
admin_states: Dict[int, str] = {}

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            gender TEXT,
            registered BOOLEAN DEFAULT FALSE,
            first_name TEXT,
            username TEXT,
            join_date TEXT,
            banned BOOLEAN DEFAULT FALSE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE,
            name TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            key TEXT PRIMARY KEY,
            value INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

# Загрузка данных из БД
def load_data():
    global total_connections, required_channels
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT channel_id, name FROM channels')
    required_channels = [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]
    cursor.execute('SELECT value FROM stats WHERE key = ?', ('total_connections',))
    row = cursor.fetchone()
    total_connections = row[0] if row else 0
    conn.close()
    logger.info(f"Данные загружены: каналов {len(required_channels)}, соединений {total_connections}")

# Сохранение данных в БД
def save_data():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM stats WHERE key = ?', ('total_connections',))
    cursor.execute('INSERT INTO stats (key, value) VALUES (?, ?)', ('total_connections', total_connections))
    conn.commit()
    conn.close()

# Функции для работы с БД
def get_user(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'id': row[0],
            'gender': row[1],
            'registered': bool(row[2]),
            'first_name': row[3],
            'username': row[4],
            'join_date': row[5],
            'banned': bool(row[6])
        }
    return None

def add_user(user_id: int, first_name: str, username: Optional[str]):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR IGNORE INTO users (id, first_name, username, join_date)
        VALUES (?, ?, ?, ?)
    ''', (user_id, first_name, username, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def update_user(user_id: int, **kwargs):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    for key, value in kwargs.items():
        cursor.execute(f'UPDATE users SET {key} = ? WHERE id = ?', (value, user_id))
    conn.commit()
    conn.close()

def get_all_users() -> Dict[int, Dict]:
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users')
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: {
        'gender': row[1],
        'registered': bool(row[2]),
        'first_name': row[3],
        'username': row[4],
        'join_date': row[5],
        'banned': bool(row[6])
    } for row in rows}

def add_channel(channel_id: str, name: str):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO channels (channel_id, name) VALUES (?, ?)', (channel_id, name))
    conn.commit()
    conn.close()

def remove_channel(channel_id: str):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM channels WHERE channel_id = ?', (channel_id,))
    conn.commit()
    conn.close()

def get_channels() -> List[Dict[str, str]]:
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT channel_id, name FROM channels')
    rows = cursor.fetchall()
    conn.close()
    return [{'id': row[0], 'name': row[1]} for row in rows]

# Проверка доступа пользователя
def check_user_access(user_id: int) -> bool:
    user = get_user(user_id)
    return user is not None and not user.get('banned', False)

# Загружаем данные при старте
init_db()
load_data()

# Клавиатуры
def get_main_keyboard():
    keyboard = [
        [KeyboardButton("🔍 Найти собеседника"), KeyboardButton("❌ Завершить чат")],
        [KeyboardButton("👥 Сменить пол"), KeyboardButton("📊 Статистика")],
        [KeyboardButton("ℹ️ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_chat_keyboard():
    keyboard = [
        [KeyboardButton("⏭️ Следующий"), KeyboardButton("❌ Завершить чат")],
        [KeyboardButton("🚨 Пожаловаться"), KeyboardButton("📊 Статистика")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_gender_keyboard():
    keyboard = [
        [InlineKeyboardButton("👨 Мужской", callback_data='gender_male')],
        [InlineKeyboardButton("👩 Женский", callback_data='gender_female')],
        [InlineKeyboardButton("🤖 Не указывать", callback_data='gender_none')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data='admin_stats')],
        [InlineKeyboardButton("📢 Управление каналами", callback_data='admin_channels')],
        [InlineKeyboardButton("👥 Управление пользователями", callback_data='admin_users')],
        [InlineKeyboardButton("💬 Просмотр чатов", callback_data='admin_chats')],
        [InlineKeyboardButton("🔧 Настройки", callback_data='admin_settings')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_channels_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Добавить канал", callback_data='admin_add_channel')],
        [InlineKeyboardButton("📋 Список каналов", callback_data='admin_list_channels')]
    ]
    if required_channels:
        keyboard.append([InlineKeyboardButton("🗑️ Удалить канал", callback_data='admin_remove_channel')])
        keyboard.append([InlineKeyboardButton("🗑️ Удалить все каналы", callback_data='admin_remove_all_channels')])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='admin_back')])
    return InlineKeyboardMarkup(keyboard)

def get_admin_users_keyboard():
    keyboard = [
        [InlineKeyboardButton("🚫 Забанить пользователя", callback_data='admin_ban_user')],
        [InlineKeyboardButton("✅ Разбанить пользователя", callback_data='admin_unban_user')],
        [InlineKeyboardButton("📋 Список заблокированных", callback_data='admin_banned_list')],
        [InlineKeyboardButton("📊 Топ по жалобам", callback_data='admin_reports_top')],
        [InlineKeyboardButton("🔙 Назад", callback_data='admin_back')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_channels_list_keyboard():
    keyboard = []
    for i, channel in enumerate(required_channels):
        keyboard.append([InlineKeyboardButton(f"🗑️ {channel['name']}", callback_data=f'remove_channel_{i}')])
    keyboard.append([InlineKeyboardButton("🔙 Назад к каналам", callback_data='admin_channels')])
    return InlineKeyboardMarkup(keyboard)

# Проверка подписки на каналы
async def check_channel_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not required_channels:
        return True
    
    for channel in required_channels:
        try:
            member = await context.bot.get_chat_member(chat_id=channel['id'], user_id=user_id)
            if member.status in ['left', 'kicked']:
                logger.info(f"Пользователь {user_id} не подписан на канал {channel['id']}")
                return False
        except Exception as e:
            error_message = str(e).lower()
            logger.error(f"Ошибка проверки подписки на канал {channel['id']}: {e}")
            
            if "member list is inaccessible" in error_message or "chat not found" in error_message:
                logger.warning(f"Пропускаем проверку канала {channel['id']} - нет доступа")
                continue
            
            return False
    
    return True

# Показ требования подписки
async def show_subscription_requirement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📢 <b>Для использования бота подпишитесь на каналы:</b>\n\n"
    keyboard = []
    for i, channel in enumerate(required_channels):
        channel_url = f"https://t.me/{channel['id'].lstrip('@')}"
        text += f"{i+1}. {channel['name']}\n"
        keyboard.append([InlineKeyboardButton(f"📢 {channel['name']}", url=channel_url)])
    
    keyboard.append([InlineKeyboardButton("✅ Проверить подписку", callback_data='check_subscription')])
    text += "\n<i>После подписки нажмите 'Проверить подписку'</i>"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
        )

# Команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    existing_user = get_user(user_id)
    if not existing_user:
        add_user(user_id, user.first_name, user.username)
        await update.message.reply_text(
            f'👋 <b>Добро пожаловать, {user.first_name}!</b>\n\n'
            '🎭 <i>Бот для анонимного общения</i>\n\n'
            '👤 Сначала выберите ваш пол:',
            reply_markup=get_gender_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return
    
    if not existing_user.get('registered', False):
        await update.message.reply_text(
            '👤 Пожалуйста, выберите ваш пол:',
            reply_markup=get_gender_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return
    
    gender_emoji = {'male': '👨', 'female': '👩', 'none': '🤖'}.get(existing_user['gender'], '🤖')
    
    await update.message.reply_text(
        f'🤖 <b>С возвращением, {user.first_name}!</b> {gender_emoji}\n\n'
        '✨ Готовы к новому общению?\n'
        '🌍 Находите интересных собеседников!\n\n'
        '<b>Используйте кнопки ниже:</b>',
        reply_markup=get_main_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет прав администратора")
        return
    
    users = get_all_users()
    male_count = sum(1 for u in users.values() if u.get('gender') == 'male')
    female_count = sum(1 for u in users.values() if u.get('gender') == 'female')
    none_count = sum(1 for u in users.values() if u.get('gender') == 'none')
    banned_count = sum(1 for u in users.values() if u.get('banned', False))
    
    text = (
        f'👑 <b>АДМИН ПАНЕЛЬ</b>\n\n'
        f'👥 Всего пользователей: {len(users)}\n'
        f'👨 Мужчин: {male_count}\n'
        f'👩 Женщин: {female_count}\n'
        f'🤖 Не указали: {none_count}\n'
        f'🚫 Заблокированных: {banned_count}\n\n'
        f'💬 Активных диалогов: {len(active_chats) // 2}\n'
        f'⏳ В очереди: {len(waiting_users)}\n'
        f'🔗 Всего соединений: {total_connections}\n'
        f'📢 Обязательных каналов: {len(required_channels)}\n\n'
        f'Выберите действие:'
    )
    
    await update.message.reply_text(
        text, reply_markup=get_admin_main_keyboard(), parse_mode=ParseMode.HTML
    )

# Обработчики callback'ов
async def handle_admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет прав администратора")
        return
    
    data = query.data
    
    if data == 'admin_back':
        await admin_panel_callback(update, context)
    elif data == 'admin_stats':
        await admin_stats_callback(update, context)
    elif data == 'admin_channels':
        await admin_channels_callback(update, context)
    elif data == 'admin_users':
        await admin_users_callback(update, context)
    elif data == 'admin_add_channel':
        await admin_add_channel_callback(update, context)
    elif data == 'admin_list_channels':
        await admin_list_channels_callback(update, context)
    elif data == 'admin_remove_channel':
        await admin_remove_channel_callback(update, context)
    elif data == 'admin_remove_all_channels':
        await admin_remove_all_channels_callback(update, context)
    elif data.startswith('remove_channel_'):
        channel_index = int(data.split('_')[2])
        await remove_specific_channel(update, context, channel_index)
    elif data == 'admin_ban_user':
        await admin_ban_user_callback(update, context)
    elif data == 'admin_unban_user':
        await admin_unban_user_callback(update, context)
    elif data == 'admin_banned_list':
        await admin_banned_list_callback(update, context)

async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    users = get_all_users()
    male_count = sum(1 for u in users.values() if u.get('gender') == 'male')
    female_count = sum(1 for u in users.values() if u.get('gender') == 'female')
    none_count = sum(1 for u in users.values() if u.get('gender') == 'none')
    banned_count = sum(1 for u in users.values() if u.get('banned', False))
    
    text = (
        f'👑 <b>АДМИН ПАНЕЛЬ</b>\n\n'
        f'👥 Всего пользователей: {len(users)}\n'
        f'👨 Мужчин: {male_count}\n'
        f'👩 Женщин: {female_count}\n'
        f'🤖 Не указали: {none_count}\n'
        f'🚫 Заблокированных: {banned_count}\n\n'
        f'💬 Активных диалогов: {len(active_chats) // 2}\n'
        f'⏳ В очереди: {len(waiting_users)}\n'
        f'🔗 Всего соединений: {total_connections}\n'
        f'📢 Обязательных каналов: {len(required_channels)}\n\n'
        f'Выберите действие:'
    )
    
    await query.edit_message_text(
        text, reply_markup=get_admin_main_keyboard(), parse_mode=ParseMode.HTML
    )

async def admin_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    users = get_all_users()
    male_count = sum(1 for u in users.values() if u.get('gender') == 'male')
    female_count = sum(1 for u in users.values() if u.get('gender') == 'female')
    none_count = sum(1 for u in users.values() if u.get('gender') == 'none')
    banned_count = sum(1 for u in users.values() if u.get('banned', False))
    total_reports = sum(reports.values())
    
    # Последние регистрации
    recent_users = sorted(
        [(uid, data.get('join_date', '')) for uid, data in users.items() if data.get('join_date')],
        key=lambda x: x[1], reverse=True
    )[:5]
    
    stats_text = (
        f'📊 <b>ДЕТАЛЬНАЯ СТАТИСТИКА</b>\n\n'
        f'👥 Всего пользователей: {len(users)}\n'
        f'├ 👨 Мужчин: {male_count}\n'
        f'├ 👩 Женщин: {female_count}\n'
        f'└ 🤖 Не указали: {none_count}\n\n'
        f'🚫 Заблокированных: {banned_count}\n'
        f'🚨 Всего жалоб: {total_reports}\n\n'
        f'💬 Активных диалогов: {len(active_chats) // 2}\n'
        f'⏳ В очереди поиска: {len(waiting_users)}\n'
        f'🔗 Всего соединений: {total_connections}\n'
        f'📢 Обязательных каналов: {len(required_channels)}\n'
        f'💾 Активных чат-логов: {len(chat_logs)}\n\n'
        f'📅 <b>Последние регистрации:</b>\n'
    )
    
    for i, (uid, join_date) in enumerate(recent_users, 1):
        try:
            date_obj = datetime.fromisoformat(join_date)
            date_str = date_obj.strftime('%d.%m %H:%M')
            stats_text += f'{i}. ID {uid} - {date_str}\n'
        except:
            stats_text += f'{i}. ID {uid} - н/д\n'
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='admin_back')]]
    
    await query.edit_message_text(
        stats_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
    )

async def admin_channels_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    text = f'📢 <b>УПРАВЛЕНИЕ КАНАЛАМИ</b>\n\n'
    if required_channels:
        text += f'Всего каналов: {len(required_channels)}\n\n'
        for i, channel in enumerate(required_channels, 1):
            text += f'{i}. <b>{channel["name"]}</b>\n'
            text += f'   ID: <code>{channel["id"]}</code>\n\n'
    else:
        text += 'Каналы не настроены\nПроверка подписки отключена\n\n'
    
    text += 'Выберите действие:'
    
    await query.edit_message_text(
        text, reply_markup=get_admin_channels_keyboard(), parse_mode=ParseMode.HTML
    )

async def admin_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    banned_users = [uid for uid, data in get_all_users().items() if data.get('banned', False)]
    reported_users = sorted(reports.items(), key=lambda x: x[1], reverse=True)[:5]
    
    text = (
        f'👥 <b>УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ</b>\n\n'
        f'🚫 Заблокированных: {len(banned_users)}\n'
        f'🚨 Пользователей с жалобами: {len(reports)}\n\n'
    )
    
    if reported_users:
        text += '<b>Топ по жалобам:</b>\n'
        for i, (uid, count) in enumerate(reported_users, 1):
            username = get_user(uid).get('username', 'н/д')
            text += f'{i}. ID {uid} (@{username}) - {count} жалоб\n'
    
    text += '\nВыберите действие:'
    
    await query.edit_message_text(
        text, reply_markup=get_admin_users_keyboard(), parse_mode=ParseMode.HTML
    )

async def admin_add_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    admin_states[user_id] = 'waiting_channel_id'
    
    await query.edit_message_text(
        '📢 <b>ДОБАВЛЕНИЕ КАНАЛА</b>\n\n'
        '📝 Отправьте ID канала (например: @mychannel)\n\n'
        '⚠️ <b>Важно:</b> Бот должен быть администратором канала для проверки подписок\n\n'
        '❌ Для отмены напишите /cancel',
        parse_mode=ParseMode.HTML
    )

async def admin_list_channels_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if not required_channels:
        await query.edit_message_text(
            '📢 <b>СПИСОК КАНАЛОВ</b>\n\n'
            'Каналы не добавлены\n\n'
            'Используйте "Добавить канал" для добавления',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_channels')]]),
            parse_mode=ParseMode.HTML
        )
        return
    
    text = f'📢 <b>СПИСОК КАНАЛОВ</b> ({len(required_channels)})\n\n'
    for i, channel in enumerate(required_channels, 1):
        text += f'{i}. <b>{channel["name"]}</b>\n'
        text += f'   ID: <code>{channel["id"]}</code>\n'
        text += f'   URL: https://t.me/{channel["id"].lstrip("@")}\n\n'
    
    keyboard = [[InlineKeyboardButton("🔙 Назад к каналам", callback_data='admin_channels')]]
    
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
    )

async def admin_remove_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if not required_channels:
        await query.edit_message_text(
            '🗑️ <b>УДАЛЕНИЕ КАНАЛА</b>\n\n'
            'Нет каналов для удаления',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_channels')]]),
            parse_mode=ParseMode.HTML
        )
        return
    
    text = '🗑️ <b>УДАЛЕНИЕ КАНАЛА</b>\n\n'
    text += 'Выберите канал для удаления:'
    
    await query.edit_message_text(
        text, reply_markup=get_channels_list_keyboard(), parse_mode=ParseMode.HTML
    )

async def remove_specific_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_index: int):
    query = update.callback_query
    
    if channel_index < len(required_channels):
        removed_channel = required_channels.pop(channel_index)
        remove_channel(removed_channel['id'])
        
        await query.edit_message_text(
            f'✅ <b>КАНАЛ УДАЛЕН</b>\n\n'
            f'Название: {removed_channel["name"]}\n'
            f'ID: {removed_channel["id"]}\n\n'
            f'Осталось каналов: {len(required_channels)}',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К каналам", callback_data='admin_channels')]]),
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text(
            '❌ Канал не найден',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_channels')]]),
            parse_mode=ParseMode.HTML
        )

async def admin_remove_all_channels_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if not required_channels:
        await query.edit_message_text(
            '🗑️ Каналы уже удалены',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_channels')]]),
            parse_mode=ParseMode.HTML
        )
        return
    
    removed_count = len(required_channels)
    required_channels.clear()
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM channels')
    conn.commit()
    conn.close()
    
    await query.edit_message_text(
        f'🗑️ <b>ВСЕ КАНАЛЫ УДАЛЕНЫ</b>\n\n'
        f'Удалено каналов: {removed_count}\n'
        f'✅ Проверка подписки отключена',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К каналам", callback_data='admin_channels')]]),
        parse_mode=ParseMode.HTML
    )

async def admin_ban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    admin_states[user_id] = 'waiting_ban_user_id'
    
    await query.edit_message_text(
        '🚫 <b>БЛОКИРОВКА ПОЛЬЗОВАТЕЛЯ</b>\n\n'
        '📝 Отправьте ID пользователя для блокировки\n\n'
        '❌ Для отмены напишите /cancel',
        parse_mode=ParseMode.HTML
    )

async def admin_unban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    admin_states[user_id] = 'waiting_unban_user_id'
    
    await query.edit_message_text(
        '✅ <b>РАЗБЛОКИРОВКА ПОЛЬЗОВАТЕЛЯ</b>\n\n'
        '📝 Отправьте ID пользователя для разблокировки\n\n'
        '❌ Для отмены напишите /cancel',
        parse_mode=ParseMode.HTML
    )

async def admin_banned_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    banned_users = [(uid, data) for uid, data in get_all_users().items() if data.get('banned', False)]
    
    if not banned_users:
        await query.edit_message_text(
            '✅ <b>СПИСОК ЗАБЛОКИРОВАННЫХ</b>\n\n'
            'Заблокированных пользователей нет',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_users')]]),
            parse_mode=ParseMode.HTML
        )
        return
    
    text = f'🚫 <b>ЗАБЛОКИРОВАННЫЕ ПОЛЬЗОВАТЕЛИ</b> ({len(banned_users)})\n\n'
    
    for i, (uid, data) in enumerate(banned_users[:10], 1):
        username = data.get('username', 'н/д')
        first_name = data.get('first_name', 'н/д')
        text += f'{i}. ID {uid}\n'
        text += f'   👤 {first_name} (@{username})\n'
        text += f'   🚨 Жалоб: {reports.get(uid, 0)}\n\n'
    
    if len(banned_users) > 10:
        text += f'... и ещё {len(banned_users) - 10} пользователей'
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='admin_users')]]
    
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
    )

# Обработка состояний админа
async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID or user_id not in admin_states:
        return False
    
    state = admin_states[user_id]
    text = update.message.text
    
    if text == '/cancel':
        del admin_states[user_id]
        await update.message.reply_text(
            '❌ <b>Действие отменено</b>',
            parse_mode=ParseMode.HTML
        )
        return True
    
    if state == 'waiting_channel_id':
        admin_states[user_id] = 'waiting_channel_name'
        admin_states[f'{user_id}_channel_id'] = text
        await update.message.reply_text(
            f'📝 <b>ID канала получен:</b> <code>{text}</code>\n\n'
            f'Теперь введите название канала:\n\n'
            f'❌ Для отмены напишите /cancel',
            parse_mode=ParseMode.HTML
        )
        return True
    
    elif state == 'waiting_channel_name':
        channel_id = admin_states.get(f'{user_id}_channel_id')
        channel_name = text
        
        # Очищаем состояние
        del admin_states[user_id]
        if f'{user_id}_channel_id' in admin_states:
            del admin_states[f'{user_id}_channel_id']
        
        # Проверяем существование канала
        try:
            chat_info = await context.bot.get_chat(channel_id)
            
            # Проверяем права бота
            try:
                bot_member = await context.bot.get_chat_member(channel_id, context.bot.id)
                if bot_member.status not in ['administrator', 'creator']:
                    await update.message.reply_text(
                        f'⚠️ <b>ПРЕДУПРЕЖДЕНИЕ!</b>\n\n'
                        f'Бот не является администратором канала {channel_id}\n'
                        f'Проверка подписки может не работать\n\n'
                        f'Канал всё равно добавлен в список',
                        parse_mode=ParseMode.HTML
                    )
            except Exception as e:
                await update.message.reply_text(
                    f'⚠️ <b>НЕ УДАЕТСЯ ПРОВЕРИТЬ ПРАВА</b>\n\n'
                    f'Канал: {channel_id}\n'
                    f'Ошибка: {str(e)[:100]}\n\n'
                    f'Канал добавлен, но проверка может не работать',
                    parse_mode=ParseMode.HTML
                )
            
        except Exception as e:
            await update.message.reply_text(
                f'❌ <b>ОШИБКА ДОБАВЛЕНИЯ</b>\n\n'
                f'Канал {channel_id} недоступен\n'
                f'Ошибка: {str(e)[:200]}\n\n'
                f'Проверьте правильность ID канала',
                parse_mode=ParseMode.HTML
            )
            return True
        
        # Проверяем дубликаты
        for channel in required_channels:
            if channel['id'] == channel_id:
                await update.message.reply_text(
                    f'❌ <b>КАНАЛ УЖЕ СУЩЕСТВУЕТ</b>\n\n'
                    f'Канал {channel_id} уже в списке',
                    parse_mode=ParseMode.HTML
                )
                return True
        
        # Добавляем канал
        add_channel(channel_id, channel_name)
        required_channels.append({
            'id': channel_id,
            'name': channel_name
        })
        
        await update.message.reply_text(
            f'✅ <b>КАНАЛ ДОБАВЛЕН</b>\n\n'
            f'📢 Название: {channel_name}\n'
            f'🆔 ID: <code>{channel_id}</code>\n'
            f'🔗 URL: https://t.me/{channel_id.lstrip("@")}\n\n'
            f'📊 Всего каналов: {len(required_channels)}',
            parse_mode=ParseMode.HTML
        )
        return True
    
    elif state == 'waiting_ban_user_id':
        try:
            ban_user_id = int(text)
        except ValueError:
            await update.message.reply_text(
                '❌ <b>НЕВЕРНЫЙ ID</b>\n\n'
                'ID должен быть числом\n'
                'Попробуйте ещё раз или напишите /cancel',
                parse_mode=ParseMode.HTML
            )
            return True
        
        del admin_states[user_id]
        
        if get_user(ban_user_id):
            update_user(ban_user_id, banned=True)
            
            # Разрываем активные чаты
            if ban_user_id in active_chats:
                partner_id = active_chats[ban_user_id]
                del active_chats[ban_user_id]
                del active_chats[partner_id]
                
                try:
                    await context.bot.send_message(
                        chat_id=partner_id,
                        text='❌ <b>Диалог завершен</b>\n\n'
                             '⚠️ Собеседник был заблокирован',
                        reply_markup=get_main_keyboard(),
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
            
            # Удаляем из очереди
            if ban_user_id in waiting_users:
                waiting_users.remove(ban_user_id)
            
            user_info = get_user(ban_user_id)
            await update.message.reply_text(
                f'🚫 <b>ПОЛЬЗОВАТЕЛЬ ЗАБЛОКИРОВАН</b>\n\n'
                f'ID: {ban_user_id}\n'
                f'Имя: {user_info.get("first_name", "н/д")}\n'
                f'Username: @{user_info.get("username", "н/д")}\n'
                f'Жалоб: {reports.get(ban_user_id, 0)}',
                parse_mode=ParseMode.HTML
            )
            
            # Уведомляем пользователя
            try:
                await context.bot.send_message(
                    chat_id=ban_user_id,
                    text='🚫 <b>ВЫ ЗАБЛОКИРОВАНЫ</b>\n\n'
                         '❌ Доступ к боту ограничен\n'
                         '📞 Для разблокировки обратитесь к администрации',
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        else:
            await update.message.reply_text(
                f'❌ <b>ПОЛЬЗОВАТЕЛЬ НЕ НАЙДЕН</b>\n\n'
                f'ID {ban_user_id} не существует в базе',
                parse_mode=ParseMode.HTML
            )
        return True
    
    elif state == 'waiting_unban_user_id':
        try:
            unban_user_id = int(text)
        except ValueError:
            await update.message.reply_text(
                '❌ <b>НЕВЕРНЫЙ ID</b>\n\n'
                'ID должен быть числом\n'
                'Попробуйте ещё раз или напишите /cancel',
                parse_mode=ParseMode.HTML
            )
            return True
        
        del admin_states[user_id]
        
        if get_user(unban_user_id):
            if get_user(unban_user_id).get('banned', False):
                update_user(unban_user_id, banned=False)
                
                user_info = get_user(unban_user_id)
                await update.message.reply_text(
                    f'✅ <b>ПОЛЬЗОВАТЕЛЬ РАЗБЛОКИРОВАН</b>\n\n'
                    f'ID: {unban_user_id}\n'
                    f'Имя: {user_info.get("first_name", "н/д")}\n'
                    f'Username: @{user_info.get("username", "н/д")}',
                    parse_mode=ParseMode.HTML
                )
                
                # Уведомляем пользователя
                try:
                    await context.bot.send_message(
                        chat_id=unban_user_id,
                        text='✅ <b>ВЫ РАЗБЛОКИРОВАНЫ</b>\n\n'
                             '🎉 Доступ к боту восстановлен\n'
                             '🤝 Используйте /start для продолжения',
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
            else:
                await update.message.reply_text(
                    f'❌ <b>ПОЛЬЗОВАТЕЛЬ НЕ ЗАБЛОКИРОВАН</b>\n\n'
                    f'ID {unban_user_id} не находится в блокировке',
                    parse_mode=ParseMode.HTML
                )
        else:
            await update.message.reply_text(
                f'❌ <b>ПОЛЬЗОВАТЕЛЬ НЕ НАЙДЕН</b>\n\n'
                f'ID {unban_user_id} не существует в базе',
                parse_mode=ParseMode.HTML
            )
        return True
    
    return False

# Обработка выбора пола
async def handle_gender_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    gender = query.data.split('_')[1]
    
    update_user(user_id, gender=gender, registered=True)
    
    gender_text = {'male': 'мужской', 'female': 'женский', 'none': 'не указан'}[gender]
    gender_emoji = {'male': '👨', 'female': '👩', 'none': '🤖'}[gender]
    
    await query.edit_message_text(
        f'✅ <b>Регистрация завершена!</b> {gender_emoji}\n\n'
        f'👤 Пол: {gender_text}\n\n'
        '🎭 Теперь вы можете найти собеседника!',
        parse_mode=ParseMode.HTML
    )
    
    await context.bot.send_message(
        chat_id=user_id,
        text='🔥 <b>Добро пожаловать в анонимный чат!</b>\n\n'
             '✨ Особенности:\n'
             '📱 Можно отправлять фото, видео, стикеры\n'
             '🔄 Кнопка "Следующий" для смены собеседника\n'
             '🛡️ Система жалоб для безопасности\n\n'
             '<b>Начните общение:</b>',
        reply_markup=get_main_keyboard(),
        parse_mode=ParseMode.HTML
    )

# Обработка текстовых команд
async def handle_text_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Сначала проверяем админские состояния
    if await handle_admin_input(update, context):
        return
    
    text = update.message.text
    user_id = update.effective_user.id
    
    # Проверяем регистрацию пользователя
    user = get_user(user_id)
    if not user or not user.get('registered', False):
        await update.message.reply_text(
            '❌ Сначала завершите регистрацию с помощью /start',
            parse_mode=ParseMode.HTML
        )
        return
    
    # Проверяем блокировку
    if not check_user_access(user_id):
        await update.message.reply_text(
            '🚫 <b>Доступ запрещен</b>\n\n'
            '❌ Вы заблокированы администрацией',
            parse_mode=ParseMode.HTML
        )
        return
    
    if text == "🔍 Найти собеседника":
        await find_partner(update, context)
    elif text == "❌ Завершить чат":
        await stop_chat(update, context)
    elif text == "⏭️ Следующий":
        await next_partner(update, context)
    elif text == "🚨 Пожаловаться":
        await report_user(update, context)
    elif text == "👥 Сменить пол":
        await change_gender(update, context)
    elif text == "📊 Статистика":
        await show_stats(update, context)
    elif text == "ℹ️ Помощь":
        await show_help(update, context)
    else:
        await handle_chat_message(update, context)

# Поиск собеседника
async def find_partner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global total_connections
    user_id = update.effective_user.id
    
    # Проверяем подписку только при поиске собеседника
    if not await check_channel_subscription(user_id, context):
        await show_subscription_requirement(update, context)
        return
    
    if user_id in active_chats:
        await update.message.reply_text(
            '💬 <b>Вы уже в диалоге!</b>\n\n'
            '🛑 Используйте "Завершить чат" чтобы выйти.',
            parse_mode=ParseMode.HTML
        )
        return
    
    if user_id in waiting_users:
        await update.message.reply_text(
            '⏳ <b>Поиск собеседника...</b>\n\n'
            '🔍 Вы уже в очереди! Ожидайте подключения.',
            parse_mode=ParseMode.HTML
        )
        return
    
    if waiting_users:
        partner_id = waiting_users.pop(0)
        active_chats[user_id] = partner_id
        active_chats[partner_id] = user_id
        total_connections += 1
        save_data()
        
        chat_id = f"{min(user_id, partner_id)}_{max(user_id, partner_id)}_{total_connections}"
        chat_logs[chat_id] = []
        
        user_gender = get_user(user_id).get('gender', 'none')
        partner_gender = get_user(partner_id).get('gender', 'none')
        
        gender_emoji = {'male': '👨', 'female': '👩', 'none': '🤖'}
        
        await update.message.reply_text(
            f'✅ <b>Собеседник найден!</b> {gender_emoji.get(partner_gender, "🤖")}\n\n'
            '🎭 Теперь вы можете общаться анонимно\n'
            '📱 Отправляйте текст, фото, видео, стикеры\n'
            '⏭️ Используйте "Следующий" для смены собеседника\n\n'
            '⚠️ <i>Будьте вежливы и уважайте собеседника</i>',
            reply_markup=get_chat_keyboard(),
            parse_mode=ParseMode.HTML
        )
        
        await context.bot.send_message(
            chat_id=partner_id,
            text=f'✅ <b>Собеседник найден!</b> {gender_emoji.get(user_gender, "🤖")}\n\n'
                 '🎭 Теперь вы можете общаться анонимно\n'
                 '📱 Отправляйте текст, фото, видео, стикеры\n'
                 '⏭️ Используйте "Следующий" для смены собеседника\n\n'
                 '⚠️ <i>Будьте вежливы и уважайте собеседника</i>',
            reply_markup=get_chat_keyboard(),
            parse_mode=ParseMode.HTML
        )
    else:
        waiting_users.append(user_id)
        await update.message.reply_text(
            '🔍 <b>Ищем собеседника...</b>\n\n'
            '⏳ Пожалуйста, подождите\n'
            '👥 Как только кто-то подключится, мы вас уведомим!\n\n'
            '❌ Используйте "Завершить чат" для отмены поиска',
            parse_mode=ParseMode.HTML
        )

# Поиск следующего собеседника
async def next_partner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in active_chats:
        await update.message.reply_text(
            '❌ <b>Вы не в диалоге</b>\n\n'
            '🔍 Сначала найдите собеседника',
            parse_mode=ParseMode.HTML
        )
        return
    
    partner_id = active_chats[user_id]
    del active_chats[user_id]
    del active_chats[partner_id]
    
    try:
        await context.bot.send_message(
            chat_id=partner_id,
            text='⏭️ <b>Собеседник перешел к следующему</b>\n\n'
                 '🔍 Используйте кнопки для поиска нового собеседника',
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления партнера: {e}")
    
    await find_partner(update, context)

# Завершение диалога
async def stop_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id in waiting_users:
        waiting_users.remove(user_id)
        await update.message.reply_text(
            '❌ <b>Поиск отменен</b>\n\n'
            '🔍 Можете начать новый поиск',
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return
    
    if user_id not in active_chats:
        await update.message.reply_text(
            '❌ <b>Вы не в диалоге</b>',
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return
    
    partner_id = active_chats[user_id]
    del active_chats[user_id]
    del active_chats[partner_id]
    
    await update.message.reply_text(
        '❌ <b>Диалог завершен</b>\n\n'
        '🔍 Можете найти нового собеседника',
        reply_markup=get_main_keyboard(),
        parse_mode=ParseMode.HTML
    )
    
    try:
        await context.bot.send_message(
            chat_id=partner_id,
            text='❌ <b>Собеседник покинул диалог</b>\n\n'
                 '🔍 Можете найти нового собеседника',
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления партнера о завершении: {e}")

# Жалоба на собеседника
async def report_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in active_chats:
        await update.message.reply_text(
            '❌ <b>Вы не в диалоге</b>',
            parse_mode=ParseMode.HTML
        )
        return
    
    partner_id = active_chats[user_id]
    reports[partner_id] = reports.get(partner_id, 0) + 1
    del active_chats[user_id]
    del active_chats[partner_id]
    
    await update.message.reply_text(
        '🚨 <b>Жалоба отправлена</b>\n\n'
        '✅ Диалог завершен\n'
        '🛡️ Спасибо за поддержание безопасности!',
        reply_markup=get_main_keyboard(),
        parse_mode=ParseMode.HTML
    )
    
    try:
        await context.bot.send_message(
            chat_id=partner_id,
            text='❌ <b>Диалог завершен</b>\n\n'
                 '⚠️ На вас поступила жалоба\n'
                 '🤝 Пожалуйста, будьте вежливы с другими пользователями',
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления о жалобе: {e}")
    
    # Уведомляем админа
    if ADMIN_ID:
        try:
            partner_info = get_user(partner_id)
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f'🚨 <b>НОВАЯ ЖАЛОБА</b>\n\n'
                     f'👤 На пользователя: {partner_id}\n'
                     f'📛 Имя: {partner_info.get("first_name", "н/д")}\n'
                     f'📱 Username: @{partner_info.get("username", "н/д")}\n'
                     f'🚨 Всего жалоб: {reports[partner_id]}\n'
                     f'📝 От пользователя: {user_id}\n\n'
                     f'{"⚠️ КРИТИЧЕСКОЕ КОЛИЧЕСТВО ЖАЛОБ!" if reports[partner_id] >= 3 else ""}',
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления админа о жалобе: {e}")

# Смена пола
async def change_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '👤 <b>Выберите новый пол:</b>',
        reply_markup=get_gender_keyboard(),
        parse_mode=ParseMode.HTML
    )

# Статистика
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    user_gender = user.get('gender', 'none')
    gender_emoji = {'male': '👨', 'female': '👩', 'none': '🤖'}.get(user_gender, '🤖')
    
    users = get_all_users()
    male_count = sum(1 for u in users.values() if u.get('gender') == 'male')
    female_count = sum(1 for u in users.values() if u.get('gender') == 'female')
    none_count = sum(1 for u in users.values() if u.get('gender') == 'none')
    
    stats_text = (
        f'📊 <b>Статистика бота</b>\n\n'
        f'👤 Ваш пол: {gender_emoji}\n\n'
        f'👥 Всего пользователей: {len(users)}\n'
        f'👨 Мужчин: {male_count}\n'
        f'👩 Женщин: {female_count}\n'
        f'🤖 Не указали: {none_count}\n\n'
        f'💬 Активных диалогов: {len(active_chats) // 2}\n'
        f'⏳ В очереди: {len(waiting_users)}\n'
        f'🔗 Всего соединений: {total_connections}\n\n'
        f'🎭 <i>Присоединяйтесь к анонимному общению!</i>'
    )
    
    await update.message.reply_text(stats_text, parse_mode=ParseMode.HTML)

# Помощь
async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        'ℹ️ <b>Как пользоваться ботом:</b>\n\n'
        '🔍 <b>Найти собеседника</b> - поиск случайного собеседника\n'
        '⏭️ <b>Следующий</b> - поиск нового собеседника\n'
        '❌ <b>Завершить чат</b> - выход из диалога\n'
        '🚨 <b>Пожаловаться</b> - жалоба на неподобающее поведение\n'
        '👥 <b>Сменить пол</b> - изменить информацию о поле\n'
        '📊 <b>Статистика</b> - информация о боте\n\n'
        '📱 <b>Поддерживаемые форматы:</b>\n'
        '• Текстовые сообщения\n'
        '• Фотографии\n'
        '• Видео\n'
        '• Стикеры\n'
        '• Голосовые сообщения\n'
        '• Аудиофайлы\n'
        '• Документы\n'
        '• Видеосообщения (кружочки)\n\n'
        '⚠️ <b>Правила:</b>\n'
        '• Будьте вежливы\n'
        '• Не делитесь личной информацией\n'
        '• Сообщайте о нарушениях\n'
        '• Уважайте собеседника'
    )
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

# Обработка сообщений в чате
async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in active_chats:
        await update.message.reply_text(
            '❌ <b>Вы не в диалоге</b>\n\n'
            '🔍 Сначала найдите собеседника',
            parse_mode=ParseMode.HTML
        )
        return
    
    partner_id = active_chats[user_id]
    chat_id = f"{min(user_id, partner_id)}_{max(user_id, partner_id)}"
    
    if chat_id not in chat_logs:
        chat_logs[chat_id] = []
    
    # Логируем сообщение
    chat_logs[chat_id].append({
        'from_user': user_id,
        'to_user': partner_id,
        'text': update.message.text,
        'timestamp': datetime.now().isoformat()
    })
    
    prefixes = ['🎭', '👤', '🗣️', '💭', '🎪', '🎨']
    prefix = random.choice(prefixes)
    
    try:
        await context.bot.send_message(
            chat_id=partner_id,
            text=f'{prefix} <i>{update.message.text}</i>',
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
        # Разрываем соединение при ошибке
        if user_id in active_chats:
            del active_chats[user_id]
        if partner_id in active_chats:
            del active_chats[partner_id]
        
        await update.message.reply_text(
            '❌ <b>Ошибка отправки сообщения</b>\n\n'
            '🔍 Попробуйте найти нового собеседника',
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML
        )

# Обработка медиа
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in active_chats:
        await update.message.reply_text(
            '❌ <b>Вы не в диалоге</b>\n\n'
            '🔍 Сначала найдите собеседника',
            parse_mode=ParseMode.HTML
        )
        return
    
    partner_id = active_chats[user_id]
    
    try:
        await update.message.forward(chat_id=partner_id)
        
        # Логируем медиа
        chat_id = f"{min(user_id, partner_id)}_{max(user_id, partner_id)}"
        if chat_id not in chat_logs:
            chat_logs[chat_id] = []
        
        media_type = "unknown"
        if update.message.photo:
            media_type = "photo"
        elif update.message.video:
            media_type = "video"
        elif update.message.voice:
            media_type = "voice"
        elif update.message.sticker:
            media_type = "sticker"
        elif update.message.audio:
            media_type = "audio"
        elif update.message.document:
            media_type = "document"
        elif update.message.video_note:
            media_type = "video_note"
        
        chat_logs[chat_id].append({
            'from_user': user_id,
            'to_user': partner_id,
            'media_type': media_type,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Ошибка отправки медиа: {e}")
        await update.message.reply_text(
            '❌ <b>Ошибка отправки медиа</b>\n\n'
            '🔍 Попробуйте снова или найдите нового собеседника',
            parse_mode=ParseMode.HTML
        )

# Проверка подписки через кнопку
async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    if await check_channel_subscription(user_id, context):
        await query.edit_message_text(
            '✅ <b>Подписка подтверждена!</b>\n\n'
            '🎭 Теперь вы можете пользоваться ботом\n'
            '👤 Выберите ваш пол для продолжения:',
            reply_markup=get_gender_keyboard(),
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text(
            '❌ <b>Подписка не найдена</b>\n\n'
            '📢 Пожалуйста, подпишитесь на все каналы и попробуйте снова\n'
            '⚠️ Убедитесь что вы не заблокированы в каналах',
            parse_mode=ParseMode.HTML
        )
        
        # Показываем список каналов снова
        keyboard = []
        for channel in required_channels:
            channel_url = f"https://t.me/{channel['id'].lstrip('@')}"
            keyboard.append([InlineKeyboardButton(f"📢 {channel['name']}", url=channel_url)])
        
        keyboard.append([InlineKeyboardButton("✅ Проверить подписку", callback_data='check_subscription')])
        
        await context.bot.send_message(
            chat_id=user_id,
            text="📢 <b>Подпишитесь на каналы:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

# Дополнительные команды
async def get_my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name
    
    text = (
        f'👤 <b>Информация о вас:</b>\n\n'
        f'🆔 ID: <code>{user_id}</code>\n'
        f'👤 Имя: {first_name}\n'
        f'📱 Username: @{username if username else "не указан"}\n\n'
        f'📋 <i>Скопируйте ваш ID для настройки админа</i>'
    )
    
    # Если это админ, добавляем админскую панель
    if user_id == ADMIN_ID:
        text += '\n\n👑 <b>Вы администратор!</b>\nИспользуйте /admin для панели управления'
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def who_am_i(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        await update.message.reply_text(
            '👑 <b>Вы администратор!</b>\n\n'
            '🎛️ Используйте /admin для панели управления\n'
            '📊 Полный доступ ко всем функциям бота',
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            f'👤 <b>Обычный пользователь</b>\n\n'
            f'🆔 Ваш ID: <code>{user_id}</code>\n'
            f'❌ У вас нет прав администратора',
            parse_mode=ParseMode.HTML
        )

# Главный обработчик кнопок
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Проверяем админские callback'и
    if user_id == ADMIN_ID and query.data.startswith('admin_'):
        await handle_admin_callbacks(update, context)
        return
    
    if query.data.startswith('remove_channel_'):
        await handle_admin_callbacks(update, context)
        return
    
    # Проверяем доступ для обычных пользователей
    if not check_user_access(user_id) and not query.data.startswith('gender_'):
        await query.edit_message_text(
            '🚫 <b>Доступ запрещен</b>\n\n'
            '❌ Вы заблокированы администрацией',
            parse_mode=ParseMode.HTML
        )
        return
    
    if query.data.startswith('gender_'):
        await handle_gender_selection(update, context)
    elif query.data == 'check_subscription':
        await check_subscription_callback(update, context)

# Обработчик ошибок
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    # Уведомляем админа об ошибке
    if ADMIN_ID and isinstance(update, Update):
        try:
            error_text = str(context.error)
            user_info = "Unknown"
            
            if update.effective_user:
                user_info = f"ID: {update.effective_user.id}"
                if update.effective_user.username:
                    user_info += f" (@{update.effective_user.username})"
            
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f'🚨 <b>ОШИБКА В БОТЕ</b>\n\n'
                     f'👤 Пользователь: {user_info}\n'
                     f'⚠️ Ошибка: {error_text[:300]}...\n'
                     f'🕐 Время: {datetime.now().strftime("%d.%m.%Y %H:%M")}',
                parse_mode=ParseMode.HTML
            )
        except Exception as admin_error:
            logger.error(f"Не удалось уведомить админа об ошибке: {admin_error}")

# Основная функция
def main() -> None:
    if not BOT_TOKEN:
        print("❌ Ошибка: BOT_TOKEN не найден в .env файле!")
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("id", get_my_id))
    application.add_handler(CommandHandler("whoami", who_am_i))
    
    # Обработчики
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_commands))
    
    # Медиа обработчики
    application.add_handler(MessageHandler(filters.PHOTO, handle_media))
    application.add_handler(MessageHandler(filters.VIDEO, handle_media))
    application.add_handler(MessageHandler(filters.VOICE, handle_media))
    application.add_handler(MessageHandler(filters.Sticker.ALL, handle_media))
    application.add_handler(MessageHandler(filters.AUDIO, handle_media))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_media))
    application.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_media))
    
    print("🤖 Улучшенный бот запущен...")
    print(f"👑 Админ ID: {ADMIN_ID}")
    print(f"📢 Обязательных каналов: {len(required_channels)}")
    print("\n🎛️ Админ команды:")
    print("   /admin - панель управления")
    print("   /id - узнать свой ID")
    print("\n📋 Для настройки администратора:")
    print("1. Напишите боту команду /id")
    print("2. Скопируйте ваш ID") 
    print("3. Замените ADMIN_ID в коде")
    print("4. Используйте /admin для управления")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
