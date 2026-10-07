"""Coleta no Vagalume: não é streaming, é site de letras que publica a autoria citando o ECAD.

A página de cada letra traz um bloco fixo (#author) com "Compositores:", "Editores:" e a
marca de verificação no ECAD. Quando o bloco diz que o compositor é desconhecido, o site
não está só omitindo: está afirmando, o que é registrado no fundamento.
A descoberta é pela página do intérprete (/<intérprete>/), que lista as letras dele.
"""

import html as _html
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

from .captura import slug

SITE = "https://www.vagalume.com.br"
NAVEGADOR = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/126.0 Safari/537.36")
VERSAO_DA_LEITURA = 1


class ErroDeColeta(Exception):
    pass


@dataclass
class Letra:
    link: str = ""
    titulo: str = ""
    interprete: str = ""
    creditos: list[str] = field(default_factory=list)  # nomes em "Compositores:"
    bloco: str = ""  # o texto do bloco de autoria, como a página mostra
    desconhecido: bool = False  # o site afirma que o compositor é desconhecido
    isrc: str = ""
    erro: str = ""


def _texto(trecho: str) -> str:
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", trecho)).split())


def interpretar_pagina(pagina: str, link: str) -> Letra:
    """Lê o bloco de autoria. Página sem o bloco é erro: nunca se classifica a partir do resto da página."""
    bloco = re.search(r"<small[^>]*\bid=[\"']?author[\"']?[^>]*>(.*?)</small>", pagina, re.S)
    if not bloco:
        raise ErroDeColeta("a página não tem o bloco de autoria (#author)")
    letra = Letra(link=link, bloco=_texto(bloco.group(1)))
    titulo = re.search(r"<h1[^>]*>(.*?)</h1>", pagina, re.S)
    interprete = re.search(r"<h2[^>]*>(.*?)</h2>", pagina, re.S)
    letra.titulo, letra.interprete = (_texto(titulo.group(1)) if titulo else ""), (_texto(interprete.group(1)) if interprete else "")
    autores = re.search(r"<span[^>]*\bclass=[\"']?tit-CA[\"']?[^>]*>(.*?)</span>", bloco.group(1), re.S)
    if autores:
        sem_associacao = re.sub(r"\(\s*<a[^>]*>.*?</a>\s*\)", "", autores.group(1), flags=re.S)  # "(UBC)", "(AMAR)"...
        lista = _texto(re.sub(r"<b>.*?</b>", "", sem_associacao, flags=re.S))
        for nome in re.split(r"\s*,\s*(?![^()]*\))", lista):
            nome = re.sub(r"\s*\([^)]*\)\s*$", "", nome).strip()  # "Nome Civil (Pseudônimo)" -> "Nome Civil"
            if nome:
                letra.creditos.append(nome)
    if any("desconhecid" in n.lower() for n in letra.creditos) or (not letra.creditos and "desconhecid" in letra.bloco.lower()):
        letra.desconhecido, letra.creditos = True, []
    return letra


def interpretar_lista(pagina: str, artista: str) -> dict:
    """{endereço da letra: título} a partir da página do intérprete (os atributos vêm sem aspas)."""
    achadas = {}
    for endereco, titulo in re.findall(rf"<a[^>]*href=[\"']?(/{re.escape(artista)}/[a-z0-9-]+\.html)[\"']?[^>]*>(.*?)</a>", pagina, re.S):
        if _texto(titulo):
            achadas.setdefault(endereco, _texto(titulo))
    return achadas


class Vagalume:
    def __init__(self, filtro=None, cache=None, intervalo=0.8, sessao=None):
        self.filtro, self.intervalo = filtro, intervalo
        self.cache = Path(cache) if cache else None
        self.sessao = sessao or requests.Session()
        self.requisicoes, self._ultima = 0, 0.0

    def _get(self, url):
        if self.filtro:
            self.filtro.conferir(url)
        time.sleep(max(self.intervalo - (time.monotonic() - self._ultima), 0))
        self._ultima = time.monotonic()
        self.requisicoes += 1
        try:
            r = self.sessao.get(url, timeout=30, headers={"User-Agent": NAVEGADOR, "Accept-Language": "pt-BR,pt;q=0.9"})
        except requests.RequestException as e:
            raise ErroDeColeta(type(e).__name__) from e
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ErroDeColeta(f"HTTP {r.status_code}")
        return r.content.decode("utf-8", "replace")

    def _guardado(self, nome):
        arquivo = self.cache / f"{nome}.json" if self.cache else None
        return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo and arquivo.exists() else None

    def _guardar(self, nome, dados):
        if self.cache:
            self.cache.mkdir(parents=True, exist_ok=True)
            (self.cache / f"{nome}.json").write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")

    def letras_de(self, interprete: str) -> dict:
        """{endereço: título} das letras do intérprete no site; {} se ele não tem página lá."""
        artistas = self._guardado("_artistas") or {}
        if interprete in artistas:
            return artistas[interprete]
        nome = slug(interprete, 80)
        candidatos = list(dict.fromkeys([nome, re.sub(r"^(grupo|banda|os|as)-", "", nome)]))
        achadas = {}
        for artista in candidatos:
            pagina = self._get(f"{SITE}/{artista}/")
            if pagina:
                achadas = interpretar_lista(pagina, artista)
                break
        artistas[interprete] = achadas
        self._guardar("_artistas", artistas)
        return achadas

    def letra(self, endereco: str) -> Letra:
        link = SITE + endereco
        guardada = self._guardado(slug(endereco, 120))
        if guardada and guardada.pop("v", None) == VERSAO_DA_LEITURA:
            return Letra(**guardada)
        try:
            pagina = self._get(link)
            if pagina is None:
                return Letra(link=link, erro="página não encontrada")
            letra = interpretar_pagina(pagina, link)
        except ErroDeColeta as e:
            return Letra(link=link, erro=str(e))
        self._guardar(slug(endereco, 120), {**asdict(letra), "v": VERSAO_DA_LEITURA})
        return letra
