"""Amazon Music, aplicativo de desktop (Windows): o único lugar em que a Amazon mostra o compositor.

No aplicativo, o menu de ações da faixa ("⋮") tem o item "Créditos", que abre uma janela com "Compositores".
São dois prints por música: o menu aberto e a janela de créditos.

Como funciona: o aplicativo é uma página web embrulhada num programa. O app o abre com a porta de depuração
ligada e passa a controlá-lo como controla um navegador. Para isso o aplicativo precisa estar instalado e com
a conta já logada: o app nunca digita senha nem faz login, só usa o programa aberto. Se o aplicativo estiver
aberto sem a porta de depuração, ele é fechado e aberto de novo.

Este módulo foi escrito a partir dos prints do escritório, sem um Windows para testar: por isso cada passo
que falha diz exatamente onde parou, e `python -m creditos.amazon_app` roda um diagnóstico que grava num
arquivo de texto o que o aplicativo deixou ver. Nada aqui inventa resultado: o que não puder ser lido vira
erro técnico, nunca "sem créditos".
"""

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from cantor.matching import normalizar

from . import captura
from .amazon import _LER_MENU, _TODOS, Candidato

PLATAFORMA = "AMAZON"
PORTA = 9333
PROCESSO = "Amazon Music.exe"
ITEM_DE_CREDITOS = ("credito", "credit")
# Itens que provam que o menu aberto é o da faixa.
ITENS_DA_FAIXA = ("adicionar a fila", "adicionar a playlist", "compartilhar musica", "reproduzir a proxima", "add to queue")
ROTULOS_DE_AUTOR = ("compositor", "letrista", "autor", "songwriter", "composer", "lyricist", "writer")

# O texto de cada rótulo da janela de créditos e do bloco em volta dele.
_LER_CREDITOS = _TODOS + """.filter(e => e.children.length === 0 && /^(compositor|letrista|autor|songwriter|composer|lyricist|writer)/i
    .test((e.textContent || '').trim()) && (e.textContent || '').trim().length < 40)
  .map(e => ({rotulo: e.textContent.trim(), bloco: ((e.parentElement && e.parentElement.innerText) || '').trim().slice(0, 600)})); }"""
_LER_TEXTO = "() => (document.body.innerText || '').slice(0, 6000)"


class AplicativoIndisponivel(Exception):
    """O aplicativo não está instalado, não abriu, ou não deixou o app se conectar. A coleta dele para; nada é contornado."""


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
    tem_item_de_creditos: bool = False
    creditos: list[str] = field(default_factory=list)
    secoes: dict = field(default_factory=dict)
    provas: list[str] = field(default_factory=list)
    lido_em: str = ""


def _powershell(comando: str) -> list[str]:
    """As linhas que um comando do PowerShell devolve. Fora do Windows, ou se o comando falhar, lista vazia."""
    if sys.platform != "win32":
        return []
    try:
        saida = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
                               capture_output=True, text=True, timeout=25, encoding="utf-8", errors="ignore").stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [linha.strip().strip('"') for linha in saida.splitlines() if linha.strip()]


# Onde o Windows sabe dizer que o Amazon Music está: o programa aberto agora, o registro de programas instalados
# e a lista de aplicativos da Microsoft Store.
_ABERTO = "Get-Process | Where-Object { $_.ProcessName -like '*Amazon*Music*' } | ForEach-Object { $_.Path }"
_REGISTRO = ("Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
             "'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
             "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' -ErrorAction SilentlyContinue | "
             "Where-Object { $_.DisplayName -like '*Amazon Music*' } | ForEach-Object { $_.InstallLocation; $_.DisplayIcon }")
_DA_LOJA = "Get-AppxPackage *AmazonMusic* -ErrorAction SilentlyContinue | ForEach-Object { $_.InstallLocation }"


def _executavel(pista: str) -> Path | None:
    """De uma pista do Windows (o próprio .exe, um ícone "arquivo,0" ou a pasta de instalação), o executável."""
    pista = str(pista or "").strip().strip('"').split(",")[0].strip()
    if not pista:
        return None
    caminho = Path(pista)
    # O programa tem ajudantes na mesma pasta ("Amazon Music Helper.exe"): o que interessa é o principal.
    pasta = caminho.parent if caminho.suffix.lower() == ".exe" else caminho
    if (pasta / PROCESSO).is_file():
        return pasta / PROCESSO
    auxiliar = lambda nome: any(parte in nome.lower() for parte in ("unins", "helper", "crash", "update", "install"))
    if caminho.is_file() and caminho.suffix.lower() == ".exe" and not auxiliar(caminho.name):
        return caminho
    if caminho.is_dir():
        return next((c for c in sorted(caminho.glob("*Amazon*Music*.exe")) if c.is_file() and not auxiliar(c.name)), None)
    return None


def caminho_do_aplicativo() -> Path | None:
    """Onde o Amazon Music está instalado. `QUEMCANTA_AMAZON_EXE` aponta outro lugar, se for o caso.

    Procura nas pastas de costume e, não achando, pergunta ao Windows: o programa que está aberto agora, o
    registro de programas instalados e, por último, os aplicativos da Microsoft Store.
    """
    candidatos = [os.environ.get("QUEMCANTA_AMAZON_EXE", "")]
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", "")):
        if base:
            candidatos += [str(Path(base) / "Amazon Music"), str(Path(base) / "Amazon" / "Amazon Music"),
                           str(Path(base) / "Programs" / "Amazon Music")]
    for pista in candidatos:
        achado = _executavel(pista)
        if achado:
            return achado
    for comando in (_ABERTO, _REGISTRO, _DA_LOJA):
        for pista in _powershell(comando):
            achado = _executavel(pista)
            if achado:
                return achado
    return None


def e_da_loja(caminho) -> bool:
    """O aplicativo veio da Microsoft Store? Esses ficam numa pasta protegida do Windows e costumam recusar ser abertos por outro programa."""
    return "windowsapps" in str(caminho or "").lower()


def interpretar_menu(itens) -> str:
    """O menu da faixa no aplicativo: "com_creditos", "sem_creditos" ou "invalido" (não é o menu da faixa)."""
    limpos = [normalizar(i) for i in itens or [] if str(i or "").strip()]
    if not any(any(sinal in item for sinal in ITENS_DA_FAIXA) for item in limpos):
        return "invalido"
    return "com_creditos" if any(any(sinal in item for sinal in ITEM_DE_CREDITOS) for item in limpos) else "sem_creditos"


def interpretar_creditos(blocos) -> dict:
    """A janela de créditos: [{rotulo, bloco}] -> {"Compositores": ["Fulano", "Beltrano"]}.

    `bloco` é o texto em volta do rótulo: o próprio rótulo numa linha e os nomes nas seguintes, separados por
    vírgula. Linhas que são outro rótulo encerram a lista.
    """
    secoes = {}
    for item in blocos or []:
        rotulo = str(item.get("rotulo", "")).strip()
        linhas = [l.strip() for l in str(item.get("bloco", "")).splitlines() if l.strip()]
        if not rotulo or rotulo not in linhas:
            continue
        nomes = []
        for linha in linhas[linhas.index(rotulo) + 1:]:
            if normalizar(linha).startswith(ROTULOS_DE_AUTOR) or normalizar(linha) in ("creditos", "credits"):
                break
            nomes += [n.strip() for n in linha.split(",") if n.strip()]
        if nomes:
            secoes.setdefault(rotulo, [])
            secoes[rotulo] += [n for n in nomes if n not in secoes[rotulo]]
    return secoes


def autores(secoes: dict) -> list[str]:
    """Os nomes creditados como autores, sem repetição, na ordem em que aparecem."""
    return list(dict.fromkeys(n for rotulo, nomes in secoes.items() if normalizar(rotulo).startswith(ROTULOS_DE_AUTOR) for n in nomes))


class AmazonApp:
    """Uso: `with AmazonApp(pasta) as app: app.ler(candidato, obra)`. O aplicativo só é aberto na primeira leitura."""

    def __init__(self, pasta, ao_avancar=None, so_o_que_ja_foi_lido=False, porta=PORTA):
        self.pasta = Path(pasta)
        self.cache = self.pasta / "leituras"
        self.avisar = ao_avancar or (lambda texto: None)
        self.so_o_que_ja_foi_lido, self.porta = so_o_que_ja_foi_lido, porta
        self.navegacoes = 0
        self._pw = self._navegador = self._pagina = None

    def __enter__(self):
        return self

    def __exit__(self, *erro):
        if self._navegador:
            try:
                self._navegador.close()  # só desfaz a conexão: o aplicativo continua aberto, com a conta logada
            except Exception:
                pass
        if self._pw:
            self._pw.stop()

    # --- conexão --------------------------------------------------------------

    def _responde(self) -> bool:
        import requests

        try:
            return requests.get(f"http://127.0.0.1:{self.porta}/json/version", timeout=2).ok
        except requests.RequestException:
            return False

    def _abrir_o_aplicativo(self):
        if self._responde():
            return
        if sys.platform != "win32":
            raise AplicativoIndisponivel("a coleta no aplicativo da Amazon Music só existe no Windows")
        caminho = caminho_do_aplicativo()
        if caminho is None:
            raise AplicativoIndisponivel(
                "o aplicativo Amazon Music não foi encontrado neste computador. Instale-o pelo site da Amazon (não pela "
                "Microsoft Store), abra uma vez e faça login com a conta do escritório")
        self.avisar("Amazon Music (aplicativo): abrindo o aplicativo")
        subprocess.run(["taskkill", "/IM", caminho.name, "/F"], capture_output=True)  # aberto sem a porta de depuração não serve
        time.sleep(2)
        try:
            subprocess.Popen([str(caminho), f"--remote-debugging-port={self.porta}"], close_fds=True)
        except OSError as e:
            raise AplicativoIndisponivel(
                f"o Windows não deixou abrir o Amazon Music a partir de {caminho} ({type(e).__name__})"
                + (". Esta é a versão da Microsoft Store: desinstale-a e instale a do site da Amazon" if e_da_loja(caminho) else "")) from e
        limite = time.monotonic() + 60
        while time.monotonic() < limite:
            if self._responde():
                time.sleep(6)  # a tela inicial ainda está carregando
                return
            time.sleep(1.5)
        raise AplicativoIndisponivel(
            "o aplicativo abriu, mas não aceitou a conexão do app (porta de depuração)"
            + (". Esta é a versão da Microsoft Store, que não aceita: desinstale-a e instale a do site da Amazon" if e_da_loja(caminho)
               else '. Rode o diagnóstico ("Testar Amazon.cmd") e mande o arquivo gerado para quem cuida do app'))

    @property
    def pagina(self):
        if self._pagina is None:
            from playwright.sync_api import sync_playwright

            from .navegador import preparar_asyncio

            self._abrir_o_aplicativo()
            preparar_asyncio()
            self._pw = sync_playwright().start()
            try:
                self._navegador = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.porta}", timeout=30000)
            except Exception as e:
                raise AplicativoIndisponivel(f"não foi possível se conectar ao aplicativo ({type(e).__name__})") from e
            paginas = [p for contexto in self._navegador.contexts for p in contexto.pages]
            if not paginas:
                raise AplicativoIndisponivel("o aplicativo abriu sem nenhuma tela visível para o app")
            # A tela principal é a que tem os componentes da Amazon Music; na dúvida, a primeira.
            self._pagina = next((p for p in paginas if "amazon" in (p.url or "").lower() or "music" in (p.url or "").lower()), paginas[0])
        return self._pagina

    def _endereco(self, cand: Candidato) -> str:
        """O endereço do álbum dentro do aplicativo, na mesma origem em que ele está rodando."""
        origem = urlsplit(self.pagina.url)
        if origem.scheme not in ("http", "https") or not origem.netloc:
            raise AplicativoIndisponivel(
                f'o aplicativo usa um endereço interno ("{self.pagina.url[:60]}") que esta versão do app não sabe navegar')
        return f"{origem.scheme}://{origem.netloc}/albums/{cand.album}?trackAsin={cand.faixa}"

    # --- leitura de uma faixa -------------------------------------------------

    def ler(self, cand: Candidato, obra: str) -> Leitura:
        """Abre a faixa no aplicativo, fotografa o menu e a janela de créditos. Leitura completa fica guardada."""
        arquivo = self.cache / f"{cand.faixa}.json"
        if arquivo.exists():
            return Leitura(**json.loads(arquivo.read_text(encoding="utf-8")))
        leitura = Leitura(faixa=cand.faixa, link=cand.link, titulo=cand.titulo, interprete=cand.interprete,
                          lido_em=captura.agora().isoformat(timespec="seconds"))
        if self.so_o_que_ja_foi_lido:
            leitura.coleta, leitura.erro = "erro", "faixa ainda não lida: a coleta foi interrompida antes dela"
            return leitura
        try:
            self._ler(leitura, cand, obra)
        except AplicativoIndisponivel:
            raise
        except Exception as e:  # timeout, elemento que sumiu: erro técnico, nunca "sem créditos"
            leitura.coleta, leitura.erro = "erro", f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
        if leitura.coleta == "ok":
            self.cache.mkdir(parents=True, exist_ok=True)
            arquivo.write_text(json.dumps(asdict(leitura), ensure_ascii=False), encoding="utf-8")
        return leitura

    def _linha_da_faixa(self, cand: Candidato):
        pagina = self.pagina
        for seletor in (f"[primary-href*='{cand.faixa}']", f"[primary-text=\"{cand.titulo}\"]"):
            linha = pagina.locator(seletor)
            if linha.count():
                return linha.first
        return None

    def _ler(self, leitura: Leitura, cand: Candidato, obra: str):
        pagina = self.pagina
        pagina.keyboard.press("Escape")  # fecha menu ou janela da faixa anterior
        time.sleep(3)  # sem pressa: o aplicativo é da conta do escritório
        self.navegacoes += 1
        pagina.goto(self._endereco(cand), wait_until="domcontentloaded", timeout=60000)
        linha = None
        limite = time.monotonic() + 30
        while linha is None and time.monotonic() < limite:
            linha = self._linha_da_faixa(cand)
            if linha is None:
                pagina.wait_for_timeout(1000)
        if linha is None:
            leitura.coleta, leitura.erro = "indisponivel", "a faixa não apareceu na lista do álbum dentro do aplicativo"
            return
        pagina.wait_for_timeout(1500)
        linha.scroll_into_view_if_needed()
        itens = None
        for _ in range(2):  # o primeiro clique às vezes pega o botão ainda carregando
            linha.hover()
            linha.locator("music-button[icon-name='more'], music-button[icon-name='moreVertical'], [icon-name*='more']").last.click()
            pagina.wait_for_timeout(1200)
            itens = [i for i in pagina.evaluate(_LER_MENU) if i]
            if interpretar_menu(itens) != "invalido":
                break
            pagina.keyboard.press("Escape")
        leitura.menu = itens or []
        resultado = interpretar_menu(itens)
        if resultado == "invalido":
            leitura.coleta, leitura.erro = "erro", "o menu de ações da faixa não abriu no aplicativo (ou abriu outro menu)"
            return
        exibido = {"titulo": cand.titulo, "interprete": cand.interprete, "itens_do_menu": itens}
        if resultado == "sem_creditos":
            exibido["constatacao"] = 'o menu da faixa no aplicativo não tem o item "Créditos"'
            leitura.provas.append(captura.capturar(pagina, self.pasta, obra, cand.interprete, "amazon-app", exibido, "menu")["captura"])
            pagina.keyboard.press("Escape")
            return
        leitura.tem_item_de_creditos = True
        leitura.provas.append(captura.capturar(pagina, self.pasta, obra, cand.interprete, "amazon-app", exibido, "menu")["captura"])
        import re
        pagina.locator("music-list-item").filter(has_text=re.compile(r"cr[eé]dito|credit", re.I)).first.click()
        blocos = []
        limite = time.monotonic() + 15
        while not blocos and time.monotonic() < limite:
            pagina.wait_for_timeout(700)
            blocos = pagina.evaluate(_LER_CREDITOS)
        leitura.secoes = interpretar_creditos(blocos)
        leitura.creditos = autores(leitura.secoes)
        texto = normalizar(pagina.evaluate(_LER_TEXTO))
        if "creditos" not in texto and "credits" not in texto:
            leitura.coleta, leitura.erro = "erro", "a janela de créditos não abriu no aplicativo"
            return
        exibido.update(creditos=leitura.secoes)
        if not leitura.creditos:
            exibido["constatacao"] = "a janela de créditos abriu e não traz compositor"
        leitura.provas.append(captura.capturar(pagina, self.pasta, obra, cand.interprete, "amazon-app", exibido, "creditos")["captura"])
        pagina.keyboard.press("Escape")


# --- diagnóstico ----------------------------------------------------------------

def diagnostico(destino=None, album="") -> Path:
    """Tenta cada passo da coleta num álbum qualquer e grava o que viu. Não lê conta, não altera nada no aplicativo.

    O álbum é o primeiro que aparecer na tela inicial do aplicativo (ou o de `QUEMCANTA_AMAZON_ALBUM`): o teste
    é do mecanismo, não de um repertório.

    O arquivo gerado é o que permite ajustar este módulo sem ter o aplicativo à mão.
    """
    destino = Path(destino or Path.home() / "Documents" / "Quem Canta" / "diagnostico-amazon")
    destino.mkdir(parents=True, exist_ok=True)
    linhas = [f"Diagnóstico do aplicativo Amazon Music - {captura.agora().isoformat(timespec='seconds')}", f"sistema: {sys.platform}"]

    def anotar(texto):
        linhas.append(str(texto))
        print(texto, flush=True)

    def tentar(nome, funcao):
        try:
            valor = funcao()
            anotar(f"[ok] {nome}: {str(valor)[:900]}")
            return valor
        except Exception as e:
            anotar(f"[FALHOU] {nome}: {type(e).__name__}: {str(e)[:400]}")
            return None

    # O que o Windows sabe sobre o Amazon Music: é o que permite achar o programa quando ele não está na pasta de costume.
    anotar(f"Amazon Music aberto agora (programa em execução): {_powershell(_ABERTO) or 'nenhum'}")
    anotar(f"Amazon Music no registro de programas instalados: {_powershell(_REGISTRO) or 'nada'}")
    anotar(f"Amazon Music entre os aplicativos da Microsoft Store: {_powershell(_DA_LOJA) or 'nada'}")
    achado = caminho_do_aplicativo()
    anotar(f"aplicativo instalado em: {achado}" + (" (versão da Microsoft Store)" if e_da_loja(achado) else ""))
    app = AmazonApp(destino, ao_avancar=anotar)
    try:
        pagina = tentar("conectar ao aplicativo", lambda: app.pagina)
        if pagina is not None:
            tentar("telas abertas", lambda: [(p.url[:120], p.title()[:60]) for c_ in app._navegador.contexts for p in c_.pages])
            tentar("endereço da tela principal", lambda: pagina.url)
            tentar("componentes da tela", lambda: pagina.evaluate(_TODOS + """.reduce((c, e) => { const t = e.tagName.toLowerCase();
                if (t.includes('-')) c[t] = (c[t] || 0) + 1; return c; }, {}); }"""))
            tentar("texto da tela inicial", lambda: " | ".join(l for l in pagina.evaluate(_LER_TEXTO).split("\n") if l.strip())[:700])
            album = album or os.environ.get("QUEMCANTA_AMAZON_ALBUM", "") or tentar("um álbum da tela inicial", lambda: pagina.evaluate(
                _TODOS + """.map(e => ((e.getAttribute && (e.getAttribute('primary-href') || e.getAttribute('href'))) || '').match(/\\/albums\\/([A-Z0-9]{8,})/))
                .filter(Boolean).map(m => m[1])[0] || ''; }""")) or ""
            cand = Candidato(album, "", "", "")
            endereco = album and tentar("endereço do álbum de teste", lambda: app._endereco(cand).split("?")[0])
            if endereco and tentar("abrir o álbum de teste", lambda: pagina.goto(endereco, wait_until="domcontentloaded", timeout=60000) and "aberto"):
                pagina.wait_for_timeout(8000)
                tentar("endereço depois de abrir", lambda: pagina.url)
                tentar("texto do álbum", lambda: " | ".join(l for l in pagina.evaluate(_LER_TEXTO).split("\n") if l.strip())[:700])
                tentar("linhas de faixa", lambda: pagina.evaluate(_TODOS + """.filter(e => e.getAttribute && e.getAttribute('primary-text') && /row|item/.test(e.tagName.toLowerCase()))
                    .slice(0, 4).map(e => ({tag: e.tagName.toLowerCase(), texto: e.getAttribute('primary-text'), href: e.getAttribute('primary-href'),
                    botoes: [...(e.shadowRoot || e).querySelectorAll('music-button, button')].map(b => b.getAttribute('icon-name') || b.getAttribute('aria-label') || '?')})); }"""))
                tentar("print do álbum", lambda: pagina.screenshot(path=str(destino / "1-album.png")) and "1-album.png")
                linha = tentar("achar a primeira faixa", lambda: pagina.locator("[primary-href*='/tracks/']").first if pagina.locator("[primary-href*='/tracks/']").count() else None)
                if linha is not None:
                    tentar("abrir o menu da faixa", lambda: (linha.hover(), linha.locator("music-button[icon-name='more'], music-button[icon-name='moreVertical'], [icon-name*='more']").last.click(), pagina.wait_for_timeout(1500)) and "clicado")
                    itens = tentar("itens do menu", lambda: [i for i in pagina.evaluate(_LER_MENU) if i])
                    tentar("leitura do menu", lambda: interpretar_menu(itens))
                    tentar("print do menu", lambda: pagina.screenshot(path=str(destino / "2-menu.png")) and "2-menu.png")
                    if itens and interpretar_menu(itens) == "com_creditos":
                        import re
                        tentar("clicar em Créditos", lambda: pagina.locator("music-list-item").filter(has_text=re.compile(r"cr[eé]dito|credit", re.I)).first.click() or "clicado")
                        pagina.wait_for_timeout(3000)
                        blocos = tentar("blocos da janela de créditos", lambda: pagina.evaluate(_LER_CREDITOS))
                        tentar("créditos lidos", lambda: interpretar_creditos(blocos))
                        tentar("texto com a janela aberta", lambda: " | ".join(l for l in pagina.evaluate(_LER_TEXTO).split("\n") if l.strip())[-500:])
                        tentar("print dos créditos", lambda: pagina.screenshot(path=str(destino / "3-creditos.png")) and "3-creditos.png")
                        tentar("fechar a janela", lambda: pagina.keyboard.press("Escape") or "fechada")
    finally:
        app.__exit__()
    relatorio = destino / "diagnostico-amazon.txt"
    relatorio.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"\nDiagnóstico gravado em: {relatorio}")
    return relatorio


if __name__ == "__main__":
    diagnostico()
