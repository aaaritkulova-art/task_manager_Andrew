-- schema.sql
--
-- Этот файл нужно выполнить ОДИН РАЗ в Supabase: Project -> SQL Editor -> New query,
-- вставить весь текст ниже и нажать Run.
--
-- ЕСЛИ У ВАС УЖЕ ЕСТЬ ТАБЛИЦЫ tasks и messages (база создавалась раньше) —
-- выполнять весь файл заново не страшно: "create table if not exists" ничего
-- не сломает и не удалит существующие данные, просто добавит недостающую
-- таблицу reminders.
--
-- Создаёт две таблицы:
--   tasks    — сами задачи
--   messages — история чата (нужна для "памяти" ассистента)
--
-- user_id — это ваш личный "ключ доступа" (придумываете сами в приложении),
-- по нему данные отделяются от чужих, если вдруг вы дадите этот проект
-- кому-то ещё для их собственного использования.

create table if not exists tasks (
    id bigint generated always as identity primary key,
    user_id text not null,
    task text not null,
    due_date date,
    due_time time,                 -- время, если указано явно ("к 18:00")
    duration_minutes integer default 60,  -- длительность, по умолчанию 1 час
    due_date_confidence text,      -- 'exact' | 'approximate' | 'none'
    priority text default 'средний',
    category text,
    status text default 'новая',   -- 'новая' | 'в работе' | 'готово'
    source text default 'ai',      -- 'ai' | 'manual'
    created_at timestamptz default now()
);

-- Для тех, у кого таблица tasks уже существовала до добавления времени —
-- эти команды безопасно добавят колонки, если их ещё нет:
alter table tasks add column if not exists due_time time;
alter table tasks add column if not exists duration_minutes integer default 60;

create table if not exists messages (
    id bigint generated always as identity primary key,
    user_id text not null,
    role text not null,            -- 'user' | 'assistant'
    content text not null,
    created_at timestamptz default now()
);

-- Индексы для быстрой выборки "мои задачи / мои сообщения"
create index if not exists idx_tasks_user on tasks (user_id);
create index if not exists idx_messages_user on messages (user_id, created_at);

create table if not exists reminders (
    id bigint generated always as identity primary key,
    user_id text not null,
    title text not null,
    event_date date not null,           -- дата события (день рождения, встреча, дедлайн)
    event_time time,                    -- время, если указано ("в 20:00")
    recurrence text default 'once',     -- 'once' | 'yearly' (дни рождения = 'yearly')
    lead_days integer[] default '{1}',  -- за сколько дней напоминать заранее, напр. {7,1}
    remind_on_day boolean default true, -- напоминать в сам день события
    repeat_on_day boolean default false,-- "нагонять" каждый день ПОСЛЕ дня события,
                                         -- пока не подтверждено (нужно для дней рождения —
                                         -- если забыли поздравить вовремя)
    status text default 'active',       -- 'active' | 'done' (для разовых)
    last_confirmed_year integer,        -- для 'yearly' — год последнего подтверждения
    created_at timestamptz default now()
);

create index if not exists idx_reminders_user on reminders (user_id);

create table if not exists categories (
    id bigint generated always as identity primary key,
    user_id text not null,
    name text not null,
    created_at timestamptz default now(),
    unique(user_id, name)
);

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

-- Отключаем RLS на всех таблицах — данные разделяются по user_id внутри
-- кода приложения, а не средствами самой базы (см. пояснение в конце файла).
-- Выполнять безопасно повторно, даже если уже отключали вручную раньше.
alter table tasks disable row level security;
alter table messages disable row level security;
alter table reminders disable row level security;
alter table categories disable row level security;
alter table lists disable row level security;
alter table list_items disable row level security;

-- ВАЖНО: на этом этапе Row Level Security (RLS) отключена — данные
-- разделяются только по полю user_id внутри кода приложения, без
-- настоящей авторизации на уровне базы. Это осознанное упрощение для
-- личного использования.
alter table tasks disable row level security;
alter table messages disable row level security;
alter table reminders disable row level security;
alter table categories disable row level security;
alter table lists disable row level security;
alter table list_items disable row level security;
