import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import asyncio
import json
from datetime import datetime, timedelta
import pytz
import aiohttp
import signal
import sys
import nest_asyncio
import atexit
from shop_functions import shop_command as shop_cmd, show_shop_category as show_shop, process_purchase as process_purchase_shop, has_active_item as has_active_item_shop, use_item as use_item_shop, save_shop_items, load_shop_items
import re
import time

# Применяем патч для вложенных циклов событий
nest_asyncio.apply()

# Проверка на уже запущенный экземпляр
LOCK_FILE = "bot.lock"

def check_running():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, 'r') as f:
                pid = int(f.read().strip())
            # Проверяем, существует ли процесс с таким PID
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            # Если процесс не существует или файл поврежден
            os.remove(LOCK_FILE)
    return False

def create_lock():
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))

def remove_lock():
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)

# Регистрируем функцию удаления файла блокировки при выходе
atexit.register(remove_lock)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальные переменные
application = None
shutdown_event = None
previous_scores = {}  # Для хранения предыдущих счетов матчей
user_predictions = {}  # Для хранения предсказаний пользователей
user_currency = {}  # Для хранения валюты пользователей
user_names = {}  # Для хранения имен пользователей
user_items = {}  # Для хранения купленных предметов
matches_cache = {
    'data': [],
    'last_update': None,
    'cache_duration': 30  # Кэш на 30 секунд
}

# Добавляем новые глобальные переменные
user_statuses = {}  # Для хранения пользовательских статусов
user_nicknames = {}  # Для хранения пользовательских никнеймов
user_roles = {}  # Для хранения ролей пользователей

# Роли пользователей и их префиксы
USER_ROLES = {
    'developer': {
        'name': '👨‍💻 Developer',
        'prefix': '[DEV] ',
        'color': '🟣',
        'description': 'Полный доступ ко всей админ-панели',
        'purchasable': False
    },
    'admin': {
        'name': '🔐 Admin',
        'prefix': '[ADMIN] ',
        'color': '🔴',
        'description': 'Доступ к админ-панели с ограничениями',
        'price': 5000,
        'duration': 30,  # Дней
        'purchasable': True
    },
    'moderator': {
        'name': '🛡️ Moderator',
        'prefix': '[MOD] ',
        'color': '🟠',
        'description': 'Доступ к отправке сообщений всем пользователям',
        'price': 3000,
        'duration': 30,  # Дней
        'purchasable': True
    },
    'operator': {
        'name': '🔧 Operator',
        'prefix': '[OP] ',
        'color': '🟡',
        'description': 'Доступ к отправке сообщений и статистике бота',
        'price': 2000,
        'duration': 30,  # Дней
        'purchasable': True
    },
    'user': {
        'name': '👤 User',
        'prefix': '',
        'color': '⚪',
        'description': 'Обычный пользователь',
        'purchasable': False
    }
}

# Товары в магазине
SHOP_ITEMS = {
    'double_reward': {
        'name': '🎯 Двойная награда',
        'description': 'Следующее правильное предсказание принесет двойную награду',
        'price': 500,
        'duration': 1,  # Количество использований
        'category': 'boosters'
    },
    'insurance': {
        'name': '🛡️ Страховка',
        'description': 'Возврат ставки при неправильном предсказании',
        'price': 300,
        'duration': 1,
        'category': 'boosters'
    },
    'vip_predict': {
        'name': '⭐️ VIP-прогноз',
        'description': 'Возможность сделать прогноз на уже начавшийся матч',
        'price': 1000,
        'duration': 1,
        'category': 'boosters'
    },
    'custom_nickname': {
        'name': '📝 Смена никнейма',
        'description': 'Установить свой никнейм в боте',
        'price': 200,
        'duration': 1,
        'category': 'game'
    },
    'custom_status': {
        'name': '💫 Кастомный статус',
        'description': 'Установить свой статус в профиле',
        'price': 300,
        'duration': 30,  # Дней
        'category': 'game'
    },
    'vip_status': {
        'name': '👑 VIP-статус',
        'description': 'Особая отметка в топе и расширенная статистика',
        'price': 2000,
        'duration': 7,  # Дней
        'category': 'game'
    },
    'extended_stats': {
        'name': '📊 Расширенная статистика',
        'description': 'Доступ к подробной статистике матчей',
        'price': 500,
        'duration': 30,  # Дней
        'category': 'football'
    },
    'priority_notifications': {
        'name': '🔔 Приоритетные уведомления',
        'description': 'Получать уведомления первым',
        'price': 400,
        'duration': 30,  # Дней
        'category': 'football'
    },
    'tournament_tables': {
        'name': '🏆 Турнирные таблицы',
        'description': 'Доступ к эксклюзивным турнирным таблицам',
        'price': 600,
        'duration': 30,  # Дней
        'category': 'football'
    },
    'role_admin': {
        'name': USER_ROLES['admin']['name'],
        'description': USER_ROLES['admin']['description'],
        'price': USER_ROLES['admin']['price'],
        'duration': USER_ROLES['admin']['duration'],
        'category': 'roles',
        'role': 'admin'
    },
    'role_moderator': {
        'name': USER_ROLES['moderator']['name'],
        'description': USER_ROLES['moderator']['description'],
        'price': USER_ROLES['moderator']['price'],
        'duration': USER_ROLES['moderator']['duration'],
        'category': 'roles',
        'role': 'moderator'
    },
    'role_operator': {
        'name': USER_ROLES['operator']['name'],
        'description': USER_ROLES['operator']['description'],
        'price': USER_ROLES['operator']['price'],
        'duration': USER_ROLES['operator']['duration'],
        'category': 'roles',
        'role': 'operator'
    }
}

# ID администратора (замените на свой ID)
ADMIN_ID = "791190609"  # Замените на ваш ID в Telegram

# Константы для системы предсказаний
PREDICTION_COST = 10    # Стоимость одного предсказания
PREDICTION_REWARD_EXACT = 50  # Награда за точный счет (x5)
PREDICTION_REWARD_DIFF = 30   # Награда за правильную разницу голов (x3)
PREDICTION_REWARD_OUTCOME = 20  # Награда за правильный исход (x2)

# Коэффициенты для топ-матчей
TOP_TEAMS = ["Real Madrid", "Barcelona", "Manchester City", "Liverpool", "Bayern Munich", "PSG"]
TOP_MATCH_MULTIPLIER = 1.5  # Коэффициент для матчей между топ-командами

# Команды бота
COMMANDS = {
    'start': 'Запустить бота',
    'matches': 'Показать сегодняшние матчи',
    'settings': 'Настройки уведомлений',
    'help': 'Показать помощь',
    'predict': 'Сделать предсказание счета матча',
    'balance': 'Проверить баланс',
    'top': 'Показать топ предсказателей',
    'shop': 'Открыть магазин',
    'admin': 'Панель администратора (только для админа)'
}

# Все доступные команды для подписки
AVAILABLE_TEAMS = {
    "Real Madrid": "Реал Мадрид",
    "Barcelona": "Барселона",
    "Manchester City": "Манчестер Сити",
    "Manchester United": "Манчестер Юнайтед",
    "Liverpool": "Ливерпуль",
    "Chelsea": "Челси",
    "Arsenal": "Арсенал",
    "Bayern Munich": "Бавария",
    "Borussia Dortmund": "Боруссия Д",
    "PSG": "ПСЖ"
}

# Избранные команды
FAVORITE_TEAMS = [
    "Real Madrid",
    "Barcelona",
    "Manchester City"
]

# Файлы для хранения данных
USER_DATA_FILE = "user_data.json"
PREDICTIONS_FILE = "predictions.json"

def load_user_data():
    """Загрузка данных пользователей из файла"""
    try:
        with open(USER_DATA_FILE, 'r') as f:
            data = json.load(f)
            return (
                data.get('user_currency', {}),
                data.get('user_predictions', {}),
                data.get('user_names', {}),
                data.get('user_items', {}),
                data.get('user_statuses', {}),
                data.get('user_nicknames', {}),
                data.get('user_roles', {})
            )
    except FileNotFoundError:
        return {}, {}, {}, {}, {}, {}, {}

def save_user_data(currency_data, predictions_data, names_data, items_data, statuses_data, nicknames_data, roles_data):
    """Сохранение данных пользователей в файл"""
    data = {
        'user_currency': currency_data,
        'user_predictions': predictions_data,
        'user_names': names_data,
        'user_items': items_data,
        'user_statuses': statuses_data,
        'user_nicknames': nicknames_data,
        'user_roles': roles_data
    }
    with open(USER_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# Загружаем данные при запуске
user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles = load_user_data()

# Функция для автоматического сохранения данных
def save_data_periodically():
    """Периодическое сохранение данных"""
    save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)

# Загрузка конфигурации
def load_config():
    try:
        with open('config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "authorized_users": [],
            "bot_token": "7736382046:AAFmMBfomQ9Xh15gglYuv6eA4Xd1oY2JGuU",
            "football_api_token": "f4d562844acb4bddb32de86d798d35b5",  # Токен для football-data.org
            "user_teams": {},
            "last_update": None,
            "cache_duration": 300,  # Кэширование на 5 минут
            "user_settings": {}
        }

# Сохранение конфигурации
def save_config(config):
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)

# Проверка авторизации пользователя
def is_authorized(user_id, config):
    return str(user_id) in config["authorized_users"]

def get_user_display_name(user_id, user=None):
    """Получить отображаемое имя пользователя с префиксом роли"""
    user_id = str(user_id)
    
    # Получаем базовое имя
    base_name = ""
    if user_id in user_nicknames:
        base_name = user_nicknames[user_id]
    elif user_id in user_names:
        base_name = user_names[user_id]
    elif user:
        if user.username:
            base_name = f"@{user.username}"
        elif user.first_name and user.last_name:
            base_name = f"{user.first_name} {user.last_name}"
        elif user.first_name:
            base_name = user.first_name
        else:
            base_name = f"User{user_id}"
        user_names[user_id] = base_name
        save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
    else:
        base_name = f"User{user_id}"
    
    # Добавляем префикс роли, если есть
    if user_id in user_roles and user_roles[user_id] in USER_ROLES:
        role = user_roles[user_id]
        prefix = USER_ROLES[role]['prefix']
        return f"{prefix}{base_name}"
    
    return base_name

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or update.effective_user.first_name
    
    # Проверяем, есть ли пользователь в базе
    if user_id not in user_currency:
        user_currency[user_id] = 1000  # Начальный баланс
        save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
        logger.info(f"Новый пользователь: {user_id} ({username})")
    
    # Создаем клавиатуру
    keyboard = [
        [InlineKeyboardButton("⚽️ Матчи", callback_data='today_matches'),
         InlineKeyboardButton("🎯 Прогнозы", callback_data='show_predictions')],
        [InlineKeyboardButton("💰 Баланс", callback_data='show_balance'),
         InlineKeyboardButton("🏆 Топ игроков", callback_data='show_top')],
        [InlineKeyboardButton("🏪 Магазин", callback_data='show_shop'),
         InlineKeyboardButton("ℹ️ Помощь", callback_data='show_help')]
    ]
    
    # Добавляем кнопку админ-панели для пользователей с соответствующими ролями
    has_admin_access = False
    
    # Developer имеет полный доступ
    if user_id == ADMIN_ID:
        has_admin_access = True
        user_roles[user_id] = 'developer'  # Устанавливаем роль developer для админа
    # Проверяем роль пользователя
    elif user_id in user_roles:
        role = user_roles[user_id]
        if role in ['developer', 'admin', 'moderator', 'operator']:
            has_admin_access = True
    
    if has_admin_access:
        keyboard.append([InlineKeyboardButton("🔐 Админ-панель", callback_data='admin_panel')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Формируем приветственное сообщение
    welcome_message = (
        f"👋 Привет, {get_user_display_name(user_id)}!\n\n"
        "🤖 Я бот для прогнозов на футбольные матчи.\n\n"
        "🏆 Новая система наград:\n"
        f"• Точный счёт: {PREDICTION_REWARD_EXACT} монет\n"
        f"• Правильная разница голов: {PREDICTION_REWARD_DIFF} монет\n"
        f"• Правильный исход: {PREDICTION_REWARD_OUTCOME} монет\n\n"
        "🌟 Топовые матчи дают повышенные награды!\n\n"
        "💰 Используйте монеты для покупки предметов в магазине.\n"
        "📊 Соревнуйтесь с другими игроками в таблице лидеров."
    )
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

def normalize_team_name(name):
    """Нормализация названий команд"""
    name_mapping = {
        "Real Madrid CF": "Real Madrid",
        "FC Barcelona": "Barcelona",
        "Manchester City FC": "Manchester City",
        "Manchester United FC": "Manchester United",
        "Liverpool FC": "Liverpool",
        "Chelsea FC": "Chelsea",
        "Arsenal FC": "Arsenal",
        "FC Bayern München": "Bayern Munich",
        "Borussia Dortmund": "Borussia Dortmund",
        "Paris Saint-Germain FC": "PSG",
        "Real Madrid": "Real Madrid",
        "Barcelona": "Barcelona",
        "Manchester City": "Manchester City",
        "Manchester United": "Manchester United",
        "Liverpool": "Liverpool",
        "Chelsea": "Chelsea",
        "Arsenal": "Arsenal",
        "Bayern Munich": "Bayern Munich",
        "PSG": "PSG"
    }
    return name_mapping.get(name, name)

def get_match_status_emoji(status):
    """Получение эмодзи для статуса матча"""
    status_mapping = {
        'SCHEDULED': '🕒',  # Запланирован
        'LIVE': '🔴',      # Идет сейчас
        'IN_PLAY': '🔴',   # Идет сейчас
        'PAUSED': '⏸️',    # Перерыв
        'FINISHED': '✅',   # Завершен
        'POSTPONED': '⏳',  # Отложен
        'CANCELLED': '❌',  # Отменен
        'SUSPENDED': '⚠️',  # Приостановлен
    }
    return status_mapping.get(status, '❓')

# Получение списка матчей
async def fetch_matches():
    """Получение списка матчей с кэшированием"""
    try:
        current_time = datetime.now()
        
        # Проверяем кэш
        if (matches_cache['last_update'] and 
            (current_time - matches_cache['last_update']).total_seconds() < matches_cache['cache_duration'] and 
            matches_cache['data']):
            logger.info("Используем кэшированные данные матчей")
            return matches_cache['data']
        
        async with aiohttp.ClientSession() as session:
            headers = {
                'X-Auth-Token': load_config().get('football_api_token', '')
            }
            
            # Получаем матчи на неделю вперед
            london_tz = pytz.timezone('Europe/London')
            today = datetime.now(london_tz).strftime("%Y-%m-%d")
            next_week = (datetime.now(london_tz) + timedelta(days=7)).strftime("%Y-%m-%d")
            url = f"http://api.football-data.org/v4/matches?dateFrom={today}&dateTo={next_week}"
            
            logger.info(f"Запрос матчей с {today} по {next_week}")
            
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    matches = data.get('matches', [])
                    logger.info(f"Получено {len(matches)} матчей из API")
                    
                    formatted_matches = []
                    uz_timezone = pytz.timezone('Asia/Tashkent')
                    
                    for match in matches:
                        try:
                            home_team = normalize_team_name(match['homeTeam'].get('name', ''))
                            away_team = normalize_team_name(match['awayTeam'].get('name', ''))
                            
                            # Фильтруем только матчи избранных команд
                            if home_team in FAVORITE_TEAMS or away_team in FAVORITE_TEAMS:
                                utc_time = datetime.strptime(match['utcDate'], "%Y-%m-%dT%H:%M:%SZ")
                                utc_time = utc_time.replace(tzinfo=pytz.UTC)
                                uz_time = utc_time.astimezone(uz_timezone)
                                
                                score = match.get('score', {})
                                current_score = "- : -"
                                
                                if match['status'] == 'FINISHED':
                                    if score.get('fullTime', {}).get('home') is not None:
                                        home_score = score['fullTime']['home']
                                        away_score = score['fullTime']['away']
                                        current_score = f"{home_score} : {away_score}"
                                
                                elif match['status'] in ['LIVE', 'IN_PLAY', 'PAUSED']:
                                    if score.get('fullTime', {}).get('home') is not None:
                                        home_score = score['fullTime']['home']
                                        away_score = score['fullTime']['away']
                                        current_score = f"{home_score} : {away_score}"
                                    elif score.get('halfTime', {}).get('home') is not None:
                                        home_score = score['halfTime']['home']
                                        away_score = score['halfTime']['away']
                                        current_score = f"{home_score} : {away_score}"
                                
                                formatted_match = {
                                    "home": home_team,
                                    "away": away_team,
                                    "time": uz_time.strftime("%H:%M"),
                                    "date": uz_time.strftime("%d.%m.%Y"),
                                    "status": match['status'],
                                    "score": current_score,
                                    "competition": match['competition']['name']
                                }
                                
                                formatted_matches.append(formatted_match)
                        
                        except Exception as e:
                            logger.error(f"Ошибка форматирования матча: {str(e)}")
                            continue
                    
                    # Сортируем матчи
                    formatted_matches.sort(key=lambda x: (
                        0 if x['status'] in ['LIVE', 'IN_PLAY', 'PAUSED'] else
                        1 if x['status'] == 'FINISHED' else 2,
                        datetime.strptime(f"{x['date']} {x['time']}", "%d.%m.%Y %H:%M")
                    ))
                    
                    # Обновляем кэш
                    matches_cache['data'] = formatted_matches
                    matches_cache['last_update'] = current_time
                    
                    return formatted_matches
                else:
                    logger.error(f"Ошибка API: {response.status}")
                    return matches_cache['data'] if matches_cache['data'] else []
            
    except Exception as e:
        logger.error(f"Ошибка при получении матчей: {str(e)}")
        return matches_cache['data'] if matches_cache['data'] else []

def get_team_id(team_name):
    """Получение ID команды для API football-data.org"""
    team_ids = {
        "Real Madrid": 86,
        "Barcelona": 81,
        "Manchester City": 65,
        "Manchester United": 66,
        "Liverpool": 64,
        "Chelsea": 61,
        "Arsenal": 57,
        "Bayern Munich": 5,
        "Borussia Dortmund": 4,
        "PSG": 524
    }
    return team_ids.get(team_name)

# Обработка нажатий на кнопки
async def split_long_message(text, max_length=4000):
    """Разделить длинное сообщение на части"""
    parts = []
    current_part = ""
    
    # Разделяем по блокам матчей (два переноса строки)
    blocks = text.split('\n\n')
    
    for block in blocks:
        # Если текущая часть + новый блок не превышает лимит
        if len(current_part + block + '\n\n') <= max_length:
            current_part += block + '\n\n'
        else:
            # Если текущая часть не пустая, добавляем её в список частей
            if current_part:
                parts.append(current_part.strip())
            current_part = block + '\n\n'
    
    # Добавляем последнюю часть
    if current_part:
        parts.append(current_part.strip())
    
    return parts

async def send_long_message(message, text, reply_markup=None):
    """Отправить длинное сообщение частями"""
    parts = await split_long_message(text)
    
    try:
        # Проверяем, является ли чат группой
        chat_type = message.chat.type if hasattr(message, 'chat') else message.message.chat.type
        is_group = chat_type in ['group', 'supergroup']
        
        # Если это группа, проверяем права бота
        if is_group:
            try:
                bot_member = await message.get_bot().get_chat_member(
                    chat_id=message.chat.id if hasattr(message, 'chat') else message.message.chat.id,
                    user_id=message.get_bot().id
                )
                can_send = bot_member.can_send_messages
                can_edit = bot_member.can_edit_messages
                
                if not can_send:
                    logger.error("У бота нет прав на отправку сообщений в группе")
                    return
            except Exception as e:
                logger.error(f"Ошибка при проверке прав бота: {str(e)}")
                return
        
        # Если это callback query (кнопки в сообщении)
        if hasattr(message, 'edit_message_text'):
            try:
                # Отправляем все части, кроме последней
                for part in parts[:-1]:
                    await message.get_bot().send_message(
                        chat_id=message.message.chat.id,
                        text=part
                    )
                # Последнюю часть отправляем с кнопками
                await message.edit_message_text(
                    text=parts[-1],
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Ошибка при обновлении сообщения: {str(e)}")
                # Пробуем отправить новое сообщение
                await message.get_bot().send_message(
                    chat_id=message.message.chat.id,
                    text=parts[-1],
                    reply_markup=reply_markup
                )
        else:
            # Обычное сообщение
            try:
                # Отправляем все части, кроме последней
                for part in parts[:-1]:
                    await message.get_bot().send_message(
                        chat_id=message.chat.id,
                        text=part
                    )
                # Последнюю часть отправляем с кнопками
                await message.get_bot().send_message(
                    chat_id=message.chat.id,
                    text=parts[-1],
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке сообщения: {str(e)}")
    except Exception as e:
        logger.error(f"Критическая ошибка при отправке сообщения: {str(e)}")
        # Пробуем отправить сообщение об ошибке
        try:
            chat_id = message.chat.id if hasattr(message, 'chat') else message.message.chat.id
            await message.get_bot().send_message(
                chat_id=chat_id,
                text="❌ Произошла ошибка при отправке сообщения. Пожалуйста, проверьте права бота в группе."
            )
        except:
            pass

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    # Обработка кнопок предсказаний
    if query.data.startswith('predict_'):
        await process_prediction(update, context)
        return
    
    # Обработка кнопок статистики
    elif query.data.startswith('stats_'):
        await show_match_stats(update, context)
        return
    
    # Обработка кнопок управления ролями
    elif query.data.startswith('role_'):
        role_name = query.data.split('_')[1]
        
        if 'awaiting_role_name' in context.user_data and 'target_user_id' in context.user_data:
            target_user_id = context.user_data['target_user_id']
            user_id = str(query.from_user.id)
            
            # Проверяем, имеет ли пользователь право назначать эту роль
            if role_name == 'developer' and user_id != ADMIN_ID:
                await query.answer("❌ Только главный администратор может назначать роль Developer!")
                return
            
            # Проверяем, не пытается ли админ изменить роль developer
            if target_user_id in user_roles and user_roles[target_user_id] == 'developer' and user_id != ADMIN_ID:
                await query.answer("❌ Вы не можете изменить роль Developer!")
                return
            
            # Назначаем роль пользователю
            user_roles[target_user_id] = role_name
            
            # Добавляем срок действия роли (30 дней) для всех ролей кроме developer и user
            if role_name not in ['developer', 'user']:
                # Добавляем информацию о сроке действия роли (30 дней)
                if 'role_expiry' not in user_items.get(target_user_id, {}):
                    if target_user_id not in user_items:
                        user_items[target_user_id] = {}
                    user_items[target_user_id]['role_expiry'] = {}
                
                user_items[target_user_id]['role_expiry'][role_name] = int(time.time()) + (30 * 24 * 60 * 60)  # 30 дней
            
            # Если роль покупается в магазине, очищаем флаг
            if 'shop_role_purchase' in context.user_data:
                context.user_data.pop('shop_role_purchase', None)
            
            save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
            
            # Получаем имя пользователя из доступных словарей
            user_name = user_names.get(target_user_id) or user_nicknames.get(target_user_id) or f"User{target_user_id}"
            
            # Отправляем уведомление пользователю о назначении роли
            try:
                role_info = USER_ROLES.get(role_name, {'name': role_name})
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"🎖️ Вам назначена роль: {role_info['name']}!\n"
                         f"Теперь у вас есть доступ к дополнительным функциям бота."
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления пользователю: {str(e)}")
            
            await query.edit_message_text(
                f"✅ Роль {role_name} успешно назначена пользователю {user_name}!"
            )
            
            # Возвращаемся в меню управления ролями
            keyboard = [
                [InlineKeyboardButton("👤 Назначить роль", callback_data="admin_assign_role")],
                [InlineKeyboardButton("🗑️ Удалить роль", callback_data="admin_remove_role")],
                [InlineKeyboardButton("📋 Список пользователей с ролями", callback_data="admin_list_roles")],
                [InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]
            ]
            
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text="Управление ролями пользователей:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            # Очищаем состояние
            context.user_data.pop('awaiting_role_name', None)
            context.user_data.pop('target_user_id', None)
        return
    
    # Обработка кнопок админ-панели
    elif query.data == 'admin_panel':
        await admin_panel(update, context)
    
    # Обработка кнопки управления ролями
    elif query.data == 'admin_manage_roles':
        await admin_manage_roles(query)
    
    # Обработка кнопки назначения роли
    elif query.data == 'admin_assign_role':
        await admin_assign_role(query, context)
    
    # Обработка кнопки удаления роли
    elif query.data == 'admin_remove_role':
        await admin_remove_role(query, context)
    
    # Обработка кнопки списка пользователей с ролями
    elif query.data == 'admin_list_roles':
        await admin_list_roles(query)
    
    # Остальные обработчики...
    elif query.data == 'back_to_main':
        user_id = str(query.from_user.id)
        
        # Создаем клавиатуру
        keyboard = [
            [InlineKeyboardButton("⚽️ Матчи", callback_data='today_matches'),
             InlineKeyboardButton("🎯 Прогнозы", callback_data='show_predictions')],
            [InlineKeyboardButton("💰 Баланс", callback_data='show_balance'),
             InlineKeyboardButton("🏆 Топ игроков", callback_data='show_top')],
            [InlineKeyboardButton("🏪 Магазин", callback_data='show_shop'),
             InlineKeyboardButton("ℹ️ Помощь", callback_data='show_help')]
        ]
        
        # Добавляем кнопку админ-панели для администратора
        if user_id == ADMIN_ID:
            keyboard.append([InlineKeyboardButton("🔐 Админ-панель", callback_data='admin_panel')])
        # Добавляем кнопку админ-панели для пользователей с ролями
        elif user_id in user_roles and user_roles[user_id] in ['admin', 'moderator', 'operator']:
            # Проверяем, не истек ли срок действия роли
            role_expired = False
            if user_id in user_items and 'role_expiry' in user_items[user_id]:
                role = user_roles[user_id]
                if role in user_items[user_id]['role_expiry']:
                    expiry_time = user_items[user_id]['role_expiry'][role]
                    if int(time.time()) > expiry_time:
                        # Роль истекла, удаляем её
                        user_roles.pop(user_id, None)
                        user_items[user_id]['role_expiry'].pop(role, None)
                        role_expired = True
                        save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
            
            if not role_expired:
                keyboard.append([InlineKeyboardButton("🔐 Админ-панель", callback_data='admin_panel')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Формируем приветственное сообщение
        welcome_message = (
            f"👋 Привет, {get_user_display_name(user_id)}!\n\n"
            "🤖 Я бот для прогнозов на футбольные матчи.\n\n"
            "🏆 Новая система наград:\n"
            f"• Точный счёт: {PREDICTION_REWARD_EXACT} монет\n"
            f"• Правильная разница голов: {PREDICTION_REWARD_DIFF} монет\n"
            f"• Правильный исход: {PREDICTION_REWARD_OUTCOME} монет\n\n"
            "🌟 Топовые матчи дают повышенные награды!\n\n"
            "💰 Используйте монеты для покупки предметов в магазине.\n"
            "📊 Соревнуйтесь с другими игроками в таблице лидеров."
        )
        
        await query.edit_message_text(welcome_message, reply_markup=reply_markup)
    
    elif query.data == 'show_predictions':
        user_id = str(query.from_user.id)
        
        # Проверяем баланс пользователя
        balance = await get_user_balance(user_id)
        if balance < PREDICTION_COST:
            await query.edit_message_text(
                f"❌ У вас недостаточно монет для прогноза!\n"
                f"Стоимость прогноза: {PREDICTION_COST} монет\n"
                f"Ваш баланс: {balance} монет",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]])
            )
            return
        
        # Получаем текущие матчи
        matches = await fetch_matches()
        
        # Фильтруем матчи, на которые можно сделать прогноз
        has_vip = has_active_item(user_id, 'vip_predict')
        
        if has_vip:
            # Для VIP пользователей - все текущие матчи
            available_matches = [m for m in matches if m['status'] in ['SCHEDULED', 'LIVE', 'IN_PLAY', 'PAUSED']]
        else:
            # Для обычных пользователей - только запланированные матчи
            available_matches = [m for m in matches if m['status'] == 'SCHEDULED']
        
        if not available_matches:
            await query.edit_message_text(
                "❌ Сейчас нет доступных матчей для прогноза!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]])
            )
            return
        
        # Создаем клавиатуру с доступными матчами
        keyboard = []
        for match in available_matches:
            button_text = f"{match['home']} vs {match['away']} ({match['date']} {match['time']})"
            callback_data = f"predict_{match['home']}_{match['away']}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        # Добавляем кнопку возврата
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Формируем сообщение с информацией о системе наград
        message = (
            "⚽️ Выберите матч для прогноза:\n"
            f"💰 Стоимость прогноза: {PREDICTION_COST} монет\n\n"
            "🏆 Система наград:\n"
            f"• Точный счёт: {PREDICTION_REWARD_EXACT} монет\n"
            f"• Правильная разница голов: {PREDICTION_REWARD_DIFF} монет\n"
            f"• Правильный исход: {PREDICTION_REWARD_OUTCOME} монет\n\n"
            "🌟 Топовые матчи дают повышенные награды!"
        )
        
        if has_vip:
            message += "\n✨ У вас есть VIP-прогноз! Вы можете прогнозировать текущие матчи."
        
        await query.edit_message_text(message, reply_markup=reply_markup)
    
    elif query.data == 'show_shop':
        # Показываем категории магазина
        keyboard = []
        categories = {
            'boosters': '🎯 Бустеры',
            'game': '🎮 Игровые возможности',
            'football': '⚽️ Футбольные привилегии',
            'roles': '🔰 Роли и префиксы'
        }
        
        for category, title in categories.items():
            keyboard.append([InlineKeyboardButton(title, callback_data=f'shop_category_{category}')])
        
        keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data='back_to_main')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🏪 Добро пожаловать в магазин!\n"
            "Выберите категорию товаров:",
            reply_markup=reply_markup
        )
    
    elif query.data.startswith('stats_'):
        # Обработка запроса расширенной статистики
        user_id = str(query.from_user.id)
        if not has_active_item(user_id, 'extended_stats'):
            await query.answer("❌ У вас нет доступа к расширенной статистике!")
            return
        
        _, home_team, away_team = query.data.split('_')
        matches = await fetch_matches()
        current_match = None
        
        for match in matches:
            if match['home'] == home_team and match['away'] == away_team:
                current_match = match
                break
        
        if not current_match:
            await query.answer("❌ Матч не найден!")
            return
        
        # Формируем расширенную статистику (демо-данные)
        stats = {
            'possession': {'home': 55, 'away': 45},
            'shots': {'home': 12, 'away': 8},
            'shots_on_target': {'home': 5, 'away': 3},
            'corners': {'home': 6, 'away': 4},
            'fouls': {'home': 10, 'away': 12},
            'yellow_cards': {'home': 2, 'away': 3},
            'red_cards': {'home': 0, 'away': 0}
        }
        
        text = f"📊 Расширенная статистика матча\n\n"
        text += f"⚽️ {home_team} {current_match['score']} {away_team}\n"
        text += f"🏆 {current_match['competition']}\n\n"
        
        text += f"⏱️ Владение мячом: {stats['possession']['home']}% - {stats['possession']['away']}%\n"
        text += f"🎯 Удары: {stats['shots']['home']} - {stats['shots']['away']}\n"
        text += f"🎯 В створ: {stats['shots_on_target']['home']} - {stats['shots_on_target']['away']}\n"
        text += f"⛳️ Угловые: {stats['corners']['home']} - {stats['corners']['away']}\n"
        text += f"⚠️ Фолы: {stats['fouls']['home']} - {stats['fouls']['away']}\n"
        text += f"🟨 Желтые карточки: {stats['yellow_cards']['home']} - {stats['yellow_cards']['away']}\n"
        text += f"🟥 Красные карточки: {stats['red_cards']['home']} - {stats['red_cards']['away']}\n"
        
        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data=query.data)],
            [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    elif query.data == 'show_tables':
        # Обработка запроса турнирных таблиц
        user_id = str(query.from_user.id)
        if not has_active_item(user_id, 'tournament_tables'):
            await query.answer("❌ У вас нет доступа к турнирным таблицам!")
            return
        
        await show_tournament_tables(query.message, context)
    
    elif query.data == 'show_balance':
        user_id = str(query.from_user.id)
        balance = await get_user_balance(user_id)
        
        # Получаем активные предметы пользователя
        active_items = []
        if user_id in user_items:
            for item_id, value in user_items[user_id].items():
                if has_active_item(user_id, item_id):
                    item = SHOP_ITEMS[item_id]
                    if isinstance(value, int):
                        active_items.append(f"{item['name']} (x{value})")
                    else:
                        try:
                            expiration = datetime.fromisoformat(value)
                            days_left = (expiration - datetime.now(pytz.UTC)).days
                            active_items.append(f"{item['name']} ({days_left} дн.)")
                        except (ValueError, TypeError):
                            continue
        
        text = f"💰 Ваш текущий баланс: {balance} монет\n\n"
        
        # Добавляем статус пользователя, если есть
        if user_id in user_statuses:
            text += f"💭 Ваш статус: {user_statuses[user_id]}\n\n"
        
        if active_items:
            text += "🎁 Ваши активные предметы:\n"
            for item in active_items:
                text += f"• {item}\n"
        else:
            text += "У вас нет активных предметов"
        
        keyboard = [
            [InlineKeyboardButton("🏪 Магазин", callback_data='shop')],
            [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    elif query.data == 'shop':
        # Показываем категории магазина
        keyboard = []
        categories = {
            'boosters': '🎯 Бустеры',
            'game': '🎮 Игровые возможности',
            'football': '⚽️ Футбольные привилегии',
            'roles': '🔰 Роли и префиксы'
        }
        
        for category, title in categories.items():
            keyboard.append([InlineKeyboardButton(title, callback_data=f'shop_category_{category}')])
        
        keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data='back_to_main')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🏪 Добро пожаловать в магазин!\n"
            "Выберите категорию товаров:",
            reply_markup=reply_markup
        )
    
    elif query.data.startswith('shop_category_'):
        # Показываем товары выбранной категории
        category = query.data.replace('shop_category_', '')
        await show_shop(query, category, SHOP_ITEMS)
    
    elif query.data.startswith('buy_'):
        # Обработка покупки товара
        item_id = query.data.replace('buy_', '')
        await process_purchase_shop(query, item_id, SHOP_ITEMS, user_currency, user_items, user_statuses, user_nicknames, user_roles, update_user_balance, save_user_data)
    
    elif query.data == 'make_prediction':
        # Получаем текущие матчи
        matches = await fetch_matches()
        live_matches = [m for m in matches if m['status'] in ['LIVE', 'IN_PLAY', 'PAUSED']]
        
        if not live_matches:
            await query.answer("❌ Сейчас нет активных матчей для прогноза!")
            return
        
        # Проверяем наличие VIP-прогноза
        user_id = str(query.from_user.id)
        has_vip = has_active_item(user_id, 'vip_predict')
        
        # Создаем клавиатуру с live матчами
        keyboard = []
        for match in live_matches:
            button_text = f"{match['home']} {match['score']} {match['away']}"
            callback_data = f"predict_{match['home']}_{match['away']}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        # Добавляем кнопку возврата
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "⚽️ Выберите матч для прогноза:\n"
            f"💰 Стоимость прогноза: {PREDICTION_COST} монет\n"
            f"🏆 Награда за точное предсказание: {PREDICTION_REWARD_EXACT} монет" +
            ("\n✨ У вас есть VIP-прогноз!" if has_vip else ""),
            reply_markup=reply_markup
        )
    
    elif query.data.startswith('predict_'):
        await process_prediction(update, context)
    
    elif query.data == 'select_all':
        user_id = str(query.from_user.id)
        if 'user_settings' not in config:
            config['user_settings'] = {}
        if user_id not in config['user_settings']:
            config['user_settings'] = {
                'subscribed_teams': [],
                'goal_alerts': True,
                'match_reminders': True
            }
        
        config['user_settings'][user_id]['subscribed_teams'] = list(AVAILABLE_TEAMS.keys())
        save_config(config)
        await query.answer("✅ Выбраны все команды")
        await show_settings(query.message)
    
    elif query.data == 'clear_all':
        user_id = str(query.from_user.id)
        if 'user_settings' in config and user_id in config['user_settings']:
            config['user_settings'][user_id]['subscribed_teams'] = []
            save_config(config)
        await query.answer("❌ Список команд очищен")
        await show_settings(query.message)
        
    elif query.data == 'divider':
        await query.answer()
        
    elif query.data in ['toggle_goals', 'toggle_matches']:
        user_id = str(query.from_user.id)
        
        if 'user_settings' not in config:
            config['user_settings'] = {}
        if user_id not in config['user_settings']:
            config['user_settings'] = {
                'subscribed_teams': [],
                'goal_alerts': True,
                'match_reminders': True
            }
            
        setting_key = 'goal_alerts' if query.data == 'toggle_goals' else 'match_reminders'
        current_value = config['user_settings'][user_id].get(setting_key, True)
        config['user_settings'][user_id][setting_key] = not current_value
        
        setting_name = 'голов' if query.data == 'toggle_goals' else 'матчей'
        status = 'включены' if not current_value else 'отключены'
        await query.answer(f"🔔 Уведомления о {setting_name} {status}")
        
        save_config(config)
        await show_settings(query.message)
    
    elif query.data.startswith('subscribe_'):
        team_id = query.data.replace('subscribe_', '')
        user_id = str(query.from_user.id)
        
        if 'user_settings' not in config:
            config['user_settings'] = {}
        if user_id not in config['user_settings']:
            config['user_settings'] = {
                'subscribed_teams': [],
                'goal_alerts': True,
                'match_reminders': True
            }
            
        if team_id in config['user_settings'][user_id].get('subscribed_teams', []):
            config['user_settings'][user_id]['subscribed_teams'].remove(team_id)
            await query.answer(f"❌ Отписка от {AVAILABLE_TEAMS[team_id]}")
        else:
            if 'subscribed_teams' not in config['user_settings'][user_id]:
                config['user_settings'][user_id]['subscribed_teams'] = []
            config['user_settings'][user_id]['subscribed_teams'].append(team_id)
            await query.answer(f"✅ Подписка на {AVAILABLE_TEAMS[team_id]}")
        
        save_config(config)
        await show_settings(query.message)
    
    elif query.data == 'admin_users_list':
        user_id = str(query.from_user.id)
        # Проверяем, имеет ли пользователь доступ к этой функции
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
            await query.answer("❌ У вас нет доступа к этой функции!")
            return
        
        await admin_users_list(query)
    
    elif query.data == 'admin_stats':
        user_id = str(query.from_user.id)
        # Проверяем, имеет ли пользователь доступ к этой функции
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin', 'operator']):
            await query.answer("❌ У вас нет доступа к этой функции!")
            return
        
        await admin_stats(query)
    
    elif query.data == 'admin_broadcast':
        user_id = str(query.from_user.id)
        # Проверяем, имеет ли пользователь доступ к этой функции
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin', 'moderator', 'operator']):
            await query.answer("❌ У вас нет доступа к этой функции!")
            return
        
        await admin_broadcast(query, context)
    
    elif query.data == 'admin_broadcast_confirm':
        user_id = str(query.from_user.id)
        # Проверяем, имеет ли пользователь доступ к этой функции
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin', 'moderator', 'operator']):
            await query.answer("❌ У вас нет доступа к этой функции!")
            return
        
        await admin_broadcast_send(query, context)
    
    elif query.data == 'admin_manage_items':
        user_id = str(query.from_user.id)
        # Проверяем, имеет ли пользователь доступ к этой функции
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
            await query.answer("❌ У вас нет доступа к этой функции!")
            return
        
        await admin_manage_items(query, context)
    
    elif query.data.startswith('admin_add_item_'):
        user_id = str(query.from_user.id)
        # Проверяем, имеет ли пользователь доступ к этой функции
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
            await query.answer("❌ У вас нет доступа к этой функции!")
            return
        
        item_id = query.data.replace('admin_add_item_', '')
        await admin_add_item(query, context, item_id)
    
    elif query.data == 'admin_manage_prices':
        user_id = str(query.from_user.id)
        # Проверяем, имеет ли пользователь доступ к этой функции
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
            await query.answer("❌ У вас нет доступа к этой функции!")
            return
        
        await admin_manage_prices(query, context)
    
    elif query.data == 'show_top':
        # Сортируем пользователей по балансу
        sorted_users = sorted(user_currency.items(), key=lambda x: x[1], reverse=True)
        
        text = "🏆 Топ предсказателей:\n\n"
        for i, (user_id, balance) in enumerate(sorted_users[:10], 1):
            name = get_user_display_name(user_id)
            
            # Добавляем VIP-статус и пользовательский статус
            vip_status = "👑 " if has_active_item(user_id, 'vip_status') else ""
            custom_status = f"\n💭 {user_statuses[user_id]}" if user_id in user_statuses else ""
            
            text += f"{i}. {vip_status}{name} - {balance} монет{custom_status}\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    elif query.data == 'show_help':
        text = "ℹ️ Помощь по использованию бота:\n\n"
        for command, description in COMMANDS.items():
            text += f"/{command} - {description}\n"
        
        text += "\n🏆 Новая система наград:\n"
        text += f"• Точный счёт: {PREDICTION_REWARD_EXACT} монет\n"
        text += f"• Правильная разница голов: {PREDICTION_REWARD_DIFF} монет\n"
        text += f"• Правильный исход: {PREDICTION_REWARD_OUTCOME} монет\n\n"
        text += "🌟 Топовые матчи дают повышенные награды!\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    elif query.data == 'today_matches':
        matches = await fetch_matches()
        if matches:
            text = "📅 Матчи:\n\n"
            
            # Сначала показываем live матчи
            live_matches = [m for m in matches if m['status'] in ['LIVE', 'IN_PLAY', 'PAUSED']]
            if live_matches:
                text += "🔴 LIVE МАТЧИ:\n\n"
                for match in live_matches:
                    home_star = "⭐️ " if match['home'] in FAVORITE_TEAMS else ""
                    away_star = " ⭐️" if match['away'] in FAVORITE_TEAMS else ""
                    text += f"{get_match_status_emoji(match['status'])} {home_star}{match['home']} {match['score']} {away_star}{match['away']}\n"
                    text += f"🏆 {match['competition']}\n\n"
            
            # Показываем завершенные матчи
            finished_matches = [m for m in matches if m['status'] == 'FINISHED']
            if finished_matches:
                text += "✅ ЗАВЕРШЕННЫЕ МАТЧИ:\n\n"
                for match in finished_matches:
                    home_star = "⭐️ " if match['home'] in FAVORITE_TEAMS else ""
                    away_star = " ⭐️" if match['away'] in FAVORITE_TEAMS else ""
                    text += f"{get_match_status_emoji(match['status'])} {home_star}{match['home']} {match['score']} {away_star}{match['away']}\n"
                    text += f"🏆 {match['competition']}\n\n"
            
            # Затем показываем предстоящие матчи
            scheduled_matches = [m for m in matches if m['status'] not in ['LIVE', 'IN_PLAY', 'PAUSED', 'FINISHED']]
            if scheduled_matches:
                text += "📆 ПРЕДСТОЯЩИЕ МАТЧИ:\n\n"
                
                # Группируем матчи по датам
                matches_by_date = {}
                for match in scheduled_matches:
                    match_date = datetime.strptime(match['date'], "%d.%m.%Y").date()
                    if match_date not in matches_by_date:
                        matches_by_date[match_date] = []
                    matches_by_date[match_date].append(match)
                
                # Сортируем даты
                sorted_dates = sorted(matches_by_date.keys())
                
                # Выводим матчи по датам
                for date in sorted_dates:
                    text += f"\n📆 {date.strftime('%d.%m.%Y')}:\n"
                    for match in matches_by_date[date]:
                        home_star = "⭐️ " if match['home'] in FAVORITE_TEAMS else ""
                        away_star = " ⭐️" if match['away'] in FAVORITE_TEAMS else ""
                        text += f"{get_match_status_emoji(match['status'])} {home_star}{match['home']} vs {away_star}{match['away']}\n"
                        text += f"🕒 {match['time']} (UZB)\n"
                        text += f"🏆 {match['competition']}\n\n"
        else:
            text = "Матчей с участием избранных команд не найдено"
        
        keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data='today_matches')],
                   [InlineKeyboardButton("🔙 Главное меню", callback_data='back_to_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Удаляем старое сообщение
        await query.message.delete()
        # Отправляем новое сообщение частями
        await send_long_message(query.message, text, reply_markup=reply_markup)
    
    elif query.data == 'admin_manage_roles':
        await admin_manage_roles(query)
    
    elif query.data == 'admin_assign_role':
        await admin_assign_role(query, context)
    
    elif query.data == 'admin_remove_role':
        await admin_remove_role(query, context)
    
    elif query.data == 'admin_list_roles':
        await admin_list_roles(query)
    
    elif query.data == 'admin_modify_balance':
        user_id = str(query.from_user.id)
        # Проверяем, имеет ли пользователь доступ к этой функции
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
            await query.answer("❌ У вас нет доступа к этой функции!")
            return
        
        # Сохраняем состояние в контексте
        context.user_data['admin_state'] = 'waiting_user_id'
        
        await query.edit_message_text(
            "💰 Изменение баланса пользователя\n\n"
            "Введите ID пользователя:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Отмена", callback_data='admin_panel')
            ]])
        )
    
    else:
        await query.answer()
        # Обработка остальных callback_data
        if query.data == 'help':
            text = "ℹ️ Помощь по использованию бота:\n\n"
            for command, description in COMMANDS.items():
                text += f"/{command} - {description}\n"
            
            text += "\n🏆 Новая система наград:\n"
            text += f"• Точный счёт: {PREDICTION_REWARD_EXACT} монет\n"
            text += f"• Правильная разница голов: {PREDICTION_REWARD_DIFF} монет\n"
            text += f"• Правильный исход: {PREDICTION_REWARD_OUTCOME} монет\n\n"
            text += "🌟 Топовые матчи дают повышенные награды!\n"
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup)
        
        elif query.data == 'settings':
            await show_settings(query.message)

# Команды для работы с матчами
async def matches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /matches"""
    matches = await fetch_matches()
    if matches:
        text = "📅 Матчи:\n\n"
        
        # Сначала показываем live матчи
        live_matches = [m for m in matches if m['status'] in ['LIVE', 'IN_PLAY', 'PAUSED']]
        if live_matches:
            text += "🔴 LIVE МАТЧИ:\n\n"
            for match in live_matches:
                home_star = "⭐️ " if match['home'] in FAVORITE_TEAMS else ""
                away_star = " ⭐️" if match['away'] in FAVORITE_TEAMS else ""
                text += f"{get_match_status_emoji(match['status'])} {home_star}{match['home']} {match['score']} {away_star}{match['away']}\n"
                text += f"🏆 {match['competition']}\n\n"
        
        # Показываем завершенные матчи
        finished_matches = [m for m in matches if m['status'] == 'FINISHED']
        if finished_matches:
            text += "✅ ЗАВЕРШЕННЫЕ МАТЧИ:\n\n"
            for match in finished_matches:
                home_star = "⭐️ " if match['home'] in FAVORITE_TEAMS else ""
                away_star = " ⭐️" if match['away'] in FAVORITE_TEAMS else ""
                text += f"{get_match_status_emoji(match['status'])} {home_star}{match['home']} {match['score']} {away_star}{match['away']}\n"
                text += f"🏆 {match['competition']}\n\n"
        
        # Затем показываем предстоящие матчи
        scheduled_matches = [m for m in matches if m['status'] not in ['LIVE', 'IN_PLAY', 'PAUSED', 'FINISHED']]
        if scheduled_matches:
            text += "📆 ПРЕДСТОЯЩИЕ МАТЧИ:\n\n"
            
            # Группируем матчи по датам
            matches_by_date = {}
            for match in scheduled_matches:
                match_date = datetime.strptime(match['date'], "%d.%m.%Y").date()
                if match_date not in matches_by_date:
                    matches_by_date[match_date] = []
                matches_by_date[match_date].append(match)
            
            # Сортируем даты
            sorted_dates = sorted(matches_by_date.keys())
            
            # Выводим матчи по датам
            for date in sorted_dates:
                text += f"\n📆 {date.strftime('%d.%m.%Y')}:\n"
                for match in matches_by_date[date]:
                    home_star = "⭐️ " if match['home'] in FAVORITE_TEAMS else ""
                    away_star = " ⭐️" if match['away'] in FAVORITE_TEAMS else ""
                    text += f"{get_match_status_emoji(match['status'])} {home_star}{match['home']} vs {away_star}{match['away']}\n"
                    text += f"🕒 {match['time']} (UZB)\n"
                    text += f"🏆 {match['competition']}\n\n"
    else:
        text = "Матчей с участием избранных команд не найдено"
    
    keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data='today_matches')],
               [InlineKeyboardButton("🔙 Главное меню", callback_data='back_to_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Отправляем сообщение частями
    await send_long_message(update.message, text, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    text = "ℹ️ Помощь по использованию бота:\n\n"
    for command, description in COMMANDS.items():
        text += f"/{command} - {description}\n"
    
    text += "\n🏆 Новая система наград:\n"
    text += f"• Точный счёт: {PREDICTION_REWARD_EXACT} монет\n"
    text += f"• Правильная разница голов: {PREDICTION_REWARD_DIFF} монет\n"
    text += f"• Правильный исход: {PREDICTION_REWARD_OUTCOME} монет\n\n"
    text += "🌟 Топовые матчи дают повышенные награды!\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)

# Вспомогательные функции
async def show_matches(message):
    """Показать матчи"""
    matches = await fetch_matches()
    if matches:
        text = "📅 Матчи:\n\n"
        
        # Сначала показываем live матчи
        live_matches = [m for m in matches if m['status'] in ['LIVE', 'IN_PLAY', 'PAUSED']]
        if live_matches:
            text += "🔴 LIVE МАТЧИ:\n\n"
            for match in live_matches:
                home_star = "⭐️ " if match['home'] in FAVORITE_TEAMS else ""
                away_star = " ⭐️" if match['away'] in FAVORITE_TEAMS else ""
                text += f"{get_match_status_emoji(match['status'])} {home_star}{match['home']} {match['score']} {away_star}{match['away']}\n"
                text += f"🏆 {match['competition']}\n\n"
        
        # Показываем завершенные матчи
        text += "📆 ПРЕДСТОЯЩИЕ МАТЧИ:\n\n"
        today = datetime.now(pytz.timezone('Asia/Tashkent')).date()
        
        matches_by_date = {}
        for match in matches:
            if match['status'] not in ['LIVE', 'IN_PLAY', 'PAUSED']:
                match_date = datetime.strptime(match['date'], "%d.%m.%Y").date()
                if match_date not in matches_by_date:
                    matches_by_date[match_date] = []
                matches_by_date[match_date].append(match)
        
        sorted_dates = sorted(matches_by_date.keys())
        
        for date in sorted_dates:
            if date >= today:
                text += f"\n📆 {date.strftime('%d.%m.%Y')}:\n"
                for match in matches_by_date[date]:
                    home_star = "⭐️ " if match['home'] in FAVORITE_TEAMS else ""
                    away_star = " ⭐️" if match['away'] in FAVORITE_TEAMS else ""
                    text += f"{get_match_status_emoji(match['status'])} {home_star}{match['home']} vs {away_star}{match['away']}\n"
                    if match['status'] == 'FINISHED':
                        text += f"📊 Финальный счет: {match['score']}\n"
                    else:
                        text += f"🕒 {match['time']} (UZB)\n"
                    text += f"🏆 {match['competition']}\n\n"
    else:
        text = "Матчей с участием избранных команд не найдено"
    
    keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data='today_matches')],
                [InlineKeyboardButton("🔙 Главное меню", callback_data='back_to_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await send_long_message(message, text, reply_markup=reply_markup)

async def show_settings(message):
    """Показать настройки пользователя"""
    config = load_config()
    user_id = str(message.from_user.id if isinstance(message, Update) else message.chat.id)
    
    # Получаем текущие подписки пользователя
    user_settings = config.get('user_settings', {}).get(user_id, {
        'subscribed_teams': [],
        'goal_alerts': True,
        'match_reminders': True
    })
    
    text = "⚙️ Настройки уведомлений\n\n"
    text += "🔔 Типы уведомлений:\n"
    text += f"{'✅' if user_settings.get('goal_alerts', True) else '❌'} Уведомления о голах\n"
    text += f"{'✅' if user_settings.get('match_reminders', True) else '❌'} Напоминания о матчах\n\n"
    text += "📋 Выбранные команды:\n"
    
    # Показываем выбранные команды в тексте
    selected_teams = user_settings.get('subscribed_teams', [])
    if selected_teams:
        for team_id in selected_teams:
            text += f"✅ {AVAILABLE_TEAMS[team_id]}\n"
    else:
        text += "❌ Нет выбранных команд\n"
    
    text += "\nВыберите команды для отслеживания:"
    
    # Создаем клавиатуру
    keyboard = []
    
    # Добавляем переключатели для типов уведомлений
    keyboard.append([
        InlineKeyboardButton(
            f"{'🔔 Вкл.' if user_settings.get('goal_alerts', True) else '🔕 Выкл.'} Голы",
            callback_data='toggle_goals'
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            f"{'🔔 Вкл.' if user_settings.get('match_reminders', True) else '🔕 Выкл.'} Матчи",
            callback_data='toggle_matches'
        )
    ])
    
    # Добавляем разделитель
    keyboard.append([InlineKeyboardButton("〰️〰️〰️〰️〰️", callback_data='divider')])
    
    # Группируем команды по 2 в строке
    teams_buttons = []
    current_row = []
    
    for team_id, team_name in AVAILABLE_TEAMS.items():
        status = "✅" if team_id in selected_teams else "➕"
        current_row.append(
            InlineKeyboardButton(
                f"{status} {team_name}",
                callback_data=f'subscribe_{team_id}'
            )
        )
        
        if len(current_row) == 2:
            teams_buttons.append(current_row)
            current_row = []
    
    # Добавляем оставшиеся кнопки
    if current_row:
        teams_buttons.append(current_row)
    
    # Добавляем кнопки команд
    keyboard.extend(teams_buttons)
    
    # Добавляем кнопки управления
    keyboard.append([
        InlineKeyboardButton("✅ Выбрать все", callback_data='select_all'),
        InlineKeyboardButton("❌ Очистить", callback_data='clear_all')
    ])
    
    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data='back_to_main')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        # Всегда пытаемся отредактировать сообщение
        if isinstance(message, Update):
            await message.message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.edit_text(text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка при обновлении настроек: {str(e)}")
        # Только если редактирование не удалось, отправляем новое сообщение
        if isinstance(message, Update):
            await message.message.reply_text(text, reply_markup=reply_markup)
        else:
            await message.reply_text(text, reply_markup=reply_markup)

async def check_and_send_goal_alerts(matches, context):
    """Проверка новых голов и отправка уведомлений"""
    global previous_scores
    config = load_config()
    
    for match in matches:
        if match['status'] in ['LIVE', 'IN_PLAY', 'PAUSED']:
            match_id = f"{match['home']}_{match['away']}_{match['date']}"
            current_score = match['score']
            
            # Инициализируем счет, если это первое обновление
            if match_id not in previous_scores:
                previous_scores[match_id] = current_score
                logger.info(f"Инициализация счета для матча {match_id}: {current_score}")
                continue
            
            old_score = previous_scores[match_id]
            if current_score != old_score:
                try:
                    # Разбираем счета на числа
                    old_home, old_away = map(int, old_score.split(' : '))
                    new_home, new_away = map(int, current_score.split(' : '))
                    
                    # Определяем, кто забил
                    if new_home > old_home:
                        scoring_team = match['home']
                        opponent_team = match['away']
                        new_score = new_home
                        team_score = "домашняя команда"
                    else:
                        scoring_team = match['away']
                        opponent_team = match['home']
                        new_score = new_away
                        team_score = "гостевая команда"
                    
                    # Формируем текст уведомления
                    alert_text = f"⚽️ ГОЛ! Забивает {team_score}!\n\n"
                    alert_text += f"✨ {scoring_team} забивает в ворота {opponent_team}!\n"
                    alert_text += f"📊 Текущий счёт: {match['home']} {current_score} {match['away']}\n"
                    alert_text += f"🏆 {match['competition']}"
                    
                    logger.info(f"Обнаружен новый гол: {scoring_team} забивает, счёт {current_score}")
                    
                    # Отправляем уведомление только подписанным пользователям
                    for user_id, settings in config.get('user_settings', {}).items():
                        subscribed_teams = settings.get('subscribed_teams', [])
                        if settings.get('goal_alerts', True):
                            # Проверяем, подписан ли пользователь на одну из команд
                            if any(team in subscribed_teams for team in [match['home'], match['away']]):
                                try:
                                    await context.bot.send_message(chat_id=user_id, text=alert_text)
                                    logger.info(f"Отправлено уведомление о голе пользователю {user_id}")
                                except Exception as e:
                                    logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {str(e)}")
                
                except ValueError as e:
                    logger.error(f"Ошибка при обработке счета матча: {str(e)}")
                except Exception as e:
                    logger.error(f"Непредвиденная ошибка при обработке гола: {str(e)}")
            
            # Обновляем предыдущий счет
            previous_scores[match_id] = current_score

async def check_and_send_match_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Отправка уведомлений о предстоящих матчах за 5 минут до начала и о начале матча"""
    config = load_config()
    matches = await fetch_matches()
    uz_timezone = pytz.timezone('Asia/Tashkent')
    now = datetime.now(uz_timezone)
    
    for match in matches:
        if match['status'] == 'SCHEDULED':
            # Преобразуем время матча в объект datetime с учетом часового пояса
            match_datetime = datetime.strptime(f"{match['date']} {match['time']}", "%d.%m.%Y %H:%M")
            match_datetime = uz_timezone.localize(match_datetime)
            
            # Вычисляем разницу во времени
            time_until_match = match_datetime - now
            minutes_until_match = time_until_match.total_seconds() / 60
            
            # Проверяем, что матч начинается через 5 минут (с погрешностью в 30 секунд)
            if 4.5 <= minutes_until_match <= 5.5:
                reminder_text = f"⚽️ Матч начнется через 5 минут!\n\n"
                reminder_text += f"{match['home']} vs {match['away']}\n"
                reminder_text += f"🕒 Начало в {match['time']} (UZB)\n"
                reminder_text += f"🏆 {match['competition']}"
                
                # Отправляем уведомление только подписанным пользователям
                for user_id, settings in config.get('user_settings', {}).items():
                    if (settings.get('match_reminders', True) and 
                        (match['home'] in settings.get('subscribed_teams', []) or 
                         match['away'] in settings.get('subscribed_teams', []))):
                        try:
                            await context.bot.send_message(chat_id=user_id, text=reminder_text)
                            logger.info(f"Отправлено напоминание о матче {match['home']} vs {match['away']} пользователю {user_id}")
                            logger.info(f"Время до матча: {minutes_until_match} минут")
                        except Exception as e:
                            logger.error(f"Ошибка отправки напоминания пользователю {user_id}: {str(e)}")
            
            # Проверяем, что матч начинается прямо сейчас (с погрешностью в 30 секунд)
            elif -0.5 <= minutes_until_match <= 0.5:
                start_text = f"🎮 Матч начинается!\n\n"
                start_text += f"{match['home']} vs {match['away']}\n"
                start_text += f"🏆 {match['competition']}"
                
                # Отправляем уведомление только подписанным пользователям
                for user_id, settings in config.get('user_settings', {}).items():
                    if (settings.get('match_reminders', True) and 
                        (match['home'] in settings.get('subscribed_teams', []) or 
                         match['away'] in settings.get('subscribed_teams', []))):
                        try:
                            await context.bot.send_message(chat_id=user_id, text=start_text)
                            logger.info(f"Отправлено уведомление о начале матча {match['home']} vs {match['away']} пользователю {user_id}")
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {str(e)}")

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /settings"""
    await show_settings(update.message)

async def get_user_balance(user_id: str) -> int:
    """Получить баланс пользователя"""
    return user_currency.get(str(user_id), 1000)  # Начальный баланс 1000

async def update_user_balance(user_id: str, amount: int):
    """Обновить баланс пользователя"""
    user_id = str(user_id)
    if user_id not in user_currency:
        user_currency[user_id] = 1000
    user_currency[user_id] += amount
    save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)  # Сохраняем после каждого обновления

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /predict"""
    user_id = str(update.effective_user.id)
    
    # Проверяем баланс пользователя
    balance = await get_user_balance(user_id)
    if balance < PREDICTION_COST:
        await update.message.reply_text(
            f"❌ У вас недостаточно монет для прогноза!\n"
            f"Стоимость прогноза: {PREDICTION_COST} монет\n"
            f"Ваш баланс: {balance} монет"
        )
        return
    
    # Получаем текущие матчи
    matches = await fetch_matches()
    
    # Фильтруем матчи, на которые можно сделать прогноз
    has_vip = has_active_item(user_id, 'vip_predict')
    
    if has_vip:
        # Для VIP пользователей - все текущие матчи
        available_matches = [m for m in matches if m['status'] in ['SCHEDULED', 'LIVE', 'IN_PLAY', 'PAUSED']]
    else:
        # Для обычных пользователей - только запланированные матчи
        available_matches = [m for m in matches if m['status'] == 'SCHEDULED']
    
    if not available_matches:
        await update.message.reply_text("❌ Сейчас нет доступных матчей для прогноза!")
        return
    
    # Создаем клавиатуру с доступными матчами
    keyboard = []
    for match in available_matches:
        button_text = f"{match['home']} vs {match['away']} ({match['date']} {match['time']})"
        callback_data = f"predict_{match['home']}_{match['away']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    # Добавляем кнопку возврата
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Формируем сообщение с информацией о системе наград
    message = (
        "⚽️ Выберите матч для прогноза:\n"
        f"💰 Стоимость прогноза: {PREDICTION_COST} монет\n\n"
        "🏆 Система наград:\n"
        f"• Точный счёт: {PREDICTION_REWARD_EXACT} монет\n"
        f"• Правильная разница голов: {PREDICTION_REWARD_DIFF} монет\n"
        f"• Правильный исход: {PREDICTION_REWARD_OUTCOME} монет\n\n"
        "🌟 Топовые матчи дают повышенные награды!"
    )
    
    if has_vip:
        message += "\n✨ У вас есть VIP-прогноз! Вы можете прогнозировать текущие матчи."
    
    await update.message.reply_text(message, reply_markup=reply_markup)

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /balance"""
    user_id = str(update.effective_user.id)
    balance = await get_user_balance(user_id)
    
    await update.message.reply_text(
        f"💰 Ваш текущий баланс: {balance} монет\n\n"
        "💡 Вы можете заработать монеты, делая точные прогнозы на матчи!"
    )

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /top"""
    # Сортируем пользователей по балансу
    sorted_users = sorted(user_currency.items(), key=lambda x: x[1], reverse=True)
    
    text = "🏆 Топ предсказателей:\n\n"
    for i, (user_id, balance) in enumerate(sorted_users[:10], 1):
        name = get_user_display_name(user_id)
        
        # Добавляем VIP-статус и пользовательский статус
        vip_status = "👑 " if has_active_item(user_id, 'vip_status') else ""
        custom_status = f"\n💭 {user_statuses[user_id]}" if user_id in user_statuses else ""
        
        text += f"{i}. {vip_status}{name} - {balance} монет{custom_status}\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, reply_markup=reply_markup)

async def process_prediction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка прогноза на матч"""
    query = update.callback_query
    user_id = str(query.from_user.id)
    
    # Получаем информацию о матче из callback_data
    _, home_team, away_team = query.data.split('_')
    
    # Получаем текущие матчи
    matches = await fetch_matches()
    current_match = None
    
    for match in matches:
        if match['home'] == home_team and match['away'] == away_team:
            current_match = match
            break
    
    if not current_match:
        await query.answer("❌ Матч не найден!")
        return
    
    # Проверяем, не начался ли уже матч
    if current_match['status'] in ['LIVE', 'IN_PLAY', 'PAUSED'] and not has_active_item(user_id, 'vip_predict'):
        await query.answer("❌ Матч уже начался! Вы не можете сделать прогноз.")
        return
    
    # Проверяем, есть ли у пользователя достаточно монет
    user_balance = await get_user_balance(user_id)
    if user_balance < PREDICTION_COST:
        await query.answer(f"❌ Недостаточно монет! Нужно: {PREDICTION_COST}, у вас: {user_balance}")
        return
    
    # Проверяем наличие бустеров
    has_double_reward = has_active_item(user_id, 'double_reward')
    has_insurance = has_active_item(user_id, 'insurance')
    
    # Определяем, является ли матч топовым
    is_top_match = home_team in TOP_TEAMS and away_team in TOP_TEAMS
    multiplier = TOP_MATCH_MULTIPLIER if is_top_match else 1.0
    
    # Рассчитываем возможные награды с учетом коэффициента
    exact_reward = int(PREDICTION_REWARD_EXACT * multiplier)
    diff_reward = int(PREDICTION_REWARD_DIFF * multiplier)
    outcome_reward = int(PREDICTION_REWARD_OUTCOME * multiplier)
    
    # Если есть двойная награда, учитываем её
    if has_double_reward:
        exact_reward *= 2
        diff_reward *= 2
        outcome_reward *= 2
    
    # Сохраняем информацию о матче в контексте пользователя
    context.user_data['predicting_match'] = {
        'home': home_team,
        'away': away_team,
        'double_reward': has_double_reward,
        'insurance': has_insurance
    }
    
    # Формируем сообщение с учетом бустеров и коэффициентов
    top_match_text = "\n🌟 Это топовый матч! Награды увеличены!" if is_top_match else ""
    boosters_text = ""
    if has_double_reward:
        boosters_text += "\n🎯 У вас активирован бустер 'Двойная награда'!"
    if has_insurance:
        boosters_text += "\n🛡️ У вас активирована 'Страховка'!"
    
    await query.edit_message_text(
        f"⚽️ Прогноз на матч: {home_team} vs {away_team}\n"
        f"💰 Стоимость прогноза: {PREDICTION_COST} монет\n"
        f"🏆 Система наград:{top_match_text}\n"
        f"• Точный счёт: {exact_reward} монет\n"
        f"• Правильная разница голов: {diff_reward} монет\n"
        f"• Правильный исход: {outcome_reward} монет{boosters_text}\n\n"
        "Введите ваш прогноз в формате 'X-Y', где X - голы домашней команды, Y - голы гостевой команды.\n"
        "Например: 2-1"
    )

async def handle_prediction_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода прогноза"""
    user_id = str(update.effective_user.id)
    prediction_text = update.message.text.strip()
    
    # Проверяем формат прогноза
    if not re.match(r'^\d+-\d+$', prediction_text):
        await update.message.reply_text(
            "❌ Неверный формат прогноза!\n"
            "Введите прогноз в формате 'X-Y', где X и Y - целые числа.\n"
            "Например: 2-1"
        )
        return
    
    # Получаем информацию о матче из контекста
    match_info = context.user_data.get('predicting_match', {})
    if not match_info:
        await update.message.reply_text("❌ Информация о матче не найдена!")
        return
    
    home_team = match_info['home']
    away_team = match_info['away']
    double_reward = match_info.get('double_reward', False)
    insurance = match_info.get('insurance', False)
    
    # Списываем стоимость прогноза
    await update_user_balance(user_id, -PREDICTION_COST)
    
    # Сохраняем прогноз
    match_id = f"{home_team}_{away_team}"
    if user_id not in user_predictions:
        user_predictions[user_id] = {}
    
    user_predictions[user_id][match_id] = {
        'prediction': prediction_text,
        'timestamp': datetime.now(pytz.UTC).isoformat(),
        'double_reward': double_reward,
        'insurance': insurance
    }
    
    # Используем бустеры, если они активны
    boosters_text = ""
    if double_reward:
        use_item(user_id, 'double_reward')
        boosters_text += "\n🎯 Бустер 'Двойная награда' активирован!"
    
    if insurance:
        use_item(user_id, 'insurance')
        boosters_text += "\n🛡️ Бустер 'Страховка' активирован!"
    
    try:
        final_home, final_away = map(int, match['score'].split(' : '))
        
        # Проверяем все прогнозы для этого матча
        for user_id, prediction_data in user_predictions[match_id].items():
            pred_home, pred_away = prediction_data['scores']
            boosters = prediction_data.get('boosters', {})
            
            if pred_home == final_home and pred_away == final_away:
                # Точное попадание
                reward = PREDICTION_REWARD_EXACT
                if boosters.get('double_reward'):
                    reward *= 2
                
                await update_user_balance(user_id, reward)
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=f"🎉 Ваш прогноз на матч {match['home']} - {match['away']} оказался точным!\n"
                             f"💰 Вы получаете {reward} монет!"
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления о выигрыше: {str(e)}")
            elif pred_home == final_home or pred_away == final_away:
                # Правильная разница голов
                reward = PREDICTION_REWARD_DIFF
                if boosters.get('double_reward'):
                    reward *= 2
                
                await update_user_balance(user_id, reward)
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=f"🎉 Ваш прогноз на матч {match['home']} - {match['away']} оказался правильным!\n"
                             f"💰 Вы получаете {reward} монет!"
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления о выигрыше: {str(e)}")
            elif pred_home == final_home and pred_away == final_away:
                # Правильный исход
                reward = PREDICTION_REWARD_OUTCOME
                if boosters.get('double_reward'):
                    reward *= 2
                
                await update_user_balance(user_id, reward)
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=f"🎉 Ваш прогноз на матч {match['home']} - {match['away']} оказался правильным!\n"
                             f"💰 Вы получаете {reward} монет!"
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления о выигрыше: {str(e)}")
            elif boosters.get('insurance'):
                # Возврат ставки при наличии страховки
                await update_user_balance(user_id, PREDICTION_COST)
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=f"🛡️ Сработала страховка! Ваша ставка {PREDICTION_COST} монет на матч {match['home']} - {match['away']} возвращена."
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления о страховке: {str(e)}")
        
        # Очищаем прогнозы для завершенного матча
        del user_predictions[match_id]
        # Сохраняем обновленные данные
        save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
        
    except (ValueError, KeyError) as e:
        logger.error(f"Ошибка при проверке прогнозов: {str(e)}")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /admin"""
    user_id = str(update.effective_user.id)
    
    # Проверяем, имеет ли пользователь доступ к админ-панели
    has_access = False
    
    # Developer имеет полный доступ
    if user_id == ADMIN_ID:
        has_access = True
        user_roles[user_id] = 'developer'  # Устанавливаем роль developer для админа
    # Проверяем роль пользователя
    elif user_id in user_roles:
        role = user_roles[user_id]
        if role in ['developer', 'admin', 'moderator', 'operator']:
            has_access = True
    
    if not has_access:
        await update.message.reply_text("❌ У вас нет доступа к панели администратора!")
        return
    
    # Определяем доступные функции в зависимости от роли
    role = user_roles.get(user_id, 'user')
    
    keyboard = []
    
    # Общие функции для всех ролей с доступом
    if role in ['developer', 'admin', 'moderator', 'operator']:
        keyboard.append([InlineKeyboardButton("📨 Массовая рассылка", callback_data='admin_broadcast')])
    
    # Функции для operator и выше
    if role in ['developer', 'admin', 'operator']:
        keyboard.append([InlineKeyboardButton("📊 Статистика бота", callback_data='admin_stats')])
    
    # Функции только для admin и developer
    if role in ['developer', 'admin']:
        keyboard.append([InlineKeyboardButton("👥 Список пользователей", callback_data='admin_users_list')])
        keyboard.append([InlineKeyboardButton("💰 Изменить баланс", callback_data='admin_modify_balance')])
        keyboard.append([InlineKeyboardButton("🎁 Управление предметами", callback_data='admin_manage_items')])
        keyboard.append([InlineKeyboardButton("💲 Управление ценами", callback_data='admin_manage_prices')])
        keyboard.append([InlineKeyboardButton("👑 Управление ролями", callback_data='admin_manage_roles')])
    
    # Кнопка возврата
    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data='back_to_main')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    role_info = USER_ROLES[role]
    await update.message.reply_text(
        f"🔐 Панель администратора\n\n"
        f"👤 Ваша роль: {role_info['name']}\n"
        f"📝 Описание: {role_info['description']}\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup
    )

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода для админ-панели"""
    user_id = str(update.message.from_user.id)
    admin_state = context.user_data.get('admin_state', '')
    
    # Проверяем, имеет ли пользователь доступ к админ-панели
    has_access = False
    
    # Developer имеет полный доступ
    if user_id == ADMIN_ID:
        has_access = True
    # Проверяем роль пользователя
    elif user_id in user_roles:
        role = user_roles[user_id]
        if role in ['developer', 'admin', 'moderator', 'operator']:
            # Проверяем, не истек ли срок действия роли
            if role != 'developer':  # Developer не имеет срока действия
                if user_id in user_items and 'role_expiry' in user_items[user_id]:
                    if role in user_items[user_id]['role_expiry']:
                        expiry_time = user_items[user_id]['role_expiry'][role]
                        if int(time.time()) > expiry_time:
                            # Роль истекла, удаляем её
                            user_roles.pop(user_id, None)
                            user_items[user_id]['role_expiry'].pop(role, None)
                            save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
                            await update.message.reply_text("❌ Срок действия вашей роли истек!")
                            return
                        else:
                            has_access = True
                    else:
                        has_access = True
                else:
                    has_access = True
            else:
                has_access = True
    
    if not has_access:
        return
    
    if admin_state == 'waiting_user_id':
        # Проверяем, имеет ли пользователь доступ к изменению баланса
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
            await update.message.reply_text("❌ У вас нет доступа к этой функции!")
            return
            
        target_user_id = update.message.text
        
        if target_user_id not in user_currency:
            await update.message.reply_text(
                "❌ Пользователь не найден!\n"
                "Попробуйте снова или нажмите /admin для возврата в панель администратора."
            )
            return
        
        context.user_data['target_user_id'] = target_user_id
        context.user_data['admin_state'] = 'waiting_amount'
        
        current_balance = user_currency.get(target_user_id, 0)
        await update.message.reply_text(
            f"👤 Пользователь: {target_user_id}\n"
            f"💰 Текущий баланс: {current_balance} монет\n\n"
            "Введите сумму для изменения баланса:\n"
            "• Положительное число для добавления\n"
            "• Отрицательное для снятия\n"
            "Например: 500 или -300"
        )
    
    elif admin_state == 'waiting_amount':
        # Проверяем, имеет ли пользователь доступ к изменению баланса
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
            await update.message.reply_text("❌ У вас нет доступа к этой функции!")
            return
            
        try:
            amount = int(update.message.text)
            target_user_id = context.user_data['target_user_id']
            
            await update_user_balance(target_user_id, amount)
            new_balance = user_currency[target_user_id]
            
            await update.message.reply_text(
                f"✅ Баланс пользователя {target_user_id} успешно изменен!\n"
                f"💰 Новый баланс: {new_balance} монет"
            )
            
            # Отправляем уведомление пользователю
            try:
                if amount > 0:
                    notification = f"💰 Администратор пополнил ваш баланс на {amount} монет!\n"
                else:
                    notification = f"💰 Администратор снял с вашего баланса {abs(amount)} монет!\n"
                notification += f"Текущий баланс: {new_balance} монет"
                
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=notification
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления пользователю: {str(e)}")
            
            # Очищаем состояние
            context.user_data.pop('admin_state', None)
            context.user_data.pop('target_user_id', None)
            
        except ValueError:
            await update.message.reply_text(
                "❌ Неверный формат суммы! Введите целое число.\n"
                "Например: 500 или -300"
            )
        except Exception as e:
            await update.message.reply_text(
                "❌ Произошла ошибка при изменении баланса!\n"
                "Попробуйте снова или обратитесь к разработчику."
            )
            logger.error(f"Ошибка изменения баланса: {str(e)}")
    
    elif admin_state == 'waiting_broadcast_message':
        # Проверяем, имеет ли пользователь доступ к массовой рассылке
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin', 'moderator', 'operator']):
            await update.message.reply_text("❌ У вас нет доступа к этой функции!")
            return
            
        message_text = update.message.text
        
        # Сохраняем сообщение для подтверждения
        context.user_data['broadcast_message'] = message_text
        context.user_data['admin_state'] = 'waiting_broadcast_confirm'
        
        keyboard = [
            [InlineKeyboardButton("✅ Подтвердить", callback_data='admin_broadcast_confirm')],
            [InlineKeyboardButton("❌ Отмена", callback_data='admin_panel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📨 Предварительный просмотр сообщения:\n\n"
            f"{message_text}\n\n"
            f"Сообщение будет отправлено {len(user_currency)} пользователям.\n"
            "Подтвердите отправку:",
            reply_markup=reply_markup
        )
    
    elif admin_state == 'waiting_item_user_id':
        # Проверяем, имеет ли пользователь доступ к управлению предметами
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
            await update.message.reply_text("❌ У вас нет доступа к этой функции!")
            return
            
        target_user_id = update.message.text
        
        # Проверяем, существует ли пользователь в любом из словарей данных
        user_exists = (target_user_id in user_names or 
                       target_user_id in user_currency or 
                       target_user_id in user_nicknames or
                       target_user_id in user_predictions)
        
        if not user_exists:
            await update.message.reply_text(
                "❌ Пользователь не найден!\n"
                "Попробуйте снова или нажмите /admin для возврата в панель администратора."
            )
            return
        
        context.user_data['target_user_id'] = target_user_id
        context.user_data['admin_state'] = 'waiting_item_selection'
        
        # Показываем список предметов для выбора
        keyboard = []
        for item_id, item in SHOP_ITEMS.items():
            keyboard.append([InlineKeyboardButton(item['name'], callback_data=f'admin_add_item_{item_id}')])
        
        keyboard.append([InlineKeyboardButton("🔙 Отмена", callback_data='admin_panel')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Получаем имя пользователя из доступных словарей
        user_name = user_names.get(target_user_id) or user_nicknames.get(target_user_id) or f"User{target_user_id}"
        
        await update.message.reply_text(
            f"👤 Пользователь: {user_name} (ID: {target_user_id})\n\n"
            "Выберите предмет для добавления:",
            reply_markup=reply_markup
        )
    
    elif admin_state == 'waiting_item_selection':
        # Проверяем, имеет ли пользователь доступ к управлению предметами
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
            await update.message.reply_text("❌ У вас нет доступа к этой функции!")
            return
            
        item_id = update.message.text
        
        if item_id not in SHOP_ITEMS:
            await update.message.reply_text(
                "❌ Предмет не найден!\n"
                "Попробуйте снова или нажмите /admin для возврата в панель администратора."
            )
            return
        
        context.user_data['item_id'] = item_id
        context.user_data['admin_state'] = 'waiting_item_quantity'
        
        current_item = SHOP_ITEMS[item_id]
        await update.message.reply_text(
            f"🎁 {current_item['name']}\n\n"
            "Введите количество:"
        )
    
    elif admin_state == 'waiting_item_quantity':
        # Проверяем, имеет ли пользователь доступ к управлению предметами
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
            await update.message.reply_text("❌ У вас нет доступа к этой функции!")
            return
            
        try:
            quantity = int(update.message.text)
            if quantity < 0:
                await update.message.reply_text("❌ Количество не может быть отрицательным!")
                return
                
            item_id = context.user_data['item_id']
            old_quantity = user_items[user_id].get(item_id, 0)
            
            # Обновляем количество
            user_items[user_id][item_id] = old_quantity + quantity
            
            # Сохраняем изменения
            save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
            
            await update.message.reply_text(
                f"✅ Количество предмета {SHOP_ITEMS[item_id]['name']} успешно изменено!\n"
                f"Старое количество: {old_quantity}\n"
                f"Новое количество: {user_items[user_id][item_id]}"
            )
            
            # Очищаем состояние
            context.user_data.pop('admin_state', None)
            context.user_data.pop('item_id', None)
            
        except ValueError:
            await update.message.reply_text(
                "❌ Неверный формат количества! Введите целое число."
            )
    
    elif admin_state == 'waiting_price_item_id':
        # Проверяем, имеет ли пользователь доступ к управлению ценами
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
            await update.message.reply_text("❌ У вас нет доступа к этой функции!")
            return
        
        item_id = update.message.text
        
        if item_id not in SHOP_ITEMS:
            await update.message.reply_text(
                "❌ Предмет не найден!\n"
                "Попробуйте снова или нажмите /admin для возврата в панель администратора."
            )
            return
        
        context.user_data['price_item_id'] = item_id
        context.user_data['admin_state'] = 'waiting_new_price'
        
        current_price = SHOP_ITEMS[item_id]['price']
        await update.message.reply_text(
            f"🏷️ Предмет: {SHOP_ITEMS[item_id]['name']}\n"
            f"💰 Текущая цена: {current_price} монет\n\n"
            "Введите новую цену:"
        )
    
    elif admin_state == 'waiting_new_price':
        # Проверяем, имеет ли пользователь доступ к управлению ценами
        if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
            await update.message.reply_text("❌ У вас нет доступа к этой функции!")
            return
            
        try:
            new_price = int(update.message.text)
            if new_price < 0:
                await update.message.reply_text("❌ Цена не может быть отрицательной!")
                return
                
            item_id = context.user_data['price_item_id']
            old_price = SHOP_ITEMS[item_id]['price']
            
            # Обновляем цену
            SHOP_ITEMS[item_id]['price'] = new_price
            
            # Сохраняем изменения
            save_shop_items()
            
            await update.message.reply_text(
                f"✅ Цена предмета {SHOP_ITEMS[item_id]['name']} успешно изменена!\n"
                f"Старая цена: {old_price} монет\n"
                f"Новая цена: {new_price} монет"
            )
            
            # Очищаем состояние
            context.user_data.pop('admin_state', None)
            context.user_data.pop('price_item_id', None)
            
        except ValueError:
            await update.message.reply_text(
                "❌ Неверный формат цены! Введите целое число."
            )
    
    return True

async def admin_users_list(query):
    """Показать список пользователей для админа"""
    text = "👥 Список пользователей:\n\n"
    for user_id, balance in user_currency.items():
        name = get_user_display_name(user_id)
        text += f"👤 {name}\n"
        text += f"ID: {user_id}\n"
        text += f"💰 Баланс: {balance} монет\n\n"
        
        keyboard = [
        [InlineKeyboardButton("🔙 Назад к админ-панели", callback_data='admin_panel')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)

async def admin_stats(query):
    """Показать статистику бота"""
    # Собираем статистику
    total_users = len(user_currency)
    total_predictions = sum(len(preds) for preds in user_predictions.values())
    total_coins = sum(user_currency.values())
    
    # Статистика по предметам
    item_stats = {}
    for user_id, items in user_items.items():
        for item_id, value in items.items():
            if item_id in SHOP_ITEMS:
                if item_id not in item_stats:
                    item_stats[item_id] = 0
                item_stats[item_id] += 1
    
    text = "📊 Статистика бота:\n\n"
    text += f"👥 Всего пользователей: {total_users}\n"
    text += f"🎯 Всего прогнозов: {total_predictions}\n"
    text += f"💰 Всего монет в обороте: {total_coins}\n\n"
    
    text += "🎁 Статистика по предметам:\n"
    for item_id, count in item_stats.items():
        text += f"• {SHOP_ITEMS[item_id]['name']}: {count} шт.\n"
    
    keyboard = [
        [InlineKeyboardButton("🔙 Назад к админ-панели", callback_data='admin_panel')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)

async def admin_broadcast(query, context):
    """Начать процесс массовой рассылки"""
    # Сохраняем состояние в контексте
    context.user_data['admin_state'] = 'waiting_broadcast_message'
    
    await query.edit_message_text(
        "📨 Массовая рассылка\n\n"
        "Введите текст сообщения для отправки всем пользователям:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Отмена", callback_data='admin_panel')
        ]])
    )

async def admin_broadcast_send(query, context):
    """Отправить массовую рассылку"""
    message_text = context.user_data.get('broadcast_message', '')
    if not message_text:
        await query.answer("❌ Сообщение не найдено!")
        return
    
    # Очищаем состояние
    context.user_data.pop('admin_state', None)
    context.user_data.pop('broadcast_message', None)
    
    # Отправляем сообщение всем пользователям
    success_count = 0
    fail_count = 0
    
    await query.edit_message_text("📨 Отправка сообщений...")
    
    # Добавляем логирование для отладки
    logger.debug(f"Начинаем рассылку сообщения: {message_text}")
    logger.debug(f"Количество пользователей для рассылки: {len(user_currency)}")
    
    for user_id in list(user_currency.keys()):
        try:
            logger.debug(f"Отправка сообщения пользователю {user_id}")
            await context.bot.send_message(
                chat_id=int(user_id),  # Преобразуем ID в число
                text=f"📢 Сообщение от администратора:\n\n{message_text}"
            )
            success_count += 1
            logger.debug(f"Сообщение успешно отправлено пользователю {user_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения пользователю {user_id}: {str(e)}")
            fail_count += 1
    
    logger.debug(f"Рассылка завершена. Успешно: {success_count}, ошибок: {fail_count}")
    
    await query.edit_message_text(
        f"📨 Рассылка завершена!\n\n"
        f"✅ Успешно отправлено: {success_count}\n"
        f"❌ Ошибок: {fail_count}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Назад к админ-панели", callback_data='admin_panel')
        ]])
    )

async def admin_manage_items(query, context):
    """Управление предметами пользователей"""
    # Сохраняем состояние в контексте
    context.user_data['admin_state'] = 'waiting_item_user_id'
    
    await query.edit_message_text(
        "🎁 Управление предметами\n\n"
        "Введите ID пользователя:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Отмена", callback_data='admin_panel')
        ]])
    )

async def admin_add_item(query, context, item_id):
    """Добавить предмет пользователю"""
    target_user_id = context.user_data.get('target_user_id')
    
    # Проверяем, существует ли пользователь в любом из словарей данных
    user_exists = (target_user_id in user_names or 
                   target_user_id in user_currency or 
                   target_user_id in user_nicknames or
                   target_user_id in user_predictions)
    
    if not user_exists:
        await query.answer("❌ Пользователь не найден!")
        return
    
    if item_id not in SHOP_ITEMS:
        await query.answer("❌ Предмет не найден!")
        return
    
    # Добавляем предмет пользователю
    if target_user_id not in user_items:
        user_items[target_user_id] = {}
    
    item = SHOP_ITEMS[item_id]
    current_time = datetime.now(pytz.UTC)
    
    # Проверяем, связан ли предмет с ролью
    role_name = None
    if item_id == 'role_admin' or item['name'] == '🔐 Admin':
        role_name = 'admin'
    elif item_id == 'role_moderator' or item['name'] == '🛡️ Moderator':
        role_name = 'moderator'
    elif item_id == 'role_operator' or item['name'] == '🔧 Operator':
        role_name = 'operator'
    
    # Если предмет связан с ролью, назначаем её пользователю
    if role_name:
        user_roles[target_user_id] = role_name
        
        # Добавляем срок действия роли (30 дней)
        if 'role_expiry' not in user_items.get(target_user_id, {}):
            if target_user_id not in user_items:
                user_items[target_user_id] = {}
            user_items[target_user_id]['role_expiry'] = {}
        
        user_items[target_user_id]['role_expiry'][role_name] = int(time.time()) + (30 * 24 * 60 * 60)  # 30 дней
    
    # Проверяем, требует ли предмет ввода от пользователя
    requires_input = False
    input_instructions = ""
    
    if item_id == 'custom_nickname' or item['name'] == '📝 Смена никнейма':
        user_items[target_user_id]['awaiting_nickname'] = True
        requires_input = True
        input_instructions = "Отправьте боту новый никнейм (максимум 20 символов)."
    elif item_id == 'custom_status' or item['name'] == '📝 Смена статуса':
        user_items[target_user_id]['awaiting_status'] = True
        requires_input = True
        input_instructions = "Отправьте боту новый статус (максимум 50 символов)."
    
    if item['duration'] > 1:
        # Для предметов с длительностью
        expiration = current_time + timedelta(days=item['duration'])
        user_items[target_user_id][item_id] = expiration.isoformat()
    else:
        # Для одноразовых предметов
        if item_id not in user_items[target_user_id]:
            user_items[target_user_id][item_id] = 0
        user_items[target_user_id][item_id] += 1
    
    # Сохраняем изменения
    save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
    
    # Определяем роль и имя администратора
    admin_id = str(query.from_user.id)
    admin_role = "Developer" if admin_id == ADMIN_ID else user_roles.get(admin_id, "Администратор")
    admin_name = user_names.get(admin_id) or user_nicknames.get(admin_id) or "Администратор"
    
    # Отправляем уведомление пользователю
    try:
        message_text = f"🎁 {admin_name} ({admin_role}) добавил вам предмет: {item['name']}!"
        
        if role_name:
            message_text += f"\n\n🎖️ Вам также назначена роль: {USER_ROLES[role_name]['name']}!\nТеперь у вас есть доступ к дополнительным функциям бота."
        
        if requires_input:
            message_text += f"\n\n✏️ Для использования предмета: {input_instructions}"
        
        await context.bot.send_message(
            chat_id=target_user_id,
            text=message_text
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления пользователю: {str(e)}")
    
    # Получаем имя пользователя из доступных словарей
    user_name = user_names.get(target_user_id) or user_nicknames.get(target_user_id) or f"User{target_user_id}"
    
    # Очищаем состояние
    context.user_data.pop('admin_state', None)
    context.user_data.pop('target_user_id', None)
    
    success_message = f"✅ Предмет {item['name']} успешно добавлен пользователю {user_name} (ID: {target_user_id})!"
    if role_name:
        success_message += f"\n\n🎖️ Пользователю также назначена роль: {USER_ROLES[role_name]['name']}!"
    if requires_input:
        success_message += f"\n\n✏️ Пользователю отправлена инструкция по использованию предмета."
    
    await query.edit_message_text(
        success_message,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Назад к админ-панели", callback_data='admin_panel')
        ]])
    )

async def admin_manage_prices(query, context):
    """Управление ценами в магазине"""
    keyboard = []
    
    # Группируем товары по категориям
    categories = {
        'boosters': '🎯 Бустеры',
        'game': '🎮 Игровые возможности',
        'football': '⚽️ Футбольные привилегии'
    }
    
    text = "💲 Управление ценами в магазине\n\n"
    
    for category, title in categories.items():
        text += f"{title}:\n"
        for item_id, item in SHOP_ITEMS.items():
            if (category == 'boosters' and item_id in ['double_reward', 'insurance', 'vip_predict']) or \
               (category == 'game' and item_id in ['custom_nickname', 'custom_status', 'vip_status']) or \
               (category == 'football' and item_id in ['extended_stats', 'priority_notifications', 'tournament_tables']):
                text += f"• {item['name']} - {item['price']} монет\n"
        text += "\n"
    
    # Сохраняем состояние в контексте
    context.user_data['admin_state'] = 'waiting_price_item_id'
    
    text += "Введите ID предмета для изменения цены:\n"
    text += "(double_reward, insurance, vip_predict, custom_nickname, custom_status, vip_status, extended_stats, priority_notifications, tournament_tables)"
    
    keyboard.append([InlineKeyboardButton("🔙 Отмена", callback_data='admin_panel')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)

def save_shop_items():
    """Сохранить настройки магазина"""
    try:
        with open('shop_items.json', 'w', encoding='utf-8') as f:
            json.dump(SHOP_ITEMS, f, ensure_ascii=False, indent=4)
        logger.info("Настройки магазина успешно сохранены")
    except Exception as e:
        logger.error(f"Ошибка при сохранении настроек магазина: {str(e)}")

def load_shop_items():
    """Загрузить настройки магазина"""
    global SHOP_ITEMS
    try:
        if os.path.exists('shop_items.json'):
            with open('shop_items.json', 'r', encoding='utf-8') as f:
                SHOP_ITEMS = json.load(f)
            logger.info("Настройки магазина успешно загружены")
    except Exception as e:
        logger.error(f"Ошибка при загрузке настроек магазина: {str(e)}")

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user_id = str(update.effective_user.id)
    
    # Проверяем ожидание никнейма
    if user_id in user_items and user_items[user_id].get('awaiting_nickname'):
        # Обработка нового никнейма
        new_nickname = update.message.text[:20]  # Ограничиваем длину
        
        # Проверяем, не занят ли никнейм другим пользователем
        if new_nickname in user_nicknames.values():
            await update.message.reply_text(
                "❌ Этот никнейм уже занят другим пользователем!\n"
                "Пожалуйста, выберите другой никнейм."
            )
            return True
        
        user_nicknames[user_id] = new_nickname
        user_items[user_id].pop('awaiting_nickname', None)  # Удаляем флаг ожидания
        save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
        await update.message.reply_text(
            f"✅ Ваш новый никнейм успешно установлен: {new_nickname}\n"
            "Он будет отображаться в топе игроков и других местах."
        )
        return True
    
    # Проверяем ожидание статуса
    if user_id in user_items and user_items[user_id].get('awaiting_status'):
        # Обработка нового статуса
        new_status = update.message.text[:50]  # Ограничиваем длину
        user_statuses[user_id] = new_status
        user_items[user_id].pop('awaiting_status', None)  # Удаляем флаг ожидания
        save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
        await update.message.reply_text(
            f"✅ Ваш новый статус успешно установлен: {new_status}\n"
            "Он будет отображаться в вашем профиле в топе игроков."
        )
        return True
    
    # Проверяем ожидание ID пользователя для назначения роли
    elif 'awaiting_user_id_for_role' in context.user_data:
        target_user_id = update.message.text.strip()
        
        # Проверяем, существует ли пользователь в любом из словарей данных
        user_exists = (target_user_id in user_names or 
                       target_user_id in user_currency or 
                       target_user_id in user_nicknames or
                       target_user_id in user_predictions)
        
        if not user_exists:
            await update.message.reply_text("❌ Пользователь с таким ID не найден!")
            context.user_data.pop('awaiting_user_id_for_role', None)
            return True
        
        # Сохраняем ID пользователя и запрашиваем роль
        context.user_data['target_user_id'] = target_user_id
        context.user_data.pop('awaiting_user_id_for_role', None)
        context.user_data['awaiting_role_name'] = True
        
        keyboard = [
            [InlineKeyboardButton("👑 Admin", callback_data="role_admin")],
            [InlineKeyboardButton("🛡️ Moderator", callback_data="role_moderator")],
            [InlineKeyboardButton("🔧 Operator", callback_data="role_operator")],
            [InlineKeyboardButton("👤 User", callback_data="role_user")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_manage_roles")]
        ]
        
        # Получаем имя пользователя из доступных словарей
        user_name = user_names.get(target_user_id) or user_nicknames.get(target_user_id) or f"User{target_user_id}"
        
        await update.message.reply_text(
            f"Выберите роль для пользователя {user_name}:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return True
    
    # Проверяем ожидание ID пользователя для удаления роли
    elif 'awaiting_user_id_for_role_removal' in context.user_data:
        target_user_id = update.message.text.strip()
        
        # Проверяем, существует ли пользователь в любом из словарей данных
        user_exists = (target_user_id in user_names or 
                       target_user_id in user_currency or 
                       target_user_id in user_nicknames or
                       target_user_id in user_predictions)
        
        if not user_exists:
            await update.message.reply_text("❌ Пользователь с таким ID не найден!")
            context.user_data.pop('awaiting_user_id_for_role_removal', None)
            return True
        
        # Проверяем, есть ли у пользователя роль
        if target_user_id not in user_roles:
            await update.message.reply_text("❌ У этого пользователя нет назначенной роли!")
            context.user_data.pop('awaiting_user_id_for_role_removal', None)
            return True
        
        # Проверяем, не пытается ли админ удалить роль developer
        if user_roles[target_user_id] == 'developer' and str(update.message.from_user.id) != ADMIN_ID:
            await update.message.reply_text("❌ Вы не можете удалить роль Developer!")
            context.user_data.pop('awaiting_user_id_for_role_removal', None)
            return True
        
        # Получаем имя пользователя из доступных словарей
        user_name = user_names.get(target_user_id) or user_nicknames.get(target_user_id) or f"User{target_user_id}"
        
        # Удаляем роль
        role_name = user_roles.pop(target_user_id)
        save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
        
        await update.message.reply_text(f"✅ Роль {role_name} успешно удалена у пользователя {user_name}!")
        
        # Возвращаемся в меню управления ролями
        keyboard = [
            [InlineKeyboardButton("👤 Назначить роль", callback_data="admin_assign_role")],
            [InlineKeyboardButton("🗑️ Удалить роль", callback_data="admin_remove_role")],
            [InlineKeyboardButton("📋 Список пользователей с ролями", callback_data="admin_list_roles")],
            [InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]
        ]
        
        await update.message.reply_text(
            "Управление ролями пользователей:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        context.user_data.pop('awaiting_user_id_for_role_removal', None)
        return True
    
    elif 'predicting_match' in context.user_data:
        await handle_prediction_input(update, context)
        return True
    
    elif 'admin_state' in context.user_data:
        await handle_admin_input(update, context)
        return True
    
    return False

async def show_extended_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать расширенную статистику матча"""
    user_id = str(update.effective_user.id)
    
    if not has_active_item(user_id, 'extended_stats'):
        await update.message.reply_text("❌ У вас нет доступа к расширенной статистике!")
        return
    
    matches = await fetch_matches()
    keyboard = []
    
    for match in matches:
        if match['status'] in ['LIVE', 'IN_PLAY', 'PAUSED', 'FINISHED']:
            button_text = f"{match['home']} {match['score']} {match['away']}"
            callback_data = f"stats_{match['home']}_{match['away']}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    if not keyboard:
        await update.message.reply_text("❌ Нет доступных матчей для просмотра статистики!")
        return
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📊 Выберите матч для просмотра расширенной статистики:",
        reply_markup=reply_markup
    )

async def show_tournament_tables(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать турнирные таблицы"""
    user_id = str(update.effective_user.id)
    
    if not has_active_item(user_id, 'tournament_tables'):
        await update.message.reply_text("❌ У вас нет доступа к турнирным таблицам!")
        return
    
    # Здесь будет логика получения турнирных таблиц через API
    # Пока заглушка с демо-данными
    tables = {
        "Премьер-лига": [
            ("Манчестер Сити", 60, 25),
            ("Арсенал", 57, 25),
            ("Ливерпуль", 54, 25)
        ],
        "Ла Лига": [
            ("Реал Мадрид", 62, 25),
            ("Жирона", 56, 25),
            ("Барселона", 54, 25)
        ]
    }
    
    text = "🏆 Турнирные таблицы:\n\n"
    for tournament, teams in tables.items():
        text += f"📊 {tournament}:\n"
        for i, (team, points, games) in enumerate(teams, 1):
            text += f"{i}. {team} - {points} очков ({games} игр)\n"
        text += "\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, reply_markup=reply_markup)

async def run_bot():
    """Запуск бота"""
    global application
    
    try:
        config = load_config()
        if not config["bot_token"]:
            logger.error("Токен бота не настроен!")
            return
            
        application = Application.builder().token(config["bot_token"]).build()
        
        # Добавление обработчиков команд
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("matches", matches_command))
        application.add_handler(CommandHandler("settings", settings_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("predict", predict_command))
        application.add_handler(CommandHandler("balance", balance_command))
        application.add_handler(CommandHandler("top", top_command))
        application.add_handler(CommandHandler("admin", admin_command))
        application.add_handler(CommandHandler("stats", show_extended_stats))
        application.add_handler(CommandHandler("table", show_tournament_tables))
        application.add_handler(CommandHandler("shop", shop_command))
        
        # Добавление обработчика кнопок
        application.add_handler(CallbackQueryHandler(button))
        
        # Обновленный обработчик текстовых сообщений
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text_input
        ))
        
        # Настройка проверки голов каждые 3 секунды
        job_queue = application.job_queue
        job_queue.run_repeating(lambda context: check_and_send_goal_alerts(asyncio.run(fetch_matches()), context), interval=3)
        
        # Настройка проверки предстоящих матчей каждые 30 секунд
        job_queue.run_repeating(check_and_send_match_reminders, interval=30)
        
        # Сохранение данных каждые 2 минуты
        job_queue.run_repeating(lambda context: save_data_periodically(), interval=120)
        
        # Проверка срока действия ролей каждый час
        job_queue.run_repeating(check_roles_periodically, interval=3600)
        
        logger.info("Бот запущен и готов к работе!")
        
        await application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {str(e)}")

async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /shop"""
    await shop_cmd(update, context, SHOP_ITEMS)

def has_active_item(user_id: str, item_id: str) -> bool:
    """Проверка наличия активного предмета у пользователя"""
    return has_active_item_shop(user_id, item_id, user_items)

def use_item(user_id: str, item_id: str) -> bool:
    """Использование предмета"""
    return use_item_shop(user_id, item_id, user_items, save_user_data, user_currency, user_predictions, user_names, user_statuses, user_nicknames)

async def check_predictions(match):
    """Проверка предсказаний после завершения матча"""
    if match['status'] != 'FINISHED':
        return
    
    match_id = f"{match['home']}_{match['away']}"
    
    # Получаем финальный счет
    try:
        final_home, final_away = map(int, match['score'].split('-'))
    except (ValueError, AttributeError):
        logger.error(f"Не удалось получить счет матча {match_id}")
        return
    
    # Определяем, является ли матч топовым
    is_top_match = match['home'] in TOP_TEAMS and match['away'] in TOP_TEAMS
    multiplier = TOP_MATCH_MULTIPLIER if is_top_match else 1.0
    
    # Проверяем прогнозы всех пользователей
    for user_id, predictions in user_predictions.items():
        if match_id in predictions:
            prediction_data = predictions[match_id]
            prediction_text = prediction_data.get('prediction', '')
            
            try:
                pred_home, pred_away = map(int, prediction_text.split('-'))
                
                # Определяем тип совпадения
                is_exact = (pred_home == final_home and pred_away == final_away)
                is_diff = (pred_home - pred_away == final_home - final_away)
                is_outcome = ((pred_home > pred_away and final_home > final_away) or
                             (pred_home < pred_away and final_home < final_away) or
                             (pred_home == pred_away and final_home == final_away))
                
                # Определяем награду
                reward = 0
                reward_type = ""
                
                if is_exact:
                    reward = int(PREDICTION_REWARD_EXACT * multiplier)
                    reward_type = "точный счёт"
                elif is_diff:
                    reward = int(PREDICTION_REWARD_DIFF * multiplier)
                    reward_type = "правильную разницу голов"
                elif is_outcome:
                    reward = int(PREDICTION_REWARD_OUTCOME * multiplier)
                    reward_type = "правильный исход"
                
                # Проверяем наличие двойной награды
                if prediction_data.get('double_reward', False) and reward > 0:
                    reward *= 2
                    reward_type += " (с двойной наградой)"
                
                if reward > 0:
                    # Выдаем награду
                    await update_user_balance(user_id, reward)
                    
                    # Отправляем уведомление
                    try:
                        await application.bot.send_message(
                            chat_id=user_id,
                            text=f"🎉 Ваш прогноз на матч {match['home']} vs {match['away']} принес награду!\n"
                                 f"✅ Прогноз: {prediction_text}, Итог: {match['score']}\n"
                                 f"🏆 Вы угадали {reward_type} и получаете {reward} монет!"
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {str(e)}")
                
                else:
                    # Неправильный прогноз - проверяем страховку
                    if prediction_data.get('insurance', False):
                        # Возвращаем ставку
                        await update_user_balance(user_id, PREDICTION_COST)
                        
                        # Отправляем уведомление
                        try:
                            await application.bot.send_message(
                                chat_id=user_id,
                                text=f"🛡️ Ваш прогноз на матч {match['home']} vs {match['away']} не сбылся, но сработала страховка!\n"
                                     f"❌ Прогноз: {prediction_text}, Итог: {match['score']}\n"
                                     f"💰 Вам возвращено {PREDICTION_COST} монет."
                            )
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {str(e)}")
                    else:
                        # Отправляем уведомление о проигрыше
                        try:
                            await application.bot.send_message(
                                chat_id=user_id,
                                text=f"❌ Ваш прогноз на матч {match['home']} vs {match['away']} не сбылся.\n"
                                     f"Прогноз: {prediction_text}, Итог: {match['score']}"
                            )
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {str(e)}")
                
                # Удаляем проверенный прогноз
                del user_predictions[user_id][match_id]
                
            except (ValueError, KeyError) as e:
                logger.error(f"Ошибка при проверке прогноза пользователя {user_id}: {str(e)}")
    
    # Сохраняем обновленные данные
    save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)

async def admin_manage_roles(query):
    """Управление ролями пользователей"""
    user_id = str(query.from_user.id)
    
    # Проверяем, имеет ли пользователь доступ к этой функции
    if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
        await query.answer("❌ У вас нет доступа к этой функции!")
        return
    
    keyboard = []
    
    # Добавляем кнопки для назначения ролей
    keyboard.append([InlineKeyboardButton("➕ Назначить роль пользователю", callback_data='admin_assign_role')])
    keyboard.append([InlineKeyboardButton("➖ Удалить роль у пользователя", callback_data='admin_remove_role')])
    keyboard.append([InlineKeyboardButton("📋 Список пользователей с ролями", callback_data='admin_list_roles')])
    
    # Кнопка возврата
    keyboard.append([InlineKeyboardButton("🔙 Назад к админ-панели", callback_data='admin_panel')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👑 Управление ролями пользователей\n\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )

async def admin_assign_role(query, context):
    """Назначение роли пользователю"""
    user_id = str(query.from_user.id)
    
    # Проверяем, имеет ли пользователь доступ к этой функции
    if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
        await query.answer("❌ У вас нет доступа к этой функции!")
        return
    
    # Сохраняем состояние для ожидания ввода ID пользователя
    context.user_data['awaiting_user_id_for_role'] = True
    
    keyboard = [[InlineKeyboardButton("🔙 Отмена", callback_data='admin_manage_roles')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👤 Назначение роли пользователю\n\n"
        "Введите ID пользователя, которому хотите назначить роль.\n"
        "Например: 123456789",
        reply_markup=reply_markup
    )

async def admin_remove_role(query, context):
    """Удаление роли у пользователя"""
    user_id = str(query.from_user.id)
    
    # Проверяем, имеет ли пользователь доступ к этой функции
    if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
        await query.answer("❌ У вас нет доступа к этой функции!")
        return
    
    # Сохраняем состояние для ожидания ввода ID пользователя
    context.user_data['awaiting_user_id_for_role_removal'] = True
    
    keyboard = [[InlineKeyboardButton("🔙 Отмена", callback_data='admin_manage_roles')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👤 Удаление роли у пользователя\n\n"
        "Введите ID пользователя, у которого хотите удалить роль.\n"
        "Например: 123456789",
        reply_markup=reply_markup
    )

async def admin_list_roles(query):
    """Список пользователей с ролями"""
    user_id = str(query.from_user.id)
    
    # Проверяем, имеет ли пользователь доступ к этой функции
    if user_id != ADMIN_ID and (user_id not in user_roles or user_roles[user_id] not in ['developer', 'admin']):
        await query.answer("❌ У вас нет доступа к этой функции!")
        return
    
    text = "👥 Пользователи с ролями:\n\n"
    
    if not user_roles:
        text += "Нет пользователей с ролями."
    else:
        for uid, role in user_roles.items():
            if uid in user_names:
                name = user_names[uid]
            elif uid in user_nicknames:
                name = user_nicknames[uid]
            else:
                name = f"User{uid}"
            
            role_info = USER_ROLES.get(role, {'name': 'Неизвестная роль', 'prefix': ''})
            text += f"👤 {name} (ID: {uid})\n"
            text += f"🔰 Роль: {role_info['name']}\n"
            text += f"🏷️ Префикс: {role_info['prefix']}\n\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='admin_manage_roles')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)

async def check_role_expiry():
    """Проверка срока действия ролей пользователей"""
    global user_roles, user_items
    current_time = int(time.time())
    users_to_update = []
    
    for user_id, items in user_items.items():
        if 'role_expiry' in items:
            for role, expiry_time in list(items['role_expiry'].items()):
                if current_time > expiry_time:
                    # Роль истекла
                    logger.info(f"Роль {role} пользователя {user_id} истекла")
                    
                    # Удаляем информацию о сроке действия
                    items['role_expiry'].pop(role, None)
                    
                    # Если у пользователя эта роль активна, удаляем её
                    if user_id in user_roles and user_roles[user_id] == role:
                        user_roles.pop(user_id, None)
                        users_to_update.append(user_id)
    
    # Сохраняем изменения, если были обновления
    if users_to_update:
        save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
        
        # Отправляем уведомления пользователям
        for user_id in users_to_update:
            try:
                await application.bot.send_message(
                    chat_id=int(user_id),
                    text="⚠️ Срок действия вашей роли истек. Вы можете приобрести новую роль в магазине."
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {str(e)}")

# Добавляем проверку срока действия ролей в основной цикл
async def check_roles_periodically(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая проверка срока действия ролей"""
    await check_role_expiry()

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки админ-панели"""
    # Извлекаем объект Update из CallbackQuery
    query = update.callback_query
    
    # Вместо создания нового объекта Update, просто используем данные из query
    user_id = str(query.from_user.id)
    
    # Проверяем, имеет ли пользователь доступ к админ-панели
    has_access = False
    
    # Developer имеет полный доступ
    if user_id == ADMIN_ID:
        has_access = True
        user_roles[user_id] = 'developer'  # Устанавливаем роль developer для админа
    # Проверяем роль пользователя
    elif user_id in user_roles:
        role = user_roles[user_id]
        if role in ['developer', 'admin', 'moderator', 'operator']:
            # Проверяем, не истек ли срок действия роли
            if role != 'developer':  # Developer не имеет срока действия
                if user_id in user_items and 'role_expiry' in user_items[user_id]:
                    if role in user_items[user_id]['role_expiry']:
                        expiry_time = user_items[user_id]['role_expiry'][role]
                        if int(time.time()) > expiry_time:
                            # Роль истекла, удаляем её
                            user_roles.pop(user_id, None)
                            user_items[user_id]['role_expiry'].pop(role, None)
                            save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames, user_roles)
                            await update.message.reply_text("❌ Срок действия вашей роли истек!")
                            return
                        else:
                            has_access = True
                    else:
                        has_access = True
                else:
                    has_access = True
            else:
                has_access = True
    
    if not has_access:
        await query.answer("❌ У вас нет доступа к панели администратора!")
        return
    
    # Определяем доступные функции в зависимости от роли
    role = user_roles.get(user_id, 'user')
    
    keyboard = []
    
    # Общие функции для всех ролей с доступом
    if role in ['developer', 'admin', 'moderator', 'operator']:
        keyboard.append([InlineKeyboardButton("📨 Массовая рассылка", callback_data='admin_broadcast')])
    
    # Функции для operator и выше
    if role in ['developer', 'admin', 'operator']:
        keyboard.append([InlineKeyboardButton("📊 Статистика бота", callback_data='admin_stats')])
    
    # Функции только для admin и developer
    if role in ['developer', 'admin']:
        keyboard.append([InlineKeyboardButton("👥 Список пользователей", callback_data='admin_users_list')])
        keyboard.append([InlineKeyboardButton("💰 Изменить баланс", callback_data='admin_modify_balance')])
        keyboard.append([InlineKeyboardButton("🎁 Управление предметами", callback_data='admin_manage_items')])
        keyboard.append([InlineKeyboardButton("💲 Управление ценами", callback_data='admin_manage_prices')])
        keyboard.append([InlineKeyboardButton("👑 Управление ролями", callback_data='admin_manage_roles')])
    
    # Кнопка возврата
    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data='back_to_main')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    role_info = USER_ROLES[role]
    await query.edit_message_text(
        f"🔐 Панель администратора\n\n"
        f"👤 Ваша роль: {role_info['name']}\n"
        f"📝 Описание: {role_info['description']}\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup
    )
    
    # Отвечаем на callback_query, чтобы убрать индикатор загрузки
    await query.answer()

if __name__ == "__main__":
    # Проверяем, не запущен ли уже бот
    if check_running():
        logger.error("Бот уже запущен! Завершение работы...")
        sys.exit(1)
    
    # Создаем файл блокировки
    create_lock()
    
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Получен сигнал завершения работы...")
    finally:
        # Удаляем файл блокировки при завершении
        remove_lock()
        logger.info("Бот остановлен")
        logger.info("Бот остановлен")