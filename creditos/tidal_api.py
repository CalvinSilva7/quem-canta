"""A API oficial do Tidal (openapi.tidal.com), só para achar o que fotografar: dado o ISRC de uma gravação, as faixas
do Tidal com esse ISRC, o álbum e os artistas de cada uma; dado um artista, os clipes (vídeos) dele. Os créditos
continuam lidos e fotografados nas páginas do próprio Tidal, sem login (`tidal.py`); a API não decide nada sobre
crédito.

A busca do site não funciona sem login (abre em branco) e o login pelo navegador do app é bloqueado. A API é o
caminho que o Tidal oferece para programas: chave própria do escritório (Client ID e Client Secret, criados em
developer.tidal.com), guardada só no computador, em variável de ambiente ou no arquivo `chaves-tidal.txt` da pasta de
dados. O ISRC vem da Deezer, que já o dá para cada faixa. Os clipes têm ISRC próprio (não o do áudio): por isso são
achados pelos artistas das faixas.
"""

import base64
import json
from urllib.parse import parse_qsl
import os
import time
from pathlib import Path

import requests
from . import disco

TOKEN = "https://auth.tidal.com/v1/oauth2/token"
API = "https://openapi.tidal.com/v2/"
FAIXAS = API + "tracks"
PAIS = "BR"
ARQUIVO_DAS_CHAVES = "chaves-tidal.txt"
POR_CONSULTA = 20  # quantos clipes pedir de uma vez pelo identificador


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
        self.pasta = Path(cache) if cache else None
        self.intervalo, self.tentativas = intervalo, tentativas
        self.sessao = sessao or requests.Session()
        self.requisicoes = 0
        self._token, self._expira, self._ultima = "", 0.0, 0.0
        self._guardadas = self._ler("_isrc.json")
        # _clipes_v2: a lista anterior (_clipes.json) repetia a primeira página de cada artista (o cursor da página
        # seguinte ia codificado duas vezes) e é descartada
        self._clipes = self._ler("_clipes_v2.json")

    def _ler(self, nome) -> dict:
        arquivo = self.pasta / nome if self.pasta else None
        return disco.ler_json(arquivo, {})

    def _gravar(self, nome, dados):
        if self.pasta:
            self.pasta.mkdir(parents=True, exist_ok=True)
            disco.gravar_json((self.pasta / nome), dados)

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

    def _get(self, url: str, parametros) -> dict:
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
                resposta = self.sessao.get(url, params=parametros, timeout=30,
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
            return resposta.json()
        raise ErroNaAPI(f"{erro} após {self.tentativas} tentativas")

    def faixas(self, isrc: str) -> list[dict]:
        """As faixas do Tidal com este ISRC: [{"id", "album", "titulo", "versao", "artistas"}] (artistas: os
        identificadores). Nenhuma: []."""
        isrc = str(isrc or "").strip().upper()
        if not isrc:
            return []
        guardadas = self._guardadas.get(isrc)
        if guardadas is not None and all("artistas" in f for f in guardadas):  # as de antes da 0.5.52 não têm artistas
            return guardadas
        if self.filtro:
            self.filtro.conferir(isrc)
        dados = self._get(FAIXAS, [("countryCode", PAIS), ("filter[isrc]", isrc), ("include", "albums,artists")])
        achadas = []
        for faixa in dados.get("data", []):
            relacoes = faixa.get("relationships") or {}
            albuns = (relacoes.get("albums") or {}).get("data") or []
            artistas = [str(a.get("id", "")) for a in (relacoes.get("artists") or {}).get("data") or []]
            atributos = faixa.get("attributes") or {}
            for album in albuns:
                achadas.append({"id": str(faixa.get("id", "")), "album": str(album.get("id", "")), "titulo": atributos.get("title", ""),
                                "versao": atributos.get("version") or "", "artistas": artistas})
        self._guardadas[isrc] = achadas
        self._gravar("_isrc.json", self._guardadas)
        return achadas

    def clipes(self, artista: str) -> list[dict]:
        """Os clipes (vídeos) de um artista: [{"id", "titulo", "isrc", "artistas"}] (artistas: os nomes)."""
        artista = str(artista)
        if artista in self._clipes:
            return self._clipes[artista]
        ids, caminho, parametros = [], f"artists/{artista}/relationships/videos", [("countryCode", PAIS)]
        for _ in range(60):  # páginas da lista (20 clipes cada); o limite só evita laço sem fim
            dados = self._get(API + caminho, parametros)
            antes = len(ids)
            ids = list(dict.fromkeys(ids + [str(v.get("id")) for v in dados.get("data", []) if v.get("type") == "videos"]))
            seguinte = (dados.get("links") or {}).get("next")
            if not seguinte or len(ids) == antes:  # sem próxima página, ou a página só repetiu clipes já vistos
                break
            caminho, _, consulta = seguinte.lstrip("/").partition("?")
            parametros = parse_qsl(consulta)  # o endereço vem codificado ("page%5Bcursor%5D"): decodifica antes de reenviar
        clipes = []
        for inicio in range(0, len(ids), POR_CONSULTA):
            lote = ids[inicio:inicio + POR_CONSULTA]
            dados = self._get(API + "videos", [("countryCode", PAIS), ("include", "artists")] + [("filter[id]", i) for i in lote])
            nomes = {str(a.get("id")): (a.get("attributes") or {}).get("name", "") for a in dados.get("included", []) if a.get("type") == "artists"}
            for video in dados.get("data", []):
                atributos = video.get("attributes") or {}
                titulo = atributos.get("title", "") + (f" ({atributos['version']})" if atributos.get("version") else "")
                donos = [nomes.get(str(a.get("id")), "") for a in ((video.get("relationships") or {}).get("artists") or {}).get("data") or []]
                clipes.append({"id": str(video.get("id")), "titulo": titulo, "isrc": atributos.get("isrc") or "",
                               "artistas": [n for n in donos if n]})
        self._clipes[artista] = clipes
        self._gravar("_clipes_v2.json", self._clipes)
        return clipes
