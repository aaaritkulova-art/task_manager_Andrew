"""
assistant.py — версия на Mistral AI (бесплатный уровень, без карты, без
региональных ограничений для ЕС — компания французская, это их основной рынок).

Как и в версии на Gemini, имена и аргументы функций (interpret_message,
formulate_reply) не меняются — app.py трогать не нужно.
"""

import json
import os
import time
from datetime import date

# В разных версиях библиотеки mistralai класс Mistral лежит в разных местах —
# пробуем оба варианта, чтобы не зависеть от конкретной версии пакета.
try:
    from mistralai import Mistral
except ImportError:
    from mistralai.client import Mistral

client = Mistral(api_key=os.environ.get("MISTRAL_API_KEY"))


def _call_with_retry(fn, max_attempts=4, base_delay=5):
    """
    Вызывает fn() с автоматическим повтором при ошибке 429 (превышен лимит
    запросов бесплатного тарифа). Ждёт с увеличивающейся паузой между
    попытками вместо того, чтобы сразу падать с ошибкой.
    """
    last_error = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            if "429" in str(e) or "rate_limited" in str(e).lower():
                last_error = e
                time.sleep(base_delay * (attempt + 1))  # 5с, 10с, 15с, 20с
            else:
                raise
    raise last_error
MODEL = "mistral-small-latest"


def _format_history(chat_history):
    if not chat_history:
        return "(история пуста, это начало общения)"
    lines = []
    for m in chat_history:
        speaker = "Пользователь" if m["role"] == "user" else "Ассистент"
        lines.append(f"{speaker}: {m['content']}")
    return "\n".join(lines)


def _format_open_tasks(open_tasks):
    if not open_tasks:
        return "(открытых задач нет)"
    lines = []
    for t in open_tasks:
        lines.append(
            f"id={t['id']} | {t['task']} | срок: {t.get('due_date') or 'не указан'} "
            f"| приоритет: {t.get('priority')} | статус: {t.get('status')}"
        )
    return "\n".join(lines)


def _format_open_reminders(open_reminders):
    if not open_reminders:
        return "(активных напоминаний нет)"
    lines = []
    for r in open_reminders:
        lines.append(
            f"id={r['id']} | {r['title']} | дата: {r.get('event_date')} "
            f"| повтор: {r.get('recurrence')}"
        )
    return "\n".join(lines)


def _format_lists(lists_with_items):
    """lists_with_items: список словарей {name, items: [строки]}"""
    if not lists_with_items:
        return "(списков пока нет)"
    lines = []
    for l in lists_with_items:
        items_str = ", ".join(l["items"]) if l["items"] else "(пусто)"
        lines.append(f"«{l['name']}»: {items_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ШАГ 1: понять, что хочет пользователь
# ---------------------------------------------------------------------------

INTERPRET_SYSTEM_PROMPT = """Ты — модуль анализа для ассистента по управлению задачами и напоминаниями.
Твоя единственная работа — прочитать сообщение пользователя и вернуть СТРОГО JSON,
без единого слова до или после, без markdown-разметки.

Сегодняшняя дата: {today} ({weekday_ru}).
Все относительные даты («завтра», «в пятницу», «через неделю») считай от неё.

--- ИСТОРИЯ ДИАЛОГА ---
{history}

--- ОТКРЫТЫЕ ЗАДАЧИ ---
{open_tasks}

--- АКТИВНЫЕ НАПОМИНАНИЯ ---
{open_reminders}

--- СУЩЕСТВУЮЩИЕ СПИСКИ (не задачи — просто перечни, напр. покупки) ---
{lists}
--- конец контекста ---

ВАЖНОЕ РАЗЛИЧИЕ: задача vs напоминание vs пункт списка.
- ЗАДАЧА — то, что пользователь должен САМ сделать один раз ("постирать",
  "подготовить КП", "позвонить клиенту"). Разовое действие.
- НАПОМИНАНИЕ — событие с конкретной датой, о котором пользователь просто
  не хочет забыть ("день рождения мамы", "стоматолог 22 июля в 20:00",
  "годовщина"). Дни рождения и любые повторяющиеся из года в год даты —
  ВСЕГДА напоминание с recurrence="yearly", даже если явно не сказано "каждый год".
- ПУНКТ СПИСКА — элемент произвольного перечня без даты и без действия
  "сделать" в привычном смысле (молоко в списке покупок, книга в списке
  "прочитать", вещь в списке "взять в поездку"). Если пользователь называет
  список явно ("добавь в покупки молоко", "в список на поездку — зарядку")
  или из контекста ясно, что речь о пополнении существующего списка —
  это list_action, НЕ add_task.

Определи intent — одно из восьми:
- "add_task"        — новая задача (разовое дело для самого пользователя)
- "query"           — вопрос про существующие задачи
- "update_task"     — изменить задачу ИЛИ отметить её ВЫПОЛНЕННОЙ (задача
                      остаётся в базе с пометкой "готово" — НЕ удаляется)
- "delete_task"     — УДАЛИТЬ задачу из базы насовсем (не то же самое, что
                      выполнить! см. правило ниже)
- "add_reminder"    — новое напоминание о дате/событии
- "confirm_reminder"— подтверждение напоминания ("поздравила Машу", "было у стоматолога")
- "list_action"     — добавить/отметить/удалить пункт(ы) в списке (покупки и т.п.)
- "chat"            — реплика не про задачи, не про напоминания, не про списки

ВАЖНОЕ РАЗЛИЧИЕ: удалить ≠ выполнить.
- "сделал(а)", "выполнил(а)", "готово", "закончил(а)", "сходил(а)" — это
  ВЫПОЛНЕНИЕ, intent="update_task", changes={{"status": "готово"}}. Задача
  остаётся в базе, просто помечается сделанной.
- "удали", "убери", "сотри", "отмени эту задачу", "удали из списка" — это
  УДАЛЕНИЕ, intent="delete_task". Задача пропадает из базы насовсем.
Если сомневаешься между ними — трактуй как выполнение (это безопаснее, задачу
всегда можно удалить отдельной командой, а восстановить удалённую — нельзя).

Формат ответа:

{{
  "intent": "add_task" | "query" | "update_task" | "delete_task" | "add_reminder" | "confirm_reminder" | "list_action" | "chat",

  "tasks": [
    {{"task": "...", "due_date": "YYYY-MM-DD"|null, "due_time": "HH:MM"|null,
      "duration_minutes": число (по умолчанию 60),
      "due_date_confidence": "exact"|"approximate"|"none",
      "priority": "высокий"|"средний"|"низкий", "category": "..."}}
  ],

  "query_date_from": "YYYY-MM-DD" | null,
  "query_date_to": "YYYY-MM-DD" | null,

  "updates": [
    {{"task_id": число, "changes": {{"status": "готово", "due_date": "YYYY-MM-DD", "priority": "высокий"}}}}
  ],

  "deletes": [
    {{"task_id": число (найди по смыслу в списке открытых задач или по истории диалога)}}
  ],

  "reminders": [
    {{
      "title": "краткое название события",
      "event_date": "YYYY-MM-DD",
      "event_time": "HH:MM" | null,
      "recurrence": "once" | "yearly",
      "lead_days": [список чисел, за сколько дней предупредить заранее — по умолчанию
                    [1] для разовых, [7, 1] для дней рождения, можно переопределить,
                    если пользователь сам указал сроки],
      "remind_on_day": true,
      "repeat_on_day": true если recurrence=="yearly" (дни рождения нельзя пропускать),
                        false для большинства разовых событий, если пользователь явно
                        не попросил "напоминай, пока не отмечу"
    }}
  ],

  "confirmations": [
    {{"reminder_id": число (найди по смыслу в списке активных напоминаний выше)}}
  ],

  "list_actions": [
    {{
      "action": "add" | "check" | "delete",
      "list_name": "название списка — если такой список уже есть в контексте выше,
                    используй ТОЧНО такое же название; если списка ещё нет —
                    придумай короткое logичное название (например 'Покупки'),
                    он будет создан автоматически",
      "item": "текст пункта (для action=add — что добавить; для check/delete —
               по какому пункту ориентироваться, ищи по смыслу среди пунктов,
               если они перечислены в контексте выше)"
    }}
  ]
}}

Заполняй только relevant для intent поля верхнего уровня.

Правила для tasks (add_task):
- По умолчанию каждый пункт, перечисленный через запятую или точку с запятой —
  ОТДЕЛЬНАЯ независимая задача. НЕ объединяй пункты и не додумывай связь между
  ними, даже если она кажется логичной. Объединяй только если пользователь сам
  явно связал их словами "для", "к", "чтобы".
- due_time заполняй, только если время названо явно ("в 18:00", "к 9 утра",
  "в полдень"). Если время не упомянуто — due_time = null, даже если приоритет
  высокий или дата известна.
- duration_minutes: по умолчанию 60 (один час). Если пользователь указал
  длительность или диапазон явно — используй его: "с 15 до 17" → due_time
  "15:00", duration_minutes 120; "на полчаса" → 30; "на 2 часа" → 120.
  Если время не указано вообще — duration_minutes всё равно ставь 60
  (используется только когда due_time тоже есть).

  ПРИМЕР (обязательно следуй этой логике буквально):
  Сообщение: "постирать, в 18 созвон, срочно подготовить КП"
  Правильно — ТРИ отдельные задачи:
    1) {{"task": "постирать", "due_time": null, ...}}
    2) {{"task": "созвон", "due_time": "18:00", "due_date_confidence": "exact", ...}}
    3) {{"task": "подготовить КП", "due_time": null, "priority": "высокий", ...}}
  НЕПРАВИЛЬНО — объединять пункты 2 и 3 в "подготовить КП к созвону в 18:00".
  Это грубая ошибка, даже если связь кажется логичной по смыслу.
- Даты: точная → "exact"; размытая ("на днях","скоро") → "approximate",
  due_date = сегодня+3 дня; не указана → "none", due_date = null.
- Приоритет: "высокий" — если есть слова срочно/горит/дедлайн/важно/asap;
  "низкий" — если явно "как-нибудь", "не срочно", "между делом"; иначе "средний".

Ссылки на задачи/напоминания без явного id ("отметь звонок как сделанный",
"поздравила Машу") — находи по смыслу в списках выше или по истории диалога.

Правила для list_action:
- "добавь в покупки молоко, хлеб и яйца" → ТРИ отдельных action=add с одним
  и тем же list_name="Покупки" — как и с задачами, не объединяй перечисленное
  через запятую в один пункт.
- Если пользователь не назвал список явно, но по смыслу понятно, о каком
  списке речь (например, единственный существующий список про покупки) —
  используй его. Если неоднозначно — создай новый список с логичным названием.

Верни ТОЛЬКО валидный JSON."""


def interpret_message(user_message: str, chat_history=None, open_tasks=None,
                       open_reminders=None, lists_with_items=None) -> dict:
    today = date.today()
    weekday_ru = ["понедельник", "вторник", "среда", "четверг",
                  "пятница", "суббота", "воскресенье"][today.weekday()]

    system_prompt = INTERPRET_SYSTEM_PROMPT.format(
        today=today.isoformat(),
        weekday_ru=weekday_ru,
        history=_format_history(chat_history),
        open_tasks=_format_open_tasks(open_tasks),
        open_reminders=_format_open_reminders(open_reminders),
        lists=_format_lists(lists_with_items)
    )

    response = _call_with_retry(lambda: client.chat.complete(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        response_format={"type": "json_object"}
    ))

    raw_text = response.choices[0].message.content.strip()
    raw_text = raw_text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return {"intent": "chat"}


# ---------------------------------------------------------------------------
# ШАГ 2: сформулировать обычный текстовый ответ
# ---------------------------------------------------------------------------

REPLY_SYSTEM_PROMPT = """Ты — дружелюбный личный ассистент по задачам и напоминаниям, ведёшь связный
диалог (ниже история последних сообщений — учитывай её).

Не показывай пользователю JSON, id из базы, служебные поля — только естественный
разговорный текст на русском.

- Добавление задач: кратко подтверди, срок — обычными словами ("завтра", "в пятницу").
- Запрос списка: перечисли живым языком, важное — вперёд. Пустой список — скажи по-доброму.
- Изменение/завершение задачи: подтверди, что именно изменено. Если задача
  отмечена выполненной — можно похвалить коротко ("Отлично, отметила!").
- Удаление задачи: подтверди, что задача удалена НАСОВСЕМ (не путай с
  "выполнено" в формулировке).
- Добавление напоминания: подтверди коротко, что запомнил, и как именно будешь
  напоминать (например: "Запомнила — день рождения Маши 12 августа, напомню
  за неделю, накануне и в день").
- Подтверждение напоминания ("поздравила Машу"): подтверди коротко, скажи,
  что напомнишь снова через год (если это ежегодное) или что всё, готово
  (если разовое).
- Действие со списком: подтверди коротко, что добавлено/отмечено/удалено,
  и в каком списке (например: "Добавила молоко и хлеб в список «Покупки»").
- Не хватило контекста понять, о какой задаче/напоминании/пункте списка
  речь — задай один уточняющий вопрос.

--- ИСТОРИЯ ДИАЛОГА ---
{history}
--- конец истории ---"""


def formulate_reply(user_message: str, interpretation: dict, chat_history=None,
                     tasks_from_db=None, update_results=None, delete_results=None,
                     reminder_results=None, confirmation_results=None,
                     list_results=None) -> str:
    today = date.today().isoformat()

    context = {
        "сегодняшняя_дата": today,
        "сообщение_пользователя": user_message,
        "результат_анализа": interpretation,
        "задачи_из_базы": tasks_from_db if tasks_from_db is not None else "не запрашивались",
        "результат_изменений": update_results if update_results is not None else "не применялись",
        "результат_удалений": delete_results if delete_results is not None else "не применялись",
        "результат_напоминаний": reminder_results if reminder_results is not None else "не создавались",
        "результат_подтверждений": confirmation_results if confirmation_results is not None else "не применялись",
        "результат_списков": list_results if list_results is not None else "не применялись",
    }

    system_prompt = REPLY_SYSTEM_PROMPT.format(history=_format_history(chat_history))

    response = _call_with_retry(lambda: client.chat.complete(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Данные для формулировки ответа:\n{json.dumps(context, ensure_ascii=False, indent=2)}"}
        ]
    ))

    return response.choices[0].message.content.strip()
