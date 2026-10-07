"""Coleta no Tidal: os créditos ficam em uma página por álbum (tidal.com/album/<id>/credits).

Cada faixa da página traz células "rótulo + nomes" (COMPOSER, LYRICIST, PRODUCER, MUSIC
PUBLISHER...). Autores = COMPOSER ∪ LYRICIST. O Tidal não tem um campo de fonte do metadado,
mas o fornecedor aparece no PRODUCER e, às vezes, no MUSIC PUBLISHER: os dois são registrados.
Guardas: a página lida é a do álbum pedido; o número de faixas lidas bate com o "N MÚSICAS"
do cabeçalho (a faixa que faltar fica NÃO VERIFICADA); cada nome é lido do seu próprio
elemento, porque o separador entre nomes é só visual.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from cantor.matching import nomes_parecidos, normalizar

from . import captura

SITE = "https://tidal.com/"
VERSAO_DA_LEITURA = 1
ROTULOS_DE_AUTOR = ("composer", "lyricist", "compositor", "letrista", "writer", "author", "autor")
ROTULOS_DE_FORNECEDOR = ("producer", "produtor", "music publisher", "editora musical")

_LER_ALBUM = """() => ({
  titulo: document.querySelector('[data-test="title"]')?.textContent.trim() || '',
  artista: document.querySelector('[data-test="grid-item-detail-text-title-artist"]')?.textContent.trim() || '',
  contagem: document.querySelector('[data-test="grid-item-meta-item-count"]')?.textContent.trim() || '',
  data: document.querySelector('[data-test="meta-release-date"]')?.textContent.trim() || '',
  faixas: [...document.querySelectorAll('[data-test="album-info-item"]')].map(item => {
    const titulo = item.querySelector('[class*="_title_"]');
    return {
      id: titulo?.getAttribute('data-test-id') || '',
      numero: item.querySelector('[class*="_trackNumber_"]')?.textContent.trim() || '',
      titulo: titulo?.getAttribute('title') || titulo?.textContent.trim() || '',
      interprete: item.querySelector('[class*="_artistName_"]')?.getAttribute('title') || item.querySelector('[class*="_artistName_"]')?.textContent.trim() || '',
      celulas: [...item.querySelectorAll('[class*="_creditsCell_"]')].map(celula => {
        const nomes = [...celula.querySelectorAll('[class*="_creditsCellText_"] [class*="_item_"]')].map(n => n.textContent.trim()).filter(Boolean);
        const texto = celula.querySelector('[class*="_creditsCellText_"]')?.textContent.trim() || '';
        return [celula.querySelector('span')?.textContent.trim() || '', nomes.length ? nomes : (texto ? [texto] : [])];
      }),
    };
  }),
})"""
_LER_ARTISTAS = """() => [...new Map([...document.querySelectorAll('a[href*="/artist/"]')]
  .map(a => [a.getAttribute('href').match(/\\/artist\\/(\\d+)/)?.[1], (a.getAttribute('title') || a.textContent).trim()])
  .filter(p => p[0] && p[1])).entries()]"""
_LER_ALBUNS = """() => [...new Set([...document.querySelectorAll('a[href*="/album/"]')]
  .map(a => a.getAttribute('href').match(/\\/album\\/(\\d+)/)?.[1]).filter(Boolean))]"""
_DESTACAR = """(numero) => {
  const item = [...document.querySelectorAll('[data-test="album-info-item"]')]
    .find(i => i.querySelector('[class*="_trackNumber_"]')?.textContent.trim() === String(numero));
  if (!item) return false;
  item.scrollIntoView({block: 'center'});
  item.style.outline = '3px solid #d00000';
  return true;
}"""


@dataclass
class Faixa:
    id: str = ""
    numero: str = ""
    titulo: str = ""
    interprete: str = ""
    creditos: list[str] = field(default_factory=list)
    papeis: dict = field(default_factory=dict)
    link: str = ""
    provas: list[str] = field(default_factory=list)


@dataclass
class Album:
    id: str = ""
    titulo: str = ""
    artista: str = ""
    fornecedor: str = ""
    lancamento: str = ""
    faixas_declaradas: int = 0
    faixas: list[Faixa] = field(default_factory=list)
    erro: str = ""
    aviso: str = ""


def interpretar_album(dados: dict, album_id: str) -> Album:
    """Monta o álbum a partir do que a página de créditos mostra. Sem faixas lidas, é erro (ValueError)."""
    if not dados or not dados.get("faixas"):
        raise ValueError("a página de créditos não mostrou nenhuma faixa")
    ano = re.search(r"(19|20)\d{2}", dados.get("data") or "")
    declaradas = re.search(r"\d+", dados.get("contagem") or "")
    album = Album(id=str(album_id), titulo=dados.get("titulo", ""), artista=dados.get("artista", ""),
                  lancamento=ano.group() if ano else "", faixas_declaradas=int(declaradas.group()) if declaradas else 0)
    fornecedores = []
    for f in dados["faixas"]:
        papeis = {rotulo: nomes for rotulo, nomes in f.get("celulas") or [] if rotulo}
        autores = []
        for rotulo, nomes in papeis.items():
            if normalizar(rotulo) in ROTULOS_DE_AUTOR:
                autores += [n for n in nomes if n not in autores]
            elif normalizar(rotulo) in ROTULOS_DE_FORNECEDOR:
                fornecedores += [n for n in nomes if n not in fornecedores]
        link = f"{SITE}album/{album_id}/track/{f['id']}" if f.get("id") else f"{SITE}album/{album_id}/credits#faixa-{f.get('numero', '')}"
        album.faixas.append(Faixa(id=f.get("id", ""), numero=f.get("numero", ""), titulo=f.get("titulo", ""),
                                  interprete=f.get("interprete") or album.artista, creditos=autores, papeis=papeis, link=link))
    album.fornecedor = ", ".join(fornecedores)
    if album.faixas_declaradas and album.faixas_declaradas != len(album.faixas):
        album.aviso = f"CONTAGEM_DIVERGENTE: a página declara {album.faixas_declaradas} faixas e mostrou {len(album.faixas)}"
    return album


class Tidal:
    def __init__(self, navegador, pasta, so_o_que_ja_foi_lido=False):
        self.nav, self.pasta = navegador, Path(pasta)
        self.cache = self.pasta / "leituras"
        self.so_o_que_ja_foi_lido = so_o_que_ja_foi_lido

    def _guardado(self, nome):
        arquivo = self.cache / f"{nome}.json"
        return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else None

    def _guardar(self, nome, dados):
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / f"{nome}.json").write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")

    def albuns_de(self, nome: str) -> list[str]:
        """Álbuns do artista com esse nome (pela busca e pela página do artista); [] se não houver."""
        guardado = (self._guardado("_artistas") or {})
        if nome in guardado:
            return guardado[nome]
        if self.so_o_que_ja_foi_lido:
            return []
        pagina = self.nav.pagina
        # A busca só existe dentro do site já carregado: a página inicial e a de busca abrem em branco para quem
        # não está logado. Por isso a consulta é digitada no campo de busca de uma página do próprio site.
        if self.nav.filtro:
            self.nav.filtro.conferir(nome)
        # Sempre a partir de uma página neutra do próprio Tidal: assim os links de artista que aparecerem são os
        # da busca, e não os da página que estava aberta (nem o campo de busca de outro site).
        self.nav.ir(SITE + "album/0")
        pagina.wait_for_selector('input[type="search"]', timeout=30000)
        self.nav.clicar_se_houver(nome="Rejeitar|Reject")
        campo = pagina.locator('input[type="search"]').first
        campo.click()
        campo.fill("")
        pagina.keyboard.type(nome, delay=40)
        try:
            pagina.wait_for_selector('a[href*="/artist/"]', timeout=12000)
        except Exception:
            self.nav.conferir(pagina.evaluate("document.body.innerText.slice(0, 1500)"))
        pagina.wait_for_timeout(2500)
        artistas = [i for i, titulo in pagina.evaluate(_LER_ARTISTAS) if nomes_parecidos(nome, titulo, parcial=False)]
        albuns = []
        for artista in artistas[:2]:
            self.nav.ir(f"{SITE}artist/{artista}")
            try:
                pagina.wait_for_selector('a[href*="/album/"]', timeout=15000)
            except Exception:
                continue
            for _ in range(6):  # a página carrega mais álbuns conforme rola
                pagina.mouse.wheel(0, 2500)
                pagina.wait_for_timeout(500)
            albuns += [a for a in pagina.evaluate(_LER_ALBUNS) if a not in albuns]
        if albuns:  # resultado vazio não é guardado: pode ter sido a busca que falhou, e não o artista que não existe
            guardado[nome] = albuns
            self._guardar("_artistas", guardado)
        return albuns

    def album(self, album_id: str) -> Album:
        guardado = self._guardado(album_id)
        if guardado and guardado.pop("v", None) == VERSAO_DA_LEITURA:
            return Album(**{**guardado, "faixas": [Faixa(**f) for f in guardado["faixas"]]})
        if self.so_o_que_ja_foi_lido:
            return Album(id=str(album_id), erro="álbum ainda não lido: a coleta foi interrompida antes dele")
        try:
            self._abrir(album_id)
            album = interpretar_album(self.nav.pagina.evaluate(_LER_ALBUM), album_id)
        except (captura.PaginaTraduzida,) + _bloqueios():
            raise
        except Exception as e:
            return Album(id=str(album_id), erro=f"{type(e).__name__}: {str(e).splitlines()[0][:160]}")
        self._guardar(album_id, {**asdict(album), "v": VERSAO_DA_LEITURA})
        return album

    def _abrir(self, album_id):
        pagina = self.nav.pagina
        self.nav.ir(f"{SITE}album/{album_id}/credits", pausa=(3, 5))
        pagina.wait_for_selector('[data-test="album-info-item"]', timeout=30000)
        pagina.wait_for_timeout(900)
        self.nav.clicar_se_houver(nome="Rejeitar|Reject")
        if f"/album/{album_id}" not in pagina.url:
            raise ValueError(f"a página aberta ({pagina.url}) não é a do álbum {album_id}")

    def capturar(self, album_id: str, faixa: Faixa, obra: str) -> str:
        """Print da página de créditos do álbum, rolada até a faixa e com ela contornada. Devolve o nome-base."""
        self._abrir(album_id)
        if not self.nav.pagina.evaluate(_DESTACAR, faixa.numero):
            raise ValueError(f"a faixa {faixa.numero} não está na página de créditos")
        self.nav.pagina.wait_for_timeout(500)
        exibido = {"titulo": faixa.titulo, "interprete": faixa.interprete, "creditos": faixa.papeis, "faixa_no_album": faixa.numero}
        if not faixa.creditos:
            exibido["constatacao"] = "a faixa contornada não traz COMPOSER nem LYRICIST na página de créditos do álbum"
        return captura.capturar(self.nav.pagina, self.pasta, obra, faixa.interprete, "tidal", exibido, "creditos")["captura"]


def _bloqueios():
    from .navegador import Bloqueio
    return (Bloqueio,)
