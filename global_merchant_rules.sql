-- Review and run manually in the Supabase SQL editor after approval.
-- This file has not been executed automatically.

create table if not exists public.global_merchant_rules (
    merchant text primary key,
    category text not null,
    source text not null default 'ai',
    created_at timestamptz not null default now()
);

alter table public.global_merchant_rules enable row level security;

create policy "Authenticated users can read global merchant rules"
on public.global_merchant_rules
for select
to authenticated
using (true);

create policy "Authenticated users can add global merchant rules"
on public.global_merchant_rules
for insert
to authenticated
with check (true);
