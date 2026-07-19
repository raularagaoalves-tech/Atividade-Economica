-- ---------------------------------------------------------------------
-- Governança do Sistema Atividade Econômica — cadastro por e-mail/senha
-- com aprovação manual, e log de auditoria (login/logout/navegação).
--
-- Rodar este arquivo inteiro, uma vez, no SQL Editor do seu projeto
-- Supabase (supabase.com → seu projeto → SQL Editor → New query → colar
-- tudo → Run). Depois de rodar, veja o passo de bootstrap do admin no
-- final deste arquivo — é manual, só precisa ser feito uma vez.
--
-- auth.users já existe (gerenciada pelo Supabase Auth) — as tabelas
-- abaixo só guardam o que a Auth não guarda: status de aprovação, se é
-- administrador, e o histórico de eventos.
-- ---------------------------------------------------------------------

create table public.profiles (
    id          uuid primary key references auth.users(id) on delete cascade,
    email       text not null,
    status      text not null default 'pendente'
                check (status in ('pendente', 'aprovado', 'rejeitado')),
    is_admin    boolean not null default false,
    created_at  timestamptz not null default now(),
    approved_at timestamptz,
    approved_by uuid references auth.users(id)
);
create index ix_profiles_status on public.profiles (status);

create table public.audit_log (
    id         bigint generated always as identity primary key,
    user_id    uuid references auth.users(id) on delete set null,
    email      text not null,
    evento     text not null
               check (evento in ('login', 'logout', 'visita', 'cadastro', 'aprovacao', 'rejeicao')),
    detalhe    text,
    criado_em  timestamptz not null default now()
);
create index ix_audit_log_criado_em on public.audit_log (criado_em desc);
create index ix_audit_log_user on public.audit_log (user_id);

-- ---------------------------------------------------------------------
-- Trigger: toda vez que alguém se cadastra (auth.users ganha uma linha
-- nova), cria automaticamente o profile correspondente com status
-- 'pendente' — sem depender do cliente (navegador) fazer esse insert à
-- parte, o que poderia falhar/ser pulado.
-- ---------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
    insert into public.profiles (id, email) values (new.id, new.email);
    insert into public.audit_log (user_id, email, evento) values (new.id, new.email, 'cadastro');
    return new;
end;
$$;

create trigger on_auth_user_created
    after insert on auth.users
    for each row execute procedure public.handle_new_user();

-- ---------------------------------------------------------------------
-- RLS (Row Level Security) — a segurança de verdade mora aqui, não no
-- JavaScript da página (que qualquer um pode ler/editar). A anon key
-- usada no site é pública por design; o que protege os dados é isto.
--
-- public.is_admin() é SECURITY DEFINER de propósito: uma policy em
-- "profiles" que consultasse a própria tabela "profiles" pra checar
-- is_admin entraria em recursão infinita (RLS reavaliando RLS). A
-- função roda com privilégio elevado só pra essa checagem pontual,
-- sem reabrir esse buraco.
-- ---------------------------------------------------------------------
create or replace function public.is_admin()
returns boolean
language sql
security definer set search_path = public
stable
as $$
    select coalesce((select is_admin from public.profiles where id = auth.uid()), false);
$$;

alter table public.profiles enable row level security;
alter table public.audit_log enable row level security;

create policy "usuario ve o proprio perfil"
    on public.profiles for select
    using (auth.uid() = id);

create policy "admin ve todos os perfis"
    on public.profiles for select
    using (public.is_admin());

create policy "admin atualiza qualquer perfil"
    on public.profiles for update
    using (public.is_admin());

create policy "usuario insere seu proprio evento"
    on public.audit_log for insert
    with check (auth.uid() = user_id);

create policy "usuario ve seus proprios eventos"
    on public.audit_log for select
    using (auth.uid() = user_id);

create policy "admin ve todo o audit log"
    on public.audit_log for select
    using (public.is_admin());

-- ---------------------------------------------------------------------
-- BOOTSTRAP DO PRIMEIRO ADMIN (manual, uma vez só)
--
-- 1. Rode tudo acima.
-- 2. Abra o site publicado, faça seu próprio cadastro (e-mail + senha)
--    pela tela normal.
-- 3. Volte aqui no SQL Editor e rode a linha abaixo, trocando o e-mail
--    pelo que você cadastrou — isso te promove a administrador e já
--    aprova seu próprio acesso, sem precisar de outro admin pra isso:
--
--   update public.profiles set is_admin = true, status = 'aprovado',
--          approved_at = now()
--   where email = 'raularagaoalves@gmail.com';
--
-- Todo cadastro seguinte (de outras pessoas) fica 'pendente' até você
-- aprovar pela tela de Governança do site.
-- ---------------------------------------------------------------------
