import logging
import re
import time
import asyncio
from typing import Dict, List, Optional, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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
DATABASE_PATH = os.getenv('DATABASE_PATH', os.path.join(os.getcwd(), 'bot_data', 'bot.db'))

# Создаем директорию если её нет
os.makedirs('bot_data', exist_ok=True)

# Глобальные переменные
waiting_users: List[Dict] = []
active_chats: Dict[int, int] = {}
total_connections: int = 0
reports: Dict[int, int] = {}
chat_logs: Dict[str, List[Dict]] = {}
admin_states: Dict[int, str] = {}
required_channels: List[Dict[str, str]] = []

# Инициализация базы данных
def init_db():
    try:
        # Создаем директорию для базы данных, если её нет
        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
        
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Создаем таблицу пользователей с новыми полями
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            gender TEXT,
            registered BOOLEAN DEFAULT FALSE,
            first_name TEXT,
            username TEXT,
            join_date TEXT,
            banned BOOLEAN DEFAULT FALSE,
            is_premium BOOLEAN DEFAULT FALSE,
            referral_code TEXT,
            referred_by INTEGER,
            referral_count INTEGER DEFAULT 0,
            FOREIGN KEY (referred_by) REFERENCES users(id)
        )
        ''')
        
        # Создаем таблицу каналов
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL
        )
        ''')
        
        # Создаем таблицу настроек
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        ''')
        
        # Создаем таблицу статистики
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            key TEXT PRIMARY KEY,
            value INTEGER DEFAULT 0
        )
        ''')
        
        # Устанавливаем значение по умолчанию для необходимых рефералов
        cursor.execute('''
        INSERT OR IGNORE INTO settings (key, value) 
        VALUES ('referral_required', '2')
        ''')
        
        # Инициализируем статистику соединений
        cursor.execute('''
        INSERT OR IGNORE INTO stats (key, value) 
        VALUES ('total_connections', 0)
        ''')
        
        # Проверяем существующие колонки и добавляем недостающие БЕЗ UNIQUE ограничения
        cursor.execute('PRAGMA table_info(users)')
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'banned' not in columns:
            cursor.execute('ALTER TABLE users ADD COLUMN banned BOOLEAN DEFAULT FALSE')
        if 'is_premium' not in columns:
            cursor.execute('ALTER TABLE users ADD COLUMN is_premium BOOLEAN DEFAULT FALSE')
        if 'referral_code' not in columns:
            cursor.execute('ALTER TABLE users ADD COLUMN referral_code TEXT')
        if 'referred_by' not in columns:
            cursor.execute('ALTER TABLE users ADD COLUMN referred_by INTEGER')
        if 'referral_count' not in columns:
            cursor.execute('ALTER TABLE users ADD COLUMN referral_count INTEGER DEFAULT 0')
        
        # Генерируем реферальные коды для существующих пользователей, у которых их нет
        cursor.execute('''
        UPDATE users 
        SET referral_code = 'USER' || id 
        WHERE referral_code IS NULL AND id IS NOT NULL
        ''')
        
        conn.commit()
        conn.close()
        print("✅ База данных успешно инициализирована")
        
    except Exception as e:
        print(f"❌ Ошибка при инициализации базы данных: {e}")
        # Создаем минимальную рабочую базу данных
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            
            # Создаем только основные таблицы без проблемных колонок
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
                channel_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL
            )
            ''')
            
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            )
            ''')
            
            cursor.execute('''
            INSERT OR IGNORE INTO stats (key, value) 
            VALUES ('total_connections', 0)
            ''')
            
            conn.commit()
            conn.close()
            print("✅ Создана упрощенная база данных")
            
        except Exception as e2:
            print(f"❌ Критическая ошибка создания БД: {e2}")

# Загрузка данных из БД
def load_data():
    global total_connections, required_channels
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Загружаем каналы
        try:
            cursor.execute('SELECT channel_id, name FROM channels')
            required_channels = [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]
        except Exception as e:
            print(f"⚠️ Ошибка загрузки каналов: {e}")
            required_channels = []
        
        # Загружаем статистику
        try:
            cursor.execute('SELECT value FROM stats WHERE key = ?', ('total_connections',))
            row = cursor.fetchone()
            total_connections = int(row[0]) if row else 0
        except Exception as e:
            print(f"⚠️ Ошибка загрузки статистики: {e}")
            total_connections = 0
        
        conn.close()
        logger.info(f"Данные загружены: каналов {len(required_channels)}, соединений {total_connections}")
        
    except Exception as e:
        print(f"❌ Ошибка загрузки данных: {e}")
        required_channels = []
        total_connections = 0

# Сохранение данных в БД
def save_data():
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM stats WHERE key = ?', ('total_connections',))
        cursor.execute('INSERT INTO stats (key, value) VALUES (?, ?)', ('total_connections', total_connections))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка сохранения данных: {e}")

# Функции для работы с БД
def get_user(user_id: int) -> Optional[Dict]:
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            # Безопасно получаем данные, учитывая возможное отсутствие колонок
            user_data = {
                'id': row[0],
                'gender': row[1] if len(row) > 1 else None,
                'registered': bool(row[2]) if len(row) > 2 else False,
                'first_name': row[3] if len(row) > 3 else '',
                'username': row[4] if len(row) > 4 else '',
                'join_date': row[5] if len(row) > 5 else '',
                'banned': bool(row[6]) if len(row) > 6 else False,
                'is_premium': bool(row[7]) if len(row) > 7 else False,
                'referral_code': row[8] if len(row) > 8 else None,
                'referred_by': row[9] if len(row) > 9 else None,
                'referral_count': row[10] if len(row) > 10 else 0
            }
            return user_data
        return None
    except Exception as e:
        logger.error(f"Ошибка получения пользователя {user_id}: {e}")
        return None

def add_user(user_id: int, first_name: str, username: Optional[str], referred_by: Optional[int] = None):
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Проверяем существование колонок
        cursor.execute('PRAGMA table_info(users)')
        columns = [col[1] for col in cursor.fetchall()]
        
        # Generate a unique referral code for the new user
        referral_code = generate_referral_code(user_id, username or str(user_id))
        
        # Вставляем пользователя с учетом доступных колонок
        if 'referral_code' in columns and 'referred_by' in columns:
            cursor.execute('''
                INSERT OR IGNORE INTO users 
                (id, first_name, username, join_date, referral_code, referred_by)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, first_name, username, datetime.now().isoformat(), 
                  referral_code, referred_by))
        else:
            # Базовая вставка без реферальных полей
            cursor.execute('''
                INSERT OR IGNORE INTO users 
                (id, first_name, username, join_date)
                VALUES (?, ?, ?, ?)
            ''', (user_id, first_name, username, datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
        
        # Process referral if applicable
        if referred_by is not None and 'referral_code' in columns:
            process_referral(referred_by)
            
        return referral_code
        
    except Exception as e:
        logger.error(f"Ошибка добавления пользователя {user_id}: {e}")
        return f'USER{user_id}'

def generate_referral_code(user_id: int, username: str) -> str:
    """Generate a unique referral code for a user"""
    import hashlib
    import base64
    
    # Create a unique string from user_id and username
    unique_str = f"{user_id}:{username}:{datetime.now().timestamp()}"
    # Generate hash
    hash_obj = hashlib.md5(unique_str.encode()).digest()
    # Convert to base64 and remove special characters
    referral_code = base64.b64encode(hash_obj).decode('utf-8')
    referral_code = ''.join(c for c in referral_code if c.isalnum())
    # Take first 8 characters
    return referral_code[:8].upper()

def get_referral_required() -> int:
    """Get the number of referrals required for premium"""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'referral_required'")
        result = cursor.fetchone()
        conn.close()
        return int(result[0]) if result and result[0] else 2
    except Exception as e:
        logger.error(f"Ошибка получения требуемого количества рефералов: {e}")
        return 2

def set_referral_required(count: int):
    """Set the number of referrals required for premium"""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ('referral_required', str(count))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка установки требуемого количества рефералов: {e}")

def process_referral(referrer_id: int) -> bool:
    """Process a referral when a new user signs up"""
    if not referrer_id:
        return False
        
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Проверяем существование колонок
        cursor.execute('PRAGMA table_info(users)')
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'referral_count' not in columns or 'is_premium' not in columns:
            conn.close()
            return False
        
        # Get current referral count
        cursor.execute(
            "SELECT referral_count, is_premium FROM users WHERE id = ?",
            (referrer_id,)
        )
        result = cursor.fetchone()
        
        if not result:
            conn.close()
            return False
            
        current_count, is_premium = result
        current_count = current_count or 0
        required_referrals = get_referral_required()
        
        # Update referral count
        new_count = current_count + 1
        cursor.execute(
            "UPDATE users SET referral_count = ? WHERE id = ?",
            (new_count, referrer_id)
        )
        
        # Check if referrer should get premium
        if not is_premium and new_count >= required_referrals:
            cursor.execute(
                "UPDATE users SET is_premium = TRUE WHERE id = ?",
                (referrer_id,)
            )
        
        conn.commit()
        conn.close()
        return True
        
    except Exception as e:
        logger.error(f"Error processing referral: {e}")
        return False

def get_user_referral_info(user_id: int, bot_username: str = None) -> dict:
    """Get referral information for a user"""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Проверяем существование колонок
        cursor.execute('PRAGMA table_info(users)')
        columns = [col[1] for col in cursor.fetchall()]
        
        # Если нет колонок для реферальной системы, возвращаем базовую информацию
        if 'referral_code' not in columns:
            conn.close()
            referral_link = f'https://t.me/{bot_username}?start=ref_USER{user_id}' if bot_username else f'https://t.me/bot?start=ref_USER{user_id}'
            return {
                'code': f'USER{user_id}',
                'link': referral_link,
                'count': 0,
                'required': 2,
                'is_premium': False,
                'needed': 2
            }
        
        # Get user's referral code and count
        cursor.execute('''
            SELECT referral_code, referral_count, is_premium
            FROM users 
            WHERE id = ?
        ''', (user_id,))
        
        user_data = cursor.fetchone()
        if not user_data:
            conn.close()
            return None
        
        # Generate referral link with correct bot username
        referral_code = user_data['referral_code'] or f'USER{user_id}'
        if bot_username:
            referral_link = f"https://t.me/{bot_username}?start=ref_{referral_code}"
        else:
            referral_link = f"https://t.me/bot?start=ref_{referral_code}"
        
        # Get referral requirement
        required = get_referral_required()
        
        conn.close()
        
        return {
            'code': referral_code,
            'link': referral_link,
            'count': user_data['referral_count'] or 0,
            'required': required,
            'is_premium': bool(user_data['is_premium']) if user_data['is_premium'] is not None else False,
            'needed': max(0, required - (user_data['referral_count'] or 0))
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения реферальной информации для {user_id}: {e}")
        referral_link = f'https://t.me/{bot_username}?start=ref_USER{user_id}' if bot_username else f'https://t.me/bot?start=ref_USER{user_id}'
        return {
            'code': f'USER{user_id}',
            'link': referral_link,
            'count': 0,
            'required': 2,
            'is_premium': False,
            'needed': 2
        }

def update_user(user_id: int, **kwargs):
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Проверяем существование колонок
        cursor.execute('PRAGMA table_info(users)')
        columns = [col[1] for col in cursor.fetchall()]
        
        for key, value in kwargs.items():
            if key in columns:
                cursor.execute(f'UPDATE users SET {key} = ? WHERE id = ?', (value, user_id))
            else:
                logger.warning(f"Колонка {key} не существует в таблице users")
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка обновления пользователя {user_id}: {e}")

def get_all_users() -> Dict[int, Dict]:
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Проверяем существование колонок
        cursor.execute('PRAGMA table_info(users)')
        columns = [col[1] for col in cursor.fetchall()]
        
        # Формируем запрос на основе доступных колонок
        base_columns = ['id', 'first_name', 'username', 'gender', 'registered', 'join_date']
        optional_columns = ['banned', 'is_premium', 'referral_count']
        
        select_columns = []
        for col in base_columns:
            if col in columns:
                select_columns.append(col)
        
        for col in optional_columns:
            if col in columns:
                select_columns.append(col)
        
        query = f"SELECT {', '.join(select_columns)} FROM users ORDER BY id DESC"
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        
        result = {}
        for row in rows:
            user_data = {
                'id': row['id'] if 'id' in row.keys() else 0,
                'gender': row['gender'] if 'gender' in row.keys() else None,
                'registered': bool(row['registered']) if 'registered' in row.keys() else False,
                'first_name': row['first_name'] if 'first_name' in row.keys() else '',
                'username': row['username'] if 'username' in row.keys() else '',
                'join_date': row['join_date'] if 'join_date' in row.keys() else '',
                'banned': bool(row['banned']) if 'banned' in row.keys() else False,
                'is_premium': bool(row['is_premium']) if 'is_premium' in row.keys() else False,
                'referral_count': row['referral_count'] if 'referral_count' in row.keys() else 0
            }
            result[row['id']] = user_data
        
        return result
        
    except Exception as e:
        logger.error(f"Ошибка получения всех пользователей: {e}")
        return {}

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
def get_main_keyboard(user_id=None):
    keyboard = [
        [KeyboardButton("🔍 Найти собеседника")]
    ]
    
    # Add premium search button if user has premium
    if user_id:
        ref_info = get_user_referral_info(user_id)
        if ref_info and ref_info.get('is_premium'):
            keyboard[0].append(KeyboardButton("🔍 Премиум поиск"))
    
    # Add other buttons
    keyboard.extend([
        [KeyboardButton("💎 Получить премиум")],
        [KeyboardButton("👥 Сменить пол")],
        [KeyboardButton("ℹ️ Помощь")]
    ])
    
    # Админ может смотреть статистику только через /admin
        
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_chat_keyboard():
    keyboard = [
        [KeyboardButton("⏭️ Следующий"), KeyboardButton("❌ Завершить чат")],
        [KeyboardButton("🚨 Пожаловаться")]
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
        [InlineKeyboardButton("👥 Пользователи", callback_data='admin_users')],
        [InlineKeyboardButton("📢 Каналы", callback_data='admin_channels')],
        [InlineKeyboardButton("💎 Настройки рефералов", callback_data='admin_referrals')]
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
    args = context.args
    referred_by = None
    
    # Check for referral code in the start command
    if args and args[0].startswith('ref_'):
        try:
            ref_code = args[0][4:]  # Remove 'ref_' prefix
            # Find user with this referral code
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE referral_code = ?", (ref_code,))
            result = cursor.fetchone()
            if result and result[0] != user_id:  # Can't refer yourself
                referred_by = result[0]
        except Exception as e:
            logger.error(f"Error processing referral: {e}")
    
    existing_user = get_user(user_id)
    if not existing_user:
        # Add new user with referral info
        referral_code = add_user(user_id, user.first_name, user.username, referred_by)
        
        welcome_msg = (
            f'👋 <b>Добро пожаловать, {user.first_name}!</b>\n\n'
            '🎭 <i>Бот для анонимного общения</i>\n\n'
            '👤 Сначала выберите ваш пол:'
        )
        
        if referred_by:
            welcome_msg += '\n\n🎉 Вы зарегистрировались по реферальной ссылке!'
        
        await update.message.reply_text(
            welcome_msg,
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
    
    gender_emoji = {'male': '👨', 'female': '👩', 'none': '🤖'}.get(existing_user.get('gender', 'none'), '🤖')
    ref_info = get_user_referral_info(user_id, context.bot.username if hasattr(context.bot, 'username') else None)
    
    welcome_msg = (
        f'🤖 <b>С возвращением, {user.first_name}!</b> {gender_emoji}\n\n'
        '✨ Готовы к новому общению?\n'
        '🌍 Находите интересных собеседников!\n\n'
        '💎 <b>Реферальная программа</b>\n'
    )
    
    if ref_info:
        welcome_msg += (
            f"👥 Приглашено друзей: {ref_info['count']}/{ref_info['required']}\n"
        )
        
        if ref_info['is_premium']:
            welcome_msg += "🎉 У вас активирован премиум-доступ!\n"
        else:
            welcome_msg += f"Пригласите ещё {ref_info['needed']} друзей для премиум-доступа\n"
    
    welcome_msg += "\n<b>Используйте кнопки ниже:</b>"
    
    await update.message.reply_text(
        welcome_msg,
        reply_markup=get_main_keyboard(user_id),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
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
    elif data == 'admin_referrals':
        await show_referral_settings(update, context)
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
        '❌ Для отмены напишите /cancel',
        parse_mode=ParseMode.HTML
    )

async def show_referral_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    current_required = get_referral_required()
    
    keyboard = [
        [InlineKeyboardButton("1 реферал", callback_data='set_referrals_1')],
        [InlineKeyboardButton("2 реферала", callback_data='set_referrals_2')],
        [InlineKeyboardButton("3 реферала", callback_data='set_referrals_3')],
        [InlineKeyboardButton("5 рефералов", callback_data='set_referrals_5')],
        [InlineKeyboardButton("🔙 Назад", callback_data='admin_back')]
    ]
    
    await query.edit_message_text(
        f"💎 <b>Настройки реферальной системы</b>\n\n"
        f"📊 Текущее требование: <b>{current_required} рефералов</b>\n\n"
        "🔧 Выберите новое количество рефералов для получения премиум-доступа:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

async def set_referral_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # Проверяем права администратора
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    
    count = int(query.data.split('_')[2])
    
    set_referral_required(count)
    
    await query.edit_message_text(
        f"✅ <b>Настройки обновлены</b>\n\n"
        f"💎 Для получения премиум-доступа теперь требуется: <b>{count} рефералов</b>\n\n"
        "🔄 Изменения применятся для всех пользователей",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_referrals')]]),
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
async def show_referral_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = context.bot.username if hasattr(context.bot, 'username') else None
    ref_info = get_user_referral_info(user_id, bot_username)
    
    if not ref_info:
        await update.message.reply_text("❌ Ошибка при получении информации о реферальной программе.")
        return
    
    message = (
        "💎 <b>Получить премиум</b>\n\n"
        f"🔗 Ваша реферальная ссылка:\n<code>{ref_info['link']}</code>\n\n"
        f"👥 Приглашено друзей: <b>{ref_info['count']}/{ref_info['required']}</b>\n"
    )
    
    if ref_info['is_premium']:
        message += "\n🎉 <b>У вас активирован премиум-доступ!</b>"
    else:
        message += f"\nПригласите ещё {ref_info['needed']} друзей, чтобы получить премиум-доступ!"
    
    message += "\n\n<b>Преимущества премиум-доступа:</b>"
    message += "\n✅ Поиск собеседников по полу"
    message += "\n✅ Приоритет в поиске"
    message += "\n✅ Отсутствие рекламы"
    
    await update.message.reply_text(
        message,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

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
        await show_search_options(update, context)
    elif text == "🔍 Премиум поиск":
        ref_info = get_user_referral_info(user_id, context.bot.username if hasattr(context.bot, 'username') else None)
        if ref_info and ref_info.get('is_premium'):
            await show_premium_search_options(update, context)
        else:
            await update.message.reply_text(
                '❌ У вас нет премиум-доступа.\n\n'
                '💎 Пригласите друзей, чтобы получить премиум-доступ!',
                reply_markup=get_main_keyboard(user_id),
                parse_mode=ParseMode.HTML
            )
    elif text == "💎 Получить премиум":
        await show_referral_info(update, context)
    elif text == "❌ Завершить чат":
        await stop_chat(update, context)
    elif text == "⏭️ Следующий":
        await next_partner(update, context)
    elif text == "🚨 Пожаловаться":
        await report_user(update, context)
    elif text == "👥 Сменить пол":
        await change_gender(update, context)
    elif text == "📈 Статистика":
        # Статистика доступна только через /admin
        await update.message.reply_text(
            '📈 Для просмотра статистики используйте команду /admin',
            parse_mode=ParseMode.HTML
        )
    elif text == "👨‍💻 Админ-панель":
        # Админ-панель доступна только через команду /admin
        if user_id == ADMIN_ID:
            await admin_panel(update, context)
        else:
            await update.message.reply_text(
                '❌ У вас нет прав администратора',
                parse_mode=ParseMode.HTML
            )
    elif text == "ℹ️ Помощь":
        await show_help(update, context)
    else:
        await handle_chat_message(update, context)

# Показ опций поиска для обычных пользователей
async def show_search_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Проверяем, находится ли пользователь в активном диалоге
    if user_id in active_chats:
        # Пользователь в диалоге - показываем опции смены собеседника
        ref_info = get_user_referral_info(user_id, context.bot.username if hasattr(context.bot, 'username') else None)
        
        if ref_info and ref_info.get('is_premium'):
            keyboard = [
                [InlineKeyboardButton("👨 Поиск мужчин", callback_data='search_male')],
                [InlineKeyboardButton("👩 Поиск девушек", callback_data='search_female')],
                [InlineKeyboardButton("🎲 Случайный собеседник", callback_data='search_random')]
            ]
            text = (
                '🔄 <b>Смена собеседника</b>\n\n'
                '💬 Вы сейчас в диалоге\n'
                '🔍 Выберите тип поиска нового собеседника:\n\n'
                '💎 <i>Премиум-доступ: можно выбирать пол!</i>'
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🎲 Случайный собеседник", callback_data='search_random')]
            ]
            needed = ref_info.get('needed', 2) if ref_info else 2
            text = (
                '🔄 <b>Смена собеседника</b>\n\n'
                '💬 Вы сейчас в диалоге\n'
                '🎲 Нажмите кнопку, чтобы найти нового собеседника\n\n'
                f'💎 <b>Хотите выбирать пол собеседника?</b>\n'
                f'Пригласите ещё {needed} друзей для получения премиум-доступа!'
            )
        
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        return
    
    # Пользователь не в диалоге - обычный поиск
    ref_info = get_user_referral_info(user_id, context.bot.username if hasattr(context.bot, 'username') else None)
    
    # Проверяем есть ли у пользователя премиум
    if ref_info and ref_info.get('is_premium'):
        # Премиум пользователь - показываем все опции
        keyboard = [
            [InlineKeyboardButton("👨 Поиск мужчин", callback_data='search_male')],
            [InlineKeyboardButton("👩 Поиск девушек", callback_data='search_female')],
            [InlineKeyboardButton("🎲 Случайный собеседник", callback_data='search_random')]
        ]
        text = (
            '🔍 <b>Выберите тип поиска:</b>\n\n'
            '💎 <i>У вас премиум-доступ!</i>\n'
            'Вы можете выбрать пол собеседника или случайный поиск'
        )
    else:
        # Обычный пользователь - только случайный поиск
        keyboard = [
            [InlineKeyboardButton("🎲 Случайный собеседник", callback_data='search_random')]
        ]
        needed = ref_info.get('needed', 2) if ref_info else 2
        text = (
            '🔍 <b>Поиск собеседника</b>\n\n'
            '🎲 Доступен только случайный поиск\n\n'
            f'💎 <b>Хотите выбирать пол собеседника?</b>\n'
            f'Пригласите ещё {needed} друзей для получения премиум-доступа!'
        )
    
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

# Показ опций премиум поиска
async def show_premium_search_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    keyboard = [
        [InlineKeyboardButton("👨 Поиск мужчин", callback_data='search_male')],
        [InlineKeyboardButton("👩 Поиск девушек", callback_data='search_female')],
        [InlineKeyboardButton("🎲 Случайный собеседник", callback_data='search_random')]
    ]
    
    # Проверяем, находится ли пользователь в активном диалоге
    if user_id in active_chats:
        text = (
            '🔄 <b>Премиум смена собеседника</b>\n\n'
            '💬 Вы сейчас в диалоге\n'
            '💎 Выберите тип поиска нового собеседника:\n'
            '• Поиск по полу\n'
            '• Случайный собеседник\n\n'
            '✨ <i>Премиум-доступ: приоритет в поиске!</i>'
        )
    else:
        text = (
            '💎 <b>Премиум поиск</b>\n\n'
            '🔍 Выберите тип поиска:\n'
            '• Поиск по полу\n'
            '• Случайный собеседник\n\n'
            '✨ <i>Премиум пользователи имеют приоритет в поиске</i>'
        )
    
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

# Вспомогательная функция для отправки сообщений (работает с callback и обычными сообщениями)
async def send_message_universal(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None, parse_mode=None):
    """Универсальная функция для отправки сообщений, работает с callback queries и обычными сообщениями"""
    if update.callback_query:
        # Если это callback query, отправляем новое сообщение
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    else:
        # Если это обычное сообщение, отвечаем на него
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )

# Новая упрощенная система поиска собеседника
async def find_partner(update: Update, context: ContextTypes.DEFAULT_TYPE, search_type: str = 'random', premium: bool = False):
    """Основная функция поиска собеседника"""
    global total_connections, waiting_users, active_chats
    user_id = update.effective_user.id
    
    logger.info(f"User {user_id} starting partner search, type: {search_type}, premium: {premium}")
    
    # 1. Проверяем подписку на каналы
    if not await check_channel_subscription(user_id, context):
        await show_subscription_requirement(update, context)
        return
    
    # 2. Если пользователь уже в чате - завершаем его
    if user_id in active_chats:
        await end_current_chat(user_id, context, "search_new")
    
    # 3. Удаляем из очереди, если уже там
    waiting_users = [u for u in waiting_users if u['id'] != user_id]
    
    # 4. Проверяем премиум доступ
    user_data = get_user(user_id)
    if not user_data:
        await send_message_universal(update, context, '❌ Ошибка: данные пользователя не найдены')
        return
    
    # Проверяем премиум для поиска по полу
    if search_type in ['male', 'female']:
        ref_info = get_user_referral_info(user_id, context.bot.username if hasattr(context.bot, 'username') else None)
        if not ref_info or not ref_info.get('is_premium'):
            needed = ref_info.get('needed', 2) if ref_info else 2
            await send_message_universal(
                update, context,
                '❌ <b>Нужен премиум для поиска по полу</b>\n\n'
                f'💎 Пригласите ещё {needed} друзей!',
                reply_markup=get_main_keyboard(user_id),
                parse_mode=ParseMode.HTML
            )
            return
    
    # 5. Ищем партнера среди ожидающих
    partner = await find_compatible_partner(user_id, search_type, user_data)
    
    if partner:
        # Нашли партнера!
        await create_chat_connection(user_id, partner['id'], context)
    else:
        # Не нашли - добавляем в очередь
        await add_to_waiting_queue(user_id, search_type, user_data, update, context)

# Вспомогательные функции для поиска собеседника

async def end_current_chat(user_id: int, context: ContextTypes.DEFAULT_TYPE, reason: str = "user_action"):
    """Завершает текущий чат пользователя"""
    global active_chats
    
    if user_id not in active_chats:
        return
    
    partner_id = active_chats[user_id]
    
    # Удаляем из активных чатов
    if user_id in active_chats:
        del active_chats[user_id]
    if partner_id in active_chats:
        del active_chats[partner_id]
    
    # Уведомляем партнера
    try:
        if reason == "search_new":
            message = ('ℹ️ <b>Собеседник начал поиск нового собеседника</b>\n\n'
                      '🔍 Вы можете тоже найти нового')
        else:
            message = ('❌ <b>Собеседник покинул диалог</b>\n\n'
                      '🔍 Вы можете найти нового собеседника')
        
        await context.bot.send_message(
            chat_id=partner_id,
            text=message,
            reply_markup=get_main_keyboard(partner_id),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error notifying partner {partner_id}: {e}")

async def find_compatible_partner(user_id: int, search_type: str, user_data: dict):
    """Ищет совместимого партнера в очереди"""
    global waiting_users
    
    user_gender = user_data.get('gender', 'none')
    
    for i, partner in enumerate(waiting_users):
        # Проверяем доступ
        if not check_user_access(partner['id']):
            continue
            
        # Получаем данные партнера
        partner_data = get_user(partner['id'])
        if not partner_data:
            continue
            
        partner_gender = partner_data.get('gender', 'none')
        partner_search = partner.get('search_type', 'random')
        
        # Проверяем совместимость
        compatible = False
        
        if search_type == 'random' and partner_search == 'random':
            compatible = True
        elif search_type == 'male' and partner_gender == 'male':
            compatible = True
        elif search_type == 'female' and partner_gender == 'female':
            compatible = True
        elif partner_search == 'male' and user_gender == 'male':
            compatible = True
        elif partner_search == 'female' and user_gender == 'female':
            compatible = True
        elif search_type == 'random' or partner_search == 'random':
            compatible = True
            
        if compatible:
            # Нашли партнера! Удаляем из очереди
            del waiting_users[i]
            return partner
    
    return None

async def create_chat_connection(user1_id: int, user2_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Создает соединение между двумя пользователями"""
    global active_chats, total_connections, chat_logs
    
    # Добавляем в активные чаты
    active_chats[user1_id] = user2_id
    active_chats[user2_id] = user1_id
    total_connections += 1
    save_data()
    
    # Создаем лог чата
    chat_id = f"{min(user1_id, user2_id)}_{max(user1_id, user2_id)}_{total_connections}"
    chat_logs[chat_id] = []
    
    # Получаем данные пользователей
    user1_data = get_user(user1_id)
    user2_data = get_user(user2_id)
    
    if not user1_data or not user2_data:
        logger.error(f"Failed to get user data for connection: {user1_id}, {user2_id}")
        return
    
    user1_gender = user1_data.get('gender', 'none')
    user2_gender = user2_data.get('gender', 'none')
    
    gender_emoji = {'male': '👨', 'female': '👩', 'none': '🤖'}
    gender_text = {'male': 'Мужской', 'female': 'Женский', 'none': 'Не указан'}
    
    # Отправляем сообщения обоим пользователям
    message_template = (
        '✅ <b>Собеседник найден! {emoji}</b>\n\n'
        '🎭 Теперь вы можете общаться анонимно\n'
        '📱 Отправляйте текст, фото, видео, стикеры\n'
        '⏭️ Используйте "Следующий" для смены собеседника\n\n'
        '💬 <i>Пол собеседника: {gender}</i>\n'
        '⚠️ <i>Будьте вежливы и уважайте собеседника</i>'
    )
    
    try:
        # Сообщение первому пользователю
        await context.bot.send_message(
            chat_id=user1_id,
            text=message_template.format(
                emoji=gender_emoji.get(user2_gender, '🤖'),
                gender=gender_text.get(user2_gender, 'Неизвестен')
            ),
            reply_markup=get_chat_keyboard(),
            parse_mode=ParseMode.HTML
        )
        
        # Сообщение второму пользователю
        await context.bot.send_message(
            chat_id=user2_id,
            text=message_template.format(
                emoji=gender_emoji.get(user1_gender, '🤖'),
                gender=gender_text.get(user1_gender, 'Неизвестен')
            ),
            reply_markup=get_chat_keyboard(),
            parse_mode=ParseMode.HTML
        )
        
        logger.info(f"Created chat connection: {user1_id} <-> {user2_id}")
        
    except Exception as e:
        logger.error(f"Error creating chat connection: {e}")
        # В случае ошибки удаляем соединение
        if user1_id in active_chats:
            del active_chats[user1_id]
        if user2_id in active_chats:
            del active_chats[user2_id]

async def add_to_waiting_queue(user_id: int, search_type: str, user_data: dict, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавляет пользователя в очередь ожидания"""
    global waiting_users
    
    # Добавляем в очередь
    waiting_users.append({
        'id': user_id,
        'gender': user_data.get('gender', 'none'),
        'search_type': search_type,
        'timestamp': time.time()
    })
    
    # Формируем сообщение
    search_messages = {
        'male': '👨 <b>Поиск мужчин</b>',
        'female': '👩 <b>Поиск девушек</b>',
        'random': '🎲 <b>Случайный поиск</b>'
    }
    
    message = (
        '🔍 <b>Ищем собеседника...</b>\n\n'
        f'{search_messages.get(search_type, search_messages["random"])}\n\n'
        '⏳ Пожалуйста, подождите\n'
        '👥 Как только кто-то подключится, мы вас уведомим!\n\n'
        '✨ Поиск будет продолжаться до тех пор, пока не найдется собеседник\n'
    )
    
    await send_message_universal(update, context, message, parse_mode=ParseMode.HTML)
    logger.info(f"User {user_id} added to waiting queue, search_type: {search_type}")

# Переписанная функция next_partner
async def next_partner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поиск следующего собеседника"""
    user_id = update.effective_user.id
    
    if user_id not in active_chats:
        await update.message.reply_text(
            '❌ <b>Вы не в диалоге</b>\n\n'
            '🔍 Сначала найдите собеседника',
            parse_mode=ParseMode.HTML
        )
        return
    
    # Завершаем текущий чат
    await end_current_chat(user_id, context, "next_search")
    
    # Начинаем новый поиск
    await update.message.reply_text(
        '🔄 <b>Ищем следующего собеседника...</b>',
        parse_mode=ParseMode.HTML
    )
    
    # Запускаем обычный поиск
    await find_partner(update, context, 'random', False)

# Обновленная функция stop_chat
async def stop_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение диалога"""
    user_id = update.effective_user.id
    
    # Проверяем, есть ли пользователь в очереди
    global waiting_users
    if any(u['id'] == user_id for u in waiting_users):
        waiting_users = [u for u in waiting_users if u['id'] != user_id]
        await update.message.reply_text(
            '❌ <b>Поиск отменен</b>\n\n'
            '🔍 Вы вышли из очереди поиска',
            reply_markup=get_main_keyboard(user_id),
            parse_mode=ParseMode.HTML
        )
        return
    
    # Проверяем, есть ли пользователь в активном чате
    if user_id not in active_chats:
        await update.message.reply_text(
            '❌ <b>Вы не в диалоге и не ищете собеседника</b>\n\n'
            '🔍 Нажмите кнопку "Найти собеседника"',
            reply_markup=get_main_keyboard(user_id),
            parse_mode=ParseMode.HTML
        )
        return
    
    # Завершаем чат
    await end_current_chat(user_id, context, "user_stop")
    
    await update.message.reply_text(
        '❌ <b>Диалог завершен</b>\n\n'
        '🔍 Вы можете найти нового собеседника',
        reply_markup=get_main_keyboard(user_id),
        parse_mode=ParseMode.HTML
    )

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
        '💎 <b>Получить премиум</b> - пригласите друзей и получите премиум\n\n'
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
        # Отправляем медиа без пересылки, чтобы избежать "Переслано от"
        if update.message.photo:
            await context.bot.send_photo(
                chat_id=partner_id,
                photo=update.message.photo[-1].file_id,
                caption=update.message.caption
            )
        elif update.message.video:
            await context.bot.send_video(
                chat_id=partner_id,
                video=update.message.video.file_id,
                caption=update.message.caption
            )
        elif update.message.voice:
            await context.bot.send_voice(
                chat_id=partner_id,
                voice=update.message.voice.file_id
            )
        elif update.message.sticker:
            await context.bot.send_sticker(
                chat_id=partner_id,
                sticker=update.message.sticker.file_id
            )
        elif update.message.audio:
            await context.bot.send_audio(
                chat_id=partner_id,
                audio=update.message.audio.file_id,
                caption=update.message.caption
            )
        elif update.message.document:
            await context.bot.send_document(
                chat_id=partner_id,
                document=update.message.document.file_id,
                caption=update.message.caption
            )
        elif update.message.video_note:
            await context.bot.send_video_note(
                chat_id=partner_id,
                video_note=update.message.video_note.file_id
            )
        else:
            # Если тип медиа неизвестен, используем forward как fallback
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

# Получение информации о боте при старте
async def get_bot_info(context: ContextTypes.DEFAULT_TYPE):
    try:
        bot_info = await context.bot.get_me()
        logger.info(f"Бот запущен: @{bot_info.username}")
        return bot_info.username
    except Exception as e:
        logger.error(f"Ошибка получения информации о боте: {e}")
        return None

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
    elif query.data.startswith('search_'):
        await handle_search_callback(update, context)
    elif query.data.startswith('set_referrals_'):
        await set_referral_count(update, context)

# Обработка поиска через callback
async def handle_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    # Проверяем подписку на каналы
    if not await check_channel_subscription(user_id, context):
        await query.edit_message_text(
            '📢 <b>Сначала подпишитесь на каналы</b>',
            parse_mode=ParseMode.HTML
        )
        await show_subscription_requirement(update, context)
        return
    
    search_type = query.data.split('_')[1]  # male, female, random
    
    # Проверяем премиум статус для поиска по полу
    if search_type in ['male', 'female']:
        ref_info = get_user_referral_info(user_id, context.bot.username if hasattr(context.bot, 'username') else None)
        if not ref_info or not ref_info.get('is_premium'):
            await query.edit_message_text(
                '❌ <b>Нужен премиум для поиска по полу</b>\n\n'
                f'💎 Пригласите ещё {ref_info.get("needed", 2)} друзей для получения премиум-доступа!',
                parse_mode=ParseMode.HTML
            )
            return
    
    await query.edit_message_text(
        '🔍 <b>Начинаем поиск...</b>',
        parse_mode=ParseMode.HTML
    )
    
    # Определяем премиум статус
    ref_info = get_user_referral_info(user_id, context.bot.username if hasattr(context.bot, 'username') else None)
    is_premium = ref_info and ref_info.get('is_premium', False)
    
    # Запускаем поиск
    await find_partner(update, context, search_type=search_type, premium=is_premium)

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
    
    # Fix for Python 3.14 - use custom event loop policy
    import asyncio
    import sys
    if sys.version_info >= (3, 14):
        # Use Windows event loop policy on all platforms for Python 3.14
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except AttributeError:
            # Fallback for non-Windows systems
            asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    
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
    
    # Получаем информацию о боте
    async def post_init(app):
        await get_bot_info(app)
    
    application.post_init = post_init
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
