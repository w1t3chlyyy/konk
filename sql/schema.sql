-- Схема для Supabase (Postgres)

create table contests (
    id            bigserial primary key,
    ref_code      text unique not null,          -- код в реф-ссылке t.me/bot?start=c_<ref_code>
    title         text not null,
    description   text,
    status        text not null default 'draft',  -- draft | active | finished
    deadline_at   timestamptz not null,
    created_at    timestamptz not null default now()
);

create table prize_places (
    id            bigserial primary key,
    contest_id    bigint not null references contests(id) on delete cascade,
    place_number  int not null,                  -- 1, 2, 3 ...
    prize_text    text not null,
    capacity      int,                           -- null = без ограничений (может влезть сколько угодно)
    unique (contest_id, place_number)
);

create table conditions (
    id            bigserial primary key,
    contest_id    bigint not null references contests(id) on delete cascade,
    type          text not null,                 -- 'auto_channel_sub' | 'manual_screenshot'
    description   text not null,                 -- текст для пользователя
    channel_id    bigint,                         -- для auto_channel_sub
    sort_order    int not null default 0
);

create table participants (
    id                bigserial primary key,
    contest_id        bigint not null references contests(id) on delete cascade,
    user_id           bigint not null,
    username          text,
    status            text not null default 'checking', -- checking | confirmed | rejected
    joined_at         timestamptz not null default now(),
    confirmed_at      timestamptz,
    assigned_place    int,
    unique (contest_id, user_id)
);

create table condition_checks (
    id               bigserial primary key,
    participant_id   bigint not null references participants(id) on delete cascade,
    condition_id     bigint not null references conditions(id) on delete cascade,
    status           text not null default 'pending', -- pending | approved | rejected
    file_id          text,                              -- telegram file_id скриншота (если применимо)
    reviewed_at      timestamptz,
    unique (participant_id, condition_id)
);

create index idx_participants_contest on participants(contest_id);
create index idx_condition_checks_status on condition_checks(status);
