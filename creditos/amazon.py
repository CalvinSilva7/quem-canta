"""Amazon Music na web (music.amazon.com.br): a prova de que o site não mostra o compositor.

O site abre sem login. A faixa é achada pela busca de músicas, aberta dentro do álbum, e o print é o do
menu de ações da faixa ("..."): na web ele traz "Ver álbum", "Ver artista", "Compartilhar esta música" e
"Reproduzir músicas semelhantes", e não tem item de créditos.

O print tem de mostrar quem canta (pedido do dono, 30/10/2026: sem isso, a defesa pode dizer que não se sabe de
quem é a gravação). No álbum, o cantor fica só no cabeçalho, lá em cima, e some quando a faixa está mais abaixo.
Por isso, antes do print, a faixa é posta para tocar (como na Deezer): a barra do tocador, no pé da página, mostra o
título e o cantor, em qualquer posição do álbum. Sem login, o site toca uma prévia de 30 s, o bastante para o print.
A música tem proteção contra cópia: só toca no Google Chrome (ver `Navegador(toca_musica=True)`). Sem tocar, o print
sai mesmo assim e vira aviso no Resumo.

O aplicativo de desktop da Amazon Music é outra coisa: lá existe o item "Créditos", com os compositores.
Ele não é um site e só funciona com a conta logada, então não é coletado aqui.

Se um dia o menu da web passar a ter item de créditos, a leitura não inventa nada: registra que o item
existe e devolve erro, para alguém olhar, em vez de afirmar que há ou que não há crédito.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from cantor.matching import normalizar

from . import captura
from . import disco

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
# A página tem dois `music-detail-header`: o primeiro é só a moldura, sem texto; o do álbum é o que tem o cantor.
_LER_ALBUM = """() => { const topo = [...document.querySelectorAll('music-detail-header')].find(e => e.getAttribute('primary-text'));
  return {album: topo ? (topo.getAttribute('headline') || topo.getAttribute('primary-text') || '') : '',
          interprete: topo ? (topo.getAttribute('primary-text') || '') : '',
          detalhe: topo ? (topo.getAttribute('tertiary-text') || topo.getAttribute('secondary-text') || '') : '',
          faixas: [...document.querySelectorAll('music-text-row')].map(l => l.getAttribute('primary-href') || '')}; }"""
# Leva a linha da faixa para o meio da tela (longe da faixa "Ouça Grátis" e do tocador, no pé) e clica no tocar dela.
_TOCAR_A_FAIXA = """(faixa) => { const l = document.querySelector(`music-text-row[primary-href='/tracks/${faixa}']`);
  if (!l) return false; l.scrollIntoView({block: 'center'});
  const b = (l.shadowRoot || l).querySelector('[icon-name="play"]'); if (!b) return false; b.click(); return true; }"""
# A barra do tocador (pé da página): título e cantor da música carregada, o tempo (segundos) e se está tocando.
_TOCADOR = _TODOS.replace("return anda(document)", "const todos = anda(document)") + """;
  const info = todos.find(e => e.id === 'miniNPVTrackInfo');
  const baixo = todos.filter(e => { const b = e.getBoundingClientRect(); return b.height > 0 && b.top > innerHeight - 110; });
  const tempo = baixo.find(e => e.getAttribute && e.getAttribute('aria-valuemax'));
  return {titulo: info ? info.getAttribute('primary-text') || '' : '', cantor: info ? info.getAttribute('secondary-text') || '' : '',
          tempo: tempo ? Number(tempo.getAttribute('aria-valuenow')) : null, duracao: tempo ? Number(tempo.getAttribute('aria-valuemax')) : null,
          tocando: baixo.some(e => e.getAttribute && e.getAttribute('icon-name') === 'pause')}; }"""
ESPERA_PARA_TOCAR = 25  # segundos; a prévia toca 30


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
    cantor: str = ""  # o intérprete que o cabeçalho do álbum mostra
    com_o_cantor: bool = False  # o print mostra o tocador com esta música e o cantor; sem isso, é fotografada de novo
    tocador: dict = field(default_factory=dict)  # o que a barra do tocador mostrava quando a música começou a tocar
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
        guardadas = disco.ler_json(arquivo, {})
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
        disco.gravar_json(arquivo, guardadas)
        return candidatos

    def ler(self, cand: Candidato, obra: str) -> Leitura:
        """Abre a faixa dentro do álbum, abre o menu dela e tira o print. Leitura completa fica guardada."""
        arquivo = self.cache / f"{cand.faixa}.json"
        dados = disco.ler_json(arquivo)
        if isinstance(dados, dict):
            guardada = Leitura(**dados)
            # print sem tela inteira, ou sem a música tocando com o cantor (antes de 30/10/2026), é refeito
            if (captura.serve(self.pasta, guardada.provas) and guardada.com_o_cantor) or self.so_o_que_ja_foi_lido:
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
            disco.gravar_json(arquivo, asdict(leitura))
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
        leitura.album, leitura.cantor = album["album"], album["interprete"]
        linha = pagina.locator(f"music-text-row[primary-href='/tracks/{cand.faixa}']")
        if not linha.count():
            leitura.coleta, leitura.erro = "indisponivel", "a faixa não aparece na lista do álbum"
            return
        exibido_na_linha = linha.first.get_attribute("primary-text") or ""
        if normalizar(exibido_na_linha) != normalizar(cand.titulo):
            leitura.coleta, leitura.erro = "erro", f'a linha da faixa mostra "{exibido_na_linha}", e não o título buscado'
            return
        leitura.tocador = self.tocar(cand, exibido_na_linha)
        if not leitura.tocador["tocando"]:
            pagina.keyboard.press("Escape")  # um aviso do site por cima da página não pode sair no print
            pagina.wait_for_timeout(500)
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
        no_print = pagina.evaluate(_TOCADOR)  # o tocador no instante do print
        leitura.com_o_cantor = bool(leitura.tocador["tocando"] and no_print["cantor"]
                                    and normalizar(no_print["titulo"]) == normalizar(exibido_na_linha))
        exibido = {"titulo": exibido_na_linha, "interprete": cand.interprete, "album": leitura.album, "cantor": leitura.cantor,
                   "itens_do_menu": itens, "tocador": leitura.tocador, "tocador_no_print": no_print,
                   "constatacao": "o menu da faixa na versão web da Amazon Music não tem item de créditos"}
        leitura.provas.append(captura.capturar(pagina, self.pasta, obra, cand.interprete, "amazon-site", exibido, "menu")["captura"])
        pagina.keyboard.press("Escape")

    def tocar(self, cand: Candidato, titulo: str) -> dict:
        """Clica no tocar da faixa e espera a barra do tocador mostrar ESTA música tocando, com o tempo andando.
        Devolve o que o tocador mostrava; sem tocar no tempo (ou sem o Google Chrome), "tocando" é False."""
        if not getattr(self.nav, "chrome", False):
            return {"tocando": False, "motivo": "o Google Chrome não está instalado neste computador: só nele a música do site toca"}
        pagina = self.nav.pagina
        if not pagina.evaluate(_TOCAR_A_FAIXA, cand.faixa):
            return {"tocando": False, "motivo": "a linha da faixa não mostrou o botão de tocar"}
        limite, antes = time.monotonic() + ESPERA_PARA_TOCAR, None
        while True:
            pagina.wait_for_timeout(1500)
            agora = pagina.evaluate(_TOCADOR)
            certa = bool(titulo) and normalizar(agora["titulo"]) == normalizar(titulo)
            if certa and agora["tocando"] and antes and agora["tempo"] not in (None, 0, antes["tempo"]):
                return {**agora, "tocando": True}
            antes = agora if certa else None
            if time.monotonic() > limite:
                return {**agora, "tocando": False, "esperado": titulo,
                        "motivo": f"a música não começou a tocar em {ESPERA_PARA_TOCAR} s"}
