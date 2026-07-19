# Publicar o site — passo a passo (100% gratuito)

Duas contas gratuitas resolvem tudo: **Supabase** (login/cadastro + banco de
governança) e **GitHub** (hospedagem do site). Nenhuma das duas cobra nada no
volume de uso deste projeto (uso interno, poucos usuários).

## Parte 1 — Supabase (login, cadastro, governança)

1. Crie uma conta em [supabase.com](https://supabase.com) (pode entrar com o
   GitHub, se já tiver conta lá).
2. Crie um novo projeto (**New project**) — escolha uma senha de banco
   forte quando pedir (isso é a senha do Postgres, não tem relação com as
   senhas dos usuários do site) e guarde-a num lugar seguro.
3. Espere o projeto provisionar (1-2 minutos).
4. No menu lateral, abra **SQL Editor** → **New query**. Copie e cole o
   conteúdo inteiro do arquivo [`supabase/schema.sql`](supabase/schema.sql)
   deste projeto e clique em **Run**. Isso cria as tabelas de perfil e
   auditoria, o gatilho de cadastro automático, e as regras de segurança
   (RLS).
5. (Opcional, recomendado pra simplicidade) No menu **Authentication** →
   **Providers** → **Email**, desligue **Confirm email** — assim o cadastro
   fica instantâneo (sem precisar clicar num link recebido por e-mail antes
   de poder logar). Sem isso, o cadastro ainda funciona, só demora um passo
   a mais.
6. No menu **Project Settings** → **API**, copie dois valores:
   - **Project URL** (algo como `https://xxxxxxxx.supabase.co`)
   - **anon public key** (uma chave longa) — **não é a `service_role`**,
     essa não deve ser usada aqui.
7. Abra [`src/gerar_sistema.py`](src/gerar_sistema.py) e troque as duas
   constantes no topo do arquivo:
   ```python
   SUPABASE_URL = "https://xxxxxxxx.supabase.co"
   SUPABASE_ANON_KEY = "sua-anon-key-aqui"
   ```
8. Rode `python src/gerar_sistema.py` de novo — a partir daqui o
   `index.html` gerado passa a exigir login.
9. Abra o site (local ou já publicado), vá na aba **Criar conta**, cadastre
   seu próprio e-mail e uma senha (mínimo 4 caracteres).
10. Volte ao **SQL Editor** do Supabase e rode, trocando pelo e-mail que
    você acabou de cadastrar:
    ```sql
    update public.profiles set is_admin = true, status = 'aprovado', approved_at = now()
    where email = 'seu-email@aqui.com';
    ```
    Isso te promove a administrador e libera seu próprio acesso — sem essa
    etapa manual, nem você conseguiria entrar (todo cadastro nasce
    'pendente', esperando aprovação de alguém que já seja admin).
11. Atualize a página (F5) — você já entra, e a aba **Governança** do menu
    aparece pra você aprovar os próximos cadastros que chegarem.

**A partir daqui**, qualquer pessoa que se cadastrar fica pendente até você
(ou outro admin) aprovar na aba Governança.

## Parte 2 — GitHub Pages (hospedagem do site)

1. Crie uma conta em [github.com](https://github.com), se ainda não tiver.
2. Crie um repositório novo (**New repository**) — pode ser privado ou
   público (privado é mais garantido se os dados forem sensíveis; o próprio
   GitHub Pages, porém, sempre publica o CONTEÚDO em uma URL pública, mesmo
   que o repositório seja privado — a proteção real de quem acessa os dados
   é o login que você acabou de configurar na Parte 1, não a visibilidade
   do repo).
3. No terminal, dentro da pasta do projeto (`Atividade Economica`), rode:
   ```
   git remote add origin https://github.com/SEU-USUARIO/NOME-DO-REPO.git
   git branch -M main
   git push -u origin main
   ```
   (O Git vai pedir login — use um *token* de acesso pessoal do GitHub como
   senha, não a senha da conta; o GitHub mostra como gerar um na tela de
   login do terminal, ou em Settings → Developer settings → Personal access
   tokens.)
4. No repositório, vá em **Settings** → **Pages**. Em **Source**, escolha
   **Deploy from a branch**; em **Branch**, escolha `main` e a pasta
   `/reports` (ou `/` se o GitHub não oferecer `/reports` — nesse caso, ver
   nota abaixo). Salve.
5. Espere 1-2 minutos; o GitHub mostra a URL final, algo como
   `https://seu-usuario.github.io/nome-do-repo/index.html`.

**Nota sobre a pasta:** o GitHub Pages só permite publicar a partir de `/`
ou `/docs`, não de `/reports` diretamente. Se `/reports` não aparecer como
opção, a solução mais simples é criar um arquivo `index.html` na RAIZ do
repositório que apenas redireciona pra `reports/index.html`:
```html
<meta http-equiv="refresh" content="0; url=reports/index.html">
```
(Posso criar esse arquivo de redirecionamento agora mesmo, se preferir —
é só avisar.)

## Rotina mensal

Depois de rodar `atualizar.bat` normalmente (que já chama
`gerar_sistema.py` no fim), publique a versão nova:
```
git add reports/ src/ supabase/
git commit -m "Atualização mensal"
git push
```
O GitHub Pages atualiza a URL publicada automaticamente em 1-2 minutos após
o push — não precisa repetir nenhum passo de configuração.

## O que muda no dia a dia

- O site deixa de abrir 100% offline — a tela de login precisa de internet
  pra conversar com o Supabase. Depois de logado, a sessão fica salva no
  navegador (não pede senha de novo a cada visita, até você clicar em
  "Sair" ou limpar os dados do navegador).
- Qualquer pessoa com o link pode se cadastrar, mas ninguém acessa o
  dashboard sem você aprovar antes na aba Governança.
- A aba Governança também mostra o histórico de login/logout e quais
  seções cada pessoa visitou — visível só pra administradores.
