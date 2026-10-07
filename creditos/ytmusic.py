"""Coleta no YouTube Music, pelo que o usuário vê: o menu da faixa e a janela de créditos.

Roda em um Chromium visível (Playwright), em pt-BR, sem login. Por faixa:
abre o link, espera a faixa carregar, abre o menu de ações do player e lê os
itens. Se não existe "Mostrar créditos da música", a faixa está SEM CRÉDITOS e a
tela, com o menu aberto, é capturada como prova. Se existe, a janela é aberta e
lida: "Composição de" e "Metadados de música fornecidos por".

O que nunca acontece: contornar captcha, login ou bloqueio (a fila para e avisa);
ler página traduzida pelo navegador; tratar erro como ausência de crédito.
"""

import json
import random
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

from cantor.matching import normalizar

from . import captura

SITE = "https://music.youtube.com/"
LINK = SITE + "watch?v={}"
JANELA = {"width": 1366, "height": 768}
ESPERA_ENTRE_CAPTURAS = (5, 10)  # segundos, sorteados: no máximo uma faixa a cada 5-10 s
ESPERA_ENTRE_BUSCAS = (2, 4)
VERSAO_DA_LEITURA = 1
ITEM_DE_CREDITOS = re.compile(r"cr[eé]ditos", re.IGNORECASE)
# Itens que todo menu de faixa tem: se nenhum aparece, o que abriu não foi o menu da faixa.
ANCORAS_DO_MENU = ("compartilhar", "ir para a pagina do artista", "adicionar a fila", "salvar na playlist")
ROTULOS_DE_AUTOR = ("composicao de", "letra de", "composicao e letra", "escrita por")
ROTULO_DE_FORNECEDOR = "metadados de musica fornecidos por"
_BLOQUEIO_NA_URL = ("accounts.google.com", "google.com/sorry", "consent.youtube.com", "consent.google.com")
_BLOQUEIO_NO_TEXTO = ("nao e um robo", "trafego incomum", "unusual traffic", "faca login para confirmar")

_LER_PLAYER = """() => {
  const barra = document.querySelector('ytmusic-player-bar');
  const visivel = (e) => { if (!e) return false; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
  return {
    titulo: barra?.querySelector('.title')?.textContent.trim() || '',
    linha: barra?.querySelector('.byline')?.innerText || '',
    tocando: barra?.querySelector('#play-pause-button')?.getAttribute('aria-label') || '',
    indisponivel: [...document.querySelectorAll('.ytp-error, ytmusic-message-renderer, yt-playability-error-supported-renderers')].filter(visivel).map(e => e.innerText.trim()).join(' ').slice(0, 300),
    lingua: document.documentElement.lang || '',
    video: document.querySelector('#movie_player')?.getVideoData?.().video_id || '',
    anuncio: !!document.querySelector('#movie_player.ad-showing, #movie_player.ad-interrupting, .ytp-ad-player-overlay, .ytp-ad-player-overlay-layout'),
    texto: (document.body?.innerText || '').slice(0, 1500),
  };
}"""
_LER_MENU = """() => {
  const visivel = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
  const aberto = [...document.querySelectorAll('tp-yt-iron-dropdown')].filter(visivel)[0];
  if (!aberto) return null;
  return [...aberto.querySelectorAll('ytmusic-menu-navigation-item-renderer, ytmusic-menu-service-item-renderer, ytmusic-toggle-menu-service-item-renderer')]
    .filter(visivel).map(i => i.innerText.trim());
}"""
_LER_JANELA = """() => {
  const visivel = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const janela = [...document.querySelectorAll('ytmusic-dismissable-dialog-renderer')].filter(visivel)[0];
  if (!janela) return null;
  return {
    cabecalho: [...janela.querySelectorAll('.header yt-formatted-string, .header a, h2, .title')].map(e => e.textContent.trim()).filter(Boolean).slice(0, 6),
    secoes: [...janela.querySelectorAll('.section-title, .section-subtitle')].map(e => [e.classList.contains('section-title') ? 'rotulo' : 'nome', e.textContent.trim()]),
    texto: janela.innerText.slice(0, 1500),
  };
}"""
_LER_RESULTADOS = """() => [...document.querySelectorAll('ytmusic-shelf-renderer ytmusic-responsive-list-item-renderer, ytmusic-section-list-renderer ytmusic-responsive-list-item-renderer')].map(e => {
  const colunas = [...e.querySelectorAll('.flex-column, .title-column')].map(c => c.innerText.trim()).filter(Boolean);
  const link = [...e.querySelectorAll('a[href*="watch?v="]')][0]?.getAttribute('href') || '';
  return { titulo: e.querySelector('.title')?.textContent.trim() || '', colunas, link,
           artistas: [...e.querySelectorAll('.secondary-flex-columns a[href^="channel/"], .secondary-flex-columns a[href^="browse/UC"]')].map(a => a.textContent.trim()) };
})"""


class Bloqueio(Exception):
    """Captcha, pedido de login, tela de consentimento ou bloqueio. A fila para; nada é contornado."""


@dataclass
class Candidato:
    video: str
    titulo: str = ""
    interprete: str = ""
    detalhe: str = ""  # álbum e duração, ou visualizações
    tipo: str = ""  # "Música" ou "Vídeo": a aba da busca em que apareceu

    @property
    def link(self):
        return LINK.format(self.video)


@dataclass
class Leitura:
    video: str
    coleta: str = "ok"  # "ok", "erro" ou "indisponivel"
    erro: str = ""
    titulo: str = ""
    interprete: str = ""
    detalhe: str = ""
    tipo: str = ""
    menu: list[str] = field(default_factory=list)
    tem_item_de_creditos: bool = False
    creditos: list[str] = field(default_factory=list)  # "Composição de" e "Letra de"
    secoes: dict = field(default_factory=dict)  # tudo o que a janela de créditos traz, por rótulo
    fornecedor: str = ""
    provas: list[str] = field(default_factory=list)  # nomes-base das capturas
    lido_em: str = ""

    @property
    def link(self):
        return LINK.format(self.video)


# --- interpretação (sem navegador: é o que os testes cobrem) -------------------


def sinal_de_bloqueio(url: str, texto: str = "") -> str:
    if any(parte in url for parte in _BLOQUEIO_NA_URL):
        return f"a plataforma desviou para {url.split('/')[2]}"
    limpo = normalizar(texto)
    return next((f'a página pede verificação humana ("{sinal}")' for sinal in _BLOQUEIO_NO_TEXTO if sinal in limpo), "")


def interpretar_menu(itens) -> str:
    """"com_creditos", "sem_creditos" ou "invalido" (o menu da faixa não abriu de verdade)."""
    if not itens:
        return "invalido"
    limpos = [normalizar(i) for i in itens]
    if not any(ancora in limpo for ancora in ANCORAS_DO_MENU for limpo in limpos):
        return "invalido"
    return "com_creditos" if any(ITEM_DE_CREDITOS.search(i) for i in itens) else "sem_creditos"


def interpretar_janela(secoes) -> dict:
    """[["rotulo", "Composição de"], ["nome", "Fulano"], ...] -> {"Composição de": ["Fulano"], ...}"""
    por_rotulo, atual = {}, None
    for tipo, texto in secoes or []:
        if tipo == "rotulo":
            atual = texto
            por_rotulo.setdefault(atual, [])
        elif atual is not None and texto:
            por_rotulo[atual].append(texto)
    return por_rotulo


def autores_e_fornecedor(por_rotulo: dict) -> tuple[list[str], str]:
    autores, fornecedor = [], ""
    for rotulo, nomes in por_rotulo.items():
        limpo = normalizar(rotulo)
        if any(limpo.startswith(r) for r in ROTULOS_DE_AUTOR):
            autores += [n for n in nomes if n not in autores]
        elif limpo.startswith(ROTULO_DE_FORNECEDOR):
            fornecedor = ", ".join(nomes)
    return autores, fornecedor


def interpretar_linha_do_player(linha: str) -> tuple[str, str]:
    """"Banda • Álbum • 2012" -> ("Banda", "Álbum • 2012")"""
    partes = [p.strip() for p in re.split(r"\s*•\s*", (linha or "").replace("\n", " ")) if p.strip()]
    return (partes[0], " • ".join(partes[1:])) if partes else ("", "")


def interpretar_resultados(itens, tipo) -> list[Candidato]:
    candidatos = []
    for item in itens or []:
        video = re.search(r"watch\?v=([\w-]{6,})", item.get("link") or "")
        if not video or not item.get("titulo"):
            continue
        colunas = [c for c in item.get("colunas") or [] if c != item["titulo"]]
        partes = [p.strip() for p in re.split(r"\s*•\s*", " • ".join(colunas).replace("\n", " ")) if p.strip()]
        interprete = ", ".join(item.get("artistas") or []) or (partes[0] if partes else "")
        detalhe = " • ".join(p for p in partes if p not in (item.get("artistas") or []) and p != interprete)
        candidatos.append(Candidato(video.group(1), item["titulo"], interprete, detalhe, tipo))
    return candidatos


# --- navegador -----------------------------------------------------------------


class YouTubeMusic:
    """Uso: `with YouTubeMusic(pasta_de_provas) as ytm: ...`. Abre uma janela do Chromium na tela."""

    def __init__(self, pasta, filtro=None, visivel=True, ao_avancar=None, so_o_que_ja_foi_lido=False, escondido=False):
        self.pasta = Path(pasta)
        self.cache = self.pasta / "leituras"
        self.filtro = filtro
        self.visivel, self.escondido = visivel, escondido
        self.avisar = ao_avancar or (lambda texto: None)
        self.navegacoes = 0
        self._ultima_captura = 0.0
        # Montar as saídas com o que já está guardado, sem abrir faixa nova (o que falta fica NÃO VERIFICADO).
        self.so_o_que_ja_foi_lido = so_o_que_ja_foi_lido

    def __enter__(self):
        self._pw = self._navegador = self._pagina = None
        return self

    @property
    def pagina(self):
        """A janela só abre na primeira vez em que é preciso navegar: com tudo já lido, não abre nenhuma."""
        if self._pagina is None:
            from playwright.sync_api import sync_playwright

            from .navegador import FORA_DA_TELA, preparar_asyncio

            preparar_asyncio()
            self._pw = sync_playwright().start()
            self._navegador = self._pw.chromium.launch(
                headless=not self.visivel,
                args=["--lang=pt-BR", "--disable-features=Translate", "--mute-audio"] + ([FORA_DA_TELA] if self.escondido else []),
            )
            contexto = self._navegador.new_context(viewport=JANELA, locale="pt-BR", timezone_id=captura.FUSO)
            contexto.add_init_script(f"({captura.TRAVA_DE_TRADUCAO})()")
            self._pagina = contexto.new_page()
        return self._pagina

    def __exit__(self, *erro):
        if self._navegador:
            self._navegador.close()
            self._pw.stop()

    def _ir(self, url):
        if self.filtro:
            self.filtro.conferir(unquote(url))  # o texto como é, e não "%C3%A7", que o filtro leria como percentual
        self.navegacoes += 1
        self.pagina.goto(url, wait_until="domcontentloaded", timeout=60000)
        motivo = sinal_de_bloqueio(self.pagina.url)
        if motivo:
            raise Bloqueio(motivo)

    def _conferir_pagina(self, estado):
        motivo = sinal_de_bloqueio(self.pagina.url, estado.get("texto", ""))
        if motivo:
            raise Bloqueio(motivo)
        if self.pagina.evaluate(captura._TRADUZIDA):
            raise captura.PaginaTraduzida(self.pagina.url)

    # --- busca ---------------------------------------------------------------

    def buscar(self, consulta: str, abas=("Músicas", "Vídeos")) -> list[Candidato]:
        """Faixas que a busca do YouTube Music mostra para a consulta, nas abas pedidas."""
        guardadas = self._buscas_guardadas()
        chave = f"{consulta} | {','.join(abas)}"
        if chave in guardadas:
            return [Candidato(**c) for c in guardadas[chave]]
        if self.so_o_que_ja_foi_lido:
            return []
        candidatos = []
        for aba in abas:
            time.sleep(random.uniform(*ESPERA_ENTRE_BUSCAS))
            self._ir(SITE + "search?q=" + quote(consulta))
            try:
                self.pagina.wait_for_selector("ytmusic-chip-cloud-chip-renderer", timeout=20000)
            except Exception:
                self._conferir_pagina(self.pagina.evaluate(_LER_PLAYER))
                continue  # busca sem resultado nenhum: não há abas
            chip = self.pagina.locator("ytmusic-chip-cloud-chip-renderer").filter(has_text=re.compile(rf"^\s*{aba}\s*$"))
            if not chip.count():
                continue
            chip.first.click()
            try:
                self.pagina.wait_for_selector("ytmusic-shelf-renderer ytmusic-responsive-list-item-renderer", timeout=15000)
            except Exception:
                continue
            self.pagina.wait_for_timeout(800)
            candidatos += interpretar_resultados(self.pagina.evaluate(_LER_RESULTADOS), aba[:-1])
        guardadas[chave] = [asdict(c) for c in candidatos]
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / "_buscas.json").write_text(json.dumps(guardadas, ensure_ascii=False), encoding="utf-8")
        return candidatos

    def _buscas_guardadas(self) -> dict:
        arquivo = self.cache / "_buscas.json"
        return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}

    # --- leitura de uma faixa -------------------------------------------------

    def ler(self, video: str, obra: str, esperado: Candidato | None = None) -> Leitura:
        """Lê menu e créditos de uma faixa. Leituras completas ficam guardadas: a retomada não refaz."""
        guardada = self._do_cache(video)
        if guardada:
            return guardada
        if self.so_o_que_ja_foi_lido:
            return Leitura(video=video, coleta="erro", erro="faixa ainda não lida: a coleta foi interrompida antes dela")
        falta = random.uniform(*ESPERA_ENTRE_CAPTURAS) - (time.monotonic() - self._ultima_captura)
        if falta > 0:
            time.sleep(falta)
        self._ultima_captura = time.monotonic()
        leitura = Leitura(video=video, tipo=esperado.tipo if esperado else "", lido_em=captura.agora().isoformat(timespec="seconds"))
        try:
            self._ler(leitura, obra)
        except (Bloqueio, captura.PaginaTraduzida):
            raise
        except Exception as e:  # timeout, elemento que sumiu, navegação interrompida: erro técnico, nunca "sem créditos"
            leitura.coleta, leitura.erro = "erro", f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
        if leitura.coleta == "ok":
            self._para_cache(leitura)
        return leitura

    def _ler(self, leitura: Leitura, obra: str):
        pagina = self.pagina
        pagina.keyboard.press("Escape")  # fecha menu ou janela da faixa anterior
        self._ir(leitura.link)
        pagina.wait_for_selector("ytmusic-player-bar", state="attached", timeout=30000)
        estado = self._esperar_a_faixa(leitura.video)
        self._conferir_pagina(estado)
        if not estado["titulo"]:
            leitura.coleta = "indisponivel"
            leitura.erro = estado["indisponivel"] or "a faixa não carregou no player"
            return
        leitura.titulo = estado["titulo"]
        leitura.interprete, leitura.detalhe = interpretar_linha_do_player(estado["linha"])

        itens = self._abrir_menu()
        leitura.menu = itens or []
        resultado = interpretar_menu(itens)
        if resultado == "invalido":
            leitura.coleta, leitura.erro = "erro", "o menu de ações da faixa não abriu (ou abriu outro menu)"
            return
        exibido = {"titulo": leitura.titulo, "interprete": leitura.interprete, "detalhe": leitura.detalhe, "itens_do_menu": itens}
        if resultado == "sem_creditos":
            exibido["constatacao"] = 'o menu da faixa não tem o item "Mostrar créditos da música"'
            leitura.provas.append(captura.capturar(pagina, self.pasta, obra, leitura.interprete, "youtube-music", exibido, "menu")["captura"])
            return
        leitura.tem_item_de_creditos = True
        pagina.locator("tp-yt-iron-dropdown ytmusic-menu-navigation-item-renderer, tp-yt-iron-dropdown ytmusic-menu-service-item-renderer").filter(
            has_text=ITEM_DE_CREDITOS).first.click()
        pagina.wait_for_selector("ytmusic-dismissable-dialog-renderer .section-title", timeout=15000)
        pagina.wait_for_timeout(500)
        janela = pagina.evaluate(_LER_JANELA)
        if not janela or not janela["secoes"]:
            leitura.coleta, leitura.erro = "erro", "a janela de créditos abriu mas não pôde ser lida"
            return
        if normalizar(leitura.titulo) not in normalizar(janela["texto"]):
            leitura.coleta, leitura.erro = "erro", "a janela de créditos aberta não é a desta faixa"
            return
        leitura.secoes = interpretar_janela(janela["secoes"])
        leitura.creditos, leitura.fornecedor = autores_e_fornecedor(leitura.secoes)
        exibido.update(creditos=leitura.secoes)
        leitura.provas.append(captura.capturar(pagina, self.pasta, obra, leitura.interprete, "youtube-music", exibido, "creditos")["captura"])
        pagina.keyboard.press("Escape")

    def _esperar_a_faixa(self, video, limite=90):
        """Espera o player mostrar a faixa pedida. Anúncio antes dela: deixa passar e depois pausa.

        Durante o anúncio a barra mostra o título do anunciante: só vale o título quando o player
        confirma que o vídeo carregado é o pedido e que não há anúncio em exibição.
        """
        pagina, inicio, tocou = self.pagina, time.monotonic(), False
        while True:
            estado = pagina.evaluate(_LER_PLAYER)
            em_anuncio = estado["anuncio"] or "anuncio" in normalizar(estado["linha"])
            if estado["titulo"] and not em_anuncio and estado["video"] == video:
                break
            if estado["indisponivel"] or time.monotonic() - inicio > limite:
                estado["titulo"] = ""  # não confirmou que é a faixa pedida: nada do que está na barra vale
                estado["indisponivel"] = estado["indisponivel"] or "o player não confirmou a faixa pedida dentro do tempo"
                break
            if em_anuncio and not tocou:
                self.avisar("YouTube Music: aguardando o anúncio antes da faixa")
                pagina.locator("ytmusic-player-bar #play-pause-button").click()  # o anúncio só passa tocando
                tocou = True
            pular = pagina.locator(".ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern")
            if pular.count() and pular.first.is_visible():
                pular.first.click()
            pagina.wait_for_timeout(1000)
        pagina.wait_for_timeout(600)  # o botão demora um instante para refletir que a faixa começou
        if "pausar" in normalizar(pagina.evaluate(_LER_PLAYER)["tocando"]):
            pagina.locator("ytmusic-player-bar #play-pause-button").click()  # pausa antes de qualquer outra coisa
        return estado

    def _abrir_menu(self):
        botao = self.pagina.locator("ytmusic-player-bar ytmusic-menu-renderer [aria-label]").first
        for _ in range(2):  # o primeiro clique às vezes pega o botão ainda carregando: repete o clique, não a navegação
            botao.click()
            try:
                self.pagina.wait_for_function(f"({_LER_MENU})() !== null", timeout=4000)
            except Exception:
                continue
            self.pagina.wait_for_timeout(400)
            itens = self.pagina.evaluate(_LER_MENU)
            if interpretar_menu(itens) != "invalido":
                return itens
            self.pagina.keyboard.press("Escape")
        return None

    # --- retomada -------------------------------------------------------------

    def _do_cache(self, video):
        arquivo = self.cache / f"{video}.json"
        if not arquivo.exists():
            return None
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        return Leitura(**{k: v for k, v in dados.items() if k != "v"}) if dados.get("v") == VERSAO_DA_LEITURA else None

    def _para_cache(self, leitura: Leitura):
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / f"{leitura.video}.json").write_text(
            json.dumps({**asdict(leitura), "v": VERSAO_DA_LEITURA}, ensure_ascii=False, indent=1), encoding="utf-8"
        )
