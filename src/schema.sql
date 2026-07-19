-- =====================================================================
-- Sistema Atividade Econômica — camada analítica
-- Executado por carregar_dados.py ao final de cada carga.
-- Organização (mesmo padrão do Sistema FIDC/CVM):
--   1. tabelas derivadas
--   2. views analíticas v_*
--   3. materialização m_* (views pesadas viram tabelas indexadas)
-- =====================================================================

-- =====================================================================
-- 1. ESQUELETOS — garantem que as views e a materialização funcionem
-- mesmo quando uma fonte falhou no download (tabela nasce vazia; a
-- carga com dados substitui via to_sql if_exists="replace").
-- =====================================================================
CREATE TABLE IF NOT EXISTS sgs_valor (
    codigo INTEGER, competencia INTEGER, data TEXT, valor REAL);
CREATE TABLE IF NOT EXISTS pib_municipio (
    cod_ibge7 TEXT, ano INTEGER, pib_mil REAL, impostos_mil REAL,
    vab_total_mil REAL, vab_agro_mil REAL, vab_industria_mil REAL,
    vab_servicos_mil REAL, vab_adm_mil REAL);
CREATE TABLE IF NOT EXISTS pib_trimestral (
    trimestre INTEGER, setor TEXT, indice_volume REAL,
    taxa_tri_ano_anterior_pct REAL, taxa_acum_4tri_pct REAL,
    taxa_acum_ano_pct REAL, taxa_tri_anterior_pct REAL);
CREATE TABLE IF NOT EXISTS estban_municipio (
    competencia INTEGER, cod_ibge7 TEXT, verbete INTEGER, saldo_mil REAL);
CREATE TABLE IF NOT EXISTS estban_presenca (
    competencia INTEGER, cod_ibge7 TEXT, instituicoes INTEGER, agencias REAL);
CREATE TABLE IF NOT EXISTS caged_mensal (
    competencia INTEGER, cod_ibge7 TEXT, divisao TEXT, admissoes INTEGER,
    desligamentos INTEGER, saldo INTEGER, salario_medio_adm REAL);
CREATE TABLE IF NOT EXISTS cempre_municipio (
    cod_ibge7 TEXT, ano INTEGER, secao TEXT, empresas REAL,
    pessoal_total REAL, pessoal_assalariado REAL, salarios_mil REAL);
CREATE TABLE IF NOT EXISTS cempre_municipio_total (
    cod_ibge7 TEXT, ano INTEGER, unidades_locais REAL,
    empresas_atuantes REAL, pessoal_total REAL, pessoal_assalariado REAL,
    salarios_mil REAL, salario_medio_reais REAL);
CREATE TABLE IF NOT EXISTS ipea_populacao_municipio (
    cod_ibge7 TEXT, ano INTEGER, populacao REAL);
CREATE TABLE IF NOT EXISTS emprego_nacional_hist (
    competencia INTEGER, fonte TEXT, admissoes REAL,
    desligamentos REAL, saldo REAL);
CREATE TABLE IF NOT EXISTS credito_detalhado_serie (
    codigo INTEGER PRIMARY KEY, titulo TEXT, metrica TEXT, cliente TEXT,
    porte TEXT, origem TEXT, regiao TEXT, modalidade TEXT, periodicidade TEXT,
    unidade TEXT, frente TEXT, parse_status TEXT);
CREATE TABLE IF NOT EXISTS credito_detalhado_valor (
    codigo INTEGER, competencia INTEGER, data TEXT, valor REAL);
CREATE TABLE IF NOT EXISTS malha_uf (
    uf TEXT PRIMARY KEY, geojson TEXT);

-- IF.data (BCB) — cadastro trimestral de instituições financeiras
-- (segmento prudencial S1-S5 no campo `segmento`) e Demonstração de
-- Resultado (DRE) trimestral por instituição individual. `situacao`:
-- A=ativa, I=inativa, F=falida, LO/LE=liquidação ordinária/extrajudicial.
CREATE TABLE IF NOT EXISTS ifdata_instituicao (
    codinst TEXT, anomes INTEGER, nome TEXT, segmento TEXT, uf TEXT,
    municipio TEXT, situacao TEXT, tipo_consolidacao INTEGER,
    cod_conglomerado_financeiro TEXT, cod_conglomerado_prudencial TEXT,
    PRIMARY KEY (codinst, anomes));
CREATE TABLE IF NOT EXISTS ifdata_dre_valor (
    codinst TEXT, anomes INTEGER, conta TEXT, valor REAL,
    PRIMARY KEY (codinst, anomes, conta));

-- IF.data relatórios 11 (carteira PF por modalidade e prazo/aging), 12 (PJ
-- por CNAE), 13 (PJ por modalidade e prazo/aging) e 14 (PJ por porte do
-- tomador) — publicados só no nível de CONGLOMERADO PRUDENCIAL
-- (TipoInstituicao=1), diferente do DRE (TipoInstituicao=2, instituição
-- individual). `codinst` aqui é o CodInst do conglomerado prudencial —
-- ainda casa com `ifdata_instituicao` (o cadastro traz as duas linhas,
-- individual e "- PRUDENCIAL", cada uma com seu próprio codinst).
-- `grupo` = modalidade (relatórios 11/13, ex. "Capital de Giro") ou NULL
-- (12/14, onde a categoria já é `nome_coluna`).
CREATE TABLE IF NOT EXISTS ifdata_carteira_valor (
    codinst TEXT, anomes INTEGER, relatorio TEXT, grupo TEXT, conta TEXT,
    nome_coluna TEXT, valor REAL,
    PRIMARY KEY (codinst, anomes, relatorio, conta));

-- Taxa de juros por instituição financeira, por segmento (PF/PJ) e
-- modalidade — fonte separada do IF.data (serviço BCB "taxaJuros"),
-- semanal (a entidade "diária" na verdade agrega em janelas de ~5 dias
-- úteis) — usa-se 1 semana por mês como proxy mensal. Único serviço do BCB
-- que já vem com o corte PF/PJ confiável por instituição (a versão
-- "mensal" da mesma API não expõe segmento, e o nome de modalidade sozinho
-- não desambigua PF de PJ em alguns casos) — EXCEÇÃO: financiamento
-- imobiliário só existe na entidade mensal (sem segmento), mas como todas
-- as 6 modalidades desse tipo são exclusivamente PF, o segmento é marcado
-- manualmente na carga (ver MODALIDADES_IMOBILIARIO_PF em baixar_dados.py)
-- — pra essas linhas, `inicio_periodo` é o 1º dia do MÊS, não de uma
-- semana como no resto da tabela.
CREATE TABLE IF NOT EXISTS taxa_juros_instituicao (
    inicio_periodo TEXT, cnpj8 TEXT, instituicao TEXT, segmento TEXT,
    modalidade TEXT, taxa_mes_pct REAL, taxa_ano_pct REAL,
    PRIMARY KEY (inicio_periodo, cnpj8, segmento, modalidade));

-- Recuperação Judicial, Extrajudicial e Falência por UF — contagem de
-- processos via API pública DataJud (CNJ), um índice Elasticsearch por
-- tribunal estadual. `classe` vem da classe processual (Tabela Processual
-- Unificada, nacional): RECUPERACAO_JUDICIAL (código 129),
-- RECUPERACAO_EXTRAJUDICIAL (128), FALENCIA (108) — confirmados idênticos
-- em mais de um tribunal via consulta real. Substituiu o scraping de
-- releases da Serasa Experian (que só dava o total nacional, sem UF).
CREATE TABLE IF NOT EXISTS datajud_rj_falencia_mensal (
    uf TEXT, tribunal TEXT, competencia INTEGER, classe TEXT, processos INTEGER,
    PRIMARY KEY (tribunal, competencia, classe));

-- =====================================================================
-- 2. VIEWS ANALÍTICAS
-- =====================================================================

-- ---------------------------------------------------------------
-- v_credito_setor — saldo de crédito por setor de atividade (SGS):
-- série mensal com variação 12M; participação % calculada sobre os
-- macro-setores (nivel = SETOR: Agropecuária, Indústria, Serviços, Outros)
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_credito_setor;
CREATE VIEW v_credito_setor AS
WITH cs AS (
    SELECT s.codigo, s.nome, s.nivel, s.setor, s.subsetor,
           v.competencia, v.valor
    FROM sgs_valor v
    JOIN sgs_serie s ON s.codigo = v.codigo
    WHERE s.grupo = 'CREDITO_SETOR'
),
com_lag AS (
    SELECT cs.*,
           LAG(valor, 12) OVER (PARTITION BY codigo ORDER BY competencia) AS valor_12m_atras
    FROM cs
)
SELECT c.codigo, c.nome, c.nivel, c.setor, c.subsetor, c.competencia,
       c.valor,
       ROUND(100.0 * c.valor / NULLIF(c.valor_12m_atras, 0) - 100, 2) AS var_12m_pct,
       CASE WHEN c.nivel = 'SETOR'
            THEN ROUND(100.0 * c.valor / NULLIF(t.total_setores, 0), 2)
       END AS participacao_pct
FROM com_lag c
LEFT JOIN (SELECT competencia, SUM(valor) AS total_setores
           FROM cs WHERE nivel = 'SETOR' GROUP BY 1) t
       ON t.competencia = c.competencia;

-- ---------------------------------------------------------------
-- v_credito_macro — carteira de crédito consolidada por mês:
-- total/PJ/PF, livres/direcionados, crédito/PIB, inadimplência,
-- concessões e taxa média de juros (uma linha por competência)
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_credito_macro;
CREATE VIEW v_credito_macro AS
SELECT competencia,
       MAX(CASE WHEN codigo = 20539 THEN valor END) AS saldo_total,
       MAX(CASE WHEN codigo = 20540 THEN valor END) AS saldo_pj,
       MAX(CASE WHEN codigo = 20541 THEN valor END) AS saldo_pf,
       MAX(CASE WHEN codigo = 20542 THEN valor END) AS saldo_livres,
       MAX(CASE WHEN codigo = 20543 THEN valor END) AS saldo_livres_pj,
       MAX(CASE WHEN codigo = 20570 THEN valor END) AS saldo_livres_pf,
       MAX(CASE WHEN codigo = 20593 THEN valor END) AS saldo_direcionados,
       MAX(CASE WHEN codigo = 20594 THEN valor END) AS saldo_direcionados_pj,
       MAX(CASE WHEN codigo = 20606 THEN valor END) AS saldo_direcionados_pf,
       MAX(CASE WHEN codigo = 20622 THEN valor END) AS credito_pib_pct,
       MAX(CASE WHEN codigo = 21082 THEN valor END) AS inadimplencia_total,
       MAX(CASE WHEN codigo = 21083 THEN valor END) AS inadimplencia_pj,
       MAX(CASE WHEN codigo = 21084 THEN valor END) AS inadimplencia_pf,
       MAX(CASE WHEN codigo = 20631 THEN valor END) AS concessoes_total,
       MAX(CASE WHEN codigo = 20632 THEN valor END) AS concessoes_pj,
       MAX(CASE WHEN codigo = 20633 THEN valor END) AS concessoes_pf,
       MAX(CASE WHEN codigo = 20714 THEN valor END) AS juros_total,
       MAX(CASE WHEN codigo = 20715 THEN valor END) AS juros_pj,
       MAX(CASE WHEN codigo = 20716 THEN valor END) AS juros_pf
FROM sgs_valor
GROUP BY competencia;

-- ---------------------------------------------------------------
-- v_pulso_nacional — painel mensal Brasil: IBC-Br, crédito,
-- concessões, inadimplência — com variações M/M e 12M
-- (o saldo do CAGED entra na etapa de emprego)
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_pulso_nacional;
CREATE VIEW v_pulso_nacional AS
WITH base AS (
    SELECT competencia,
           MAX(CASE WHEN codigo = 24363 THEN valor END) AS ibc_br,
           MAX(CASE WHEN codigo = 24364 THEN valor END) AS ibc_br_ajustado,
           MAX(CASE WHEN codigo = 20539 THEN valor END) AS saldo_credito,
           MAX(CASE WHEN codigo = 20631 THEN valor END) AS concessoes,
           MAX(CASE WHEN codigo = 21082 THEN valor END) AS inadimplencia,
           MAX(CASE WHEN codigo = 20622 THEN valor END) AS credito_pib_pct
    FROM sgs_valor
    GROUP BY competencia
)
SELECT competencia, ibc_br, ibc_br_ajustado,
       ROUND(100.0 * ibc_br_ajustado /
             NULLIF(LAG(ibc_br_ajustado) OVER w, 0) - 100, 2)      AS ibc_var_mm_pct,
       ROUND(100.0 * ibc_br / NULLIF(LAG(ibc_br, 12) OVER w, 0) - 100, 2) AS ibc_var_12m_pct,
       saldo_credito,
       ROUND(100.0 * saldo_credito /
             NULLIF(LAG(saldo_credito, 12) OVER w, 0) - 100, 2)    AS credito_var_12m_pct,
       concessoes,
       ROUND(100.0 * concessoes /
             NULLIF(LAG(concessoes, 12) OVER w, 0) - 100, 2)       AS concessoes_var_12m_pct,
       inadimplencia, credito_pib_pct
FROM base
WINDOW w AS (ORDER BY competencia);

-- ---------------------------------------------------------------
-- v_atividade_regional — IBC-Br e IBCR das 5 regiões (base do
-- acompanhamento regional de atividade)
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_atividade_regional;
CREATE VIEW v_atividade_regional AS
SELECT v.competencia, s.nome AS indicador, v.valor,
       ROUND(100.0 * v.valor /
             NULLIF(LAG(v.valor, 12) OVER (PARTITION BY v.codigo
                                           ORDER BY v.competencia), 0) - 100, 2)
           AS var_12m_pct
FROM sgs_valor v
JOIN sgs_serie s ON s.codigo = v.codigo
WHERE s.grupo = 'ATIVIDADE';

-- ---------------------------------------------------------------
-- v_credito_detalhado — base do crédito por modalidade/porte/MEI/
-- ICC/prazo médio (BACEN, semente gerada por
-- descobrir_credito_detalhado.py). Periodicidade mista (mensal ×
-- trimestral) entre séries — por isso a variação anual usa self-join
-- por competencia-100 (mesmo mês/trimestre do ano anterior em AAAAMM),
-- não LAG(12) OVER(...), que assume espaçamento mensal uniforme.
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_credito_detalhado;
CREATE VIEW v_credito_detalhado AS
SELECT v.codigo, s.metrica, s.cliente, s.porte, s.origem, s.regiao, s.modalidade,
       s.periodicidade, s.unidade, v.competencia, v.valor,
       ant.valor AS valor_ano_anterior,
       ROUND(100.0 * v.valor / NULLIF(ant.valor, 0) - 100, 2) AS var_ano_pct
FROM credito_detalhado_valor v
JOIN credito_detalhado_serie s ON s.codigo = v.codigo
LEFT JOIN credito_detalhado_valor ant
       ON ant.codigo = v.codigo AND ant.competencia = v.competencia - 100;

-- v_credito_juros_modalidade — taxa de juros por modalidade (PJ por
-- porte e MEI), trimestral
DROP VIEW IF EXISTS v_credito_juros_modalidade;
CREATE VIEW v_credito_juros_modalidade AS
SELECT * FROM v_credito_detalhado WHERE metrica = 'TAXA_JUROS';

-- v_credito_inadimplencia_modalidade — inadimplência por modalidade
-- (PJ por porte e MEI), trimestral
DROP VIEW IF EXISTS v_credito_inadimplencia_modalidade;
CREATE VIEW v_credito_inadimplencia_modalidade AS
SELECT * FROM v_credito_detalhado WHERE metrica = 'TAXA_INADIMPLENCIA';

-- v_credito_saldo_porte — saldo de crédito por porte de PJ (micro/
-- pequeno/MPMe/grande) e MEI, com participação % dentro do porte
DROP VIEW IF EXISTS v_credito_saldo_porte;
CREATE VIEW v_credito_saldo_porte AS
SELECT d.*,
       ROUND(100.0 * d.valor /
             NULLIF(SUM(d.valor) OVER (PARTITION BY d.cliente, d.porte,
                                       d.competencia), 0), 2)
           AS participacao_pct
FROM v_credito_detalhado d
WHERE d.metrica = 'SALDO';

-- v_credito_icc — Indicador de Custo do Crédito e Spread do ICC por
-- origem/cliente/modalidade, mensal
DROP VIEW IF EXISTS v_credito_icc;
CREATE VIEW v_credito_icc AS
SELECT * FROM v_credito_detalhado WHERE metrica IN ('ICC', 'SPREAD_ICC');

-- v_credito_prazo_medio — prazo médio da carteira e das concessões
-- por origem/cliente/modalidade, mensal
DROP VIEW IF EXISTS v_credito_prazo_medio;
CREATE VIEW v_credito_prazo_medio AS
SELECT * FROM v_credito_detalhado
WHERE metrica IN ('PRAZO_MEDIO_CARTEIRA', 'PRAZO_MEDIO_CONCESSAO');

-- v_credito_concessao_modalidade — concessões de crédito (novas operações
-- no mês) por modalidade, PF/PJ em geral (família "Concessões de crédito
-- com recursos livres/direcionados", espelho da de saldo/estoque — mesma
-- ressalva de hierarquia pai-filho e do corte paralelo Rotativo/Não
-- rotativo, ver CARTEIRA_FILHOS_OU_PARALELOS em gerar_dashboard.py)
DROP VIEW IF EXISTS v_credito_concessao_modalidade;
CREATE VIEW v_credito_concessao_modalidade AS
SELECT * FROM v_credito_detalhado WHERE metrica = 'CONCESSAO' AND modalidade IS NOT NULL;

-- v_credito_saldo_regiao — saldo de crédito por grande região do IBGE
-- (Norte/Nordeste/Centro-Oeste/Sudeste/Sul) e porte de PJ/MEI, trimestral —
-- única quebra geográfica abaixo do nacional que o BACEN publica para
-- saldo por porte; não existe por UF nem com taxa/modalidade completa
DROP VIEW IF EXISTS v_credito_saldo_regiao;
CREATE VIEW v_credito_saldo_regiao AS
SELECT * FROM v_credito_detalhado WHERE metrica = 'SALDO' AND regiao IS NOT NULL;

-- ---------------------------------------------------------------
-- v_ifdata_dre — Demonstração de Resultado trimestral por instituição
-- financeira individual, com segmento prudencial (S1-S5) e situação.
--
-- O BCB TROCOU a numeração de contas do relatório DRE do IF.data a partir
-- de 202503 (esquema antigo: contas 78xxx, ~30 contas; esquema novo:
-- contas 140xxx-142xxx, ~52 contas) — confirmado batendo os dois conjuntos
-- de contas lado a lado. Nem todo conceito tem equivalente direto no
-- esquema antigo:
--   lucro líquido:            novo 141870 (z)   / antigo 78187 (j)   — equivalentes
--   receita de operações de
--     crédito (total da categoria): novo 141835 (c) / antigo 78203 (a1) — equivalentes
--   receita de JUROS de crédito (sub-linha só de juros, sem câmbio/ajustes):
--     só existe no esquema novo (141831, c1) — antigo não abre esse sub-corte, fica NULL
--   perda esperada de operações de crédito: novo 141840 (f3) / antigo 78213 (b5) — equivalentes
--   perda esperada TOTAL (todas as categorias de ativo, não só crédito):
--     só existe no esquema novo (141842, f) — antigo não tem essa linha isolada, fica NULL
--   ativo total: só existe no esquema novo (140220) — no antigo viria de outro
--     relatório (Ativo), não carregado ainda, fica NULL antes de 202503
--
-- Árvore completa do esquema novo (2025+, "COSIF 2025"), validada em jul/2026
-- contra o site www3.bcb.gov.br/ifdata (fonte alternativa à API Olinda,
-- usada quando a Olinda está fora do ar — MESMOS códigos de conta, confirmado
-- cruzando valor a valor para o Banco do Brasil em 12/2025):
--   Lucro Líquido (z, 141870) = Resultado antes Tributação/Participações (w,
--     141867) + Imposto de Renda/CSLL (x, 141868) + Participações no Lucro
--     (y, 141869) — identidade oficial do BCB, validada numérica e exatamente
--   Resultado antes Tributação (w) = Resultado de Intermediação Financeira
--     (k, 141851) + Resultado com Transações de Pagamento (l, 141855) +
--     Outras Receitas/Despesas (v, 141866) — também validado exatamente
--   Resultado de Intermediação Financeira (k) = Rendas de Aplicações
--     Interfinanceiras (a, 141825) + Rendas de TVM (b, 141830) + Rendas de
--     Operações de Crédito (c, 141835) + Rendas de Arrendamento Financeiro
--     (d, 141836 — confirmado via catálogo "info" do www3.bcb.gov.br/ifdata,
--     campo lid) + Rendas de Outras Operações com Características de
--     Concessão de Crédito (e, 141837) + Resultado com Perda Esperada (f,
--     141842) + Despesas de Captações (g, 141847) + Despesas de
--     Instrumentos de Dívida Elegíveis a Capital (h, 141848) + Resultado
--     com Derivativos (i, 141849) + Outros Resultados de Intermediação
--     Financeira (j, 141850) — validado exatamente (soma de
--     a+b+c+d+e+f+g+h+i+j bate com k, Banco do Brasil dez/2025 e mar/2026)
--   Dentro de (c): Receita de Juros com Operações de Crédito (c1, 141831) é
--     o SUBCONJUNTO de juros dentro do total (c) — é o par receita_credito_
--     total/receita_juros_credito já existente. Dentro de (f): Resultado
--     com Perda Esperada de Operações de Crédito (f3, 141840) é o
--     subconjunto de crédito dentro do total (f) — o par perda_esperada_
--     total/perda_esperada_credito já existente.
--   (l) se abre em l1 (Resultado c/ Serviços por Transações de Pagamento,
--     141852) + l2 (Perda Esperada c/ Transações de Pagamento, 141853) +
--     l3 (Outros, 141854). (v) se abre em m (Rendas de Tarifas Bancárias,
--     141856) + n (Outras Rendas de Prestação de Serviços, 141857) + o
--     (Despesas de Pessoal, 141858) + p (Despesas Administrativas, 141859)
--     + q (Result. Perdas Esperadas Outras Operações, 141860) + r (Despesas
--     Tributárias, 141862) + s (Resultado de Participações, 141863) + t
--     (Outras Receitas, 141864) + u (Outras Despesas, 141865). Usados por
--     gerar_instituicoes_financeiras.py para montar Receita Total/Despesa
--     Total (classificação por natureza da conta + sinal para os itens
--     ambíguos tipo "Resultado com X") que reconcilia exatamente com
--     Lucro Líquido.
DROP VIEW IF EXISTS v_ifdata_dre;
CREATE VIEW v_ifdata_dre AS
SELECT i.codinst, i.anomes, i.nome, i.segmento, i.uf, i.municipio, i.situacao,
       MAX(CASE WHEN d.conta = '140220' THEN d.valor END) AS ativo_total,
       MAX(CASE WHEN d.conta = '141831' THEN d.valor END) AS receita_juros_credito,
       MAX(CASE WHEN d.conta IN ('141835', '78203') THEN d.valor END) AS receita_credito_total,
       MAX(CASE WHEN d.conta IN ('141840', '78213') THEN d.valor END) AS perda_esperada_credito,
       MAX(CASE WHEN d.conta = '141842' THEN d.valor END) AS perda_esperada_total,
       MAX(CASE WHEN d.conta = '141825' THEN d.valor END) AS rendas_interfinanceiras,
       MAX(CASE WHEN d.conta = '141830' THEN d.valor END) AS rendas_tvm,
       MAX(CASE WHEN d.conta = '141836' THEN d.valor END) AS rendas_arrendamento,
       MAX(CASE WHEN d.conta = '141837' THEN d.valor END) AS rendas_outras_credito,
       MAX(CASE WHEN d.conta = '141847' THEN d.valor END) AS despesas_captacao,
       MAX(CASE WHEN d.conta = '141848' THEN d.valor END) AS despesas_divida_capital,
       MAX(CASE WHEN d.conta = '141849' THEN d.valor END) AS resultado_derivativos,
       MAX(CASE WHEN d.conta = '141850' THEN d.valor END) AS outros_resultado_intermediacao,
       MAX(CASE WHEN d.conta = '141851' THEN d.valor END) AS resultado_intermediacao_financeira,
       MAX(CASE WHEN d.conta = '141852' THEN d.valor END) AS resultado_servicos_pagamento,
       MAX(CASE WHEN d.conta = '141853' THEN d.valor END) AS perda_esperada_pagamento,
       MAX(CASE WHEN d.conta = '141854' THEN d.valor END) AS outros_resultado_pagamento,
       MAX(CASE WHEN d.conta = '141855' THEN d.valor END) AS resultado_transacoes_pagamento,
       MAX(CASE WHEN d.conta = '141856' THEN d.valor END) AS rendas_tarifas_bancarias,
       MAX(CASE WHEN d.conta = '141857' THEN d.valor END) AS outras_rendas_servicos,
       MAX(CASE WHEN d.conta = '141858' THEN d.valor END) AS despesas_pessoal,
       MAX(CASE WHEN d.conta = '141859' THEN d.valor END) AS despesas_administrativas,
       MAX(CASE WHEN d.conta = '141860' THEN d.valor END) AS perda_esperada_outras_operacoes,
       MAX(CASE WHEN d.conta = '141862' THEN d.valor END) AS despesas_tributarias,
       MAX(CASE WHEN d.conta = '141863' THEN d.valor END) AS resultado_participacoes,
       MAX(CASE WHEN d.conta = '141864' THEN d.valor END) AS outras_receitas,
       MAX(CASE WHEN d.conta = '141865' THEN d.valor END) AS outras_despesas,
       MAX(CASE WHEN d.conta = '141866' THEN d.valor END) AS outras_receitas_despesas,
       MAX(CASE WHEN d.conta = '141867' THEN d.valor END) AS resultado_antes_tributacao,
       MAX(CASE WHEN d.conta = '141868' THEN d.valor END) AS imposto_renda_csll,
       MAX(CASE WHEN d.conta = '141869' THEN d.valor END) AS participacoes_lucro,
       MAX(CASE WHEN d.conta IN ('141870', '78187') THEN d.valor END) AS lucro_liquido
FROM ifdata_instituicao i
JOIN ifdata_dre_valor d ON d.codinst = i.codinst AND d.anomes = i.anomes
GROUP BY i.codinst, i.anomes;

-- v_ifdata_aging — carteira de crédito ativa por modalidade × faixa de
-- vencimento (relatório 11 = Pessoa Física, 13 = Pessoa Jurídica), por
-- conglomerado prudencial. `bucket` distingue as 6 faixas "a vencer", a
-- única faixa "vencido" (a partir de 15 dias — o BCB não abre mais fino
-- que isso) e o "Total" da modalidade.
DROP VIEW IF EXISTS v_ifdata_aging;
CREATE VIEW v_ifdata_aging AS
SELECT i.codinst, i.anomes, i.nome, i.segmento, i.uf, i.situacao,
       CASE c.relatorio WHEN '11' THEN 'PF' WHEN '13' THEN 'PJ' END AS cliente,
       c.grupo AS modalidade, c.nome_coluna AS bucket, c.valor
FROM ifdata_instituicao i
JOIN ifdata_carteira_valor c ON c.codinst = i.codinst AND c.anomes = i.anomes
WHERE c.relatorio IN ('11', '13');

-- v_ifdata_cnae — carteira de crédito ativa Pessoa Jurídica por atividade
-- econômica (CNAE), por conglomerado prudencial (relatório 12). Tem a
-- MESMA estrutura aninhada do aging (CNAE × faixa de vencimento) — `bucket`
-- = 'Total' dá o saldo da atividade inteira; os demais valores abrem por
-- faixa de vencimento dentro daquele CNAE, igual v_ifdata_aging.
DROP VIEW IF EXISTS v_ifdata_cnae;
CREATE VIEW v_ifdata_cnae AS
SELECT i.codinst, i.anomes, i.nome, i.segmento, i.uf, i.situacao,
       c.grupo AS atividade, c.nome_coluna AS bucket, c.valor
FROM ifdata_instituicao i
JOIN ifdata_carteira_valor c ON c.codinst = i.codinst AND c.anomes = i.anomes
WHERE c.relatorio = '12' AND c.grupo IS NOT NULL;

-- v_ifdata_porte — carteira de crédito ativa Pessoa Jurídica por porte do
-- tomador, por conglomerado prudencial (relatório 14)
DROP VIEW IF EXISTS v_ifdata_porte;
CREATE VIEW v_ifdata_porte AS
SELECT i.codinst, i.anomes, i.nome, i.segmento, i.uf, i.situacao,
       c.nome_coluna AS porte, c.valor
FROM ifdata_instituicao i
JOIN ifdata_carteira_valor c ON c.codinst = i.codinst AND c.anomes = i.anomes
WHERE c.relatorio = '14';

-- ---------------------------------------------------------------
-- v_recuperacao_falencia_uf / _nacional — Recuperação Judicial,
-- Extrajudicial e Falência (DataJud/CNJ), por UF e agregado Brasil
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_recuperacao_falencia_uf;
CREATE VIEW v_recuperacao_falencia_uf AS
SELECT uf, tribunal, competencia,
       SUM(CASE WHEN classe = 'RECUPERACAO_JUDICIAL' THEN processos ELSE 0 END) AS processos_rj,
       SUM(CASE WHEN classe = 'RECUPERACAO_EXTRAJUDICIAL' THEN processos ELSE 0 END) AS processos_extrajudicial,
       SUM(CASE WHEN classe = 'FALENCIA' THEN processos ELSE 0 END) AS processos_falencia
FROM datajud_rj_falencia_mensal
GROUP BY uf, tribunal, competencia;

DROP VIEW IF EXISTS v_recuperacao_falencia_nacional;
CREATE VIEW v_recuperacao_falencia_nacional AS
SELECT competencia,
       SUM(CASE WHEN classe = 'RECUPERACAO_JUDICIAL' THEN processos ELSE 0 END) AS processos_rj,
       SUM(CASE WHEN classe = 'RECUPERACAO_EXTRAJUDICIAL' THEN processos ELSE 0 END) AS processos_extrajudicial,
       SUM(CASE WHEN classe = 'FALENCIA' THEN processos ELSE 0 END) AS processos_falencia
FROM datajud_rj_falencia_mensal
GROUP BY competencia;

-- ---------------------------------------------------------------
-- v_pib_municipio — PIB, per capita e composição do VAB por
-- município/ano, com participação na UF e rankings
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_pib_municipio;
CREATE VIEW v_pib_municipio AS
WITH base AS (
    SELECT p.cod_ibge7, m.nome, m.uf, m.regiao, p.ano,
           p.pib_mil, p.impostos_mil, p.vab_total_mil,
           p.vab_agro_mil, p.vab_industria_mil,
           p.vab_servicos_mil, p.vab_adm_mil,
           r.populacao,
           ROUND(1000.0 * p.pib_mil / NULLIF(r.populacao, 0), 2) AS pib_per_capita
    FROM pib_municipio p
    JOIN municipio m ON m.cod_ibge7 = p.cod_ibge7
    LEFT JOIN municipio_populacao_ref r
           ON r.cod_ibge7 = p.cod_ibge7 AND r.ano = p.ano
)
SELECT b.*,
       ROUND(100.0 * b.pib_mil /
             NULLIF(SUM(b.pib_mil) OVER (PARTITION BY b.uf, b.ano), 0), 3)
           AS participacao_uf_pct,
       ROUND(100.0 * b.vab_agro_mil      / NULLIF(b.vab_total_mil, 0), 1) AS pct_agro,
       ROUND(100.0 * b.vab_industria_mil / NULLIF(b.vab_total_mil, 0), 1) AS pct_industria,
       ROUND(100.0 * b.vab_servicos_mil  / NULLIF(b.vab_total_mil, 0), 1) AS pct_servicos,
       ROUND(100.0 * b.vab_adm_mil       / NULLIF(b.vab_total_mil, 0), 1) AS pct_adm_publica,
       RANK() OVER (PARTITION BY b.ano ORDER BY b.pib_mil DESC)       AS rank_pib_brasil,
       RANK() OVER (PARTITION BY b.uf, b.ano ORDER BY b.pib_mil DESC) AS rank_pib_uf
FROM base b;

-- ---------------------------------------------------------------
-- v_porte_municipio — classificação de porte econômico e
-- populacional de cada município (último ano de PIB disponível)
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_porte_municipio;
CREATE VIEW v_porte_municipio AS
SELECT v.cod_ibge7, v.nome, v.uf, v.regiao, v.ano,
       v.pib_mil, v.populacao, v.pib_per_capita,
       CASE WHEN v.pib_mil >= 10000000 THEN '1. Acima de R$ 10 bi'
            WHEN v.pib_mil >=  1000000 THEN '2. R$ 1 bi a 10 bi'
            WHEN v.pib_mil >=   100000 THEN '3. R$ 100 mi a 1 bi'
            ELSE                            '4. Até R$ 100 mi' END AS faixa_pib,
       CASE WHEN v.populacao >= 1000000 THEN '1. Acima de 1 milhão'
            WHEN v.populacao >=  500000 THEN '2. 500 mil a 1 milhão'
            WHEN v.populacao >=  100000 THEN '3. 100 mil a 500 mil'
            WHEN v.populacao >=   20000 THEN '4. 20 mil a 100 mil'
            ELSE                             '5. Até 20 mil' END AS faixa_pop,
       v.pct_agro, v.pct_industria, v.pct_servicos, v.pct_adm_publica
FROM v_pib_municipio v
WHERE v.ano = (SELECT MAX(ano) FROM pib_municipio);

-- ---------------------------------------------------------------
-- v_pib_trimestral — pulso trimestral por setor: índice de volume
-- e taxas de variação (T/T-1, T/T-4, acumulada 4T e no ano)
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_pib_trimestral;
CREATE VIEW v_pib_trimestral AS
SELECT trimestre, setor,
       indice_volume,
       taxa_tri_anterior_pct,
       taxa_tri_ano_anterior_pct,
       taxa_acum_4tri_pct,
       taxa_acum_ano_pct
FROM pib_trimestral;

-- ---------------------------------------------------------------
-- v_credito_municipio — carteira bancária por município × mês
-- (ESTBAN): crédito total e aberturas, captação e presença bancária.
-- Valores em R$ mil.
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_credito_municipio;
CREATE VIEW v_credito_municipio AS
WITH pivo AS (
    SELECT competencia, cod_ibge7,
           SUM(CASE WHEN verbete = 160 THEN saldo_mil END) AS credito_total_mil,
           SUM(CASE WHEN verbete = 161 THEN saldo_mil END) AS emprestimos_mil,
           SUM(CASE WHEN verbete = 162 THEN saldo_mil END) AS financiamentos_mil,
           SUM(CASE WHEN verbete IN (163,164,165,166,167)
                    THEN saldo_mil END)                    AS credito_rural_mil,
           SUM(CASE WHEN verbete = 169 THEN saldo_mil END) AS credito_imobiliario_mil,
           SUM(CASE WHEN verbete = 171 THEN saldo_mil END) AS outras_oper_credito_mil,
           SUM(CASE WHEN verbete = 174 THEN saldo_mil END) AS provisao_mil,
           SUM(CASE WHEN verbete = 420 THEN saldo_mil END) AS poupanca_mil,
           SUM(CASE WHEN verbete = 432 THEN saldo_mil END) AS deposito_prazo_mil,
           SUM(CASE WHEN verbete = 399 THEN saldo_mil END) AS ativo_total_mil
    FROM estban_municipio
    GROUP BY competencia, cod_ibge7
)
SELECT p.competencia, p.cod_ibge7, m.nome, m.uf, m.regiao,
       p.credito_total_mil, p.emprestimos_mil, p.financiamentos_mil,
       p.credito_rural_mil, p.credito_imobiliario_mil,
       p.outras_oper_credito_mil, p.provisao_mil,
       ROUND(-100.0 * p.provisao_mil / NULLIF(p.credito_total_mil, 0), 2)
           AS provisao_pct_carteira,
       p.poupanca_mil, p.deposito_prazo_mil, p.ativo_total_mil,
       pr.instituicoes, pr.agencias,
       LAG(p.credito_total_mil, 12) OVER (PARTITION BY p.cod_ibge7
                                          ORDER BY p.competencia)
           AS credito_12m_atras_mil,
       ROUND(100.0 * p.credito_total_mil /
             NULLIF(LAG(p.credito_total_mil, 12)
                    OVER (PARTITION BY p.cod_ibge7
                          ORDER BY p.competencia), 0) - 100, 2)
           AS credito_var_12m_pct
FROM pivo p
JOIN municipio m ON m.cod_ibge7 = p.cod_ibge7
LEFT JOIN estban_presenca pr ON pr.cod_ibge7 = p.cod_ibge7
                            AND pr.competencia = p.competencia;

-- ---------------------------------------------------------------
-- v_credito_uf — mesma rubrica ESTBAN de v_credito_municipio, agregada
-- por UF × mês (série completa, não só a última competência)
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_credito_uf;
CREATE VIEW v_credito_uf AS
WITH pivo AS (
    SELECT competencia, uf,
           SUM(credito_total_mil) AS credito_total_mil,
           SUM(emprestimos_mil) AS emprestimos_mil,
           SUM(financiamentos_mil) AS financiamentos_mil,
           SUM(credito_rural_mil) AS credito_rural_mil,
           SUM(credito_imobiliario_mil) AS credito_imobiliario_mil,
           SUM(outras_oper_credito_mil) AS outras_oper_credito_mil,
           SUM(provisao_mil) AS provisao_mil,
           SUM(poupanca_mil) AS poupanca_mil,
           SUM(deposito_prazo_mil) AS deposito_prazo_mil,
           SUM(ativo_total_mil) AS ativo_total_mil,
           SUM(instituicoes) AS instituicoes, SUM(agencias) AS agencias
    FROM v_credito_municipio
    GROUP BY competencia, uf
)
SELECT p.*,
       ROUND(-100.0 * p.provisao_mil / NULLIF(p.credito_total_mil, 0), 2)
           AS provisao_pct_carteira,
       LAG(p.credito_total_mil, 12) OVER (PARTITION BY p.uf ORDER BY p.competencia)
           AS credito_12m_atras_mil,
       ROUND(100.0 * p.credito_total_mil /
             NULLIF(LAG(p.credito_total_mil, 12)
                    OVER (PARTITION BY p.uf ORDER BY p.competencia), 0) - 100, 2)
           AS credito_var_12m_pct
FROM pivo p;

-- ---------------------------------------------------------------
-- v_credito_municipio_pc — última posição de crédito por município,
-- per capita e rankings (Brasil e UF)
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_credito_municipio_pc;
CREATE VIEW v_credito_municipio_pc AS
WITH ultimo AS (
    SELECT * FROM v_credito_municipio
    WHERE competencia = (SELECT MAX(competencia) FROM estban_municipio)
),
pop AS (
    SELECT cod_ibge7, populacao FROM municipio_populacao_ref
    WHERE ano = (SELECT MAX(ano) FROM municipio_populacao_ref)
)
SELECT u.*, p.populacao,
       ROUND(1000.0 * u.credito_total_mil / NULLIF(p.populacao, 0), 0)
           AS credito_per_capita,
       RANK() OVER (ORDER BY u.credito_total_mil DESC)                AS rank_credito_brasil,
       RANK() OVER (PARTITION BY u.uf ORDER BY u.credito_total_mil DESC) AS rank_credito_uf
FROM ultimo u
LEFT JOIN pop p ON p.cod_ibge7 = u.cod_ibge7;

-- ---------------------------------------------------------------
-- v_penetracao_credito — crédito bancário / PIB municipal
-- (crédito: média das últimas 12 competências do ESTBAN;
--  PIB: último ano disponível) — proxy de bancarização do crédito
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_penetracao_credito;
CREATE VIEW v_penetracao_credito AS
WITH cred12 AS (
    SELECT cod_ibge7, AVG(credito_total_mil) AS credito_medio_12m_mil
    FROM v_credito_municipio
    WHERE competencia > (SELECT MAX(competencia) - 100 FROM estban_municipio)
    GROUP BY cod_ibge7
),
pib AS (
    SELECT cod_ibge7, ano, pib_mil FROM pib_municipio
    WHERE ano = (SELECT MAX(ano) FROM pib_municipio)
)
SELECT m.cod_ibge7, m.nome, m.uf, m.regiao,
       c.credito_medio_12m_mil, p.ano AS ano_pib, p.pib_mil,
       ROUND(100.0 * c.credito_medio_12m_mil / NULLIF(p.pib_mil, 0), 1)
           AS credito_pib_pct,
       RANK() OVER (ORDER BY 100.0 * c.credito_medio_12m_mil /
                             NULLIF(p.pib_mil, 0) DESC) AS rank_penetracao
FROM municipio m
JOIN cred12 c ON c.cod_ibge7 = m.cod_ibge7
JOIN pib p    ON p.cod_ibge7 = m.cod_ibge7;

-- ---------------------------------------------------------------
-- v_emprego_setor — CAGED: saldo de emprego por divisão/seção CNAE
-- × mês, nacional, com acumulado 12M e salário médio de admissão
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_emprego_setor;
CREATE VIEW v_emprego_setor AS
WITH por_divisao AS (
    SELECT c.competencia, c.divisao, d.secao, d.secao_nome, d.nome AS divisao_nome,
           SUM(c.admissoes) AS admissoes, SUM(c.desligamentos) AS desligamentos,
           SUM(c.saldo) AS saldo,
           ROUND(SUM(c.salario_medio_adm * c.admissoes) /
                 NULLIF(SUM(CASE WHEN c.salario_medio_adm IS NOT NULL
                                 THEN c.admissoes END), 0), 2) AS salario_medio_adm
    FROM caged_mensal c
    LEFT JOIN cnae_divisao d ON d.divisao = c.divisao
    GROUP BY c.competencia, c.divisao
)
SELECT p.*,
       SUM(p.saldo) OVER (PARTITION BY p.divisao ORDER BY p.competencia
                          ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)
           AS saldo_12m
FROM por_divisao p;

-- ---------------------------------------------------------------
-- v_emprego_municipio — CAGED: saldo de emprego por município × mês
-- com acumulado 12M e saldo por 1.000 habitantes
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_emprego_municipio;
CREATE VIEW v_emprego_municipio AS
WITH por_mun AS (
    SELECT c.competencia, c.cod_ibge7,
           SUM(c.admissoes) AS admissoes, SUM(c.desligamentos) AS desligamentos,
           SUM(c.saldo) AS saldo
    FROM caged_mensal c
    GROUP BY c.competencia, c.cod_ibge7
),
com_12m AS (
    SELECT p.*,
           SUM(p.saldo) OVER (PARTITION BY p.cod_ibge7 ORDER BY p.competencia
                              ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)
               AS saldo_12m
    FROM por_mun p
)
SELECT c.competencia, c.cod_ibge7, m.nome, m.uf, m.regiao,
       c.admissoes, c.desligamentos, c.saldo, c.saldo_12m,
       ROUND(1000.0 * c.saldo_12m / NULLIF(r.populacao, 0), 2)
           AS saldo_12m_por_mil_hab
FROM com_12m c
JOIN municipio m ON m.cod_ibge7 = c.cod_ibge7
LEFT JOIN municipio_populacao_ref r
       ON r.cod_ibge7 = c.cod_ibge7
      AND r.ano = (SELECT MAX(ano) FROM municipio_populacao_ref);

-- ---------------------------------------------------------------
-- v_emprego_uf_historico — CAGED: saldo de emprego por UF × mês, com
-- acumulado 12M (série completa, análoga a v_emprego_municipio por UF)
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_emprego_uf_historico;
CREATE VIEW v_emprego_uf_historico AS
WITH por_uf AS (
    SELECT c.competencia, m.uf,
           SUM(c.admissoes) AS admissoes, SUM(c.desligamentos) AS desligamentos,
           SUM(c.saldo) AS saldo
    FROM caged_mensal c
    JOIN municipio m ON m.cod_ibge7 = c.cod_ibge7
    GROUP BY c.competencia, m.uf
)
SELECT p.*,
       SUM(p.saldo) OVER (PARTITION BY p.uf ORDER BY p.competencia
                          ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)
           AS saldo_12m
FROM por_uf p;

-- ---------------------------------------------------------------
-- v_empresas_municipio — CEMPRE: empresas, pessoal e salários por
-- município × seção CNAE, com densidade empresarial (por mil hab.)
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_empresas_municipio;
CREATE VIEW v_empresas_municipio AS
SELECT c.ano, c.cod_ibge7, m.nome, m.uf, m.regiao,
       c.secao, d.secao_nome,
       c.empresas, c.pessoal_total, c.pessoal_assalariado, c.salarios_mil,
       ROUND(1000.0 * c.empresas / NULLIF(r.populacao, 0), 2)
           AS empresas_por_mil_hab
FROM cempre_municipio c
JOIN municipio m ON m.cod_ibge7 = c.cod_ibge7
LEFT JOIN (SELECT DISTINCT secao, secao_nome FROM cnae_divisao) d
       ON d.secao = c.secao
LEFT JOIN municipio_populacao_ref r
       ON r.cod_ibge7 = c.cod_ibge7 AND r.ano = c.ano;

-- ---------------------------------------------------------------
-- v_emprego_historico — série nacional mensal de admissões,
-- demissões e saldo: CAGED antigo (1999-2019) + novo CAGED (2020+),
-- com saldo acumulado 12M (fonte IPEADATA)
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_emprego_historico;
CREATE VIEW v_emprego_historico AS
SELECT competencia, fonte, admissoes, desligamentos, saldo,
       SUM(saldo) OVER (ORDER BY competencia
                        ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)
           AS saldo_12m
FROM (
    SELECT competencia, fonte, admissoes, desligamentos, saldo
    FROM emprego_nacional_hist
    WHERE fonte = 'CAGED_ANTIGO'
    UNION ALL
    SELECT competencia, fonte, admissoes, desligamentos, saldo
    FROM emprego_nacional_hist
    WHERE fonte = 'NOVO_CAGED' AND competencia >= 202001
)
ORDER BY competencia;

-- ---------------------------------------------------------------
-- v_setor_consolidado — cruzamento setorial via setor_mapa:
-- crédito BACEN (SGS) × VAB nacional (PIB municipal) × saldo CAGED
-- 12M × empresas CEMPRE, por setor consolidado.
-- Obs.: o crédito da Adm. pública está dentro de SERVICOS no SGS.
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_setor_consolidado;
CREATE VIEW v_setor_consolidado AS
WITH cred AS (
    SELECT setor, SUM(valor) AS credito_milhoes,
           MAX(competencia) AS competencia_credito
    FROM v_credito_setor
    WHERE nivel = 'SETOR'
      AND competencia = (SELECT MAX(competencia) FROM sgs_valor
                         WHERE codigo IN (SELECT codigo FROM sgs_serie
                                          WHERE grupo = 'CREDITO_SETOR'))
    GROUP BY setor
),
vab AS (
    SELECT 'AGRO' AS setor_vab, SUM(vab_agro_mil) AS vab_mil,
           MAX(ano) AS ano_vab
    FROM pib_municipio WHERE ano = (SELECT MAX(ano) FROM pib_municipio)
    UNION ALL
    SELECT 'INDUSTRIA', SUM(vab_industria_mil), MAX(ano)
    FROM pib_municipio WHERE ano = (SELECT MAX(ano) FROM pib_municipio)
    UNION ALL
    SELECT 'SERVICOS', SUM(vab_servicos_mil), MAX(ano)
    FROM pib_municipio WHERE ano = (SELECT MAX(ano) FROM pib_municipio)
    UNION ALL
    SELECT 'ADM_PUBLICA', SUM(vab_adm_mil), MAX(ano)
    FROM pib_municipio WHERE ano = (SELECT MAX(ano) FROM pib_municipio)
),
cag AS (
    SELECT d.secao, SUM(c.saldo) AS saldo_12m,
           SUM(c.admissoes) AS admissoes_12m
    FROM caged_mensal c
    JOIN cnae_divisao d ON d.divisao = c.divisao
    WHERE c.competencia > (SELECT MAX(competencia) - 100 FROM caged_mensal)
    GROUP BY d.secao
),
emp AS (
    SELECT secao, SUM(empresas) AS empresas,
           SUM(pessoal_total) AS pessoal_total
    FROM cempre_municipio
    WHERE ano = (SELECT MAX(ano) FROM cempre_municipio)
    GROUP BY secao
)
SELECT m.setor_padrao,
       c.credito_milhoes,
       c.competencia_credito,
       v.vab_mil, v.ano_vab,
       ROUND(100.0 * v.vab_mil / NULLIF((SELECT SUM(vab_mil) FROM vab), 0), 1)
           AS vab_participacao_pct,
       (SELECT SUM(cg.saldo_12m) FROM cag cg
        WHERE ',' || REPLACE(m.secoes_cnae, ' ', '') || ',' LIKE
              '%,' || cg.secao || ',%')       AS caged_saldo_12m,
       (SELECT SUM(cg.admissoes_12m) FROM cag cg
        WHERE ',' || REPLACE(m.secoes_cnae, ' ', '') || ',' LIKE
              '%,' || cg.secao || ',%')       AS caged_admissoes_12m,
       (SELECT SUM(e.empresas) FROM emp e
        WHERE ',' || REPLACE(m.secoes_cnae, ' ', '') || ',' LIKE
              '%,' || e.secao || ',%')        AS empresas,
       (SELECT SUM(e.pessoal_total) FROM emp e
        WHERE ',' || REPLACE(m.secoes_cnae, ' ', '') || ',' LIKE
              '%,' || e.secao || ',%')        AS pessoal_ocupado
FROM setor_mapa m
LEFT JOIN cred c ON c.setor = m.setor_sgs
LEFT JOIN vab v  ON v.setor_vab = m.setor_vab;

-- ---------------------------------------------------------------
-- v_atividade_uf — painel consolidado por UF: PIB, crédito
-- bancário (ESTBAN), emprego (CAGED 12M), empresas (CEMPRE)
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_atividade_uf;
CREATE VIEW v_atividade_uf AS
WITH pib AS (
    SELECT m.uf, SUM(p.pib_mil) AS pib_mil, MAX(p.ano) AS ano_pib
    FROM pib_municipio p JOIN municipio m ON m.cod_ibge7 = p.cod_ibge7
    WHERE p.ano = (SELECT MAX(ano) FROM pib_municipio)
    GROUP BY m.uf
),
pop AS (
    SELECT m.uf, SUM(r.populacao) AS populacao
    FROM municipio_populacao_ref r JOIN municipio m ON m.cod_ibge7 = r.cod_ibge7
    WHERE r.ano = (SELECT MAX(ano) FROM municipio_populacao_ref)
    GROUP BY m.uf
),
cred AS (
    SELECT uf,
           SUM(credito_total_mil) AS credito_mil,
           SUM(credito_12m_atras_mil) AS credito_12m_atras_mil,
           MAX(competencia) AS competencia_credito
    FROM v_credito_municipio
    WHERE competencia = (SELECT MAX(competencia) FROM estban_municipio)
    GROUP BY uf
),
cag AS (
    SELECT m.uf, SUM(c.saldo) AS caged_saldo_12m
    FROM caged_mensal c JOIN municipio m ON m.cod_ibge7 = c.cod_ibge7
    WHERE c.competencia > (SELECT MAX(competencia) - 100 FROM caged_mensal)
    GROUP BY m.uf
),
emp AS (
    SELECT m.uf, SUM(c.empresas) AS empresas
    FROM cempre_municipio c JOIN municipio m ON m.cod_ibge7 = c.cod_ibge7
    WHERE c.ano = (SELECT MAX(ano) FROM cempre_municipio)
    GROUP BY m.uf
)
SELECT u.uf, u.regiao,
       pop.populacao,
       pib.pib_mil, pib.ano_pib,
       ROUND(1000.0 * pib.pib_mil / NULLIF(pop.populacao, 0), 0) AS pib_per_capita,
       cred.credito_mil, cred.competencia_credito,
       ROUND(100.0 * cred.credito_mil /
             NULLIF(cred.credito_12m_atras_mil, 0) - 100, 2) AS credito_var_12m_pct,
       ROUND(100.0 * cred.credito_mil / NULLIF(pib.pib_mil, 0), 1)
           AS credito_pib_pct,
       ROUND(1000.0 * cred.credito_mil / NULLIF(pop.populacao, 0), 0)
           AS credito_per_capita,
       cag.caged_saldo_12m,
       ROUND(1000.0 * cag.caged_saldo_12m / NULLIF(pop.populacao, 0), 2)
           AS caged_12m_por_mil_hab,
       emp.empresas,
       ROUND(1000.0 * emp.empresas / NULLIF(pop.populacao, 0), 1)
           AS empresas_por_mil_hab
FROM (SELECT DISTINCT uf, regiao FROM municipio) u
LEFT JOIN pib  ON pib.uf = u.uf
LEFT JOIN pop  ON pop.uf = u.uf
LEFT JOIN cred ON cred.uf = u.uf
LEFT JOIN cag  ON cag.uf = u.uf
LEFT JOIN emp  ON emp.uf = u.uf;

-- ---------------------------------------------------------------
-- v_pib_uf_historico — PIB e composição do VAB por UF × ano
-- (agregado de pib_municipio), com participação no Brasil do ano
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_pib_uf_historico;
CREATE VIEW v_pib_uf_historico AS
WITH base AS (
    SELECT m.uf, m.regiao, p.ano,
           SUM(p.pib_mil) AS pib_mil,
           SUM(p.vab_agro_mil) AS vab_agro_mil,
           SUM(p.vab_industria_mil) AS vab_industria_mil,
           SUM(p.vab_servicos_mil) AS vab_servicos_mil,
           SUM(p.vab_adm_mil) AS vab_adm_mil,
           SUM(r.populacao) AS populacao
    FROM pib_municipio p
    JOIN municipio m ON m.cod_ibge7 = p.cod_ibge7
    LEFT JOIN municipio_populacao_ref r
           ON r.cod_ibge7 = p.cod_ibge7 AND r.ano = p.ano
    GROUP BY m.uf, m.regiao, p.ano
)
SELECT b.uf, b.regiao, b.ano, b.pib_mil, b.populacao,
       ROUND(1000.0 * b.pib_mil / NULLIF(b.populacao, 0), 0) AS pib_per_capita,
       ROUND(100.0 * b.vab_agro_mil /
             NULLIF(b.vab_agro_mil + b.vab_industria_mil + b.vab_servicos_mil + b.vab_adm_mil, 0), 1)
           AS pct_agro,
       ROUND(100.0 * b.vab_industria_mil /
             NULLIF(b.vab_agro_mil + b.vab_industria_mil + b.vab_servicos_mil + b.vab_adm_mil, 0), 1)
           AS pct_industria,
       ROUND(100.0 * b.vab_servicos_mil /
             NULLIF(b.vab_agro_mil + b.vab_industria_mil + b.vab_servicos_mil + b.vab_adm_mil, 0), 1)
           AS pct_servicos,
       ROUND(100.0 * b.pib_mil /
             NULLIF((SELECT SUM(b2.pib_mil) FROM base b2 WHERE b2.ano = b.ano), 0), 2)
           AS participacao_brasil_pct
FROM base b;

-- ---------------------------------------------------------------
-- v_atividade_cnae — painel nacional por seção CNAE: empresas e
-- pessoal (CEMPRE, último ano) + saldo/admissões de emprego (CAGED
-- 12M), com participação % sobre o total de empresas
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_atividade_cnae;
CREATE VIEW v_atividade_cnae AS
WITH secoes AS (
    SELECT DISTINCT secao, secao_nome FROM cnae_divisao
),
emp AS (
    SELECT secao, SUM(empresas) AS empresas, SUM(pessoal_total) AS pessoal_total,
           SUM(salarios_mil) AS salarios_mil
    FROM cempre_municipio
    WHERE ano = (SELECT MAX(ano) FROM cempre_municipio)
    GROUP BY secao
),
cag AS (
    SELECT d.secao, SUM(c.saldo) AS caged_saldo_12m, SUM(c.admissoes) AS admissoes_12m
    FROM caged_mensal c
    JOIN cnae_divisao d ON d.divisao = c.divisao
    WHERE c.competencia > (SELECT MAX(competencia) - 100 FROM caged_mensal)
    GROUP BY d.secao
)
SELECT s.secao, s.secao_nome,
       emp.empresas, emp.pessoal_total, emp.salarios_mil,
       cag.caged_saldo_12m, cag.admissoes_12m,
       ROUND(100.0 * emp.empresas / NULLIF((SELECT SUM(empresas) FROM emp), 0), 2)
           AS participacao_empresas_pct
FROM secoes s
LEFT JOIN emp ON emp.secao = s.secao
LEFT JOIN cag ON cag.secao = s.secao
WHERE emp.empresas IS NOT NULL OR cag.caged_saldo_12m IS NOT NULL;

-- ---------------------------------------------------------------
-- v_atividade_cnae_uf — empresas por seção CNAE × UF (último ano
-- CEMPRE) — cruzamento para filtro do mapa regional
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_atividade_cnae_uf;
CREATE VIEW v_atividade_cnae_uf AS
SELECT m.uf, c.secao,
       (SELECT secao_nome FROM cnae_divisao WHERE secao = c.secao LIMIT 1) AS secao_nome,
       c.ano, SUM(c.empresas) AS empresas, SUM(c.pessoal_total) AS pessoal_total
FROM cempre_municipio c
JOIN municipio m ON m.cod_ibge7 = c.cod_ibge7
WHERE c.ano = (SELECT MAX(ano) FROM cempre_municipio)
GROUP BY m.uf, c.secao, c.ano;

-- ---------------------------------------------------------------
-- v_ranking_municipios — visão multi-métrica por município (últimas
-- posições de cada fonte): PIB, crédito, penetração, emprego, empresas
-- ---------------------------------------------------------------
DROP VIEW IF EXISTS v_ranking_municipios;
CREATE VIEW v_ranking_municipios AS
WITH pib AS (
    SELECT cod_ibge7, pib_mil, pib_per_capita, rank_pib_brasil
    FROM v_pib_municipio WHERE ano = (SELECT MAX(ano) FROM pib_municipio)
),
cred AS (
    SELECT cod_ibge7, credito_total_mil, credito_var_12m_pct,
           credito_per_capita, rank_credito_brasil
    FROM v_credito_municipio_pc
),
pen AS (
    SELECT cod_ibge7, credito_pib_pct FROM v_penetracao_credito
),
cag AS (
    SELECT cod_ibge7, saldo_12m AS caged_saldo_12m, saldo_12m_por_mil_hab
    FROM v_emprego_municipio
    WHERE competencia = (SELECT MAX(competencia) FROM caged_mensal)
),
emp AS (
    SELECT cod_ibge7, SUM(empresas) AS empresas
    FROM cempre_municipio
    WHERE ano = (SELECT MAX(ano) FROM cempre_municipio)
    GROUP BY cod_ibge7
),
pop AS (
    SELECT cod_ibge7, populacao FROM municipio_populacao_ref
    WHERE ano = (SELECT MAX(ano) FROM municipio_populacao_ref)
)
SELECT m.cod_ibge7, m.nome, m.uf, m.regiao, m.capital,
       pop.populacao,
       pib.pib_mil, pib.pib_per_capita, pib.rank_pib_brasil,
       cred.credito_total_mil, cred.credito_var_12m_pct,
       cred.credito_per_capita, cred.rank_credito_brasil,
       pen.credito_pib_pct,
       cag.caged_saldo_12m, cag.saldo_12m_por_mil_hab,
       emp.empresas,
       ROUND(1000.0 * emp.empresas / NULLIF(pop.populacao, 0), 1)
           AS empresas_por_mil_hab
FROM municipio m
LEFT JOIN pop  ON pop.cod_ibge7 = m.cod_ibge7
LEFT JOIN pib  ON pib.cod_ibge7 = m.cod_ibge7
LEFT JOIN cred ON cred.cod_ibge7 = m.cod_ibge7
LEFT JOIN pen  ON pen.cod_ibge7 = m.cod_ibge7
LEFT JOIN cag  ON cag.cod_ibge7 = m.cod_ibge7
LEFT JOIN emp  ON emp.cod_ibge7 = m.cod_ibge7;

-- =====================================================================
-- 3. MATERIALIZAÇÃO (velocidade): as views que varrem as tabelas
-- municipais mensais (ESTBAN/CAGED, milhões de linhas) são calculadas
-- UMA vez por carga e viram tabelas indexadas; cada view é recriada
-- como atalho para a tabela, mantendo compatibilidade de nomes.
-- A ordem importa: views que dependem de outras materializam depois.
-- =====================================================================

DROP TABLE IF EXISTS m_credito_municipio;
CREATE TABLE m_credito_municipio AS SELECT * FROM v_credito_municipio;
CREATE INDEX ix_m_cred_mun ON m_credito_municipio (cod_ibge7, competencia);
CREATE INDEX ix_m_cred_comp ON m_credito_municipio (competencia);
DROP VIEW v_credito_municipio;
CREATE VIEW v_credito_municipio AS SELECT * FROM m_credito_municipio;

DROP TABLE IF EXISTS m_credito_municipio_pc;
CREATE TABLE m_credito_municipio_pc AS SELECT * FROM v_credito_municipio_pc;
CREATE UNIQUE INDEX ix_m_cred_pc ON m_credito_municipio_pc (cod_ibge7);
DROP VIEW v_credito_municipio_pc;
CREATE VIEW v_credito_municipio_pc AS SELECT * FROM m_credito_municipio_pc;

DROP TABLE IF EXISTS m_penetracao_credito;
CREATE TABLE m_penetracao_credito AS SELECT * FROM v_penetracao_credito;
CREATE UNIQUE INDEX ix_m_pen ON m_penetracao_credito (cod_ibge7);
DROP VIEW v_penetracao_credito;
CREATE VIEW v_penetracao_credito AS SELECT * FROM m_penetracao_credito;

DROP TABLE IF EXISTS m_emprego_setor;
CREATE TABLE m_emprego_setor AS SELECT * FROM v_emprego_setor;
CREATE INDEX ix_m_emp_setor ON m_emprego_setor (divisao, competencia);
DROP VIEW v_emprego_setor;
CREATE VIEW v_emprego_setor AS SELECT * FROM m_emprego_setor;

DROP TABLE IF EXISTS m_emprego_municipio;
CREATE TABLE m_emprego_municipio AS SELECT * FROM v_emprego_municipio;
CREATE INDEX ix_m_emp_mun ON m_emprego_municipio (cod_ibge7, competencia);
CREATE INDEX ix_m_emp_comp ON m_emprego_municipio (competencia);
DROP VIEW v_emprego_municipio;
CREATE VIEW v_emprego_municipio AS SELECT * FROM m_emprego_municipio;

DROP TABLE IF EXISTS m_empresas_municipio;
CREATE TABLE m_empresas_municipio AS SELECT * FROM v_empresas_municipio;
CREATE INDEX ix_m_empr_mun ON m_empresas_municipio (cod_ibge7, ano);
DROP VIEW v_empresas_municipio;
CREATE VIEW v_empresas_municipio AS SELECT * FROM m_empresas_municipio;

DROP TABLE IF EXISTS m_atividade_uf;
CREATE TABLE m_atividade_uf AS SELECT * FROM v_atividade_uf;
DROP VIEW v_atividade_uf;
CREATE VIEW v_atividade_uf AS SELECT * FROM m_atividade_uf;

DROP TABLE IF EXISTS m_ranking_municipios;
CREATE TABLE m_ranking_municipios AS SELECT * FROM v_ranking_municipios;
CREATE UNIQUE INDEX ix_m_rank ON m_ranking_municipios (cod_ibge7);
DROP VIEW v_ranking_municipios;
CREATE VIEW v_ranking_municipios AS SELECT * FROM m_ranking_municipios;

DROP TABLE IF EXISTS m_setor_consolidado;
CREATE TABLE m_setor_consolidado AS SELECT * FROM v_setor_consolidado;
DROP VIEW v_setor_consolidado;
CREATE VIEW v_setor_consolidado AS SELECT * FROM m_setor_consolidado;

ANALYZE;
