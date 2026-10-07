"""Coleta na Deezer: os créditos vêm do estado que a própria página do álbum publica.

A página www.deezer.com/br/album/<id> carrega window.__DZR_APP_STATE__ com, por
faixa, os contribuidores (composer, author), o ISRC e, por álbum, o selo
(LABEL_NAME, o fornecedor do metadado). É o mesmo dado que a tela mostra em
"Consulte créditos musicais", e não depende de menu, clique nem login. A API
pública (api.deezer.com) só é usada para achar artistas, álbuns e faixas.

Guardas: o álbum devolvido tem de ser o pedido; o número de faixas lidas tem de
bater com o declarado; falha de rede, de leitura ou pedido de login é ERRO, e
nunca "sem créditos".
"""

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from cantor.matching import nomes_parecidos

API = "https://api.deezer.com/"
PAGINA_DO_ALBUM = "https://www.deezer.com/br/album/{}"
LINK_DA_FAIXA = "https://www.deezer.com/track/{}"
NAVEGADOR = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/126.0 Safari/537.36")
PAPEIS_DE_AUTOR = ("composer", "author", "lyricist", "writer", "composerlyricist")
VERSAO_DA_LEITURA = 1  # muda quando muda o que se guarda de cada álbum
COTA_EXCEDIDA = 4


class ErroDeColeta(Exception):
    """A plataforma não respondeu como esperado. Quem chama registra como erro técnico."""


@dataclass
class Faixa:
    id: str = ""
    titulo: str = ""
    interprete: str = ""
    isrc: str = ""
    creditos: list[str] = field(default_factory=list)  # compositores e autores, na ordem em que a página traz
    papeis: dict = field(default_factory=dict)  # todos os contribuidores, por papel, como a página publica
    duracao: int = 0

    @property
    def link(self) -> str:
        return LINK_DA_FAIXA.format(self.id)


@dataclass
class Album:
    id: str = ""
    titulo: str = ""
    artista: str = ""
    fornecedor: str = ""  # LABEL_NAME
    upc: str = ""
    lancamento: str = ""
    faixas_declaradas: int = 0
    faixas: list[Faixa] = field(default_factory=list)
    erro: str = ""  # preenchido = o álbum não pôde ser lido; nada se conclui das faixas
    aviso: str = ""  # ex.: CONTAGEM_DIVERGENTE


class Deezer:
    def __init__(self, filtro=None, cache=None, intervalo=0.4, tentativas=3, sessao=None):
        self.filtro = filtro  # creditos.filtro.FiltroDeSaida, conferido antes de cada requisição
        self.cache = Path(cache) if cache else None
        self.intervalo = intervalo
        self.tentativas = tentativas
        self.sessao = sessao or requests.Session()
        self.requisicoes = 0
        self._ultima = 0.0

    # --- rede ---------------------------------------------------------------

    def _get(self, url, parametros=None, pagina=False):
        if self.filtro:
            self.filtro.conferir(url, *(str(v) for v in (parametros or {}).values()))
        cabecalhos = {"User-Agent": NAVEGADOR, "Accept-Language": "pt-BR,pt;q=0.9"} if pagina else {"Accept": "application/json"}
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
                resposta = self.sessao.get(url, params=parametros, headers=cabecalhos, timeout=30)
            except requests.RequestException as e:
                erro = type(e).__name__
                continue
            if resposta.status_code in (429, 500, 502, 503, 504):
                erro = f"HTTP {resposta.status_code}"
                continue
            if resposta.status_code != 200:
                raise ErroDeColeta(f"HTTP {resposta.status_code}")
            if pagina:
                if "account.deezer.com" in resposta.url or "/login" in resposta.url:
                    raise ErroDeColeta("a Deezer pediu login; a coleta para aqui (não se contorna login)")
                return resposta.text
            try:
                dados = resposta.json()
            except ValueError:
                erro = "resposta que não é JSON"
                continue
            falha = dados.get("error") if isinstance(dados, dict) else None
            if isinstance(falha, dict):
                erro = f"Deezer: {falha.get('message', 'erro')}"
                if falha.get("code") == COTA_EXCEDIDA:
                    continue
                raise ErroDeColeta(erro)
            return dados
        raise ErroDeColeta(f"{erro} após {self.tentativas} tentativas")

    def _paginas(self, caminho, maximo=1000, **parametros):
        itens = []
        while len(itens) < maximo:
            dados = self._get(API + caminho, {**parametros, "limit": 100, "index": len(itens)})
            lote = dados.get("data", [])
            itens += lote
            if not lote or not dados.get("next"):
                break
        return itens

    # --- descoberta (API pública) ------------------------------------------

    def artistas(self, nome: str, maximo=3) -> list[dict]:
        """Artistas da Deezer com esse nome: [{"id", "nome", "albuns", "fas"}], os mais seguidos primeiro."""
        achados = self._get(API + "search/artist", {"q": nome, "limit": 25}).get("data", [])
        iguais = [a for a in achados if nomes_parecidos(nome, a.get("name"), parcial=False)]
        iguais.sort(key=lambda a: -(a.get("nb_fan") or 0))
        return [{"id": str(a["id"]), "nome": a.get("name", ""), "albuns": a.get("nb_album") or 0, "fas": a.get("nb_fan") or 0}
                for a in iguais[:maximo]]

    def albuns_do_artista(self, artista_id: str) -> list[str]:
        return [str(a["id"]) for a in self._paginas(f"artist/{artista_id}/albums")]

    def buscar_faixas(self, consulta: str, maximo=300) -> list[dict]:
        """Faixas que a busca da Deezer devolve: [{"id", "titulo", "interprete", "album_id"}].

        Vai além da primeira página: em título comum, a gravação procurada costuma estar longe do topo.
        """
        guardadas = self.cache / "_buscas.json" if self.cache else None
        buscas = json.loads(guardadas.read_text(encoding="utf-8")) if guardadas and guardadas.exists() else {}
        if consulta in buscas:
            return buscas[consulta]
        itens = self._paginas("search", maximo=maximo, q=consulta)
        faixas = [{"id": str(i.get("id", "")), "titulo": i.get("title", ""), "interprete": (i.get("artist") or {}).get("name", ""),
                   "album_id": str((i.get("album") or {}).get("id", ""))} for i in itens]
        if guardadas:
            buscas[consulta] = faixas
            self.cache.mkdir(parents=True, exist_ok=True)
            guardadas.write_text(json.dumps(buscas, ensure_ascii=False), encoding="utf-8")
        return faixas

    # --- créditos (estado da página do álbum) ------------------------------

    def album(self, album_id: str) -> Album:
        """Lê um álbum. Qualquer falha volta em `erro`: quem chama não pode tratar como álbum sem créditos."""
        album_id = str(album_id)
        guardado = self._do_cache(album_id)
        if guardado:
            return guardado
        try:
            album = interpretar_pagina(self._get(PAGINA_DO_ALBUM.format(album_id), pagina=True), album_id)
        except ErroDeColeta as e:
            return Album(id=album_id, erro=str(e))  # erro não vai para o cache: na próxima rodada tenta de novo
        self._para_cache(album)
        return album

    def _do_cache(self, album_id):
        arquivo = self.cache / f"{album_id}.json" if self.cache else None
        if not arquivo or not arquivo.exists():
            return None
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        if dados.pop("v", None) != VERSAO_DA_LEITURA:
            return None
        return Album(**{**dados, "faixas": [Faixa(**f) for f in dados["faixas"]]})

    def _para_cache(self, album: Album):
        if not self.cache:
            return
        from dataclasses import asdict
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / f"{album.id}.json").write_text(
            json.dumps({**asdict(album), "v": VERSAO_DA_LEITURA}, ensure_ascii=False), encoding="utf-8"
        )


def interpretar_pagina(html: str, album_id: str) -> Album:
    """Extrai o álbum do HTML da página. Levanta ErroDeColeta se a página não for a do álbum pedido."""
    posicao = html.find("__DZR_APP_STATE__")
    inicio = html.find("{", posicao) if posicao >= 0 else -1
    if inicio < 0:
        raise ErroDeColeta("a página não trouxe o estado do álbum (__DZR_APP_STATE__)")
    try:
        estado, _ = json.JSONDecoder().raw_decode(html, inicio)
    except ValueError as e:
        raise ErroDeColeta(f"estado do álbum ilegível: {e}") from e
    dados, musicas = estado.get("DATA") or {}, estado.get("SONGS") or {}
    if str(dados.get("ALB_ID", "")) != str(album_id):
        raise ErroDeColeta(f"a página devolveu o álbum {dados.get('ALB_ID') or '(nenhum)'} em vez do {album_id}")
    album = Album(
        id=str(album_id), titulo=dados.get("ALB_TITLE", ""), artista=dados.get("ART_NAME", ""),
        fornecedor=dados.get("LABEL_NAME", ""), upc=str(dados.get("UPC") or ""),
        lancamento=dados.get("ORIGINAL_RELEASE_DATE") or dados.get("PHYSICAL_RELEASE_DATE") or dados.get("DIGITAL_RELEASE_DATE") or "",
        faixas_declaradas=int(dados.get("NUMBER_TRACK") or 0),
    )
    for m in musicas.get("data") or []:
        papeis = m.get("SNG_CONTRIBUTORS")
        papeis = {k: list(v) for k, v in papeis.items() if isinstance(v, list)} if isinstance(papeis, dict) else {}
        autores = []
        for papel in PAPEIS_DE_AUTOR:
            autores += [n for n in papeis.get(papel, []) if n and n not in autores]
        titulo = m.get("SNG_TITLE", "")
        if m.get("VERSION"):
            titulo = f"{titulo} {m['VERSION']}"
        album.faixas.append(Faixa(
            id=str(m.get("SNG_ID", "")), titulo=titulo, interprete=m.get("ART_NAME", "") or album.artista,
            isrc=m.get("ISRC", ""), creditos=autores, papeis=papeis, duracao=int(m.get("DURATION") or 0),
        ))
    if len(album.faixas) != album.faixas_declaradas:
        album.aviso = f"CONTAGEM_DIVERGENTE: a página declara {album.faixas_declaradas} faixas e trouxe {len(album.faixas)}"
    return album
