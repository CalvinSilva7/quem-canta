"""Persistência em SQLite: cache de consultas e correções manuais."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

CAMINHO_PADRAO = Path(
    os.environ.get("QUEMCANTA_DB", Path(__file__).resolve().parent.parent / "dados" / "quem_canta.db")
)

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS cache_consultas (
    titulo_norm     TEXT NOT NULL,
    compositor_norm TEXT NOT NULL,
    resultado_json  TEXT NOT NULL,
    criado_em       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (titulo_norm, compositor_norm)
);
CREATE TABLE IF NOT EXISTS cache_artistas (
    nome_norm TEXT PRIMARY KEY,
    ids_json  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache_obras_compositor (
    nome_norm  TEXT PRIMARY KEY,
    obras_json TEXT NOT NULL,
    criado_em  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS cache_discografias (
    nome_norm   TEXT PRIMARY KEY,
    faixas_json TEXT NOT NULL,
    criado_em   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS correcoes (
    titulo_norm     TEXT NOT NULL,
    compositor_norm TEXT NOT NULL,
    cantor          TEXT NOT NULL,
    atualizado_em   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (titulo_norm, compositor_norm)
);
"""


class Banco:
    """Todas as chaves já chegam normalizadas (ver matching.chave_consulta).

    Abre uma conexão por operação: o Streamlit roda cada execução em uma
    thread diferente e conexões SQLite não devem ser compartilhadas entre elas.
    """

    def __init__(self, caminho=None):
        self.caminho = str(caminho or CAMINHO_PADRAO)
        Path(self.caminho).parent.mkdir(parents=True, exist_ok=True)
        self._executar_script(_ESQUEMA)

    def _conectar(self):
        return sqlite3.connect(self.caminho, timeout=30)

    def _executar_script(self, sql):
        with closing(self._conectar()) as con, con:
            con.executescript(sql)

    def _executar(self, sql, parametros=()):
        with closing(self._conectar()) as con, con:
            return con.execute(sql, parametros).fetchall()

    # --- cache de consultas -------------------------------------------------

    def obter_cache(self, titulo_norm, compositor_norm):
        linhas = self._executar(
            "SELECT resultado_json FROM cache_consultas WHERE titulo_norm = ? AND compositor_norm = ?",
            (titulo_norm, compositor_norm),
        )
        return json.loads(linhas[0][0]) if linhas else None

    def salvar_cache(self, titulo_norm, compositor_norm, resultado: dict):
        self._executar(
            "INSERT OR REPLACE INTO cache_consultas (titulo_norm, compositor_norm, resultado_json) VALUES (?, ?, ?)",
            (titulo_norm, compositor_norm, json.dumps(resultado, ensure_ascii=False)),
        )

    def limpar_cache(self):
        self._executar("DELETE FROM cache_consultas")
        self._executar("DELETE FROM cache_artistas")
        self._executar("DELETE FROM cache_obras_compositor")
        self._executar("DELETE FROM cache_discografias")

    def contar_cache(self) -> int:
        return self._executar("SELECT COUNT(*) FROM cache_consultas")[0][0]

    # --- cache de artistas ({id do MusicBrainz: [nome e aliases]}) ---------

    def obter_artista(self, nome_norm):
        linhas = self._executar("SELECT ids_json FROM cache_artistas WHERE nome_norm = ?", (nome_norm,))
        return json.loads(linhas[0][0]) if linhas else None

    def salvar_artista(self, nome_norm, artistas: dict):
        self._executar(
            "INSERT OR REPLACE INTO cache_artistas (nome_norm, ids_json) VALUES (?, ?)",
            (nome_norm, json.dumps(artistas, ensure_ascii=False)),
        )

    # --- cache das obras de um compositor (modo relatório) -----------------

    def obter_obras(self, nome_norm):
        linhas = self._executar("SELECT obras_json FROM cache_obras_compositor WHERE nome_norm = ?", (nome_norm,))
        return json.loads(linhas[0][0]) if linhas else None

    def salvar_obras(self, nome_norm, dados: dict):
        self._executar(
            "INSERT OR REPLACE INTO cache_obras_compositor (nome_norm, obras_json) VALUES (?, ?)",
            (nome_norm, json.dumps(dados, ensure_ascii=False)),
        )

    # --- cache da discografia de um nome artístico (modo relatório) --------

    def obter_discografia(self, nome_norm):
        linhas = self._executar("SELECT faixas_json FROM cache_discografias WHERE nome_norm = ?", (nome_norm,))
        return json.loads(linhas[0][0]) if linhas else None

    def salvar_discografia(self, nome_norm, dados: dict):
        self._executar(
            "INSERT OR REPLACE INTO cache_discografias (nome_norm, faixas_json) VALUES (?, ?)",
            (nome_norm, json.dumps(dados, ensure_ascii=False)),
        )

    # --- correções manuais --------------------------------------------------

    def obter_correcao(self, titulo_norm, compositor_norm):
        linhas = self._executar(
            "SELECT cantor FROM correcoes WHERE titulo_norm = ? AND compositor_norm = ?",
            (titulo_norm, compositor_norm),
        )
        return linhas[0][0] if linhas else None

    def salvar_correcao(self, titulo_norm, compositor_norm, cantor: str):
        self._executar(
            "INSERT OR REPLACE INTO correcoes (titulo_norm, compositor_norm, cantor) VALUES (?, ?, ?)",
            (titulo_norm, compositor_norm, cantor),
        )

    def apagar_correcao(self, titulo_norm, compositor_norm):
        self._executar(
            "DELETE FROM correcoes WHERE titulo_norm = ? AND compositor_norm = ?",
            (titulo_norm, compositor_norm),
        )

    def listar_correcoes(self) -> list[tuple]:
        return self._executar(
            "SELECT titulo_norm, compositor_norm, cantor, atualizado_em FROM correcoes ORDER BY atualizado_em DESC"
        )
