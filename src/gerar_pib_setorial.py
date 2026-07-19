# -*- coding: utf-8 -*-
"""
Gera o dashboard interativo de PIB trimestral por setor
(reports/pib_setorial.html) a partir do banco data/db/atividade.db —
série nacional trimestral (IBGE SIDRA t/1620 + t/5932, 1996-hoje):
índice de volume e as 4 taxas de variação, por setor de oferta (VAB) e
por componente de demanda.

Autocontido: os dados são embutidos como JSON no HTML (sem servidor, sem
chamada de rede) — abre direto no navegador, roda 100% local.

Uso:
    python gerar_pib_setorial.py
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

from caminhos import DB_ATIVIDADE as DB_PATH

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
TEMPLATE = Path(__file__).resolve().parent / "pib_setorial_template.html"

METRICAS_TAXA = ["taxa_tri_anterior_pct", "taxa_tri_ano_anterior_pct",
                 "taxa_acum_4tri_pct", "taxa_acum_ano_pct"]

# classificação de cada "setor" de pib_trimestral — AGREGADO (headline/
# referência), OFERTA (VAB por atividade) ou DEMANDA (ótica da despesa).
# "nivel" marca os 3 grandes grupos de oferta (Agropecuária/Indústria/
# Serviços) vs. seus subsetores, pra o front poder destacar os totais.
SETOR_GRUPO = {
    "PIB a preços de mercado": ("AGREGADO", "TOTAL"),
    "Valor adicionado a preços básicos": ("AGREGADO", "TOTAL"),
    "Impostos líquidos sobre produtos": ("AGREGADO", "TOTAL"),

    "Agropecuária - total": ("OFERTA", "GRANDE_GRUPO"),
    "Indústria - total": ("OFERTA", "GRANDE_GRUPO"),
    "Indústrias extrativas": ("OFERTA", "SUBSETOR"),
    "Indústrias de transformação": ("OFERTA", "SUBSETOR"),
    "Eletricidade e gás, água, esgoto, atividades de gestão de resíduos": ("OFERTA", "SUBSETOR"),
    "Construção": ("OFERTA", "SUBSETOR"),
    "Serviços - total": ("OFERTA", "GRANDE_GRUPO"),
    "Comércio": ("OFERTA", "SUBSETOR"),
    "Transporte, armazenagem e correio": ("OFERTA", "SUBSETOR"),
    "Informação e comunicação": ("OFERTA", "SUBSETOR"),
    "Atividades financeiras, de seguros e serviços relacionados": ("OFERTA", "SUBSETOR"),
    "Atividades imobiliárias": ("OFERTA", "SUBSETOR"),
    "Administração, saúde e educação públicas e seguridade social": ("OFERTA", "SUBSETOR"),
    "Outras atividades de serviços": ("OFERTA", "SUBSETOR"),

    "Despesa de consumo das famílias": ("DEMANDA", "TOTAL"),
    "Despesa de consumo da administração pública": ("DEMANDA", "TOTAL"),
    "Formação bruta de capital fixo": ("DEMANDA", "TOTAL"),
    "Exportação de bens e serviços": ("DEMANDA", "TOTAL"),
    "Importação de bens e serviços (-)": ("DEMANDA", "TOTAL"),
}


def limpar(registro: dict) -> dict:
    return {k: (None if pd.isna(v) else v) for k, v in registro.items()}


def montar_dados(con: sqlite3.Connection) -> dict:
    df = pd.read_sql_query(
        "SELECT trimestre, setor, indice_volume, taxa_tri_anterior_pct, "
        "taxa_tri_ano_anterior_pct, taxa_acum_4tri_pct, taxa_acum_ano_pct "
        "FROM pib_trimestral ORDER BY trimestre", con)

    periodos = sorted(df["trimestre"].unique().tolist())
    setores = []
    nao_classificados = []
    for nome, grp in df.groupby("setor"):
        grp = grp.set_index("trimestre").reindex(periodos)
        grupo, nivel = SETOR_GRUPO.get(nome, (None, None))
        if grupo is None:
            nao_classificados.append(nome)
            continue
        registro = dict(
            nome=nome, grupo=grupo, nivel=nivel,
            hist_indice=[None if pd.isna(v) else v for v in grp["indice_volume"]],
            hist_tri_anterior=[None if pd.isna(v) else v for v in grp["taxa_tri_anterior_pct"]],
            hist_tri_ano_anterior=[None if pd.isna(v) else v for v in grp["taxa_tri_ano_anterior_pct"]],
            hist_acum_4tri=[None if pd.isna(v) else v for v in grp["taxa_acum_4tri_pct"]],
            hist_acum_ano=[None if pd.isna(v) else v for v in grp["taxa_acum_ano_pct"]],
        )
        setores.append(registro)

    if nao_classificados:
        print(f"  [aviso] {len(nao_classificados)} setor(es) não classificado(s), "
              f"ficaram fora do relatório: {nao_classificados}")

    return dict(
        meta=dict(trimestre_max=periodos[-1], trimestre_min=periodos[0],
                  total_trimestres=len(periodos)),
        periodos=periodos,
        setores=setores,
    )


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    dados = montar_dados(con)
    con.close()

    template = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(dados, ensure_ascii=False)
    html = template.replace("__DADOS_JSON__", payload)
    destino = REPORTS / "pib_setorial.html"
    destino.write_text(html, encoding="utf-8")
    periodos = dados["periodos"]
    print(f"  pib_setorial.html gerado ({len(dados['setores'])} setores, "
          f"{len(periodos)} trimestres {periodos[0]}-{periodos[-1]}, "
          f"payload {len(payload)/1e6:.1f} MB)")
    print(f"  {destino}")


if __name__ == "__main__":
    main()
