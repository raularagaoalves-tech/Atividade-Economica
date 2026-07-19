# -*- coding: utf-8 -*-
"""Caminhos de dados do sistema — fonte única, usada por todos os scripts.

Por padrão os dados ficam em <raiz do projeto>/data (bom para desenvolvimento).
Em produção, defina a variável de ambiente ATIV_DADOS_DIR apontando para um
diretório fora de qualquer pasta sincronizada (OneDrive, Dropbox etc.): a
sincronização de arquivo pode corromper um SQLite em uso no meio de uma escrita,
além de tentar subir para a nuvem milhões de linhas de dados brutos (ESTBAN,
CAGED) sem necessidade.
"""
from __future__ import annotations

import os
from pathlib import Path

RAIZ_PROJETO = Path(__file__).resolve().parent.parent

_dir_env = os.environ.get("ATIV_DADOS_DIR")
DIR_DADOS = Path(_dir_env).expanduser().resolve() if _dir_env else RAIZ_PROJETO / "data"

DIR_DB = DIR_DADOS / "db"
DB_ATIVIDADE = DIR_DB / "atividade.db"
DIR_RAW = DIR_DADOS / "raw"
DIR_MANUAL = RAIZ_PROJETO / "data" / "manual"
