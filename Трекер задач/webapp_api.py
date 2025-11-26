from flask import Flask, request, jsonify, send_from_directory
import sqlite3
import os
from datetime import datetime, timedelta
import json

app = Flask(__name__)


DB_PATH = os.path.join(os.path.dirname(__file__), 'bot_users.db')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/webapp')
def serve_webapp():
    return send_from_directory('webapp', 'index.html')

@app.route('/webapp/<path:path>')
def serve_static(path):
    return send_from_directory('webapp', path)

@app.route('/api/reminders', methods=['GET'])
def get_reminders():
    user_id = request.args.get('user_id')
    
    conn = get_db_connection()
    reminders = conn.execute(
        '''SELECT id, text, time, repeat, is_habit, habit_streak 
           FROM reminders WHERE user_id = ? 
           ORDER BY time''',
        (user_id,)
    ).fetchall()
    conn.close()
    
    reminders_list = [dict(reminder) for reminder in reminders]
    return jsonify(reminders_list)

@app.route('/api/reminders', methods=['POST'])
def add_reminder():
    try:
        data = request.json
        user_id = data['user_id']
        text = data['text']
        time_str = data['time']
        repeat = data.get('repeat', 'ежедневно')
        is_habit = data.get('is_habit', False)

        try:
            datetime.strptime(time_str, '%H:%M')
        except ValueError:
            return jsonify({"error": "Неверный формат времени"}), 400
        
        conn = get_db_connection()

        existing = conn.execute(
            'SELECT id FROM reminders WHERE user_id = ? AND text = ? AND time = ? AND repeat = ?',
            (user_id, text, time_str, repeat)
        ).fetchone()
        
        if existing:
            conn.close()
            return jsonify({"error": "Такое напоминание уже существует"}), 400

        current_time = datetime.now()
        reminder_time = datetime.strptime(time_str, '%H:%M').replace(
            year=current_time.year, month=current_time.month, day=current_time.day
        )
        
        if reminder_time < current_time:
            reminder_time += timedelta(days=1)
            
        conn.execute(
            '''INSERT INTO reminders (user_id, text, time, repeat, is_habit, created_at, next_send) 
               VALUES (?, ?, ?, ?, ?, datetime('now'), ?)''',
            (user_id, text, time_str, repeat, is_habit, reminder_time.isoformat())
        )
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success", 
            "message": "Напоминание добавлено",
            "reminder": {
                "text": text,
                "time": time_str,
                "repeat": repeat,
                "is_habit": is_habit
            }
        })
        
    except Exception as e:
        return jsonify({"error": f"Ошибка сервера: {str(e)}"}), 500

@app.route('/api/reminders/<int:reminder_id>', methods=['DELETE'])
def delete_reminder(reminder_id):
    user_id = request.args.get('user_id')
    
    conn = get_db_connection()
    conn.execute('DELETE FROM reminders WHERE id = ? AND user_id = ?', (reminder_id, user_id))
    conn.execute('DELETE FROM habit_completions WHERE reminder_id = ? AND user_id = ?', (reminder_id, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({"status": "success", "message": "Напоминание удалено"})

@app.route('/api/habits/<int:reminder_id>/complete', methods=['POST'])
def complete_habit(reminder_id):
    user_id = request.args.get('user_id')
    today = datetime.now().date().isoformat()
    current_time = datetime.now().time().strftime('%H:%M')
    
    conn = get_db_connection()
    existing = conn.execute(
        'SELECT id FROM habit_completions WHERE user_id = ? AND reminder_id = ? AND completion_date = ?',
        (user_id, reminder_id, today)
    ).fetchone()
    
    if existing:
        conn.close()
        return jsonify({"error": "Привычка уже выполнена сегодня"}), 400
    
    conn.execute(
        'INSERT INTO habit_completions (user_id, reminder_id, completion_date, completion_time, created_at) VALUES (?, ?, ?, ?, datetime("now"))',
        (user_id, reminder_id, today, current_time)
    )
    
    conn.execute(
        'UPDATE user_stats SET total_habits_completed = total_habits_completed + 1 WHERE user_id = ?',
        (user_id,)
    )
    
    # Обновляем стрик
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    yesterday_completed = conn.execute(
        'SELECT id FROM habit_completions WHERE user_id = ? AND reminder_id = ? AND completion_date = ?',
        (user_id, reminder_id, yesterday)
    ).fetchone()
    
    current_streak = conn.execute(
        'SELECT habit_streak FROM reminders WHERE id = ?', (reminder_id,)
    ).fetchone()[0] or 0
    
    new_streak = current_streak + 1 if yesterday_completed else 1
    
    conn.execute(
        'UPDATE reminders SET habit_streak = ? WHERE id = ?',
        (new_streak, reminder_id)
    )
    
    conn.commit()

    habit = conn.execute(
        'SELECT text, time, habit_streak FROM reminders WHERE id = ?', (reminder_id,)
    ).fetchone()
    
    conn.close()
    
    return jsonify({
        "status": "success", 
        "message": "Привычка выполнена", 
        "new_streak": new_streak,
        "habit": dict(habit) if habit else {}
    })

@app.route('/api/stats', methods=['GET'])
def get_stats():
    user_id = request.args.get('user_id')
    today = datetime.now().date().isoformat()
    
    conn = get_db_connection()

    total_reminders = conn.execute(
        'SELECT COUNT(*) FROM reminders WHERE user_id = ?', (user_id,)
    ).fetchone()[0]

    habits_count = conn.execute(
        'SELECT COUNT(*) FROM reminders WHERE user_id = ? AND is_habit = 1', (user_id,)
    ).fetchone()[0]
    
    completed_today = conn.execute(
        'SELECT COUNT(*) FROM habit_completions WHERE user_id = ? AND completion_date = ?', (user_id, today)
    ).fetchone()[0]

    best_streak = conn.execute(
        'SELECT MAX(habit_streak) FROM reminders WHERE user_id = ? AND is_habit = 1', (user_id,)
    ).fetchone()[0] or 0
    
    habits = conn.execute('''
        SELECT r.id, r.text, r.time, r.habit_streak as streak,
               (SELECT COUNT(*) FROM habit_completions hc 
                WHERE hc.reminder_id = r.id AND hc.completion_date >= date('now', '-7 days')) as completed_days
        FROM reminders r 
        WHERE r.user_id = ? AND r.is_habit = 1
        ORDER BY r.habit_streak DESC
    ''', (user_id,)).fetchall()
    
    week_completions = conn.execute('''
        SELECT COUNT(*) FROM habit_completions 
        WHERE user_id = ? AND completion_date >= date('now', '-7 days')
    ''', (user_id,)).fetchone()[0]
    
    conn.close()
    
    return jsonify({
        "total_reminders": total_reminders,
        "habits_count": habits_count,
        "completed_today": completed_today,
        "best_streak": best_streak,
        "week_completions": week_completions,
        "habits": [dict(habit) for habit in habits]
    })

@app.route('/api/user/info', methods=['GET'])
def get_user_info():
    user_id = request.args.get('user_id')
    
    conn = get_db_connection()
    user = conn.execute(
        'SELECT user_id, username, joined_at FROM users WHERE user_id = ?', (user_id,)
    ).fetchone()
    
    if not user:
        conn.close()
        return jsonify({"error": "Пользователь не найден"}), 404
    
    user_info = dict(user)
    conn.close()
    
    return jsonify(user_info)


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

if __name__ == '__main__':
    if not os.path.exists('webapp'):
        os.makedirs('webapp')
    
    print("🚀 Веб-API запущено на http://0.0.0.0:5000")
    print("📱 Mini App доступно по /webapp")

    app.run(host='0.0.0.0', port=5000, debug=True)
