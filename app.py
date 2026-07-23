"""
app.py — интерфейс приложения (облачная версия).

Запуск:  streamlit run app.py

МОДЕЛЬ ДАННЫХ: одна таблица задач, три типа (task_type):
  🕑 event   — точная дата и время
  🔔 marker  — точная дата, важно не забыть
     regular — обычная задача, без иконки

При первом открытии просит "ключ доступа" — любое слово, которое вы
придумываете сами и вводите на каждом устройстве, чтобы попадать в одни
и те же данные (это не настоящий пароль, см. README про ограничения).
"""

import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta

import db
import assistant

st.set_page_config(page_title="Ассистент задач", page_icon="✅", layout="centered")

PRIORITY_DOT = {"высокий": "🔴", "средний": "🟡", "низкий": "🟢"}
TYPE_ICON = {"event": "🕑", "marker": "🔔", "regular": ""}
WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_RU = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
             'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']


# ---------------------------------------------------------------------------
# ВХОД ПО КЛЮЧУ ДОСТУПА
# ---------------------------------------------------------------------------

if "user_id" not in st.session_state:
    st.session_state.user_id = None

if not st.session_state.user_id:
    st.title("✅ Ассистент задач")
    st.write("Введите свой ключ доступа. Один и тот же ключ на всех устройствах "
             "даёт доступ к одним и тем же задачам.")
    key_input = st.text_input("Ключ доступа", type="password",
                               help="Придумайте любое слово при первом входе и используйте его дальше")
    if st.button("Войти") and key_input.strip():
        st.session_state.user_id = key_input.strip()
        st.rerun()
    st.stop()

user_id = st.session_state.user_id


# ---------------------------------------------------------------------------
# ЗАГРУЗКА ИСТОРИИ ЧАТА ИЗ БАЗЫ (память между сессиями и устройствами)
# ---------------------------------------------------------------------------

if "chat_history" not in st.session_state:
    st.session_state.chat_history = db.get_recent_messages(user_id, limit=30)

st.title("✅ Ассистент задач")
st.caption(f"Вы вошли как: {user_id}")


# ---------------------------------------------------------------------------
# НАВИГАЦИЯ (сайдбар) — Чат / Ежедневник / Категории / Списки / Выйти
# ---------------------------------------------------------------------------

if "view" not in st.session_state:
    st.session_state.view = "chat"

st.sidebar.button(
    "💬 Чат", use_container_width=True,
    type="primary" if st.session_state.view == "chat" else "secondary",
    on_click=lambda: st.session_state.update(view="chat")
)
st.sidebar.button(
    "📅 Ежедневник", use_container_width=True,
    type="primary" if st.session_state.view == "table" else "secondary",
    on_click=lambda: st.session_state.update(view="table")
)

st.sidebar.divider()

st.sidebar.markdown("**Категории**")
categories = db.get_categories(user_id)

for cat in categories:
    is_active = st.session_state.view == "category" and st.session_state.get("active_category") == cat
    if st.sidebar.button(f"🏷️ {cat}", use_container_width=True,
                          type="primary" if is_active else "secondary", key=f"cat_btn_{cat}"):
        st.session_state.view = "category"
        st.session_state.active_category = cat
        st.rerun()

if st.sidebar.button("➕ Категория", use_container_width=True, key="add_cat_btn"):
    st.session_state.creating_category = not st.session_state.get("creating_category", False)

if st.session_state.get("creating_category"):
    with st.sidebar.form("new_category_form", clear_on_submit=True):
        new_cat_name = st.text_input("Название категории", label_visibility="collapsed",
                                      placeholder="Например: работа")
        if st.form_submit_button("Создать", use_container_width=True) and new_cat_name.strip():
            db.ensure_category_exists(user_id, new_cat_name.strip())
            st.session_state.view = "category"
            st.session_state.active_category = new_cat_name.strip()
            st.session_state.creating_category = False
            st.rerun()

st.sidebar.divider()

st.sidebar.markdown("**Списки**")
lists = db.get_lists(user_id)
list_options = [l["name"] for l in lists] + ["➕ Новый список"]


def _on_list_select():
    selection = st.session_state.get("list_selector")
    if selection == "➕ Новый список":
        st.session_state.creating_list = True
    elif selection:
        matched = next((l for l in lists if l["name"] == selection), None)
        if matched:
            st.session_state.view = "list"
            st.session_state.active_list_id = matched["id"]
            st.session_state.creating_list = False


st.sidebar.selectbox(
    "Списки", list_options, index=None, placeholder="Выбрать список…",
    label_visibility="collapsed", key="list_selector", on_change=_on_list_select
)

if st.session_state.get("creating_list"):
    with st.sidebar.form("new_list_form", clear_on_submit=True):
        new_list_name = st.text_input("Название списка", label_visibility="collapsed",
                                       placeholder="Например: покупки")
        if st.form_submit_button("Создать", use_container_width=True) and new_list_name.strip():
            created = db.add_list(user_id, new_list_name.strip())
            st.session_state.view = "list"
            st.session_state.active_list_id = created["id"]
            st.session_state.creating_list = False
            st.rerun()

st.sidebar.divider()

if st.sidebar.button("Выйти", use_container_width=True):
    st.session_state.user_id = None
    st.session_state.chat_history = []
    st.rerun()


# ---------------------------------------------------------------------------
# БАННЕР С АКТУАЛЬНЫМИ MARKER-ЗАДАЧАМИ — считается при каждом открытии
# ---------------------------------------------------------------------------

due_markers = db.compute_due_markers(user_id, date.today())
if due_markers:
    lines = []
    for item in due_markers:
        m = item["task"]
        when = {"заранее": f"через {abs(item['days_offset'])} дн.",
                "сегодня": "сегодня",
                "просрочено": f"было {abs(item['days_offset'])} дн. назад — не забудьте подтвердить"}[item["kind"]]
        time_part = f" в {str(m['due_time'])[:5]}" if m.get("due_time") else ""
        lines.append(f"🔔 **{m['task']}**{time_part} — {when}")
    st.warning("\n\n".join(lines))


# ---------------------------------------------------------------------------
# ВИД: ЧАТ
# ---------------------------------------------------------------------------

if st.session_state.view == "chat":

    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    user_input = st.chat_input("Напишите задачу или спросите про планы…")

    if user_input:
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        db.save_message(user_id, "user", user_input)

        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            with st.spinner("Думаю…"):

                open_tasks = db.get_open_tasks_summary(user_id)
                lists_with_items = db.get_lists_with_items(user_id)
                existing_categories = db.get_categories(user_id)
                history_for_context = st.session_state.chat_history[:-1][-20:]

                interpretation = assistant.interpret_message(
                    user_message=user_input,
                    chat_history=history_for_context,
                    open_tasks=open_tasks,
                    lists_with_items=lists_with_items,
                    categories=existing_categories
                )
                intent = interpretation.get("intent", "chat")

                tasks_from_db = None
                update_results = None
                delete_results = None
                category_priority_result = None
                list_results = None

                # Одно сообщение может содержать НЕСКОЛЬКО разных действий сразу
                # (например "закончила задачу X, а ещё нужно Y и Z") — поэтому
                # обрабатываем каждый непустой блок из ответа модели независимо,
                # а не только тот, что указан в "intent" (он используется лишь
                # для query, у которой нет своего массива).

                if interpretation.get("tasks"):
                    for t in interpretation["tasks"]:
                        db.add_task(
                            user_id=user_id,
                            task=t.get("task", "").strip(),
                            task_type=t.get("task_type", "regular"),
                            due_date=t.get("due_date"),
                            due_time=t.get("due_time"),
                            duration_minutes=t.get("duration_minutes", 60),
                            due_date_confidence=t.get("due_date_confidence", "none"),
                            recurrence=t.get("recurrence"),
                            lead_days=t.get("lead_days"),
                            priority=t.get("priority"),
                            category=t.get("category", ""),
                            source="ai"
                        )

                if interpretation.get("updates"):
                    update_results = []
                    for u in interpretation["updates"]:
                        task_id = u.get("task_id")
                        changes = dict(u.get("changes", {}))
                        if not task_id or not changes:
                            continue
                        if changes.get("status") == "готово":
                            db.complete_task(user_id, task_id, date.today().year)
                            changes.pop("status")
                        if changes:
                            db.update_task(user_id, task_id, **changes)
                        update_results.append({"task_id": task_id, "changes": u.get("changes", {})})

                if interpretation.get("deletes"):
                    delete_results = []
                    for d in interpretation["deletes"]:
                        task_id = d.get("task_id")
                        if task_id:
                            db.delete_task(user_id, task_id)
                            delete_results.append({"task_id": task_id})

                if interpretation.get("category_priority"):
                    cp = interpretation["category_priority"]
                    if cp.get("category") and cp.get("priority"):
                        db.set_category_priority(user_id, cp["category"].strip(), cp["priority"])
                        category_priority_result = cp

                if interpretation.get("list_actions"):
                    list_results = []
                    for la in interpretation["list_actions"]:
                        action = la.get("action")
                        list_name = la.get("list_name", "").strip()
                        item_text = la.get("item", "").strip()
                        if not list_name:
                            continue
                        list_id = db.find_or_create_list(user_id, list_name)

                        if action == "add" and item_text:
                            db.add_list_item(user_id, list_id, item_text)
                            list_results.append({"action": "add", "list": list_name, "item": item_text})

                        elif action in ("check", "delete"):
                            existing_items = db.get_list_items(user_id, list_id)
                            match = next(
                                (i for i in existing_items
                                 if item_text.lower() in i["content"].lower()
                                 or i["content"].lower() in item_text.lower()),
                                None
                            )
                            if match:
                                if action == "check":
                                    db.update_list_item(user_id, match["id"], True)
                                else:
                                    db.delete_list_item(user_id, match["id"])
                                list_results.append({"action": action, "list": list_name, "item": match["content"]})

                if intent == "query":
                    tasks_from_db = db.get_tasks(
                        user_id=user_id,
                        date_from=interpretation.get("query_date_from"),
                        date_to=interpretation.get("query_date_to")
                    )

                reply_text = assistant.formulate_reply(
                    user_message=user_input,
                    interpretation=interpretation,
                    chat_history=history_for_context,
                    tasks_from_db=tasks_from_db,
                    update_results=update_results,
                    delete_results=delete_results,
                    category_priority_result=category_priority_result,
                    list_results=list_results
                )

                st.markdown(reply_text)

        st.session_state.chat_history.append({"role": "assistant", "content": reply_text})
        db.save_message(user_id, "assistant", reply_text)

        if (interpretation.get("tasks") or interpretation.get("list_actions")
                or interpretation.get("deletes") or interpretation.get("category_priority")):
            # новая категория/список могли появиться — обновляем сайдбар
            st.rerun()


# ---------------------------------------------------------------------------
# ФУНКЦИЯ ОТРИСОВКИ ЕЖЕДНЕВНИКА — переиспользуется для полного вида и
# для вида, отфильтрованного по категории
# ---------------------------------------------------------------------------

def _format_time_range(due_time, duration_minutes):
    if not due_time:
        return "—"
    time_str = str(due_time)[:5]
    try:
        start_dt = datetime.strptime(time_str, "%H:%M")
    except ValueError:
        return time_str
    end_dt = start_dt + timedelta(minutes=duration_minutes or 60)
    return f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"


def _mark_done(task_id):
    db.complete_task(user_id, task_id, date.today().year)


def _mark_undone(task_id):
    db.update_task(user_id, task_id, status="новая")


def render_planner(key_prefix, category=None):
    """Рисует задачи всех трёх типов, сгруппированные по датам (display_date
    с учётом переноса просроченных regular-задач и годового цикла marker).
    category=None — полный ежедневник; иначе только эта категория."""

    show_completed = st.checkbox("Показывать выполненные задачи", value=False,
                                  key=f"{key_prefix}_show_completed")

    tasks = db.get_tasks_for_planner(user_id, date.today(), category=category)
    if not show_completed:
        tasks = [t for t in tasks if t.get("status") != "готово"]

    if tasks:
        by_date = {}
        no_date = []
        for t in tasks:
            if t.get("display_date"):
                by_date.setdefault(t["display_date"], []).append(t)
            else:
                no_date.append(t)

        for tasks_list in by_date.values():
            tasks_list.sort(key=lambda t: (t.get("task_type") != "event", t.get("due_time") or "99:99"))

        today_str = date.today().isoformat()

        if no_date:
            st.markdown("#### 📌 Без срока")
            for t in no_date:
                cols = st.columns([0.06, 0.5, 0.15, 0.19, 0.1])
                cols[0].checkbox("", value=(t["status"] == "готово"), key=f"{key_prefix}_nd_{t['id']}",
                                  on_change=(lambda tid=t["id"], s=t["status"]:
                                             _mark_undone(tid) if s == "готово" else _mark_done(tid)))
                cols[1].write(t["task"])
                cols[2].write(PRIORITY_DOT.get(t.get("priority"), "⚪") + " " + (t.get("priority") or ""))
                cols[3].write(t.get("category") or "—")
            st.divider()

        for d_str in sorted(by_date.keys()):
            d = date.fromisoformat(d_str)
            label = f"📅 {WEEKDAYS_RU[d.weekday()].capitalize()}, {d.day} {MONTHS_RU[d.month-1]}"
            if d_str == today_str:
                label += "  — сегодня"
            st.markdown(f"#### {label}")

            for t in by_date[d_str]:
                icon = TYPE_ICON.get(t.get("task_type"), "")
                cols = st.columns([0.06, 0.16, 0.34, 0.15, 0.19, 0.1])
                cols[0].checkbox("", value=(t["status"] == "готово"), key=f"{key_prefix}_d_{t['id']}",
                                  on_change=(lambda tid=t["id"], s=t["status"]:
                                             _mark_undone(tid) if s == "готово" else _mark_done(tid)))
                if t.get("task_type") == "event":
                    cols[1].write(f"{icon} {_format_time_range(t.get('due_time'), t.get('duration_minutes'))}")
                elif t.get("task_type") == "marker":
                    time_part = f" {str(t['due_time'])[:5]}" if t.get("due_time") else ""
                    cols[1].write(f"{icon}{time_part}")
                else:
                    cols[1].write("—")
                task_text = t["task"]
                if t.get("original_date"):
                    task_text += f"  _(с {t['original_date']})_"
                if t["status"] == "готово":
                    task_text = f"~~{task_text}~~"
                cols[2].write(task_text)
                cols[3].write(PRIORITY_DOT.get(t.get("priority"), "⚪") + " " + (t.get("priority") or ""))
                cols[4].write(t.get("category") or "—")
            st.divider()
    else:
        st.info("Пока нет ни одной задачи здесь. Добавьте через чат или вручную ниже.")

    with st.expander("➕ Добавить задачу вручную"):
        with st.form(f"{key_prefix}_manual_add_form", clear_on_submit=True):
            m_task = st.text_input("Задача")
            m_type = st.radio("Тип", ["regular", "event", "marker"], horizontal=True,
                               format_func=lambda x: {"regular": "Обычная", "event": "🕑 Точное время",
                                                       "marker": "🔔 Важная дата"}[x],
                               key=f"{key_prefix}_type")
            m_col1, m_col2, m_col3, m_col4 = st.columns(4)
            m_date = m_col1.date_input("Дата", value=None)
            m_time = m_col2.time_input("Время", value=None,
                                        disabled=(m_type != "event"))
            m_duration = m_col3.number_input("Длительность, мин", min_value=5, value=60, step=5,
                                              disabled=(m_type != "event"))
            m_priority = m_col4.selectbox("Приоритет (необязательно)",
                                           [None, "высокий", "средний", "низкий"],
                                           format_func=lambda x: "По умолчанию" if x is None else x)
            m_yearly = st.checkbox("Повторяется каждый год", value=False,
                                    disabled=(m_type != "marker"))
            m_category = st.text_input("Категория (необязательно)", value=category or "")
            if st.form_submit_button("Добавить") and m_task.strip():
                db.add_task(
                    user_id=user_id,
                    task=m_task.strip(),
                    task_type=m_type,
                    due_date=m_date.isoformat() if m_date else None,
                    due_time=m_time.strftime("%H:%M") if (m_time and m_type == "event") else None,
                    duration_minutes=int(m_duration),
                    due_date_confidence="exact" if m_date else "none",
                    recurrence=("yearly" if m_yearly else "once") if m_type == "marker" else None,
                    priority=m_priority,
                    category=m_category.strip(),
                    source="manual"
                )
                st.rerun()

    st.divider()

    export_df = pd.DataFrame(tasks)
    if not export_df.empty:
        csv_data = export_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "⬇️ Скачать на устройство (CSV)",
            data=csv_data,
            file_name=f"tasks_{key_prefix}_{date.today().isoformat()}.csv",
            mime="text/csv",
            key=f"{key_prefix}_download"
        )

    with st.expander("🛠️ Расширенное редактирование (вся таблица целиком)"):
        st.caption("Для массовых правок — здесь можно менять любое поле напрямую.")
        raw_df = pd.DataFrame(tasks)
        if not raw_df.empty:
            column_order = ["id", "task", "task_type", "due_date", "due_time", "duration_minutes",
                             "priority", "category", "status", "due_date_confidence", "source", "created_at"]
            raw_df = raw_df[[c for c in column_order if c in raw_df.columns]]
            edited_df = st.data_editor(
                raw_df,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "id": st.column_config.NumberColumn("ID", disabled=True),
                    "task": st.column_config.TextColumn("Задача", width="large"),
                    "task_type": st.column_config.SelectboxColumn(
                        "Тип", options=["event", "marker", "regular"]
                    ),
                    "due_date": st.column_config.TextColumn("Срок (YYYY-MM-DD)"),
                    "due_time": st.column_config.TextColumn("Время (HH:MM)"),
                    "priority": st.column_config.SelectboxColumn(
                        "Приоритет", options=["высокий", "средний", "низкий"]
                    ),
                    "status": st.column_config.SelectboxColumn(
                        "Статус", options=["новая", "в работе", "готово"]
                    ),
                    "due_date_confidence": st.column_config.SelectboxColumn(
                        "Точность даты", options=["exact", "approximate", "none"]
                    ),
                    "source": st.column_config.TextColumn("Источник", disabled=True),
                    "created_at": st.column_config.TextColumn("Создано", disabled=True),
                },
                key=f"{key_prefix}_task_editor"
            )
            if st.button("💾 Сохранить изменения (расширенная таблица)", key=f"{key_prefix}_save_advanced"):
                db.upsert_tasks_from_table(user_id, edited_df.to_dict("records"))
                st.success("Сохранено в облако")
                st.rerun()


# ---------------------------------------------------------------------------
# ВИД: ЕЖЕДНЕВНИК (все задачи)
# ---------------------------------------------------------------------------

if st.session_state.view == "table":
    st.caption("🕑 — точное время · 🔔 — важная дата · без иконки — обычная задача")
    render_planner(key_prefix="all")


# ---------------------------------------------------------------------------
# ВИД: КАТЕГОРИЯ
# ---------------------------------------------------------------------------

elif st.session_state.view == "category":
    cat = st.session_state.get("active_category", "")
    st.subheader(f"🏷️ {cat}")
    render_planner(key_prefix=f"cat_{cat}", category=cat)


# ---------------------------------------------------------------------------
# ВИД: СПИСОК
# ---------------------------------------------------------------------------

elif st.session_state.view == "list":
    list_id = st.session_state.get("active_list_id")
    current_list = next((l for l in db.get_lists(user_id) if l["id"] == list_id), None)

    if not current_list:
        st.info("Список не найден или был удалён. Выберите другой список в сайдбаре.")
    else:
        st.subheader(f"📝 {current_list['name']}")

        items = db.get_list_items(user_id, list_id)
        for item in items:
            cols = st.columns([0.08, 0.8, 0.12])
            new_checked = cols[0].checkbox("", value=item["checked"], key=f"li_{item['id']}")
            if new_checked != item["checked"]:
                db.update_list_item(user_id, item["id"], new_checked)
                st.rerun()
            text = item["content"]
            if item["checked"]:
                text = f"~~{text}~~"
            cols[1].write(text)
            if cols[2].button("✕", key=f"del_li_{item['id']}"):
                db.delete_list_item(user_id, item["id"])
                st.rerun()

        with st.form(f"add_item_form_{list_id}", clear_on_submit=True):
            new_item = st.text_input("Новый пункт", label_visibility="collapsed",
                                      placeholder="Добавить пункт…")
            if st.form_submit_button("➕ Добавить") and new_item.strip():
                db.add_list_item(user_id, list_id, new_item.strip())
                st.rerun()

        st.divider()
        if st.button("🗑️ Удалить список целиком"):
            db.delete_list(user_id, list_id)
            st.session_state.view = "chat"
            st.rerun()
