# -*- coding: utf-8 -*-
"""
Gera os relatórios Excel na pasta reports/ a partir do banco
data/db/atividade.db.

Relatórios (v1 — habilitados por etapa de implementação):
  01_visao_brasil.xlsx       pulso mensal, PIB trimestral, crédito macro
  02_credito_setor.xlsx      crédito por setor de atividade (BACEN SGS)
  03_credito_municipal.xlsx  crédito por município (ESTBAN)
  04_pib_municipal.xlsx      PIB, VAB e penetração de crédito por município
  05_empresas.xlsx           empresas por CNAE e município (CEMPRE)
  06_emprego.xlsx            emprego formal (Novo CAGED)
  07_fichas_uf.xlsx          painel por UF
  08_ranking_municipios.xlsx ranking multi-métrica municipal
  09_credito_detalhado.xlsx  crédito por modalidade, porte de PJ, MEI, ICC, prazo médio, concessões
  10_ifdata_instituicoes.xlsx segmentação prudencial (S1-S5) e DRE por instituição financeira (IF.data)

Uso:
    python gerar_relatorios.py
"""
import sqlite3
from pathlib import Path

import pandas as pd

from caminhos import DB_ATIVIDADE as DB_PATH

REPORTS = Path(__file__).resolve().parents[1] / "reports"


def escrever_aba(writer: pd.ExcelWriter, df: pd.DataFrame, aba: str) -> None:
    df.to_excel(writer, sheet_name=aba, index=False, freeze_panes=(1, 0))
    ws = writer.sheets[aba]
    ws.auto_filter.ref = ws.dimensions
    for idx, col in enumerate(df.columns, start=1):
        amostra = df[col].head(200).fillna("").astype(str).str.len()
        largura = min(max(int(amostra.max() if len(amostra) else 10), len(col)) + 2, 55)
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = largura
        if pd.api.types.is_float_dtype(df[col]):
            for cel in ws.iter_cols(min_col=idx, max_col=idx, min_row=2):
                for c in cel:
                    c.number_format = "#,##0.00"


def gerar(con: sqlite3.Connection, arquivo: str, abas: dict[str, str]) -> None:
    caminho = REPORTS / arquivo
    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
        for aba, sql in abas.items():
            df = pd.read_sql_query(sql, con)
            escrever_aba(writer, df, aba)
            print(f"  {arquivo} :: {aba}: {len(df)} linhas")


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    gerar(con, "01_visao_brasil.xlsx", {
        "Pulso mensal":
            "SELECT * FROM v_pulso_nacional ORDER BY competencia DESC",
        "Credito macro":
            "SELECT * FROM v_credito_macro ORDER BY competencia DESC",
        "Atividade regional":
            "SELECT * FROM v_atividade_regional "
            "ORDER BY indicador, competencia DESC",
        "PIB trimestral":
            "SELECT * FROM v_pib_trimestral WHERE trimestre >= 201901 "
            "ORDER BY trimestre DESC, setor",
    })

    gerar(con, "02_credito_setor.xlsx", {
        "Ultima posicao":
            "SELECT nome, nivel, setor, subsetor, competencia, valor, "
            "var_12m_pct, participacao_pct FROM v_credito_setor "
            "WHERE competencia = (SELECT MAX(competencia) FROM v_credito_setor) "
            "ORDER BY nivel, valor DESC",
        "Serie por setor":
            "SELECT competencia, nome, setor, subsetor, valor, var_12m_pct "
            "FROM v_credito_setor WHERE nivel = 'SETOR' "
            "ORDER BY nome, competencia DESC",
        "Serie subsetores":
            "SELECT competencia, nome, setor, subsetor, valor, var_12m_pct "
            "FROM v_credito_setor WHERE nivel = 'SUBSETOR' "
            "ORDER BY setor, nome, competencia DESC",
        "Participacao":
            "SELECT competencia, nome, valor, participacao_pct "
            "FROM v_credito_setor WHERE nivel = 'SETOR' "
            "ORDER BY competencia DESC, valor DESC",
    })

    gerar(con, "03_credito_municipal.xlsx", {
        "Top 300 municipios":
            "SELECT rank_credito_brasil, nome, uf, regiao, competencia, "
            "credito_total_mil, credito_var_12m_pct, credito_per_capita, "
            "credito_rural_mil, credito_imobiliario_mil, provisao_pct_carteira, "
            "poupanca_mil, deposito_prazo_mil, instituicoes, agencias, populacao "
            "FROM v_credito_municipio_pc ORDER BY rank_credito_brasil LIMIT 300",
        "Per capita (pop 20 mil+)":
            "SELECT nome, uf, competencia, credito_per_capita, "
            "credito_total_mil, populacao, credito_var_12m_pct "
            "FROM v_credito_municipio_pc WHERE populacao >= 20000 "
            "ORDER BY credito_per_capita DESC LIMIT 300",
        "Serie por UF":
            "SELECT competencia, uf, SUM(credito_total_mil) AS credito_total_mil, "
            "SUM(credito_rural_mil) AS credito_rural_mil, "
            "SUM(credito_imobiliario_mil) AS credito_imobiliario_mil, "
            "SUM(poupanca_mil) AS poupanca_mil, "
            "SUM(deposito_prazo_mil) AS deposito_prazo_mil "
            "FROM v_credito_municipio GROUP BY competencia, uf "
            "ORDER BY uf, competencia DESC",
        "Penetracao credito-PIB":
            "SELECT rank_penetracao, nome, uf, regiao, credito_medio_12m_mil, "
            "ano_pib, pib_mil, credito_pib_pct FROM v_penetracao_credito "
            "WHERE pib_mil >= 100000 ORDER BY rank_penetracao LIMIT 300",
    })

    gerar(con, "04_pib_municipal.xlsx", {
        "Ranking PIB":
            "SELECT rank_pib_brasil, nome, uf, regiao, ano, pib_mil, populacao, "
            "pib_per_capita, participacao_uf_pct, pct_agro, pct_industria, "
            "pct_servicos, pct_adm_publica FROM v_pib_municipio "
            "WHERE ano = (SELECT MAX(ano) FROM pib_municipio) "
            "ORDER BY rank_pib_brasil LIMIT 500",
        "PIB per capita":
            "SELECT nome, uf, ano, pib_per_capita, pib_mil, populacao "
            "FROM v_pib_municipio "
            "WHERE ano = (SELECT MAX(ano) FROM pib_municipio) "
            "  AND populacao >= 20000 "
            "ORDER BY pib_per_capita DESC LIMIT 500",
        "Porte dos municipios":
            "SELECT faixa_pib, faixa_pop, COUNT(*) AS municipios, "
            "SUM(pib_mil) AS pib_mil, SUM(populacao) AS populacao "
            "FROM v_porte_municipio GROUP BY faixa_pib, faixa_pop "
            "ORDER BY faixa_pib, faixa_pop",
        "PIB por UF":
            "SELECT uf, ano, SUM(pib_mil) AS pib_mil, "
            "SUM(vab_agro_mil) AS vab_agro_mil, "
            "SUM(vab_industria_mil) AS vab_industria_mil, "
            "SUM(vab_servicos_mil) AS vab_servicos_mil, "
            "SUM(vab_adm_mil) AS vab_adm_mil "
            "FROM v_pib_municipio GROUP BY uf, ano ORDER BY uf, ano DESC",
    })

    gerar(con, "05_empresas.xlsx", {
        "Brasil por secao":
            "SELECT ano, secao, secao_nome, SUM(empresas) AS empresas, "
            "SUM(pessoal_total) AS pessoal_total, "
            "SUM(pessoal_assalariado) AS pessoal_assalariado, "
            "SUM(salarios_mil) AS salarios_mil "
            "FROM v_empresas_municipio GROUP BY ano, secao "
            "ORDER BY ano DESC, empresas DESC",
        "UF por secao":
            "SELECT ano, uf, secao, secao_nome, SUM(empresas) AS empresas, "
            "SUM(pessoal_total) AS pessoal_total "
            "FROM v_empresas_municipio "
            "WHERE ano = (SELECT MAX(ano) FROM cempre_municipio) "
            "GROUP BY uf, secao ORDER BY uf, empresas DESC",
        "Top municipios empresas":
            "SELECT t.ano, t.cod_ibge7, m.nome, m.uf, t.empresas_atuantes, "
            "t.unidades_locais, t.pessoal_total, t.pessoal_assalariado, "
            "t.salario_medio_reais "
            "FROM cempre_municipio_total t JOIN municipio m "
            "ON m.cod_ibge7 = t.cod_ibge7 "
            "WHERE t.ano = (SELECT MAX(ano) FROM cempre_municipio_total) "
            "ORDER BY t.empresas_atuantes DESC LIMIT 300",
        "Densidade empresarial":
            "SELECT ano, nome, uf, SUM(empresas) AS empresas, "
            "ROUND(SUM(empresas_por_mil_hab), 1) AS empresas_por_mil_hab "
            "FROM v_empresas_municipio "
            "WHERE ano = (SELECT MAX(ano) FROM cempre_municipio) "
            "GROUP BY cod_ibge7 HAVING SUM(empresas) >= 1000 "
            "ORDER BY empresas_por_mil_hab DESC LIMIT 300",
    })

    gerar(con, "06_emprego.xlsx", {
        "Saldo 12M por divisao":
            "SELECT divisao, divisao_nome, secao, secao_nome, competencia, "
            "admissoes, desligamentos, saldo, saldo_12m, salario_medio_adm "
            "FROM v_emprego_setor "
            "WHERE competencia = (SELECT MAX(competencia) FROM caged_mensal) "
            "ORDER BY saldo_12m DESC",
        "Serie por secao":
            "SELECT competencia, secao, secao_nome, SUM(admissoes) AS admissoes, "
            "SUM(desligamentos) AS desligamentos, SUM(saldo) AS saldo "
            "FROM v_emprego_setor GROUP BY competencia, secao "
            "ORDER BY secao, competencia DESC",
        "Saldo por UF":
            "SELECT competencia, uf, SUM(admissoes) AS admissoes, "
            "SUM(desligamentos) AS desligamentos, SUM(saldo) AS saldo "
            "FROM v_emprego_municipio GROUP BY competencia, uf "
            "ORDER BY uf, competencia DESC",
        "Top municipios saldo 12M":
            "SELECT nome, uf, regiao, competencia, saldo_12m, "
            "saldo_12m_por_mil_hab, admissoes, desligamentos "
            "FROM v_emprego_municipio "
            "WHERE competencia = (SELECT MAX(competencia) FROM caged_mensal) "
            "ORDER BY saldo_12m DESC LIMIT 300",
        "Serie nacional 1999+":
            "SELECT * FROM v_emprego_historico ORDER BY competencia DESC",
    })

    gerar(con, "07_fichas_uf.xlsx", {
        "Painel UF":
            "SELECT * FROM v_atividade_uf ORDER BY pib_mil DESC NULLS LAST",
        "Credito por UF (serie)":
            "SELECT competencia, uf, SUM(credito_total_mil) AS credito_total_mil "
            "FROM v_credito_municipio GROUP BY competencia, uf "
            "ORDER BY uf, competencia DESC",
        "Emprego por UF (serie)":
            "SELECT competencia, uf, SUM(saldo) AS saldo "
            "FROM v_emprego_municipio GROUP BY competencia, uf "
            "ORDER BY uf, competencia DESC",
    })

    gerar(con, "08_ranking_municipios.xlsx", {
        "Ranking multimetrica":
            "SELECT * FROM v_ranking_municipios "
            "WHERE populacao >= 20000 "
            "ORDER BY rank_pib_brasil NULLS LAST LIMIT 1000",
        "Por porte":
            "SELECT faixa_pib, faixa_pop, COUNT(*) AS municipios, "
            "SUM(pib_mil) AS pib_mil, SUM(populacao) AS populacao, "
            "ROUND(AVG(pct_industria), 1) AS pct_industria_medio, "
            "ROUND(AVG(pct_agro), 1) AS pct_agro_medio "
            "FROM v_porte_municipio GROUP BY faixa_pib, faixa_pop "
            "ORDER BY faixa_pib, faixa_pop",
        "Setor consolidado":
            "SELECT * FROM v_setor_consolidado ORDER BY vab_mil DESC NULLS LAST",
    })

    gerar(con, "09_credito_detalhado.xlsx", {
        "Juros por modalidade":
            "SELECT cliente, porte, origem, modalidade, competencia, valor, "
            "var_ano_pct FROM v_credito_juros_modalidade "
            "WHERE competencia = (SELECT MAX(competencia) FROM "
            "v_credito_juros_modalidade) "
            "ORDER BY cliente, porte, origem, valor DESC",
        "Inadimplencia por modalidade":
            # a maioria das séries é trimestral; o par agregado por porte
            # (MPMe/Grande) é mensal e tem competência mais recente — pega
            # cada bloco na sua própria última competência, não uma única
            # data global (que deixaria a massa trimestral de fora)
            "SELECT cliente, porte, origem, modalidade, competencia, valor, "
            "var_ano_pct FROM v_credito_inadimplencia_modalidade "
            "WHERE competencia = (SELECT MAX(competencia) FROM "
            "v_credito_inadimplencia_modalidade WHERE periodicidade = 'TRIMESTRAL') "
            "UNION ALL "
            "SELECT cliente, porte, origem, modalidade, competencia, valor, "
            "var_ano_pct FROM v_credito_inadimplencia_modalidade "
            "WHERE periodicidade = 'MENSAL' AND competencia = (SELECT MAX(competencia) "
            "FROM v_credito_inadimplencia_modalidade WHERE periodicidade = 'MENSAL') "
            "ORDER BY cliente, porte, origem, valor DESC",
        "Saldo por porte e MEI":
            "SELECT cliente, porte, origem, modalidade, competencia, valor, "
            "participacao_pct, var_ano_pct FROM v_credito_saldo_porte "
            "WHERE competencia = (SELECT MAX(competencia) FROM "
            "v_credito_saldo_porte WHERE periodicidade = 'TRIMESTRAL') "
            "UNION ALL "
            "SELECT cliente, porte, origem, modalidade, competencia, valor, "
            "participacao_pct, var_ano_pct FROM v_credito_saldo_porte "
            "WHERE periodicidade = 'MENSAL' AND competencia = (SELECT MAX(competencia) "
            "FROM v_credito_saldo_porte WHERE periodicidade = 'MENSAL') "
            "ORDER BY cliente, porte, valor DESC",
        "ICC e spread":
            "SELECT metrica, cliente, origem, modalidade, competencia, valor, "
            "var_ano_pct FROM v_credito_icc "
            "WHERE competencia = (SELECT MAX(competencia) FROM v_credito_icc) "
            "ORDER BY metrica, cliente, origem, valor DESC",
        "Prazo medio":
            "SELECT metrica, cliente, origem, modalidade, competencia, valor, "
            "var_ano_pct FROM v_credito_prazo_medio "
            "WHERE competencia = (SELECT MAX(competencia) FROM "
            "v_credito_prazo_medio) "
            "ORDER BY metrica, cliente, origem, valor DESC",
        "Concessoes por modalidade":
            "SELECT cliente, origem, modalidade, competencia, valor, "
            "var_ano_pct FROM v_credito_concessao_modalidade "
            "WHERE competencia = (SELECT MAX(competencia) FROM "
            "v_credito_concessao_modalidade) "
            "ORDER BY cliente, origem, valor DESC",
        "Nao parseados (auditoria)":
            "SELECT codigo, titulo, frente FROM credito_detalhado_serie "
            "WHERE parse_status != 'OK' ORDER BY frente, codigo",
    })

    gerar(con, "10_ifdata_instituicoes.xlsx", {
        "Segmentacao (S1-S5)":
            "SELECT i.segmento AS segmento, COUNT(*) AS instituicoes, "
            "ROUND(SUM(d.ativo_total) / 1e6, 1) AS ativo_total_bi "
            "FROM ifdata_instituicao i "
            "LEFT JOIN v_ifdata_dre d ON d.codinst = i.codinst AND d.anomes = i.anomes "
            "WHERE i.anomes = (SELECT MAX(anomes) FROM ifdata_instituicao) "
            "AND i.situacao = 'A' "
            "GROUP BY i.segmento ORDER BY ativo_total_bi DESC",
        "Ranking por lucro liquido":
            "SELECT nome, segmento, uf, situacao, ativo_total, "
            "receita_juros_credito, receita_credito_total, "
            "perda_esperada_credito, perda_esperada_total, lucro_liquido "
            "FROM v_ifdata_dre "
            "WHERE anomes = (SELECT MAX(anomes) FROM v_ifdata_dre "
            "WHERE lucro_liquido IS NOT NULL) "
            "ORDER BY lucro_liquido DESC",
        "Ranking por receita de credito":
            "SELECT nome, segmento, uf, ativo_total, receita_juros_credito, "
            "receita_credito_total, lucro_liquido FROM v_ifdata_dre "
            "WHERE anomes = (SELECT MAX(anomes) FROM v_ifdata_dre "
            "WHERE receita_credito_total IS NOT NULL) "
            "ORDER BY receita_credito_total DESC",
        "Cadastro (ultimo trimestre)":
            "SELECT codinst, nome, segmento, uf, municipio, situacao, "
            "tipo_consolidacao FROM ifdata_instituicao "
            "WHERE anomes = (SELECT MAX(anomes) FROM ifdata_instituicao) "
            "ORDER BY segmento, nome",
        # aging/CNAE/porte são publicados no nível de CONGLOMERADO
        # PRUDENCIAL (codinst diferente do DRE, que é por instituição
        # individual) — "top 30" aqui é rankeado pela própria carteira PJ
        # total (relatório 14), não pelo ativo total do DRE, já que os dois
        # níveis de consolidação não compartilham o mesmo codinst
        "Carteira PJ por modalidade":
            "SELECT nome, uf, modalidade, valor FROM v_ifdata_aging "
            "WHERE cliente = 'PJ' AND bucket = 'Total' "
            "AND anomes = (SELECT MAX(anomes) FROM v_ifdata_aging) "
            "AND codinst IN (SELECT codinst FROM v_ifdata_porte "
            "WHERE anomes = (SELECT MAX(anomes) FROM v_ifdata_porte) "
            "AND porte = 'Total da Carteira de Pessoa Jurídica' "
            "ORDER BY valor DESC LIMIT 30) "
            "ORDER BY nome, valor DESC",
        "Vencidos PJ por modalidade":
            "SELECT nome, uf, modalidade, valor FROM v_ifdata_aging "
            "WHERE cliente = 'PJ' AND bucket = 'Vencido a Partir de 15 Dias' "
            "AND anomes = (SELECT MAX(anomes) FROM v_ifdata_aging) "
            "AND codinst IN (SELECT codinst FROM v_ifdata_porte "
            "WHERE anomes = (SELECT MAX(anomes) FROM v_ifdata_porte) "
            "AND porte = 'Total da Carteira de Pessoa Jurídica' "
            "ORDER BY valor DESC LIMIT 30) "
            "ORDER BY nome, valor DESC",
        "Carteira PJ por CNAE":
            "SELECT nome, uf, atividade, valor FROM v_ifdata_cnae "
            "WHERE bucket = 'Total' "
            "AND anomes = (SELECT MAX(anomes) FROM v_ifdata_cnae) "
            "AND codinst IN (SELECT codinst FROM v_ifdata_porte "
            "WHERE anomes = (SELECT MAX(anomes) FROM v_ifdata_porte) "
            "AND porte = 'Total da Carteira de Pessoa Jurídica' "
            "ORDER BY valor DESC LIMIT 30) "
            "ORDER BY nome, valor DESC",
        "Carteira PJ por porte":
            "SELECT nome, uf, porte, valor FROM v_ifdata_porte "
            "WHERE anomes = (SELECT MAX(anomes) FROM v_ifdata_porte) "
            "AND codinst IN (SELECT codinst FROM v_ifdata_porte "
            "WHERE anomes = (SELECT MAX(anomes) FROM v_ifdata_porte) "
            "AND porte = 'Total da Carteira de Pessoa Jurídica' "
            "ORDER BY valor DESC LIMIT 30) "
            "ORDER BY nome, valor DESC",
        "Taxa de juros por instituicao":
            "SELECT instituicao, segmento, modalidade, taxa_mes_pct, taxa_ano_pct "
            "FROM taxa_juros_instituicao "
            "WHERE inicio_periodo = (SELECT MAX(inicio_periodo) FROM taxa_juros_instituicao "
            "WHERE modalidade NOT LIKE '%imobili%') "
            "OR (modalidade LIKE '%imobili%' AND inicio_periodo = (SELECT MAX(inicio_periodo) "
            "FROM taxa_juros_instituicao WHERE modalidade LIKE '%imobili%')) "
            "ORDER BY segmento, modalidade, taxa_ano_pct ASC",
    })

    con.close()
    print("\nRelatórios gerados com sucesso.")


if __name__ == "__main__":
    main()
