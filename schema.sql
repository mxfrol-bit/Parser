-- ════════════════════════════════════════════════════════════════════
--  СНАБЖЕНЕЦ · Полная схема Supabase
--  Вставить целиком в SQL Editor → Run
-- ════════════════════════════════════════════════════════════════════

create extension if not exists pg_trgm;

-- ── Пользователи ─────────────────────────────────────────────────────
create table if not exists users (
  telegram_id    bigint primary key,
  username       text    default '',
  name           text    not null,
  role           text    not null check (role in ('snabzhenets','manager','admin')),
  city           text    not null default '',
  supplier_name  text    default '',
  created_at     timestamptz default now()
);

-- МИГРАЦИЯ (если таблица уже есть — добавляет новые колонки)
alter table users add column if not exists supplier_name text default '';
alter table users drop constraint if exists users_role_check;
alter table users add constraint users_role_check
  check (role in ('snabzhenets','manager','admin'));

-- ── Прайсы (полная история) ───────────────────────────────────────────
create table if not exists prices (
  id               uuid    default gen_random_uuid() primary key,
  product          text    not null,
  product_original text,
  category         text,
  density          numeric(8,2),
  width            numeric(6,2),
  roll_length      numeric(8,2),
  thickness        numeric(6,3),
  color            text,
  material         text,
  price            numeric(12,2) not null,
  unit             text    default 'м²',
  price_per_roll   numeric(12,2),
  supplier_id      bigint  references users(telegram_id) on delete set null,
  supplier_name    text,
  city             text,
  price_date       date    not null,
  source_file      text,
  uploaded_at      timestamptz default now()
);

-- ── Индексы ───────────────────────────────────────────────────────────
create index if not exists prices_product_trgm  on prices using gin (product gin_trgm_ops);
create index if not exists prices_category_idx  on prices (category);
create index if not exists prices_city_idx      on prices (city);
create index if not exists prices_date_idx      on prices (price_date desc);
create index if not exists prices_supplier_idx  on prices (supplier_id);
create index if not exists prices_uploaded_idx  on prices (uploaded_at desc);

-- ── View: актуальные цены ─────────────────────────────────────────────
create or replace view prices_latest as
select distinct on (product, city, supplier_id)
  id, product, product_original, category,
  density, width, roll_length, thickness, color, material,
  price, unit, price_per_roll,
  supplier_id, supplier_name, city,
  price_date, uploaded_at
from prices
order by product, city, supplier_id, price_date desc, uploaded_at desc;

-- ── View: динамика цен ────────────────────────────────────────────────
create or replace view prices_history as
select
  product, category, city, supplier_name,
  price, unit, price_date,
  lag(price) over (
    partition by product, city, supplier_id
    order by price_date
  ) as prev_price
from prices
order by price_date desc;

-- ── RLS ───────────────────────────────────────────────────────────────
alter table users  enable row level security;
alter table prices enable row level security;

drop policy if exists "service all users"  on users;
drop policy if exists "service all prices" on prices;
create policy "service all users"  on users  for all using (true);
create policy "service all prices" on prices for all using (true);
