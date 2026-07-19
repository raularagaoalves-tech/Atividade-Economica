# -*- coding: utf-8 -*-
"""
Gera o dashboard interativo de Recuperação Judicial, Extrajudicial e
Falência (reports/recuperacao_judicial.html) a partir do banco
data/db/atividade.db — série nacional mensal (CNJ DataJud, desde 2020):
evolução histórica, variação M/M e 12M, e visão por UF.

Autocontido: os dados são embutidos como JSON no HTML (sem servidor, sem
chamada de rede) — abre direto no navegador, roda 100% local.

Uso:
    python gerar_recuperacao_judicial.py
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

from caminhos import DB_ATIVIDADE as DB_PATH

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
TEMPLATE = Path(__file__).resolve().parent / "recuperacao_judicial_template.html"

CATEGORIAS_CAMPO = [
    ("rj", "processos_rj", "Recuperação Judicial"),
    ("extrajudicial", "processos_extrajudicial", "Recuperação Extrajudicial"),
    ("falencia", "processos_falencia", "Falência"),
]


def limpar(registro: dict) -> dict:
    return {k: (None if pd.isna(v) else v) for k, v in registro.items()}


def series_derivadas(hist_valor: list) -> dict:
    """A partir da contagem mensal bruta, deriva variação M/M (absoluta e
    %) e variação 12M (%) — mesma lógica usada no drawer de UF do mapa
    regional, aqui pré-computada no servidor pra alimentar o gráfico
    histórico e a tabela sem repetir a conta no cliente."""
    n = len(hist_valor)
    mm_delta, mm_pct, a12_pct = [None] * n, [None] * n, [None] * n
    for i, v in enumerate(hist_valor):
        if v is None:
            continue
        if i >= 1 and hist_valor[i - 1] is not None:
            anterior = hist_valor[i - 1]
            mm_delta[i] = v - anterior
            mm_pct[i] = round((v / anterior - 1) * 100, 2) if anterior else None
        if i >= 12 and hist_valor[i - 12] not in (None, 0):
            a12_pct[i] = round((v / hist_valor[i - 12] - 1) * 100, 2)
    return dict(hist_mm_delta=mm_delta, hist_mm_pct=mm_pct, hist_a12_pct=a12_pct)


def montar_dados(con: sqlite3.Connection) -> dict:
    periodos = [r[0] for r in con.execute(
        "SELECT DISTINCT competencia FROM datajud_rj_falencia_mensal ORDER BY competencia")]
    if not periodos:
        return dict(meta=dict(competencia_min=None, competencia_max=None, total_competencias=0),
                     periodos=[], categorias=[], ufs=[])

    nacional_df = pd.read_sql_query(
        "SELECT competencia, processos_rj, processos_extrajudicial, processos_falencia "
        "FROM v_recuperacao_falencia_nacional", con,
    ).set_index("competencia").reindex(periodos)

    categorias = []
    for key, campo, label in CATEGORIAS_CAMPO:
        hist_valor = [None if pd.isna(v) else int(v) for v in nacional_df[campo]]
        registro = dict(key=key, label=label, hist_valor=hist_valor)
        registro.update(series_derivadas(hist_valor))
        categorias.append(registro)

    regiao_por_uf = dict(con.execute("SELECT DISTINCT uf, regiao FROM municipio").fetchall())

    uf_df = pd.read_sql_query(
        "SELECT uf, competencia, processos_rj, processos_extrajudicial, processos_falencia "
        "FROM v_recuperacao_falencia_uf", con,
    )
    ufs = []
    for uf, grp in uf_df.groupby("uf"):
        grp = grp.set_index("competencia").reindex(periodos)
        ufs.append(dict(
            uf=uf, regiao=regiao_por_uf.get(uf),
            hist_rj=[None if pd.isna(v) else int(v) for v in grp["processos_rj"]],
            hist_extrajudicial=[None if pd.isna(v) else int(v) for v in grp["processos_extrajudicial"]],
            hist_falencia=[None if pd.isna(v) else int(v) for v in grp["processos_falencia"]],
        ))

    return dict(
        meta=dict(competencia_min=periodos[0], competencia_max=periodos[-1],
                  total_competencias=len(periodos)),
        periodos=periodos,
        categorias=categorias,
        ufs=ufs,
    )


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    dados = montar_dados(con)
    con.close()

    template = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(dados, ensure_ascii=False)
    html = template.replace("__DADOS_JSON__", payload)
    destino = REPORTS / "recuperacao_judicial.html"
    destino.write_text(html, encoding="utf-8")
    per = dados["periodos"]
    faixa = f"{per[0]}-{per[-1]}" if per else "sem dados"
    print(f"  recuperacao_judicial.html gerado ({len(dados['ufs'])} UFs, "
          f"{len(per)} competências {faixa}, payload {len(payload)/1e6:.1f} MB)")
    print(f"  {destino}")


if __name__ == "__main__":
    main()
