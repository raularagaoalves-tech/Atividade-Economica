# Publicar o site — passo a passo (100% gratuito)

Duas contas gratuitas resolvem tudo: **Firebase** (Google — login/cadastro +
banco de governança) e **GitHub** (hospedagem do site). Nenhuma das duas
cobra nada no volume de uso deste projeto (uso interno, poucos usuários).

## Parte 1 — Firebase (login, cadastro, governança)

1. Acesse [console.firebase.google.com](https://console.firebase.google.com)
   com uma conta Google (pode ser a mesma do Gmail que você já usa).
2. **Criar um projeto** → dê um nome (ex. "atividade-economica") → pode
   desativar o Google Analytics (não é necessário) → **Criar projeto**.
3. No menu lateral, **Compilação → Authentication** → **Vamos começar** →
   escolha **E-mail/senha** na lista de provedores → ative a primeira
   opção ("E-mail/senha") → **Salvar**.
4. **Compilação → Firestore Database** → **Criar banco de dados** → modo
   de produção → escolha uma localização (qualquer uma nas Américas serve,
   ex. `southamerica-east1` se disponível) → **Ativar**.
5. Na aba **Regras** do Firestore, apague o conteúdo padrão e cole o
   conteúdo inteiro do arquivo
   [`firebase/firestore.rules`](firebase/firestore.rules) deste projeto →
   **Publicar**. Isso é o que garante, no servidor, que ninguém consegue
   se auto-aprovar ou se promover a admin mexendo no navegador — e já
   reconhece `raularagaoalves@gmail.com` como administrador permanente,
   sem nenhum passo manual extra.
6. No ícone de engrenagem (⚙) → **Configurações do projeto** → role até
   **Seus aplicativos** → clique no ícone `</>` (Web) → dê um apelido (ex.
   "site") → **Registrar app** (não precisa marcar Firebase Hosting).
7. Copie o objeto `firebaseConfig` que aparece na tela — algo assim:
   ```js
   const firebaseConfig = {
     apiKey: "AIza...",
     authDomain: "atividade-economica-xxxx.firebaseapp.com",
     projectId: "atividade-economica-xxxx",
     storageBucket: "atividade-economica-xxxx.appspot.com",
     appId: "1:...:web:..."
   };
   ```
8. Abra [`src/gerar_sistema.py`](src/gerar_sistema.py) e cole esses
   valores na constante `FIREBASE_CONFIG` no topo do arquivo (troque só os
   5 campos, mantendo as aspas).
9. Rode `python src/gerar_sistema.py` de novo — a partir daqui o
   `index.html` gerado passa a exigir login.
10. Abra o site (local ou já publicado), vá na aba **Criar conta**, e
    cadastre **exatamente** `raularagaoalves@gmail.com` com uma senha
    (mínimo 4 caracteres) — como esse e-mail já está fixado nas regras do
    Firestore como administrador permanente, o acesso é liberado na hora,
    sem nenhum passo manual no banco (diferente do Supabase, que exigia
    rodar um SQL à parte).

**A partir daqui**, qualquer outra pessoa que se cadastrar fica pendente
até você (ou outro admin que você aprovar) liberar na aba Governança.

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

## Rotina mensal — automática

A atualização roda sozinha, na nuvem, todo dia 20 do mês (`.github/workflows/atualizacao-mensal.yml`):
baixa os dados novos, reconstrói o banco, gera os relatórios e o site, e
publica tudo de volta no repositório — sem depender do seu computador estar
ligado. O GitHub Pages atualiza a URL publicada automaticamente em 1-2
minutos depois.

- **Ver se rodou:** aba **Actions** do repositório no GitHub mostra o
  histórico de execuções (verde = deu certo, vermelho = falhou).
- **Rodar fora do dia 20** (pra testar, ou por qualquer motivo): aba
  **Actions** → **Atualizacao mensal** (menu à esquerda) → botão **Run
  workflow**.
- **Se falhar:** o GitHub manda um e-mail automático pro dono do
  repositório avisando — não precisa configurar nada a mais pra isso.
- **Rodar manualmente no seu computador continua funcionando** do mesmo
  jeito de sempre (`atualizar.bat` + `git add` / `commit` / `push`), caso
  quira os relatórios `.xlsx` localmente ou queira forçar uma atualização
  fora do calendário sem usar o GitHub.

## O que muda no dia a dia

- O site deixa de abrir 100% offline — a tela de login precisa de internet
  pra conversar com o Firebase. Depois de logado, a sessão fica salva no
  navegador (não pede senha de novo a cada visita, até você clicar em
  "Sair" ou limpar os dados do navegador).
- Qualquer pessoa com o link pode se cadastrar, mas ninguém acessa o
  dashboard sem você aprovar antes na aba Governança.
- A aba Governança também mostra o histórico de login/logout e quais
  seções cada pessoa visitou — visível só pra administradores.
