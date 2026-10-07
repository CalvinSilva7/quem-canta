"""Coleta no Spotify, pelo que o usuário vê: a janela "Ver créditos" de cada faixa.

No Spotify o item "Ver créditos" existe sempre, mesmo quando a faixa não tem compositor:
por isso a janela é aberta em todos os casos, e SEM CRÉDITOS é a ausência da seção
"Composição e letra" na janela aberta, nunca a ausência do item de menu.
Guardas: a janela é reconhecida por "Créditos" + "Notificar erro" (que existem sempre, ao
contrário da seção de composição); o título da janela tem de ser o da faixa pedida.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from cantor.matching import normalizar

from . import captura

SITE = "https://open.spotify.com/"
LINK = SITE + "intl-pt/track/{}"
VERSAO_DA_LEITURA = 1
SECAO_DE_AUTORIA = "composicao"
SECAO_DE_FONTES = "fontes"

_LER_LINHAS = """() => [...document.querySelectorAll('[data-testid="tracklist-row"]')].map(l => ({
  titulo: l.querySelector('a[href*="/track/"]')?.textContent.trim() || '',
  faixa: (l.querySelector('a[href*="/track/"]')?.getAttribute('href') || '').split('/track/')[1]?.split('?')[0] || '',
  artistas: [...l.querySelectorAll('a[href*="/artist/"]')].map(a => a.textContent.trim()),
  album: l.querySelector('a[href*="/album/"]')?.textContent.trim() || '',
}))"""
_LER_JANELA = """() => {
  const janela = [...document.querySelectorAll('dialog, [role="dialog"]')].filter(d => d.getBoundingClientRect().width > 0 && d.querySelector('h1'))
    .find(d => /cr[eé]ditos|credits/i.test(d.querySelector('h1').textContent));
  if (!janela) return null;
  return {
    titulo: janela.querySelector('h2')?.textContent.trim() || '',
    ancora: !!janela.querySelector('a[href*="song-credits"], a[href*="support.spotify.com"]'),
    partes: [...janela.querySelectorAll('span')].filter(s => s.children.length === 0 && s.textContent.trim())
      .map(s => [/encore-text-body-medium(?!-bold)/.test(s.className) ? 'nome' : 'texto', s.textContent.trim()]),
  };
}"""


@dataclass
class Candidato:
    faixa: str
    titulo: str = ""
    interprete: str = ""
    album: str = ""

    @property
    def link(self):
        return LINK.format(self.faixa)


@dataclass
class Leitura:
    faixa: str
    coleta: str = "ok"
    erro: str = ""
    titulo: str = ""
    interprete: str = ""
    album: str = ""
    creditos: list[str] = field(default_factory=list)
    secoes: dict = field(default_factory=dict)
    fornecedor: str = ""
    provas: list[str] = field(default_factory=list)
    lido_em: str = ""

    @property
    def link(self):
        return LINK.format(self.faixa)


def interpretar_janela(partes) -> dict:
    """[["texto", "Composição e letra"], ["nome", "Fulano"], ["texto", "Autores"], ...] -> {"Composição e letra": ["Fulano"], ...}

    Cabeçalho de seção é o texto que vem logo antes de um nome sem vir logo depois de outro (o texto
    que vem depois de um nome é o papel dele: "Autores", "Artista principal"). "Fontes" não tem nomes
    marcados: tudo o que vem depois dele é fornecedor do metadado.
    """
    secoes, atual, depois_de_nome = {}, None, False
    partes = list(partes or [])
    for i, (tipo, texto) in enumerate(partes):
        if normalizar(texto) == SECAO_DE_FONTES:
            atual, depois_de_nome = texto, False
            secoes[atual] = []
        elif tipo == "nome":
            if atual is not None:
                secoes[atual].append(texto)
            depois_de_nome = True
        elif atual is not None and normalizar(atual) == SECAO_DE_FONTES:
            secoes[atual].append(texto)
        elif not depois_de_nome and i + 1 < len(partes) and partes[i + 1][0] == "nome":
            atual = texto
            secoes[atual] = []
        else:
            depois_de_nome = False
    return secoes


def autores_e_fornecedor(secoes: dict) -> tuple[list[str], str]:
    autores, fornecedor = [], ""
    for rotulo, nomes in secoes.items():
        if normalizar(rotulo).startswith(SECAO_DE_AUTORIA):
            autores += [n for n in nomes if n not in autores]
        elif normalizar(rotulo) == SECAO_DE_FONTES:
            fornecedor = ", ".join(nomes)
    return autores, fornecedor


def interpretar_linhas(linhas) -> list[Candidato]:
    return [Candidato(l["faixa"], l["titulo"], ", ".join(l.get("artistas") or []), l.get("album", ""))
            for l in linhas or [] if l.get("faixa") and l.get("titulo")]


class Spotify:
    def __init__(self, navegador, pasta, so_o_que_ja_foi_lido=False):
        self.nav, self.pasta = navegador, Path(pasta)
        self.cache = self.pasta / "leituras"
        self.so_o_que_ja_foi_lido = so_o_que_ja_foi_lido

    def buscar(self, consulta: str) -> list[Candidato]:
        arquivo = self.cache / "_buscas.json"
        guardadas = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}
        if consulta in guardadas:
            return [Candidato(**c) for c in guardadas[consulta]]
        if self.so_o_que_ja_foi_lido:
            return []
        self.nav.ir(SITE + "search/" + quote(consulta) + "/tracks")
        try:
            self.nav.pagina.wait_for_selector('[data-testid="tracklist-row"]', timeout=15000)
            self.nav.pagina.wait_for_timeout(700)
        except Exception:
            self.nav.conferir(self.nav.pagina.evaluate("document.body.innerText.slice(0, 1500)"))
        candidatos = interpretar_linhas(self.nav.pagina.evaluate(_LER_LINHAS))
        guardadas[consulta] = [asdict(c) for c in candidatos]
        self.cache.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(guardadas, ensure_ascii=False), encoding="utf-8")
        return candidatos

    def ler(self, cand: Candidato, obra: str) -> Leitura:
        arquivo = self.cache / f"{cand.faixa}.json"
        if arquivo.exists():
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
            if dados.pop("v", None) == VERSAO_DA_LEITURA:
                return Leitura(**dados)
        leitura = Leitura(faixa=cand.faixa, titulo=cand.titulo, interprete=cand.interprete, album=cand.album,
                          lido_em=captura.agora().isoformat(timespec="seconds"))
        if self.so_o_que_ja_foi_lido:
            leitura.coleta, leitura.erro = "erro", "faixa ainda não lida: a coleta foi interrompida antes dela"
            return leitura
        try:
            self._ler(leitura, obra)
        except (captura.PaginaTraduzida,) + _bloqueios():
            raise
        except Exception as e:
            leitura.coleta, leitura.erro = "erro", f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
        if leitura.coleta == "ok":
            self.cache.mkdir(parents=True, exist_ok=True)
            arquivo.write_text(json.dumps({**asdict(leitura), "v": VERSAO_DA_LEITURA}, ensure_ascii=False, indent=1), encoding="utf-8")
        return leitura

    def _ler(self, leitura: Leitura, obra: str):
        pagina = self.nav.pagina
        pagina.keyboard.press("Escape")
        self.nav.ir(leitura.link, pausa=(4, 7))
        pagina.wait_for_selector('[data-testid="more-button"]', timeout=30000)
        pagina.wait_for_timeout(1200)
        self.nav.conferir(pagina.evaluate("document.body.innerText.slice(0, 1500)"))
        na_pagina = pagina.evaluate("document.querySelector('[data-testid=entityTitle] h1, main h1')?.textContent.trim() || ''")
        if na_pagina:
            leitura.titulo = na_pagina
        pagina.locator('[data-testid="more-button"]').first.click()
        pagina.wait_for_selector('[role="menu"] [role="menuitem"]', timeout=8000)
        item = pagina.locator('[role="menu"] [role="menuitem"]').filter(has_text=re.compile(r"cr[eé]ditos", re.I))
        if not item.count():
            leitura.coleta, leitura.erro = "erro", 'o menu da faixa abriu sem o item "Ver créditos" (não é o menu esperado)'
            return
        item.first.click()
        pagina.wait_for_function(f"({_LER_JANELA})() !== null", timeout=15000)
        pagina.wait_for_timeout(700)
        janela = pagina.evaluate(_LER_JANELA)
        if not janela["ancora"]:
            leitura.coleta, leitura.erro = "erro", 'a janela aberta não é a de créditos (falta "Notificar erro")'
            return
        if normalizar(janela["titulo"]) != normalizar(leitura.titulo):
            leitura.coleta, leitura.erro = "erro", f'a janela de créditos é de "{janela["titulo"]}", e não desta faixa'
            return
        leitura.secoes = interpretar_janela(janela["partes"])
        leitura.creditos, leitura.fornecedor = autores_e_fornecedor(leitura.secoes)
        exibido = {"titulo": leitura.titulo, "interprete": leitura.interprete, "creditos": leitura.secoes}
        if not leitura.creditos:
            exibido["constatacao"] = 'a janela de créditos não tem a seção "Composição e letra"'
        leitura.provas.append(captura.capturar(pagina, self.pasta, obra, leitura.interprete, "spotify", exibido, "creditos")["captura"])
        pagina.keyboard.press("Escape")


def _bloqueios():
    from .navegador import Bloqueio
    return (Bloqueio,)
