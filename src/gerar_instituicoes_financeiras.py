# -*- coding: utf-8 -*-
"""
Gera o dashboard interativo de Instituições Financeiras (IF.data, BACEN)
— reports/instituicoes_financeiras.html — a partir do banco
data/db/atividade.db: segmentação prudencial (S1-S5), resultados (DRE)
por instituição individual com histórico e comparação, carteira de
crédito ativa (aging, CNAE, porte) por conglomerado prudencial, e taxa
de juros por instituição.

Autocontido: os dados são embutidos como JSON no HTML (sem servidor, sem
chamada de rede) — abre direto no navegador, roda 100% local.

ATENÇÃO — dois níveis de consolidação que NÃO compartilham `codinst`:
DRE (instituição individual) vs. aging/CNAE/porte (conglomerado
prudencial). Nunca cruzar os dois por `codinst` (bug já cometido e
corrigido em gerar_relatorios.py) — por isso este dashboard trata
"Resultados (DRE)" e "Carteira de crédito" como rankings/entidades
independentes, cada um com sua própria lista de instituições.

Uso:
    python gerar_instituicoes_financeiras.py
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

from caminhos import DB_ATIVIDADE as DB_PATH

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
TEMPLATE = Path(__file__).resolve().parent / "instituicoes_financeiras_template.html"

# top N por dimensão — mantém o payload e os gráficos legíveis
TOP_N_DRE = 50
TOP_N_CARTEIRA = 30
TOP_N_SEGMENTO = 10
# janela padrão de histórico pedida pelo usuário (jul/2026) — últimos 5 anos,
# aplicada aos relatórios de IF que não são o DRE (taxa de juros, evolução do
# sistema pros KPIs)
HIST_ANOS = 5
# DRE: histórico mais longo, pedido explícito do usuário (jul/2026) — o
# IF.data via Olinda (TipoInstituicao=1, relatório 4) só tem dado confirmado
# a partir de 1T/2014 (confirmado empiricamente: 4T/2013 retorna 0 linhas);
# 12 anos cobre 2014-2026 inteiro com folga, sem depender de trimestres
# ainda não confirmados. baixar_dados.py já busca cadastro+DRE até esse
# piso independente do --desde padrão (ver DESDE_DRE_MINIMO).
HIST_ANOS_DRE = 12

DRE_CAMPOS = [
    ("ativo_total", "Ativo total"),
    ("receita_total", "Receita total"),
    ("receita_juros_credito", "Receita de juros de crédito"),
    ("receita_credito_total", "Receita de operações de crédito"),
    ("despesa_total", "Despesa total"),
    ("perda_esperada_credito", "Perda esperada de crédito"),
    ("lucro_liquido", "Lucro líquido"),
    ("roa", "ROA* (Lucro líquido / Ativo total)"),
]

# Classificação por natureza da conta (COSIF 2025, ver nota em schema.sql em
# v_ifdata_dre): contas de receita somam direto em Receita Total, contas de
# despesa entram em módulo (valor absoluto) em Despesa Total. As contas
# "Resultado com X" (derivativos, outros da intermediação, outros de
# transações de pagamento, participações societárias) não têm natureza fixa
# — podem ser ganho ou perda conforme o período — por isso entram pelo
# SINAL de cada linha: positivo vira receita, negativo vira despesa (em
# módulo). Isso garante Receita Total − Despesa Total = Lucro Líquido
# exatamente, linha a linha (identidade validada com Banco do Brasil,
# dez/2025), sem redefinir o que o BCB já reporta.
_DRE_RECEITA_FIXA = [
    "rendas_interfinanceiras", "rendas_tvm", "receita_credito_total",
    "rendas_arrendamento", "rendas_outras_credito", "resultado_servicos_pagamento",
    "rendas_tarifas_bancarias", "outras_rendas_servicos", "outras_receitas",
]
_DRE_DESPESA_FIXA = [
    "perda_esperada_total", "despesas_captacao", "despesas_divida_capital",
    "perda_esperada_pagamento", "despesas_pessoal", "despesas_administrativas",
    "perda_esperada_outras_operacoes", "despesas_tributarias", "outras_despesas",
]
_DRE_AMBIGUAS = [
    "resultado_derivativos", "outros_resultado_intermediacao",
    "outros_resultado_pagamento", "resultado_participacoes",
    "imposto_renda_csll", "participacoes_lucro",
]


def _somar(row: pd.Series, campos: list[str]) -> float:
    return sum(v for c in campos if pd.notna(v := row.get(c)))


def multiplicador_anualizacao(anomes: int) -> float | None:
    """O DRE do IF.data é ACUMULADO NO ANO (1ºT = jan-mar, 2ºT = jan-jun,
    3ºT = jan-set, 4ºT = jan-dez) — pra anualizar o Lucro Líquido antes de
    dividir pelo Ativo Total (ROA), multiplica pelo inverso da fração do
    ano já decorrida: 1ºT ×4, 2ºT ×2, 3ºT ×4/3, 4ºT ×1 (já é o ano
    inteiro). Fórmula e regra pedidas explicitamente pelo usuário
    (jul/2026); resultado marcado como "ROA anualizado" na UI."""
    trimestre = {3: 1, 6: 2, 9: 3, 12: 4}.get(anomes % 100)
    return None if trimestre is None else 4 / trimestre


def ultimos_n_anos(periodos: list[int], anos: int = HIST_ANOS) -> list[int]:
    """Recorta uma lista de competências trimestrais (AAAAMM) pros últimos
    N anos — janela padrão pedida pelo usuário pra todos os relatórios de
    IF (jul/2026). Simples: mantém só as últimas anos*4 entradas (a lista
    já vem ordenada por competência)."""
    return periodos[-(anos * 4):] if len(periodos) > anos * 4 else periodos


def calcular_receita_despesa_total(row: pd.Series) -> tuple[float | None, float | None]:
    # a árvore completa (a, b, e, f, g, h, i, j, l1-l3, m-u) só existe no
    # esquema novo (contas 140xxx-142xxx, a partir de 202503) — trimestres
    # do esquema antigo (78xxx) só têm receita_credito_total e
    # perda_esperada_credito mapeados (ver v_ifdata_dre em schema.sql), sem
    # os demais componentes. Calcular a soma mesmo assim resultaria numa
    # Receita/Despesa Total muito menor que a real, que NUNCA reconciliaria
    # com o Lucro Líquido (esse sim sempre presente, mapeado nos dois
    # esquemas) — bug real encontrado rodando o pipeline completo (Olinda
    # recuperada trouxe de volta trimestres 2020-2024 no esquema antigo,
    # que antes nem apareciam por falta de dado). Usa `rendas_interfinanceiras`
    # (só existe no esquema novo) como sinalizador; se ausente, não calcula
    # — melhor mostrar "sem dado" do que um total incompleto e enganoso.
    if pd.isna(row.get("rendas_interfinanceiras")):
        return None, None
    receita = _somar(row, _DRE_RECEITA_FIXA)
    despesa = -_somar(row, _DRE_DESPESA_FIXA)
    for campo in _DRE_AMBIGUAS:
        v = row.get(campo)
        if pd.isna(v):
            continue
        if v >= 0:
            receita += v
        else:
            despesa += -v
    return receita, despesa

RUBRICAS_CARTEIRA = [
    ("modalidade", "Carteira PJ por modalidade", "Total"),
    ("vencidos", "Vencidos PJ por modalidade (>15 dias)", "Vencido a Partir de 15 Dias"),
]


def limpar(registro: dict) -> dict:
    """NaN -> None pra virar `null` no JSON. `pd.isna` não aceita lista
    (os campos hist_* de instituicoes_dre) sem virar array ambíguo — só
    aplica o teste em valores escalares."""
    return {k: (None if not isinstance(v, list) and pd.isna(v) else v)
            for k, v in registro.items()}


def segmentacao(con: sqlite3.Connection) -> list[dict]:
    # COUNT(d.codinst), nao COUNT(*): o cadastro (TipoInstituicao=1) traz tambem
    # as entidades individuais/subsidiarias de cada conglomerado (herdam o
    # segmento do grupo mas nao tem DRE consolidado proprio -> ativo_total fica
    # NULL no LEFT JOIN); contar so quem tem match real evita inflar a
    # contagem de instituicoes com essas subsidiarias sem DRE.
    df = pd.read_sql_query(
        "SELECT i.segmento AS segmento, COUNT(d.codinst) AS instituicoes, "
        "ROUND(SUM(d.ativo_total) / 1e6, 2) AS ativo_total_bi "
        "FROM ifdata_instituicao i "
        "LEFT JOIN v_ifdata_dre d ON d.codinst = i.codinst AND d.anomes = i.anomes "
        "AND d.ativo_total IS NOT NULL "
        "WHERE i.anomes = (SELECT MAX(anomes) FROM ifdata_instituicao) "
        "AND i.situacao = 'A' "
        "GROUP BY i.segmento ORDER BY ativo_total_bi DESC", con)
    return [limpar(r) for r in df.to_dict("records")]


def evolucao_sistema(con: sqlite3.Connection) -> list[dict]:
    """Série histórica (últimos 5 anos) de instituições ativas e ativo total
    do sistema inteiro, uma linha por competência — usada só pras legendas
    dos KPIs principais (variação nominal/% vs. trimestre e ano anterior),
    não pro detalhamento por segmento (que é sempre foto da última
    competência)."""
    periodos = ultimos_n_anos(
        [r[0] for r in con.execute("SELECT DISTINCT anomes FROM ifdata_instituicao ORDER BY anomes")])
    if not periodos:
        return []
    placeholders = ",".join("?" * len(periodos))
    df = pd.read_sql_query(
        # COUNT(DISTINCT d.codinst), nao i.codinst: mesmo motivo de segmentacao()
        # acima - so contar instituicoes com DRE consolidado de verdade, nao as
        # subsidiarias individuais que o cadastro TipoInstituicao=1 tambem traz.
        f"SELECT i.anomes AS anomes, COUNT(DISTINCT d.codinst) AS instituicoes, "
        f"ROUND(SUM(d.ativo_total) / 1e6, 2) AS ativo_total_bi "
        f"FROM ifdata_instituicao i "
        f"LEFT JOIN v_ifdata_dre d ON d.codinst = i.codinst AND d.anomes = i.anomes "
        f"AND d.ativo_total IS NOT NULL "
        f"WHERE i.situacao = 'A' AND i.anomes IN ({placeholders}) "
        f"GROUP BY i.anomes ORDER BY i.anomes", con, params=periodos)
    return [limpar(r) for r in df.to_dict("records")]


def segmentacao_detalhe(con: sqlite3.Connection, top_n: int = TOP_N_SEGMENTO) -> dict:
    """Top N instituições DENTRO de cada segmento (S1-S5), por ativo total,
    na última competência — INDEPENDENTE do ranking global de
    `instituicoes_dre` (que só pega o top TOP_N_DRE geral, dominado por
    S1-S3; sem isso, o detalhamento de S4/S5 ficaria vazio ou quase, já que
    são os segmentos com as MENORES instituições). Pedido explícito do
    usuário pro S5 (jul/2026), aplicado de forma uniforme aos 5 segmentos —
    S1/S2 têm poucas instituições no total e aparecem inteiros de qualquer
    forma."""
    df = pd.read_sql_query(
        "SELECT i.segmento, i.codinst, i.nome, i.uf, d.ativo_total, d.lucro_liquido, "
        "d.anomes "
        "FROM ifdata_instituicao i JOIN v_ifdata_dre d "
        "ON d.codinst = i.codinst AND d.anomes = i.anomes "
        "WHERE i.anomes = (SELECT MAX(anomes) FROM ifdata_instituicao) "
        "AND i.situacao = 'A' AND d.ativo_total IS NOT NULL AND i.segmento IS NOT NULL",
        con)
    if df.empty:
        return {}
    mult = df["anomes"].iloc[0]
    mult = multiplicador_anualizacao(int(mult))
    df["roa"] = (df["lucro_liquido"] * mult / df["ativo_total"] * 100).round(2) if mult else None
    resultado = {}
    for segmento, grp in df.groupby("segmento"):
        top = grp.sort_values("ativo_total", ascending=False).head(top_n)
        resultado[segmento] = [
            limpar(dict(codinst=r["codinst"], nome=r["nome"], uf=r["uf"],
                        ativo_total=r["ativo_total"], lucro_liquido=r["lucro_liquido"],
                        roa=r["roa"]))
            for _, r in top.iterrows()
        ]
    return resultado


def todas_instituicoes(con: sqlite3.Connection) -> list[dict]:
    """Todas as instituições ativas na última competência — nome, segmento,
    UF, ativo total, lucro líquido, ROA — SEM limite de quantidade (pedido
    explícito do usuário: tabela de busca/filtro por nome e por UF, além
    dos filtros por segmento já existentes)."""
    df = pd.read_sql_query(
        "SELECT i.codinst, i.nome, i.segmento, i.uf, d.ativo_total, d.lucro_liquido, d.anomes "
        "FROM ifdata_instituicao i JOIN v_ifdata_dre d "
        "ON d.codinst = i.codinst AND d.anomes = i.anomes "
        "WHERE i.anomes = (SELECT MAX(anomes) FROM ifdata_instituicao) "
        "AND i.situacao = 'A' AND d.ativo_total IS NOT NULL "
        "ORDER BY d.ativo_total DESC", con)
    if df.empty:
        return []
    mult = multiplicador_anualizacao(int(df["anomes"].iloc[0]))
    df["roa"] = (df["lucro_liquido"] * mult / df["ativo_total"] * 100).round(2) if mult else None
    return [limpar(dict(codinst=r["codinst"], nome=r["nome"], segmento=r["segmento"],
                        uf=r["uf"], ativo_total=r["ativo_total"],
                        lucro_liquido=r["lucro_liquido"], roa=r["roa"]))
            for _, r in df.iterrows()]


def instituicoes_dre(con: sqlite3.Connection) -> tuple[list, list, list]:
    """Ranking + histórico (nível instituição individual) — só as TOP_N_DRE
    por ativo total na última competência entram com série completa, pra
    não estourar o payload com milhares de instituições pequenas/inativas."""
    periodos = [r[0] for r in con.execute(
        "SELECT DISTINCT anomes FROM ifdata_dre_valor ORDER BY anomes")]
    periodos = ultimos_n_anos(periodos, anos=HIST_ANOS_DRE)
    if not periodos:
        return [], [], []

    top_codinst = [r[0] for r in con.execute(
        "SELECT codinst FROM v_ifdata_dre "
        "WHERE anomes = (SELECT MAX(anomes) FROM v_ifdata_dre WHERE ativo_total IS NOT NULL) "
        "ORDER BY ativo_total DESC LIMIT ?", (TOP_N_DRE,)).fetchall()]
    if not top_codinst:
        return periodos, [], []

    placeholders = ",".join("?" * len(top_codinst))
    colunas_base = [
        "codinst", "anomes", "nome", "segmento", "uf", "situacao",
        "ativo_total", "receita_juros_credito", "receita_credito_total",
        "perda_esperada_credito", "perda_esperada_total", "lucro_liquido",
    ]
    colunas_calculo = _DRE_RECEITA_FIXA + _DRE_DESPESA_FIXA + _DRE_AMBIGUAS
    colunas = colunas_base + [c for c in colunas_calculo if c not in colunas_base]
    df = pd.read_sql_query(
        f"SELECT {', '.join(colunas)} FROM v_ifdata_dre "
        f"WHERE codinst IN ({placeholders})", con, params=top_codinst)
    df["receita_total"], df["despesa_total"] = zip(
        *df.apply(calcular_receita_despesa_total, axis=1))
    # o BCB grava contas de despesa com sinal negativo (convenção COSIF, pra
    # somar direto na árvore sem subtração) — perda_esperada_credito herda
    # esse sinal, mas exibida como "contida" na Despesa Total (sempre uma
    # magnitude positiva) ficaria com sinal invertido em relação ao total,
    # o que é confuso mesmo estando certo. Invertida aqui só pra exibição —
    # não entra na soma de despesa_total (que já usa perda_esperada_total).
    df["perda_esperada_credito"] = -df["perda_esperada_credito"]
    # ROA ANUALIZADO = (Lucro Líquido × multiplicador do trimestre) / Ativo
    # Total, em % — o DRE do IF.data é acumulado no ano, então cada
    # competência precisa do seu próprio multiplicador (ver
    # multiplicador_anualizacao) antes de dividir pelo ativo total.
    df["_mult_anual"] = df["anomes"].apply(multiplicador_anualizacao)
    df["roa"] = (df["lucro_liquido"] * df["_mult_anual"] / df["ativo_total"] * 100).round(2)
    checavel = df.dropna(subset=["lucro_liquido"])
    if not checavel.empty:
        divergencia = (checavel["receita_total"] - checavel["despesa_total"]
                       - checavel["lucro_liquido"]).abs()
        piores = divergencia[divergencia > 1].sort_values(ascending=False)
        if not piores.empty:
            print(f"  [aviso] Receita Total - Despesa Total != Lucro Líquido em "
                  f"{len(piores)}/{len(checavel)} linhas do DRE (maior divergência: "
                  f"{piores.iloc[0]:.1f}) — conferir contas ambíguas/rendas de arrendamento.")

    instituicoes = []
    for codinst, grp in df.groupby("codinst"):
        grp = grp.sort_values("anomes").set_index("anomes").reindex(periodos)
        ultima = grp.dropna(subset=["nome"]).iloc[-1] if grp["nome"].notna().any() else grp.iloc[-1]
        registro = dict(codinst=codinst, nome=ultima["nome"], segmento=ultima["segmento"],
                         uf=ultima["uf"], situacao=ultima["situacao"])
        for campo, _ in DRE_CAMPOS:
            registro[f"hist_{campo}"] = [None if pd.isna(v) else v for v in grp[campo]]
        instituicoes.append(limpar(registro))

    ranking = sorted(
        ({"codinst": r["codinst"], "nome": r["nome"], "segmento": r["segmento"],
          "uf": r["uf"], **{c: r[f"hist_{c}"][-1] for c, _ in DRE_CAMPOS}}
         for r in instituicoes),
        key=lambda r: (r["ativo_total"] is None, -(r["ativo_total"] or 0)),
    )
    return periodos, instituicoes, ranking


def carteira_instituicoes(con: sqlite3.Connection) -> list[dict]:
    """Carteira de crédito ativa por conglomerado prudencial — só a última
    competência (é um retrato do portfólio, não uma série histórica) — top
    30 por carteira PJ total, mesma regra de ranking já validada em
    gerar_relatorios.py (rankear pela própria carteira, nunca pelo ativo
    total do DRE — são níveis de consolidação diferentes)."""
    top = pd.read_sql_query(
        "SELECT codinst, nome, uf, valor AS carteira_pj_total FROM v_ifdata_porte "
        "WHERE anomes = (SELECT MAX(anomes) FROM v_ifdata_porte) "
        "AND porte = 'Total da Carteira de Pessoa Jurídica' "
        "ORDER BY valor DESC LIMIT ?", con, params=(TOP_N_CARTEIRA,))
    if top.empty:
        return []
    codinsts = top["codinst"].tolist()
    placeholders = ",".join("?" * len(codinsts))

    aging = pd.read_sql_query(
        f"SELECT codinst, modalidade, bucket, valor FROM v_ifdata_aging "
        f"WHERE cliente = 'PJ' AND codinst IN ({placeholders}) "
        f"AND anomes = (SELECT MAX(anomes) FROM v_ifdata_aging)", con, params=codinsts)
    cnae = pd.read_sql_query(
        f"SELECT codinst, atividade, bucket, valor FROM v_ifdata_cnae "
        f"WHERE codinst IN ({placeholders}) "
        f"AND anomes = (SELECT MAX(anomes) FROM v_ifdata_cnae)", con, params=codinsts)
    porte = pd.read_sql_query(
        f"SELECT codinst, porte, valor FROM v_ifdata_porte "
        f"WHERE codinst IN ({placeholders}) "
        f"AND anomes = (SELECT MAX(anomes) FROM v_ifdata_porte) "
        f"AND porte != 'Total da Carteira de Pessoa Jurídica'", con, params=codinsts)

    registros = []
    for _, row in top.iterrows():
        codinst = row["codinst"]
        sub_aging = aging[aging["codinst"] == codinst]
        modalidade = (sub_aging[sub_aging["bucket"] == "Total"]
                      .sort_values("valor", ascending=False))
        vencidos = (sub_aging[sub_aging["bucket"] == "Vencido a Partir de 15 Dias"]
                    .sort_values("valor", ascending=False))
        sub_cnae = (cnae[(cnae["codinst"] == codinst) & (cnae["bucket"] == "Total")]
                    .sort_values("valor", ascending=False))
        sub_porte = porte[porte["codinst"] == codinst].sort_values("valor", ascending=False)
        registros.append(dict(
            codinst=codinst, nome=row["nome"], uf=row["uf"],
            carteira_pj_total=row["carteira_pj_total"],
            modalidade=[[r["modalidade"], r["valor"]] for _, r in modalidade.iterrows()],
            vencidos=[[r["modalidade"], r["valor"]] for _, r in vencidos.iterrows()],
            cnae=[[r["atividade"], r["valor"]] for _, r in sub_cnae.iterrows()],
            porte=[[r["porte"], r["valor"]] for _, r in sub_porte.iterrows()],
        ))
    return registros


def taxa_juros(con: sqlite3.Connection) -> dict:
    """Última competência de cada família (semanal vs. mensal-imobiliário,
    ver nota em schema.sql) por modalidade, pra montar um ranking (mais
    barata primeiro) com seletor de modalidade no cliente — mais o
    histórico dos últimos 5 anos por instituição×modalidade, pra abrir ao
    clicar numa linha do ranking (pedido explícito do usuário, jul/2026)."""
    df = pd.read_sql_query(
        "SELECT instituicao, segmento, modalidade, taxa_mes_pct, taxa_ano_pct "
        "FROM taxa_juros_instituicao "
        "WHERE inicio_periodo = (SELECT MAX(inicio_periodo) FROM taxa_juros_instituicao "
        "WHERE modalidade NOT LIKE '%imobili%') "
        "OR (modalidade LIKE '%imobili%' AND inicio_periodo = (SELECT MAX(inicio_periodo) "
        "FROM taxa_juros_instituicao WHERE modalidade LIKE '%imobili%'))", con)
    modalidades = sorted(df["modalidade"].dropna().unique().tolist())
    por_modalidade = {}
    for mod, grp in df.groupby("modalidade"):
        grp = grp.sort_values("taxa_ano_pct")
        por_modalidade[mod] = [
            dict(instituicao=r["instituicao"], segmento=r["segmento"],
                 taxa_mes_pct=r["taxa_mes_pct"], taxa_ano_pct=r["taxa_ano_pct"])
            for _, r in grp.iterrows()
        ]

    # histórico: um eixo de período compartilhado (todas as datas distintas
    # nos últimos 5 anos) + hist_taxa_ano_pct por (modalidade, instituição) —
    # só pras combinações que aparecem no ranking acima, pra não estourar o
    # payload com instituições/modalidades sem dado recente
    corte = f"{pd.Timestamp.now().year - HIST_ANOS}-01-01"
    periodos_taxa = [r[0] for r in con.execute(
        "SELECT DISTINCT inicio_periodo FROM taxa_juros_instituicao "
        "WHERE inicio_periodo >= ? ORDER BY inicio_periodo", (corte,)).fetchall()]
    hist = pd.read_sql_query(
        "SELECT inicio_periodo, modalidade, instituicao, taxa_ano_pct "
        "FROM taxa_juros_instituicao WHERE inicio_periodo >= ?", con, params=(corte,))
    idx_periodo = {p: i for i, p in enumerate(periodos_taxa)}
    historico: dict[str, dict[str, list]] = {}
    for (mod, inst), grp in hist.groupby(["modalidade", "instituicao"]):
        arr = [None] * len(periodos_taxa)
        for _, r in grp.iterrows():
            pos = idx_periodo.get(r["inicio_periodo"])
            if pos is not None and pd.notna(r["taxa_ano_pct"]):
                arr[pos] = r["taxa_ano_pct"]
        historico.setdefault(mod, {})[inst] = arr

    return dict(modalidades=modalidades, por_modalidade=por_modalidade,
                periodos_historico=periodos_taxa, historico=historico)


def montar_dados(con: sqlite3.Connection) -> dict:
    periodos_dre, insts_dre, ranking_dre = instituicoes_dre(con)
    return dict(
        meta=dict(anomes_max=periodos_dre[-1] if periodos_dre else None,
                  total_periodos=len(periodos_dre)),
        periodos_dre=periodos_dre,
        segmentacao=segmentacao(con),
        segmentacao_detalhe=segmentacao_detalhe(con),
        evolucao_sistema=evolucao_sistema(con),
        instituicoes_dre=insts_dre,
        ranking_dre=ranking_dre,
        todas_instituicoes=todas_instituicoes(con),
        carteira=carteira_instituicoes(con),
        taxa_juros=taxa_juros(con),
    )


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    dados = montar_dados(con)
    con.close()

    template = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(dados, ensure_ascii=False)
    html = template.replace("__DADOS_JSON__", payload)
    destino = REPORTS / "instituicoes_financeiras.html"
    destino.write_text(html, encoding="utf-8")
    print(f"  instituicoes_financeiras.html gerado ({len(dados['instituicoes_dre'])} IFs "
          f"(DRE), {len(dados['carteira'])} IFs (carteira), payload {len(payload)/1e6:.1f} MB)")
    print(f"  {destino}")


if __name__ == "__main__":
    main()
