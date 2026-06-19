-- =============================================================================
-- Güzel Tahmin Gateway — Supabase Schema
-- Multi-Tenant SaaS Foundation
-- Run this in: Supabase Dashboard → SQL Editor → New Query
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 0. EXTENSIONS
-- ---------------------------------------------------------------------------
create extension if not exists "pgcrypto";
create extension if not exists "uuid-ossp";

-- ---------------------------------------------------------------------------
-- 1. TENANT CONTEXT FUNCTION
-- JWT claim tabanlı tenant izolasyonu.
-- n8n'den gelen isteklerde JWT'ye app_metadata.tenant_id eklenir.
-- ---------------------------------------------------------------------------
create or replace function public.current_tenant_id()
returns text
language sql
stable
as $$
  select nullif(
    coalesce(
      current_setting('request.jwt.claims', true)::json->>'tenant_id',
      current_setting('request.jwt.claims', true)::json->'app_metadata'->>'tenant_id'
    ),
    ''
  )
$$;

-- ---------------------------------------------------------------------------
-- 2. TENANTS
-- Her SaaS müşterisi (Güzel Tahmin, gelecekte başka servisler) bir tenant.
-- ---------------------------------------------------------------------------
create table public.tenants (
  id            text        primary key,               -- 'guzeltahmin', 'sporx_vip' vb.
  name          text        not null,
  telegram_channel_id  bigint,                         -- VIP kanal ID
  telegram_bot_token   text,                           -- Bu tenant'a ait bot token (şifreli sakla)
  plan          text        not null default 'active',
  settings      jsonb       not null default '{}',     -- Esnek config (fiyatlar, vs.)
  created_at    timestamptz not null default now()
);

-- Sadece service_role okuyabilir/yazabilir
alter table public.tenants enable row level security;

create policy "service_role_all" on public.tenants
  for all
  to service_role
  using (true)
  with check (true);

-- ---------------------------------------------------------------------------
-- 3. USERS
-- Telegram kullanıcıları. Her kayıt bir tenant'a bağlı.
-- ---------------------------------------------------------------------------
create table public.users (
  id              uuid        primary key default gen_random_uuid(),
  tenant_id       text        not null references public.tenants(id) on delete cascade,
  telegram_id     bigint      not null,
  telegram_username text,
  first_name      text,
  last_name       text,
  language_code   text,
  referred_by     text,                                -- affiliate kodu
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (tenant_id, telegram_id)
);

alter table public.users enable row level security;

create policy "service_role_all" on public.users
  for all to service_role
  using (true) with check (true);

create policy "tenant_all" on public.users
  for all to authenticated
  using (tenant_id = public.current_tenant_id())
  with check (tenant_id = public.current_tenant_id());

create index idx_users_tenant_telegram on public.users(tenant_id, telegram_id);

-- ---------------------------------------------------------------------------
-- 4. AFFILIATES
-- Referans sistemi. Her affiliate'in komisyon oranı ve kodu var.
-- ---------------------------------------------------------------------------
create table public.affiliates (
  id              uuid        primary key default gen_random_uuid(),
  tenant_id       text        not null references public.tenants(id) on delete cascade,
  code            text        not null,                -- 'ref_iddaa365'
  owner_telegram_id bigint,                            -- Affiliate sahibi
  commission_pct  numeric(5,2) not null default 20.00, -- %20 komisyon
  total_referrals int         not null default 0,
  total_earned_usd numeric(10,2) not null default 0,
  is_active       boolean     not null default true,
  created_at      timestamptz not null default now(),
  unique (tenant_id, code)
);

alter table public.affiliates enable row level security;

create policy "service_role_all" on public.affiliates
  for all to service_role
  using (true) with check (true);

create policy "tenant_all" on public.affiliates
  for all to authenticated
  using (tenant_id = public.current_tenant_id())
  with check (tenant_id = public.current_tenant_id());

create index idx_affiliates_code on public.affiliates(tenant_id, code);

-- ---------------------------------------------------------------------------
-- 5. LEGAL_LOGS
-- TOS kabul kaydı. Hukuki kanıt — asla silinmez.
-- ---------------------------------------------------------------------------
create table public.legal_logs (
  id              uuid        primary key default gen_random_uuid(),
  tenant_id       text        not null references public.tenants(id),
  telegram_id     bigint      not null,
  tos_version     text        not null,                -- 'v1.0', 'v1.1' vb.
  accepted_at     timestamptz not null default now(),  -- Tam timestamp (UTC)
  ip_country      text,                                -- Opsiyonel coğrafya
  callback_data   jsonb       not null default '{}'    -- Ham Telegram callback payload
);

alter table public.legal_logs enable row level security;

-- Hiçbir zaman update/delete yapılamaz — sadece insert
create policy "service_role_all" on public.legal_logs
  for all to service_role
  using (true) with check (true);

create policy "tenant_select" on public.legal_logs
  for select to authenticated
  using (tenant_id = public.current_tenant_id());

-- Silme yasağı — trigger ile enforce et
create or replace function public.prevent_legal_log_delete()
returns trigger language plpgsql as $$
begin
  raise exception 'Legal logs cannot be deleted or updated.';
end;
$$;

create trigger no_delete_legal_logs
  before delete or update on public.legal_logs
  for each row execute function public.prevent_legal_log_delete();

create index idx_legal_logs_tenant_user on public.legal_logs(tenant_id, telegram_id);

-- ---------------------------------------------------------------------------
-- 6. SUBSCRIPTIONS
-- Kullanıcının aktif/geçmiş abonelik kayıtları.
-- ---------------------------------------------------------------------------
create table public.subscriptions (
  id                  uuid        primary key default gen_random_uuid(),
  tenant_id           text        not null references public.tenants(id) on delete cascade,
  user_id             uuid        not null references public.users(id) on delete cascade,
  plan                text        not null check (plan in ('weekly', 'monthly')),
  status              text        not null default 'pending'
                                  check (status in ('pending', 'active', 'expired', 'cancelled')),
  premium_start_date  timestamptz,
  premium_end_date    timestamptz,
  invite_link         text,                            -- Telegram one-time link
  affiliate_code      text,                            -- Hangi affiliate ile geldi
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

alter table public.subscriptions enable row level security;

create policy "service_role_all" on public.subscriptions
  for all to service_role
  using (true) with check (true);

create policy "tenant_all" on public.subscriptions
  for all to authenticated
  using (tenant_id = public.current_tenant_id())
  with check (tenant_id = public.current_tenant_id());

create index idx_subscriptions_active on public.subscriptions(tenant_id, status, premium_end_date)
  where status = 'active';

create index idx_subscriptions_user on public.subscriptions(tenant_id, user_id);

-- ---------------------------------------------------------------------------
-- 7. TRANSACTIONS
-- Cryptomus ödeme kaydı. Her ödeme bir subscription'a bağlı.
-- ---------------------------------------------------------------------------
create table public.transactions (
  id                  uuid        primary key default gen_random_uuid(),
  tenant_id           text        not null references public.tenants(id) on delete cascade,
  subscription_id     uuid        not null references public.subscriptions(id),
  user_id             uuid        not null references public.users(id),
  cryptomus_order_id  text        unique,              -- Cryptomus order UUID
  cryptomus_payment_id text,                           -- Cryptomus ödeme ID
  amount_usd          numeric(10,2) not null,
  currency            text        not null default 'USDT',
  network             text        not null default 'TRON',
  status              text        not null default 'pending'
                                  check (status in ('pending', 'paid', 'failed', 'expired', 'refunded')),
  ipn_payload         jsonb       not null default '{}', -- Ham Cryptomus webhook verisi
  paid_at             timestamptz,
  created_at          timestamptz not null default now()
);

alter table public.transactions enable row level security;

create policy "service_role_all" on public.transactions
  for all to service_role
  using (true) with check (true);

create policy "tenant_select" on public.transactions
  for select to authenticated
  using (tenant_id = public.current_tenant_id());

create index idx_transactions_order on public.transactions(cryptomus_order_id);
create index idx_transactions_tenant_status on public.transactions(tenant_id, status);

-- ---------------------------------------------------------------------------
-- 8. UPDATED_AT TRIGGER (users, subscriptions)
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger trg_users_updated_at
  before update on public.users
  for each row execute function public.set_updated_at();

create trigger trg_subscriptions_updated_at
  before update on public.subscriptions
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- 9. SEED — Güzel Tahmin tenant (başlangıç verisi)
-- ---------------------------------------------------------------------------
insert into public.tenants (id, name, plan, settings)
values (
  'guzeltahmin',
  'Güzel Tahmin',
  'active',
  '{
    "prices": {
      "weekly_usd":  9.00,
      "monthly_usd": 29.00
    },
    "tos_version": "v1.0",
    "tos_text": "Bu platform istatistiksel veri analitiği yazılımı sunan bir ABD LLC tarafından işletilmektedir. Finansal tavsiye değildir. Kazanç garantisi verilmez. Tüm riski kullanıcı kabul eder."
  }'::jsonb
)
on conflict (id) do nothing;

-- =============================================================================
-- DONE. Tabloları kontrol et:
--   select table_name from information_schema.tables where table_schema = 'public';
-- =============================================================================
