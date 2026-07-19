@echo off
rem Atualiza o Sistema Atividade Economica: descobre series novas de credito
rem detalhado, baixa dados das fontes oficiais (BACEN, IBGE, MTE, CNJ),
rem recarrega o banco, gera os relatorios Excel, os dashboards HTML avulsos
rem (credito, mapa regional, PIB por setor, recuperacao judicial,
rem instituicoes financeiras) e o portal unico (index.html).
setlocal
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
if not defined ATIV_DADOS_DIR set "ATIV_DADOS_DIR=C:\SistemaAtividade-dados"

echo === 1/10 Descobrindo series de credito detalhado (BCB) ===
"%PY%" "%~dp0src\descobrir_credito_detalhado.py"
if errorlevel 1 echo (aviso) descoberta falhou; mantendo semente anterior.

echo === 2/10 Baixando dados (BACEN, IBGE, CAGED, CNJ) ===
"%PY%" "%~dp0src\baixar_dados.py" %*
if errorlevel 1 goto :erro

echo === 3/10 Carregando banco de dados ===
"%PY%" "%~dp0src\carregar_dados.py"
if errorlevel 1 goto :erro

echo === 4/10 Gerando relatorios ===
"%PY%" "%~dp0src\gerar_relatorios.py"
if errorlevel 1 goto :erro

echo === 5/10 Gerando dashboard de credito ===
"%PY%" "%~dp0src\gerar_dashboard.py"
if errorlevel 1 goto :erro

echo === 6/10 Gerando mapa regional ===
"%PY%" "%~dp0src\gerar_mapa.py"
if errorlevel 1 goto :erro

echo === 7/10 Gerando PIB por setor ===
"%PY%" "%~dp0src\gerar_pib_setorial.py"
if errorlevel 1 goto :erro

echo === 8/10 Gerando dashboard de Recuperacao Judicial ===
"%PY%" "%~dp0src\gerar_recuperacao_judicial.py"
if errorlevel 1 goto :erro

echo === 9/10 Gerando dashboard de Instituicoes Financeiras ===
"%PY%" "%~dp0src\gerar_instituicoes_financeiras.py"
if errorlevel 1 goto :erro

echo === 10/10 Gerando portal unico (index.html) ===
"%PY%" "%~dp0src\gerar_sistema.py"
if errorlevel 1 goto :erro

echo.
echo Atualizacao concluida.
goto :eof

:erro
echo.
echo ERRO durante a atualizacao. Veja as mensagens acima.
exit /b 1
