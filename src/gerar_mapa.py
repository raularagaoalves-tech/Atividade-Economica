# -*- coding: utf-8 -*-
"""
Gera o mapa/painel regional interativo (reports/mapa.html) a partir do banco
data/db/atividade.db — mapa hierárquico Brasil→UF→município (PIB, crédito,
emprego, empresas), visão por UF, ranking nacional por seção CNAE e
histórico Crédito/PIB.

Autocontido: os dados são embutidos como JSON no HTML (sem servidor, sem
chamada de rede) — abre direto no navegador, roda 100% local.

Uso:
    python gerar_mapa.py
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

from caminhos import DB_ATIVIDADE as DB_PATH

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
TEMPLATE = Path(__file__).resolve().parent / "mapa_template.html"

# quantas seções CNAE aparecem no detalhamento por município/UF antes de dobrar em "Outras"
CNAE_TOP_N_MUNICIPIO = 6
CNAE_TOP_N_UF = 8
# teto de histórico (anos para séries anuais, meses para séries mensais)
MAX_ANOS_HISTORICO = 10
MAX_MESES_HISTORICO = MAX_ANOS_HISTORICO * 12

# rubrica ESTBAN exibida no detalhamento de crédito bancário (exclui
# provisão/ativo total — são agregados de balanço, não "produtos" de crédito)
RUBRICAS_ESTBAN = [
    ("emprestimos_mil", "Empréstimos"),
    ("financiamentos_mil", "Financiamentos"),
    ("credito_rural_mil", "Crédito rural"),
    ("credito_imobiliario_mil", "Crédito imobiliário"),
    ("outras_oper_credito_mil", "Outras operações"),
    ("poupanca_mil", "Poupança"),
    ("deposito_prazo_mil", "Depósito a prazo"),
]

# municipio.regiao ("Centro-Oeste") -> credito_detalhado_serie.regiao ("CENTRO_OESTE")
REGIAO_MAP = {"Norte": "NORTE", "Nordeste": "NORDESTE", "Centro-Oeste": "CENTRO_OESTE",
              "Sudeste": "SUDESTE", "Sul": "SUL"}


def limpar(registro: dict) -> dict:
    return {k: (None if pd.isna(v) else v) for k, v in registro.items()}


def serie_alinhada(df: pd.DataFrame, index_col: str, period_col: str,
                   value_col: str, periodos: list) -> dict:
    """Pivota df para {chave: [valor alinhado a `periodos`, ...]} — um único
    array de período compartilhado entre todas as chaves (município/UF),
    em vez de repetir [periodo, valor] em cada série (economiza payload)."""
    pivot = df.pivot_table(index=index_col, columns=period_col, values=value_col, aggfunc="first")
    pivot = pivot.reindex(columns=periodos)
    return {chave: [None if pd.isna(v) else v for v in linha.tolist()]
            for chave, linha in pivot.iterrows()}


def periodos(con: sqlite3.Connection) -> dict:
    ano_min, ano_max = con.execute("SELECT MIN(ano), MAX(ano) FROM pib_municipio").fetchone()
    anos_pib = list(range(max(ano_min, ano_max - MAX_ANOS_HISTORICO + 1), ano_max + 1))

    competencias_credito = [r[0] for r in con.execute(
        "SELECT DISTINCT competencia FROM estban_municipio ORDER BY competencia")]
    competencias_credito = competencias_credito[-MAX_MESES_HISTORICO:]

    competencias_emprego = [r[0] for r in con.execute(
        "SELECT DISTINCT competencia FROM caged_mensal ORDER BY competencia")]
    competencias_emprego = competencias_emprego[-MAX_MESES_HISTORICO:]

    anos_empresas = [r[0] for r in con.execute(
        "SELECT DISTINCT ano FROM cempre_municipio ORDER BY ano")]
    anos_empresas = anos_empresas[-MAX_ANOS_HISTORICO:]

    # janela própria de população (não presa ao teto do PIB) — população tem
    # dado mais recente que o PIB (ex.: 2025 vs. 2023), então usar sua última
    # janela real evita esconder anos que já existem na fonte
    ano_min_pop, ano_max_pop = con.execute(
        "SELECT MIN(ano), MAX(ano) FROM municipio_populacao_ref").fetchone()
    anos_populacao = list(range(max(ano_min_pop, ano_max_pop - MAX_ANOS_HISTORICO + 1), ano_max_pop + 1))

    competencias_rj = [r[0] for r in con.execute(
        "SELECT DISTINCT competencia FROM datajud_rj_falencia_mensal ORDER BY competencia")]
    competencias_rj = competencias_rj[-MAX_MESES_HISTORICO:]

    return dict(pib=anos_pib, credito=competencias_credito,
                emprego=competencias_emprego, empresas=anos_empresas,
                populacao=anos_populacao, rj_falencia=competencias_rj)


def meta(con: sqlite3.Connection, per: dict) -> dict:
    ano_pib = con.execute("SELECT MAX(ano) FROM pib_municipio").fetchone()[0]
    ano_composicao = con.execute(
        "SELECT MAX(ano) FROM pib_municipio WHERE vab_agro_mil IS NOT NULL"
    ).fetchone()[0]
    ano_cempre = con.execute("SELECT MAX(ano) FROM cempre_municipio").fetchone()[0]
    competencia_credito = con.execute("SELECT MAX(competencia) FROM estban_municipio").fetchone()[0]
    competencia_caged = con.execute("SELECT MAX(competencia) FROM caged_mensal").fetchone()[0]
    totais = con.execute(
        "SELECT SUM(pib_mil), SUM(credito_total_mil), SUM(empresas), "
        "SUM(populacao), COUNT(*) FROM v_ranking_municipios"
    ).fetchone()
    return dict(
        ano_pib=ano_pib, ano_composicao=ano_composicao, ano_cempre=ano_cempre,
        competencia_credito=competencia_credito, competencia_caged=competencia_caged,
        total_pib_mil=totais[0], total_credito_mil=totais[1], total_empresas=totais[2],
        total_populacao=totais[3], total_municipios=totais[4],
        periodos=per,
    )


def estban_breakdown(con: sqlite3.Connection, tabela: str, chave_col: str) -> dict:
    """Detalhamento de crédito bancário por rubrica ESTBAN (última competência),
    reutilizável para município (m_credito_municipio) e UF (v_credito_uf)."""
    ultima = con.execute(f"SELECT MAX(competencia) FROM {tabela}").fetchone()[0]
    campos = ", ".join(c for c, _ in RUBRICAS_ESTBAN)
    df = pd.read_sql_query(
        f"SELECT {chave_col} AS chave, {campos} FROM {tabela} WHERE competencia = ?",
        con, params=(ultima,),
    )
    saida = {}
    for r in df.to_dict("records"):
        chave = r.pop("chave")
        saida[chave] = [[rotulo, r[campo]] for campo, rotulo in RUBRICAS_ESTBAN
                        if pd.notna(r[campo])]
    return saida


def municipios(con: sqlite3.Connection, per: dict) -> list[dict]:
    base = pd.read_sql_query(
        "SELECT r.cod_ibge7 AS cod, r.nome, r.uf, r.regiao, m.lat, m.lng, "
        "m.capital, m.nome_norm, "
        "r.populacao AS pop, r.pib_mil AS pib, r.pib_per_capita AS pibpc, "
        "r.rank_pib_brasil AS rkbr, r.credito_total_mil AS cred, "
        "r.credito_var_12m_pct AS credv12, r.credito_pib_pct AS credpib, "
        "r.caged_saldo_12m AS cagedv12, r.saldo_12m_por_mil_hab AS cagedpmil, "
        "r.empresas AS emp, r.empresas_por_mil_hab AS emppmil "
        "FROM v_ranking_municipios r JOIN municipio m ON m.cod_ibge7 = r.cod_ibge7",
        con,
    )
    porte = pd.read_sql_query(
        "SELECT cod_ibge7 AS cod, faixa_pib AS fpib, faixa_pop AS fpop "
        "FROM v_porte_municipio",
        con,
    )
    base = base.merge(porte, on="cod", how="left")

    pib_df = pd.read_sql_query(
        f"SELECT cod_ibge7 AS cod, ano, pib_mil FROM pib_municipio "
        f"WHERE ano >= {per['pib'][0]}", con)
    hist_pib = serie_alinhada(pib_df, "cod", "ano", "pib_mil", per["pib"])

    credito_df = pd.read_sql_query(
        "SELECT cod_ibge7 AS cod, competencia, credito_total_mil FROM m_credito_municipio", con)
    hist_credito = serie_alinhada(credito_df, "cod", "competencia", "credito_total_mil", per["credito"])

    emprego_df = pd.read_sql_query(
        "SELECT cod_ibge7 AS cod, competencia, saldo_12m FROM m_emprego_municipio", con)
    hist_emprego = serie_alinhada(emprego_df, "cod", "competencia", "saldo_12m", per["emprego"])

    empresas_df = pd.read_sql_query(
        "SELECT cod_ibge7 AS cod, ano, SUM(empresas) AS empresas "
        "FROM cempre_municipio GROUP BY cod_ibge7, ano", con)
    hist_empresas = serie_alinhada(empresas_df, "cod", "ano", "empresas", per["empresas"])

    # duas séries de população: uma na janela própria mais recente (o que o
    # gráfico "População" mostra por padrão — não fica preso ao teto do PIB,
    # que é mais antigo) e outra alinhada à janela do PIB (só para dividir
    # índice a índice no cálculo de PIB per capita). Cobertura validada:
    # 5571/5571 municípios têm população em todos os anos 2014-2023 (sem
    # buracos), então o recorte por PIB nunca perde dado por gap.
    pop_fresh_df = pd.read_sql_query(
        f"SELECT cod_ibge7 AS cod, ano, populacao FROM municipio_populacao_ref "
        f"WHERE ano >= {per['populacao'][0]}", con)
    hist_populacao = serie_alinhada(pop_fresh_df, "cod", "ano", "populacao", per["populacao"])

    pop_pib_df = pd.read_sql_query(
        f"SELECT cod_ibge7 AS cod, ano, populacao FROM municipio_populacao_ref "
        f"WHERE ano >= {per['pib'][0]} AND ano <= {per['pib'][-1]}", con)
    hist_populacao_pib = serie_alinhada(pop_pib_df, "cod", "ano", "populacao", per["pib"])

    cnae_df = pd.read_sql_query(
        "SELECT cod_ibge7 AS cod, secao_nome, empresas FROM v_empresas_municipio "
        "WHERE ano = (SELECT MAX(ano) FROM cempre_municipio) AND empresas IS NOT NULL "
        "ORDER BY cod_ibge7, empresas DESC",
        con,
    )
    cnae_map: dict[str, list] = {}
    for cod, grp in cnae_df.groupby("cod"):
        top = grp.head(CNAE_TOP_N_MUNICIPIO)
        cnae_map[cod] = [[n, v] for n, v in zip(top["secao_nome"], top["empresas"])]

    estban_map = estban_breakdown(con, "m_credito_municipio", "cod_ibge7")

    registros = []
    for r in base.to_dict("records"):
        cod = r["cod"]
        limpo = limpar(r)
        limpo["capital"] = bool(r["capital"])
        limpo["hist_pib"] = hist_pib.get(cod, [])
        limpo["hist_credito"] = hist_credito.get(cod, [])
        limpo["hist_emprego"] = hist_emprego.get(cod, [])
        limpo["hist_empresas"] = hist_empresas.get(cod, [])
        limpo["hist_populacao"] = hist_populacao.get(cod, [])
        limpo["hist_populacao_pib"] = hist_populacao_pib.get(cod, [])
        limpo["cnae"] = cnae_map.get(cod, [])
        limpo["estban"] = estban_map.get(cod, [])
        registros.append(limpo)
    return registros


def ufs(con: sqlite3.Connection, per: dict) -> list[dict]:
    atual = pd.read_sql_query("SELECT * FROM v_atividade_uf", con)
    comp_recente = pd.read_sql_query(
        "SELECT uf, pct_agro, pct_industria, pct_servicos FROM v_pib_uf_historico "
        "WHERE ano = (SELECT MAX(ano) FROM pib_municipio WHERE vab_agro_mil IS NOT NULL)",
        con,
    ).set_index("uf")

    pib_df = pd.read_sql_query(
        f"SELECT uf, ano, pib_mil, populacao FROM v_pib_uf_historico WHERE ano >= {per['pib'][0]}", con)
    hist_pib = serie_alinhada(pib_df, "uf", "ano", "pib_mil", per["pib"])
    hist_populacao_pib = serie_alinhada(pib_df, "uf", "ano", "populacao", per["pib"])

    pop_fresh_df = pd.read_sql_query(
        f"SELECT m.uf, r.ano, SUM(r.populacao) AS populacao FROM municipio_populacao_ref r "
        f"JOIN municipio m ON m.cod_ibge7 = r.cod_ibge7 "
        f"WHERE r.ano >= {per['populacao'][0]} GROUP BY m.uf, r.ano", con)
    hist_populacao = serie_alinhada(pop_fresh_df, "uf", "ano", "populacao", per["populacao"])

    credito_df = pd.read_sql_query("SELECT uf, competencia, credito_total_mil FROM v_credito_uf", con)
    hist_credito = serie_alinhada(credito_df, "uf", "competencia", "credito_total_mil", per["credito"])

    emprego_df = pd.read_sql_query("SELECT uf, competencia, saldo_12m FROM v_emprego_uf_historico", con)
    hist_emprego = serie_alinhada(emprego_df, "uf", "competencia", "saldo_12m", per["emprego"])

    empresas_df = pd.read_sql_query(
        "SELECT m.uf, c.ano, SUM(c.empresas) AS empresas FROM cempre_municipio c "
        "JOIN municipio m ON m.cod_ibge7 = c.cod_ibge7 GROUP BY m.uf, c.ano", con)
    hist_empresas = serie_alinhada(empresas_df, "uf", "ano", "empresas", per["empresas"])

    estban_map = estban_breakdown(con, "v_credito_uf", "uf")

    rj_df = pd.read_sql_query(
        f"SELECT uf, competencia, processos_rj FROM v_recuperacao_falencia_uf "
        f"WHERE competencia >= {per['rj_falencia'][0] if per['rj_falencia'] else 0}", con)
    hist_rj = serie_alinhada(rj_df, "uf", "competencia", "processos_rj", per["rj_falencia"])
    extra_df = pd.read_sql_query(
        f"SELECT uf, competencia, processos_extrajudicial FROM v_recuperacao_falencia_uf "
        f"WHERE competencia >= {per['rj_falencia'][0] if per['rj_falencia'] else 0}", con)
    hist_extrajudicial = serie_alinhada(extra_df, "uf", "competencia", "processos_extrajudicial", per["rj_falencia"])
    falencia_df = pd.read_sql_query(
        f"SELECT uf, competencia, processos_falencia FROM v_recuperacao_falencia_uf "
        f"WHERE competencia >= {per['rj_falencia'][0] if per['rj_falencia'] else 0}", con)
    hist_falencia = serie_alinhada(falencia_df, "uf", "competencia", "processos_falencia", per["rj_falencia"])

    saldo_regiao_df = pd.read_sql_query(
        "SELECT cliente, porte, regiao, valor FROM v_credito_saldo_regiao "
        "WHERE competencia = (SELECT MAX(competencia) FROM v_credito_saldo_regiao)", con)

    registros = []
    for r in atual.to_dict("records"):
        uf = r["uf"]
        if uf in comp_recente.index:
            c = comp_recente.loc[uf]
            r["pct_agro"] = c["pct_agro"]
            r["pct_industria"] = c["pct_industria"]
            r["pct_servicos"] = c["pct_servicos"]
        else:
            r["pct_agro"] = r["pct_industria"] = r["pct_servicos"] = None
        limpo = limpar(r)
        limpo["hist_pib"] = hist_pib.get(uf, [])
        limpo["hist_credito"] = hist_credito.get(uf, [])
        limpo["hist_emprego"] = hist_emprego.get(uf, [])
        limpo["hist_empresas"] = hist_empresas.get(uf, [])
        limpo["hist_populacao"] = hist_populacao.get(uf, [])
        limpo["hist_populacao_pib"] = hist_populacao_pib.get(uf, [])
        limpo["estban"] = estban_map.get(uf, [])
        limpo["hist_rj"] = hist_rj.get(uf, [])
        limpo["hist_extrajudicial"] = hist_extrajudicial.get(uf, [])
        limpo["hist_falencia"] = hist_falencia.get(uf, [])

        regiao_cod = REGIAO_MAP.get(r["regiao"])
        sub = saldo_regiao_df[saldo_regiao_df["regiao"] == regiao_cod] if regiao_cod else saldo_regiao_df.iloc[0:0]
        limpo["saldo_regiao"] = [[row["cliente"], row["porte"], row["valor"]]
                                 for _, row in sub.iterrows() if pd.notna(row["valor"])]
        registros.append(limpo)
    return registros


def malha_uf(con: sqlite3.Connection) -> dict:
    """Contorno geográfico real de cada UF (IBGE) — geometry (type +
    coordinates) pronta pra projetar no cliente, sem o envelope GeoJSON."""
    rows = con.execute("SELECT uf, geojson FROM malha_uf").fetchall()
    return {uf: json.loads(geo) for uf, geo in rows}


def cnae_nacional(con: sqlite3.Connection) -> list[dict]:
    df = pd.read_sql_query(
        "SELECT secao, secao_nome, empresas, participacao_empresas_pct, "
        "caged_saldo_12m FROM v_atividade_cnae ORDER BY empresas DESC",
        con,
    )
    return [limpar(r) for r in df.to_dict("records")]


def cnae_por_uf(con: sqlite3.Connection) -> dict:
    df = pd.read_sql_query(
        "SELECT uf, secao_nome, empresas FROM v_atividade_cnae_uf "
        "WHERE empresas IS NOT NULL ORDER BY uf, empresas DESC",
        con,
    )
    saida: dict[str, list] = {}
    for uf, grp in df.groupby("uf"):
        top = grp.head(CNAE_TOP_N_UF)
        saida[uf] = [[n, v] for n, v in zip(top["secao_nome"], top["empresas"])]
    return saida


def rj_falencia_ranking(con: sqlite3.Connection) -> dict:
    """Ranking por UF (última competência) + totais nacionais de Recuperação
    Judicial, Extrajudicial e Falência (DataJud/CNJ) — alimenta a seção
    "comparativo" (barras por UF) e o cálculo de participação % de cada UF
    no total nacional, mostrado no detalhamento por UF."""
    ultima = con.execute("SELECT MAX(competencia) FROM datajud_rj_falencia_mensal").fetchone()[0]
    if ultima is None:
        return dict(competencia=None, ranking=[], nacional={})
    ranking = pd.read_sql_query(
        "SELECT uf, processos_rj AS rj, processos_extrajudicial AS extrajudicial, "
        "processos_falencia AS falencia FROM v_recuperacao_falencia_uf WHERE competencia = ?",
        con, params=(ultima,),
    )
    nacional = con.execute(
        "SELECT processos_rj, processos_extrajudicial, processos_falencia "
        "FROM v_recuperacao_falencia_nacional WHERE competencia = ?", (ultima,)
    ).fetchone()
    return dict(
        competencia=int(ultima),
        ranking=[limpar(r) for r in ranking.to_dict("records")],
        nacional=dict(rj=nacional[0], extrajudicial=nacional[1], falencia=nacional[2]),
    )


def credito_pib_historico(con: sqlite3.Connection) -> list[list]:
    rows = con.execute(
        "SELECT competencia, credito_pib_pct FROM v_credito_macro "
        "WHERE credito_pib_pct IS NOT NULL ORDER BY competencia"
    ).fetchall()
    return [[c, v] for c, v in rows]


def montar_dados(con: sqlite3.Connection) -> dict:
    """Monta o payload completo do mapa — única função de montagem, chamada
    tanto por main() (mapa.html avulso) quanto por gerar_sistema.py (portal
    único), pra nunca dessincronizar os dois quando um campo novo é
    adicionado (bug real já cometido e corrigido nesse ponto)."""
    per = periodos(con)
    return dict(
        meta=meta(con, per),
        municipios=municipios(con, per),
        ufs=ufs(con, per),
        cnae_nacional=cnae_nacional(con),
        cnae_por_uf=cnae_por_uf(con),
        credito_pib_historico=credito_pib_historico(con),
        rj_falencia=rj_falencia_ranking(con),
        malha_uf=malha_uf(con),
    )


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    dados = montar_dados(con)
    con.close()

    template = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(dados, ensure_ascii=False)
    html = template.replace("__DADOS_JSON__", payload)
    destino = REPORTS / "mapa.html"
    destino.write_text(html, encoding="utf-8")
    print(f"  mapa.html gerado ({len(dados['municipios'])} municípios, "
          f"{len(dados['ufs'])} UFs, {len(dados['cnae_nacional'])} seções CNAE, "
          f"payload {len(payload)/1e6:.1f} MB)")
    print(f"  {destino}")


if __name__ == "__main__":
    main()
