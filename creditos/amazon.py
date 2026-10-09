"""Amazon Music na web (music.amazon.com.br): a prova de que o site não mostra o compositor.

O site abre sem login. A faixa é achada pela busca de músicas, aberta dentro do álbum, e o print é o do
menu de ações da faixa ("..."): na web ele traz "Ver álbum", "Ver artista", "Compartilhar esta música" e
"Reproduzir músicas semelhantes", e não tem item de créditos. É um print por música.

O aplicativo de desktop da Amazon Music é outra coisa: lá existe o item "Créditos", com os compositores.
Ele não é um site e só funciona com a conta logada, então não é coletado aqui.

Se um dia o menu da web passar a ter item de créditos, a leitura não inventa nada: registra que o item
existe e devolve erro, para alguém olhar, em vez de afirmar que há ou que não há crédito.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from cantor.matching import normalizar

from . import captura

SITE = "https://music.amazon.com.br/"
PLATAFORMA = "AMAZON - SITE"
ITEM_DE_CREDITOS = ("credito", "credit")  # como o item apareceria no menu, sem acento
# Itens que provam que o menu aberto é o da faixa (e não outro menu da página).
ITENS_DA_FAIXA = ("ver album", "ver artista", "compartilhar esta musica", "view album", "view artist")

# A página é feita de componentes com árvore própria (shadow DOM): a varredura precisa entrar em cada um.
_TODOS = """() => { const anda = (raiz) => { let saida = []; for (const e of raiz.querySelectorAll('*')) {
  saida.push(e); if (e.shadowRoot) saida = saida.concat(anda(e.shadowRoot)); } return saida; }; return anda(document)"""
_LER_RESULTADOS = _TODOS + """.filter(e => /trackAsin=/.test((e.getAttribute && e.getAttribute('primary-href')) || ''))
  .map(e => ({titulo: e.getAttribute('primary-text') || '', interprete: e.getAttribute('secondary-text') || e.getAttribute('secondary-text-1') || '',
              endereco: e.getAttribute('primary-href') || ''})); }"""
_LER_MENU = _TODOS + """.filter(e => e.tagName.toLowerCase() === 'music-list-item')
  .map(e => e.getAttribute('primary-text') || (e.shadowRoot ? e.shadowRoot.textContent : e.textContent).trim()); }"""
_LER_ALBUM = """() => { const topo = document.querySelector('music-detail-header');
  return {album: topo ? (topo.getAttribute('headline') || topo.getAttribute('primary-text') || '') : '',
          interprete: topo ? (topo.getAttribute('primary-text') || '') : '',
          detalhe: topo ? (topo.getAttribute('tertiary-text') || topo.getAttribute('secondary-text') || '') : '',
          faixas: [...document.querySelectorAll('music-text-row')].map(l => l.getAttribute('primary-href') || '')}; }"""


@dataclass
class Candidato:
    album: str   # identificador do álbum
    faixa: str   # identificador da faixa
    titulo: str = ""
    interprete: str = ""

    @property
    def link(self):
        return f"{SITE}albums/{self.album}?trackAsin={self.faixa}"


@dataclass
class Leitura:
    faixa: str
    link: str = ""
    coleta: str = "ok"  # "ok", "erro" ou "indisponivel"
    erro: str = ""
    titulo: str = ""
    interprete: str = ""
    album: str = ""
    menu: list[str] = field(default_factory=list)
    provas: list[str] = field(default_factory=list)
    lido_em: str = ""


def interpretar_resultados(itens) -> list[Candidato]:
    """Os resultados da busca de músicas: [{titulo, interprete, endereco}] -> candidatos, sem repetição."""
    import re

    candidatos, vistos = [], set()
    for item in itens or []:
        achado = re.search(r"/albums/([A-Z0-9]+)\?trackAsin=([A-Z0-9]+)", item.get("endereco", ""))
        if achado and achado[2] not in vistos and (item.get("titulo") or "").strip():
            vistos.add(achado[2])
            candidatos.append(Candidato(achado[1], achado[2], item["titulo"].strip(), (item.get("interprete") or "").strip()))
    return candidatos


def interpretar_menu(itens) -> str:
    """O que o menu da faixa mostra: "sem_creditos", "com_creditos" ou "invalido" (não é o menu da faixa)."""
    limpos = [normalizar(i) for i in itens or [] if str(i or "").strip()]
    if not any(any(sinal in item for sinal in ITENS_DA_FAIXA) for item in limpos):
        return "invalido"
    return "com_creditos" if any(any(sinal in item for sinal in ITEM_DE_CREDITOS) for item in limpos) else "sem_creditos"


class AmazonWeb:
    """Uso: `AmazonWeb(navegador, pasta)`; `buscar(consulta)` e `ler(candidato, obra)`."""

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
        self.nav.ir(SITE + "search/" + quote(consulta) + "/songs")
        try:
            self.nav.pagina.wait_for_selector("music-horizontal-item", timeout=15000)
            self.nav.pagina.wait_for_timeout(1200)
        except Exception:  # busca sem resultado, ou a página pediu outra coisa
            self.nav.conferir(self.nav.pagina.evaluate("document.body.innerText.slice(0, 1500)"))
        candidatos = interpretar_resultados(self.nav.pagina.evaluate(_LER_RESULTADOS))
        guardadas[consulta] = [asdict(c) for c in candidatos]
        self.cache.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(guardadas, ensure_ascii=False), encoding="utf-8")
        return candidatos

    def ler(self, cand: Candidato, obra: str) -> Leitura:
        """Abre a faixa dentro do álbum, abre o menu dela e tira o print. Leitura completa fica guardada."""
        arquivo = self.cache / f"{cand.faixa}.json"
        if arquivo.exists():
            guardada = Leitura(**json.loads(arquivo.read_text(encoding="utf-8")))
            if captura.serve(self.pasta, guardada.provas) or self.so_o_que_ja_foi_lido:  # print sem tela inteira é refeito
                return guardada
        leitura = Leitura(faixa=cand.faixa, link=cand.link, titulo=cand.titulo, interprete=cand.interprete,
                          lido_em=captura.agora().isoformat(timespec="seconds"))
        if self.so_o_que_ja_foi_lido:
            leitura.coleta, leitura.erro = "erro", "faixa ainda não lida: a coleta foi interrompida antes dela"
            return leitura
        from .navegador import Bloqueio

        try:
            self._ler(leitura, cand, obra)
        except (Bloqueio, captura.PaginaTraduzida):
            raise
        except Exception as e:  # timeout, elemento que sumiu: erro técnico, nunca "sem créditos"
            leitura.coleta, leitura.erro = "erro", f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
        if leitura.coleta == "ok":
            self.cache.mkdir(parents=True, exist_ok=True)
            arquivo.write_text(json.dumps(asdict(leitura), ensure_ascii=False), encoding="utf-8")
        return leitura

    def _ler(self, leitura: Leitura, cand: Candidato, obra: str):
        pagina = self.nav.pagina
        pagina.keyboard.press("Escape")  # fecha o menu da faixa anterior
        self.nav.ir(cand.link)
        try:
            pagina.wait_for_selector("music-text-row", timeout=25000)
        except Exception:
            self.nav.conferir(pagina.evaluate("document.body.innerText.slice(0, 1500)"))
            leitura.coleta, leitura.erro = "indisponivel", "o álbum não carregou as faixas"
            return
        pagina.wait_for_timeout(1500)
        album = pagina.evaluate(_LER_ALBUM)
        leitura.album = album["album"]
        linha = pagina.locator(f"music-text-row[primary-href='/tracks/{cand.faixa}']")
        if not linha.count():
            leitura.coleta, leitura.erro = "indisponivel", "a faixa não aparece na lista do álbum"
            return
        exibido_na_linha = linha.first.get_attribute("primary-text") or ""
        if normalizar(exibido_na_linha) != normalizar(cand.titulo):
            leitura.coleta, leitura.erro = "erro", f'a linha da faixa mostra "{exibido_na_linha}", e não o título buscado'
            return
        linha.first.scroll_into_view_if_needed()
        pagina.wait_for_timeout(400)
        itens = None
        for _ in range(2):  # o primeiro clique às vezes pega o botão ainda carregando
            linha.first.hover()
            linha.first.locator("music-button[icon-name='more']").click()
            pagina.wait_for_timeout(1200)
            itens = [i for i in pagina.evaluate(_LER_MENU) if i]
            if interpretar_menu(itens) != "invalido":
                break
            pagina.keyboard.press("Escape")
        leitura.menu = itens or []
        resultado = interpretar_menu(itens)
        if resultado == "invalido":
            leitura.coleta, leitura.erro = "erro", "o menu de ações da faixa não abriu (ou abriu outro menu)"
            return
        if resultado == "com_creditos":
            leitura.coleta = "erro"
            leitura.erro = "o menu da faixa na web tem um item de créditos, que esta versão do app não sabe ler: conferir à mão"
            return
        exibido = {"titulo": exibido_na_linha, "interprete": cand.interprete, "album": leitura.album, "itens_do_menu": itens,
                   "constatacao": "o menu da faixa na versão web da Amazon Music não tem item de créditos"}
        leitura.provas.append(captura.capturar(pagina, self.pasta, obra, cand.interprete, "amazon-site", exibido, "menu")["captura"])
        pagina.keyboard.press("Escape")
