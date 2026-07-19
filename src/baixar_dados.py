# -*- coding: utf-8 -*-
"""
Baixa os dados abertos das fontes oficiais do Sistema Atividade Econômica.

Fontes (v1):
  - IBGE localidades : municípios do Brasil (dimensão território)
  - IBGE SIDRA t/6579: população municipal estimada
  - BACEN SGS        : séries de crédito, inadimplência, concessões, juros
                       e atividade (semente data/manual/sgs_series.csv)
  - IBGE SIDRA t/5938: PIB municipal e VAB por setor (anual)
  - IBGE SIDRA t/1620 e t/5932: PIB trimestral nacional por setor
  - BACEN ESTBAN     : balancete bancário por município (crédito etc.)
  - Novo CAGED (MTE) : microdados de emprego formal (FTP, arquivos .7z)
  - IBGE SIDRA t/9418 e t/9509 (CEMPRE): empresas e unidades locais
                       por seção CNAE × município (anual, 2022+)
  - IPEADATA         : população municipal histórica (desde 1992) e
                       CAGED nacional 1999+ (semente
                       data/manual/ipea_series.csv)
  - BACEN SGS (crédito detalhado): taxa de juros/inadimplência por
                       modalidade, saldo por porte de PJ e MEI, ICC e
                       prazo médio (semente gerada por
                       descobrir_credito_detalhado.py — ~870 séries)
  - IBGE malhas territoriais: contorno geográfico de cada UF (GeoJSON),
                       usado para desenhar o mapa regional
  - BACEN IF.data (Olinda/OData): cadastro de instituições financeiras
                       (segmento prudencial S1-S5, UF, situação),
                       Demonstração de Resultado (DRE) trimestral por
                       instituição individual, e carteira de crédito ativa
                       por modalidade/prazo de vencimento (aging), CNAE e
                       porte do tomador, por conglomerado prudencial —
                       com plano B (www3.bcb.gov.br/ifdata, backend
                       diferente/mesmos códigos de conta) pra cadastro+DRE
                       quando a Olinda cai, cobrindo só os últimos ~5
                       trimestres
  - BACEN taxaJuros (Olinda/OData): taxa de juros por instituição
                       financeira, segmento (PF/PJ) e modalidade, semanal
  - CNJ DataJud (API pública, Elasticsearch por tribunal): contagem de
                       processos de Recuperação Judicial, Extrajudicial e
                       Falência por tribunal estadual (UF) e mês, a partir
                       da classe processual (Tabela Processual Unificada)

Uso:
    python baixar_dados.py [--desde AAAAMM] [--force]
"""
import argparse
import csv
import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from caminhos import DIR_MANUAL as MANUAL, DIR_RAW as RAW
HEADERS = {"User-Agent": "Mozilla/5.0 (sistema-atividade-economica)"}

# códigos IBGE das 27 UFs
UFS = [11, 12, 13, 14, 15, 16, 17,            # Norte
       21, 22, 23, 24, 25, 26, 27, 28, 29,    # Nordeste
       31, 32, 33, 35,                        # Sudeste
       41, 42, 43,                            # Sul
       50, 51, 52, 53]                        # Centro-Oeste


def baixar(url: str, destino: Path, force: bool = False,
          tentativas: int = 3) -> Path | None:
    """Baixa `url` para `destino`. As APIs do BACEN/IBGE falham de forma
    transitória com alguma frequência sob uso sequencial (timeout, conexão
    recusada) — refaz até `tentativas` vezes com espera curta antes de
    desistir e reportar [ERRO]."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists() and destino.stat().st_size > 0 and not force:
        print(f"  [mantido ] {destino.name}")
        return destino
    print(f"  [baixando] {url}")
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=900) as resp, open(destino, "wb") as fh:
                while True:
                    bloco = resp.read(1 << 20)
                    if not bloco:
                        break
                    fh.write(bloco)
            # algumas APIs (IBGE) respondem gzip mesmo sem Accept-Encoding:
            # descompacta em disco para os leitores não precisarem saber disso
            with open(destino, "rb") as fh:
                magico = fh.read(2)
            if magico == b"\x1f\x8b":
                destino.write_bytes(gzip.decompress(destino.read_bytes()))
            return destino
        except Exception as exc:  # tolerante: fonte pode estar fora do ar
            ultimo_erro = exc
            if destino.exists():
                destino.unlink()
            if tentativa < tentativas:
                print(f"  [retry {tentativa}/{tentativas - 1}] {url}: {exc}")
                time.sleep(2 * tentativa)
    print(f"  [ERRO    ] {url}: {ultimo_erro}")
    return None


# ---------------------------------------------------------------------
# IBGE — municípios e população
# ---------------------------------------------------------------------
def baixar_ibge(force: bool) -> None:
    print("\n== IBGE: municípios (localidades) ==")
    baixar("https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
           "?view=nivelado",
           RAW / "ibge" / "municipios.json", force)

    print("\n== IBGE: PIB dos municípios (SIDRA t/5938) ==")
    # variáveis: 37 PIB, 543 impostos, 498 VAB total, 513 agro, 517 indústria,
    # 6575 serviços (exc. adm pública), 525 adm pública — tudo em Mil Reais.
    # Particionado por UF × bloco de 7 anos: a API SIDRA limita ~100 mil
    # valores por consulta (MG com 853 municípios × 22 anos estourava).
    # Paralelo — ~80 requisições (27 UFs × ~3 blocos), sequencial custava
    # minutos num --force; mesma concorrência moderada usada nas outras
    # fontes públicas deste arquivo.
    ano_max = date.today().year
    blocos = [f"{a}-{min(a + 6, ano_max)}" for a in range(2010, ano_max + 1, 7)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futuros = [pool.submit(
            baixar,
            f"https://apisidra.ibge.gov.br/values/t/5938/n6/in%20n3%20{uf}"
            f"/v/37,543,498,513,517,6575,525/p/{bloco}?formato=json",
            RAW / "sidra" / f"pib_mun_{uf}_{bloco}.json", force)
            for uf in UFS for bloco in blocos]
        for fut in as_completed(futuros):
            fut.result()

    print("\n== IBGE: PIB trimestral por setor (SIDRA t/1620 e t/5932) ==")
    # rebaixa sempre: série revisada e pequena (1 request por tabela)
    baixar("https://apisidra.ibge.gov.br/values/t/1620/n1/all/v/583/p/all"
           "/c11255/all?formato=json",
           RAW / "sidra" / "pib_tri_1620.json", force=True)
    baixar("https://apisidra.ibge.gov.br/values/t/5932/n1/all"
           "/v/6561,6562,6563,6564/p/all/c11255/all?formato=json",
           RAW / "sidra" / "pib_tri_5932.json", force=True)

    print("\n== IBGE: população municipal estimada (SIDRA t/6579) ==")
    # estimativa anual: mantém o arquivo se já existe (27 requisições não
    # compensa refazer a cada atualização mensal); use --force quando o
    # IBGE publicar uma estimativa nova. Paralelo pelo mesmo motivo acima.
    with ThreadPoolExecutor(max_workers=6) as pool:
        futuros = [pool.submit(
            baixar,
            f"https://apisidra.ibge.gov.br/values/t/6579/n6/in%20n3%20{uf}"
            f"/v/9324/p/all?formato=json",
            RAW / "sidra" / f"pop_{uf}.json", force)
            for uf in UFS]
        for fut in as_completed(futuros):
            fut.result()


def meses_ate(desde: str, defasagem_meses: int) -> list[str]:
    """Lista competências AAAAMM de `desde` até (mês corrente − defasagem)."""
    hoje = date.today()
    ano_f, mes_f = hoje.year, hoje.month - defasagem_meses
    while mes_f < 1:
        mes_f, ano_f = mes_f + 12, ano_f - 1
    fim = f"{ano_f}{mes_f:02d}"
    meses, (ano, mes) = [], (int(desde[:4]), int(desde[4:]))
    while f"{ano}{mes:02d}" <= fim:
        meses.append(f"{ano}{mes:02d}")
        mes += 1
        if mes > 12:
            mes, ano = 1, ano + 1
    return meses


# ---------------------------------------------------------------------
# BACEN — ESTBAN (balancete bancário mensal por município)
# ---------------------------------------------------------------------
def baixar_estban(desde: str, force: bool) -> None:
    """ZIP mensal com defasagem de ~60-90 dias; 404 nos meses mais
    recentes é esperado e tolerado (o mês entra na próxima atualização)."""
    print("\n== BACEN ESTBAN: balancete por município ==")
    base = ("https://www.bcb.gov.br/content/estatisticas/"
            "estatistica_bancaria_estban/municipio")
    # paralelo por mês — cada mês ainda tenta .csv.zip e cai pro .ZIP antigo
    # em sequência, mas os meses entre si rodam concorrentes; numa carga
    # inicial (sem cache) são ~78+ meses, minutos a menos que sequencial
    def tarefa(m: str):
        destino = RAW / "estban" / f"{m}_ESTBAN.csv.zip"
        if baixar(f"{base}/{m}_ESTBAN.csv.zip", destino, force) is None:
            baixar(f"{base}/{m}_ESTBAN.ZIP", destino, force)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futuros = [pool.submit(tarefa, m) for m in meses_ate(desde, defasagem_meses=2)]
        for fut in as_completed(futuros):
            fut.result()


# ---------------------------------------------------------------------
# IBGE — CEMPRE (Cadastro Central de Empresas)
# ---------------------------------------------------------------------
# códigos das 21 seções CNAE na classificação c12762 da tabela 9418
CEMPRE_SECOES = ("116830,116880,116910,117296,117307,117329,117363,117484,"
                 "117543,117555,117608,117666,117673,117714,117774,117788,"
                 "117810,117838,117861,117888,117892")


def anos_tabela_sidra(tabela: int) -> list[str]:
    """Consulta os metadados do agregado para saber os anos disponíveis."""
    import json as _json
    url = f"https://servicodados.ibge.gov.br/api/v3/agregados/{tabela}/metadados"
    try:
        req = Request(url, headers=HEADERS)
        bruto = urlopen(req, timeout=120).read()
        if bruto[:2] == b"\x1f\x8b":
            bruto = gzip.decompress(bruto)
        meta = _json.loads(bruto)
        ini = int(meta["periodicidade"]["inicio"])
        fim = int(meta["periodicidade"]["fim"])
        return [str(a) for a in range(ini, fim + 1)]
    except Exception as exc:
        print(f"  [aviso] metadados t/{tabela} indisponíveis ({exc}); usando 2022-2024")
        return ["2022", "2023", "2024"]


def baixar_cempre(force: bool) -> None:
    print("\n== IBGE CEMPRE: empresas por seção CNAE × município (t/9418) ==")
    # particionado por UF × ano × variável: o limite efetivo da API SIDRA
    # nessa tabela é bem menor que os 100 mil valores documentados (MG com
    # 4 variáveis × 21 seções retorna 400); 1 variável nunca passa de
    # ~18 mil linhas. Paralelo — até ~350 requisições (anos × 27 UFs × 4
    # variáveis) num --force; sequencial levava vários minutos.
    tarefas = [(ano, uf, var) for ano in anos_tabela_sidra(9418)
               for uf in UFS for var in (2585, 707, 708, 662)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futuros = [pool.submit(
            baixar,
            f"https://apisidra.ibge.gov.br/values/t/9418"
            f"/n6/in%20n3%20{uf}/v/{var}/p/{ano}"
            f"/c12762/{CEMPRE_SECOES}?formato=json",
            RAW / "sidra" / f"cempre_secao_{uf}_{ano}_{var}.json", force)
            for ano, uf, var in tarefas]
        for fut in as_completed(futuros):
            fut.result()

    print("\n== IBGE CEMPRE: totais municipais de unidades locais (t/9509) ==")
    with ThreadPoolExecutor(max_workers=6) as pool:
        futuros = [pool.submit(
            baixar,
            f"https://apisidra.ibge.gov.br/values/t/9509/n6/in%20n3%20{uf}"
            f"/v/706,367,707,708,662,10143/p/all?formato=json",
            RAW / "sidra" / f"cempre_total_{uf}.json", force)
            for uf in UFS]
        for fut in as_completed(futuros):
            fut.result()


# ---------------------------------------------------------------------
# IPEADATA — séries municipais e nacionais (ODATA4)
# ---------------------------------------------------------------------
def baixar_ipea(force: bool) -> None:
    print("\n== IPEADATA: IDHM, Gini, população histórica, CAGED nacional ==")
    with open(MANUAL / "ipea_series.csv", encoding="utf-8") as fh:
        linhas = [l for l in fh if not l.startswith("#")]
    for reg in csv.DictReader(linhas, delimiter=";"):
        codigo = reg["codigo"].strip()
        rebaixar = force or reg["atualiza"].strip() == "SEMPRE"
        baixar(f"https://www.ipeadata.gov.br/api/odata4/"
               f"ValoresSerie(SERCODIGO='{codigo}')",
               RAW / "ipea" / f"{codigo}.json", rebaixar)


# ---------------------------------------------------------------------
# Novo CAGED — microdados (FTP do MTE/PDET)
# ---------------------------------------------------------------------
def baixar_caged(desde: str) -> None:
    """Baixa CAGEDMOV/CAGEDFOR/CAGEDEXC de cada mês (~50-90 MB/mês no
    total). Incremental por existência do .7z; o mês mais recente pode
    ainda não estar publicado (tolerado). O servidor FTP do MTE derruba
    sessões longas — reconecta e tenta de novo a cada queda."""
    from ftplib import FTP, error_perm

    print("\n== Novo CAGED: microdados de emprego (FTP MTE) ==")
    pasta = RAW / "caged"
    pasta.mkdir(parents=True, exist_ok=True)
    pendentes = [m for m in meses_ate(desde, defasagem_meses=1)
                 if not all((pasta / f"CAGED{t}{m}.7z").exists()
                            for t in ("MOV", "FOR", "EXC"))]
    if not pendentes:
        print("  [mantido ] todos os meses já baixados")
        return

    def conectar() -> FTP:
        ftp = FTP("ftp.mtps.gov.br", timeout=300, encoding="latin-1")
        ftp.login()
        return ftp

    try:
        ftp = conectar()
    except Exception as exc:
        print(f"  [ERRO    ] conexão FTP falhou: {exc}")
        return

    falhas_consecutivas = 0
    for m in pendentes:
        disponiveis = None
        for tentativa in (1, 2):
            try:
                ftp.cwd(f"/pdet/microdados/NOVO CAGED/{m[:4]}/{m}")
                disponiveis = set(ftp.nlst())
                break
            except error_perm:
                print(f"  [aviso   ] {m}: ainda não publicado")
                break
            except Exception:
                if tentativa == 2:
                    print(f"  [ERRO    ] {m}: conexão perdida; mês fica "
                          f"para a próxima atualização")
                    break
                try:
                    ftp = conectar()
                except Exception as exc:
                    print(f"  [ERRO    ] reconexão falhou: {exc}")
                    return
        if disponiveis is None:
            falhas_consecutivas += 1
            if falhas_consecutivas >= 5:
                print("  [ERRO    ] 5 meses seguidos falharam; interrompendo "
                      "o CAGED nesta execução")
                return
            continue
        falhas_consecutivas = 0

        for tipo in ("MOV", "FOR", "EXC"):
            nome = f"CAGED{tipo}{m}.7z"
            destino = pasta / nome
            if destino.exists() and destino.stat().st_size > 0:
                print(f"  [mantido ] {nome}")
                continue
            if nome not in disponiveis:
                print(f"  [aviso   ] {nome}: não disponível")
                continue
            print(f"  [baixando] {nome}")
            for tentativa in (1, 2):
                try:
                    with open(destino, "wb") as fh:
                        ftp.retrbinary(f"RETR {nome}", fh.write)
                    break
                except Exception as exc:
                    if destino.exists():
                        destino.unlink()
                    if tentativa == 2:
                        print(f"  [ERRO    ] {nome}: {exc}")
                        break
                    try:
                        ftp = conectar()
                        ftp.cwd(f"/pdet/microdados/NOVO CAGED/{m[:4]}/{m}")
                    except Exception as exc2:
                        print(f"  [ERRO    ] reconexão falhou: {exc2}")
                        return
    try:
        ftp.quit()
    except Exception:
        pass


# ---------------------------------------------------------------------
# BACEN — SGS (séries temporais)
# ---------------------------------------------------------------------
def baixar_sgs(desde: str) -> None:
    """Rebaixa a série INTEIRA a cada execução: o BACEN revisa valores
    retroativamente e o volume é mínimo (uma requisição JSON por série).
    Paralelo (ThreadPoolExecutor) — mesmo host/endpoint de
    baixar_credito_detalhado, mesma concorrência já validada como segura."""
    print("\n== BACEN SGS: séries de crédito e atividade ==")
    data_inicial = f"01/{desde[4:]}/{desde[:4]}"
    with open(MANUAL / "sgs_series.csv", encoding="utf-8") as fh:
        linhas = [l for l in fh if not l.startswith("#")]
    codigos = [reg["codigo"].strip() for reg in csv.DictReader(linhas, delimiter=";")]

    def tarefa(codigo: str):
        return baixar(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
                      f"?formato=json&dataInicial={data_inicial}",
                      RAW / "sgs" / f"{codigo}.json", force=True)

    ok = erro = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futuros = [pool.submit(tarefa, c) for c in codigos]
        for fut in as_completed(futuros):
            if fut.result() is not None:
                ok += 1
            else:
                erro += 1
    print(f"  {ok} séries baixadas, {erro} falharam (ver [ERRO] acima)")


def baixar_credito_detalhado(desde: str) -> None:
    """Baixa as ~870 séries descobertas por descobrir_credito_detalhado.py
    (crédito por modalidade, porte de PJ, MEI, ICC, prazo médio). Paralelo
    (ThreadPoolExecutor) — não há endpoint em lote no SGS e o volume é grande
    demais para download sequencial; rebaixa sempre, mesmo motivo de
    baixar_sgs (o BACEN revisa valores retroativamente)."""
    print("\n== BACEN: crédito detalhado (modalidade, porte, MEI, ICC, prazo) ==")
    semente = MANUAL / "credito_detalhado_series.csv"
    if not semente.exists():
        print("  [aviso] credito_detalhado_series.csv não existe — rode "
              "descobrir_credito_detalhado.py antes")
        return
    data_inicial = f"01/{desde[4:]}/{desde[:4]}"
    with open(semente, encoding="utf-8") as fh:
        linhas = [l for l in fh if not l.startswith("#")]
    codigos = [reg["codigo"].strip() for reg in csv.DictReader(linhas, delimiter=";")]

    def tarefa(codigo: str):
        time.sleep(0.1)
        destino = RAW / "credito_detalhado" / f"{codigo}.json"
        resultado = baixar(
            f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
            f"?formato=json&dataInicial={data_inicial}", destino, force=True)
        if resultado is None:
            # séries de nicho com histórico curto (ex.: MEI + modalidade
            # pouco usada) retornam 404 quando dataInicial é anterior ao
            # primeiro valor publicado — pedir a série inteira sem filtro
            # de data sempre funciona, mesmo que ela tenha só 1-2 pontos
            resultado = baixar(
                f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
                f"?formato=json", destino, force=True)
        return resultado

    ok = erro = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futuros = [pool.submit(tarefa, c) for c in codigos]
        for fut in as_completed(futuros):
            if fut.result() is not None:
                ok += 1
            else:
                erro += 1
    print(f"  {ok} séries baixadas, {erro} falharam (ver [ERRO] acima)")


SIGLAS_UF = ["AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG",
             "PA","PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"]


def baixar_malha_uf(force: bool = False) -> None:
    """Baixa o contorno geográfico real de cada UF (IBGE, API de malhas
    territoriais) — usado para desenhar o mapa regional com fronteiras
    oficiais em vez de um contorno aproximado. Geografia não muda de um mês
    pro outro, então não força redownload por padrão (--force rebaixa)."""
    print("\n== IBGE: malhas territoriais (contorno de UF) ==")

    def tarefa(sigla: str):
        destino = RAW / "malha_uf" / f"{sigla}.json"
        return baixar(
            f"https://servicodados.ibge.gov.br/api/v3/malhas/estados/{sigla}"
            f"?formato=application/vnd.geo+json&qualidade=minima",
            destino, force=force)

    ok = erro = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futuros = [pool.submit(tarefa, s) for s in SIGLAS_UF]
        for fut in as_completed(futuros):
            if fut.result() is not None:
                ok += 1
            else:
                erro += 1
    print(f"  {ok} UFs baixadas, {erro} falharam (ver [ERRO] acima)")


def trimestres_ate(desde: str, defasagem_meses: int) -> list[str]:
    """Como meses_ate(), mas só as competências de fim de trimestre
    (mar/jun/set/dez) — IF.data (DRE, segmentação) só publica trimestral."""
    return [m for m in meses_ate(desde, defasagem_meses) if int(m[4:]) in (3, 6, 9, 12)]


#  piso de histórico do DRE/cadastro do IF.data — confirmado empiricamente
# (jul/2026) que o Olinda (TipoInstituicao=1, relatório 4) retorna 0 linhas
# em 4T/2013 e já retorna dado real em 1T/2014; não há como saber isso via
# metadado da API, só testando trimestre a trimestre.
DESDE_DRE_MINIMO = "201403"


def baixar_ifdata(desde: str, force: bool = False) -> None:
    """IF.data (BCB, API Olinda/OData): cadastro de instituições financeiras
    (nome, segmento prudencial S1-S5, UF, situação), Demonstração de
    Resultado (DRE, relatório 4) e carteira de crédito ativa por
    modalidade/prazo de vencimento (aging — relatórios 11 PF e 13 PJ), por
    CNAE (relatório 12) e por porte do tomador (relatório 14) — todos no
    nível de CONGLOMERADO PRUDENCIAL (TipoInstituicao=1, Resolução
    4.553/2017: 1 linha por grupo econômico, ex. "ITAU - PRUDENCIAL", não
    2-3 pessoas jurídicas separadas do mesmo banco — pedido explícito do
    usuário, jul/2026). O DRE por instituição individual
    (TipoInstituicao=2) também existe na API mas foi abandonado aqui: com
    TipoInstituicao=1 os 5 relatórios passam a compartilhar o MESMO nível
    de consolidação (e portanto o MESMO `codinst`), o que também corrige o
    limite antigo documentado (não dava pra cruzar DRE com aging/CNAE/porte
    pelo mesmo código).
    Cadastro+DRE usam uma janela própria, sempre até DESDE_DRE_MINIMO
    (2014 Q1) independente de `desde` — pedido explícito do usuário
    (jul/2026) de mais histórico no dashboard de IFs; carteira ativa
    (aging/CNAE/porte) só mostra a foto da última competência hoje, sem
    benefício em baixar mais que `desde`, então fica na janela padrão.
    Paralelo (ThreadPoolExecutor) — cada trimestre é uma chamada só por
    relatório (a API já retorna todas as instituições de uma vez), mas o
    payload total é grande (~dezenas de MB por trimestre). Cadastro/DRE
    rebaixam sempre (mesmo motivo de baixar_sgs/credito_detalhado: o BCB
    pode retificar valores de trimestres passados) — como a janela de DRE é
    bem mais longa que a de carteira, isso custa mais tempo a cada
    atualização mensal do que antes (mais trimestres pra re-baixar), troca
    aceita pelo usuário em troca do histórico maior."""
    print("\n== BACEN IF.data: cadastro + DRE + carteira (aging/CNAE/porte) por instituição ==")
    base = "https://olinda.bcb.gov.br/olinda/servico/IFDATA/versao/v1/odata"
    trimestres_dre = set(trimestres_ate(min(desde, DESDE_DRE_MINIMO), defasagem_meses=3))
    trimestres_carteira = set(trimestres_ate(desde, defasagem_meses=3))
    trimestres = sorted(trimestres_dre | trimestres_carteira)
    RELATORIOS_CARTEIRA = {"11": "aging_pf", "12": "cnae", "13": "aging_pj", "14": "porte"}

    def tarefa(am: str):
        ok = True
        if am in trimestres_dre:
            ok = baixar(f"{base}/IfDataCadastro(AnoMes={am})?$format=json",
                        RAW / "ifdata" / f"cadastro_{am}.json", force=True) is not None and ok
            ok = baixar(f"{base}/IfDataValores(AnoMes={am},TipoInstituicao=1,Relatorio='4')"
                        f"?$format=json",
                        RAW / "ifdata" / f"dre_{am}.json", force=True) is not None and ok
        if am in trimestres_carteira:
            for rel, nome in RELATORIOS_CARTEIRA.items():
                ok = baixar(f"{base}/IfDataValores(AnoMes={am},TipoInstituicao=1,Relatorio='{rel}')"
                            f"?$format=json",
                            RAW / "ifdata" / f"{nome}_{am}.json", force=True) is not None and ok
        return ok

    ok = erro = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futuros = [pool.submit(tarefa, am) for am in trimestres]
        for fut in as_completed(futuros):
            if fut.result():
                ok += 1
            else:
                erro += 1
    print(f"  {ok} trimestres baixados, {erro} falharam (ver [ERRO] acima)")


# Códigos de conta do DRE (esquema COSIF 2025, "novo esquema" — ver nota em
# schema.sql/v_ifdata_dre) usados por baixar_ifdata_www3_fallback() pra
# filtrar, do shard "dados_1" do backend alternativo, só as contas que o
# resto do pipeline conhece — esse shard traz ~180 contas por instituição
# (Basileia, capital etc. junto), não é só DRE.
CONTAS_DRE_NOVO_ESQUEMA = {
    "140220", "141825", "141830", "141831", "141835", "141836", "141837",
    "141840", "141842", "141847", "141848", "141849", "141850", "141851",
    "141852", "141853", "141854", "141855", "141856", "141857", "141858",
    "141859", "141860", "141862", "141863", "141864", "141865", "141866",
    "141867", "141868", "141869", "141870",
}


# relatórios "Carteira de crédito ativa" do www3 (ids "trel" confirmados
# jul/2026 buscando trelXXX_<id>.json e lendo o campo "n") que correspondem
# aos 4 relatórios já usados via Olinda — mesmo par (prefixo de arquivo,
# número do relatório Olinda) que RELATORIOS_CARTEIRA em carregar_dados.py,
# pra escrever nos MESMOS nomes de arquivo.
TREL_CARTEIRA_WWW3 = {
    123: ("aging_pf", "11"),  # Carteira ativa PF - modalidade e prazo de vencimento
    129: ("cnae", "12"),      # Carteira ativa PJ - por atividade econômica (CNAE)
    128: ("aging_pj", "13"),  # Carteira ativa PJ - modalidade e prazo de vencimento
    127: ("porte", "14"),     # Carteira ativa PJ - por porte do tomador
}
# confirmado empiricamente (jul/2026): pro lote "ifdata_2025_2030", os 4
# relatórios de carteira acima vêm todos juntos no shard "dados_3" (mesma
# entidade "e" de cadastro_1009 — Banco do Brasil - Prudencial tinha
# 395 contas nesse shard, incluindo códigos de aging E de porte já
# validados). Se a BCB reorganizar os shards em algum lote futuro, o aviso
# de contagem zerada no fim da função vai denunciar isso.
SHARD_CARTEIRA_WWW3 = 3


def _resolver_colunas_relatorio(trel: dict, info_por_id: dict) -> dict:
    """A partir da definição de colunas de um relatório (arquivo trel) e do
    catálogo de nomes (arquivo info), monta {conta: (grupo, nome_coluna)}.
    Colunas com subcolunas (`sc`) são relatórios aninhados tipo aging/CNAE
    (grupo=modalidade ou seção CNAE, nome_coluna=faixa de vencimento);
    colunas sem subcoluna são relatórios "flat" tipo porte (grupo=None,
    nome_coluna=a própria categoria, ex. "Micro"). Ids sem `lid` válido
    (colunas de identificação tipo nome/UF, não contas de valor) são
    ignorados. Dinâmico de propósito — os códigos de conta desses
    relatórios somam a centenas (modalidade × faixa de vencimento × PF/PJ +
    CNAE + porte); mapear à mão seria grande demais pra manter/revisar."""
    mapa = {}
    for col in trel.get("c", []):
        sub = col.get("sc")
        if sub:
            grupo_info = info_por_id.get(col["ifd"])
            grupo_nome = grupo_info["n"] if grupo_info else None
            for s in sub:
                info_s = info_por_id.get(s["ifd"])
                if not info_s or info_s.get("lid") in (None, -1):
                    continue
                mapa[str(info_s["lid"])] = (grupo_nome, info_s["n"])
        else:
            info_col = info_por_id.get(col["ifd"])
            if not info_col or info_col.get("lid") in (None, -1):
                continue
            mapa[str(info_col["lid"])] = (None, info_col["n"])
    return mapa


def baixar_ifdata_www3_fallback() -> None:
    """Plano B pro IF.data: quando a API oficial (Olinda) fica fora do ar —
    já aconteceu por vários dias em jul/2026, HTTP 500 em todos os
    endpoints — busca cadastro, DRE e carteira ativa (aging PF/PJ, CNAE,
    porte) no site público do BCB (www3.bcb.gov.br/ifdata), um backend
    TOTALMENTE diferente (arquivos JSON estáticos pré-gerados, não OData)
    mas que usa os MESMOS códigos de conta (validado por reconciliação
    matemática exata contra dados reais do Banco do Brasil e Itaú em
    jul/2026). Só cobre uma janela ROLANTE de ~5 trimestres recentes — não
    serve pra backfill histórico, só pra cobrir o "buraco" mais recente
    quando a Olinda falha. Nunca sobrescreve um dado bom já baixado da
    Olinda: só entra nas competências/relatórios ausentes ou vazios.
    Escreve no MESMO formato/nome de arquivo que baixar_ifdata() produz,
    pra carregar_ifdata() (carregar_dados.py) não precisar de nenhuma
    mudança."""
    pasta = RAW / "ifdata"

    def tem_dado(arq: Path) -> bool:
        if not arq.exists():
            return False
        try:
            with open(arq, encoding="utf-8") as fh:
                return bool(json.load(fh).get("value"))
        except json.JSONDecodeError:
            return False

    try:
        req = Request("https://www3.bcb.gov.br/ifdata/rest/relatorios2025a2030", headers=HEADERS)
        with urlopen(req, timeout=60) as resp:
            competencias = [str(r["dt"]) for r in json.loads(resp.read())]
    except Exception as e:
        print(f"  [aviso] backend alternativo (www3) do IF.data indisponível: {e}")
        return

    arquivos_carteira = [prefixo for prefixo, _ in TREL_CARTEIRA_WWW3.values()]
    pendentes = {
        am: dict(
            dre=not tem_dado(pasta / f"cadastro_{am}.json") or not tem_dado(pasta / f"dre_{am}.json"),
            carteira=any(not tem_dado(pasta / f"{p}_{am}.json") for p in arquivos_carteira),
        )
        for am in competencias
    }
    faltantes = {am: v for am, v in pendentes.items() if v["dre"] or v["carteira"]}
    if not faltantes:
        return
    print(f"\n== BACEN IF.data (plano B, www3.bcb.gov.br): preenchendo "
          f"{len(faltantes)} competência(s) sem dado da Olinda: {', '.join(faltantes)} ==")

    base = "https://www3.bcb.gov.br/ifdata/rest/arquivos?nomeArquivo=ifdata_2025_2030//"
    for am, precisa in faltantes.items():
        try:
            with urlopen(Request(f"{base}{am}/cadastro{am}_1009.json", headers=HEADERS),
                        timeout=120) as resp:
                cadastro = json.loads(resp.read())
        except Exception as e:
            print(f"  [ERRO] {am} (cadastro): {e}")
            continue

        # cadastro_1009 = "Conglomerados Prudenciais e Instituições Independentes"
        # (Resolução 4.553/2017) — nível CONSOLIDADO (1 linha por grupo
        # econômico, ex. "ITAU - PRUDENCIAL" em vez de 2-3 pessoas jurídicas
        # separadas do mesmo banco) — pedido explícito do usuário, jul/2026.
        # Confirmado que o shard "dados_N" tem valores de DRE também pra essas
        # entidades consolidadas (mesmo "e" usado em qualquer nível de
        # consolidação — é um índice global de instituição/grupo). c0=índice
        # global, c2=nome, c12=segmento S1-S5, c10/c11=UF/município, c4=tipo
        # de consolidação (C/I). Sem campo de situação nesse cadastro —
        # assume-se 'A' (só aparecem instituições que reportaram no período).
        cadastro_out = [{
            "CodInst": int(c["c0"]), "Data": am, "NomeInstituicao": c["c2"],
            "Sr": c.get("c12") or None, "Uf": c.get("c10") or None,
            "Municipio": c.get("c11") or None, "Situacao": "A", "Tc": c.get("c4"),
            "CodConglomeradoFinanceiro": None, "CodConglomeradoPrudencial": None,
        } for c in cadastro]
        codinsts_validos = {int(c["c0"]) for c in cadastro}

        if precisa["dre"]:
            (pasta / f"cadastro_{am}.json").write_text(
                json.dumps({"value": cadastro_out}, ensure_ascii=False), encoding="utf-8")
            try:
                with urlopen(Request(f"{base}{am}/dados{am}_1.json", headers=HEADERS),
                            timeout=120) as resp:
                    dados1 = json.loads(resp.read())
                # valores do www3, assim como os da própria Olinda em
                # TipoInstituicao=1, vêm em REAIS CHEIOS (não R$ mil, apesar
                # da documentação) — a normalização (÷1000) é feita uma
                # única vez, de forma centralizada, em carregar_ifdata()
                # (carregar_dados.py), pra não dividir duas vezes.
                dre_out = [
                    {"CodInst": item["e"], "AnoMes": am, "Conta": str(v["i"]), "Saldo": v["v"]}
                    for item in dados1.get("values", [])
                    if item["e"] in codinsts_validos
                    for v in item.get("v", []) if str(v["i"]) in CONTAS_DRE_NOVO_ESQUEMA
                ]
                (pasta / f"dre_{am}.json").write_text(
                    json.dumps({"value": dre_out}, ensure_ascii=False), encoding="utf-8")
                print(f"  {am}: {len(cadastro_out)} instituições (consolidado), "
                      f"{len(dre_out)} valores DRE (www3)")
            except Exception as e:
                print(f"  [ERRO] {am} (DRE): {e}")

        if precisa["carteira"]:
            try:
                with urlopen(Request(f"{base}{am}/info{am}.json", headers=HEADERS), timeout=120) as resp:
                    info_por_id = {r["id"]: r for r in json.loads(resp.read())}
                mapa_por_relatorio = {}
                for trel_id, (prefixo, rel_num) in TREL_CARTEIRA_WWW3.items():
                    with urlopen(Request(f"{base}{am}/trel{am}_{trel_id}.json", headers=HEADERS),
                                timeout=120) as resp:
                        trel = json.loads(resp.read())
                    mapa_por_relatorio[prefixo] = (rel_num, _resolver_colunas_relatorio(trel, info_por_id))
                with urlopen(Request(f"{base}{am}/dados{am}_{SHARD_CARTEIRA_WWW3}.json", headers=HEADERS),
                            timeout=120) as resp:
                    dados_carteira = json.loads(resp.read())
            except Exception as e:
                print(f"  [ERRO] {am} (carteira): {e}")
                continue

            for prefixo, (rel_num, mapa_conta) in mapa_por_relatorio.items():
                # (mesma nota de escala do DRE acima: Saldo cru, sem dividir
                # por 1000 aqui — a normalização é centralizada em
                # carregar_ifdata())
                linhas = [
                    {"CodInst": item["e"], "AnoMes": am, "NumeroRelatorio": rel_num,
                     "Grupo": mapa_conta[str(v["i"])][0], "Conta": str(v["i"]),
                     "NomeColuna": mapa_conta[str(v["i"])][1], "Saldo": v["v"]}
                    for item in dados_carteira.get("values", [])
                    if item["e"] in codinsts_validos
                    for v in item.get("v", []) if str(v["i"]) in mapa_conta
                ]
                (pasta / f"{prefixo}_{am}.json").write_text(
                    json.dumps({"value": linhas}, ensure_ascii=False), encoding="utf-8")
                if not linhas:
                    print(f"  [aviso] {am}/{prefixo}: 0 linhas — conferir se a BCB reorganizou "
                          f"o shard {SHARD_CARTEIRA_WWW3} ou os ids de relatório (www3)")
            print(f"  {am}: carteira ativa (aging PF/PJ, CNAE, porte) preenchida (www3)")


# financiamento imobiliário (tipoModalidade='M' na API taxaJuros) só existe
# na entidade mensal, que não tem campo de segmento — confirmado via
# ParametrosConsulta que essas 6 modalidades são TODAS pessoa física
# (não existe variante PJ), então dá pra marcar o segmento manualmente
MODALIDADES_IMOBILIARIO_PF = [
    "Financiamento imobiliário com taxas de mercado - Prefixado",
    "Financiamento imobiliário com taxas de mercado - Pós-fixado referenciado em TR",
    "Financiamento imobiliário com taxas de mercado - Pós-fixado referenciado em IPCA",
    "Financiamento imobiliário com taxas reguladas - Prefixado",
    "Financiamento imobiliário com taxas reguladas - Pós-fixado referenciado em TR",
    "Financiamento imobiliário com taxas reguladas - Pós-fixado referenciado em IPCA",
]


def semanas_por_mes(desde: str, defasagem_meses: int) -> list[str]:
    """Uma competência (InicioPeriodo, formato AAAA-MM-DD) por mês, usando o
    ConsultaDatas da API taxaJuros pra achar a semana mais próxima do
    início de cada mês — a API só publica em janelas semanais (~5 dias
    úteis), não existe uma data "mensal" fixa."""
    datas_url = ("https://olinda.bcb.gov.br/olinda/servico/taxaJuros/versao/v2/odata/"
                 "ConsultaDatas?$format=json&$filter=tipoModalidade%20eq%20%27D%27")
    req = Request(datas_url, headers=HEADERS)
    with urlopen(req, timeout=60) as resp:
        dados = json.loads(resp.read())
    semanas = sorted(r["inicioPeriodo"] for r in dados["value"])
    meses_alvo = meses_ate(desde, defasagem_meses)
    escolhidas = []
    for am in meses_alvo:
        alvo = f"{am[:4]}-{am[4:]}-01"
        candidatas = [s for s in semanas if s[:7] == alvo[:7]]
        if candidatas:
            escolhidas.append(min(candidatas))
    return escolhidas


def baixar_taxa_juros_instituicao(desde: str, force: bool = False) -> None:
    """Taxa de juros por instituição financeira, segmento (PF/PJ) e
    modalidade — serviço BCB "taxaJuros" (separado do IF.data), única fonte
    aberta do BCB com esse corte por instituição confiável. Publica em
    janelas semanais desde 2012; usa-se 1 semana por mês (a mais próxima do
    início do mês) como proxy da série mensal do resto do sistema."""
    print("\n== BACEN: taxa de juros por instituição, PF/PJ e modalidade ==")
    base = "https://olinda.bcb.gov.br/olinda/servico/taxaJuros/versao/v2/odata"
    semanas = semanas_por_mes(desde, defasagem_meses=1)

    def tarefa(semana: str):
        destino = RAW / "taxa_juros" / f"{semana}.json"
        return baixar(
            f"{base}/TaxasJurosDiariaPorInicioPeriodo?$filter=InicioPeriodo%20eq%20"
            f"%27{semana}%27&$format=json",
            destino, force=force)

    ok = erro = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futuros = [pool.submit(tarefa, s) for s in semanas]
        for fut in as_completed(futuros):
            if fut.result() is not None:
                ok += 1
            else:
                erro += 1
    print(f"  {ok} semanas baixadas, {erro} falharam (ver [ERRO] acima)")

    # financiamento imobiliário só existe na entidade "mensal" da mesma API
    # (tipoModalidade='M'), que não tem campo de segmento — mas as 6
    # modalidades desse tipo são TODAS pessoa física (confirmado consultando
    # ParametrosConsulta: nenhuma variante PJ existe), então dá pra marcar
    # o segmento manualmente sem risco de ambiguidade. 1 chamada por
    # modalidade já traz a série mensal completa desde 2012 (não precisa
    # amostrar semana a semana como a família "diária").
    for i, mod in enumerate(MODALIDADES_IMOBILIARIO_PF):
        # nome de arquivo por índice, não por texto truncado da modalidade —
        # um truncamento ingênuo (ex. mod[:40]) colide entre as 3 variantes
        # "Prefixado"/"TR"/"IPCA" de cada grupo (mercado/reguladas), já que
        # elas só diferem depois do caractere 40; bug real já cometido aqui.
        destino = RAW / "taxa_juros" / f"imobiliario_{i}.json"
        baixar(f"{base}/TaxasJurosMensalPorMes?$filter=Modalidade%20eq%20"
               f"%27{quote(mod)}%27&$format=json", destino, force=force)


# API pública DataJud (CNJ) — chave documentada publicamente em
# https://datajud-wiki.cnj.jus.br/api-publica/acesso/ (o CNJ pode trocá-la a
# qualquer momento; se os downloads começarem a falhar com 401/403, checar
# essa página primeiro).
DATAJUD_API_KEY = "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw=="

# um índice Elasticsearch por tribunal estadual (Recuperação Judicial e
# Falência são competência da Justiça Estadual, não federal/trabalhista)
DATAJUD_TRIBUNAIS = {
    "AC": "tjac", "AL": "tjal", "AP": "tjap", "AM": "tjam", "BA": "tjba",
    "CE": "tjce", "DF": "tjdft", "ES": "tjes", "GO": "tjgo", "MA": "tjma",
    "MT": "tjmt", "MS": "tjms", "MG": "tjmg", "PA": "tjpa", "PB": "tjpb",
    "PR": "tjpr", "PE": "tjpe", "PI": "tjpi", "RJ": "tjrj", "RN": "tjrn",
    "RS": "tjrs", "RO": "tjro", "RR": "tjrr", "SC": "tjsc", "SP": "tjsp",
    "SE": "tjse", "TO": "tjto",
}
# classe processual (Tabela Processual Unificada do CNJ, nacional —
# confirmado idêntico em TJSP e TJMG via consulta real à API)
DATAJUD_CLASSES = {128: "RECUPERACAO_EXTRAJUDICIAL", 129: "RECUPERACAO_JUDICIAL",
                    108: "FALENCIA"}


def baixar_datajud_rj_falencia(desde: str, force: bool = False) -> None:
    """Recuperação Judicial, Extrajudicial e Falência por UF — API pública
    DataJud (CNJ), um índice Elasticsearch por tribunal estadual. Substitui
    o scraping de releases da Serasa (só dava o total nacional, sem UF):
    aqui a contagem vem direto do cadastro de processos judiciais, com
    quebra por tribunal/UF, filtrando pela classe processual (campo
    classe.codigo). Uma única consulta por tribunal cobre TODO o histórico
    desde `desde` (agregação Elasticsearch "range" por mês + sub-agregação
    "terms" por classe) — não precisa de 1 chamada por mês."""
    print("\n== CNJ DataJud: Recuperação Judicial, Extrajudicial e Falência por UF ==")
    meses = meses_ate(desde, defasagem_meses=1)  # mês corrente pode estar incompleto
    ranges = []
    for am in meses:
        ano, mes = int(am[:4]), int(am[4:])
        fim_ano, fim_mes = (ano, mes + 1) if mes < 12 else (ano + 1, 1)
        ranges.append({
            "key": am,
            "from": f"{ano}{mes:02d}01000000",
            "to": f"{fim_ano}{fim_mes:02d}01000000",
        })
    payload = json.dumps({
        "size": 0,
        "query": {"terms": {"classe.codigo": list(DATAJUD_CLASSES)}},
        "aggs": {"por_mes": {
            "range": {"field": "dataAjuizamento", "ranges": ranges},
            "aggs": {"por_classe": {"terms": {"field": "classe.codigo"}}},
        }},
    }).encode("utf-8")

    def tarefa(uf: str, sigla: str):
        destino = RAW / "datajud_rj_falencia" / f"{uf}.json"
        if destino.exists() and destino.stat().st_size > 0 and not force:
            print(f"  [mantido ] {uf}")
            return destino
        print(f"  [baixando] {uf} ({sigla})")
        req = Request(
            f"https://api-publica.datajud.cnj.jus.br/api_publica_{sigla}/_search",
            data=payload, method="POST",
            headers={**HEADERS, "Content-Type": "application/json; charset=utf-8",
                      "Authorization": f"APIKey {DATAJUD_API_KEY}"})
        try:
            with urlopen(req, timeout=120) as resp:
                corpo = resp.read()
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(corpo)
            return destino
        except Exception as exc:
            print(f"  [ERRO    ] {uf}: {exc}")
            return None

    ok = erro = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futuros = {pool.submit(tarefa, uf, sigla): uf
                   for uf, sigla in DATAJUD_TRIBUNAIS.items()}
        for fut in as_completed(futuros):
            if fut.result() is not None:
                ok += 1
            else:
                erro += 1
    print(f"  {ok} tribunais baixados, {erro} falharam (ver [ERRO] acima)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--desde", default="202001",
                    help="primeira competência mensal a baixar (AAAAMM, padrão 202001)")
    ap.add_argument("--force", action="store_true",
                    help="rebaixa arquivos mesmo que já existam")
    args = ap.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    baixar_ibge(args.force)
    baixar_malha_uf(args.force)
    baixar_sgs(args.desde)
    baixar_credito_detalhado(args.desde)
    baixar_ifdata(args.desde, args.force)
    baixar_ifdata_www3_fallback()
    baixar_taxa_juros_instituicao(args.desde, args.force)
    baixar_datajud_rj_falencia(args.desde, args.force)
    baixar_estban(args.desde, args.force)
    baixar_cempre(args.force)
    baixar_ipea(args.force)
    baixar_caged(args.desde)
    print("\nDownload concluído.")


if __name__ == "__main__":
    main()
