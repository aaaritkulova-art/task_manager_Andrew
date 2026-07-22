-- schema.sql
--
-- Выполнить в Supabase: Project -> SQL Editor -> New query, вставить весь
-- текст и нажать Run. Безопасно выполнять повторно.
--
-- МОДЕЛЬ ДАННЫХ: понятия "напоминание" как отдельной сущности больше нет.
-- Есть только ЗАДАЧИ, у каждой есть task_type — один из трёх типов:
--
--   'event'   🕑  точная дата И время (приём врача, созвон, вылет самолёта)
--             — по умолчанию без напоминаний, приоритет по умолчанию высокий
--
--   'marker'  🔔  точная дата, время не обязательно: то, что нужно не забыть
--             (день рождения, день отъезда, срок оплаты счёта)
--             — по умолчанию напоминает накануне и в день, приоритет низкий
--
--   'regular' —  обычная задача, дата не обязательна, точного времени нет
--             — по умолчанию без напоминаний, приоритет средний, может
--               "переезжать" на сегодня, если срок прошёл и не выполнена

create table if not exists tasks (
    id bigint generated always as identity primary key,
    user_id text not null,
    task text not null,
    task_type text default 'regular',   -- 'event' | 'marker' | 'regular'
    due_date date,
    due_time time,
    duration_minutes integer default 60,
    due_date_confidence text,           -- 'exact' | 'approximate' | 'none'
    recurrence text,                    -- 'once' | 'yearly' — только для marker
    lead_days integer[] default '{1}',  -- за сколько дней напоминать — только marker
    remind_on_day boolean default true, -- только marker
    repeat_on_day boolean default false,-- "нагонять" пока не подтверждено — только marker
    last_confirmed_year integer,        -- для marker с recurrence='yearly'
    priority text default 'средний',
    category text,
    status text default 'новая',        -- 'новая' | 'в работе' | 'готово'
    source text default 'ai',           -- 'ai' | 'manual'
    created_at timestamptz default now()
);

-- Миграция для тех, у кого таблица tasks уже существовала до этой версии:
alter table tasks add column if not exists due_time time;
alter table tasks add column if not exists duration_minutes integer default 60;
alter table tasks add column if not exists task_type text default 'regular';
alter table tasks add column if not exists recurrence text;
alter table tasks add column if not exists lead_days integer[] default '{1}';
alter table tasks add column if not exists remind_on_day boolean default true;
alter table tasks add column if not exists repeat_on_day boolean default false;
alter table tasks add column if not exists last_confirmed_year integer;

-- Задачи, у которых уже указано точное время, но тип ещё не проставлен —
-- по определению это тип "event". Безопасно выполнять повторно.
update tasks set task_type = 'event' where due_time is not null and task_type = 'regular';

create table if not exists messages (
    id bigint generated always as identity primary key,
    user_id text not null,
    role text not null,
    content text not null,
    created_at timestamptz default now()
);

create index if not exists idx_tasks_user on tasks (user_id);
create index if not exists idx_messages_user on messages (user_id, created_at);

create table if not exists categories (
    id bigint generated always as identity primary key,
    user_id text not null,
    name text not null,
    default_priority text,  -- null = нет переопределения; иначе
                             -- 'высокий'|'средний'|'низкий' — новые задачи
                             -- этой категории получают приоритет не ниже этого
    created_at timestamptz default now(),
    unique(user_id, name)
);

alter table categories add column if not exists default_priority text;

create table if not exists lists (
    id bigint generated always as identity primary key,
    user_id text not null,
    name text not null,
    created_at timestamptz default now(),
    unique(user_id, name)
);

create table if not exists list_items (
    id bigint generated always as identity primary key,
    list_id bigint references lists(id) on delete cascade,
    user_id text not null,
    content text not null,
    checked boolean default false,
    created_at timestamptz default now()
);

create index if not exists idx_list_items_list on list_items (list_id);

-- ---------------------------------------------------------------------------
-- ЛЕГАСИ: старая таблица reminders (из прошлой версии приложения). Код
-- приложения больше её не использует — вместо неё задачи типа 'marker' в
-- единой таблице tasks. Если у вас уже была эта таблица с данными
-- (например, от тестировщиков) — следующий блок ОДИН РАЗ переносит их в
-- tasks и помечает перенесённые строки, чтобы не задублировать при
-- повторном запуске этого файла.
-- ---------------------------------------------------------------------------

create table if not exists reminders (
    id bigint generated always as identity primary key,
    user_id text not null,
    title text not null,
    event_date date not null,
    event_time time,
    recurrence text default 'once',
    lead_days integer[] default '{1}',
    remind_on_day boolean default true,
    repeat_on_day boolean default false,
    status text default 'active',
    last_confirmed_year integer,
    category text,
    priority text default 'низкий',
    created_at timestamptz default now()
);

alter table reminders add column if not exists category text;
alter table reminders add column if not exists priority text default 'низкий';
alter table reminders add column if not exists migrated_to_tasks boolean default false;

insert into tasks (user_id, task, due_date, due_time, duration_minutes,
                    due_date_confidence, priority, category, status, source,
                    task_type, recurrence, lead_days, remind_on_day,
                    repeat_on_day, last_confirmed_year, created_at)
select
    user_id, title, event_date, event_time, 60,
    'exact', coalesce(priority, 'низкий'), coalesce(category, ''),
    case when status = 'done' then 'готово' else 'новая' end,
    'ai',
    'marker', recurrence, lead_days, remind_on_day,
    repeat_on_day, last_confirmed_year, created_at
from reminders
where migrated_to_tasks = false or migrated_to_tasks is null;

update reminders set migrated_to_tasks = true where migrated_to_tasks = false or migrated_to_tasks is null;

create index if not exists idx_reminders_user on reminders (user_id);

-- ---------------------------------------------------------------------------
-- Отключаем RLS на всех таблицах — данные разделяются по user_id внутри
-- кода приложения, а не средствами самой базы. Осознанное упрощение для
-- личного использования и тестирования, безопасно выполнять повторно.
-- ---------------------------------------------------------------------------
alter table tasks disable row level security;
alter table messages disable row level security;
alter table reminders disable row level security;
alter table categories disable row level security;
alter table lists disable row level security;
alter table list_items disable row level security;
