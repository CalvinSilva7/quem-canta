"""O navegador automatizado que os coletores de tela compartilham (Spotify, Tidal, Deezer, Apple Music, Vagalume).

Sempre um Chromium visível, em pt-BR, fuso de Brasília, sem som, sem login e com a
tradução automática travada. Toda navegação passa pelo filtro de dados sensíveis.
Captcha, pedido de login ou tela de bloqueio levantam Bloqueio: a fila para, o que já
foi lido fica salvo, e nada é contornado.
"""

import random
import time
from urllib.parse import unquote

from cantor.matching import normalizar

from . import captura

JANELA = {"width": 1366, "height": 768}
_BLOQUEIO_NA_URL = ("accounts.google.com", "google.com/sorry", "consent.youtube.com", "consent.google.com",
                    "accounts.spotify.com", "challenge.spotify.com", "login.tidal.com", "account.deezer.com",
                    "idmsa.apple.com", "/captcha")
_BLOQUEIO_NO_TEXTO = ("nao e um robo", "trafego incomum", "unusual traffic", "faca login para confirmar",
                      "verify you are human", "confirme que voce e humano", "access denied", "acesso negado")


# Fora da área visível de qualquer monitor: a janela existe (para a plataforma, é um navegador comum), mas não
# aparece na frente de quem está trabalhando.
FORA_DA_TELA = "--window-position=-32000,-32000"


def preparar_asyncio():
    """No Windows, o navegador automatizado precisa do laço de eventos "proactor"; o servidor da tela usa outro."""
    import asyncio
    import sys
    import warnings

    if sys.platform == "win32":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


class Bloqueio(Exception):
    """Captcha, pedido de login, tela de consentimento ou bloqueio. A fila para; nada é contornado."""


def sinal_de_bloqueio(url: str, texto: str = "") -> str:
    if any(parte in url for parte in _BLOQUEIO_NA_URL):
        return f"a plataforma desviou para {url.split('/')[2] if '//' in url else url}"
    limpo = normalizar(texto)
    return next((f'a página pede verificação humana ou login ("{sinal}")' for sinal in _BLOQUEIO_NO_TEXTO if sinal in limpo), "")


class Navegador:
    """Uso: `with Navegador(filtro) as nav: nav.ir(url); nav.pagina...`"""

    def __init__(self, filtro=None, visivel=True, ao_avancar=None, escondido=False):
        self.filtro, self.visivel, self.escondido = filtro, visivel, escondido
        self.avisar = ao_avancar or (lambda texto: None)
        self.navegacoes = 0
        self._ultima = 0.0

    def __enter__(self):
        self._pw = self._navegador = self._pagina = None
        return self

    @property
    def pagina(self):
        """A janela só abre na primeira vez em que é preciso navegar: com tudo já lido, não abre nenhuma."""
        if self._pagina is None:
            from playwright.sync_api import sync_playwright

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

    def ir(self, url, pausa=(2, 4)):
        """Abre a URL respeitando o intervalo mínimo entre páginas. Levanta Bloqueio se a plataforma desviar."""
        if self.filtro:
            self.filtro.conferir(unquote(url))
        falta = random.uniform(*pausa) - (time.monotonic() - self._ultima)
        if falta > 0:
            time.sleep(falta)
        self._ultima = time.monotonic()
        self.navegacoes += 1
        self.pagina.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.conferir()

    def conferir(self, texto=""):
        motivo = sinal_de_bloqueio(self.pagina.url, texto)
        if motivo:
            raise Bloqueio(motivo)
        if self.pagina.evaluate(captura._TRADUZIDA):
            raise captura.PaginaTraduzida(self.pagina.url)

    def clicar_se_houver(self, seletor=None, nome=None, espera=600):
        """Fecha avisos de cookies e parecidos: clica se o botão existir e estiver visível; senão, nada."""
        import re
        alvo = self.pagina.locator(seletor) if seletor else self.pagina.get_by_role("button", name=re.compile(nome, re.I))
        try:
            if alvo.count() and alvo.first.is_visible():
                alvo.first.click()
                self.pagina.wait_for_timeout(espera)
                return True
        except Exception:
            pass
        return False
