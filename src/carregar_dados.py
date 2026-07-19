# -*- coding: utf-8 -*-
"""
Carrega os dados brutos (já baixados por baixar_dados.py) no banco SQLite
(data/db/atividade.db). O banco é recarregado do zero a cada execução —
sempre consistente com o que está em data/raw.

Tabelas (v1):
  - municipio            : dimensão território (código IBGE 7 díg., UF, coordenadas)
  - municipio_populacao  : população estimada por município/ano (SIDRA t/6579)
  - cnae_divisao         : divisões CNAE 2.0 (semente manual)
  - estban_verbete       : verbetes ESTBAN de interesse (semente manual)
  - (próximas etapas: sgs_valor, pib_municipio, estban_municipio,
     cempre_municipio, caged_mensal)

Ao final executa src/schema.sql (views analíticas + materialização).

Uso:
    python carregar_dados.py
"""
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd

from caminhos import DB_ATIVIDADE as DB_PATH, DIR_MANUAL as MANUAL, DIR_RAW as RAW

SCHEMA = Path(__file__).resolve().parent / "schema.sql"

REGIOES = {"1": "Norte", "2": "Nordeste", "3": "Sudeste",
           "4": "Sul", "5": "Centro-Oeste"}


def so_digitos(valor) -> str:
    if not isinstance(valor, str):
        return ""
    return re.sub(r"\D", "", valor)


def sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


def nome_norm(texto: str) -> str:
    return sem_acento(str(texto)).upper().strip()


def ler_semente(nome: str) -> pd.DataFrame:
    """Lê CSV de data/manual (utf-8, ';', linhas iniciadas em # ignoradas)."""
    return pd.read_csv(MANUAL / nome, sep=";", encoding="utf-8",
                       dtype=str, comment="#")


# ---------------------------------------------------------------------
# SIDRA — utilitários
# ---------------------------------------------------------------------
def ler_sidra(path: Path) -> pd.DataFrame:
    """Lê JSON da API SIDRA: 1ª linha é o cabeçalho (código -> nome)."""
    with open(path, encoding="utf-8") as fh:
        bruto = json.load(fh)
    if not bruto or len(bruto) < 2:
        return pd.DataFrame()
    df = pd.DataFrame(bruto[1:])
    df.attrs["cabecalho"] = bruto[0]
    return df


def col_sidra(df: pd.DataFrame, contendo: str) -> str:
    """Acha a coluna cujo rótulo no cabeçalho contém o texto dado."""
    for cod, rotulo in df.attrs["cabecalho"].items():
        if contendo.lower() in str(rotulo).lower():
            return cod
    raise KeyError(f"coluna SIDRA contendo '{contendo}' não encontrada")


# ---------------------------------------------------------------------
# Cargas
# ---------------------------------------------------------------------
def carregar_municipios(con: sqlite3.Connection) -> None:
    print("== Municípios (IBGE localidades + coordenadas) ==")
    with open(RAW / "ibge" / "municipios.json", encoding="utf-8") as fh:
        mun = pd.DataFrame(json.load(fh))
    mun = mun.rename(columns={
        "municipio-id": "cod_ibge7", "municipio-nome": "nome",
        "UF-id": "cod_uf", "UF-sigla": "uf", "regiao-id": "cod_regiao"})
    mun["cod_ibge7"] = mun["cod_ibge7"].astype(str)
    mun["cod_ibge6"] = mun["cod_ibge7"].str[:6]
    mun["nome_norm"] = mun["nome"].map(nome_norm)
    mun["regiao"] = mun["cod_regiao"].astype(str).map(REGIOES)
    mun = mun[["cod_ibge7", "cod_ibge6", "nome", "nome_norm",
               "uf", "cod_uf", "regiao"]]

    coord = pd.read_csv(RAW / "ibge" / "municipios_coord.csv",
                        encoding="utf-8", dtype={"codigo_ibge": str})
    coord = coord.rename(columns={"codigo_ibge": "cod_ibge7",
                                  "latitude": "lat", "longitude": "lng"})
    mun = mun.merge(coord[["cod_ibge7", "lat", "lng", "capital"]],
                    on="cod_ibge7", how="left")

    mun.to_sql("municipio", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_mun ON municipio (cod_ibge7)")
    con.execute("CREATE INDEX ix_mun_nome ON municipio (nome_norm, uf)")
    con.execute("CREATE INDEX ix_mun6 ON municipio (cod_ibge6)")
    sem_coord = int(mun["lat"].isna().sum())
    print(f"  municípios: {len(mun)} (sem coordenada: {sem_coord})")


def _ler_ipea(arq: Path) -> pd.DataFrame:
    """Lê JSON do IPEADATA (ODATA4): TERCODIGO, NIVNOME, VALDATA, VALVALOR."""
    with open(arq, encoding="utf-8") as fh:
        bruto = json.load(fh)
    df = pd.DataFrame(bruto.get("value", []))
    if df.empty:
        return df
    df["ano"] = df["VALDATA"].str[:4].astype(int)
    df["competencia"] = (df["VALDATA"].str[:4] + df["VALDATA"].str[5:7]).astype(int)
    df["valor"] = pd.to_numeric(df["VALVALOR"], errors="coerce")
    df["TERCODIGO"] = df["TERCODIGO"].fillna("").astype(str).str.strip()
    return df.dropna(subset=["valor"])


def carregar_ipea(con: sqlite3.Connection) -> None:
    print("== IPEADATA: IDHM, Gini, população histórica, CAGED nacional ==")
    pasta = RAW / "ipea"
    if not pasta.exists() or not any(pasta.glob("*.json")):
        print("  [aviso] nenhum arquivo IPEA em data/raw/ipea")
        return

    # --- população municipal histórica (estimativas desde 1992) ---
    arq = pasta / "ESTIMA_PO.json"
    if arq.exists():
        df = _ler_ipea(arq)
        df = df[df["NIVNOME"] == "Municípios"]
        pop = pd.DataFrame({"cod_ibge7": df["TERCODIGO"], "ano": df["ano"],
                            "populacao": df["valor"]})
        pop.to_sql("ipea_populacao_municipio", con, if_exists="replace",
                   index=False)
        con.execute("CREATE UNIQUE INDEX ix_ipea_pop ON "
                    "ipea_populacao_municipio (cod_ibge7, ano)")
        print(f"  população histórica: {len(pop)} registros "
              f"({pop['ano'].min()}–{pop['ano'].max()})")

    # --- CAGED nacional: antigo (1999-2019) + novo (2020+) ---
    HIST = {"CAGED12_ADMIS": ("admissoes", "CAGED_ANTIGO"),
            "CAGED12_DESLIG": ("desligamentos", "CAGED_ANTIGO"),
            "CAGED12_SALDO12": ("saldo", "CAGED_ANTIGO"),
            "CAGED12_ADMISN12": ("admissoes", "NOVO_CAGED"),
            "CAGED12_DESLIGN12": ("desligamentos", "NOVO_CAGED"),
            "CAGED12_SALDON12": ("saldo", "NOVO_CAGED")}
    frames = []
    for codigo, (coluna, fonte) in HIST.items():
        arq = pasta / f"{codigo}.json"
        if not arq.exists():
            continue
        df = _ler_ipea(arq)
        frames.append(pd.DataFrame({"competencia": df["competencia"],
                                    "fonte": fonte, "coluna": coluna,
                                    "valor": df["valor"]}))
    if frames:
        emp = pd.concat(frames, ignore_index=True)
        largo = emp.pivot_table(index=["competencia", "fonte"],
                                columns="coluna", values="valor",
                                aggfunc="first").reset_index()
        largo.to_sql("emprego_nacional_hist", con, if_exists="replace",
                     index=False)
        con.execute("CREATE UNIQUE INDEX ix_emp_hist ON "
                    "emprego_nacional_hist (fonte, competencia)")
        comp = con.execute("SELECT MIN(competencia), MAX(competencia) "
                           "FROM emprego_nacional_hist").fetchone()
        print(f"  CAGED nacional: {len(largo)} meses ({comp[0]}–{comp[1]})")


def carregar_populacao(con: sqlite3.Connection) -> None:
    print("== População municipal estimada (SIDRA t/6579) ==")
    frames = []
    for arq in sorted((RAW / "sidra").glob("pop_*.json")):
        df = ler_sidra(arq)
        if df.empty:
            continue
        try:
            c_mun = col_sidra(df, "Município (Código)")
            c_ano = col_sidra(df, "Ano (Código)")
        except KeyError as exc:
            print(f"  [ERRO] {arq.name}: {exc} — layout da tabela SIDRA "
                  f"mudou? arquivo pulado")
            continue
        frames.append(pd.DataFrame({
            "cod_ibge7": df[c_mun].astype(str),
            "ano": pd.to_numeric(df[c_ano], errors="coerce"),
            "populacao": pd.to_numeric(df["V"], errors="coerce"),
        }))
    if not frames:
        print("  [aviso] nenhum arquivo de população em data/raw/sidra")
        return
    pop = pd.concat(frames, ignore_index=True).dropna(subset=["populacao"])
    pop["ano"] = pop["ano"].astype(int)
    pop.to_sql("municipio_populacao", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_pop ON municipio_populacao (cod_ibge7, ano)")
    anos = con.execute("SELECT MIN(ano), MAX(ano) FROM municipio_populacao").fetchone()
    print(f"  registros: {len(pop)} (anos {anos[0]}–{anos[1]})")

    # série de referência SEM buracos (a estimativa do IBGE não cobre anos de
    # censo, ex. 2022): completa com o histórico do IPEADATA (desde 1992,
    # quando carregado) e preenche cada ano faltante com o mais próximo —
    # base dos indicadores per capita. SIDRA tem precedência no overlap.
    base = pop[["cod_ibge7", "ano", "populacao"]]
    try:
        ipea = pd.read_sql_query(
            "SELECT cod_ibge7, ano, populacao FROM ipea_populacao_municipio",
            con)
        base = (pd.concat([base.assign(prio=0), ipea.assign(prio=1)])
                .sort_values("prio")
                .drop_duplicates(["cod_ibge7", "ano"], keep="first"))
    except Exception:
        pass
    grade = base.pivot_table(index="cod_ibge7", columns="ano", values="populacao")
    grade = grade.reindex(columns=range(int(base["ano"].min()),
                                        int(base["ano"].max()) + 1))
    grade = grade.ffill(axis=1).bfill(axis=1)
    ref = grade.stack().rename("populacao").reset_index()
    ref["ano"] = ref["ano"].astype(int)
    ref.to_sql("municipio_populacao_ref", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_pop_ref ON municipio_populacao_ref (cod_ibge7, ano)")
    print(f"  série de referência (anos preenchidos): {len(ref)}")


def carregar_pib_municipal(con: sqlite3.Connection) -> None:
    print("== PIB dos municípios (SIDRA t/5938) ==")
    # variáveis (Mil Reais): 37 PIB, 543 impostos, 498 VAB total, 513 agro,
    # 517 indústria, 6575 serviços exc. adm pública, 525 adm pública
    NOMES = {37: "pib_mil", 543: "impostos_mil", 498: "vab_total_mil",
             513: "vab_agro_mil", 517: "vab_industria_mil",
             6575: "vab_servicos_mil", 525: "vab_adm_mil"}
    frames = []
    for arq in sorted((RAW / "sidra").glob("pib_mun_*.json")):
        df = ler_sidra(arq)
        if df.empty:
            continue
        try:
            c_mun = col_sidra(df, "Município (Código)")
            c_var = col_sidra(df, "Variável (Código)")
            c_ano = col_sidra(df, "Ano (Código)")
        except KeyError as exc:
            print(f"  [ERRO] {arq.name}: {exc} — layout da tabela SIDRA "
                  f"mudou? arquivo pulado")
            continue
        frames.append(pd.DataFrame({
            "cod_ibge7": df[c_mun].astype(str),
            "variavel": pd.to_numeric(df[c_var], errors="coerce"),
            "ano": pd.to_numeric(df[c_ano], errors="coerce"),
            "valor": pd.to_numeric(df["V"], errors="coerce"),
        }))
    if not frames:
        print("  [aviso] nenhum arquivo pib_mun em data/raw/sidra")
        return
    pib = pd.concat(frames, ignore_index=True).dropna(subset=["ano", "variavel"])
    pib = pib[pib["ano"] >= 2010]
    pib["coluna"] = pib["variavel"].astype(int).map(NOMES)
    largo = pib.pivot_table(index=["cod_ibge7", "ano"], columns="coluna",
                            values="valor", aggfunc="first").reset_index()
    largo["ano"] = largo["ano"].astype(int)
    largo.to_sql("pib_municipio", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_pib_mun ON pib_municipio (cod_ibge7, ano)")
    anos = con.execute("SELECT MIN(ano), MAX(ano) FROM pib_municipio").fetchone()
    print(f"  registros: {len(largo)} (anos {anos[0]}–{anos[1]})")


def carregar_pib_trimestral(con: sqlite3.Connection) -> None:
    print("== PIB trimestral por setor (SIDRA t/1620 e t/5932) ==")
    # variáveis: 583 índice de volume; 6561 taxa vs mesmo tri ano anterior;
    # 6562 acumulada 4 tri; 6563 acumulada no ano; 6564 tri vs tri anterior
    NOMES = {583: "indice_volume", 6561: "taxa_tri_ano_anterior_pct",
             6562: "taxa_acum_4tri_pct", 6563: "taxa_acum_ano_pct",
             6564: "taxa_tri_anterior_pct"}
    frames = []
    for arq in ("pib_tri_1620.json", "pib_tri_5932.json"):
        path = RAW / "sidra" / arq
        if not path.exists():
            continue
        df = ler_sidra(path)
        if df.empty:
            continue
        try:
            c_var = col_sidra(df, "Variável (Código)")
            c_tri = col_sidra(df, "Trimestre (Código)")
            c_set = col_sidra(df, "Setores e subsetores")
        except KeyError as exc:
            print(f"  [ERRO] {arq}: {exc} — layout da tabela SIDRA "
                  f"mudou? arquivo pulado")
            continue
        frames.append(pd.DataFrame({
            "trimestre": pd.to_numeric(df[c_tri], errors="coerce"),
            "setor": df[c_set.replace("C", "N") if c_set.endswith("C") else c_set],
            "variavel": pd.to_numeric(df[c_var], errors="coerce"),
            "valor": pd.to_numeric(df["V"], errors="coerce"),
        }))
    if not frames:
        print("  [aviso] nenhum arquivo pib_tri em data/raw/sidra")
        return
    tri = pd.concat(frames, ignore_index=True).dropna(subset=["trimestre", "variavel"])
    tri["coluna"] = tri["variavel"].astype(int).map(NOMES)
    largo = tri.pivot_table(index=["trimestre", "setor"], columns="coluna",
                            values="valor", aggfunc="first").reset_index()
    largo["trimestre"] = largo["trimestre"].astype(int)
    largo.to_sql("pib_trimestral", con, if_exists="replace", index=False)
    con.execute("CREATE INDEX ix_pib_tri ON pib_trimestral (setor, trimestre)")
    tris = con.execute("SELECT MIN(trimestre), MAX(trimestre) FROM pib_trimestral").fetchone()
    print(f"  registros: {len(largo)} ({tris[0]}–{tris[1]})")


def _agregar_estban(arq_zip: Path, cache_saldo: Path, cache_presenca: Path) -> None:
    """Agrega um ZIP mensal do ESTBAN por (competência, município, verbete) e
    por (competência, município) para presença bancária; grava em cache.
    TODOS os verbetes do arquivo entram no cache (não só os da semente atual)
    — assim, adicionar um verbete novo em estban_verbetes.csv não exige
    reprocessar o ZIP, só ajustar o filtro na carga."""
    df = pd.read_csv(arq_zip, sep=";", encoding="latin-1", skiprows=2,
                     dtype=str, low_memory=False)
    colunas_verbete = {}
    for col in df.columns:
        m = re.search(r"VERBETE_(\d{3})", col)
        if m:
            colunas_verbete[col] = int(m.group(1))

    df["competencia"] = pd.to_numeric(df["#DATA_BASE"], errors="coerce")
    df["cod_ibge7"] = df["CODMUN_IBGE"].fillna("").str.strip()
    for col in colunas_verbete:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    longo = df.melt(id_vars=["competencia", "cod_ibge7"],
                    value_vars=list(colunas_verbete),
                    var_name="coluna", value_name="valor")
    longo["verbete"] = longo["coluna"].map(colunas_verbete)
    agg = (longo.dropna(subset=["valor"])
           .groupby(["competencia", "cod_ibge7", "verbete"],
                    as_index=False)["valor"].sum())
    agg["saldo_mil"] = (agg["valor"] / 1000).round(1)
    agg[["competencia", "cod_ibge7", "verbete", "saldo_mil"]].to_csv(
        cache_saldo, sep=";", index=False)

    df["AGEN_PROCESSADAS"] = pd.to_numeric(df["AGEN_PROCESSADAS"],
                                           errors="coerce")
    pres = df.groupby(["competencia", "cod_ibge7"]).agg(
        instituicoes=("CNPJ", "nunique"),
        agencias=("AGEN_PROCESSADAS", "sum")).reset_index()
    pres.to_csv(cache_presenca, sep=";", index=False)


def carregar_estban(con: sqlite3.Connection) -> None:
    """Balancete bancário por município (doc 4500). Agregados por mês ficam
    em cache (data/raw/estban/agregado/) — só ZIPs novos são reprocessados;
    mantém só os verbetes da semente ao carregar no banco. Valores do arquivo
    em R$ (unidades) — convertidos para R$ mil na agregação."""
    print("== BACEN ESTBAN: crédito e captação por município ==")
    pasta = RAW / "estban"
    cache_dir = pasta / "agregado"
    cache_dir.mkdir(parents=True, exist_ok=True)
    arquivos = sorted(pasta.glob("*_ESTBAN.csv.zip"))
    if not arquivos:
        print("  [aviso] nenhum arquivo ESTBAN em data/raw/estban")
        return
    verbetes = {int(v) for (v,) in
                con.execute("SELECT verbete FROM estban_verbete")}
    validos = {str(c) for (c,) in
               con.execute("SELECT cod_ibge7 FROM municipio")}

    saldos, presencas = [], []
    for arq in arquivos:
        mes = arq.stem.split("_")[0]
        cache_saldo = cache_dir / f"{mes}_saldo.csv"
        cache_presenca = cache_dir / f"{mes}_presenca.csv"
        if not cache_saldo.exists() or not cache_presenca.exists():
            print(f"  [agregando] {arq.name}")
            try:
                _agregar_estban(arq, cache_saldo, cache_presenca)
            except Exception as exc:
                print(f"  [ERRO      ] {arq.name}: {exc}")
                continue
        saldos.append(pd.read_csv(cache_saldo, sep=";",
                                  dtype={"cod_ibge7": str}))
        presencas.append(pd.read_csv(cache_presenca, sep=";",
                                     dtype={"cod_ibge7": str}))
    if not saldos:
        return

    est = pd.concat(saldos, ignore_index=True)
    ok = est["cod_ibge7"].isin(validos)
    nao_casados = int((~ok).sum())
    est = est[ok & est["verbete"].isin(verbetes)].copy()
    est["competencia"] = est["competencia"].astype(int)
    est.to_sql("estban_municipio", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_estban ON estban_municipio "
                "(cod_ibge7, verbete, competencia)")
    con.execute("CREATE INDEX ix_estban_comp ON estban_municipio "
                "(competencia, verbete)")

    pres = pd.concat(presencas, ignore_index=True)
    pres = pres[pres["cod_ibge7"].isin(validos)].copy()
    pres["competencia"] = pres["competencia"].astype(int)
    pres.to_sql("estban_presenca", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_estban_pres ON estban_presenca "
                "(cod_ibge7, competencia)")

    comp = con.execute("SELECT MIN(competencia), MAX(competencia), "
                       "COUNT(DISTINCT cod_ibge7) FROM estban_municipio").fetchone()
    print(f"  meses: {len(arquivos)}  registros: {len(est)}  "
          f"municípios: {comp[2]}  ({comp[0]}–{comp[1]})")
    if nao_casados:
        print(f"  [aviso] {nao_casados} linhas sem código IBGE válido descartadas")


def _agregar_caged(arq7z: Path, cache: Path) -> None:
    """Agrega um arquivo de microdados (MOV/FOR/EXC) por competência de
    movimentação × município × divisão CNAE e grava CSV em cache. O .txt
    (~700 MB) é extraído em pasta temporária e apagado ao final."""
    import shutil
    import tempfile

    import py7zr

    tmp = Path(tempfile.mkdtemp(prefix="caged_"))
    try:
        with py7zr.SevenZipFile(arq7z) as z:
            z.extractall(tmp)
        txt = next(tmp.glob("*.txt"))
        partes = []
        for chunk in pd.read_csv(txt, sep=";", encoding="utf-8", dtype=str,
                                 chunksize=1_000_000):
            chunk.columns = [sem_acento(c).lower() for c in chunk.columns]
            chunk = chunk[["competenciamov", "municipio", "subclasse",
                           "saldomovimentacao", "salario"]]
            chunk["saldo"] = pd.to_numeric(chunk["saldomovimentacao"],
                                           errors="coerce").fillna(0).astype(int)
            chunk["salario"] = pd.to_numeric(
                chunk["salario"].str.replace(",", ".", regex=False),
                errors="coerce")
            chunk["divisao"] = chunk["subclasse"].str[:2]
            chunk["admissao"] = (chunk["saldo"] > 0).astype(int)
            chunk["desligamento"] = (chunk["saldo"] < 0).astype(int)
            chunk["salario_adm"] = chunk["salario"].where(chunk["saldo"] > 0)
            partes.append(chunk.groupby(
                ["competenciamov", "municipio", "divisao"], as_index=False).agg(
                admissoes=("admissao", "sum"),
                desligamentos=("desligamento", "sum"),
                saldo=("saldo", "sum"),
                salario_soma_adm=("salario_adm", "sum"),
                salario_n_adm=("salario_adm", "count")))
        agg = (pd.concat(partes, ignore_index=True)
               .groupby(["competenciamov", "municipio", "divisao"],
                        as_index=False).sum())
        agg.to_csv(cache, sep=";", index=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def carregar_caged(con: sqlite3.Connection) -> None:
    """Emprego formal (Novo CAGED). Consolidação oficial: movimentações no
    prazo (MOV) + fora do prazo (FOR) − excluídas (EXC), pela competência de
    movimentação. Agregados por arquivo ficam em cache (data/raw/caged/
    agregado) — só arquivos novos reprocessam microdados."""
    print("== Novo CAGED: emprego formal por município × divisão CNAE ==")
    pasta = RAW / "caged"
    cache_dir = pasta / "agregado"
    cache_dir.mkdir(parents=True, exist_ok=True)
    arquivos = sorted(pasta.glob("CAGED???[0-9]*.7z"))
    if not arquivos:
        print("  [aviso] nenhum arquivo CAGED em data/raw/caged")
        return

    frames = []
    for arq in arquivos:
        cache = cache_dir / (arq.stem + ".csv")
        if not cache.exists():
            print(f"  [agregando] {arq.name}")
            try:
                _agregar_caged(arq, cache)
            except Exception as exc:
                print(f"  [ERRO     ] {arq.name}: {exc}")
                continue
        df = pd.read_csv(cache, sep=";", dtype={"municipio": str,
                                                "divisao": str})
        sinal = -1 if arq.stem.startswith("CAGEDEXC") else 1
        for col in ("admissoes", "desligamentos", "saldo",
                    "salario_soma_adm", "salario_n_adm"):
            df[col] = sinal * df[col]
        frames.append(df)
    if not frames:
        return

    tudo = (pd.concat(frames, ignore_index=True)
            .groupby(["competenciamov", "municipio", "divisao"],
                     as_index=False).sum())
    tudo = tudo.rename(columns={"competenciamov": "competencia",
                                "municipio": "cod_ibge6"})
    tudo["competencia"] = pd.to_numeric(tudo["competencia"],
                                        errors="coerce").astype("Int64")
    tudo = tudo.dropna(subset=["competencia"])
    # município 6 dígitos -> 7 dígitos (com DV) pela dimensão
    depara = dict(con.execute("SELECT cod_ibge6, cod_ibge7 FROM municipio"))
    tudo["cod_ibge7"] = tudo["cod_ibge6"].map(depara)
    sem_mun = int(tudo["cod_ibge7"].isna().sum())
    tudo = tudo.dropna(subset=["cod_ibge7"])
    tudo["salario_medio_adm"] = (tudo["salario_soma_adm"] /
                                 tudo["salario_n_adm"].astype(float)
                                     .replace(0.0, float("nan"))).round(2)
    final = tudo[["competencia", "cod_ibge7", "divisao", "admissoes",
                  "desligamentos", "saldo", "salario_medio_adm"]]
    final.to_sql("caged_mensal", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_caged ON caged_mensal "
                "(cod_ibge7, divisao, competencia)")
    con.execute("CREATE INDEX ix_caged_comp ON caged_mensal "
                "(competencia, divisao)")
    comp = con.execute("SELECT MIN(competencia), MAX(competencia) "
                       "FROM caged_mensal").fetchone()
    print(f"  registros: {len(final)}  ({comp[0]}–{comp[1]})")
    if sem_mun:
        print(f"  [aviso] {sem_mun} agregados sem município na dimensão descartados")


def carregar_cempre(con: sqlite3.Connection) -> None:
    print("== IBGE CEMPRE: empresas por seção CNAE × município ==")
    frames = []
    for arq in sorted((RAW / "sidra").glob("cempre_secao_*.json")):
        df = ler_sidra(arq)
        if df.empty:
            continue
        try:
            c_mun = col_sidra(df, "Município (Código)")
            c_var = col_sidra(df, "Variável (Código)")
            c_ano = col_sidra(df, "Ano (Código)")
            c_cls = col_sidra(df, "Classificação Nacional de Atividades")
        except KeyError as exc:
            print(f"  [ERRO] {arq.name}: {exc} — layout da tabela SIDRA "
                  f"mudou? arquivo pulado")
            continue
        # a coluna de nome da categoria é a versão N da coluna de código
        c_cls_n = c_cls[:-1] + "N" if c_cls.endswith("C") else c_cls
        frames.append(pd.DataFrame({
            "cod_ibge7": df[c_mun].astype(str),
            "ano": pd.to_numeric(df[c_ano], errors="coerce"),
            "variavel": pd.to_numeric(df[c_var], errors="coerce"),
            "categoria": df[c_cls_n].astype(str),
            "valor": pd.to_numeric(df["V"], errors="coerce"),
        }))
    if not frames:
        print("  [aviso] nenhum arquivo cempre_secao em data/raw/sidra")
        return
    NOMES = {2585: "empresas", 707: "pessoal_total",
             708: "pessoal_assalariado", 662: "salarios_mil"}
    cem = pd.concat(frames, ignore_index=True).dropna(subset=["ano", "variavel"])
    # "A Agricultura, pecuária, ..." -> secao "A" + nome
    cem["secao"] = cem["categoria"].str.strip().str[0]
    cem["coluna"] = cem["variavel"].astype(int).map(NOMES)
    largo = cem.pivot_table(index=["cod_ibge7", "ano", "secao"],
                            columns="coluna", values="valor",
                            aggfunc="first").reset_index()
    largo["ano"] = largo["ano"].astype(int)
    largo.to_sql("cempre_municipio", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_cempre ON cempre_municipio "
                "(cod_ibge7, secao, ano)")
    anos = con.execute("SELECT MIN(ano), MAX(ano) FROM cempre_municipio").fetchone()
    print(f"  registros: {len(largo)} (anos {anos[0]}–{anos[1]})")

    frames = []
    for arq in sorted((RAW / "sidra").glob("cempre_total_*.json")):
        df = ler_sidra(arq)
        if df.empty:
            continue
        try:
            c_mun = col_sidra(df, "Município (Código)")
            c_var = col_sidra(df, "Variável (Código)")
            c_ano = col_sidra(df, "Ano (Código)")
        except KeyError as exc:
            print(f"  [ERRO] {arq.name}: {exc} — layout da tabela SIDRA "
                  f"mudou? arquivo pulado")
            continue
        frames.append(pd.DataFrame({
            "cod_ibge7": df[c_mun].astype(str),
            "ano": pd.to_numeric(df[c_ano], errors="coerce"),
            "variavel": pd.to_numeric(df[c_var], errors="coerce"),
            "valor": pd.to_numeric(df["V"], errors="coerce"),
        }))
    if not frames:
        return
    NOMES_TOT = {706: "unidades_locais", 367: "empresas_atuantes",
                 707: "pessoal_total", 708: "pessoal_assalariado",
                 662: "salarios_mil", 10143: "salario_medio_reais"}
    tot = pd.concat(frames, ignore_index=True).dropna(subset=["ano", "variavel"])
    tot["coluna"] = tot["variavel"].astype(int).map(NOMES_TOT)
    largo = tot.pivot_table(index=["cod_ibge7", "ano"], columns="coluna",
                            values="valor", aggfunc="first").reset_index()
    largo["ano"] = largo["ano"].astype(int)
    largo.to_sql("cempre_municipio_total", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_cempre_tot ON cempre_municipio_total "
                "(cod_ibge7, ano)")
    print(f"  totais municipais: {len(largo)}")


def carregar_sementes(con: sqlite3.Connection) -> None:
    print("== Sementes (data/manual) ==")
    div = ler_semente("cnae_divisoes.csv")
    div.to_sql("cnae_divisao", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_cnae_div ON cnae_divisao (divisao)")
    print(f"  divisões CNAE 2.0: {len(div)}")

    verb = ler_semente("estban_verbetes.csv")
    verb.to_sql("estban_verbete", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_verbete ON estban_verbete (verbete)")
    print(f"  verbetes ESTBAN: {len(verb)}")

    sgs = ler_semente("sgs_series.csv")
    sgs["codigo"] = sgs["codigo"].astype(int)
    sgs.to_sql("sgs_serie", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_sgs_serie ON sgs_serie (codigo)")
    print(f"  séries SGS: {len(sgs)}")

    mapa = ler_semente("setor_mapa.csv")
    mapa.to_sql("setor_mapa", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_setor_mapa ON setor_mapa (setor_padrao)")
    print(f"  setores consolidados: {len(mapa)}")

    if (MANUAL / "credito_detalhado_series.csv").exists():
        cd = ler_semente("credito_detalhado_series.csv")
        cd["codigo"] = cd["codigo"].astype(int)
        cd.to_sql("credito_detalhado_serie", con, if_exists="replace", index=False)
        con.execute("CREATE UNIQUE INDEX ix_cred_det_serie ON "
                    "credito_detalhado_serie (codigo)")
        print(f"  séries de crédito detalhado (BCB): {len(cd)}")
    else:
        print("  [aviso] credito_detalhado_series.csv ainda não existe "
              "(rode descobrir_credito_detalhado.py)")


def carregar_sgs(con: sqlite3.Connection) -> None:
    print("== BACEN SGS: valores das séries ==")
    frames, corrompidos = [], 0
    for arq in sorted((RAW / "sgs").glob("*.json")):
        try:
            with open(arq, encoding="utf-8") as fh:
                dados = json.load(fh)
        except json.JSONDecodeError:
            # o BACEN ocasionalmente responde 200 OK com uma página de erro
            # HTML em vez de JSON (sob carga); apaga para forçar rebaixa na
            # próxima atualização, em vez de travar a carga inteira
            arq.unlink()
            corrompidos += 1
            continue
        if not dados:
            continue
        if not isinstance(dados, list):
            # série sem nenhum dado publicado (o BCB responde 200 OK com um
            # corpo tipo {"erro": {...}} em vez de lista vazia) — não é
            # arquivo corrompido, é uma série real que nunca teve valor
            continue
        df = pd.DataFrame(dados)
        df["codigo"] = int(arq.stem)
        frames.append(df)
    if corrompidos:
        print(f"  [aviso] {corrompidos} arquivo(s) corrompido(s) descartado(s) "
              f"(rebaixam na próxima atualização)")
    if not frames:
        print("  [aviso] nenhum arquivo SGS em data/raw/sgs")
        return
    sgs = pd.concat(frames, ignore_index=True)
    # data dd/mm/aaaa -> competencia AAAAMM e data ISO
    partes = sgs["data"].str.split("/", expand=True)
    sgs["competencia"] = (partes[2] + partes[1]).astype(int)
    sgs["data"] = partes[2] + "-" + partes[1] + "-" + partes[0]
    sgs["valor"] = pd.to_numeric(sgs["valor"], errors="coerce")
    sgs = sgs.dropna(subset=["valor"])[["codigo", "competencia", "data", "valor"]]
    sgs.to_sql("sgs_valor", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_sgs_valor ON sgs_valor (codigo, competencia)")
    n_series = sgs["codigo"].nunique()
    comp = con.execute("SELECT MIN(competencia), MAX(competencia) FROM sgs_valor").fetchone()
    print(f"  séries: {n_series}  valores: {len(sgs)}  ({comp[0]}–{comp[1]})")


def carregar_credito_detalhado(con: sqlite3.Connection) -> None:
    """Crédito por modalidade, porte de PJ, MEI, ICC e prazo médio — mesma
    lógica de carregar_sgs (código descoberto pelo nome do arquivo), pasta
    própria data/raw/credito_detalhado/ para não misturar com sgs_valor."""
    print("== BACEN: crédito detalhado (modalidade, porte, MEI, ICC, prazo) ==")
    pasta = RAW / "credito_detalhado"
    frames, corrompidos = [], 0
    for arq in sorted(pasta.glob("*.json")) if pasta.exists() else []:
        try:
            with open(arq, encoding="utf-8") as fh:
                dados = json.load(fh)
        except json.JSONDecodeError:
            # o BACEN ocasionalmente responde 200 OK com uma página de erro
            # HTML em vez de JSON (sob carga); apaga para forçar rebaixa na
            # próxima atualização, em vez de travar a carga inteira
            arq.unlink()
            corrompidos += 1
            continue
        if not dados:
            continue
        if not isinstance(dados, list):
            # série sem nenhum dado publicado (o BCB responde 200 OK com um
            # corpo tipo {"erro": {...}} em vez de lista vazia) — não é
            # arquivo corrompido, é uma série real que nunca teve valor
            continue
        df = pd.DataFrame(dados)
        df["codigo"] = int(arq.stem)
        frames.append(df)
    if corrompidos:
        print(f"  [aviso] {corrompidos} arquivo(s) corrompido(s) descartado(s) "
              f"(rebaixam na próxima atualização)")
    if not frames:
        print("  [aviso] nenhum arquivo em data/raw/credito_detalhado")
        return
    cd = pd.concat(frames, ignore_index=True)
    partes = cd["data"].str.split("/", expand=True)
    cd["competencia"] = (partes[2] + partes[1]).astype(int)
    cd["data"] = partes[2] + "-" + partes[1] + "-" + partes[0]
    cd["valor"] = pd.to_numeric(cd["valor"], errors="coerce")
    cd = cd.dropna(subset=["valor"])[["codigo", "competencia", "data", "valor"]]
    cd.to_sql("credito_detalhado_valor", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_cred_det_valor ON "
                "credito_detalhado_valor (codigo, competencia)")
    n_series = cd["codigo"].nunique()
    comp = con.execute("SELECT MIN(competencia), MAX(competencia) "
                       "FROM credito_detalhado_valor").fetchone()
    print(f"  séries: {n_series}  valores: {len(cd)}  ({comp[0]}–{comp[1]})")


def carregar_ifdata(con: sqlite3.Connection) -> None:
    """IF.data (BCB): cadastro trimestral de instituições financeiras
    (segmento prudencial S1-S5, UF, situação) e Demonstração de Resultado
    (DRE) por instituição individual. Cada arquivo é a resposta OData bruta
    de um trimestre ({"value": [...]})."""
    print("== BACEN IF.data: cadastro + DRE por instituição ==")
    pasta = RAW / "ifdata"
    if not pasta.exists():
        print("  [aviso] nenhum arquivo em data/raw/ifdata")
        return

    cadastros, dres, corrompidos = [], [], 0
    for arq in sorted(pasta.glob("cadastro_*.json")):
        try:
            with open(arq, encoding="utf-8") as fh:
                registros = json.load(fh).get("value", [])
        except json.JSONDecodeError:
            arq.unlink()
            corrompidos += 1
            continue
        if registros:
            cadastros.append(pd.DataFrame(registros))
    for arq in sorted(pasta.glob("dre_*.json")):
        try:
            with open(arq, encoding="utf-8") as fh:
                registros = json.load(fh).get("value", [])
        except json.JSONDecodeError:
            arq.unlink()
            corrompidos += 1
            continue
        if registros:
            dres.append(pd.DataFrame(registros))

    if corrompidos:
        print(f"  [aviso] {corrompidos} arquivo(s) corrompido(s) descartado(s) "
              f"(rebaixam na próxima atualização)")
    if not cadastros or not dres:
        print("  [aviso] cadastro ou DRE do IF.data ausente")
        return

    cad = pd.concat(cadastros, ignore_index=True)
    cad = cad.rename(columns={
        "CodInst": "codinst", "Data": "anomes", "NomeInstituicao": "nome",
        "Sr": "segmento", "Uf": "uf", "Municipio": "municipio", "Situacao": "situacao",
        "Tc": "tipo_consolidacao", "CodConglomeradoFinanceiro": "cod_conglomerado_financeiro",
        "CodConglomeradoPrudencial": "cod_conglomerado_prudencial"})
    cad["anomes"] = cad["anomes"].astype(int)
    cad = cad[["codinst", "anomes", "nome", "segmento", "uf", "municipio", "situacao",
               "tipo_consolidacao", "cod_conglomerado_financeiro", "cod_conglomerado_prudencial"]]
    cad = cad.drop_duplicates(subset=["codinst", "anomes"])
    cad.to_sql("ifdata_instituicao", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_ifdata_inst ON ifdata_instituicao (codinst, anomes)")

    dre = pd.concat(dres, ignore_index=True)
    dre = dre.rename(columns={"CodInst": "codinst", "AnoMes": "anomes", "Conta": "conta"})
    dre["anomes"] = dre["anomes"].astype(int)
    # a Olinda documenta "valores em R$ mil" pro IF.data, mas em
    # TipoInstituicao=1 (conglomerado prudencial — usado aqui desde
    # jul/2026 pra consolidar por grupo econômico) o "Saldo" na verdade
    # vem em REAIS CHEIOS — confirmado comparando com números públicos
    # reais (e com o mesmo valor bruto obtido de forma independente via
    # www3.bcb.gov.br/ifdata): Ativo Total do Itaú bate em ~R$2,83 tri e
    # Lucro Líquido em ~R$12,15 bi só se o "Saldo" for a própria cifra em
    # reais, não milhares (ex.: uma cooperativa pequena aparecia com
    # ~R$698 BILHÕES de ativo se tratado como R$ mil, quando na real são
    # ~R$698 milhões). Divide por 1000 aqui, uma única vez, pra normalizar
    # pra R$ mil (convenção que o resto do pipeline — views, `fmtReaisMil`
    # etc. — sempre assumiu).
    dre["valor"] = pd.to_numeric(dre["Saldo"], errors="coerce") / 1000
    dre = dre.dropna(subset=["valor"])[["codinst", "anomes", "conta", "valor"]]
    dre = dre.drop_duplicates(subset=["codinst", "anomes", "conta"])
    dre.to_sql("ifdata_dre_valor", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_ifdata_dre ON ifdata_dre_valor (codinst, anomes, conta)")

    # carteira de crédito ativa: aging PF/PJ (relatórios 11/13), CNAE (12),
    # porte do tomador (14) — nível de conglomerado prudencial
    RELATORIOS_CARTEIRA = {"aging_pf": "11", "cnae": "12", "aging_pj": "13", "porte": "14"}
    carteiras = []
    for prefixo, relatorio in RELATORIOS_CARTEIRA.items():
        for arq in sorted(pasta.glob(f"{prefixo}_*.json")):
            try:
                with open(arq, encoding="utf-8") as fh:
                    registros = json.load(fh).get("value", [])
            except json.JSONDecodeError:
                arq.unlink()
                corrompidos += 1
                continue
            if registros:
                carteiras.append(pd.DataFrame(registros))
    if carteiras:
        cart = pd.concat(carteiras, ignore_index=True)
        cart = cart.rename(columns={
            "CodInst": "codinst", "AnoMes": "anomes", "NumeroRelatorio": "relatorio",
            "Grupo": "grupo", "Conta": "conta", "NomeColuna": "nome_coluna"})
        cart["anomes"] = cart["anomes"].astype(int)
        # mesma correção de escala do DRE acima (TipoInstituicao=1 vem em
        # reais cheios, não R$ mil, apesar da documentação da Olinda)
        cart["valor"] = pd.to_numeric(cart["Saldo"], errors="coerce") / 1000
        cart = cart.dropna(subset=["valor"])
        cart = cart[["codinst", "anomes", "relatorio", "grupo", "conta", "nome_coluna", "valor"]]
        cart = cart.drop_duplicates(subset=["codinst", "anomes", "relatorio", "conta"])
        cart.to_sql("ifdata_carteira_valor", con, if_exists="replace", index=False)
        con.execute("CREATE UNIQUE INDEX ix_ifdata_carteira ON "
                    "ifdata_carteira_valor (codinst, anomes, relatorio, conta)")
        print(f"  carteira (aging/CNAE/porte): {len(cart)} valores")
    else:
        print("  [aviso] nenhum arquivo de carteira (aging/CNAE/porte) em data/raw/ifdata")

    trims = sorted(cad["anomes"].unique())
    print(f"  instituições: {cad['codinst'].nunique()}  trimestres: {len(trims)} "
          f"({trims[0]}–{trims[-1]})  valores DRE: {len(dre)}")


def carregar_taxa_juros_instituicao(con: sqlite3.Connection) -> None:
    """Taxa de juros por instituição financeira, segmento (PF/PJ) e
    modalidade (serviço BCB "taxaJuros", separado do IF.data) — 1 arquivo
    por semana amostrada (1 por mês) para a maioria das modalidades, mais os
    arquivos "imobiliario_*" (entidade mensal, sem campo de segmento — aqui
    marcado manualmente como Pessoa Física, ver MODALIDADES_IMOBILIARIO_PF
    em baixar_dados.py)."""
    print("== BACEN: taxa de juros por instituição, PF/PJ e modalidade ==")
    pasta = RAW / "taxa_juros"
    if not pasta.exists():
        print("  [aviso] nenhum arquivo em data/raw/taxa_juros")
        return

    frames, imobiliario, corrompidos = [], [], 0
    for arq in sorted(pasta.glob("*.json")):
        try:
            with open(arq, encoding="utf-8") as fh:
                registros = json.load(fh).get("value", [])
        except json.JSONDecodeError:
            arq.unlink()
            corrompidos += 1
            continue
        if not registros:
            continue
        (imobiliario if arq.stem.startswith("imobiliario_") else frames).append(pd.DataFrame(registros))
    if corrompidos:
        print(f"  [aviso] {corrompidos} arquivo(s) corrompido(s) descartado(s) "
              f"(rebaixam na próxima atualização)")
    if not frames and not imobiliario:
        print("  [aviso] nenhum dado de taxa de juros por instituição carregado")
        return

    partes = []
    if frames:
        tx = pd.concat(frames, ignore_index=True)
        tx = tx.rename(columns={
            "InicioPeriodo": "inicio_periodo", "InstituicaoFinanceira": "instituicao",
            "Segmento": "segmento", "Modalidade": "modalidade",
            "TaxaJurosAoMes": "taxa_mes_pct", "TaxaJurosAoAno": "taxa_ano_pct"})
        partes.append(tx[["inicio_periodo", "cnpj8", "instituicao", "segmento",
                           "modalidade", "taxa_mes_pct", "taxa_ano_pct"]])
    if imobiliario:
        im = pd.concat(imobiliario, ignore_index=True)
        im["inicio_periodo"] = im["anoMes"] + "-01"
        im["segmento"] = "Pessoa Física"
        im = im.rename(columns={
            "InstituicaoFinanceira": "instituicao", "Modalidade": "modalidade",
            "TaxaJurosAoMes": "taxa_mes_pct", "TaxaJurosAoAno": "taxa_ano_pct"})
        partes.append(im[["inicio_periodo", "cnpj8", "instituicao", "segmento",
                           "modalidade", "taxa_mes_pct", "taxa_ano_pct"]])

    tx = pd.concat(partes, ignore_index=True)
    tx["taxa_mes_pct"] = pd.to_numeric(tx["taxa_mes_pct"], errors="coerce")
    tx["taxa_ano_pct"] = pd.to_numeric(tx["taxa_ano_pct"], errors="coerce")
    tx = tx.dropna(subset=["taxa_mes_pct"])
    tx = tx.drop_duplicates(subset=["inicio_periodo", "cnpj8", "segmento", "modalidade"])
    tx.to_sql("taxa_juros_instituicao", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_taxa_juros_inst ON "
                "taxa_juros_instituicao (inicio_periodo, cnpj8, segmento, modalidade)")
    semanas = sorted(tx["inicio_periodo"].unique())
    print(f"  semanas: {len(semanas)} ({semanas[0]}–{semanas[-1]})  "
          f"instituições: {tx['cnpj8'].nunique()}  valores: {len(tx)}")


# espelha DATAJUD_CLASSES/DATAJUD_TRIBUNAIS de baixar_dados.py (módulos não
# se importam entre si nesse projeto — mesmo padrão usado por
# RELATORIOS_CARTEIRA etc.)
DATAJUD_CLASSES = {128: "RECUPERACAO_EXTRAJUDICIAL", 129: "RECUPERACAO_JUDICIAL",
                    108: "FALENCIA"}
DATAJUD_TRIBUNAIS = {
    "AC": "tjac", "AL": "tjal", "AP": "tjap", "AM": "tjam", "BA": "tjba",
    "CE": "tjce", "DF": "tjdft", "ES": "tjes", "GO": "tjgo", "MA": "tjma",
    "MT": "tjmt", "MS": "tjms", "MG": "tjmg", "PA": "tjpa", "PB": "tjpb",
    "PR": "tjpr", "PE": "tjpe", "PI": "tjpi", "RJ": "tjrj", "RN": "tjrn",
    "RS": "tjrs", "RO": "tjro", "RR": "tjrr", "SC": "tjsc", "SP": "tjsp",
    "SE": "tjse", "TO": "tjto",
}


def carregar_datajud_rj_falencia(con: sqlite3.Connection) -> None:
    """Recuperação Judicial, Extrajudicial e Falência por UF — a partir da
    agregação Elasticsearch (por mês × classe processual) baixada de cada
    tribunal estadual pela API pública DataJud (CNJ)."""
    print("== CNJ DataJud: Recuperação Judicial, Extrajudicial e Falência por UF ==")
    pasta = RAW / "datajud_rj_falencia"
    if not pasta.exists():
        print("  [aviso] nenhum arquivo em data/raw/datajud_rj_falencia")
        return

    linhas = []
    corrompidos = 0
    for arq in sorted(pasta.glob("*.json")):
        uf = arq.stem
        try:
            dados = json.loads(arq.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # mesma tolerância usada nas demais fontes: apaga pra forçar
            # rebaixa na próxima atualização, não trava a carga inteira
            arq.unlink()
            corrompidos += 1
            continue
        buckets = dados.get("aggregations", {}).get("por_mes", {}).get("buckets", [])
        for bucket in buckets:
            competencia = int(bucket["key"])
            for sub in bucket.get("por_classe", {}).get("buckets", []):
                classe = DATAJUD_CLASSES.get(sub["key"])
                if classe is None:
                    continue
                linhas.append(dict(uf=uf, tribunal=DATAJUD_TRIBUNAIS.get(uf, uf),
                                    competencia=competencia, classe=classe,
                                    processos=sub["doc_count"]))

    if corrompidos:
        print(f"  [aviso] {corrompidos} arquivo(s) corrompido(s) descartado(s) "
              f"(rebaixam na próxima atualização)")
    if not linhas:
        print("  [aviso] nenhum dado de Recuperação Judicial/Falência carregado")
        return

    df = pd.DataFrame(linhas).sort_values(["competencia", "uf", "classe"])
    df.to_sql("datajud_rj_falencia_mensal", con, if_exists="replace", index=False)
    print(f"  {len(df)} linhas ({df['uf'].nunique()} UFs, competências "
          f"{df['competencia'].min()}–{df['competencia'].max()})")


def carregar_malha_uf(con: sqlite3.Connection) -> None:
    """Contorno geográfico real de cada UF (IBGE) — guarda só a geometry
    (type + coordinates) de cada GeoJSON, sem o envelope FeatureCollection,
    que o mapa regional não usa."""
    print("== IBGE: malhas territoriais (contorno de UF) ==")
    pasta = RAW / "malha_uf"
    linhas = []
    for arq in sorted(pasta.glob("*.json")) if pasta.exists() else []:
        try:
            with open(arq, encoding="utf-8") as fh:
                geo = json.load(fh)
            geometria = geo["features"][0]["geometry"]
        except (json.JSONDecodeError, KeyError, IndexError):
            continue
        linhas.append({"uf": arq.stem, "geojson": json.dumps(geometria, separators=(",", ":"))})
    if not linhas:
        print("  [aviso] nenhum arquivo em data/raw/malha_uf")
        return
    pd.DataFrame(linhas).to_sql("malha_uf", con, if_exists="replace", index=False)
    con.execute("CREATE UNIQUE INDEX ix_malha_uf ON malha_uf (uf)")
    print(f"  UFs carregadas: {len(linhas)}")


def executar_schema(con: sqlite3.Connection) -> None:
    print("== Aplicando schema.sql (views analíticas) ==")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    con.commit()


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()  # recarga do zero: sempre consistente
    con = sqlite3.connect(DB_PATH)

    carregar_municipios(con)
    carregar_sementes(con)
    carregar_ipea(con)
    carregar_populacao(con)
    carregar_sgs(con)
    carregar_credito_detalhado(con)
    carregar_ifdata(con)
    carregar_taxa_juros_instituicao(con)
    carregar_datajud_rj_falencia(con)
    carregar_pib_municipal(con)
    carregar_pib_trimestral(con)
    carregar_estban(con)
    carregar_caged(con)
    carregar_cempre(con)
    carregar_malha_uf(con)
    executar_schema(con)

    con.commit()
    con.close()
    print(f"\nBanco gravado em {DB_PATH}")


if __name__ == "__main__":
    main()
