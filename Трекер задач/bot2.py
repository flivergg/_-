import telebot
import sqlite3
import datetime
import time
import threading
import re
import random 
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')
import io
from datetime import timedelta
import urllib.parse

BOT_TOKEN = ""
bot = telebot.TeleBot(BOT_TOKEN)

class Config:
    STATS_DAYS_BACK = 7
    DAILY_REPORT_HOUR = 9
    REMINDER_RETRY_MINUTES = 10
    MAX_RETRY_COUNT = 3
    ADMIN_IDS = [7638967663]
    # ЗАМЕНИТЕ НА ВАШ IP СЕРВЕРА!
    SERVER_IP = "89.223.66.145"
    WEB_APP_PORT = "5000"

USER_REMINDER_DATA = {}

MOTIVATION_QUOTES = [
    "💧 Время освежиться! Вода — это красота всей природы и источник твоей энергии.",
    "🚀 Пей воду! Она помогает тебе быть на 100% продуктивным и сосредоточенным.",
    "✨ Твоя кожа будет сиять! Не забывай: ты состоишь из воды на 60%.",
    "🧠 Голова работает лучше с H₂O! Сделай глоток для ясности ума.",
    "💪 Вода — твой лучший друг в борьбе с усталостью. Зарядись!",
    "🌱 Каждая капля — инвестиция в твое здоровье. Пей и процветай!",
]

class DBManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.init_db_structure()

    def _execute(self, sql, params=(), commit=False, fetchone=False, fetchall=False):
        conn = None
        result = None
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute(sql, params)
            
            if commit:
                conn.commit()
            if fetchone:
                result = c.fetchone()
            if fetchall:
                result = c.fetchall()
        except sqlite3.Error as e:
            print(f"Database error in _execute: {e}")
            return None
        finally:
            if conn:
                conn.close()
        return result

    def execute(self, sql, params=(), commit=False, fetchone=False, fetchall=False):
        with self.lock:
            return self._execute(sql, params, commit, fetchone, fetchall)

    def init_db_structure(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        joined_at TEXT
                    )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS reminders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        text TEXT,
                        time TEXT,
                        repeat TEXT,
                        created_at TEXT,
                        last_sent TEXT,
                        next_send TEXT,
                        is_habit BOOLEAN DEFAULT 0,
                        habit_streak INTEGER DEFAULT 0,
                        retry_count INTEGER DEFAULT 0,
                        last_reminder_sent TEXT,
                        FOREIGN KEY (user_id) REFERENCES users (user_id)
                    )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS habit_completions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        reminder_id INTEGER,
                        completion_date TEXT,
                        completion_time TEXT,
                        created_at TEXT,
                        FOREIGN KEY (user_id) REFERENCES users (user_id),
                        FOREIGN KEY (reminder_id) REFERENCES reminders (id)
                    )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS user_stats (
                        user_id INTEGER PRIMARY KEY,
                        water_reminders_completed INTEGER DEFAULT 0,
                        total_habits_completed INTEGER DEFAULT 0,
                        last_daily_report TEXT,
                        FOREIGN KEY (user_id) REFERENCES users (user_id)
                    )''')
        
        conn.commit()
        conn.close()
        print("✅ База данных готова")

DB_MANAGER = DBManager('bot_users.db')

def is_admin(user_id):
    return user_id in Config.ADMIN_IDS

def get_bot_stats():
    total_users = DB_MANAGER.execute("SELECT COUNT(*) FROM users", fetchone=True)[0]
    total_reminders = DB_MANAGER.execute("SELECT COUNT(*) FROM reminders", fetchone=True)[0]
    total_habits = DB_MANAGER.execute("SELECT COUNT(*) FROM reminders WHERE is_habit = 1", fetchone=True)[0]
    active_today = DB_MANAGER.execute('''SELECT COUNT(DISTINCT user_id) FROM habit_completions 
                                       WHERE completion_date = ?''', 
                                    (datetime.datetime.now().date().isoformat(),), fetchone=True)[0]
    return {
        'total_users': total_users,
        'total_reminders': total_reminders,
        'total_habits': total_habits,
        'active_today': active_today
    }

def get_all_users():
    return DB_MANAGER.execute("SELECT user_id, username, joined_at FROM users ORDER BY joined_at DESC", fetchall=True)

def broadcast_message(user_ids, message):
    success = 0
    failed = 0
    for user_id in user_ids:
        try:
            bot.send_message(user_id, message)
            success += 1
        except Exception as e:
            print(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            failed += 1
        time.sleep(0.1)
    return success, failed

def add_user(user_id, username):
    DB_MANAGER.execute("INSERT OR IGNORE INTO users (user_id, username, joined_at) VALUES (?, ?, ?)", 
              (user_id, username, datetime.datetime.now().isoformat()), commit=True)
    DB_MANAGER.execute("INSERT OR IGNORE INTO user_stats (user_id) VALUES (?)", 
              (user_id,), commit=True)

def add_reminder(user_id, text, time_str, repeat, is_habit=False):
    is_duplicate = DB_MANAGER.execute("SELECT id FROM reminders WHERE user_id = ? AND text = ? AND time = ? AND repeat = ?", 
              (user_id, text, time_str, repeat), fetchone=True)
    if is_duplicate:
        return

    current_time = datetime.datetime.now()
    
    try:
        reminder_time = datetime.datetime.strptime(time_str, '%H:%M').replace(
            year=current_time.year, month=current_time.month, day=current_time.day, second=0, microsecond=0)
    except ValueError:
        raise ValueError("Неверный формат времени.")

    if reminder_time < current_time:
        reminder_time += timedelta(days=1)
        
    next_send = reminder_time

    DB_MANAGER.execute("INSERT INTO reminders (user_id, text, time, repeat, created_at, next_send, is_habit) VALUES (?, ?, ?, ?, ?, ?, ?)", 
              (user_id, text, time_str, repeat, current_time.isoformat(), next_send.isoformat(), is_habit), commit=True)

def delete_reminder(user_id, reminder_id):
    DB_MANAGER.execute("DELETE FROM reminders WHERE user_id = ? AND id = ?", (user_id, reminder_id), commit=True)
    DB_MANAGER.execute("DELETE FROM habit_completions WHERE user_id = ? AND reminder_id = ?", (user_id, reminder_id), commit=True)

def get_user_reminders(user_id):
    return DB_MANAGER.execute("SELECT id, text, time, repeat, is_habit, habit_streak FROM reminders WHERE user_id = ?", (user_id,), fetchall=True)

def get_habits(user_id):
    return DB_MANAGER.execute("SELECT id, text, time, repeat, habit_streak FROM reminders WHERE user_id = ? AND is_habit = 1", (user_id,), fetchall=True)

def update_last_sent(reminder_id, next_send):
    next_send_iso = next_send if next_send else None 
    DB_MANAGER.execute("UPDATE reminders SET last_sent = ?, next_send = ? WHERE id = ?", 
              (datetime.datetime.now().isoformat(), next_send_iso, reminder_id), commit=True)

def get_due_reminders():
    now = datetime.datetime.now().isoformat()
    return DB_MANAGER.execute(
        "SELECT id, user_id, text, time, repeat, last_sent, next_send, is_habit, retry_count, last_reminder_sent FROM reminders WHERE next_send <= ? AND next_send IS NOT NULL", 
        (now,), fetchall=True
    )

def update_reminder_retry(reminder_id, retry_count):
    DB_MANAGER.execute("UPDATE reminders SET retry_count = ?, last_reminder_sent = ? WHERE id = ?", 
              (retry_count, datetime.datetime.now().isoformat(), reminder_id), commit=True)

def postpone_reminder(reminder_id, minutes=None, days=None):
    current_time = datetime.datetime.now()
    
    if minutes:
        new_time = current_time + timedelta(minutes=minutes)
    elif days:
        new_time = current_time + timedelta(days=days)
    else:
        return False
    
    DB_MANAGER.execute("UPDATE reminders SET next_send = ?, retry_count = 0 WHERE id = ?", 
              (new_time.isoformat(), reminder_id), commit=True)
    return True

def mark_habit_completed(user_id, reminder_id):
    today = datetime.datetime.now().date().isoformat()
    current_time = datetime.datetime.now().time().strftime('%H:%M')
    
    is_completed = DB_MANAGER.execute("SELECT id FROM habit_completions WHERE user_id = ? AND reminder_id = ? AND completion_date = ?", 
              (user_id, reminder_id, today), fetchone=True)
    if is_completed:
        return False
    
    DB_MANAGER.execute("INSERT INTO habit_completions (user_id, reminder_id, completion_date, completion_time, created_at) VALUES (?, ?, ?, ?, ?)",
              (user_id, reminder_id, today, current_time, datetime.datetime.now().isoformat()), commit=True)
    
    habit_text = DB_MANAGER.execute("SELECT text FROM reminders WHERE id = ?", (reminder_id,), fetchone=True)
    if habit_text:
        habit_text = habit_text[0]
        if "пить воду" in habit_text.lower() or "стакан воды" in habit_text.lower():
            DB_MANAGER.execute("UPDATE user_stats SET water_reminders_completed = water_reminders_completed + 1 WHERE user_id = ?", (user_id,), commit=True)
    
    DB_MANAGER.execute("UPDATE user_stats SET total_habits_completed = total_habits_completed + 1 WHERE user_id = ?", (user_id,), commit=True)
    
    current_streak = DB_MANAGER.execute("SELECT habit_streak FROM reminders WHERE id = ?", (reminder_id,), fetchone=True)
    current_streak = current_streak[0] if current_streak else 0
    
    yesterday = (datetime.datetime.now() - timedelta(days=1)).date().isoformat()
    yesterday_completed = DB_MANAGER.execute("SELECT id FROM habit_completions WHERE user_id = ? AND reminder_id = ? AND completion_date = ?",
              (user_id, reminder_id, yesterday), fetchone=True)
    
    new_streak = current_streak + 1 if yesterday_completed else 1
    
    DB_MANAGER.execute("UPDATE reminders SET habit_streak = ? WHERE id = ?", (new_streak, reminder_id), commit=True)
    
    return True

def get_habit_stats(user_id, reminder_id, days=Config.STATS_DAYS_BACK):
    end_date = datetime.datetime.now().date()
    start_date = end_date - timedelta(days=days-1)
    
    completions = DB_MANAGER.execute('''SELECT completion_date FROM habit_completions 
                 WHERE user_id = ? AND reminder_id = ? AND completion_date BETWEEN ? AND ?
                 ORDER BY completion_date''',
              (user_id, reminder_id, start_date.isoformat(), end_date.isoformat()), fetchall=True)
    
    completions = [row[0] for row in completions]
    
    habit_info = DB_MANAGER.execute("SELECT text, habit_streak FROM reminders WHERE id = ?", (reminder_id,), fetchone=True)
    
    return {
        'completions': completions,
        'habit_name': habit_info[0] if habit_info else '',
        'current_streak': habit_info[1] if habit_info else 0,
        'period': f"{start_date.strftime('%d.%m')} - {end_date.strftime('%d.%m')}"
    }

# === СИСТЕМА НАПОМИНАНИЙ ===
def check_reminders():
    while True:
        try:
            current_datetime = datetime.datetime.now()
            reminders = get_due_reminders()
            
            for reminder in reminders:
                reminder_id, user_id, text, time_str, repeat, last_sent, next_send, is_habit, retry_count, last_reminder_sent = reminder
                
                try:
                    if not should_send_today(current_datetime.date(), repeat):
                        new_next_send = calculate_next_send(current_datetime, repeat)
                        update_last_sent(reminder_id, new_next_send.isoformat() if new_next_send else None)
                        continue
                    
                    if retry_count == 0:
                        if is_habit:
                            is_water_reminder = "пить воду" in text.lower() or "стакан воды" in text.lower()
                            
                            if is_water_reminder:
                                motivation = random.choice(MOTIVATION_QUOTES)
                                reminder_message = f"💧 {motivation}\n\n⏰ Напоминание: {text} ({time_str})"
                            else:
                                reminder_message = f"🌱 Напоминание о привычке: {text} ({time_str}) [Повтор: {repeat}]"
                            
                            keyboard = telebot.types.InlineKeyboardMarkup()
                            done_btn = telebot.types.InlineKeyboardButton("✅ Выполнено", callback_data=f"habit_done_{reminder_id}")
                            postpone_btn = telebot.types.InlineKeyboardButton("⏰ Напомнить позже", callback_data=f"postpone_{reminder_id}")
                            stats_btn = telebot.types.InlineKeyboardButton("📊 Статистика", callback_data=f"habit_stats_{reminder_id}")
                            keyboard.add(done_btn, postpone_btn, stats_btn)
                            
                            try:
                                bot.send_message(user_id, reminder_message, reply_markup=keyboard)
                            except Exception as e:
                                print(f"❌ Ошибка отправки привычки {reminder_id}: {e}")
                        else:
                            send_reminder_with_button(user_id, f"⏰ {text} ({time_str}) [Повтор: {repeat}]", reminder_id)
                    
                    elif retry_count > 0 and retry_count <= Config.MAX_RETRY_COUNT:
                        if last_reminder_sent:
                            last_sent_time = datetime.datetime.fromisoformat(last_reminder_sent)
                            retry_time = last_sent_time + timedelta(minutes=Config.REMINDER_RETRY_MINUTES)
                            
                            if current_datetime >= retry_time:
                                new_retry_count = retry_count + 1
                                if new_retry_count <= Config.MAX_RETRY_COUNT:
                                    if is_habit:
                                        reminder_message = f"🌱 Напоминание о привычке: {text} ({time_str})"
                                        keyboard = telebot.types.InlineKeyboardMarkup()
                                        done_btn = telebot.types.InlineKeyboardButton("✅ Выполнено", callback_data=f"habit_done_{reminder_id}")
                                        postpone_btn = telebot.types.InlineKeyboardButton("⏰ Напомнить позже", callback_data=f"postpone_{reminder_id}")
                                        stats_btn = telebot.types.InlineKeyboardButton("📊 Статистика", callback_data=f"habit_stats_{reminder_id}")
                                        keyboard.add(done_btn, postpone_btn, stats_btn)
                                        
                                        try:
                                            bot.send_message(user_id, reminder_message, reply_markup=keyboard)
                                        except Exception as e:
                                            print(f"❌ Ошибка отправки повторной привычки {reminder_id}: {e}")
                                    else:
                                        send_reminder_with_button(user_id, f"⏰ {text} ({time_str}) [Повтор: {repeat}]", reminder_id, is_retry=True)
                                    
                                    update_reminder_retry(reminder_id, new_retry_count)
                                else:
                                    delete_reminder(user_id, reminder_id)
                                    try:
                                        bot.send_message(user_id, f"🔕 Напоминание автоматически удалено:\n{text}")
                                    except:
                                        pass
                    
                    is_one_time = (repeat.lower() == '1 раз')

                    if is_one_time:
                        new_next_send = None 
                    else:
                        new_next_send = calculate_next_send(current_datetime, repeat)
                        
                    update_last_sent(reminder_id, new_next_send.isoformat() if new_next_send else None)
                    
                    if not is_habit and retry_count == 0:
                        update_reminder_retry(reminder_id, 1)
                    
                except telebot.apihelper.ApiTelegramException as e:
                    if 'bot was blocked by the user' in str(e):
                        print(f"🚫 Пользователь {user_id} заблокировал бота. Удаляем его напоминания.")
                        delete_reminder(user_id, reminder_id)
                    else:
                        print(f"❌ Ошибка API при отправке напоминания {reminder_id}: {e}")
                except Exception as e:
                    print(f"❌ Неизвестная ошибка при отправке напоминания {reminder_id}: {e}")

        except Exception as e:
            print(f"❌ Критическая ошибка в check_reminders: {e}")
        
        time.sleep(10)

def send_reminder_with_button(user_id, reminder_text, reminder_id, is_retry=False):
    retry_text = " 🔄 Повторное напоминание" if is_retry else ""
    
    keyboard = telebot.types.InlineKeyboardMarkup()
    done_btn = telebot.types.InlineKeyboardButton("✅ ВЫПОЛНЕНО", callback_data=f"reminder_done_{reminder_id}")
    postpone_btn = telebot.types.InlineKeyboardButton("⏰ Напомнить позже", callback_data=f"postpone_{reminder_id}")
    keyboard.add(done_btn, postpone_btn)
    
    try:
        bot.send_message(
            user_id, 
            f"🔔 Напоминание{retry_text}:\n{reminder_text}", 
            reply_markup=keyboard
        )
        return True
    except Exception as e:
        print(f"Ошибка отправки напоминания пользователю {user_id}: {e}")
        return False

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def is_weekday(date):
    return date.weekday() < 5

def is_weekend(date):
    return date.weekday() >= 5

def is_wednesday_or_friday(date):
    return date.weekday() in [2, 4]

def calculate_next_send(current_send, repeat):
    if repeat == 'ежедневно':
        return current_send + timedelta(days=1)
    elif repeat == 'раз в 2 дня':
        return current_send + timedelta(days=2)
    elif repeat == 'раз в неделю':
        return current_send + timedelta(days=7)
    elif repeat == 'раз в 2 недели':
        return current_send + timedelta(days=14)
    elif repeat == 'раз в месяц':
        return current_send + timedelta(days=30)
    elif repeat == 'по рабочим дням (Пн-Пт)':
        next_day = current_send + timedelta(days=1)
        while is_weekend(next_day):
            next_day += timedelta(days=1)
        return next_day
    elif repeat == 'по выходным':
        next_day = current_send + timedelta(days=1)
        while is_weekday(next_day):
            next_day += timedelta(days=1)
        return next_day
    elif repeat == 'каждую среду и пятницу':
        next_day = current_send + timedelta(days=1)
        while not is_wednesday_or_friday(next_day):
            next_day += timedelta(days=1)
        return next_day
    elif repeat == '1 раз':
        return None
    else:
        return current_send + timedelta(days=1)

def should_send_today(reminder_date, repeat):
    today = datetime.datetime.now().date()
    
    if repeat == 'ежедневно':
        return True
    elif repeat == 'по рабочим дням (Пн-Пт)':
        return is_weekday(reminder_date)
    elif repeat == 'по выходным':
        return is_weekend(reminder_date)
    elif repeat == 'каждую среду и пятницу':
        return is_wednesday_or_friday(reminder_date)
    elif repeat == 'раз в 2 недели':
        days_diff = (reminder_date - today).days
        return days_diff % 14 == 0
    else:
        return True

def is_valid_time(time_str):
    try:
        if re.fullmatch(r'\d{2}:\d{2}', time_str):
            datetime.datetime.strptime(time_str, '%H:%M')
            return True
        return False
    except ValueError:
        return False

def parse_task_and_time(text):
    text = text.strip()
    match = re.search(r'(\d{2}:\d{2})\s*$', text)
    
    if not match:
        return None, None

    time_str = match.group(1)
    task = text[:match.start()].strip()
    
    if not is_valid_time(time_str) or not task: 
        return None, None
    
    return task, time_str

# === КЛАВИАТУРЫ ===
def main_keyboard():
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("💧 Напоминания о воде", "⏰ Обычные напоминания")
    kb.row("🌱 Привычки", "📊 Статистика")
    kb.row("📋 Мои напоминания", "🗑 Удалить напоминание")
    kb.row("📱 Открыть приложение", "ℹ️ Помощь")
    return kb

def admin_keyboard():
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📊 Статистика бота", "👥 Список пользователей")
    kb.row("📢 Сделать рассылку", "🏠 Главное меню")
    return kb

def mini_app_keyboard():
    """Клавиатура с кнопкой для Mini App"""
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    web_app_url = f"http://{Config.SERVER_IP}:{Config.WEB_APP_PORT}/webapp"
    web_app_btn = telebot.types.KeyboardButton(
        "📱 Открыть приложение", 
        web_app=telebot.types.WebAppInfo(url=web_app_url)
    )
    kb.add(web_app_btn)
    kb.add("🏠 Главное меню")
    return kb

def repeat_keyboard():
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Ежедневно", "По рабочим дням (Пн-Пт)")
    kb.row("По выходным", "Каждую среду и пятницу")
    kb.row("Раз в неделю", "Раз в 2 недели")
    kb.row("Раз в месяц", "1 раз")
    kb.row("🏠 Главное меню")
    return kb

def back_keyboard():
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🏠 Главное меню")
    return kb

def get_reminders_list_text(user_id):
    reminders = get_user_reminders(user_id)
    if not reminders: return "📭 У вас пока нет напоминаний."
    msg = "📋 Ваши напоминания:\n\n"
    for r in reminders:
        habit_icon = "🌱" if r[4] else "⏰"
        streak_text = f" 🔥{r[5]}" if r[4] and r[5] > 0 else ""
        msg += f"• {habit_icon} ID: {r[0]} | {r[1]} ⏰ {r[2]} 🔁 {r[3]}{streak_text}\n"
    return msg

def reminders_keyboard(reminders):
    kb = telebot.types.InlineKeyboardMarkup()
    for r in reminders:
        habit_icon = "🌱" if r[4] else "⏰"
        kb.add(telebot.types.InlineKeyboardButton(
            f"{habit_icon} {r[1]} ⏰ {r[2]}", 
            callback_data=f"delete_{r[0]}"))
    return kb

def habits_stats_keyboard(habits):
    kb = telebot.types.InlineKeyboardMarkup()
    for habit in habits:
        kb.add(telebot.types.InlineKeyboardButton(
            f"📊 {habit[1]} (стрик: {habit[4]})", 
            callback_data=f"stats_{habit[0]}"))
    
    if not habits:
        kb.add(telebot.types.InlineKeyboardButton("📝 Создать первую привычку", callback_data="create_habit"))
    else:
        kb.add(telebot.types.InlineKeyboardButton("🔄 Обновить список", callback_data="refresh_stats"))
    
    kb.add(telebot.types.InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu"))
    return kb

def postpone_keyboard(reminder_id):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("⏰ Через 15 минут", callback_data=f"postpone_15_{reminder_id}"))
    kb.add(telebot.types.InlineKeyboardButton("⏰ Через 1 час", callback_data=f"postpone_60_{reminder_id}"))
    kb.add(telebot.types.InlineKeyboardButton("⏰ Завтра в это же время", callback_data=f"postpone_tomorrow_{reminder_id}"))
    kb.add(telebot.types.InlineKeyboardButton("❌ Отмена", callback_data=f"postpone_cancel_{reminder_id}"))
    return kb

# === ГРАФИКИ ===
def create_habit_chart(stats):
    days = Config.STATS_DAYS_BACK
    dates = [(datetime.datetime.now() - timedelta(days=i)).date() for i in range(days-1, -1, -1)]
    completion_dates = [datetime.datetime.fromisoformat(date).date() for date in stats['completions']]
    
    completed = [1 if date in completion_dates else 0 for x, date in enumerate(dates)]
    
    plt.figure(figsize=(10, 4))
    plt.bar([date.strftime('%d.%m') for date in dates], completed, color=['#4CAF50' if x else '#f44336' for x in completed])
    plt.title(f"Выполнение привычки: {stats['habit_name']}\n({stats['period']})")
    plt.ylabel('Выполнено')
    plt.xlabel('Дни')
    plt.ylim(0, 1)
    plt.grid(axis='y', alpha=0.3)
    
    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', bbox_inches='tight')
    buffer.seek(0)
    plt.close()
    
    return buffer

# === ОБРАБОТКА КОМАНД ===
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or "пользователь"
    first_name = message.from_user.first_name or "друг"
    
    existing_user = DB_MANAGER.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    
    add_user(user_id, username)
    
    if not existing_user:
        welcome_text = f"Приветствую, {first_name}! 👋\n\n"
    else:
        welcome_text = f"С возвращением, {first_name}! 👋\n\n"
    
    welcome_text += (
        "Добро пожаловать в Loopmatic - вашу систему управления напоминаниями!\n\n"
        "📱 Откройте Mini App для удобного управления\n"
        "⏰ Создавайте напоминания и привычки\n"
        "📊 Отслеживайте статистику выполнения"
    )
    
    bot.send_message(message.chat.id, welcome_text, reply_markup=main_keyboard())

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        bot.send_message(message.chat.id, "❌ У вас нет доступа к админ-панели.")
        return
    
    bot.send_message(message.chat.id, 
                    "👨‍💻 АДМИН-ПАНЕЛЬ\n\n"
                    "Выберите действие:", 
                    reply_markup=admin_keyboard())

@bot.message_handler(func=lambda msg: True)
def handle_message(message):
    text = message.text
    user_id = message.from_user.id

    if text == "🏠 Главное меню":
        bot.send_message(message.chat.id, "🏠 Главное меню:", reply_markup=main_keyboard())
        return
        
    elif text == "📱 Открыть приложение":
        bot.send_message(message.chat.id, 
                        "📱 **Loopmatic Mini App**\n\n"
                        "Нажмите кнопку ниже чтобы открыть приложение внутри Telegram:",
                        reply_markup=mini_app_keyboard())

    elif text == "💧 Напоминания о воде":
        bot.send_message(message.chat.id, 
                        "💧 Введите время для напоминания о воде в формате HH:MM\n\nНапример: 09:00", 
                        reply_markup=main_keyboard())

    elif text == "⏰ Обычные напоминания":
        bot.send_message(message.chat.id, 
                        "⏰ Введите напоминание в формате:\n\nТекст напоминания и Время ЧЧ:ММ\n\nНапример: Принять витамины 09:00",
                        reply_markup=main_keyboard())
        bot.register_next_step_handler(message, lambda msg: handle_task_and_time(msg, False))

    elif text == "🌱 Привычки":
        bot.send_message(message.chat.id, 
                        "🌱 Введите привычку в формате:\nНазвание привычки и Время ЧЧ:ММ\n\nНапример: Читать 20 минут 21:00",
                        reply_markup=main_keyboard())
        bot.register_next_step_handler(message, lambda msg: handle_task_and_time(msg, True))

    elif text == "📋 Мои напоминания":
        bot.send_message(message.chat.id, get_reminders_list_text(user_id), reply_markup=main_keyboard())

    elif text == "📊 Статистика":
        habits = get_habits(user_id)
        if not habits:
            bot.send_message(message.chat.id, 
                           "🌱 У вас пока нет привычек для статистики.", 
                           reply_markup=main_keyboard())
        else:
            bot.send_message(message.chat.id, 
                           "📊 Выберите привычку для просмотра статистики:",
                           reply_markup=habits_stats_keyboard(habits))

    elif text == "🗑 Удалить напоминание":
        reminders = get_user_reminders(user_id)
        if not reminders:
            bot.send_message(message.chat.id, "📭 У вас нет напоминаний для удаления.", reply_markup=main_keyboard())
        else:
            bot.send_message(message.chat.id, "Выберите напоминание для удаления:", reply_markup=reminders_keyboard(reminders))

    elif text == "ℹ️ Помощь":
        help_text = """ℹ️ **Помощь по Loopmatic**

**Основные команды:**
• /start - перезапустить бота
• /admin - админ-панель (только для администраторов)
• 📱 Открыть приложение - Mini App внутри Telegram
• 💧 Напоминания о воде - установить водные напоминания
• ⏰ Обычные напоминания - создать разовые напоминания
• 🌱 Привычки - создать повторяющиеся привычки
• 📋 Мои напоминания - список всех напоминаний
• 🗑 Удалить напоминание - удалить существующие
• 📊 Статистика - просмотр прогресса привычек

**Mini App:**
- Удобный интерфейс внутри Telegram
- Быстрый просмотр и редактирование
- Визуальная статистика"""

        bot.send_message(message.chat.id, help_text, reply_markup=main_keyboard())

    # === АДМИН-КОМАНДЫ ===
    elif text == "📊 Статистика бота" and is_admin(user_id):
        stats = get_bot_stats()
        msg = f"""📊 СТАТИСТИКА БОТА:

👥 Всего пользователей: {stats['total_users']}
⏰ Всего напоминаний: {stats['total_reminders']}
🌱 Всего привычек: {stats['total_habits']}
✅ Активных сегодня: {stats['active_today']}"""
        bot.send_message(user_id, msg, reply_markup=admin_keyboard())

    elif text == "👥 Список пользователей" and is_admin(user_id):
        users = get_all_users()
        if not users:
            bot.send_message(user_id, "📭 Нет пользователей в базе данных.", reply_markup=admin_keyboard())
            return
        
        msg = "👥 ПОСЛЕДНИЕ 10 ПОЛЬЗОВАТЕЛЕЙ:\n\n"
        for i, (user_id, username, joined_at) in enumerate(users[:10], 1):
            date = datetime.datetime.fromisoformat(joined_at).strftime('%d.%m.%Y')
            msg += f"{i}. ID: {user_id}\n   👤: @{username or 'нет'}\n   📅: {date}\n\n"
        
        bot.send_message(user_id, msg, reply_markup=admin_keyboard())

    elif text == "📢 Сделать рассылку" and is_admin(user_id):
        bot.send_message(user_id, 
                        "📢 ОТПРАВКА РАССЫЛКИ\n\n"
                        "Введите сообщение для рассылки всем пользователям:",
                        reply_markup=back_keyboard())
        bot.register_next_step_handler(message, handle_broadcast_message)

    elif is_valid_time(text):
        add_reminder(user_id, "Пить воду", text, "ежедневно", is_habit=True)
        bot.send_message(message.chat.id, 
                        f"✅ Напоминание о воде установлено на {text}!", 
                        reply_markup=main_keyboard())

    else:
        if user_id not in USER_REMINDER_DATA:
            task, time_str = parse_task_and_time(text)
            if task and time_str:
                 handle_task_and_time(message, False)
                 return
            
        bot.send_message(message.chat.id, 
                        "⚠️ Неизвестная команда. Пожалуйста, выберите действие из меню.", 
                        reply_markup=main_keyboard())

def handle_broadcast_message(message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        return
        
    if message.text == "🏠 Главное меню":
        bot.send_message(user_id, "🏠 Возвращаемся в админ-панель:", reply_markup=admin_keyboard())
        return
        
    broadcast_text = message.text
    users = DB_MANAGER.execute("SELECT user_id FROM users", fetchall=True)
    user_ids = [user[0] for user in users]
    
    bot.send_message(user_id, f"📤 Начинаю рассылку для {len(user_ids)} пользователей...")
    
    success, failed = broadcast_message(user_ids, broadcast_text)
    
    bot.send_message(user_id,
                    f"✅ РАССЫЛКА ЗАВЕРШЕНА:\n\n"
                    f"✅ Успешно: {success}\n"
                    f"❌ Не удалось: {failed}\n"
                    f"📊 Всего: {len(user_ids)}",
                    reply_markup=admin_keyboard())

def handle_task_and_time(message, is_habit=False):
    user_id = message.from_user.id
    text = message.text

    if text in ["🏠 Главное меню"]:
        bot.send_message(user_id, "🏠 Возвращаемся в главное меню:", reply_markup=main_keyboard())
        return

    task, time_str = parse_task_and_time(text)

    if task and time_str:
        USER_REMINDER_DATA[user_id] = {'task': task, 'time_str': time_str, 'is_habit': is_habit}
        
        habit_text = "привычки" if is_habit else "напоминания"
        bot.send_message(user_id, 
                         f"📝 Задача: {task}\n🕒 Время: {time_str}\n\nТеперь выберите частоту повтора для {habit_text}:", 
                         reply_markup=repeat_keyboard())
        
        bot.register_next_step_handler(message, handle_repeat_choice)
    else:
        msg = "⚠️ Неверный формат. Пожалуйста, введите: Текст напоминания и Время ЧЧ:ММ.\n\nНапример: Читать 20 минут 21:00"
        bot.send_message(user_id, msg, reply_markup=main_keyboard())
        bot.register_next_step_handler(message, lambda msg: handle_task_and_time(msg, is_habit))

def handle_repeat_choice(message):
    user_id = message.from_user.id
    repeat_choice = message.text.lower()
    
    if repeat_choice in ["🏠 главное меню"]:
        bot.send_message(user_id, "🏠 Возвращаемся в главное меню:", reply_markup=main_keyboard())
        if user_id in USER_REMINDER_DATA:
            del USER_REMINDER_DATA[user_id]
        return

    valid_repeats = {
        'ежедневно': 'ежедневно',
        'по рабочим дням (пн-пт)': 'по рабочим дням (Пн-Пт)',
        'по выходным': 'по выходным', 
        'каждую среду и пятницу': 'каждую среду и пятницу',
        'раз в неделю': 'раз в неделю',
        'раз в 2 недели': 'раз в 2 недели',
        'раз в месяц': 'раз в месяц',
        '1 раз': '1 раз'
    }
    
    repeat = valid_repeats.get(repeat_choice)
    
    if not repeat:
        bot.send_message(user_id, "⚠️ Неверный выбор. Пожалуйста, выберите опцию из кнопок.", reply_markup=repeat_keyboard())
        bot.register_next_step_handler(message, handle_repeat_choice)
        return

    if user_id in USER_REMINDER_DATA:
        task = USER_REMINDER_DATA[user_id]['task']
        time_str = USER_REMINDER_DATA[user_id]['time_str']
        is_habit = USER_REMINDER_DATA[user_id]['is_habit']
        
        try:
            add_reminder(user_id, task, time_str, repeat, is_habit)
            habit_text = "🌱 Привычка" if is_habit else "⏰ Напоминание"
            bot.send_message(user_id, f"✅ {habit_text} сохранено:\n📝 {task}\n🕒 {time_str}\n🔁 {repeat}", reply_markup=main_keyboard())
        except Exception as e:
            bot.send_message(user_id, f"❌ Ошибка при сохранении: {e}", reply_markup=main_keyboard())
            
        del USER_REMINDER_DATA[user_id]
    else:
        bot.send_message(user_id, "❌ Произошла ошибка: данные напоминания потеряны. Начните сначала.", reply_markup=main_keyboard())

# === ОБРАБОТКА CALLBACK ===
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    user_id = call.from_user.id
    
    if call.data.startswith('delete_'):
        reminder_id = int(call.data.split('_')[1])
        delete_reminder(user_id, reminder_id)
        bot.answer_callback_query(call.id, "Напоминание удалено!")
        
        try:
            reminders = get_user_reminders(user_id)
            if reminders:
                bot.edit_message_text("Выберите напоминание для удаления:", call.message.chat.id, call.message.message_id, 
                                      reply_markup=reminders_keyboard(reminders))
            else:
                bot.edit_message_text("Все напоминания удалены.", call.message.chat.id, call.message.message_id)
        except Exception:
            bot.send_message(call.message.chat.id, "Напоминание удалено. Актуальный список можно посмотреть через '📋 Мои напоминания'.", reply_markup=main_keyboard())

    elif call.data.startswith('habit_done_'):
        reminder_id = int(call.data.split('_')[2])
        
        reminder_info = DB_MANAGER.execute("SELECT repeat FROM reminders WHERE id = ?", (reminder_id,), fetchone=True)
        if reminder_info:
            repeat = reminder_info[0]
            
            if mark_habit_completed(user_id, reminder_id):
                bot.answer_callback_query(call.id, "✅ Отлично! Привычка выполнена!")
                
                if repeat and repeat.lower() == '1 раз':
                    delete_reminder(user_id, reminder_id)
                    try:
                        bot.edit_message_text(f"✅ ПРИВЫЧКА ВЫПОЛНЕНА И УДАЛЕНА!\n\nПривычка была одноразовой и автоматически удалена.", 
                                            call.message.chat.id, call.message.message_id)
                    except:
                        pass
                else:
                    try:
                        habits = get_habits(user_id)
                        current_habit = None
                        for habit in habits:
                            if habit[0] == reminder_id:
                                current_habit = habit
                                break
                        
                        if current_habit:
                            new_text = f"🌱 {current_habit[1]} ✅ ВЫПОЛНЕНО!\n\n🕒 Следующее напоминание: {current_habit[2]}\n🔥 Текущий стрик: {current_habit[4]} дней"
                            keyboard = telebot.types.InlineKeyboardMarkup()
                            done_btn = telebot.types.InlineKeyboardButton("✅ Выполнено", callback_data=f"habit_done_{reminder_id}")
                            postpone_btn = telebot.types.InlineKeyboardButton("⏰ Напомнить позже", callback_data=f"postpone_{reminder_id}")
                            stats_btn = telebot.types.InlineKeyboardButton("📊 Статистика", callback_data=f"habit_stats_{reminder_id}")
                            keyboard.add(done_btn, postpone_btn, stats_btn)
                            
                            bot.edit_message_text(new_text, call.message.chat.id, call.message.message_id, reply_markup=keyboard)
                    except Exception as e:
                        print(f"Ошибка при обновлении сообщения: {e}")
            else:
                bot.answer_callback_query(call.id, "ℹ️ Вы уже отмечали эту привычку сегодня!")

    elif call.data.startswith('reminder_done_'):
        reminder_id = int(call.data.split('_')[2])
        
        reminder_info = DB_MANAGER.execute("SELECT repeat FROM reminders WHERE id = ?", (reminder_id,), fetchone=True)
        if reminder_info:
            repeat = reminder_info[0]
            
            if repeat and repeat.lower() == '1 раз':
                delete_reminder(user_id, reminder_id)
                bot.answer_callback_query(call.id, "✅ Напоминание выполнено и удалено!")
                
                try:
                    bot.edit_message_text("✅ НАПОМИНАНИЕ ВЫПОЛНЕНО И УДАЛЕНО!\n\nНапоминание было одноразовым и автоматически удалено.", 
                                        call.message.chat.id, call.message.message_id)
                except:
                    pass
            else:
                update_last_sent(reminder_id, calculate_next_send(datetime.datetime.now(), repeat))
                bot.answer_callback_query(call.id, "✅ Напоминание выполнено!")
                
                try:
                    bot.edit_message_reply_markup(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        reply_markup=None
                    )
                except:
                    pass

    elif call.data.startswith('postpone_'):
        parts = call.data.split('_')
        reminder_id = int(parts[-1])
        
        if len(parts) == 2:
            bot.answer_callback_query(call.id, "⏰ Выберите время переноса")
            
            reminder_info = DB_MANAGER.execute("SELECT text FROM reminders WHERE id = ?", (reminder_id,), fetchone=True)
            reminder_text = reminder_info[0] if reminder_info else "Напоминание"
            
            try:
                bot.edit_message_text(
                    f"🔔 Напоминание:\n{reminder_text}\n\n⏰ Напомнить позже:",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=postpone_keyboard(reminder_id)
                )
            except:
                bot.send_message(user_id, 
                               f"🔔 Напоминание:\n{reminder_text}\n\n⏰ Напомнить позже:",
                               reply_markup=postpone_keyboard(reminder_id))
            return
        
        action = parts[1]
        
        if action == "15":
            postpone_reminder(reminder_id, minutes=15)
            bot.answer_callback_query(call.id, "⏰ Напоминание перенесено на 15 минут")
            new_time = (datetime.datetime.now() + timedelta(minutes=15)).strftime("%H:%M")
            try:
                bot.edit_message_text(
                    f"✅ Напоминание перенесено!\n\nСледующее напоминание придет в {new_time}",
                    call.message.chat.id,
                    call.message.message_id
                )
            except:
                pass
                
        elif action == "60":
            postpone_reminder(reminder_id, minutes=60)
            bot.answer_callback_query(call.id, "⏰ Напоминание перенесено на 1 час")
            new_time = (datetime.datetime.now() + timedelta(hours=1)).strftime("%H:%M")
            try:
                bot.edit_message_text(
                    f"✅ Напоминание перенесено!\n\nСледующее напоминание придет в {new_time}",
                    call.message.chat.id,
                    call.message.message_id
                )
            except:
                pass
                
        elif action == "tomorrow":
            postpone_reminder(reminder_id, days=1)
            bot.answer_callback_query(call.id, "⏰ Напоминание перенесено на завтра")
            tomorrow = (datetime.datetime.now() + timedelta(days=1)).strftime("%d.%m.%Y")
            try:
                bot.edit_message_text(
                    f"✅ Напоминание перенесено!\n\nСледующее напоминание придет завтра ({tomorrow})",
                    call.message.chat.id,
                    call.message.message_id
                )
            except:
                pass
                
        elif action == "cancel":
            bot.answer_callback_query(call.id, "❌ Перенос отменен")
            reminder_info = DB_MANAGER.execute("SELECT text, time, repeat, is_habit FROM reminders WHERE id = ?", (reminder_id,), fetchone=True)
            if reminder_info:
                text, time_str, repeat, is_habit = reminder_info
                if is_habit:
                    reminder_message = f"🌱 Напоминание о привычке: {text} ({time_str}) [Повтор: {repeat}]"
                    keyboard = telebot.types.InlineKeyboardMarkup()
                    done_btn = telebot.types.InlineKeyboardButton("✅ Выполнено", callback_data=f"habit_done_{reminder_id}")
                    postpone_btn = telebot.types.InlineKeyboardButton("⏰ Напомнить позже", callback_data=f"postpone_{reminder_id}")
                    stats_btn = telebot.types.InlineKeyboardButton("📊 Статистика", callback_data=f"habit_stats_{reminder_id}")
                    keyboard.add(done_btn, postpone_btn, stats_btn)
                else:
                    reminder_message = f"🔔 Напоминание:\n{text} ({time_str}) [Повтор: {repeat}]"
                    keyboard = telebot.types.InlineKeyboardMarkup()
                    done_btn = telebot.types.InlineKeyboardButton("✅ ВЫПОЛНЕНО", callback_data=f"reminder_done_{reminder_id}")
                    postpone_btn = telebot.types.InlineKeyboardButton("⏰ Напомнить позже", callback_data=f"postpone_{reminder_id}")
                    keyboard.add(done_btn, postpone_btn)
                
                try:
                    bot.edit_message_text(
                        reminder_message,
                        call.message.chat.id,
                        call.message.message_id,
                        reply_markup=keyboard
                    )
                except:
                    pass

    elif call.data.startswith('habit_stats_'):
        reminder_id = int(call.data.split('_')[2])
        stats = get_habit_stats(user_id, reminder_id)
        
        if stats['habit_name']:
            chart_buffer = create_habit_chart(stats)
            
            bot.send_photo(user_id, chart_buffer, 
                          caption=f"📊 Статистика привычки: {stats['habit_name']}\n"
                                 f"📅 Период: {stats['period']}\n"
                                 f"🔥 Текущий стрик: {stats['current_streak']} дней\n"
                                 f"✅ Выполнено: {len(stats['completions'])} из 7 дней")
            
            bot.answer_callback_query(call.id, "📊 Статистика загружена!")
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка загрузки статистики")

    elif call.data.startswith('stats_'):
        reminder_id = int(call.data.split('_')[1])
        stats = get_habit_stats(user_id, reminder_id)
        
        if stats['habit_name']:
            chart_buffer = create_habit_chart(stats)
            
            bot.send_photo(user_id, chart_buffer, 
                          caption=f"📊 Статистика привычки: {stats['habit_name']}\n"
                                 f"📅 Период: {stats['period']}\n"
                                 f"🔥 Текущий стрик: {stats['current_streak']} дней\n"
                                 f"✅ Выполнено: {len(stats['completions'])} из 7 дней")
            
            bot.answer_callback_query(call.id, "📊 Статистика загружена!")
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка загрузки статистики")

    elif call.data == 'refresh_stats':
        habits = get_habits(user_id)
        if not habits:
            bot.answer_callback_query(call.id, "🌱 У вас пока нет привычек")
            bot.send_message(user_id, "🌱 У вас пока нет привычек для статистики. Создайте первую привычку!", reply_markup=main_keyboard())
        else:
            bot.edit_message_text("📊 Выберите привычку для просмотра статистики:",
                                call.message.chat.id, call.message.message_id,
                                reply_markup=habits_stats_keyboard(habits))
            bot.answer_callback_query(call.id, "🔄 Список обновлен")

    elif call.data == 'create_habit':
        bot.answer_callback_query(call.id, "📝 Создание привычки")
        bot.send_message(user_id, 
                        "🌱 Создание новой привычки\n\nВведите в формате:\nНазвание привычки и Время ЧЧ:ММ\n\nНапример: Читать 20 минут 21:00", 
                        reply_markup=back_keyboard())
        bot.register_next_step_handler(call.message, lambda msg: handle_task_and_time(msg, True))

    elif call.data == 'main_menu':
        bot.answer_callback_query(call.id, "🏠 Главное меню")
        bot.send_message(user_id, "🏠 Главное меню:", reply_markup=main_keyboard())

if __name__ == "__main__":
    print("✅ Бот Loopmatic запущен!")
    
    reminder_thread = threading.Thread(target=check_reminders, daemon=True)
    reminder_thread.start()

    try:
        bot.polling(none_stop=True, interval=0, timeout=30)
    except Exception as e:
        print(f"❌ Ошибка: {e}")

        time.sleep(5)
