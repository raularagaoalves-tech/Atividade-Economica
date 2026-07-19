# -*- coding: utf-8 -*-
"""
Descobre no catálogo público de dados abertos do BCB (CKAN) as séries SGS que
compõem o "crédito detalhado": taxa de juros e inadimplência por modalidade de
crédito, saldo por porte de pessoa jurídica (inclusive MEI), Indicador de
Custo do Crédito (ICC) e prazo médio.

Gera a semente `data/manual/credito_detalhado_series.csv`, consumida por
`baixar_dados.py`/`carregar_dados.py`. Roda a cada atualização (chamado por
`atualizar.bat`, passo 1/4) — tolerante a falha: se o catálogo estiver fora do
ar, mantém a semente existente e avisa (nunca apaga uma semente boa).

Uso:
    python descobrir_credito_detalhado.py
"""
import json
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd

from caminhos import DIR_MANUAL as MANUAL

CATALOGO = "https://dadosabertos.bcb.gov.br/api/3/action/package_search"
HEADERS = {"User-Agent": "Mozilla/5.0 (sistema-atividade-economica)"}
SEMENTE = MANUAL / "credito_detalhado_series.csv"

# ---------------------------------------------------------------------
# Frases de busca no catálogo (uma consulta paginada por frase)
# ---------------------------------------------------------------------
BUSCAS = [
    "Saldo de crédito pessoa jurídica por modalidade de crédito",
    "Saldo de crédito do MEI pessoa física por modalidade de crédito",
    "Saldo de crédito do MEI pessoa jurídica por modalidade de crédito",
    "Saldo de crédito por origem dos recursos e modalidade de crédito",
    "Taxa de juros de pessoa jurídica por origem dos recursos e modalidade de crédito",
    "Taxa de juros por origem dos recursos e modalidade de crédito",
    "Taxa de inadimplência de pessoa jurídica por origem dos recursos e modalidade de crédito",
    "Taxa de inadimplência por origem dos recursos e modalidade de crédito",
    "Indicador de Custo do Crédito",
    "Prazo médio da carteira de crédito com recursos livres",
    "Prazo médio da carteira de crédito com recursos direcionados",
    "Prazo médio das concessões de crédito com recursos livres",
    "Prazo médio das concessões de crédito com recursos direcionados",
    "Saldo de crédito por região",
    "Saldo de crédito pessoa jurídica por região",
    # família clássica "recursos livres/direcionados" por produto — cobre PF e
    # PJ em geral, SEM restrição de porte (a família "por modalidade de
    # crédito" acima só cobre MEI/micro/pequena empresa)
    "Saldo da carteira de crédito",
    # mesma família clássica, agora do lado da CONCESSÃO (novas operações no
    # mês) em vez do estoque/saldo — mesma estrutura de título, mesmas
    # modalidades. Também traz de brinde "sazonalmente ajustadas" e "série
    # encadeada ao crédito referencial" (variantes metodológicas que não
    # batem nenhum PADRAO abaixo por não ter "com recursos livres/
    # direcionados - Pessoas ..." exato — ficam NAO_PARSEADO de propósito,
    # não são um corte que pedimos).
    "Concessões de crédito",
]

# ---------------------------------------------------------------------
# (prefixo exato do título, métrica, cliente fixo, origem fixa, modalidade fixa)
# Tentados em ordem contra cada título — `None` em cliente/origem significa
# "detectar via token no restante do título" (ver consumir_token). O prefixo
# NUNCA é usado para split posicional: tudo que sobra após consumir os tokens
# conhecidos vira `modalidade` inteiro, mesmo que contenha " - " embutido
# (ex. "ARO - adiantamento de receitas orçamentárias"). `modalidade fixa`
# é usada só nos poucos títulos com ordem invertida (ver "rotativo" abaixo),
# onde a modalidade vem ANTES do cliente no título, não depois.
# ---------------------------------------------------------------------
PADROES = [
    ("Saldo de crédito pessoa jurídica por modalidade de crédito", "SALDO", "PJ", None, None),
    ("Saldo de crédito do MEI pessoa jurídica por modalidade de crédito", "SALDO", "MEI_PJ", None, None),
    ("Saldo de crédito do MEI pessoa física por modalidade de crédito", "SALDO", "MEI_PF", None, None),
    ("Saldo de crédito por origem dos recursos e modalidade de crédito", "SALDO", "MEI", None, None),
    ("Taxa de juros de pessoa jurídica por origem dos recursos e modalidade de crédito", "TAXA_JUROS", "PJ", None, None),
    ("Taxa de juros por origem dos recursos e modalidade de crédito", "TAXA_JUROS", "MEI", None, None),
    ("Taxa de inadimplência de pessoa jurídica por origem dos recursos e modalidade de crédito", "TAXA_INADIMPLENCIA", "PJ", None, None),
    ("Taxa de inadimplência por origem dos recursos e modalidade de crédito", "TAXA_INADIMPLENCIA", "MEI", None, None),
    ("Indicador de Custo do Crédito - ICC", "ICC", None, None, None),
    ("Spread do ICC", "SPREAD_ICC", None, None, None),
    ("Prazo médio da carteira de crédito com recursos livres", "PRAZO_MEDIO_CARTEIRA", None, "LIVRE", None),
    ("Prazo médio da carteira de crédito com recursos direcionados", "PRAZO_MEDIO_CARTEIRA", None, "DIRECIONADO", None),
    ("Prazo médio das concessões de crédito com recursos livres", "PRAZO_MEDIO_CONCESSAO", None, "LIVRE", None),
    ("Prazo médio das concessões de crédito com recursos direcionados", "PRAZO_MEDIO_CONCESSAO", None, "DIRECIONADO", None),
    ("Saldo de crédito por região - MEI", "SALDO", "MEI", None, None),
    ("Saldo de crédito pessoa jurídica por região", "SALDO", "PJ", None, None),
    # família clássica "recursos livres/direcionados" por produto (PF/PJ em
    # geral, sem restrição de porte) — títulos com ordem invertida
    # ("rotativo"/"não rotativo" ANTES de "- Pessoas ...") tratados à parte,
    # antes dos prefixos genéricos por causa do sort por comprimento
    ("Saldo da carteira de crédito com recursos livres rotativo -", "SALDO", None, "LIVRE", "Rotativo"),
    ("Saldo da carteira de crédito com recursos livres não rotativo -", "SALDO", None, "LIVRE", "Não rotativo"),
    ("Saldo da carteira de crédito com recursos livres - Pessoas jurídicas -", "SALDO", "PJ", "LIVRE", None),
    ("Saldo da carteira de crédito com recursos livres - Pessoas físicas -", "SALDO", "PF", "LIVRE", None),
    ("Saldo da carteira de crédito com recursos direcionados - Pessoas jurídicas -", "SALDO", "PJ", "DIRECIONADO", None),
    ("Saldo da carteira de crédito com recursos direcionados - Pessoas físicas -", "SALDO", "PF", "DIRECIONADO", None),
    ("Saldo da carteira de crédito com recursos livres -", "SALDO", None, "LIVRE", None),
    ("Saldo da carteira de crédito com recursos direcionados -", "SALDO", None, "DIRECIONADO", None),
    ("Saldo da carteira de crédito -", "SALDO", None, None, None),
    # espelho da família acima, do lado da concessão (novas operações no mês)
    ("Concessões de crédito com recursos livres rotativo -", "CONCESSAO", None, "LIVRE", "Rotativo"),
    ("Concessões de crédito com recursos livres não rotativo -", "CONCESSAO", None, "LIVRE", "Não rotativo"),
    ("Concessões de crédito com recursos livres - Pessoas jurídicas -", "CONCESSAO", "PJ", "LIVRE", None),
    ("Concessões de crédito com recursos livres - Pessoas físicas -", "CONCESSAO", "PF", "LIVRE", None),
    ("Concessões de crédito com recursos direcionados - Pessoas jurídicas -", "CONCESSAO", "PJ", "DIRECIONADO", None),
    ("Concessões de crédito com recursos direcionados - Pessoas físicas -", "CONCESSAO", "PF", "DIRECIONADO", None),
    ("Concessões de crédito com recursos livres -", "CONCESSAO", None, "LIVRE", None),
    ("Concessões de crédito com recursos direcionados -", "CONCESSAO", None, "DIRECIONADO", None),
    ("Concessões de crédito -", "CONCESSAO", None, None, None),
    # prazo médio das concessões, agregados sem quebra de origem (a versão
    # "com recursos livres/direcionados" já é tratada acima, nas 2 entradas
    # PRAZO_MEDIO_CONCESSAO originais — essas aqui pegam só Total/PJ/PF puro)
    ("Prazo médio das concessões de crédito -", "PRAZO_MEDIO_CONCESSAO", None, None, None),
]
# prefixo mais longo (mais específico) primeiro — evita que uma variante
# genérica capture por engano um título de uma variante mais qualificada
PADROES.sort(key=lambda p: -len(p[0]))

PORTE_TOKENS = {"microempresa": "MICRO", "pequeno porte": "PEQUENO"}
ORIGEM_TOKENS = {"recursos livres": "LIVRE", "recursos direcionados": "DIRECIONADO",
                 "crédito livre": "LIVRE", "crédito direcionado": "DIRECIONADO"}
CLIENTE_TOKENS = {"pessoas jurídicas": "PJ", "pessoas físicas": "PF"}
# grandes regiões do IBGE — única quebra geográfica abaixo do nacional que o
# BACEN publica para saldo por porte/MEI (não existe por UF nem por modalidade)
REGIAO_TOKENS = {"norte": "NORTE", "nordeste": "NORDESTE", "centro-oeste": "CENTRO_OESTE",
                 "sudeste": "SUDESTE", "sul": "SUL"}

# par agregado por porte de empresa (únicos códigos SGS com granularidade
# média/grande — não têm quebra por modalidade, só o total do porte)
AGREGADOS_PORTE = {
    27701: ("SALDO", "PJ", "MPME", "R$ milhões"),
    27702: ("SALDO", "PJ", "GRANDE", "R$ milhões"),
    27703: ("TAXA_INADIMPLENCIA", "PJ", "MPME", "%"),
    27704: ("TAXA_INADIMPLENCIA", "PJ", "GRANDE", "%"),
}


def buscar_pacotes(frase: str) -> list[dict]:
    """Busca paginada (CKAN pagina em blocos; uma chamada não basta para
    famílias com mais de ~200 resultados)."""
    pacotes, start = [], 0
    while True:
        url = (f"{CATALOGO}?q={quote(chr(34) + frase + chr(34))}"
               f"&rows=200&start={start}")
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=120) as resp:
            dados = json.loads(resp.read())
        resultados = dados["result"]["results"]
        pacotes.extend(resultados)
        start += len(resultados)
        if not resultados or start >= dados["result"]["count"]:
            break
    return pacotes


def consumir_token(resto: str, tokens: dict) -> tuple[str | None, str]:
    """Se `resto` começa com um token conhecido (segmento inteiro, seguido de
    ' - ' ou fim de string), retorna o valor mapeado e o restante sem o
    token. Senão retorna (None, resto) inalterado."""
    resto_lower = resto.lower()
    for chave, valor in tokens.items():
        if resto_lower == chave:
            return valor, ""
        if resto_lower.startswith(chave + " - "):
            return valor, resto[len(chave) + 3:]
    return None, resto


def parsear_titulo(title: str) -> dict:
    for prefixo, metrica, cliente_fixo, origem_fixo, modalidade_fixa in PADROES:
        if not title.startswith(prefixo):
            continue
        resto = title[len(prefixo):].lstrip(" -")
        cliente, porte, origem, regiao = cliente_fixo, None, origem_fixo, None

        v, resto = consumir_token(resto, PORTE_TOKENS)
        if v:
            porte = v
        if cliente is None:
            v, resto = consumir_token(resto, CLIENTE_TOKENS)
            if v:
                cliente = v
        if origem is None:
            v, resto = consumir_token(resto, ORIGEM_TOKENS)
            if v:
                origem = v
        if cliente is None:
            v, resto = consumir_token(resto, CLIENTE_TOKENS)
            if v:
                cliente = v
        v, resto = consumir_token(resto, REGIAO_TOKENS)
        if v:
            regiao = v

        if modalidade_fixa:
            modalidade = modalidade_fixa
        else:
            modalidade = resto.strip(" -") or None
            if modalidade and modalidade.lower() == "total":
                modalidade = None
        return dict(metrica=metrica, cliente=cliente, porte=porte, origem=origem,
                    regiao=regiao, modalidade=modalidade, parse_status="OK")
    return dict(metrica=None, cliente=None, porte=None, origem=None, regiao=None,
                modalidade=None, parse_status="NAO_PARSEADO")


def normalizar_periodicidade(valor) -> str:
    return "MENSAL" if str(valor).strip().lower() == "mensal" else "TRIMESTRAL"


def main() -> None:
    print("== Descobrindo séries de crédito detalhado (catálogo BCB) ==")
    try:
        vistos: dict[int, dict] = {}
        for frase in BUSCAS:
            pacotes = buscar_pacotes(frase)
            print(f"  [busca] \"{frase}\": {len(pacotes)} pacotes")
            for p in pacotes:
                codigo = int(p["codigo_sgs"])
                if codigo in vistos:
                    continue  # já capturado por uma busca anterior
                info = parsear_titulo(p["title"].strip())
                vistos[codigo] = dict(
                    codigo=codigo, titulo=p["title"].strip(),
                    periodicidade=normalizar_periodicidade(p.get("periodicidade")),
                    unidade=p.get("unidade_medida", ""),
                    frente=frase, **info)

        for codigo, (metrica, cliente, porte, unidade) in AGREGADOS_PORTE.items():
            vistos[codigo] = dict(
                codigo=codigo, titulo=f"[agregado por porte] {metrica} - {porte}",
                periodicidade="MENSAL", unidade=unidade,
                frente="por porte da empresa (agregado)", metrica=metrica,
                cliente=cliente, porte=porte, origem=None, regiao=None,
                modalidade=None, parse_status="OK")

    except Exception as exc:  # catálogo fora do ar: preserva a semente anterior
        print(f"  [ERRO] catálogo do BCB indisponível ({exc}); "
              f"mantendo semente anterior")
        return

    df = pd.DataFrame(vistos.values())
    df = df[["codigo", "titulo", "metrica", "cliente", "porte", "origem", "regiao",
             "modalidade", "periodicidade", "unidade", "frente", "parse_status"]]
    df = df.sort_values("codigo")
    df.to_csv(SEMENTE, sep=";", index=False, encoding="utf-8")

    print(f"\n  total de séries descobertas: {len(df)}")
    print("  por status de parsing:")
    for status, n in df["parse_status"].value_counts().items():
        print(f"    {status}: {n}")
    print("  por métrica:")
    for metrica, n in df["metrica"].value_counts(dropna=False).items():
        print(f"    {metrica}: {n}")


if __name__ == "__main__":
    main()
