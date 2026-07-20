# -*- coding: utf-8 -*-
"""
Gera o dashboard interativo de crédito (reports/dashboard.html) a partir do
banco data/db/atividade.db — visão consolidada de saldo histórico + taxa
média, e detalhamentos por modalidade (juros, inadimplência, prazo médio).

Autocontido: os dados são embutidos como JSON no HTML (sem servidor, sem
chamada de rede) — abre direto no navegador, roda 100% local.

Uso:
    python gerar_dashboard.py
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

from caminhos import DB_ATIVIDADE as DB_PATH

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
TEMPLATE = Path(__file__).resolve().parent / "dashboard_template.html"

# quantidade de modalidades exibidas em cada ranking do detalhamento
TOP_N = 12

# quantidade de carteiras (modalidades) no gráfico "5 maiores + Outros"
TOP_N_CARTEIRAS = 5

# modalidades da família "Saldo da carteira de crédito" que são subtotal de
# outra modalidade já considerada (ex. "Cartão de crédito rotativo" é parte
# de "Cartão de crédito total") ou uma classificação PARALELA que reparte o
# mesmo total por outro corte ("Rotativo"/"Não rotativo" somam 100% do total
# de novo, por cima) — excluídas do ranking pra não contar a mesma carteira
# duas vezes. Validado por reconciliação manual: a soma das modalidades-folha
# de cada grupo (cliente × origem) bate com o "... total" daquele grupo.
CARTEIRA_FILHOS_OU_PARALELOS = {
    "Rotativo", "Não rotativo",
    "Capital de giro rotativo", "Capital de giro com prazo de até 365 dias",
    "Capital de giro com prazo superior a 365 dias",
    "Cartão de crédito rotativo", "Cartão de crédito parcelado", "Cartão de crédito à vista",
    "Aquisição de veículos", "Aquisição de outros bens",
    "Arrendamento mercantil de veículos", "Arrendamento mercantil de outros bens",
    "Crédito rural com taxas de mercado", "Crédito rural com taxas reguladas",
    "Financiamento imobiliário com taxas de mercado", "Financiamento imobiliário com taxas reguladas",
    "Financiamento de investimentos com recursos do BNDES",
    "Financiamento agroindustrial com recursos do BNDES",
    "Capital de giro com recursos do BNDES",
    "Crédito pessoal consignado total",
    "Crédito pessoal consignado para trabalhadores do setor público",
    "Crédito pessoal consignado para trabalhadores do setor privado",
    "Crédito pessoal consignado para aposentados e pensionistas do INSS",
    "Crédito pessoal não consignado",
    "Crédito pessoal não consignado vinculado à composição de dívidas",
    "Microcrédito destinado a consumo", "Microcrédito destinado a microempreendedores",
}


def serie_mensal(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        "SELECT competencia, saldo_total, saldo_pj, saldo_pf, "
        "juros_total, inadimplencia_total, credito_pib_pct "
        "FROM v_credito_macro ORDER BY competencia"
    ).fetchall()
    return [dict(r) for r in rows]


def kpis(serie: list[dict]) -> dict:
    if not serie:
        return {}
    atual = serie[-1]
    idx_12m = next((i for i, r in enumerate(serie)
                    if r["competencia"] == atual["competencia"] - 100), None)
    anterior = serie[idx_12m] if idx_12m is not None else None

    def var_pct(campo):
        if not anterior or not anterior[campo] or not atual[campo]:
            return None
        return round(100 * atual[campo] / anterior[campo] - 100, 2)

    def var_pp(campo):
        if not anterior or anterior[campo] is None or atual[campo] is None:
            return None
        return round(atual[campo] - anterior[campo], 2)

    return dict(
        competencia=atual["competencia"],
        saldo_total=atual["saldo_total"],
        saldo_var_12m_pct=var_pct("saldo_total"),
        juros_total=atual["juros_total"],
        juros_var_12m_pp=var_pp("juros_total"),
        inadimplencia_total=atual["inadimplencia_total"],
        inadimplencia_var_12m_pp=var_pp("inadimplencia_total"),
        credito_pib_pct=atual["credito_pib_pct"],
    )


def taxa_media_am(con: sqlite3.Connection) -> float | None:
    """Taxa média de juros do crédito total em % a.m. (SGS 25433) — série
    irmã da 20714 (% a.a.) que alimenta o KPI, mesma taxa em outra
    unidade, direto da fonte (pedido do usuário, jul/2026: o card exibia
    só o valor a.a. sob o rótulo enganoso "Taxa média mensal")."""
    r = con.execute(
        "SELECT valor FROM sgs_valor WHERE codigo = 25433 "
        "ORDER BY competencia DESC LIMIT 1").fetchone()
    return r[0] if r else None


def taxa_duplicata(con: sqlite3.Connection) -> dict:
    """Taxa média de juros de "Desconto de duplicatas e recebíveis" (PJ,
    recursos livres) — a média PONDERADA pelo volume das concessões, como
    o BCB publica (pedido do usuário, jul/2026). Duas séries SGS nativas,
    mesma taxa em unidades diferentes: 20719 (% a.a.) e 25438 (% a.m.) —
    não converte nada, usa as duas direto da fonte."""
    def serie(codigo):
        return con.execute(
            "SELECT competencia, valor FROM sgs_valor WHERE codigo = ? "
            "ORDER BY competencia", (codigo,)).fetchall()
    aa, am = serie(20719), serie(25438)
    if not aa:
        return {}
    comp, valor_aa = aa[-1]
    valor_am = next((v for c, v in reversed(am) if c == comp), None)
    ref_12m = next((v for c, v in aa if c == comp - 100), None)
    return dict(
        competencia=comp, aa=valor_aa, am=valor_am,
        var_12m_pp=round(valor_aa - ref_12m, 2) if ref_12m is not None else None,
    )


def ranking(con: sqlite3.Connection, view: str, filtro_cliente: str | None,
           metrica_filtro: str | None = None) -> list[dict]:
    condicoes = ["modalidade IS NOT NULL",
                 "competencia = (SELECT MAX(competencia) FROM %s "
                 "WHERE periodicidade = 'TRIMESTRAL')" % view
                 if view != "v_credito_prazo_medio" else
                 "competencia = (SELECT MAX(competencia) FROM %s)" % view]
    parametros = []
    if filtro_cliente:
        condicoes.append("cliente = ?")
        parametros.append(filtro_cliente)
    if metrica_filtro:
        condicoes.append("metrica = ?")
        parametros.append(metrica_filtro)
    sql = (f"SELECT modalidade, ROUND(AVG(valor), 1) AS valor "
           f"FROM {view} WHERE {' AND '.join(condicoes)} "
           f"GROUP BY modalidade ORDER BY valor DESC LIMIT {TOP_N}")
    return [dict(r) for r in con.execute(sql, parametros).fetchall()]


def ranking_concessao(con: sqlite3.Connection, top_n: int = TOP_N) -> list[dict]:
    """Concessões de crédito (novas operações no mês) por modalidade —
    família mensal PF/PJ em geral, espelho de "Saldo da carteira de
    crédito" (mesma hierarquia pai-filho e corte paralelo Rotativo/Não
    rotativo — por isso reusa CARTEIRA_FILHOS_OU_PARALELOS, não a função
    genérica `ranking()`, que é pensada pra família trimestral micro/MEI)."""
    candidatos = pd.read_sql_query(
        "SELECT codigo, cliente, modalidade FROM credito_detalhado_serie "
        "WHERE metrica = 'CONCESSAO' AND frente = 'Concessões de crédito' "
        "AND modalidade IS NOT NULL", con)
    candidatos = candidatos[~candidatos["modalidade"].isin(CARTEIRA_FILHOS_OU_PARALELOS)]
    # "Série encadeada ao crédito referencial" bate um PADRAO genérico por
    # acidente (não é uma modalidade de produto de verdade) — mesma
    # ressalva documentada pra família de saldo, exclusão explícita aqui
    candidatos = candidatos[~candidatos["modalidade"].str.contains("Série encadeada", na=False)]

    ultima = con.execute(
        "SELECT MAX(competencia) FROM credito_detalhado_valor").fetchone()[0]
    atual = pd.read_sql_query(
        "SELECT codigo, valor FROM credito_detalhado_valor WHERE competencia = ?",
        con, params=(ultima,)).set_index("codigo")["valor"]
    candidatos = candidatos.assign(valor=candidatos["codigo"].map(atual)).dropna(subset=["valor"])
    top = candidatos.nlargest(top_n, "valor")
    return [dict(modalidade=r["modalidade"] + (f" ({r['cliente']})" if pd.notna(r["cliente"]) else ""),
                 valor=round(r["valor"], 1))
            for _, r in top.iterrows()]


def saldo_por_porte(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        "SELECT cliente, porte, ROUND(SUM(valor), 0) AS valor "
        "FROM v_credito_saldo_porte "
        "WHERE competencia = (SELECT MAX(competencia) FROM "
        "v_credito_saldo_porte WHERE periodicidade = 'TRIMESTRAL') "
        "GROUP BY cliente, porte ORDER BY valor DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def top_carteiras(con: sqlite3.Connection, top_n: int = TOP_N_CARTEIRAS) -> dict:
    """5 maiores carteiras de crédito por modalidade (saldo), histórico
    mensal completo, mais "Outros" (residual: total nacional menos as N
    maiores, mês a mês) e o total. Fonte: família "Saldo da carteira de
    crédito" (recursos livres/direcionados por produto) — cobre PF e PJ em
    geral, ao contrário da família "por modalidade de crédito" (usada nos
    rankings de juros/inadimplência), que só cobre micro/pequena empresa e
    MEI."""
    candidatos = pd.read_sql_query(
        "SELECT codigo, cliente, origem, modalidade FROM credito_detalhado_serie "
        "WHERE metrica = 'SALDO' AND frente = 'Saldo da carteira de crédito' "
        "AND modalidade IS NOT NULL", con)
    candidatos = candidatos[~candidatos["modalidade"].isin(CARTEIRA_FILHOS_OU_PARALELOS)]

    ultima = con.execute(
        "SELECT MAX(competencia) FROM credito_detalhado_valor").fetchone()[0]
    atual = pd.read_sql_query(
        "SELECT codigo, valor FROM credito_detalhado_valor WHERE competencia = ?",
        con, params=(ultima,)).set_index("codigo")["valor"]
    candidatos = candidatos.assign(valor_atual=candidatos["codigo"].map(atual)).dropna(subset=["valor_atual"])
    top = candidatos.nlargest(top_n, "valor_atual")

    total_df = pd.read_sql_query(
        "SELECT competencia, valor FROM credito_detalhado_valor WHERE codigo = 20539 ORDER BY competencia", con)
    periodos = total_df["competencia"].tolist()
    total_serie = [None if pd.isna(v) else v for v in total_df["valor"]]

    series = []
    soma_top = [0.0] * len(periodos)
    for _, r in top.iterrows():
        df = pd.read_sql_query(
            "SELECT competencia, valor FROM credito_detalhado_valor WHERE codigo = ? ORDER BY competencia",
            con, params=(int(r["codigo"]),)).set_index("competencia").reindex(periodos)
        valores = [None if pd.isna(v) else v for v in df["valor"]]
        for i, v in enumerate(valores):
            if v is not None:
                soma_top[i] += v
        nome = r["modalidade"] + (f" ({r['cliente']})" if r["cliente"] else "")
        series.append(dict(nome=nome, valores=valores))

    outros = [None if t is None else round(t - s, 1) for t, s in zip(total_serie, soma_top)]
    series.append(dict(nome="Outros", valores=outros))

    return dict(periodos=periodos, series=series, total=total_serie)


def montar_dados(con: sqlite3.Connection) -> dict:
    serie = serie_mensal(con)
    return dict(
        meta=dict(competencia_max=serie[-1]["competencia"] if serie else None),
        serie_mensal=serie,
        kpi=kpis(serie),
        ranking_juros=ranking(con, "v_credito_juros_modalidade", "PJ"),
        ranking_inadimplencia=ranking(con, "v_credito_inadimplencia_modalidade", "PJ"),
        ranking_prazo=ranking(con, "v_credito_prazo_medio", None, "PRAZO_MEDIO_CARTEIRA"),
        ranking_concessao=ranking_concessao(con),
        saldo_porte=saldo_por_porte(con),
        top_carteiras=top_carteiras(con),
        duplicata=taxa_duplicata(con),
        juros_am=taxa_media_am(con),
    )


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    dados = montar_dados(con)
    con.close()

    template = TEMPLATE.read_text(encoding="utf-8")
    html = template.replace("__DADOS_JSON__", json.dumps(dados, ensure_ascii=False))
    destino = REPORTS / "dashboard.html"
    destino.write_text(html, encoding="utf-8")
    print(f"  dashboard.html gerado ({len(dados['serie_mensal'])} meses, "
          f"{len(dados['ranking_juros'])} modalidades em cada ranking)")
    print(f"  {destino}")


if __name__ == "__main__":
    main()
