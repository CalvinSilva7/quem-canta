"""Claro Música (claromusica.com): o compositor aparece em "Informações e créditos", no menu da faixa.

O site só mostra o catálogo para quem está logado. O app não digita senha nem faz login: usa a sessão que a
pessoa abriu, uma vez, numa janela do próprio navegador do app (ver `entrar`), guardada num perfil só dele.
Sem essa sessão a coleta para e avisa.

A faixa é achada pela lista completa de músicas da busca (`/predictiveDetail/songs/<título>`), que traz, de cada
resultado, o título, os artistas, o álbum e a duração. O print é o do escritório: a página do álbum, com o menu
da faixa aberto e o cartão de informações por cima dele (Música, Duração, Artista, Autores, Álbum, Gravadora,
Ano). É um print por música.

O menu abre para baixo e, numa faixa do fim do álbum, não cabe na tela: o item de informações fica fora de
alcance. O escritório resolve diminuindo o zoom do navegador; aqui a página é aumentada (ou, no print de tela
inteira, o zoom é reduzido) até o menu e o cartão caberem.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from cantor.matching import normalizar

from . import captura

SITE = "https://www.claromusica.com/"
PLATAFORMA = "CLARO"
ROTULOS = ("musica", "duracao", "artista", "autores", "album", "gravadora", "ano")
# O que o site mostra a quem não está logado, em vez do catálogo.
SINAIS_DE_SEM_LOGIN = ("iniciar sessao", "inicie sessao", "faca login", "entrar com", "cadastre-se", "continuar com e-mail")

_LER_RESULTADOS = """() => [...document.querySelectorAll('tr.song')].map(l => {
  const faixa = ([...l.classList].find(c => /^song-\\d+$/.test(c)) || '').slice(5);
  const album = ((l.querySelector('td[id^="song-"]') || {}).id || '').replace(/^song-/, '').split('-')[0];
  const celula = (fim) => l.querySelector('td[id$="-' + fim + '"]');
  return {faixa, album, titulo: ((l.querySelector('td span') || {}).textContent || '').trim(),
          artistas: celula('artist') ? [...celula('artist').querySelectorAll('a')].map(a => a.textContent.trim()).filter(Boolean) : [],
          nome_do_album: celula('album') ? celula('album').textContent.trim() : '',
          duracao: celula('duration') ? celula('duration').textContent.trim() : ''};
})"""
# A lista de artistas da busca: o código e o nome de cada um.
_LER_ARTISTAS = """() => [...document.querySelectorAll('td[id^="artist-"]')].filter(c => /^artist-\\d+$/.test(c.id))
  .map(c => [c.id.slice(7), (c.getAttribute('title') || c.textContent || '').trim()])"""
# Os álbuns que a página do artista mostra: o código e o nome.
_LER_ALBUNS = """() => { const vistos = {}; for (const a of document.querySelectorAll('a[href*="/album/"]')) {
  const codigo = ((a.getAttribute('href') || '').match(/\\/album\\/(\\d+)/) || [])[1]; const nome = a.textContent.trim();
  if (codigo && (nome || !(codigo in vistos))) vistos[codigo] = nome || vistos[codigo] || ''; }
  return Object.entries(vistos); }"""
# As faixas da página de um álbum: número, título, artistas e duração.
_LER_FAIXAS = """() => [...document.querySelectorAll('.list-content-row')].map(l => ({
  numero: ((l.querySelector('.song-numbered') || {}).textContent || '').trim(),
  titulo: ((l.querySelector('.album-song-name') || {}).textContent || '').trim(),
  artistas: [...l.querySelectorAll('.cd-artist-name a')].map(a => a.textContent.trim()).filter(Boolean),
  duracao: ((l.querySelector('.song-duration') || {}).textContent || '').trim()}))"""
# A linha da faixa na página do álbum (pelo título e, havendo, pela duração) e onde está o botão de opções dela.
_ACHAR_LINHA = """([titulo, duracao]) => {
  const linhas = [...document.querySelectorAll('.list-content-row')].filter(l => {
    const nome = l.querySelector('.album-song-name'); return nome && nome.textContent.trim() === titulo; });
  const linha = linhas.find(l => duracao && (l.innerText || '').includes(duracao)) || linhas[0];
  if (!linha) return null;
  // A faixa vai para o alto da tela, para o menu (que abre para baixo) e o cartão caberem sem aumentar a página.
  // Numa faixa do fim do álbum não há para onde rolar: um espaço em branco depois da lista dá essa folga.
  if (!document.getElementById('__quemcanta_folga')) {
    const folga = document.createElement('div'); folga.id = '__quemcanta_folga'; folga.style.height = '900px';
    linha.parentElement.appendChild(folga);
  }
  let rolavel = linha.parentElement;
  while (rolavel && rolavel !== document.body && !(rolavel.scrollHeight > rolavel.clientHeight + 4 && /auto|scroll/.test(getComputedStyle(rolavel).overflowY)))
    rolavel = rolavel.parentElement;
  if (!rolavel || rolavel === document.body) rolavel = document.scrollingElement;
  // Só rola se a faixa ainda não estiver no lugar: rolar de novo logo antes do clique fecharia o menu recém-aberto.
  const antes = rolavel.scrollTop;
  if (linha.getAttribute('data-quemcanta-rolagem') !== String(antes)) {
    linha.scrollIntoView({block: 'start'});
    rolavel.scrollBy(0, -130);
    linha.setAttribute('data-quemcanta-rolagem', String(rolavel.scrollTop));
  }
  const botao = linha.querySelector('.cm-icon-more-vertical');
  if (!botao) return {sem_botao: true};
  const q = botao.getBoundingClientRect();
  return {x: q.x + q.width / 2, y: q.y + q.height / 2, repetidas: linhas.length, moveu: rolavel.scrollTop !== antes};
}"""
# O menu aberto: os itens, onde está o de informações e se o menu inteiro cabe na tela.
_LER_MENU = """() => {
  const menu = document.querySelector('.context-menu-open');
  if (!menu) return null;
  const item = [...menu.querySelectorAll('a')].find(a => /informa/i.test(a.textContent));
  const caixa = menu.getBoundingClientRect(), q = item ? item.getBoundingClientRect() : null;
  return {itens: [...menu.querySelectorAll('li')].map(l => l.textContent.trim()).filter(Boolean),
          texto: (menu.innerText || '').trim().slice(0, 200), cabe: caixa.bottom <= innerHeight - 4 && caixa.top >= 0,
          falta: Math.max(0, Math.ceil(caixa.bottom - innerHeight + 8)), item: q ? {x: q.x + q.width / 2, y: q.y + q.height / 2} : null};
}"""
# O cartão de informações: o menor bloco à vista que traz "Música" e "Ano" (ou "Gravadora"), e se ele cabe na tela.
_LER_CARTAO = """() => {
  const limpo = (t) => (t || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').trim().toLowerCase();
  const rotulo = [...document.querySelectorAll('*')].filter(e => e.children.length === 0 && limpo(e.textContent) === 'duracao'
    && e.getBoundingClientRect().width > 0).pop();
  if (!rotulo) return null;
  let cartao = rotulo;
  for (let i = 0; i < 8 && cartao.parentElement; i++) {
    const texto = limpo(cartao.innerText);
    if (/musica/.test(texto) && /artista/.test(texto) && /album/.test(texto)) break;
    cartao = cartao.parentElement;
  }
  const caixa = cartao.getBoundingClientRect();
  return {linhas: (cartao.innerText || '').split('\\n').map(l => l.trim()).filter(Boolean),
          cabe: caixa.bottom <= innerHeight - 4 && caixa.top >= 0, falta: Math.max(0, Math.ceil(caixa.bottom - innerHeight + 8))};
}"""


class SemLogin(Exception):
    """A Claro Música não está logada no navegador do app: a coleta dela não pode seguir."""


@dataclass
class Candidato:
    album: str
    faixa: str
    titulo: str = ""
    interprete: str = ""
    nome_do_album: str = ""
    duracao: str = ""

    @property
    def link(self):
        # o endereço é o do álbum, como o escritório anota; o trecho final só distingue as faixas do mesmo álbum
        return f"{SITE}album/{self.album}/BR#faixa-{self.faixa}"


@dataclass
class Leitura:
    faixa: str
    link: str = ""
    coleta: str = "ok"  # "ok", "erro" ou "indisponivel"
    erro: str = ""
    titulo: str = ""
    interprete: str = ""
    album: str = ""
    creditos: list[str] = field(default_factory=list)
    informacoes: dict = field(default_factory=dict)
    gravadora: str = ""
    ano: str = ""
    provas: list[str] = field(default_factory=list)
    lido_em: str = ""


def interpretar_resultados(itens) -> list[Candidato]:
    """As linhas da lista de músicas da busca -> candidatos, sem repetição e só com o que tem álbum e faixa."""
    candidatos, vistos = [], set()
    for item in itens or []:
        faixa, album, titulo = str(item.get("faixa") or ""), str(item.get("album") or ""), (item.get("titulo") or "").strip()
        if not (faixa.isdigit() and album.isdigit() and titulo) or faixa in vistos:
            continue
        vistos.add(faixa)
        candidatos.append(Candidato(album, faixa, titulo, " & ".join(item.get("artistas") or []), item.get("nome_do_album") or "",
                                    item.get("duracao") or ""))
    return candidatos


def interpretar_cartao(linhas) -> dict:
    """O cartão de informações: ["Música", "Tal", "Duração", "02:57", "Autores", "A, B", ...] -> {"Autores": "A, B", ...}.

    As duas primeiras linhas repetem o título e o artista (o cabeçalho do cartão) e ficam de fora. Rótulo sem
    valor (a Claro não tem o dado) fica com texto vazio.
    """
    campos, atual = {}, None
    for linha in linhas or []:
        if normalizar(linha) in ROTULOS:
            atual = linha
            campos[atual] = ""
        elif atual is not None:
            campos[atual] = (campos[atual] + " " + linha).strip()
    return campos


def campo(informacoes: dict, nome: str) -> str:
    return next((valor for rotulo, valor in (informacoes or {}).items() if normalizar(rotulo) == nome), "")


def autores(informacoes: dict) -> list[str]:
    """Os nomes do campo "Autores", sem repetição. A Claro separa por vírgula; em algumas faixas, por barra."""
    import re
    return list(dict.fromkeys(n.strip() for n in re.split(r"[,/;]", campo(informacoes, "autores")) if n.strip()))


class Claro:
    """Uso: `Claro(navegador, pasta)`; `buscar(consulta)` e `ler(candidato, obra)`. O navegador precisa usar o
    perfil em que a pessoa fez o login."""

    def __init__(self, navegador, pasta, so_o_que_ja_foi_lido=False):
        self.nav, self.pasta = navegador, Path(pasta)
        self.cache = self.pasta / "leituras"
        self.so_o_que_ja_foi_lido = so_o_que_ja_foi_lido
        self._conferido = False

    def conferir_login(self):
        """Levanta SemLogin se o site não mostrar o catálogo. Roda uma vez por coleta, na primeira página aberta."""
        if self._conferido:
            return
        pagina = self.nav.pagina
        texto = normalizar(pagina.evaluate("document.body.innerText.slice(0, 3000)"))
        if any(parte in pagina.url for parte in ("/login", "/landing", "/signin")) or any(s in texto for s in SINAIS_DE_SEM_LOGIN):
            raise SemLogin('a Claro Música não está logada no navegador do app. Clique em "Entrar na Claro Música", faça o login na '
                           "janela que abrir e colete de novo")
        self._conferido = True

    def buscar(self, consulta: str) -> list[Candidato]:
        arquivo = self.cache / "_buscas.json"
        guardadas = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}
        if consulta in guardadas:
            return [Candidato(**c) for c in guardadas[consulta]]
        if self.so_o_que_ja_foi_lido:
            return []
        pagina = self.nav.pagina
        self.nav.ir(SITE + "predictiveDetail/songs/" + quote(consulta, safe=""), pausa=(4, 7))
        try:
            pagina.wait_for_selector("tr.song", timeout=20000)
            pagina.wait_for_timeout(1500)
        except Exception:  # busca sem resultado, ou o site pediu outra coisa
            self.nav.conferir(pagina.evaluate("document.body.innerText.slice(0, 1500)"))
        self.conferir_login()
        candidatos = interpretar_resultados(pagina.evaluate(_LER_RESULTADOS))
        guardadas[consulta] = [asdict(c) for c in candidatos]
        self.cache.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(guardadas, ensure_ascii=False), encoding="utf-8")
        return candidatos

    # --- último recurso: pela página do artista ------------------------------------

    def _guardado(self, nome: str) -> dict:
        arquivo = self.cache / f"{nome}.json"
        return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}

    def _guardar(self, nome: str, dados: dict):
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / f"{nome}.json").write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")

    def _lista(self, nome_do_cache: str, chave: str, endereco: str, esperar: str, script: str) -> list:
        """Abre uma página de lista, lê com o script e guarda; lista vazia não é guardada (pode ter sido falha)."""
        guardado = self._guardado(nome_do_cache)
        if chave in guardado:
            return guardado[chave]
        if self.so_o_que_ja_foi_lido:
            return []
        pagina = self.nav.pagina
        self.nav.ir(endereco, pausa=(4, 7))
        try:
            pagina.wait_for_selector(esperar, timeout=20000)
            pagina.wait_for_timeout(1500)
        except Exception:
            self.nav.conferir(pagina.evaluate("document.body.innerText.slice(0, 1500)"))
        self.conferir_login()
        itens = pagina.evaluate(script)
        if itens:
            guardado[chave] = itens
            self._guardar(nome_do_cache, guardado)
        return itens

    def buscar_pelo_artista(self, nome: str, mesmo_artista, e_da_obra, albuns_no_maximo=25, no_maximo=2) -> list[Candidato]:
        """Quando a busca por título não traz a faixa do intérprete (título comum demais): acha o artista pelo nome,
        abre os álbuns que a página dele mostra e procura neles a faixa da obra.

        `mesmo_artista(nome exibido)` diz se o artista é o procurado; `e_da_obra(título exibido)`, se a faixa é a obra.
        Os álbuns com o título da obra no nome são abertos primeiro. Só entram artistas e faixas aprovados por elas.
        """
        achados = []
        artistas = [(codigo, exibido) for codigo, exibido in self._lista("_artistas", nome, SITE + "predictiveDetail/artists/" + quote(nome, safe=""),
                                                                         'td[id^="artist-"]', _LER_ARTISTAS) if mesmo_artista(exibido)]
        for codigo, exibido in artistas[:2]:
            albuns = self._lista("_albuns_do_artista", codigo, f"{SITE}artist/{codigo}/BR", 'a[href*="/album/"]', _LER_ALBUNS)
            albuns = sorted(albuns, key=lambda a: not e_da_obra(a[1]))[:albuns_no_maximo]  # primeiro os que levam o nome da obra
            for album, nome_do_album in albuns:
                for faixa in self._lista("_faixas_do_album", album, f"{SITE}album/{album}/BR", ".list-content-row", _LER_FAIXAS):
                    if faixa.get("titulo") and e_da_obra(faixa["titulo"]):
                        achados.append(Candidato(album, f"{album}n{faixa.get('numero') or len(achados)}", faixa["titulo"],
                                                 " & ".join(faixa.get("artistas") or []) or exibido, nome_do_album, faixa.get("duracao") or ""))
                if len(achados) >= no_maximo:
                    return achados[:no_maximo]
        return achados

    def ler(self, cand: Candidato, obra: str) -> Leitura:
        """Abre o álbum, o menu da faixa e o cartão de informações, e tira o print. Leitura completa fica guardada."""
        arquivo = self.cache / f"{cand.faixa}.json"
        if arquivo.exists():
            return Leitura(**json.loads(arquivo.read_text(encoding="utf-8")))
        leitura = Leitura(faixa=cand.faixa, link=cand.link, titulo=cand.titulo, interprete=cand.interprete, album=cand.nome_do_album,
                          lido_em=captura.agora().isoformat(timespec="seconds"))
        if self.so_o_que_ja_foi_lido:
            leitura.coleta, leitura.erro = "erro", "faixa ainda não lida: a coleta foi interrompida antes dela"
            return leitura
        from .navegador import Bloqueio

        try:
            self._ler(leitura, cand, obra)
        except (Bloqueio, captura.PaginaTraduzida, SemLogin):
            raise
        except Exception as e:  # timeout, elemento que sumiu: erro técnico, nunca "sem créditos"
            leitura.coleta, leitura.erro = "erro", f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
        finally:
            self._desfazer_o_ajuste()
        if leitura.coleta == "ok":
            self.cache.mkdir(parents=True, exist_ok=True)
            arquivo.write_text(json.dumps(asdict(leitura), ensure_ascii=False), encoding="utf-8")
        return leitura

    # --- o ajuste de tamanho para o menu e o cartão caberem ------------------------

    def _ajustar(self, falta: int, tentativa: int):
        """Dá mais espaço à página: aumenta a altura dela ou, no print de tela inteira, reduz o zoom."""
        pagina = self.nav.pagina
        tamanho = pagina.viewport_size
        if tamanho:  # página de tamanho fixo: basta ser mais alta
            self._tamanho_de_antes = getattr(self, "_tamanho_de_antes", None) or dict(tamanho)
            pagina.set_viewport_size({"width": tamanho["width"], "height": tamanho["height"] + falta + 160})
        else:  # a página acompanha a janela (tela inteira): zoom menor, como o escritório faz à mão
            pagina.evaluate("(z) => { document.documentElement.style.zoom = z; }", ("0.8", "0.67", "0.5")[min(tentativa, 2)])
        pagina.wait_for_timeout(700)

    def _desfazer_o_ajuste(self):
        try:
            pagina = self.nav.pagina
            if getattr(self, "_tamanho_de_antes", None):
                pagina.set_viewport_size(self._tamanho_de_antes)
                self._tamanho_de_antes = None
            pagina.evaluate("() => { document.documentElement.style.zoom = ''; const f = document.getElementById('__quemcanta_folga'); if (f) f.remove(); }")
        except Exception:
            pass

    def _abrir_o_menu(self, cand: Candidato) -> dict | None:
        pagina = self.nav.pagina
        pagina.keyboard.press("Escape")
        pagina.mouse.click(640, 30)  # fora de qualquer menu: fecha o que estiver aberto
        pagina.wait_for_timeout(500)
        onde = None
        for _ in range(4):  # até a lista parar de se mexer: clicar com ela ainda rolando fecha o menu
            onde = pagina.evaluate(_ACHAR_LINHA, [cand.titulo, cand.duracao])
            pagina.wait_for_timeout(700)
            if not onde or not onde.get("moveu"):
                break
        if not onde or onde.get("sem_botao"):
            return None
        pagina.mouse.click(onde["x"], onde["y"])
        pagina.wait_for_timeout(1500)
        return pagina.evaluate(_LER_MENU)

    def _ler(self, leitura: Leitura, cand: Candidato, obra: str):
        pagina = self.nav.pagina
        self.nav.ir(cand.link, pausa=(4, 7))
        try:
            pagina.wait_for_selector(".list-content-row", timeout=25000)
        except Exception:
            self.nav.conferir(pagina.evaluate("document.body.innerText.slice(0, 1500)"))
            self.conferir_login()
            leitura.coleta, leitura.erro = "indisponivel", "o álbum não carregou as faixas"
            return
        pagina.wait_for_timeout(1500)
        self.conferir_login()
        if not pagina.evaluate(_ACHAR_LINHA, [cand.titulo, cand.duracao]):
            leitura.coleta, leitura.erro = "indisponivel", "a faixa não aparece na lista do álbum"
            return
        menu = cartao = None
        for tentativa in range(7):  # menu e cartão podem pedir mais espaço, cada um mais de uma vez
            menu = self._abrir_o_menu(cand)
            if not menu or not menu.get("item"):
                leitura.coleta = "erro"
                leitura.erro = "o menu da faixa não abriu" if not menu else 'o menu da faixa não tem o item "Informações e créditos"'
                return
            if normalizar(cand.titulo) not in normalizar(menu["texto"]):
                leitura.coleta, leitura.erro = "erro", "o menu aberto não é o da faixa pedida"
                return
            if not menu["cabe"]:
                self._ajustar(menu["falta"], tentativa)
                continue
            pagina.mouse.click(menu["item"]["x"], menu["item"]["y"])
            pagina.wait_for_timeout(1800)
            cartao = pagina.evaluate(_LER_CARTAO)
            if cartao and not cartao["cabe"]:
                self._ajustar(cartao["falta"], tentativa)
                cartao = None
                continue
            break
        if not cartao:
            leitura.coleta, leitura.erro = "erro", "o cartão de informações da faixa não abriu (ou não coube na tela)"
            return
        leitura.informacoes = interpretar_cartao(cartao["linhas"])
        if normalizar(campo(leitura.informacoes, "musica")) != normalizar(cand.titulo):
            leitura.coleta, leitura.erro = "erro", f'o cartão aberto é de "{campo(leitura.informacoes, "musica")}", e não da faixa pedida'
            return
        leitura.creditos = autores(leitura.informacoes)
        leitura.gravadora, leitura.ano = campo(leitura.informacoes, "gravadora"), campo(leitura.informacoes, "ano")
        leitura.album = campo(leitura.informacoes, "album") or leitura.album
        self.nav.conferir()
        exibido = {"titulo": cand.titulo, "interprete": cand.interprete, "informacoes": leitura.informacoes, "itens_do_menu": menu["itens"]}
        if not leitura.creditos:
            exibido["constatacao"] = 'o cartão de informações da faixa não traz nome nenhum em "Autores"'
        leitura.provas.append(captura.capturar(pagina, self.pasta, obra, cand.interprete, "claro", exibido, "informacoes")["captura"])
        pagina.keyboard.press("Escape")


def entrar(perfil, espera=900) -> bool:
    """Abre a Claro Música numa janela do navegador do app, com o perfil dele, para a pessoa fazer o login.

    O app não preenche nada: só espera a janela ser fechada (ou o prazo), e a sessão fica guardada no perfil.
    Devolve True se, ao fim, o site mostrava o catálogo (sessão aberta).
    """
    import time
    from playwright.sync_api import sync_playwright

    from .navegador import preparar_asyncio

    preparar_asyncio()
    logado = False
    with sync_playwright() as pw:
        contexto = pw.chromium.launch_persistent_context(str(perfil), headless=False, locale="pt-BR", viewport={"width": 1280, "height": 800},
                                                         args=["--lang=pt-BR", "--disable-features=Translate"])
        pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
        pagina.goto(SITE, wait_until="domcontentloaded", timeout=60000)
        fim = time.monotonic() + espera
        while time.monotonic() < fim and contexto.pages:
            time.sleep(2)
            try:
                aberta = contexto.pages[0]
                texto = normalizar(aberta.evaluate("document.body.innerText.slice(0, 3000)"))
                logado = "claromusica.com" in aberta.url and "seja premium" in texto or "minha musica" in texto
            except Exception:
                pass
        try:
            contexto.close()
        except Exception:
            pass
    return bool(logado)
