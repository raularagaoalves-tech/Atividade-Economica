# -*- coding: utf-8 -*-
"""
Compõe o portal único (reports/index.html) a partir dos 3 templates-fonte já
existentes (Crédito, Mapa Regional, PIB por Setor) mais uma seção nova
("Visão Geral"): extrai style/body/script de cada um, prefixa IDs para
evitar colisão entre domínios, embrulha cada script num IIFE que publica seus
dados e seu init() num registro global, e monta uma única página com menu
interno trocando qual seção fica visível (roteador por location.hash, lazy —
só chama init() na primeira visita daquela seção).

Não duplica lógica de dado: reusa as funções já existentes em
gerar_dashboard.py / gerar_mapa.py / gerar_pib_setorial.py. Não mexe nos 3
templates-fonte nem nos 3 geradores individuais, que continuam gerando seus
arquivos avulsos (dashboard.html, mapa.html, pib_setorial.html) normalmente.

Uso:
    python gerar_sistema.py
"""
import json
import re
import sqlite3
from pathlib import Path

from caminhos import DB_ATIVIDADE as DB_PATH
import gerar_dashboard
import gerar_mapa
import gerar_pib_setorial
import gerar_recuperacao_judicial
import gerar_instituicoes_financeiras

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
SRC = Path(__file__).resolve().parent

# Governança (login/cadastro/auditoria) — preencha depois de criar o projeto
# no Firebase (ver PUBLICAR.md, passo 1). Enquanto ficar com o placeholder
# abaixo, o site gerado NÃO exige login (mostra um aviso e libera acesso
# direto) — assim o resto do pipeline continua funcionando normalmente
# antes de você terminar a configuração. Esses valores são públicos por
# design (o mesmo princípio do anon key do Supabase) — a segurança de
# verdade mora nas regras do Firestore (firebase/firestore.rules), não em
# esconder esta config.
FIREBASE_CONFIG = {
    "apiKey": "AIzaSyCFYTBd0njtMKqg0FDbDZk8uVo0roeY0dA",
    "authDomain": "atividade-economica.firebaseapp.com",
    "projectId": "atividade-economica",
    "storageBucket": "atividade-economica.firebasestorage.app",
    "messagingSenderId": "397008799592",
    "appId": "1:397008799592:web:069a1723c1d963a63178dd",
}
# admin permanente — nunca depende de nenhum campo do banco (ver a mesma
# regra espelhada em firebase/firestore.rules, que é quem garante isso de
# verdade; esta constante só evita mostrar a tela de "aguardando
# aprovação" pra essa conta no navegador).
ADMIN_EMAIL_FIXO = "raularagaoalves@gmail.com"

PREFIXOS = {"credito": "cr", "mapa": "mp", "pib": "pb", "geral": "vg", "rj": "rj", "if": "if", "governanca": "gv"}

IDS_POR_DOMINIO = {
    "credito": [
        "as-of-value", "kpi-row", "window-pills", "hero-table-btn", "pane-saldo",
        "svg-saldo", "pane-taxa", "svg-taxa", "hero-xticks", "hero-catcher",
        "hero-tip", "hero-table", "card-juros", "bars-juros", "table-juros",
        "card-inad", "bars-inad", "table-inad", "card-prazo", "bars-prazo",
        "table-prazo", "card-porte", "bars-porte", "table-porte",
        "card-concessao", "bars-concessao", "table-concessao",
        "carteiras-legend", "svg-carteiras", "carteiras-catcher", "carteiras-tip", "carteiras-xticks",
    ],
    "mapa": [
        "as-of-line", "summary-row", "f-uf", "f-cnae", "f-porte", "clear-filters",
        "map-card-sub", "busca-municipio", "busca-resultados", "metric-pills",
        "map-stage", "svg-map", "btn-voltar-brasil", "zoom-in", "zoom-out",
        "zoom-reset", "map-legend", "map-hint", "map-tip", "drawer-card",
        "drawer-empty", "drawer-body", "comparacao-card", "f-regiao-comp",
        "busca-comparacao", "busca-comparacao-resultados", "comp-chips",
        "comp-charts", "uf-metric-pills", "bars-uf", "rj-metric-pills", "bars-rj",
        "bars-cnae", "table-cnae",
        "cp-table-btn", "svg-creditopib", "cp-catcher", "cp-tip", "cp-xticks",
        "table-cp", "composicao-caveat", "elevate-shadow",
    ],
    "pib": [
        "as-of-line", "kpi-row", "metric-pills", "window-pills", "hero-legend",
        "hero-table-btn", "svg-hero", "hero-catcher", "hero-tip", "hero-xticks",
        "hero-asof", "hero-table", "drawer-card", "drawer-empty", "drawer-body",
        "comparacao-card", "comp-metric-pills", "comp-select", "comp-add-btn",
        "comp-chips", "comp-chart-wrap", "bars-oferta", "bars-demanda",
        "svg-comp", "comp-catcher", "comp-tip", "comp-xticks",
    ],
    # "geral" e "governanca" são templates novos, autorais — já nascem com
    # ids namespaced (vg-.../gv-...) direto no HTML/JS, então não passam
    # pela prefixação
    "geral": [],
    "governanca": [],
    "rj": [
        "as-of-line", "kpi-row", "categoria-pills", "serie-pills", "window-pills",
        "hero-legend", "hero-table-btn", "svg-hero", "hero-catcher", "hero-tip",
        "hero-xticks", "hero-crosshair", "hero-asof", "hero-table",
        "uf-metric-pills", "bars-uf", "drawer-card", "drawer-empty", "drawer-body",
    ],
    "if": [
        "as-of-line", "kpi-row", "bars-segmento", "drawer-segmento-card",
        "drawer-segmento-empty", "drawer-segmento-body",
        "busca-if-input", "busca-if-dropdown", "filtro-if-segmento", "filtro-if-uf",
        "tabela-if", "tabela-if-corpo", "tabela-if-contagem", "tabela-if-toggle-btn",
        "dre-ranking-pills", "dre-visao-toggle", "bars-dre", "dre-historico-wrap",
        "svg-dre-hist", "dre-hist-catcher", "dre-hist-tip", "dre-hist-xticks",
        "drawer-card", "drawer-empty", "drawer-body", "comparacao-card",
        "comp-metric-pills", "comp-busca-input", "comp-busca-dropdown", "comp-uf-select",
        "comp-segmento-btns", "comp-limite-aviso",
        "comp-chips", "comp-chart-wrap", "svg-comp", "comp-catcher", "comp-tip", "comp-xticks",
        "bars-carteira", "drawer-carteira-card", "drawer-carteira-empty",
        "drawer-carteira-body", "taxa-modalidade-select", "taxa-busca-input",
        "taxa-busca-dropdown", "bars-taxa", "drawer-taxa-card", "drawer-taxa-empty",
        "drawer-taxa-body",
    ],
}

# rótulos do menu e da seção, na ordem em que aparecem
SECOES = [
    ("geral", "Visão Geral"),
    ("credito", "Crédito"),
    ("mapa", "Mapa Regional"),
    ("pib", "PIB por Setor"),
    ("rj", "Recuperação Judicial"),
    ("if", "Instituições Financeiras"),
    ("governanca", "Governança"),
]


def extrair_partes(html: str) -> tuple[str, str, str]:
    """Retorna (style, body, script) de um template autocontido — os 4
    templates-fonte seguem o mesmo esqueleto <style>...<body>...<script>...,
    então um split por delimitador literal basta (não é HTML arbitrário)."""
    style = html.split("<style>", 1)[1].split("</style>", 1)[0]
    body_e_script = html.split("<body>", 1)[1].split("</body>", 1)[0]
    body, resto = body_e_script.split("<script>", 1)
    script = resto.split("</script>", 1)[0]
    return style.strip(), body.strip(), script.strip()


def prefixar_ids(texto: str, ids: list[str], prefixo: str) -> str:
    """Troca toda referência a um ID conhecido — atributo id="X", literal
    'X'/"X" em JS (getElementById, querySelector), ou seletor/URL CSS bare #X
    (ex. `select#comp-select`, `url(#elevate-shadow)`) — por uma versão
    prefixada. Casamento exato (aspas ou delimitador de palavra) evita pegar
    substrings, ex. "hero-table" dentro de "hero-table-btn". Exclui
    `class="X"` — vários ids (ex. "kpi-row", "summary-row", "map-legend")
    também nomeiam a classe CSS do próprio elemento; sem essa exclusão a
    prefixação renomearia a classe e quebraria o estilo do componente."""
    for id_ in ids:
        escapado = re.escape(id_)
        texto = re.sub(rf"(?<!class=)(['\"]){escapado}\1", rf"\1{prefixo}-{id_}\1", texto)
        texto = re.sub(rf"#{escapado}(?![\w-])", f"#{prefixo}-{id_}", texto)
    return texto


def montar_dados_credito(con: sqlite3.Connection) -> dict:
    # única fonte de verdade: reusa a mesma função que dashboard.html usa,
    # em vez de duplicar a montagem do dict aqui (uma cópia manual já
    # ficou pra trás de um campo novo — ver lição em memória)
    return gerar_dashboard.montar_dados(con)


def montar_dados_mapa(con: sqlite3.Connection) -> dict:
    return gerar_mapa.montar_dados(con)


def resumo_mapa_para_geral(mp: dict) -> dict:
    """Subconjunto pequeno de dados_mapa usado só pela Visão Geral (KPIs de
    população/empresas e o destaque de maior/menor PIB per capita) — evita
    embutir os ~11 MB do payload completo do mapa (histórico de 5571
    municípios, CNAE, malha geográfica etc.) na seção que abre por padrão.
    `meta`/`ufs` já são pequenos (27 UFs) e ficam como estão; só
    `municipios` (o grosso do peso) é reduzido a nome/UF/PIB per capita."""
    return dict(
        meta=mp["meta"],
        ufs=mp["ufs"],
        municipios=[{"nome": m["nome"], "uf": m["uf"], "pibpc": m.get("pibpc")}
                    for m in mp["municipios"]],
    )


def emprego_historico_12m(con: sqlite3.Connection, meses: int = 26) -> list[dict]:
    """Últimos N meses de saldo acumulado 12M (CAGED) — janela grande o
    bastante pra Visão Geral calcular variação mensal, no ano (vs. dez do
    ano anterior) e 12 meses sobre essa própria série acumulada."""
    rows = con.execute(
        "SELECT competencia, saldo_12m FROM v_emprego_historico "
        "WHERE saldo_12m IS NOT NULL ORDER BY competencia DESC LIMIT ?",
        (meses,),
    ).fetchall()
    return [dict(competencia=r[0], saldo_12m=r[1]) for r in reversed(rows)]


TEMPLATES = {
    "credito": SRC / "dashboard_template.html",
    "mapa": SRC / "mapa_template.html",
    "pib": SRC / "pib_setorial_template.html",
    "geral": SRC / "geral_template.html",
    "rj": SRC / "recuperacao_judicial_template.html",
    "if": SRC / "instituicoes_financeiras_template.html",
    "governanca": SRC / "governanca_template.html",
}

# div decorativa de fundo, idêntica nos 4 templates — mantida uma única vez
# como parte do shell (fora das seções), removida do corpo de cada domínio
BG_DECORACAO = re.compile(r'<div class="bg-glow"></div>\s*<div class="bg-grid"></div>\s*')


def compor_dominio(nome: str, dados: dict | None) -> dict:
    html = TEMPLATES[nome].read_text(encoding="utf-8")
    style, body, script = extrair_partes(html)
    prefixo = PREFIXOS[nome]
    ids = IDS_POR_DOMINIO[nome]

    if dados is not None:
        # registra os dados no namespace global ANTES de substituir o
        # placeholder — evita qualquer ambiguidade de regex vs. conteúdo do JSON
        script = script.replace(
            "const DADOS = __DADOS_JSON__;",
            "const DADOS = __DADOS_JSON__;\n"
            "  window.SISTEMA_DADOS = window.SISTEMA_DADOS || {};\n"
            f"  window.SISTEMA_DADOS.{nome} = DADOS;",
        )
        payload = json.dumps(dados, ensure_ascii=False)
        script = script.replace("__DADOS_JSON__", payload)

    style = prefixar_ids(style, ids, prefixo)
    body = prefixar_ids(body, ids, prefixo)
    script = prefixar_ids(script, ids, prefixo)

    if nome == "credito":
        # setupTableToggles() monta o id por concatenação ('table-' + sufixo),
        # não por literal exato — a prefixação acima não alcança essa forma
        script = script.replace(
            "'table-' + btn.dataset.tableToggle",
            f"'{prefixo}-table-' + btn.dataset.tableToggle",
        )

    script = script.replace(
        "document.addEventListener('DOMContentLoaded', init);",
        "window.SISTEMA_APPS = window.SISTEMA_APPS || {};\n"
        f"window.SISTEMA_APPS.{nome} = init;",
    )

    body = BG_DECORACAO.sub("", body)

    return dict(style=style, body=body, script=f"(function() {{\n{script}\n}})();")


SHELL_CSS = """
  .app-nav {
    position: sticky; top: 0; z-index: 50; display: flex; gap: .4rem;
    padding: .7rem 1.5rem; background: var(--surface); border-bottom: 1px solid var(--line);
  }
  .app-nav button {
    font-family: var(--sans); font-size: .82rem; font-weight: 600; color: var(--ink-soft);
    background: transparent; border: 1px solid transparent; border-radius: 8px;
    padding: .45rem .9rem; cursor: pointer; transition: background .12s, color .12s;
  }
  .app-nav button:hover { color: var(--ink); background: var(--surface-2); }
  .app-nav button.active { color: var(--ink); background: var(--surface-2); border-color: var(--line); }
  .app-secao { display: none; }
  .app-secao.active { display: block; }

  .vg-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 1.1rem; }
  @media (max-width: 1100px) { .vg-cards { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 560px) { .vg-cards { grid-template-columns: 1fr; } }
  .vg-card { cursor: pointer; transition: transform .12s ease-out, border-color .12s; }
  .vg-card:hover { transform: translateY(-3px); border-color: var(--ink-muted); }
  .vg-card .card-title { display: flex; align-items: center; gap: .5rem; }
  .vg-card .vg-metric { font-family: var(--mono); font-size: 1.5rem; font-weight: 700; color: var(--ink); margin: .3rem 0; }
  .vg-card .vg-goto { font-size: .74rem; color: var(--teal); }

  #secao-geral .kpi-row { grid-template-columns: repeat(3, 1fr); }
  @media (max-width: 900px) { #secao-geral .kpi-row { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 560px) { #secao-geral .kpi-row { grid-template-columns: 1fr; } }
  #secao-geral .kpi[data-ir] { cursor: pointer; transition: transform .12s ease-out, border-color .12s; }
  #secao-geral .kpi[data-ir]:hover { transform: translateY(-2px); border-color: var(--ink-muted); }
  #secao-geral .kpi .kpi-caption {
    font-size: .68rem; line-height: 1.35; color: var(--ink-muted); margin-top: .4rem;
  }

  .vg-destaques { display: flex; flex-direction: column; gap: 0; }
  .vg-destaque {
    display: flex; align-items: baseline; gap: .7rem; padding: .6rem 0;
    border-bottom: 1px solid var(--line);
  }
  .vg-destaque:last-child { border-bottom: none; }
  .vg-destaque .tag {
    font-family: var(--mono); font-size: .62rem; text-transform: uppercase; letter-spacing: .07em;
    color: var(--ink-muted); width: 130px; flex-shrink: 0;
  }
  .vg-destaque .txt { font-size: .85rem; color: var(--ink); }
  .vg-destaque .num { margin-left: auto; font-family: var(--mono); font-size: .85rem; font-weight: 700; }
  .vg-destaque .num.up { color: var(--green); }
  .vg-destaque .num.down { color: var(--red); }

  .gv-row {
    display: flex; align-items: center; gap: .8rem; padding: .55rem 0;
    border-bottom: 1px solid var(--line-soft); flex-wrap: wrap;
  }
  .gv-row:last-child { border-bottom: none; }
  .gv-row .gv-email { color: var(--ink); font-weight: 600; font-size: .84rem; }
  .gv-row .badge { margin-left: 0; }
  .gv-row button { margin-left: auto; }
  .gv-row button + button { margin-left: 0; }

  .auth-overlay {
    position: fixed; inset: 0; z-index: 200; display: flex; align-items: center; justify-content: center;
    background: var(--page); padding: 1.5rem;
  }
  .auth-box {
    width: 100%; max-width: 380px; background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--radius); padding: 1.6rem 1.5rem;
  }
  .auth-brand { display: flex; align-items: center; gap: .5rem; margin-bottom: 1.3rem; }
  .auth-brand .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--teal); flex-shrink: 0; }
  .auth-brand span.txt { font-size: .95rem; font-weight: 700; color: var(--ink); }
  .auth-tabs { display: flex; gap: .3rem; background: var(--surface-2); padding: .25rem; border-radius: 999px; border: 1px solid var(--line); margin-bottom: 1.2rem; }
  .auth-tabs button {
    flex: 1; appearance: none; border: none; background: transparent; color: var(--ink-soft);
    font-family: var(--sans); font-size: .78rem; font-weight: 600; padding: .45rem .8rem; border-radius: 999px; cursor: pointer;
  }
  .auth-tabs button.active { background: var(--teal); color: #04211d; }
  .auth-form { display: none; flex-direction: column; gap: .7rem; }
  .auth-form.active { display: flex; }
  .auth-form label { font-size: .72rem; color: var(--ink-muted); font-weight: 600; }
  .auth-form input {
    width: 100%; appearance: none; background: var(--surface-2); border: 1px solid var(--line); color: var(--ink);
    font-family: var(--sans); font-size: .85rem; padding: .55rem .8rem; border-radius: 8px; margin-top: .25rem;
  }
  .auth-form input:focus { outline: 2px solid var(--violet); outline-offset: 1px; }
  .auth-submit {
    appearance: none; border: none; background: var(--teal); color: #04211d; font-family: var(--sans);
    font-size: .84rem; font-weight: 700; padding: .6rem .9rem; border-radius: 8px; cursor: pointer; margin-top: .4rem;
  }
  .auth-submit:hover { filter: brightness(1.08); }
  .auth-hint { font-size: .68rem; color: var(--ink-muted); margin-top: -.3rem; }
  .auth-msg { font-size: .78rem; padding: .6rem .75rem; border-radius: 8px; margin-bottom: 1rem; display: none; }
  .auth-msg.erro { display: block; background: rgba(244,63,94,.1); color: var(--rose-lite); border: 1px solid rgba(244,63,94,.3); }
  .auth-msg.info { display: block; background: rgba(13,148,136,.1); color: var(--teal-lite); border: 1px solid rgba(13,148,136,.3); }
  .auth-logout {
    position: fixed; top: .9rem; right: 1.2rem; z-index: 60;
    appearance: none; border: 1px solid var(--line); background: var(--surface-2); color: var(--ink-soft);
    font-family: var(--sans); font-size: .72rem; font-weight: 600; padding: .4rem .8rem; border-radius: 8px; cursor: pointer;
  }
  .auth-logout:hover { color: var(--ink); border-color: var(--ink-muted); }
  .auth-nao-configurado-banner {
    background: rgba(217,119,6,.12); border-bottom: 1px solid rgba(217,119,6,.35); color: var(--amber-lite);
    font-size: .74rem; padding: .5rem 1.5rem; text-align: center;
  }
"""


# domínios cujo script (com o DADOS embutido) só é injetado no DOM — e só
# então parseado/executado pelo motor JS — na primeira visita à seção, em
# vez de rodar no carregamento inicial da página. O mapa é hoje ~11 MB
# (histórico de 5571 municípios, CNAE, malha geográfica) contra <200 KB de
# cada um dos outros domínios — é o único que precisa dessa deferência pra
# o load inicial (Visão Geral) ficar em milissegundos; a Visão Geral usa um
# resumo pequeno (`resumo_mapa_para_geral`) embutido nela mesma, não o
# payload completo do mapa, então não depende dele estar carregado.
DOMINIOS_DIFERIDOS = {"mapa"}


def _string_literal_js(texto: str) -> str:
    """Serializa `texto` como literal de string JS seguro pra embutir DENTRO
    de outro <script> — json.dumps já escapa aspas/backslash/controle;
    falta só neutralizar '</script' (fecharia a tag <script> pro parser
    HTML mesmo estando dentro de uma string JS, escape indiferente)."""
    literal = json.dumps(texto)
    return re.sub(r"</script", "<\\/script", literal, flags=re.IGNORECASE)


# overlay de login/cadastro — cobre a página inteira até autenticação (e
# aprovação) bem-sucedidas; ver montar_auth_js() pro comportamento
AUTH_HTML = """
  <div class="auth-nao-configurado-banner" style="display:none;">
    Login não configurado ainda (FIREBASE_CONFIG em branco em gerar_sistema.py) —
    acesso liberado sem autenticação. Ver PUBLICAR.md.
  </div>
  <div class="auth-overlay" id="auth-overlay">
    <div class="auth-box">
      <div class="auth-brand"><span class="dot"></span><span class="txt">Sistema Atividade Econômica</span></div>
      <div class="auth-msg" id="auth-msg"></div>
      <div class="auth-tabs">
        <button type="button" class="active" data-auth-tab="login">Entrar</button>
        <button type="button" data-auth-tab="cadastro">Criar conta</button>
      </div>
      <form class="auth-form active" id="form-login" data-auth-form="login">
        <div>
          <label for="login-email">E-mail</label>
          <input type="email" id="login-email" required autocomplete="username">
        </div>
        <div>
          <label for="login-senha">Senha</label>
          <input type="password" id="login-senha" required autocomplete="current-password">
        </div>
        <button type="submit" class="auth-submit">Entrar</button>
      </form>
      <form class="auth-form" id="form-cadastro" data-auth-form="cadastro">
        <div>
          <label for="cad-email">E-mail</label>
          <input type="email" id="cad-email" required autocomplete="username">
        </div>
        <div>
          <label for="cad-senha">Senha</label>
          <input type="password" id="cad-senha" required minlength="4" autocomplete="new-password">
        </div>
        <p class="auth-hint">Mínimo de 4 caracteres.</p>
        <button type="submit" class="auth-submit">Criar conta</button>
        <p class="auth-hint">Seu acesso precisa ser aprovado por um administrador antes de liberar o dashboard.</p>
      </form>
    </div>
  </div>
  <button class="auth-logout" id="auth-logout" style="display:none;">Sair</button>
"""


def montar_auth_js() -> str:
    """Autenticação (Firebase Auth) + gate de aprovação + log de auditoria
    de login/logout/visita — ver firebase/firestore.rules pro lado do
    banco (é lá que a aprovação é REALMENTE garantida; o que está aqui é
    só a experiência do usuário, não a fronteira de segurança). Se
    FIREBASE_CONFIG ainda for o placeholder, libera o app direto (sem
    overlay), com um aviso — não trava o pipeline antes da configuração
    ser feita.

    escapeHtml() fica aqui (não dentro de um IIFE) de propósito: é usada
    por governanca_template.html também, que roda no seu próprio IIFE —
    uma function declaration num <script> de nível superior vira global
    (window), acessível de dentro de qualquer IIFE depois dela."""
    config_json = json.dumps(FIREBASE_CONFIG, indent=2)
    admin_json = json.dumps(ADMIN_EMAIL_FIXO)
    return f"""
const FIREBASE_CONFIG = {config_json};
const ADMIN_EMAIL_FIXO = {admin_json};
const AUTH_CONFIGURADO = !FIREBASE_CONFIG.apiKey.startsWith('COLE_AQUI');
window.SISTEMA_AUTH = {{ db: null, usuario: null, configurado: AUTH_CONFIGURADO }};

// escape defensivo — nada de HTML de e-mail digitado por usuário vira
// markup na tela de ninguém, mesmo que o Firebase já valide formato de
// e-mail no servidor (dupla proteção, barata)
function escapeHtml(texto) {{
  const div = document.createElement('div');
  div.textContent = texto == null ? '' : String(texto);
  return div.innerHTML;
}}

function authMostrarMsg(texto, tipo) {{
  const el = document.getElementById('auth-msg');
  el.textContent = texto;
  el.className = 'auth-msg ' + tipo;
}}
function authLimparMsg() {{
  const el = document.getElementById('auth-msg');
  el.textContent = '';
  el.className = 'auth-msg';
}}
function authLiberarApp(admin) {{
  document.getElementById('auth-overlay').style.display = 'none';
  document.getElementById('app-shell').style.display = '';
  document.getElementById('auth-logout').style.display = window.SISTEMA_AUTH.usuario ? 'block' : 'none';
  if (window.SISTEMA_AUTH.usuario && !admin) {{
    document.querySelectorAll('.app-nav [data-secao="governanca"]').forEach(function(b) {{ b.style.display = 'none'; }});
  }}
  // reinvoca a navegação pra seção atual — idempotente (só troca classes e
  // chama init() se ainda não tiver chamado), mas agora com auth.usuario
  // já preenchido, então o log de "visita" da primeira seção acontece
  if (window.irParaSecao) window.irParaSecao(location.hash ? location.hash.slice(1) : 'geral', false);
}}
// traduz os codigos de erro mais comuns do Firebase Auth pra mensagem em
// portugues — sem isso, o usuario veria "auth/wrong-password" na tela
function traduzErroFirebase(err) {{
  const mapa = {{
    'auth/email-already-in-use': 'Esse e-mail já tem uma conta cadastrada.',
    'auth/invalid-email': 'E-mail inválido.',
    'auth/weak-password': 'Senha muito curta (mínimo de 4 caracteres).',
    'auth/wrong-password': 'E-mail ou senha inválidos.',
    'auth/user-not-found': 'E-mail ou senha inválidos.',
    'auth/invalid-credential': 'E-mail ou senha inválidos.',
    'auth/too-many-requests': 'Muitas tentativas seguidas — aguarde alguns minutos e tente de novo.',
  }};
  return mapa[err.code] || 'Não foi possível completar a ação. Tente novamente.';
}}

(function initAuth() {{
  if (!AUTH_CONFIGURADO) {{
    document.querySelector('.auth-nao-configurado-banner').style.display = 'block';
    authLiberarApp(true);
    return;
  }}
  firebase.initializeApp(FIREBASE_CONFIG);
  const auth = firebase.auth();
  const db = firebase.firestore();
  window.SISTEMA_AUTH.db = db;

  // sinalizador setado logo antes de uma acao explicita do usuario
  // (login/cadastro), consumido pelo UNICO callback que efetivamente
  // libera o app (onAuthStateChanged) — evita logar 'login' duas vezes
  // (uma pela acao explicita, outra pela sessao restaurada no reload)
  let logarLoginPendente = false;

  async function aoAutenticar(user, logarLogin) {{
    const perfilRef = db.collection('profiles').doc(user.uid);
    let perfilSnap = await perfilRef.get();
    if (!perfilSnap.exists) {{
      // perfil ausente (cadastro novo, ou uma tentativa antiga que falhou
      // no meio): cria aqui mesmo, em QUALQUER login — as regras do
      // Firestore garantem que só se cria o proprio perfil e sempre
      // nascendo pendente/sem privilegio, então não há risco em ser
      // permissivo no cliente
      await perfilRef.set({{
        email: user.email, status: 'pendente', isAdmin: false,
        criadoEm: firebase.firestore.FieldValue.serverTimestamp(),
      }});
      perfilSnap = await perfilRef.get();
    }}
    let perfil = perfilSnap.data();
    const souAdminFixo = user.email === ADMIN_EMAIL_FIXO;
    // auto-cura: o admin fixo sempre entra, e de quebra deixa o proprio
    // documento consistente (isAdmin/status) pra tela de Governanca
    // mostrar certo — as regras do Firestore permitem essa auto-edicao
    // só pra esse e-mail (ver ehAdminFixo() em firestore.rules)
    if (souAdminFixo && (!perfil.isAdmin || perfil.status !== 'aprovado')) {{
      await perfilRef.update({{ isAdmin: true, status: 'aprovado' }});
      perfil = {{ ...perfil, isAdmin: true, status: 'aprovado' }};
    }}
    if (!souAdminFixo && perfil.status === 'pendente') {{
      authMostrarMsg('Cadastro recebido — aguardando aprovação de um administrador.', 'info');
      document.getElementById('auth-logout').style.display = 'block';
      return;
    }}
    if (!souAdminFixo && perfil.status === 'rejeitado') {{
      authMostrarMsg('Acesso não autorizado para esta conta.', 'erro');
      document.getElementById('auth-logout').style.display = 'block';
      return;
    }}
    const isAdmin = souAdminFixo || !!perfil.isAdmin;
    window.SISTEMA_AUTH.usuario = {{ id: user.uid, email: user.email, isAdmin: isAdmin, status: perfil.status }};
    if (logarLogin) {{
      db.collection('audit_log').add({{
        userId: user.uid, email: user.email, evento: 'login',
        criadoEm: firebase.firestore.FieldValue.serverTimestamp(),
      }});
    }}
    authLimparMsg();
    authLiberarApp(isAdmin);
  }}

  // fonte unica de verdade pro estado de login — dispara no load (sessao
  // restaurada, logarLogin=false) e apos signIn/signUp bem-sucedidos
  // (logarLogin=true via a flag setada nos handlers de submit abaixo).
  // O catch é essencial: sem ele, uma falha na etapa pos-login (ex. regra
  // do Firestore negando a leitura/criacao do perfil) rejeitaria em
  // silencio e a tela ficaria "travada" sem nenhuma mensagem.
  auth.onAuthStateChanged(function(user) {{
    if (!user) return;
    const logar = logarLoginPendente;
    logarLoginPendente = false;
    aoAutenticar(user, logar).catch(function(err) {{
      console.error('Erro pos-autenticacao:', err);
      authMostrarMsg('Conta autenticada, mas houve um erro ao carregar seu perfil: ' +
        (err && err.code ? err.code : (err && err.message ? err.message : 'erro desconhecido')) +
        ' — atualize a página (F5) e tente entrar de novo.', 'erro');
      document.getElementById('auth-logout').style.display = 'block';
    }});
  }});

  document.querySelectorAll('[data-auth-tab]').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      authLimparMsg();
      document.querySelectorAll('[data-auth-tab]').forEach(function(b) {{ b.classList.toggle('active', b === btn); }});
      document.querySelectorAll('[data-auth-form]').forEach(function(f) {{ f.classList.toggle('active', f.dataset.authForm === btn.dataset.authTab); }});
    }});
  }});

  document.getElementById('form-login').addEventListener('submit', async function(ev) {{
    ev.preventDefault();
    authLimparMsg();
    const email = document.getElementById('login-email').value.trim();
    const senha = document.getElementById('login-senha').value;
    try {{
      logarLoginPendente = true;
      await auth.signInWithEmailAndPassword(email, senha);
    }} catch (err) {{
      logarLoginPendente = false;
      authMostrarMsg(traduzErroFirebase(err), 'erro');
    }}
  }});

  document.getElementById('form-cadastro').addEventListener('submit', async function(ev) {{
    ev.preventDefault();
    authLimparMsg();
    const email = document.getElementById('cad-email').value.trim();
    const senha = document.getElementById('cad-senha').value;
    if (senha.length < 4) {{ authMostrarMsg('A senha precisa ter pelo menos 4 caracteres.', 'erro'); return; }}
    try {{
      logarLoginPendente = true;
      await auth.createUserWithEmailAndPassword(email, senha);
    }} catch (err) {{
      logarLoginPendente = false;
      authMostrarMsg(traduzErroFirebase(err), 'erro');
    }}
  }});

  document.getElementById('auth-logout').addEventListener('click', async function() {{
    const eu = window.SISTEMA_AUTH.usuario;
    if (eu) {{
      await db.collection('audit_log').add({{
        userId: eu.id, email: eu.email, evento: 'logout',
        criadoEm: firebase.firestore.FieldValue.serverTimestamp(),
      }});
    }}
    await auth.signOut();
    location.reload();
  }});
}})();
"""


def montar_shell(dominios: dict[str, dict]) -> str:
    estilos = "\n".join(dominios[nome]["style"] for nome, _ in SECOES)
    menu = "\n    ".join(
        f'<button data-secao="{nome}"{" class=\"active\"" if nome == "geral" else ""}>{rotulo}</button>'
        for nome, rotulo in SECOES
    )
    secoes_html = "\n\n".join(
        f'  <section id="secao-{nome}" class="app-secao{" active" if nome == "geral" else ""}">\n'
        f'{dominios[nome]["body"]}\n  </section>'
        for nome, _ in SECOES
    )
    eager = [nome for nome, _ in SECOES if nome not in DOMINIOS_DIFERIDOS]
    diferidos = [nome for nome, _ in SECOES if nome in DOMINIOS_DIFERIDOS]
    scripts = "\n".join(f"<script>\n{dominios[nome]['script']}\n</script>" for nome in eager)
    scripts_diferidos_js = ",\n".join(
        f"  {nome}: {_string_literal_js(dominios[nome]['script'])}" for nome in diferidos
    )
    nomes_json = json.dumps([nome for nome, _ in SECOES])

    auth_js = montar_auth_js()
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sistema Atividade Econômica</title>
<script src="https://www.gstatic.com/firebasejs/10.14.1/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.14.1/firebase-auth-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore-compat.js"></script>
<style>
{estilos}
{SHELL_CSS}
</style>
</head>
<body>
  <div class="bg-glow"></div>
  <div class="bg-grid"></div>

{AUTH_HTML}

  <div id="app-shell" style="display:none;">
  <nav class="app-nav" aria-label="Navegação principal">
    {menu}
  </nav>

{secoes_html}
  </div>

<script>
{auth_js}
</script>
{scripts}
<script>
(function() {{
  const SECOES = {nomes_json};
  const inicializadas = {{}};
  // domínios com carga diferida: o texto do <script> (que embute o DADOS
  // completo daquele domínio) só é injetado no DOM — e só então parseado
  // pelo motor JS — na primeira visita, não no load inicial da página
  const SCRIPTS_DIFERIDOS = {{
{scripts_diferidos_js}
  }};
  const carregados = {{}};
  function carregarDominioDiferido(nome) {{
    if (carregados[nome] || !SCRIPTS_DIFERIDOS[nome]) return;
    const el = document.createElement('script');
    el.textContent = SCRIPTS_DIFERIDOS[nome];
    document.body.appendChild(el);
    carregados[nome] = true;
  }}
  let ultimaSecaoLogada = null;
  function logarVisita(nome) {{
    const auth = window.SISTEMA_AUTH;
    if (!auth || !auth.db || !auth.usuario || nome === ultimaSecaoLogada) return;
    ultimaSecaoLogada = nome;
    auth.db.collection('audit_log').add({{
      userId: auth.usuario.id, email: auth.usuario.email, evento: 'visita', detalhe: nome,
      criadoEm: firebase.firestore.FieldValue.serverTimestamp(),
    }});
  }}
  function irParaSecao(nome, atualizarHash) {{
    if (!SECOES.includes(nome)) nome = 'geral';
    document.querySelectorAll('.app-secao').forEach(s => s.classList.toggle('active', s.id === 'secao-' + nome));
    document.querySelectorAll('.app-nav button').forEach(b => b.classList.toggle('active', b.dataset.secao === nome));
    carregarDominioDiferido(nome);
    if (!inicializadas[nome] && window.SISTEMA_APPS && window.SISTEMA_APPS[nome]) {{
      window.SISTEMA_APPS[nome]();
      inicializadas[nome] = true;
    }}
    logarVisita(nome);
    if (atualizarHash !== false) location.hash = nome;
  }}
  window.irParaSecao = irParaSecao;
  document.querySelectorAll('.app-nav button').forEach(b => {{
    b.addEventListener('click', () => irParaSecao(b.dataset.secao, true));
  }});
  window.addEventListener('hashchange', () => irParaSecao(location.hash.slice(1), false));
  document.addEventListener('DOMContentLoaded', () => {{
    irParaSecao(location.hash ? location.hash.slice(1) : 'geral', false);
  }});
}})();
</script>
</body>
</html>
"""


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    dados_credito = montar_dados_credito(con)
    dados_mapa = montar_dados_mapa(con)
    dados_pib = gerar_pib_setorial.montar_dados(con)
    dados_rj = gerar_recuperacao_judicial.montar_dados(con)
    dados_if = gerar_instituicoes_financeiras.montar_dados(con)
    dados_emprego = emprego_historico_12m(con)
    con.close()

    dominios = {
        "credito": compor_dominio("credito", dados_credito),
        "mapa": compor_dominio("mapa", dados_mapa),
        "pib": compor_dominio("pib", dados_pib),
        "rj": compor_dominio("rj", dados_rj),
        "if": compor_dominio("if", dados_if),
        "geral": compor_dominio("geral", dict(
            emprego=dados_emprego, resumo_mapa=resumo_mapa_para_geral(dados_mapa))),
        "governanca": compor_dominio("governanca", None),
    }

    html = montar_shell(dominios)
    destino = REPORTS / "index.html"
    destino.write_text(html, encoding="utf-8")
    print(f"  index.html gerado (payload {len(html)/1e6:.1f} MB)")
    print(f"  {destino}")


if __name__ == "__main__":
    main()
