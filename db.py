"""
db.py — всё, что связано с облачной базой данных (Supabase).

МОДЕЛЬ ДАННЫХ: одна таблица tasks для всех задач. Поле task_type различает
три поведения:
  'event'   — точная дата и время (приём, созвон, вылет)
  'marker'  — точная дата, важно не забыть (день рождения, дедлайн)
  'regular' — обычная задача, дата не обязательна

Каждая функция принимает user_id первым аргументом — так на разных
устройствах с одним и тем же ключом доступа видны одни и те же данные.
"""

import os
from datetime import date
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

PRIORITY_RANK = {"низкий": 0, "средний": 1, "высокий": 2}

DEFAULT_PRIORITY_BY_TYPE = {
    "event": "высокий",
    "marker": "низкий",
    "regular": "средний",
}

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


def _merge_with_category_priority(user_id, category, priority):
    """Если у категории задан приоритет по умолчанию (пользователь явно
    попросил "категория X всегда высокий приоритет") — итоговый приоритет
    задачи не может быть НИЖЕ приоритета категории. Если категория не задана
    или у неё нет override — возвращает приоритет как есть."""
    if not category:
        return priority
    client = get_client()
    result = (
        client.table("categories").select("default_priority")
        .eq("user_id", user_id).eq("name", category).execute()
    )
    if not result.data:
        return priority
    cat_priority = result.data[0].get("default_priority")
    if not cat_priority:
        return priority
    if PRIORITY_RANK.get(cat_priority, 0) > PRIORITY_RANK.get(priority, 0):
        return cat_priority
    return priority


# ---------------------------------------------------------------------------
# ЗАДАЧИ (все три типа — event / marker / regular — в одной таблице)
# ---------------------------------------------------------------------------

def add_task(user_id, task, task_type="regular", due_date=None, due_time=None,
             duration_minutes=60, due_date_confidence="none", priority=None,
             category="", source="ai", recurrence=None, lead_days=None,
             remind_on_day=True, repeat_on_day=None):
    """
    Добавляет одну задачу любого из трёх типов.

    priority: если None — берётся значение по умолчанию для task_type
              (event→высокий, marker→низкий, regular→средний), затем
              приподнимается до приоритета категории, если он выше.
    repeat_on_day: если None — True для marker с recurrence='yearly' (дни
                   рождения нельзя пропускать), иначе False.
    """
    category = (category or "").strip() or "Общее"

    if priority is None:
        priority = DEFAULT_PRIORITY_BY_TYPE.get(task_type, "средний")
    priority = _merge_with_category_priority(user_id, category, priority)

    if repeat_on_day is None:
        repeat_on_day = (task_type == "marker" and recurrence == "yearly")

    client = get_client()
    result = client.table("tasks").insert({
        "user_id": user_id,
        "task": task,
        "task_type": task_type,
        "due_date": due_date,
        "due_time": due_time,
        "duration_minutes": duration_minutes or 60,
        "due_date_confidence": due_date_confidence,
        "recurrence": recurrence,
        "lead_days": lead_days if lead_days is not None else [1],
        "remind_on_day": remind_on_day,
        "repeat_on_day": repeat_on_day,
        "priority": priority,
        "category": category,
        "source": source,
    }).execute()

    if category:
        ensure_category_exists(user_id, category)
    return result.data[0] if result.data else None


def get_tasks(user_id, date_from=None, date_to=None, status_not="готово"):
    """Задачи пользователя за период [date_from, date_to]. Без дат — все."""
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


def get_open_tasks_summary(user_id, limit=40):
    """Короткий список незавершённых задач всех типов — контекст для модели,
    чтобы она понимала ссылки вида 'отметь звонок как сделанный' и видела
    уже существующие маркеры/события, не создавая дубликаты."""
    client = get_client()
    result = (
        client.table("tasks")
        .select("id, task, task_type, due_date, due_time, priority, category, status, recurrence")
        .eq("user_id", user_id)
        .neq("status", "готово")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


def get_all_tasks(user_id):
    """Все задачи пользователя (включая выполненные) — для полного ежедневника."""
    client = get_client()
    result = (
        client.table("tasks").select("*")
        .eq("user_id", user_id).order("id", desc=True).execute()
    )
    return result.data


def get_tasks_by_category(user_id, category):
    """Все задачи (любого типа) конкретной категории — для отфильтрованного вида."""
    client = get_client()
    result = (
        client.table("tasks").select("*")
        .eq("user_id", user_id).eq("category", category).execute()
    )
    return result.data


def update_task(user_id, task_id, **fields):
    """Обновляет произвольные поля задачи по id."""
    if not fields:
        return
    client = get_client()
    client.table("tasks").update(fields).eq("id", task_id).eq("user_id", user_id).execute()


def delete_task(user_id, task_id):
    client = get_client()
    client.table("tasks").delete().eq("id", task_id).eq("user_id", user_id).execute()


def complete_task(user_id, task_id, today_year):
    """
    Отмечает задачу выполненной/подтверждённой — с учётом типа:
    - marker с recurrence='yearly' (день рождения и т.п.): НЕ переводится
      в статус "готово" насовсем, а запоминается, что в ЭТОМ году уже
      подтверждено (last_confirmed_year) — на следующий год напомнит снова.
    - всё остальное (event, regular, marker с recurrence='once'):
      обычный статус "готово".
    """
    client = get_client()
    row = (
        client.table("tasks").select("task_type, recurrence")
        .eq("id", task_id).eq("user_id", user_id).execute()
    )
    if not row.data:
        return
    task_type, recurrence = row.data[0]["task_type"], row.data[0]["recurrence"]
    if task_type == "marker" and recurrence == "yearly":
        client.table("tasks").update(
            {"last_confirmed_year": today_year}
        ).eq("id", task_id).eq("user_id", user_id).execute()
    else:
        client.table("tasks").update(
            {"status": "готово"}
        ).eq("id", task_id).eq("user_id", user_id).execute()


def upsert_tasks_from_table(user_id, rows):
    """Применяет изменения из редактируемой таблицы интерфейса. Строки с id
    обновляются, строки без id считаются новыми и добавляются."""
    client = get_client()
    for row in rows:
        row_id = row.get("id")
        payload = {
            "task": row.get("task"),
            "task_type": row.get("task_type") or "regular",
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
        return value != value
    except Exception:
        return False


def get_tasks_for_planner(user_id, today, category=None):
    """
    Возвращает задачи (опционально отфильтрованные по категории) с полем
    display_date — датой, под которой задача должна показаться в
    ежедневнике. Обычно совпадает с due_date, но:
    - для marker с recurrence='yearly' пересчитывается на ближайшее
      наступление даты в этом или следующем году (и пропускается, если
      уже подтверждено в этом году);
    - для regular задач с прошедшим сроком и status != 'готово' —
      "переезжает" на сегодня (не потерялась, раз не выполнена вовремя).
    event-задачи никогда не переезжают — пропущенная встреча остаётся
    в прошлом, это не "долг", который нужно перенести.
    """
    all_tasks = get_tasks_by_category(user_id, category) if category else get_all_tasks(user_id)
    result = []

    for t in all_tasks:
        t = dict(t)
        due_date_str = t.get("due_date")

        if t["task_type"] == "marker" and t.get("recurrence") == "yearly" and due_date_str:
            event_date = date.fromisoformat(due_date_str)
            next_occurrence = date(today.year, event_date.month, event_date.day)
            already_confirmed = t.get("last_confirmed_year") == next_occurrence.year
            if next_occurrence < today or already_confirmed:
                next_occurrence = date(today.year + 1, event_date.month, event_date.day)
                if t.get("last_confirmed_year") == next_occurrence.year:
                    continue  # уже подтверждено и на следующий цикл — не показываем
            t["display_date"] = next_occurrence.isoformat()

        elif (t["task_type"] == "regular" and due_date_str and t["status"] != "готово"
              and date.fromisoformat(due_date_str) < today):
            t["display_date"] = today.isoformat()
            t["original_date"] = due_date_str  # чтобы можно было показать "(с 18 июля)"

        else:
            t["display_date"] = due_date_str

        result.append(t)

    return result


def compute_due_markers(user_id, today):
    """
    Считает, какие marker-задачи актуальны СЕГОДНЯ — для баннера при
    открытии приложения. Возвращает список {task, kind, days_offset}.
    kind: 'заранее' | 'сегодня' | 'просрочено'
    """
    OVERDUE_WINDOW_DAYS = 30

    client = get_client()
    result = (
        client.table("tasks").select("*")
        .eq("user_id", user_id).eq("task_type", "marker")
        .neq("status", "готово").execute()
    )
    markers = result.data
    due = []

    for m in markers:
        if not m.get("due_date"):
            continue
        event_date = date.fromisoformat(m["due_date"])

        if m.get("recurrence") == "yearly":
            already_confirmed_this_year = m.get("last_confirmed_year") == today.year
            this_year_occurrence = date(today.year, event_date.month, event_date.day)
            days_until = (this_year_occurrence - today).days

            if already_confirmed_this_year or days_until < -OVERDUE_WINDOW_DAYS:
                next_occurrence = date(today.year + 1, event_date.month, event_date.day)
                days_until = (next_occurrence - today).days
                lead_days = m.get("lead_days") or []
                if days_until in lead_days:
                    due.append({"task": m, "kind": "заранее", "days_offset": days_until})
                continue
        else:
            days_until = (event_date - today).days

        lead_days = m.get("lead_days") or []

        if days_until < 0 and m.get("repeat_on_day", False):
            due.append({"task": m, "kind": "просрочено", "days_offset": days_until})
        elif days_until == 0 and m.get("remind_on_day", True):
            due.append({"task": m, "kind": "сегодня", "days_offset": 0})
        elif days_until in lead_days:
            due.append({"task": m, "kind": "заранее", "days_offset": days_until})

    return due


# ---------------------------------------------------------------------------
# КАТЕГОРИИ
# ---------------------------------------------------------------------------

def ensure_category_exists(user_id, name):
    """Регистрирует категорию, если её ещё нет. Дубликат имени — штатная
    ситуация и тихо игнорируется; любая другая ошибка пробрасывается дальше."""
    client = get_client()
    try:
        client.table("categories").insert({"user_id": user_id, "name": name}).execute()
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            pass
        else:
            raise


def get_categories(user_id):
    client = get_client()
    result = (
        client.table("categories").select("name")
        .eq("user_id", user_id).order("name").execute()
    )
    return [row["name"] for row in result.data]


def delete_category(user_id, name):
    client = get_client()
    client.table("categories").delete().eq("user_id", user_id).eq("name", name).execute()


def set_category_priority(user_id, name, priority):
    """Задаёт приоритет по умолчанию для категории — новые задачи этой
    категории будут получать приоритет не ниже указанного. Не меняет
    приоритет уже существующих задач задним числом."""
    ensure_category_exists(user_id, name)
    client = get_client()
    client.table("categories").update(
        {"default_priority": priority}
    ).eq("user_id", user_id).eq("name", name).execute()


# ---------------------------------------------------------------------------
# СПИСКИ — произвольные перечни (покупки, вещи в поездку и т.п.), НЕ задачи.
# ---------------------------------------------------------------------------

def add_list(user_id, name):
    client = get_client()
    result = client.table("lists").insert({"user_id": user_id, "name": name}).execute()
    return result.data[0] if result.data else None


def get_lists(user_id):
    client = get_client()
    result = (
        client.table("lists").select("id, name")
        .eq("user_id", user_id).order("name").execute()
    )
    return result.data


def get_lists_with_items(user_id):
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
    client = get_client()
    existing = client.table("lists").select("id, name").eq("user_id", user_id).execute()
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


# ---------------------------------------------------------------------------
# ИСТОРИЯ ЧАТА (память)
# ---------------------------------------------------------------------------

def save_message(user_id, role, content):
    client = get_client()
    client.table("messages").insert({
        "user_id": user_id, "role": role, "content": content,
    }).execute()


def get_recent_messages(user_id, limit=20):
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
    return list(reversed(messages))
