"""A API oficial do Tidal (openapi.tidal.com), só para achar a faixa: dado o ISRC de uma gravação, ela devolve as
faixas do Tidal com esse ISRC e o álbum de cada uma. Os créditos continuam lidos e fotografados na página do
próprio Tidal, sem login (`tidal.py`); a API não decide nada sobre crédito.

A busca do site não funciona sem login (abre em branco) e o login pelo navegador do app é bloqueado. A API é o
caminho que o Tidal oferece para programas: chave própria do escritório (Client ID e Client Secret, criados em
developer.tidal.com), guardada só no computador, em variável de ambiente ou no arquivo `chaves-tidal.txt` da pasta de
dados. O ISRC vem da Deezer, que já o dá para cada faixa.
"""

import base64
import json
import os
import time
from pathlib import Path

import requests

TOKEN = "https://auth.tidal.com/v1/oauth2/token"
FAIXAS = "https://openapi.tidal.com/v2/tracks"
PAIS = "BR"
ARQUIVO_DAS_CHAVES = "chaves-tidal.txt"


class SemChaves(Exception):
    pass


class ErroNaAPI(Exception):
    pass


def chaves(pasta_de_dados) -> tuple[str, str] | None:
    """(Client ID, Client Secret): das variáveis TIDAL_CLIENT_ID e TIDAL_CLIENT_SECRET ou, sem elas, do arquivo
    `chaves-tidal.txt` da pasta de dados (linhas "TIDAL_CLIENT_ID=..." e "TIDAL_CLIENT_SECRET=..."). Sem as duas, None."""
    valores = {k: os.environ.get(k, "") for k in ("TIDAL_CLIENT_ID", "TIDAL_CLIENT_SECRET")}
    arquivo = Path(pasta_de_dados) / ARQUIVO_DAS_CHAVES
    if not all(valores.values()) and arquivo.is_file():
        for linha in arquivo.read_text(encoding="utf-8-sig").splitlines():
            nome, _, valor = linha.partition("=")
            if nome.strip() in valores and not valores[nome.strip()]:
                valores[nome.strip()] = valor.strip()
    return (valores["TIDAL_CLIENT_ID"], valores["TIDAL_CLIENT_SECRET"]) if all(valores.values()) else None


class TidalAPI:
    def __init__(self, chave: tuple[str, str], filtro=None, cache=None, intervalo=0.5, tentativas=3, sessao=None):
        self.chave, self.filtro = chave, filtro  # creditos.filtro.FiltroDeSaida, conferido antes de cada consulta
        self.cache = Path(cache) / "_isrc.json" if cache else None
        self.intervalo, self.tentativas = intervalo, tentativas
        self.sessao = sessao or requests.Session()
        self.requisicoes = 0
        self._token, self._expira, self._ultima = "", 0.0, 0.0
        self._guardadas = json.loads(self.cache.read_text(encoding="utf-8")) if self.cache and self.cache.exists() else {}

    def _autorizacao(self) -> str:
        if not self._token or time.monotonic() > self._expira - 60:
            basico = base64.b64encode(f"{self.chave[0]}:{self.chave[1]}".encode()).decode()
            resposta = self.sessao.post(TOKEN, data={"grant_type": "client_credentials"}, timeout=30,
                                        headers={"Authorization": f"Basic {basico}"})
            self.requisicoes += 1
            if resposta.status_code != 200:
                raise ErroNaAPI(f"o Tidal recusou as chaves da API (HTTP {resposta.status_code}): confira o arquivo {ARQUIVO_DAS_CHAVES}")
            dados = resposta.json()
            self._token, self._expira = dados["access_token"], time.monotonic() + float(dados.get("expires_in", 3600))
        return self._token

    def faixas(self, isrc: str) -> list[dict]:
        """As faixas do Tidal com este ISRC: [{"id", "album", "titulo", "versao"}]. Nenhuma: []."""
        isrc = str(isrc or "").strip().upper()
        if not isrc:
            return []
        if isrc in self._guardadas:
            return self._guardadas[isrc]
        if self.filtro:
            self.filtro.conferir(isrc)
        parametros = [("countryCode", PAIS), ("filter[isrc]", isrc), ("include", "albums")]
        erro = ""
        for tentativa in range(self.tentativas):
            falta = self.intervalo - (time.monotonic() - self._ultima)
            if falta > 0:
                time.sleep(falta)
            if tentativa:
                time.sleep(2 ** tentativa)
            self._ultima = time.monotonic()
            self.requisicoes += 1
            try:
                resposta = self.sessao.get(FAIXAS, params=parametros, timeout=30,
                                           headers={"Authorization": f"Bearer {self._autorizacao()}", "Accept": "application/vnd.api+json"})
            except requests.RequestException as e:
                erro = type(e).__name__
                continue
            if resposta.status_code == 401:
                self._token = ""
                erro = "HTTP 401"
                continue
            if resposta.status_code in (429, 500, 502, 503, 504):
                erro = f"HTTP {resposta.status_code}"
                continue
            if resposta.status_code != 200:
                raise ErroNaAPI(f"HTTP {resposta.status_code}")
            achadas = []
            for faixa in resposta.json().get("data", []):
                albuns = ((faixa.get("relationships") or {}).get("albums") or {}).get("data") or []
                atributos = faixa.get("attributes") or {}
                for album in albuns:
                    achadas.append({"id": str(faixa.get("id", "")), "album": str(album.get("id", "")),
                                    "titulo": atributos.get("title", ""), "versao": atributos.get("version") or ""})
            self._guardadas[isrc] = achadas
            if self.cache:
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                self.cache.write_text(json.dumps(self._guardadas, ensure_ascii=False), encoding="utf-8")
            return achadas
        raise ErroNaAPI(f"{erro} após {self.tentativas} tentativas")
