# Sistema Atividade Econômica — mapeamento por setor, município, crédito, PIB, empresas e emprego

Banco SQLite alimentado por fontes oficiais (BACEN, IBGE, MTE), com camada
analítica de views e relatórios Excel prontos — no mesmo modelo do Sistema
FIDC/CVM: download incremental → banco recarregado do zero → views
materializadas → relatórios.

## Atualização (mensal)

Dê dois cliques em **`atualizar.bat`** (ou rode no terminal). Ele:

1. Descobre séries novas de crédito detalhado no catálogo do BCB (regera
   `data/manual/credito_detalhado_series.csv` — tolerante a falha, mantém a
   semente anterior se o catálogo estiver fora do ar);
2. Baixa apenas o que há de novo em cada fonte (`data/raw/`);
3. Recarrega o banco `data/db/atividade.db` do zero (sempre consistente);
4. Regera os relatórios Excel em `reports/`;
5. Regera o dashboard de crédito (`reports/dashboard.html`);
6. Regera o mapa regional (`reports/mapa.html`);
7. Regera o PIB por setor (`reports/pib_setorial.html`);
8. Regera o dashboard de Recuperação Judicial (`reports/recuperacao_judicial.html`);
9. Regera o dashboard de Instituições Financeiras (`reports/instituicoes_financeiras.html`);
10. Regera o portal único (`reports/index.html`), que reúne as 5 seções acima
    com menu interno + uma Visão Geral consolidada.

Opções: `atualizar.bat --desde AAAAMM` (padrão `202001`) e `--force`
(rebaixa arquivos existentes — use quando o IBGE publicar **ano novo** de
PIB municipal, população ou CEMPRE, pois os arquivos anuais são mantidos
em cache).

**Banco e dados brutos fora do OneDrive**: o banco e a pasta `raw/` (vários
GB de ESTBAN e CAGED) ficam em `C:\SistemaAtividade-dados` — não dentro do
projeto — no mesmo padrão do Sistema FIDC/CVM (`caminhos.py` + variável de
ambiente `ATIV_DADOS_DIR`). Sincronizar um SQLite em uso pode corrompê-lo, e
não há motivo para subir dados brutos de fontes públicas para a nuvem. Só
`data/manual/` (as sementes, pequenas e editáveis) fica no projeto. Para
usar outro caminho, defina `ATIV_DADOS_DIR` antes de rodar o `.bat`.

**Primeira carga**: o Novo CAGED baixa ~76 meses de microdados (~4–5 GB
via FTP) e os agrega — pode levar algumas horas. Depois disso, cada
atualização processa só o mês novo (agregados ficam em cache em
`data/raw/caged/agregado/`).

Requisitos: Python 3.12 (`%LOCALAPPDATA%\Programs\Python\Python312`) com
`pandas`, `openpyxl` e `py7zr`.

## Fontes

| Fonte | Conteúdo | Frequência / defasagem |
|---|---|---|
| BACEN SGS (`api.bcb.gov.br`) | Crédito por atividade econômica (38 séries), carteira total/PJ/PF, livres/direcionados, crédito/PIB, inadimplência, concessões, juros, IBC-Br e IBCR regionais — semente `data/manual/sgs_series.csv` | Mensal (~1 mês) |
| BACEN ESTBAN | Balancete bancário por município (verbetes: crédito 160-171, provisão, poupança, prazo, ativo, PL) — semente `estban_verbetes.csv` | Mensal (~60-90 dias) |
| IBGE SIDRA t/5938 | PIB municipal: PIB, impostos, VAB por setor (agro/indústria/serviços/adm. pública) | Anual (~2 anos), desde 2010 |
| IBGE SIDRA t/1620 + t/5932 | PIB trimestral nacional por setor (índice de volume e taxas) | Trimestral |
| IBGE SIDRA t/9418 + t/9509 (CEMPRE) | Empresas, unidades locais, pessoal e salários por seção CNAE × município | Anual, 2022+ |
| IBGE t/6579 + localidades | População municipal estimada; dimensão município (código IBGE, UF, região, coordenadas) | Anual |
| Novo CAGED (FTP MTE) | Admissões, desligamentos, saldo e salário de admissão por município × divisão CNAE (consolidação MOV + FOR − EXC) | Mensal (~1 mês) |
| IPEADATA (`ipeadata.gov.br/api/odata4`) | População municipal desde 1992 e CAGED nacional 1999+ (antigo + novo) — semente `ipea_series.csv` | Anual / mensal |
| BACEN SGS — crédito detalhado (`dadosabertos.bcb.gov.br` + `api.bcb.gov.br`) | Taxa de juros e inadimplência por modalidade de crédito, saldo e concessões (novas operações) por modalidade — PF/PJ em geral e por porte de PJ (micro/pequeno/MPMe/grande)/MEI —, ICC + spread, prazo médio (carteira e concessões), saldo por grande região (Norte/Nordeste/Centro-Oeste/Sudeste/Sul) — ~1085 séries descobertas automaticamente (`descobrir_credito_detalhado.py`, semente `credito_detalhado_series.csv`) | Mensal / trimestral (mista) |
| IBGE malhas territoriais (`servicodados.ibge.gov.br/api/v3/malhas`) | Contorno geográfico (GeoJSON) de cada UF, usado para desenhar o mapa regional com fronteiras reais | Estático (não muda mês a mês) |
| BACEN IF.data (`olinda.bcb.gov.br/olinda/servico/IFDATA`) | Cadastro de instituições financeiras (segmento prudencial S1-S5, UF, situação), Demonstração de Resultado (DRE) por instituição individual — receita de juros de operações de crédito, perda esperada de crédito, lucro líquido, ativo total —, e carteira de crédito ativa por conglomerado prudencial: aging (a vencer em 6 faixas + vencido a partir de 15 dias) por modalidade PF/PJ, por CNAE (PJ) e por porte do tomador (PJ) | Trimestral |
| BACEN taxaJuros (`olinda.bcb.gov.br/olinda/servico/taxaJuros`) | Taxa de juros por instituição financeira, segmento (PF/PJ) e modalidade — única fonte aberta do BCB com esse corte por instituição; financiamento imobiliário (só existe na versão mensal da API) marcado como Pessoa Física (única variante publicada) | Semanal (amostrado 1×/mês) / mensal (imobiliário) |
| CNJ DataJud (`api-publica.datajud.cnj.jus.br`, um índice Elasticsearch por tribunal) | Contagem de processos de Recuperação Judicial, Recuperação Extrajudicial e Falência por tribunal estadual (UF), via classe processual (Tabela Processual Unificada, códigos 129/128/108) — fonte judicial primária, chave pública documentada, sem scraping | Mensal |

## Estrutura do banco (`data/db/atividade.db`)

**Dimensões**: `municipio` (código IBGE 7 díg., UF, região, lat/lng),
`municipio_populacao` (+ `_ref`, série sem buracos para per capita),
`cnae_divisao`, `sgs_serie`, `estban_verbete`, `setor_mapa` (chave de
cruzamento SGS ↔ seções CNAE ↔ VAB — editável em `data/manual/`).

**Tabelas-base**: `sgs_valor`, `pib_municipio`, `pib_trimestral`,
`estban_municipio` (agregado por município; R$ mil), `estban_presenca`
(instituições e agências), `caged_mensal` (microdados agregados; os
individuais não persistem), `cempre_municipio` (+ `_total`), `ipea_populacao_municipio`
(estende a série de população para trás, até 1992),
`emprego_nacional_hist` (CAGED nacional 1999+), `credito_detalhado_serie`
(dimensão: métrica, cliente, porte, origem, regiao, modalidade, periodicidade,
gerada pelo descobridor) + `credito_detalhado_valor`, `malha_uf` (uf, geojson
— contorno geográfico de cada estado, usado só pelo mapa regional),
`ifdata_instituicao` (cadastro trimestral: nome, segmento S1-S5, UF, situação)
+ `ifdata_dre_valor` (DRE por instituição, formato longo por conta COSIF) +
`ifdata_carteira_valor` (aging/CNAE/porte, formato longo, nível de
conglomerado prudencial), `taxa_juros_instituicao` (taxa por instituição,
segmento e modalidade), `datajud_rj_falencia_mensal` (uf, tribunal,
competência AAAAMM, classe — RECUPERACAO_JUDICIAL/RECUPERACAO_EXTRAJUDICIAL/
FALENCIA —, processos; fonte CNJ DataJud).

**Views analíticas** (as pesadas são materializadas em `m_*` ao final da
carga — consultas em milissegundos):

| View | Responde |
|---|---|
| `v_pulso_nacional` | Painel mensal Brasil: IBC-Br, crédito, concessões, inadimplência, crédito/PIB — var. M/M e 12M |
| `v_pib_trimestral` | PIB trimestral por setor: índice de volume e taxas T/T-1, T/T-4, acumuladas |
| `v_credito_macro` | Carteira total/PJ/PF, livres/direcionados, inadimplência, concessões, juros por mês |
| `v_credito_setor` | Crédito por setor de atividade: série, var. 12M, participação % |
| `v_atividade_regional` | IBC-Br e IBCR das 5 regiões |
| `v_credito_municipio` | Carteira bancária por município × mês (ESTBAN): crédito e aberturas, captação, presença, var. 12M |
| `v_credito_municipio_pc` | Última posição municipal: per capita e rankings Brasil/UF |
| `v_penetracao_credito` | Crédito bancário (média 12M) ÷ PIB municipal |
| `v_pib_municipio` | PIB, per capita, composição do VAB, participação na UF, rankings |
| `v_porte_municipio` | Classificação por faixa de PIB e de população |
| `v_emprego_setor` | CAGED por divisão/seção CNAE: saldo mensal e 12M, salário de admissão |
| `v_emprego_municipio` | CAGED por município: saldo, 12M, saldo/1.000 hab |
| `v_empresas_municipio` | CEMPRE por município × seção: empresas, pessoal, densidade |
| `v_setor_consolidado` | Cruzamento setorial: crédito × VAB × emprego 12M × empresas |
| `v_atividade_uf` | Painel por UF: PIB, crédito, emprego, empresas, per capita |
| `v_ranking_municipios` | Ranking municipal multi-métrica (uma linha por município) |
| `v_emprego_historico` | Série nacional mensal 1999+ de admissões/demissões/saldo (CAGED antigo + novo) |
| `v_credito_detalhado` | Base do crédito detalhado (todas as métricas/cortes), com var. ano (self-join, funciona para mensal e trimestral) |
| `v_credito_juros_modalidade` | Taxa de juros por modalidade — PJ por porte e MEI |
| `v_credito_inadimplencia_modalidade` | Taxa de inadimplência por modalidade — PJ por porte e MEI |
| `v_credito_saldo_porte` | Saldo por porte de PJ (micro/pequeno/MPMe/grande) e MEI, participação % dentro do porte |
| `v_credito_icc` | Indicador de Custo do Crédito (ICC) e Spread do ICC por origem/cliente/modalidade |
| `v_credito_prazo_medio` | Prazo médio da carteira e das concessões por origem/cliente/modalidade |
| `v_pib_uf_historico` | PIB e composição do VAB por UF × ano, participação no Brasil |
| `v_atividade_cnae` | Painel nacional por seção CNAE: empresas, pessoal, saldo/admissões CAGED 12M |
| `v_atividade_cnae_uf` | Empresas por seção CNAE × UF (último ano CEMPRE) |
| `v_credito_uf` | Mesma rubrica ESTBAN de `v_credito_municipio`, agregada por UF × mês (série completa) |
| `v_emprego_uf_historico` | CAGED por UF × mês: saldo e acumulado 12M (série completa) |
| `v_credito_saldo_regiao` | Saldo de crédito por grande região (Norte/Nordeste/Centro-Oeste/Sudeste/Sul) e porte PJ/MEI — única quebra geográfica abaixo do nacional que o BACEN publica para esse corte |
| `v_credito_concessao_modalidade` | Concessões de crédito (novas operações no mês) por modalidade — PF/PJ em geral, livres/direcionados |
| `v_ifdata_dre` | DRE por instituição financeira: segmento S1-S5, ativo total, e a árvore completa do DRE (COSIF 2025) — rendas por origem (interfinanceiras, TVM, crédito, tarifas etc.), despesas por natureza (captação, pessoal, perda esperada etc.), resultado de intermediação financeira, resultado antes de tributação e lucro líquido. `gerar_instituicoes_financeiras.py` deriva "Receita total"/"Despesa total" reconciliados (Receita − Despesa = Lucro líquido, exatamente) a partir dessas contas |
| `v_ifdata_aging` | Carteira de crédito ativa PF/PJ por modalidade × faixa de vencimento (aging), por conglomerado prudencial |
| `v_ifdata_cnae` | Carteira de crédito ativa PJ por atividade econômica (CNAE) × faixa de vencimento, por conglomerado prudencial |
| `v_ifdata_porte` | Carteira de crédito ativa PJ por porte do tomador, por conglomerado prudencial |
| `v_recuperacao_falencia_uf` | Processos de Recuperação Judicial, Extrajudicial e Falência por UF × mês |
| `v_recuperacao_falencia_nacional` | Mesmo dado, agregado Brasil × mês |

Para consultar: qualquer cliente SQLite (DBeaver, DB Browser, Power BI,
Python/pandas).

## Relatórios (`reports/`)

1. `01_visao_brasil.xlsx` — pulso mensal, crédito macro, atividade regional, PIB trimestral
2. `02_credito_setor.xlsx` — última posição, séries por setor/subsetor, participação
3. `03_credito_municipal.xlsx` — top 300, per capita, série por UF, penetração crédito/PIB
4. `04_pib_municipal.xlsx` — ranking PIB, per capita, porte, PIB por UF
5. `05_empresas.xlsx` — seções CNAE Brasil/UF, top municípios, densidade empresarial
6. `06_emprego.xlsx` — saldo 12M por divisão, série por seção, UF, top municípios, série nacional 1999+
7. `07_fichas_uf.xlsx` — painel por UF + séries de crédito e emprego
8. `08_ranking_municipios.xlsx` — ranking multi-métrica, por porte, setor consolidado
9. `09_credito_detalhado.xlsx` — juros e inadimplência por modalidade, saldo por porte e MEI, concessões por modalidade, ICC/spread, prazo médio, auditoria de séries não parseadas
10. `10_ifdata_instituicoes.xlsx` — segmentação prudencial (S1-S5), ranking de instituições por lucro líquido e por receita de crédito, cadastro completo, carteira PJ das 30 maiores instituições por modalidade/vencidos/CNAE/porte, taxa de juros por instituição

Dashboards HTML autocontidos (dados embutidos em JSON, abrem direto no navegador, sem servidor):

- **`index.html`** — **portal único, ponto de entrada principal do sistema**: uma página só com menu interno (Visão Geral | Crédito | Mapa Regional | PIB por Setor | Recuperação Judicial | Instituições Financeiras) trocando qual seção fica visível, sem recarregar. A **Visão Geral** traz KPIs consolidados (PIB Brasil, crédito total, crédito/PIB, população, empresas, emprego 12M, Recuperação Judicial), cards de atalho para cada seção e destaques automáticos (setor de maior alta, UF com maior crédito/PIB, município com maior PIB per capita, UF líder em RJ) — tudo calculado no cliente a partir dos dados já embutidos nas seções, sem consulta adicional ao banco (exceto o saldo de emprego nacional). Gerado por `gerar_sistema.py`, que **compõe** (não duplica) os 5 templates abaixo: extrai style/body/script de cada um, prefixa IDs para não colidir entre seções e faz lazy-init (só monta o DOM de uma seção na primeira vez que o usuário a visita). Os 5 templates-fonte e seus geradores continuam existindo e gerando seus arquivos avulsos normalmente, para quem preferir abrir um só.
  **Carga diferida do Mapa Regional** (jul/2026): o payload do mapa (~9 MB — histórico de 5571 municípios) só é injetado no DOM e parseado pelo motor JS na primeira vez que o usuário abre essa seção — antes disso rodava sempre, incondicionalmente, no carregamento inicial da página (medido: ~112 ms de `JSON.parse` bloqueando a Visão Geral mesmo quando o usuário nunca chegava a abrir o mapa). A Visão Geral usa um **resumo pequeno** (nome/UF/PIB per capita por município + os dados por UF, já pequenos) embutido nela mesma (`resumo_mapa_para_geral()` em `gerar_sistema.py`), não o payload completo — por isso os destaques de UF/município continuam funcionando sem precisar do mapa carregado. Os outros 4 domínios (Crédito, PIB, RJ, IFs) somam menos de 500 KB juntos — não precisaram do mesmo tratamento.
- `dashboard.html` — Visão Crédito: saldo consolidado + taxa média mensal (janelas de 1 a 60 meses), detalhamento por modalidade/porte/MEI/prazo/**concessões mensais** (novas operações no mês, por modalidade)
- `mapa.html` — Mapa Regional: contorno real das 27 UFs (malha territorial do IBGE), hierarquia Brasil → UF → município (hover destaca o estado, clique entra e mostra as cidades — clique direto na cidade também funciona), capital marcada só pela estrela (clicável, sem bolha própria), busca por município com autocomplete, detalhamento com até 10 anos de histórico em 7 métricas (PIB, crédito, crédito/PIB, emprego, empresas/hab, PIB per capita, população) + Recuperação Judicial/Extrajudicial/Falência em gráficos grandes e interativos (cursor mostra o valor exato) + rubrica de crédito ESTBAN, comparação de até 5 municípios (manual) ou automática entre as capitais de uma região inteira, ranking por UF (inclusive por RJ/Extrajudicial/Falência) e por seção CNAE com posição numerada
- `pib_setorial.html` — PIB por Setor: série trimestral nacional (IBGE, 1996-hoje) do PIB a preços de mercado — índice de volume e as 4 taxas de variação (T/T-1, interanual, acum. 4 tri, acum. no ano), ranking por atividade (ótica da oferta/VAB) e por componente da demanda com posição numerada e cor por sinal, comparação de até 5 setores, detalhamento com as 5 métricas históricas por setor e marcador de dado mais recente
- `recuperacao_judicial.html` — Recuperação Judicial, Extrajudicial e Falência (CNJ DataJud): KPIs nacionais com variação M/M e 12M, gráfico histórico mensal desde 2020 (nível ou variação %, por categoria, com janela de tempo ajustável), ranking por UF com detalhamento (histórico das 3 categorias por estado + participação % no total nacional) — mesma fonte e dados usados no painel de UF do Mapa Regional, aqui com foco dedicado ao tema
- `instituicoes_financeiras.html` — Instituições Financeiras (BACEN IF.data): segmentação prudencial (S1-S5); ranking e histórico de **resultados (DRE)** por instituição individual (ativo total, receita de juros de crédito, receita/perda esperada de operações de crédito, lucro líquido) com comparação entre até 5 instituições; ranking de **carteira de crédito ativa** por conglomerado prudencial (top 30 por carteira PJ) com detalhamento por modalidade, vencidos (>15 dias), CNAE e porte; ranking de **taxa de juros por instituição**, por modalidade. Os dois primeiros blocos usam identificadores de consolidação diferentes (DRE = instituição individual, carteira = conglomerado prudencial) — nunca comparados linha a linha, ver nota na própria página

## Convenções e limitações

- **Unidades**: valores municipais (ESTBAN, PIB, CEMPRE salários) em
  **R$ mil**; séries SGS de saldo/concessão em **R$ milhões**; per capita
  em R$ por habitante.
- **ESTBAN** cobre bancos comerciais/múltiplos com carteira comercial —
  municípios sem agência bancária não aparecem (≈2.900 municípios têm
  dado); cooperativas de crédito e fintechs ficam fora. Use como proxy de
  presença bancária, não como total do crédito no município.
- **Crédito por atividade (SGS)** é a carteira PJ do SCR; o crédito da
  administração pública está dentro de SERVIÇOS no consolidado setorial.
- **PIB municipal** tem defasagem de ~2 anos; per capita usa a população
  do ano mais próximo disponível (estimativas do IBGE não cobrem anos de
  censo).
- **CAGED**: consolidação oficial MOV + FOR − EXC pela competência de
  movimentação; ajustes retroativos entram a cada recarga (o banco é
  refeito do zero). Salário médio refere-se a admissões.
- **CEMPRE** (série vigente) existe a partir de 2022.
- **Crédito detalhado**: séries descobertas automaticamente a cada atualização
  (não hardcoded) — o título de cada série é decomposto em métrica/cliente/
  porte/origem/modalidade por reconhecimento de padrões conhecidos, não por
  split posicional (evita quebrar em nomes de produto com hífen embutido,
  ex. "ARO - adiantamento de receitas orçamentárias"). Títulos fora do
  padrão esperado ficam com `parse_status != 'OK'` e aparecem na aba de
  auditoria do relatório 09 — nunca entram com dado errado. Combinações de
  nicho (ex. MEI + modalidade pouco usada) têm histórico curtíssimo; o
  download tenta a série completa sem filtro de data quando a janela
  configurada (`--desde`) não retorna nada. Não existe quebra por
  modalidade para porte médio/grande — só o par agregado (saldo e
  inadimplência totais por porte MPMe/Grande). Também **não existe por
  UF** — a única quebra geográfica abaixo do nacional é por grande região
  (Norte/Nordeste/Centro-Oeste/Sudeste/Sul), e só para saldo por porte
  PJ/MEI (sem taxa). O mapa regional usa isso como contexto comparativo
  no detalhamento de UF, ao lado da rubrica ESTBAN (que é por UF de verdade,
  mas sem quebra por modalidade/taxa).
- **Saldo/concessão por modalidade** (família clássica "recursos livres/
  direcionados por produto"): tem estrutura pai-filho (ex. "Cartão de
  crédito total" = rotativo + parcelado + à vista) e também um corte
  PARALELO que reparte a mesma carteira de novo por outro critério
  ("Rotativo"/"Não rotativo" somam 100% do total do grupo). Somar
  modalidades sem excluir filhos e o corte paralelo conta a mesma carteira
  duas ou três vezes — ver `CARTEIRA_FILHOS_OU_PARALELOS` em
  `gerar_dashboard.py` (lista revisada e validada por reconciliação manual,
  usada no gráfico "5 maiores carteiras"). O relatório 09 lista todas as
  modalidades cruas, sem esse filtro — é uma tabela de detalhe, não um
  ranking somado.
- **IF.data — DRE e carteira agora no MESMO nível de consolidação**
  (jul/2026): até então o DRE (relatório 4) só era buscado por
  **instituição individual** (TipoInstituicao=2), diferente da carteira
  ativa (aging, CNAE, porte — relatórios 11-14, sempre TipoInstituicao=1,
  conglomerado prudencial) — impedindo cruzar os dois pelo mesmo
  `codinst`. Confirmado que a própria Olinda aceita TipoInstituicao=1 pro
  relatório 4 também — `baixar_ifdata()` foi trocado pra usar
  TipoInstituicao=1 em TUDO (pedido do usuário: "só é pra existir 1 Itaú
  por exemplo", 1 linha por grupo econômico, ex. "ITAU - PRUDENCIAL", não
  2-3 pessoas jurídicas separadas do mesmo banco). Isso também elimina a
  limitação antiga: DRE e carteira agora compartilham `codinst`, dá pra
  cruzar os dois. O aging tem só 1 faixa de vencido ("a partir de 15
  dias") — o BCB não abre mais fino que isso (15-30/30-60/60-90/&gt;90 dias
  não existem nessa fonte).
- **Bug real da própria Olinda, achado validando o pipeline completo
  (jul/2026)**: em TipoInstituicao=1 (conglomerado prudencial), o campo
  `Saldo` do IF.data vem em **REAIS CHEIOS**, não em "R$ mil" como a
  própria documentação da Olinda afirma (e como TipoInstituicao=2 sempre
  se comportou) — confirmado comparando com números públicos reais (Ativo
  Total do Itaú só bate em ~R$2,83 tri, e não ~R$2,83 QUATRILHÕES, se o
  valor bruto for tratado como reais desde o início; uma cooperativa
  pequena apareceria com ~R$698 bilhões de ativo, quando na realidade são
  ~R$698 milhões). Afeta TANTO o DRE quanto a carteira (aging/CNAE/porte)
  — ambos usam TipoInstituicao=1. Corrigido de forma centralizada em
  `carregar_ifdata()` (`carregar_dados.py`): divide `Saldo` por 1000 uma
  única vez, na carga, pra normalizar pra R$ mil (convenção que todo o
  resto do pipeline — views, `fmtReaisMil` etc. — sempre assumiu). Esse
  bug ficou anos sem ser notado porque a validação anterior conferia só a
  **ordem** do ranking (os maiores bancos certos, na posição certa), não
  a magnitude absoluta — um erro de escala uniforme não muda a ordem.
- **Receita Total/Despesa Total só existem a partir de 202503**: a árvore
  completa de contas que sustenta esse cálculo (ver item abaixo) só existe
  no esquema novo (COSIF 2025) — trimestres anteriores (78xxx) só têm
  receita de crédito e lucro líquido mapeados, sem os demais componentes.
  `calcular_receita_despesa_total()` (`gerar_instituicoes_financeiras.py`)
  detecta isso (checando se `rendas_interfinanceiras`, exclusiva do
  esquema novo, está presente) e retorna `None` pros trimestres antigos,
  em vez de calcular um total incompleto que nunca reconciliaria com o
  Lucro Líquido (bug real: ao validar com o histórico completo — antes só
  tinha sido testado com o trimestre mais recente — 746 de 945 linhas
  "divergiam" em até dezenas de bilhões, todas em trimestres pré-2025).
- **Taxa de juros por instituição**: não existe corte por CNAE nem por
  porte do tomador em nenhuma API aberta do BCB — só por instituição,
  segmento (PF/PJ) e modalidade. Financiamento imobiliário só é publicado
  na versão mensal da API (sem campo de segmento), mas como as 6
  modalidades desse tipo são exclusivamente PF, foi marcado manualmente —
  para essas linhas, a "semana" na tabela `taxa_juros_instituicao` na
  verdade representa o mês inteiro (dia 1), não uma janela semanal como o
  resto da tabela.
- **IF.data**: o BCB trocou toda a numeração de contas do relatório DRE a
  partir de 202503 (esquema antigo: contas na faixa 78xxx, ~30 contas;
  esquema novo: contas 140xxx-142xxx, ~52 contas) — `v_ifdata_dre` mapeia
  os dois esquemas onde há equivalente direto (lucro líquido, receita/perda
  de operações de crédito), mas a receita de juros isolada (sem câmbio/
  ajustes) e o ativo total só existem no esquema novo, ficando `NULL` antes
  de 202503. O relatório DRE do IF.data é sempre carteira **ativa**
  (estoque) — não tem concessão/originação por instituição. A árvore
  completa do esquema novo (rendas por origem, despesas por natureza,
  resultado de intermediação financeira etc.) foi validada por identidade
  matemática exata contra dados reais (Banco do Brasil) usando o site
  público `www3.bcb.gov.br/ifdata`. BACEN não publica uma linha única de
  "Receita Total"/"Despesa Total" — `gerar_instituicoes_financeiras.py`
  deriva as duas (classificando cada conta por natureza — receita ou
  despesa — e pelo sinal nas poucas contas ambíguas tipo "Resultado com
  X") de forma que sempre reconciliam exatamente com o lucro líquido;
  contas de despesa vêm negativas do BCB (convenção COSIF, soma direto na
  árvore) mas são exibidas com sinal positivo (magnitude de custo),
  consistente com o próprio "Despesa Total".
- **IF.data — plano B quando a Olinda cai (`baixar_ifdata_www3_fallback()`
  em `baixar_dados.py`)**: a API oficial (Olinda) já ficou fora do ar por
  vários dias seguidos (HTTP 500 em todos os endpoints, jul/2026). Existe
  um backend alternativo VIVO, o site público `www3.bcb.gov.br/ifdata`
  (arquivos JSON estáticos pré-gerados, não OData) — usa os MESMOS
  códigos de conta da Olinda (confirmado por reconciliação matemática
  exata), mas só cobre uma janela **rolante de ~5 trimestres recentes**
  (não serve pra backfill histórico). O fallback roda sempre, depois de
  `baixar_ifdata()`: lista as competências disponíveis
  (`rest/relatorios2025a2030`), e só baixa/preenche as que estiverem
  ausentes/vazias no `RAW/ifdata` da Olinda — nunca sobrescreve um dado
  bom já baixado da fonte oficial. Escreve nos MESMOS nomes de arquivo que
  `baixar_ifdata()` produziria (`cadastro_{am}.json`, `dre_{am}.json`, no
  formato `{"value": [...]}` com os mesmos nomes de campo), então
  `carregar_ifdata()` (`carregar_dados.py`) não precisa de nenhuma mudança
  — é transparente pro resto do pipeline. Cobre cadastro + DRE + carteira
  ativa (aging PF/PJ, CNAE, porte), no nível **CONSOLIDADO** (conglomerado
  prudencial, `cadastro_1009` — 1 linha por grupo econômico, ex.
  "ITAU - PRUDENCIAL", não 2-3 pessoas jurídicas separadas do mesmo
  banco); não cobre taxa de juros por instituição (só existe via Olinda).
  A carteira ativa usa mapeamento **dinâmico**: busca a definição de
  colunas de cada relatório (`trelXXX_123/127/128/129.json` — Aging PF,
  Porte, Aging PJ, CNAE) e o catálogo de nomes (`infoXXXXXX.json`) a cada
  execução, em vez de uma lista de códigos fixa no código (são ~300 contas
  entre os 4 relatórios — grande demais pra manter à mão com segurança).
  **Dois bugs reais corrigidos logo depois da primeira versão** (achados
  pelo usuário comparando os números com a realidade): (1) os valores "v"
  desse backend vêm em **reais cheios**, não em "R$ mil" como a Olinda —
  gravar o valor bruto direto inflava tudo por 1000× (ex. lucro do Itaú
  aparecia como "11,65 tri" em vez de "11,65 bi"); corrigido dividindo por
  1000 na entrada. (2) o cadastro usado era `cadastro_1006` ("Instituições
  Individuais"), fazendo o mesmo banco aparecer 2-3 vezes (pessoa jurídica
  + holding); trocado para `cadastro_1009` (conglomerado prudencial),
  confirmando que o mesmo shard de dados tem os valores de DRE também
  nesse nível consolidado.
- **Recuperação Judicial / Extrajudicial / Falência (CNJ DataJud)**: conta
  processos pela classe processual (nº129/128/108 da Tabela Processual
  Unificada) e pela `dataAjuizamento`, um índice Elasticsearch por tribunal
  estadual — cobre as 27 UFs (RJ/Falência são competência da Justiça
  Estadual, não federal). Nota importante: o total nacional por esta fonte
  fica sensivelmente **mais alto** que o do antigo indicador da Serasa
  Experian (ex.: jan/2026 ≈ 103 processos de RJ pelo DataJud vs. 53
  divulgados pela Serasa) — verificado que não é bug (distribuição por UF é
  suave, sem outlier isolado); é diferença de metodologia real: o DataJud
  conta **processos** judiciais com aquela classe, enquanto o indicador da
  Serasa é um número curado com critério próprio (possivelmente restrito a
  empresas de certo porte, ou com outro filtro de admissibilidade). Ao
  comparar com números divulgados pela imprensa/Serasa, considerar que a
  base aqui é mais ampla (contagem judicial bruta), não uma substituição
  exata do indicador comercial.
- A carga de uma fonte que falhar no download **não derruba as demais**
  — a tabela nasce vazia e é preenchida na atualização seguinte.
