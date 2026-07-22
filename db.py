"""
db.py — всё, что связано с облачной базой данных (Supabase).

В отличие от версии на SQLite, здесь каждая функция принимает user_id первым
аргументом — это и есть механизм "доступ с разных устройств": вы вводите
один и тот же user_id на телефоне и на компьютере и попадаете в один и тот
же набор данных.
"""

import os
from datetime import date
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

_client: Client = None


def get_client() -> Client:
    """Ленивая инициализация клиента Supabase (создаётся один раз при первом обращении)."""
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError(
                "Не заданы SUPABASE_URL / SUPABASE_KEY. "
                "Проверьте переменные окружения (см. README.md)."
            )
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


# ---------------------------------------------------------------------------
# ЗАДАЧИ
# ---------------------------------------------------------------------------

def add_task(user_id, task, due_date=None, due_time=None, duration_minutes=60,
             due_date_confidence="none", priority="средний", category="", source="ai"):
    """Добавляет одну задачу. Возвращает созданную запись (словарь)."""
    client = get_client()
    result = client.table("tasks").insert({
        "user_id": user_id,
        "task": task,
        "due_date": due_date,
        "due_time": due_time,
        "duration_minutes": duration_minutes or 60,
        "due_date_confidence": due_date_confidence,
        "priority": priority,
        "category": category,
        "source": source,
    }).execute()
    if category and category.strip():
        ensure_category_exists(user_id, category.strip())
    return result.data[0] if result.data else None


def get_tasks(user_id, date_from=None, date_to=None, status_not="готово"):
    """Задачи пользователя за период [date_from, date_to]. Без дат — не фильтрует по дате."""
    client = get_client()
    query = client.table("tasks").select("*").eq("user_id", user_id)

    if date_from:
        query = query.gte("due_date", date_from)
    if date_to:
        query = query.lte("due_date", date_to)
    if status_not:
        query = query.neq("status", status_not)

    result = query.order("due_date", desc=False).order("priority", desc=True).execute()
    return result.data


def get_open_tasks_summary(user_id, limit=30):
    """
    Короткий список незавершённых задач — используется как контекст для
    модели, чтобы она могла понимать ссылки вида "перенеси её", "отметь
    как готово" в чате.
    """
    client = get_client()
    result = (
        client.table("tasks")
        .select("id, task, due_date, priority, status")
        .eq("user_id", user_id)
        .neq("status", "готово")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


def get_all_tasks(user_id):
    """Все задачи пользователя (включая выполненные) — для табличного вида."""
    client = get_client()
    result = (
        client.table("tasks")
        .select("*")
        .eq("user_id", user_id)
        .order("id", desc=True)
        .execute()
    )
    return result.data


def update_task(user_id, task_id, **fields):
    """Обновляет поля задачи по id (только для этого пользователя)."""
    if not fields:
        return
    client = get_client()
    client.table("tasks").update(fields).eq("id", task_id).eq("user_id", user_id).execute()


def delete_task(user_id, task_id):
    client = get_client()
    client.table("tasks").delete().eq("id", task_id).eq("user_id", user_id).execute()


def upsert_tasks_from_table(user_id, rows):
    """
    Применяет изменения из редактируемой таблицы интерфейса.
    Строки с id — обновляются, строки без id — считаются новыми и добавляются.
    """
    client = get_client()
    for row in rows:
        row_id = row.get("id")
        payload = {
            "task": row.get("task"),
            "due_date": row.get("due_date") or None,
            "due_time": row.get("due_time") or None,
            "duration_minutes": row.get("duration_minutes") or 60,
            "due_date_confidence": row.get("due_date_confidence") or "none",
            "priority": row.get("priority") or "средний",
            "category": row.get("category") or "",
            "status": row.get("status") or "новая",
        }
        if row_id and not _is_nan(row_id):
            client.table("tasks").update(payload).eq("id", int(row_id)).eq("user_id", user_id).execute()
        else:
            payload["user_id"] = user_id
            payload["source"] = "manual"
            client.table("tasks").insert(payload).execute()


def _is_nan(value):
    try:
        return value != value  # True только для NaN
    except Exception:
        return False


# ---------------------------------------------------------------------------
# ИСТОРИЯ ЧАТА (память)
# ---------------------------------------------------------------------------

def save_message(user_id, role, content):
    """Сохраняет одно сообщение (пользователя или ассистента) в историю."""
    client = get_client()
    client.table("messages").insert({
        "user_id": user_id,
        "role": role,
        "content": content,
    }).execute()


def get_recent_messages(user_id, limit=20):
    """
    Последние N сообщений пользователя в хронологическом порядке —
    именно это и даёт ассистенту "память" между сессиями и устройствами.
    """
    client = get_client()
    result = (
        client.table("messages")
        .select("role, content, created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    messages = result.data or []
    return list(reversed(messages))  # разворачиваем в хронологический порядок


# ---------------------------------------------------------------------------
# НАПОМИНАНИЯ (дни рождения, встречи с фиксированной датой и т.п.)
# ---------------------------------------------------------------------------

def add_reminder(user_id, title, event_date, event_time=None, recurrence="once",
                  lead_days=None, remind_on_day=True, repeat_on_day=False):
    """
    Добавляет одно напоминание.
    recurrence: 'once' (разовое) | 'yearly' (повторяется каждый год, для ДР)
    lead_days: список чисел — за сколько дней предупреждать заранее, напр. [7, 1]
    repeat_on_day: если True — "нагонять" напоминание каждый день ПОСЛЕ дня
                   события, пока пользователь явно не подтвердит (для ДР по
                   умолчанию должно быть True — чтобы точно не забыть).
    """
    client = get_client()
    result = client.table("reminders").insert({
        "user_id": user_id,
        "title": title,
        "event_date": event_date,
        "event_time": event_time,
        "recurrence": recurrence,
        "lead_days": lead_days if lead_days is not None else [1],
        "remind_on_day": remind_on_day,
        "repeat_on_day": repeat_on_day,
    }).execute()
    return result.data[0] if result.data else None


def get_active_reminders(user_id):
    """Все активные напоминания пользователя — используется и для расчёта
    'что показать сегодня', и как контекст модели для команд вида
    'поздравила Машу' / 'отмени напоминание про стоматолога'."""
    client = get_client()
    result = (
        client.table("reminders")
        .select("*")
        .eq("user_id", user_id)
        .eq("status", "active")
        .execute()
    )
    return result.data


def confirm_reminder(user_id, reminder_id, today_year):
    """
    Подтверждает напоминание ('поздравила', 'сделано').
    Для разовых — переводит в status='done' насовсем.
    Для ежегодных — запоминает, что в ЭТОМ году уже подтверждено
    (на следующий год напоминание само 'проснётся' заново).
    """
    client = get_client()
    reminder = (
        client.table("reminders").select("recurrence")
        .eq("id", reminder_id).eq("user_id", user_id).execute()
    )
    if not reminder.data:
        return
    if reminder.data[0]["recurrence"] == "yearly":
        client.table("reminders").update(
            {"last_confirmed_year": today_year}
        ).eq("id", reminder_id).eq("user_id", user_id).execute()
    else:
        client.table("reminders").update(
            {"status": "done"}
        ).eq("id", reminder_id).eq("user_id", user_id).execute()


def delete_reminder(user_id, reminder_id):
    client = get_client()
    client.table("reminders").delete().eq("id", reminder_id).eq("user_id", user_id).execute()


def get_all_reminders_with_next_date(user_id, today):
    """Все активные напоминания с вычисленной ближайшей датой наступления —
    используется, чтобы показывать напоминания в общем ежедневнике вместе
    с задачами, а не только в баннере в день события."""
    reminders = get_active_reminders(user_id)
    result = []
    for r in reminders:
        event_date = date.fromisoformat(r["event_date"]) if isinstance(r["event_date"], str) else r["event_date"]
        if r["recurrence"] == "yearly":
            next_occurrence = date(today.year, event_date.month, event_date.day)
            if next_occurrence < today or r.get("last_confirmed_year") == next_occurrence.year:
                next_occurrence = date(today.year + 1, event_date.month, event_date.day)
        else:
            next_occurrence = event_date
        r_copy = dict(r)
        r_copy["next_occurrence"] = next_occurrence.isoformat()
        result.append(r_copy)
    return result
    """
    Считает, какие напоминания актуальны СЕГОДНЯ — вызывается при каждом
    открытии приложения. Не требует отдельного планировщика/бэкенда:
    вся логика — простое сравнение дат в Python.

    Возвращает список словарей: {reminder, kind, days_offset}
    kind: 'заранее' | 'сегодня' | 'просрочено'
    """
    OVERDUE_WINDOW_DAYS = 30  # дольше месяца просроченный ДР не "нагоняем" —
                               # считаем, что дата пропущена, и просто ждём
                               # следующего цикла, чтобы не спамить вечно

    reminders = get_active_reminders(user_id)
    due = []

    for r in reminders:
        event_date = date.fromisoformat(r["event_date"]) if isinstance(r["event_date"], str) else r["event_date"]

        if r["recurrence"] == "yearly":
            already_confirmed_this_year = r.get("last_confirmed_year") == today.year
            this_year_occurrence = date(today.year, event_date.month, event_date.day)
            days_until = (this_year_occurrence - today).days

            if already_confirmed_this_year or days_until < -OVERDUE_WINDOW_DAYS:
                # уже подтверждено в этом цикле, или дата была настолько давно,
                # что смотрим вперёд, на следующий год
                next_occurrence = date(today.year + 1, event_date.month, event_date.day)
                days_until = (next_occurrence - today).days
                lead_days = r.get("lead_days") or []
                if days_until in lead_days:
                    due.append({"reminder": r, "kind": "заранее", "days_offset": days_until})
                continue
        else:
            days_until = (event_date - today).days

        lead_days = r.get("lead_days") or []

        if days_until < 0 and r.get("repeat_on_day", False):
            # событие прошло, а подтверждения не было — "нагоняем" каждый день
            due.append({"reminder": r, "kind": "просрочено", "days_offset": days_until})
        elif days_until == 0 and r.get("remind_on_day", True):
            due.append({"reminder": r, "kind": "сегодня", "days_offset": 0})
        elif days_until in lead_days:
            due.append({"reminder": r, "kind": "заранее", "days_offset": days_until})

    return due


# ---------------------------------------------------------------------------
# КАТЕГОРИИ — существуют как отдельная сущность, чтобы кнопка в сайдбаре
# могла появиться ДО того, как в этой категории будет хоть одна задача.
# ---------------------------------------------------------------------------

def ensure_category_exists(user_id, name):
    """Регистрирует категорию, если её ещё нет. Дубликат имени — это штатная
    ситуация (категория уже есть) и тихо игнорируется; любая другая ошибка
    (например, RLS блокирует запись) — пробрасывается дальше, чтобы не
    прятать реальную проблему молча."""
    client = get_client()
    try:
        client.table("categories").insert({"user_id": user_id, "name": name}).execute()
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            pass  # категория с таким именем уже существует — это нормально
        else:
            raise


def get_categories(user_id):
    """Список названий категорий пользователя, отсортированный по алфавиту."""
    client = get_client()
    result = (
        client.table("categories").select("name")
        .eq("user_id", user_id).order("name").execute()
    )
    return [row["name"] for row in result.data]


def delete_category(user_id, name):
    client = get_client()
    client.table("categories").delete().eq("user_id", user_id).eq("name", name).execute()


def get_tasks_by_category(user_id, category):
    """Все незавершённые задачи конкретной категории — для отфильтрованного ежедневника."""
    client = get_client()
    result = (
        client.table("tasks").select("*")
        .eq("user_id", user_id).eq("category", category)
        .execute()
    )
    return result.data


# ---------------------------------------------------------------------------
# СПИСКИ — произвольные перечни (покупки, вещи в поездку и т.п.), НЕ задачи.
# ---------------------------------------------------------------------------

def add_list(user_id, name):
    client = get_client()
    result = client.table("lists").insert({"user_id": user_id, "name": name}).execute()
    return result.data[0] if result.data else None


def get_lists(user_id):
    """Список словарей {id, name} — списки пользователя, по алфавиту."""
    client = get_client()
    result = (
        client.table("lists").select("id, name")
        .eq("user_id", user_id).order("name").execute()
    )
    return result.data


def get_lists_with_items(user_id):
    """Списки вместе с текстами их пунктов — используется как контекст для
    модели, чтобы она могла находить нужный список/пункт по смыслу."""
    lists = get_lists(user_id)
    result = []
    for l in lists:
        items = get_list_items(user_id, l["id"])
        result.append({
            "id": l["id"], "name": l["name"],
            "items": [i["content"] for i in items if not i["checked"]]
        })
    return result


def find_or_create_list(user_id, name):
    """Находит список по имени (без учёта регистра) или создаёт новый,
    если такого ещё нет. Возвращает id списка."""
    client = get_client()
    existing = (
        client.table("lists").select("id, name")
        .eq("user_id", user_id).execute()
    )
    for l in existing.data:
        if l["name"].strip().lower() == name.strip().lower():
            return l["id"]
    created = add_list(user_id, name.strip())
    return created["id"] if created else None


def delete_list(user_id, list_id):
    client = get_client()
    client.table("lists").delete().eq("id", list_id).eq("user_id", user_id).execute()


def add_list_item(user_id, list_id, content):
    client = get_client()
    result = client.table("list_items").insert({
        "user_id": user_id, "list_id": list_id, "content": content
    }).execute()
    return result.data[0] if result.data else None


def get_list_items(user_id, list_id):
    client = get_client()
    result = (
        client.table("list_items").select("*")
        .eq("user_id", user_id).eq("list_id", list_id)
        .order("created_at").execute()
    )
    return result.data


def update_list_item(user_id, item_id, checked):
    client = get_client()
    client.table("list_items").update({"checked": checked}).eq("id", item_id).eq("user_id", user_id).execute()


def delete_list_item(user_id, item_id):
    client = get_client()
    client.table("list_items").delete().eq("id", item_id).eq("user_id", user_id).execute()
