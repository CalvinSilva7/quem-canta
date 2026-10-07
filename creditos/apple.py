"""Coleta na Apple Music: a plataforma de controle (onde o crédito certo costuma estar).

A página do álbum publica o estado do servidor em <script id="serialized-server-data">, e
cada faixa traz o `composer` que a tela mostra. A busca usa a API pública do iTunes.
Guardas: o álbum devolvido é o pedido (pelo og:url, porque a Apple redireciona para um
endereço com o nome do álbum); o número de faixas lidas bate com o declarado.
"""

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

from cantor.matching import dividir_compositores

BUSCA = "https://itunes.apple.com/search"
PAGINA_DO_ALBUM = "https://music.apple.com/br/album/{}"
NAVEGADOR = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/126.0 Safari/537.36")
VERSAO_DA_LEITURA = 1


class ErroDeColeta(Exception):
    pass


@dataclass
class Faixa:
    titulo: str = ""
    interprete: str = ""
    creditos: list[str] = field(default_factory=list)
    link: str = ""
    isrc: str = ""
    numero: int = 0


@dataclass
class Album:
    id: str = ""
    titulo: str = ""
    artista: str = ""
    fornecedor: str = ""  # linha de ℗/© do rodapé do álbum
    lancamento: str = ""
    link: str = ""
    faixas_declaradas: int = 0
    faixas: list[Faixa] = field(default_factory=list)
    erro: str = ""
    aviso: str = ""


class AppleMusic:
    def __init__(self, filtro=None, cache=None, intervalo=0.5, sessao=None):
        self.filtro, self.intervalo = filtro, intervalo
        self.cache = Path(cache) if cache else None
        self.sessao = sessao or requests.Session()
        self.requisicoes, self._ultima = 0, 0.0

    def _get(self, url, parametros=None):
        if self.filtro:
            self.filtro.conferir(url, *(str(v) for v in (parametros or {}).values()))
        erro = ""
        for tentativa in range(3):
            falta = self.intervalo - (time.monotonic() - self._ultima)
            time.sleep(max(falta, 0) + (2 ** tentativa if tentativa else 0))
            self._ultima = time.monotonic()
            self.requisicoes += 1
            try:
                r = self.sessao.get(url, params=parametros, timeout=30,
                                    headers={"User-Agent": NAVEGADOR, "Accept-Language": "pt-BR,pt;q=0.9"})
            except requests.RequestException as e:
                erro = type(e).__name__
                continue
            if r.status_code in (403, 429, 500, 502, 503, 504):
                erro = f"HTTP {r.status_code}"
                continue
            if r.status_code != 200:
                raise ErroDeColeta(f"HTTP {r.status_code}")
            return r
        raise ErroDeColeta(f"{erro} após 3 tentativas")

    def buscar_faixas(self, consulta: str, maximo=50) -> list[dict]:
        """[{"titulo", "interprete", "album_id"}] pela API pública de busca do iTunes (loja do Brasil)."""
        guardadas = self.cache / "_buscas.json" if self.cache else None
        buscas = json.loads(guardadas.read_text(encoding="utf-8")) if guardadas and guardadas.exists() else {}
        if consulta in buscas:
            return buscas[consulta]
        dados = self._get(BUSCA, {"term": consulta, "country": "BR", "entity": "song", "limit": maximo}).json()
        faixas = [{"titulo": i.get("trackName", ""), "interprete": i.get("artistName", ""), "album_id": str(i.get("collectionId", ""))}
                  for i in dados.get("results", [])]
        if guardadas:
            buscas[consulta] = faixas
            self.cache.mkdir(parents=True, exist_ok=True)
            guardadas.write_text(json.dumps(buscas, ensure_ascii=False), encoding="utf-8")
        return faixas

    def album(self, album_id: str) -> Album:
        album_id = str(album_id)
        arquivo = self.cache / f"{album_id}.json" if self.cache else None
        if arquivo and arquivo.exists():
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
            if dados.pop("v", None) == VERSAO_DA_LEITURA:
                return Album(**{**dados, "faixas": [Faixa(**f) for f in dados["faixas"]]})
        try:
            resposta = self._get(PAGINA_DO_ALBUM.format(album_id))
            album = interpretar_pagina(resposta.content.decode("utf-8", "replace"), album_id)
        except ErroDeColeta as e:
            return Album(id=album_id, erro=str(e))
        if arquivo:
            self.cache.mkdir(parents=True, exist_ok=True)
            arquivo.write_text(json.dumps({**asdict(album), "v": VERSAO_DA_LEITURA}, ensure_ascii=False), encoding="utf-8")
        return album


def interpretar_pagina(html: str, album_id: str) -> Album:
    endereco = re.search(r'property="og:url" content="([^"]+)"', html)
    if not endereco or not endereco.group(1).rstrip("/").endswith("/" + str(album_id)):
        raise ErroDeColeta(f"a página não é a do álbum {album_id} (og:url: {endereco.group(1) if endereco else 'ausente'})")
    estado = re.search(r'<script type="application/json" id="serialized-server-data">(.*?)</script>', html, re.S)
    if not estado:
        raise ErroDeColeta("a página não trouxe o estado do álbum (serialized-server-data)")
    try:
        dados = json.loads(estado.group(1))
        secoes = (dados["data"] if isinstance(dados, dict) else dados)[0]["data"]["sections"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise ErroDeColeta(f"estado do álbum ilegível: {type(e).__name__}") from e
    album = Album(id=str(album_id), link=endereco.group(1))
    for secao in secoes:
        itens = secao.get("items") or []
        if secao.get("itemKind") == "containerDetailHeaderLockup" and itens:
            cabecalho = itens[0]
            album.titulo = cabecalho.get("title", "")
            album.artista = ", ".join(l.get("title", "") for l in cabecalho.get("subtitleLinks") or [])
            album.faixas_declaradas = int(cabecalho.get("trackCount") or 0)
            ano = re.search(r"(19|20)\d{2}", cabecalho.get("quaternaryTitle") or "")
            album.lancamento = ano.group() if ano else ""
        elif secao.get("itemKind") == "trackLockup":
            for i in itens:
                album.faixas.append(Faixa(
                    titulo=i.get("title", ""), interprete=i.get("artistName") or album.artista,
                    creditos=dividir_compositores(i.get("composer") or ""), numero=int(i.get("trackNumber") or 0),
                    link=(i.get("contentDescriptor") or {}).get("url", ""),
                ))
        elif secao.get("itemKind") == "containerDetailTracklistFooterLockup" and itens:
            rodape = " ".join(str(itens[0].get("description") or "").split())
            album.fornecedor = rodape[rodape.index("℗"):] if "℗" in rodape else rodape[-120:]
    if not album.faixas:
        raise ErroDeColeta("a página do álbum não trouxe nenhuma faixa")
    if album.faixas_declaradas and len(album.faixas) != album.faixas_declaradas:
        album.aviso = f"CONTAGEM_DIVERGENTE: a página declara {album.faixas_declaradas} faixas e trouxe {len(album.faixas)}"
    return album
