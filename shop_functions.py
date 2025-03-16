import logging
import json
import os
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import ContextTypes
import time

logger = logging.getLogger(__name__)

# Функции магазина
async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE, SHOP_ITEMS):
    """Обработчик команды /shop"""
    keyboard = []
    
    # Группируем товары по категориям
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
    
    await update.message.reply_text(
        "🏪 Добро пожаловать в магазин!\n"
        "Выберите категорию товаров:",
        reply_markup=reply_markup
    )

async def show_shop_category(query: CallbackQuery, category: str, SHOP_ITEMS):
    """Показать товары в выбранной категории"""
    categories = {
        'boosters': '🎯 Бустеры',
        'game': '🎮 Игровые возможности',
        'football': '⚽️ Футбольные привилегии',
        'roles': '🔰 Роли и префиксы'
    }
    
    # Получаем список товаров в каждой категории
    category_items = {}
    for item_id, item in SHOP_ITEMS.items():
        if 'category' in item:
            if item['category'] not in category_items:
                category_items[item['category']] = []
            category_items[item['category']].append(item_id)
    
    if category not in category_items:
        await query.answer("❌ Категория не найдена")
        return
    
    text = f"🏪 {categories.get(category, 'Категория')}:\n\n"
    keyboard = []
    
    for item_id in category_items[category]:
        item = SHOP_ITEMS[item_id]
        text += f"{item['name']} - {item['price']} монет\n"
        text += f"📝 {item['description']}\n"
        if item['duration'] > 1:
            text += f"⏳ Срок действия: {item['duration']} дней\n"
        text += "\n"
        keyboard.append([InlineKeyboardButton(f"Купить {item['name']}", callback_data=f'buy_{item_id}')])
    
    keyboard.append([InlineKeyboardButton("🔙 К категориям", callback_data='shop')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception as e:
        # Игнорируем ошибку "Message is not modified"
        if "Message is not modified" not in str(e):
            logger.error(f"Ошибка при отображении категории магазина: {str(e)}")
            await query.answer("❌ Произошла ошибка при отображении категории")

async def process_purchase(query: CallbackQuery, item_id: str, SHOP_ITEMS, user_currency, user_items, user_statuses, user_nicknames, user_roles, update_user_balance, save_user_data):
    """Обработка покупки предмета"""
    user_id = str(query.from_user.id)
    
    if item_id not in SHOP_ITEMS:
        await query.answer("❌ Товар не найден")
        return
    
    item = SHOP_ITEMS[item_id]
    user_balance = user_currency.get(user_id, 1000)
    
    # Проверка логики покупки ролей
    if item_id.startswith('role_') and 'role' in item:
        current_role = user_roles.get(user_id, 'user')
        target_role = item['role']
        
        # Проверяем, может ли пользователь купить эту роль
        can_buy = False
        
        # Если у пользователя уже есть роль admin, он не может купить другие роли
        if current_role == 'admin':
            await query.answer("❌ У вас уже есть роль Admin, которая выше других ролей!")
            return
        
        # Если у пользователя роль moderator, он может купить только admin
        elif current_role == 'moderator':
            if target_role == 'admin':
                can_buy = True
            elif target_role == 'operator':
                await query.answer("❌ Ваша текущая роль Moderator выше роли Operator!")
                return
            elif target_role == 'moderator':
                await query.answer("❌ У вас уже есть эта роль!")
                return
        
        # Если у пользователя роль operator, он может купить только admin и moderator
        elif current_role == 'operator':
            if target_role in ['admin', 'moderator']:
                can_buy = True
            elif target_role == 'operator':
                await query.answer("❌ У вас уже есть эта роль!")
                return
        
        # Обычный пользователь может купить любую роль
        else:
            can_buy = True
        
        if not can_buy:
            return
    
    if user_balance < item['price']:
        # Отправляем всплывающее уведомление
        await query.answer(f"❌ Недостаточно монет!")
        
        # Отправляем более заметное сообщение с кнопкой возврата в магазин
        try:
            keyboard = [[InlineKeyboardButton("🔙 Вернуться в магазин", callback_data='shop')]]
            await query.edit_message_text(
                f"❌ Недостаточно монет для покупки {item['name']}!\n\n"
                f"💰 Цена товара: {item['price']} монет\n"
                f"💰 Ваш баланс: {user_balance} монет\n\n"
                f"Вам не хватает {item['price'] - user_balance} монет.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            # Если не удалось отредактировать сообщение, игнорируем ошибку
            if "Message is not modified" not in str(e):
                logger.error(f"Ошибка при отображении сообщения о недостатке монет: {str(e)}")
        return
    
    # Списываем стоимость
    await update_user_balance(user_id, -item['price'])
    
    # Добавляем предмет пользователю
    if user_id not in user_items:
        user_items[user_id] = {}
    
    current_time = datetime.now(pytz.UTC)
    
    # Обработка покупки роли
    if item_id.startswith('role_') and 'role' in item:
        role = item['role']
        
        # Напрямую назначаем роль пользователю
        user_roles[user_id] = role
        
        # Добавляем срок действия роли
        if 'role_expiry' not in user_items.get(user_id, {}):
            if user_id not in user_items:
                user_items[user_id] = {}
            user_items[user_id]['role_expiry'] = {}
        
        # Устанавливаем срок действия роли (30 дней)
        user_items[user_id]['role_expiry'][role] = int(time.time()) + (item['duration'] * 24 * 60 * 60)
        
        # Отправляем сообщение о покупке
        await query.message.reply_text(
            f"✅ Вы успешно приобрели роль {item['name']}!\n"
            f"Роль будет действовать {item['duration']} дней."
        )
        
        # Сохраняем изменения
        save_user_data(user_currency, {}, {}, user_items, user_statuses, user_nicknames, user_roles)
        
        # Возвращаемся в магазин
        try:
            keyboard = [[InlineKeyboardButton("🔙 Вернуться в магазин", callback_data='shop')]]
            await query.edit_message_text(
                f"✅ Вы успешно приобрели роль {item['name']}!\n"
                f"Роль будет действовать {item['duration']} дней.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Ошибка при отображении сообщения о покупке роли: {str(e)}")
        
        return
    
    elif item_id == 'custom_nickname':
        # Запрашиваем новый никнейм
        await query.message.reply_text(
            "✏️ Введите ваш новый никнейм (максимум 20 символов):\n\n"
            "❗️ Просто отправьте сообщение с желаемым никнеймом"
        )
        if user_id not in user_items:
            user_items[user_id] = {}
        user_items[user_id]['awaiting_nickname'] = True
        save_user_data(user_currency, {}, {}, user_items, user_statuses, user_nicknames, user_roles)
        return
    
    elif item_id == 'custom_status':
        # Запрашиваем новый статус
        await query.message.reply_text(
            "✏️ Введите ваш новый статус (максимум 50 символов):\n\n"
            "❗️ Просто отправьте сообщение с желаемым статусом"
        )
        if user_id not in user_items:
            user_items[user_id] = {}
        user_items[user_id]['awaiting_status'] = True
        save_user_data(user_currency, {}, {}, user_items, user_statuses, user_nicknames, user_roles)
        return
    
    # Для остальных предметов стандартная логика
    if item['duration'] > 1:
        expiration = current_time + timedelta(days=item['duration'])
        user_items[user_id][item_id] = expiration.isoformat()
    else:
        if item_id not in user_items[user_id]:
            user_items[user_id][item_id] = 0
        user_items[user_id][item_id] += 1
    
    # Сохраняем изменения
    save_user_data(user_currency, {}, {}, user_items, user_statuses, user_nicknames, user_roles)
    
    await query.answer(f"✅ Вы успешно приобрели {item['name']}!")
    
    category = item.get('category', 'boosters')
    try:
        await show_shop_category(query, category, SHOP_ITEMS)
    except Exception as e:
        # Игнорируем ошибку "Message is not modified"
        if "Message is not modified" not in str(e):
            logger.error(f"Ошибка при отображении категории после покупки: {str(e)}")
            # Пытаемся вернуться в магазин
            try:
                keyboard = [[InlineKeyboardButton("🔙 Вернуться в магазин", callback_data='shop')]]
                await query.edit_message_text(
                    f"✅ Вы успешно приобрели {item['name']}!\n\n"
                    "Произошла ошибка при отображении категории.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except:
                pass

def has_active_item(user_id: str, item_id: str, user_items):
    """Проверка наличия активного предмета у пользователя"""
    if user_id not in user_items or item_id not in user_items[user_id]:
        return False
    
    value = user_items[user_id][item_id]
    if isinstance(value, int):
        # Для одноразовых предметов
        return value > 0
    else:
        # Для предметов с длительностью
        try:
            expiration = datetime.fromisoformat(value)
            return datetime.now(pytz.UTC) < expiration
        except (ValueError, TypeError):
            return False

def use_item(user_id: str, item_id: str, user_items, save_user_data, user_currency, user_predictions, user_names, user_statuses, user_nicknames):
    """Использование предмета"""
    if not has_active_item(user_id, item_id, user_items):
        return False
    
    value = user_items[user_id][item_id]
    if isinstance(value, int):
        # Для одноразовых предметов
        user_items[user_id][item_id] -= 1
        if user_items[user_id][item_id] <= 0:
            del user_items[user_id][item_id]
    # Для предметов с длительностью ничего не делаем,
    # они будут автоматически деактивированы по истечении срока
    
    save_user_data(user_currency, user_predictions, user_names, user_items, user_statuses, user_nicknames)
    return True

def save_shop_items(SHOP_ITEMS):
    """Сохранить настройки магазина"""
    try:
        shop_file = os.path.join(os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', ''), 'shop_items.json')
        # Создаем директорию, если она не существует
        os.makedirs(os.path.dirname(shop_file) or '.', exist_ok=True)
        
        with open(shop_file, 'w', encoding='utf-8') as f:
            json.dump(SHOP_ITEMS, f, ensure_ascii=False, indent=4)
        logger.info(f"Настройки магазина успешно сохранены в {shop_file}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении настроек магазина: {str(e)}")

def load_shop_items():
    """Загрузить настройки магазина"""
    try:
        shop_file = os.path.join(os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', ''), 'shop_items.json')
        if os.path.exists(shop_file):
            with open(shop_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"Настройки магазина успешно загружены из {shop_file}")
                return data
        logger.info(f"Файл настроек магазина не найден по пути: {shop_file}")
        return None
    except Exception as e:
        logger.error(f"Ошибка при загрузке настроек магазина: {str(e)}")
        return None 