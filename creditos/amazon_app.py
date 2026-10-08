"""Amazon Music, aplicativo de desktop (Windows): o único lugar em que a Amazon mostra o compositor.

No aplicativo, o menu de ações da faixa ("⋮") tem o item "Créditos", que abre uma janela com "Compositores".
São dois prints por música: o menu aberto e a janela de créditos.

Como funciona: o aplicativo é uma página web embrulhada num programa. O app o abre com a porta de depuração
ligada e fala direto com a tela dele (ver cdp.py): roda scripts na página, mexe o mouse e fotografa. Para isso o aplicativo precisa estar instalado e com
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
from .amazon import _TODOS, Candidato

PLATAFORMA = "AMAZON"
PORTA = 9333
PROCESSO = "Amazon Music.exe"
ITEM_DE_CREDITOS = ("credito", "credit")
# Itens que provam que o menu aberto é o da faixa.
# "Adicionar à fila" e "Adicionar à playlist" não servem: também estão na lista de playlists que o aplicativo
# guarda escondida na página, e ela não pode passar por menu de faixa.
ITENS_DA_FAIXA = ("compartilhar musica", "reproduzir musicas semelhantes", "share song", "play similar")
# Itens que só existem nessa lista escondida: se aparecem, o que foi lido não é o menu da faixa.
ITENS_DE_OUTRO_MENU = ("criar uma playlist", "minhas curtidas", "create playlist", "my likes")
# Itens pelos quais se reconhece um menu de faixa aberto na tela (os do aplicativo e os do site).
SINAIS_DE_MENU = ("adicionar a fila", "adicionar a playlist", "compartilhar musica", "reproduzir a proxima", "creditos",
                  "ver album", "ver artista", "compartilhar esta musica")
ROTULOS_DE_AUTOR = ("compositor", "letrista", "autor", "songwriter", "composer", "lyricist", "writer")

# O texto de cada rótulo da janela de créditos e do bloco em volta dele.
_LER_CREDITOS = _TODOS + """.filter(e => e.children.length === 0 && /^(compositor|letrista|autor|songwriter|composer|lyricist|writer)/i
    .test((e.textContent || '').trim()) && (e.textContent || '').trim().length < 40)
  .map(e => ({rotulo: e.textContent.trim(), bloco: ((e.parentElement && e.parentElement.innerText) || '').trim().slice(0, 600)})); }"""
_LER_TEXTO = "() => (document.body.innerText || '').slice(0, 6000)"
# O aplicativo de desktop não usa os mesmos componentes do site: por isso tudo aqui se guia pelo que está escrito
# na tela (o título da faixa, "Créditos", "Compositores") e pela posição dos elementos, não por nomes internos.
_BASE = r"""const anda = (raiz) => { let s = []; for (const e of raiz.querySelectorAll('*')) { s.push(e); if (e.shadowRoot) s = s.concat(anda(e.shadowRoot)); } return s; };
  const limpo = (t) => (t || '').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visivel = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight; };
  const proprio = (e) => [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
  const centro = (e) => { const r = e.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; };
  const pai = (e) => e.parentElement || (e.getRootNode && e.getRootNode().host) || null;
  const folhas = (raiz) => anda(raiz).filter(e => proprio(e) && visivel(e));
  // À vista de verdade: dentro da tela e sendo o que está por cima naquele ponto. O aplicativo guarda menus
  // escondidos na página (a lista de playlists, por exemplo), que têm tamanho mas não aparecem.
  const avista = (e) => { if (!visivel(e)) return false; const r = e.getBoundingClientRect();
    if (r.right <= 0 || r.left >= innerWidth) return false;
    const estilo = getComputedStyle(e); if (estilo.visibility === 'hidden' || estilo.display === 'none') return false;
    let topo = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    while (topo && topo.shadowRoot && topo.shadowRoot.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
           && topo.shadowRoot.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2) !== topo) topo = topo.shadowRoot.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return !!topo && (topo === e || e.contains(topo) || (topo.contains(e) && topo.getBoundingClientRect().height <= r.height + 30)); };
  const avistas = (raiz) => anda(raiz).filter(e => proprio(e) && avista(e));
"""
# A linha da faixa: o elemento com o título, dentro de um bloco largo e baixo que também traz a duração ("3:45").
_ACHAR_LINHA = "(titulo, rolar) => { " + _BASE + r""" const alvo = limpo(titulo);
  const candidatos = anda(document).filter(e => proprio(e) && (alvo ? limpo(proprio(e)) === alvo : true));
  for (const e of candidatos) {
    let linha = e;
    for (let i = 0; i < 10 && linha; i++) {
      const r = linha.getBoundingClientRect();
      if (r.width > innerWidth * 0.5 && r.height <= 170 && /\d{1,2}:\d\d/.test(linha.innerText || linha.textContent || '')) {
        // sobe até o bloco mais de fora que ainda tem altura de linha: é nele que ficam os botões
        // (mas sem passar para um bloco que junte duas faixas: cada faixa tem uma duração só)
        const duracoes = (b) => (folhas(b).map(proprio).join(' ').match(/\b\d{1,2}:\d\d\b/g) || []).length;
        while (pai(linha) && pai(linha).getBoundingClientRect().height <= 170 && pai(linha).getBoundingClientRect().height > 0
               && duracoes(pai(linha)) <= 1) linha = pai(linha);
        if (rolar !== false) linha.scrollIntoView({block: 'center'});
        window.__quemcanta_linha = linha;
        return {texto: proprio(e).slice(0, 80), linha: (linha.innerText || '').replace(/\n+/g, ' | ').slice(0, 160), tag: linha.tagName.toLowerCase()};
      }
      linha = pai(linha);
    }
  }
  return null; }"""
# Nos resultados da busca as músicas não vêm em linhas com duração, e sim em blocos pequenos (capa, título, artista).
# Acha o bloco pelo título e o guarda como "linha", para os passos seguintes servirem igual.
_ACHAR_ITEM = "(titulo) => { " + _BASE + r""" const alvo = limpo(titulo);
  const guardado = window.__quemcanta_alvo;
  const iguais = anda(document).filter(x => proprio(x) && visivel(x) && limpo(proprio(x)) === alvo);
  for (const e of (guardado && iguais.includes(guardado) ? [guardado] : []).concat(iguais)) {
    // O bloco é o maior antepassado ainda baixo e estreito: é ele que traz a capa e os botões, além do texto.
    let bloco = null, atual = e;
    for (let i = 0; i < 10 && pai(atual); i++) { atual = pai(atual); const r = atual.getBoundingClientRect();
      if (r.height > 140 || r.width > innerWidth * 0.6) break;
      if (folhas(atual).length >= 2 && r.width >= 180) bloco = atual; }
    if (bloco) { const r = bloco.getBoundingClientRect();
      bloco.scrollIntoView({block: 'center'}); window.__quemcanta_linha = bloco;
      return {titulo: centro(e), bloco: (folhas(bloco).map(proprio).join(' | ')).slice(0, 160), largura: Math.round(r.width), classe: (bloco.className || '').toString().slice(0, 60)}; }
  }
  return null; }"""
# O primeiro texto depois de um título de seção ("Músicas", "Álbuns"): serve para escolher um item qualquer no teste.
_PRIMEIRO_DA_SECAO = "(secao) => { " + _BASE + """ const todas = anda(document).filter(e => proprio(e) && e.getBoundingClientRect().width > 0);
  const i = todas.findIndex(e => limpo(proprio(e)) === limpo(secao));
  if (i < 0) return null;
  const item = todas.slice(i + 1).find(e => proprio(e).length > 1 && !/^(ver tudo|ver mais|hd|ultra hd|sd|letras)$/i.test(proprio(e).trim()));
  if (!item) return null;
  item.scrollIntoView({block: 'center'}); window.__quemcanta_alvo = item;
  return {texto: proprio(item).trim()}; }"""
_ONDE_ESTA_O_ALVO = "() => { " + _BASE + " const e = window.__quemcanta_alvo; return e && visivel(e) ? centro(e) : null; }"
# Onde estão a linha guardada e os botões dela. O de mais ações ("⋮") é o que fica mais à direita.
_ONDE_CLICAR = "() => { " + _BASE + r""" const linha = window.__quemcanta_linha; if (!linha || !linha.isConnected) return null;
  const r = linha.getBoundingClientRect();
  const clicaveis = anda(linha).concat(linha.shadowRoot ? anda(linha.shadowRoot) : []).filter(e => { const b = e.getBoundingClientRect();
    return visivel(e) && b.width <= 70 && b.height <= 70 && b.width >= 8 && (/^(button|a)$/.test(e.tagName.toLowerCase())
      || e.getAttribute('role') === 'button' || /button/.test(e.tagName.toLowerCase()) || getComputedStyle(e).cursor === 'pointer'); });
  clicaveis.sort((a, b) => centro(a).x - centro(b).x);
  const ultimo = clicaveis[clicaveis.length - 1];
  return {linha: {x: r.x + Math.min(r.width / 2, 400), y: r.y + r.height / 2}, botao: ultimo ? centro(ultimo) : null,
          botoes: clicaveis.map(e => e.tagName.toLowerCase() + ':' + (e.getAttribute('aria-label') || e.getAttribute('title') || e.getAttribute('icon-name') || (e.className && e.className.baseVal !== undefined ? e.className.baseVal : e.className) || '').toString().slice(0, 30) + '@' + Math.round(centro(e).x)).slice(-8)}; }"""
# O menu aberto: acha um item conhecido e sobe até o bloco que junta os itens.
_LER_MENU_ABERTO = "(sinais) => { " + _BASE + r""" const todas = avistas(document);
  const item = todas.find(e => sinais.some(s => limpo(proprio(e)) === s || limpo(proprio(e)).startsWith(s)));
  if (!item) return [];
  let bloco = item;
  for (let i = 0; i < 10 && pai(bloco); i++) { bloco = pai(bloco); const r = bloco.getBoundingClientRect();
    if (avistas(bloco).length >= 3 && r.width < innerWidth * 0.6) break; }
  return [...new Set(avistas(bloco).map(e => proprio(e).replace(/\s+/g, ' ').trim()).filter(t => t && t.length < 60))].slice(0, 20); }"""
# Onde está, na tela, o texto que casa com o padrão (o item "Créditos" do menu).
_ONDE_ESTA_O_TEXTO = "(padrao) => { " + _BASE + r""" const achado = avistas(document).find(e => new RegExp(padrao, 'i').test(proprio(e)) && proprio(e).length < 40);
  return achado ? centro(achado) : null; }"""
# Um retrato do que há de clicável na tela e dos caminhos que ela usa: é o que permite ajustar o módulo à distância.
_RETRATO = "() => { " + _BASE + r""" const todos = anda(document);
  return {caminhos: [...new Set(todos.map(e => (e.getAttribute && (e.getAttribute('href') || e.getAttribute('primary-href') || e.getAttribute('to'))) || '').filter(h => /^(#|\/)/.test(h) && h.length < 120 && !/\.(css|js|png|ico|xml)(\?|$)/.test(h)))].slice(0, 70),
          etiquetas: Object.entries(todos.reduce((c, e) => { const t = e.tagName.toLowerCase(); c[t] = (c[t] || 0) + 1; return c; }, {})).sort((a, b) => b[1] - a[1]).slice(0, 25),
          botoes: [...new Set(todos.filter(e => visivel(e) && (/button/.test(e.tagName.toLowerCase()) || e.getAttribute('role') === 'button')).map(e => (e.getAttribute('aria-label') || e.getAttribute('title') || proprio(e) || (e.className && e.className.toString()) || '').slice(0, 40)))].slice(0, 40)}; }"""
_LER_HTML = "() => document.documentElement.outerHTML.slice(0, 900000)"
# Escreve no campo de busca do jeito que a tela do aplicativo percebe (avisando a mudança) e dá Enter nele.
_BUSCAR_PELO_CAMPO = "(texto) => { " + _BASE + """ const campo = anda(document).find(e => e.tagName === 'INPUT' && visivel(e) && !/checkbox|radio|hidden|range/.test(e.type || ''));
  if (!campo) return 'sem campo de busca';
  campo.focus();
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(campo, texto);
  campo.dispatchEvent(new Event('input', {bubbles: true})); campo.dispatchEvent(new Event('change', {bubbles: true}));
  for (const tipo of ['keydown', 'keypress', 'keyup']) campo.dispatchEvent(new KeyboardEvent(tipo, {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
  return 'escrito: ' + campo.value; }"""
# Onde está o campo de busca do aplicativo (o primeiro campo de texto à vista).
_ONDE_ESTA_A_BUSCA = "() => { " + _BASE + """ const campo = anda(document).find(e => e.tagName === 'INPUT' && visivel(e)
    && !/checkbox|radio|hidden|range/.test(e.type || ''));
  if (!campo) return null; campo.focus(); if (campo.select) campo.select(); return centro(campo); }"""
# Os endereços de álbum que a tela mostra (em links e em atributos), para aprender o formato que o aplicativo usa.
_ENDERECOS_DE_ALBUM = "() => { " + _BASE + """ const achados = [];
  for (const e of anda(document)) for (const a of (e.attributes || [])) if (/album/i.test(a.value) && a.value.length < 160 && !/\\.(css|js|png|jpg|svg)/.test(a.value)) achados.push(a.name + '=' + a.value);
  return [...new Set(achados)].slice(0, 25); }"""


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


def _powershell(comando: str, com_erros=False) -> list[str]:
    """As linhas que um comando do PowerShell devolve. Fora do Windows, ou se o comando falhar, lista vazia.

    `com_erros` junta as mensagens de erro do PowerShell às linhas: serve para dizer por que um comando não funcionou.
    """
    if sys.platform != "win32":
        return []
    try:
        resultado = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
                                   capture_output=True, text=True, timeout=40, encoding="utf-8", errors="ignore")
    except (OSError, subprocess.SubprocessError) as e:
        return [f"{type(e).__name__}: {e}"] if com_erros else []
    saida = resultado.stdout + ("\n" + resultado.stderr if com_erros else "")
    return [linha.strip().strip('"') for linha in saida.splitlines() if linha.strip()]


def comando_da_loja(caminho, porta: int) -> str:
    """O comando que abre a versão da Microsoft Store com a porta de depuração.

    Aplicativo da loja não pode ser aberto por outro programa com uma instrução extra. O Windows tem um comando
    próprio para isso, `Invoke-CommandInDesktopPackage`, que executa o programa dentro do pacote do aplicativo;
    no Windows 10 atual e no 11 ele não exige o modo de desenvolvedor. O pacote e o identificador do aplicativo
    são lidos do próprio Windows.
    """
    return (
        "$pacote = Get-AppxPackage *AmazonMusic* | Select-Object -First 1; "
        "$id = (($pacote | Get-AppxPackageManifest).Package.Applications.Application | Select-Object -First 1).Id; "
        f"Invoke-CommandInDesktopPackage -PackageFamilyName $pacote.PackageFamilyName -AppId $id -Command '{caminho}' "
        f"-Args '--remote-debugging-port={porta}'; "
        'Write-Output "pacote: $($pacote.PackageFamilyName) | aplicativo: $id"'
    )


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


def e_de_pagina_unica(endereco: str) -> bool:
    """O endereço é o de uma página só, que troca de tela pelo trecho depois do "#"? É assim no aplicativo de desktop."""
    partes = urlsplit(endereco or "")
    return "#" in (endereco or "") or partes.path.endswith(".html")


def e_da_loja(caminho) -> bool:
    """O aplicativo veio da Microsoft Store? Esses ficam numa pasta protegida do Windows e costumam recusar ser abertos por outro programa."""
    return "windowsapps" in str(caminho or "").lower()


def interpretar_menu(itens) -> str:
    """O menu da faixa no aplicativo: "com_creditos", "sem_creditos" ou "invalido" (não é o menu da faixa)."""
    limpos = [normalizar(i) for i in itens or [] if str(i or "").strip()]
    if not any(any(sinal in item for sinal in ITENS_DA_FAIXA) for item in limpos):
        return "invalido"
    if any(any(sinal in item for sinal in ITENS_DE_OUTRO_MENU) for item in limpos):
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
        self.navegacoes, self.pela_loja, self.telas, self.botoes_vistos, self.caminho_que_funcionou = 0, [], [], [], ""
        self._pagina = None

    def __enter__(self):
        return self

    def __exit__(self, *erro):
        if self._pagina is not None:
            self._pagina.fechar()  # só desfaz a conexão: o aplicativo continua aberto, com a conta logada

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
                "o aplicativo Amazon Music não foi encontrado neste computador. Instale-o, abra uma vez e faça login com a "
                "conta do escritório")
        self.avisar("Amazon Music (aplicativo): abrindo o aplicativo")
        subprocess.run(["taskkill", "/IM", caminho.name, "/F"], capture_output=True)  # aberto sem a porta de depuração não serve
        time.sleep(2)
        if e_da_loja(caminho):
            # A versão da loja só abre com instrução extra pelo comando do próprio Windows.
            self.pela_loja = _powershell(comando_da_loja(caminho, self.porta), com_erros=True)
            self.avisar("Amazon Music (aplicativo): abrindo pela Microsoft Store - " + " / ".join(self.pela_loja)[:300])
        else:
            try:
                subprocess.Popen([str(caminho), f"--remote-debugging-port={self.porta}"], close_fds=True)
            except OSError as e:
                raise AplicativoIndisponivel(f"o Windows não deixou abrir o Amazon Music a partir de {caminho} ({type(e).__name__})") from e
        limite = time.monotonic() + 60
        while time.monotonic() < limite:
            if self._responde():
                time.sleep(6)  # a tela inicial ainda está carregando
                return
            time.sleep(1.5)
        raise AplicativoIndisponivel(
            "o Amazon Music não aceitou a conexão do app (porta de depuração)"
            + (f", aberto pela Microsoft Store. O que o Windows respondeu: {' / '.join(self.pela_loja)[:300] or 'nada'}" if e_da_loja(caminho)
               else '. Rode o diagnóstico ("Testar Amazon.cmd") e mande o arquivo gerado para quem cuida do app'))

    @property
    def pagina(self):
        if self._pagina is None:
            from . import cdp

            self._abrir_o_aplicativo()
            try:
                abertas = [t for t in cdp.telas(self.porta) if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            except Exception as e:
                raise AplicativoIndisponivel(f"o aplicativo não informou as telas abertas ({type(e).__name__}: {e})") from e
            if not abertas:
                raise AplicativoIndisponivel("o aplicativo abriu sem nenhuma tela visível para o app")
            # A tela principal é a que mostra a Amazon Music; na dúvida, a primeira.
            self.telas = [(t.get("url", "")[:120], t.get("title", "")[:60]) for t in abertas]
            escolhida = next((t for t in abertas if "amazon" in t.get("url", "").lower() or "music" in t.get("url", "").lower()), abertas[0])
            try:
                self._pagina = cdp.PaginaCDP(escolhida["webSocketDebuggerUrl"])
            except Exception as e:
                raise AplicativoIndisponivel(f"não foi possível se conectar à tela do aplicativo ({type(e).__name__}: {str(e)[:200]})") from e
        return self._pagina

    def _esperar_carregar(self, limite=75) -> int:
        """Espera a tela do aplicativo aparecer. Devolve quantas letras de texto ela tem (zero: não carregou)."""
        fim = time.monotonic() + limite
        letras = 0
        while time.monotonic() < fim:
            letras = len(self.pagina.evaluate(_LER_TEXTO) or "")
            if letras > 120:
                return letras
            self.pagina.wait_for_timeout(1500)
        return letras

    def _ir_para(self, caminho: str):
        """Abre um caminho ("/albums/<álbum>?trackAsin=<faixa>") dentro do aplicativo.

        O aplicativo é uma página só, que troca de tela pelo trecho depois do "#" do endereço
        (".../webapp/index.html#/albums/..."): navegar é trocar esse trecho. Carregar outro endereço tiraria o
        aplicativo da tela dele. Num navegador comum, com o site, o caminho vai no próprio endereço.
        """
        atual = urlsplit(self.pagina.url)
        if atual.scheme not in ("http", "https") or not atual.netloc:
            raise AplicativoIndisponivel(f'o aplicativo usa um endereço interno ("{self.pagina.url[:60]}") que esta versão do app não sabe navegar')
        if e_de_pagina_unica(self.pagina.url):
            self.pagina.evaluate("(caminho) => { location.hash = '#' + caminho; }", caminho)
        else:
            self.pagina.goto(f"{atual.scheme}://{atual.netloc}{caminho}")

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
            try:
                self.pagina.keyboard.press("Escape")  # não deixa menu nem janela abertos no aplicativo
            except Exception:
                pass
        if leitura.coleta == "ok":
            self.cache.mkdir(parents=True, exist_ok=True)
            arquivo.write_text(json.dumps(asdict(leitura), ensure_ascii=False), encoding="utf-8")
        return leitura

    def _abrir_o_menu(self, titulo: str = "") -> list[str]:
        """Clica no botão de mais ações da linha da faixa e devolve os itens do menu que abriu.

        A lista de faixas é redesenhada quando rola: por isso a linha é procurada de novo, sem rolar, logo antes de
        cada clique, em vez de confiar na que foi achada antes.
        """
        pagina = self.pagina
        itens = []
        for _ in range(2):  # o primeiro clique às vezes pega o botão ainda carregando
            pagina.evaluate(_ACHAR_LINHA, titulo, False)
            onde = pagina.evaluate(_ONDE_CLICAR)
            if not onde:
                return []
            pagina.mover_o_mouse(onde["linha"]["x"], onde["linha"]["y"])  # há botão que só aparece com o mouse em cima da linha
            pagina.wait_for_timeout(600)
            pagina.evaluate(_ACHAR_LINHA, titulo, False)
            onde = pagina.evaluate(_ONDE_CLICAR) or onde
            self.botoes_vistos = onde.get("botoes", [])
            if not onde.get("botao"):
                return []
            pagina.clicar(onde["botao"]["x"], onde["botao"]["y"])
            pagina.wait_for_timeout(1400)
            itens = pagina.evaluate(_LER_MENU_ABERTO, list(SINAIS_DE_MENU)) or []
            if interpretar_menu(itens) != "invalido":
                break
            pagina.keyboard.press("Escape")
            pagina.wait_for_timeout(400)
        return itens

    def _abrir_o_menu_da_guardada(self) -> list[str]:
        """Abre o menu da linha ou do bloco já guardado na tela (sem procurá-lo de novo pelo título)."""
        pagina = self.pagina
        onde = pagina.evaluate(_ONDE_CLICAR)
        if not onde:
            return []
        pagina.mover_o_mouse(onde["linha"]["x"], onde["linha"]["y"])
        pagina.wait_for_timeout(700)
        onde = pagina.evaluate(_ONDE_CLICAR) or onde
        self.botoes_vistos = onde.get("botoes", [])
        if not onde.get("botao"):
            return []
        pagina.clicar(onde["botao"]["x"], onde["botao"]["y"])
        pagina.wait_for_timeout(1500)
        return pagina.evaluate(_LER_MENU_ABERTO, list(SINAIS_DE_MENU)) or []

    def _abrir_album(self, cand: Candidato) -> dict | None:
        """Abre o álbum no aplicativo e acha a linha da faixa. Tenta os formatos de endereço que o aplicativo pode usar."""
        pagina = self.pagina
        # O primeiro formato é o que o próprio aplicativo mostra ao abrir um álbum; os outros são os do site.
        for caminho in (f"/album/detail/{cand.album}?id={cand.album}&asin={cand.album}", f"/albums/{cand.album}?trackAsin={cand.faixa}",
                        f"/albums/{cand.album}", f"/album/{cand.album}"):
            self._ir_para(caminho)
            limite = time.monotonic() + 20
            while time.monotonic() < limite:
                pagina.wait_for_timeout(1200)
                linha = pagina.evaluate(_ACHAR_LINHA, cand.titulo)
                if linha:
                    self.caminho_que_funcionou = caminho.split(cand.album)[0]
                    return linha
            if pagina.evaluate(_ACHAR_LINHA, "", False):  # o álbum abriu e tem faixas: a procurada não está nele
                return None
        return None

    def _ler(self, leitura: Leitura, cand: Candidato, obra: str):
        pagina = self.pagina
        pagina.keyboard.press("Escape")  # fecha menu ou janela da faixa anterior
        time.sleep(3)  # sem pressa: o aplicativo é da conta do escritório
        self.navegacoes += 1
        if not self._esperar_carregar():
            raise AplicativoIndisponivel("a tela do aplicativo não carregou")
        linha = self._abrir_album(cand)
        if linha is None:
            leitura.coleta, leitura.erro = "indisponivel", "a faixa não apareceu na lista do álbum dentro do aplicativo"
            return
        pagina.wait_for_timeout(1200)
        pagina.evaluate(_ACHAR_LINHA, cand.titulo)  # rola de novo, com a lista já carregada
        pagina.wait_for_timeout(900)
        itens = self._abrir_o_menu(cand.titulo)
        leitura.menu = itens
        resultado = interpretar_menu(itens)
        if resultado == "invalido":
            leitura.coleta = "erro"
            leitura.erro = "o menu de ações da faixa não abriu no aplicativo" + (
                f" (itens vistos: {', '.join(itens)[:120]})" if itens else f" (botões da linha: {', '.join(self.botoes_vistos)[:160]})")
            return
        exibido = {"titulo": cand.titulo, "interprete": cand.interprete, "itens_do_menu": itens}
        if resultado == "sem_creditos":
            exibido["constatacao"] = 'o menu da faixa no aplicativo não tem o item "Créditos"'
            leitura.provas.append(captura.capturar(pagina, self.pasta, obra, cand.interprete, "amazon-app", exibido, "menu")["captura"])
            pagina.keyboard.press("Escape")
            return
        leitura.tem_item_de_creditos = True
        leitura.provas.append(captura.capturar(pagina, self.pasta, obra, cand.interprete, "amazon-app", exibido, "menu")["captura"])
        item = pagina.evaluate(_ONDE_ESTA_O_TEXTO, "^cr[eé]ditos?$|^credits?$")
        if not item:
            leitura.coleta, leitura.erro = "erro", 'o item "Créditos" sumiu do menu antes do clique'
            return
        pagina.clicar(item["x"], item["y"])
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
            linhas.append(f"[ok] {nome}: {valor}")  # no arquivo vai inteiro; na tela, só o começo
            print(f"[ok] {nome}: {str(valor)[:900]}", flush=True)
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
    def guardar(nome):
        """A foto e o HTML da tela neste ponto: é com eles que o módulo é ajustado sem ter o aplicativo à mão."""
        tentar(f"foto {nome}.png", lambda: len(pagina.screenshot(path=str(destino / f"{nome}.png"))) and "gravada")
        tentar(f"tela {nome}.html", lambda: (destino / f"{nome}.html").write_text(pagina.evaluate(_LER_HTML) or "", encoding="utf-8") and "gravada")

    try:
        pagina = tentar("conectar ao aplicativo", lambda: app.pagina)
        anotar(f"o que o Windows respondeu ao abrir pela loja: {app.pela_loja or '(não foi pela loja)'}")
        if pagina is not None:
            anotar(f"telas abertas: {app.telas}")
            tentar("esperar a tela carregar (letras de texto)", app._esperar_carregar)
            tentar("endereço da tela principal", lambda: pagina.url)
            anotar(f"página única (navega pelo trecho depois do #): {e_de_pagina_unica(pagina.url)}")
            tentar("texto da tela inicial", lambda: " | ".join(l for l in pagina.evaluate(_LER_TEXTO).split("\n") if l.strip())[:500])
            retrato = tentar("retrato da tela inicial", lambda: pagina.evaluate(_RETRATO)) or {}
            guardar("1-inicio")
            album = album or next((c_.split("/album")[1].lstrip("s/").split("?")[0].split("/")[0] for c_ in retrato.get("caminhos", []) if "/album" in c_), "") \
                or os.environ.get("QUEMCANTA_AMAZON_ALBUM", "")
            anotar(f"álbum de teste: {album or '(nenhum: a tela não mostrou endereço de álbum e nenhum foi indicado)'}")
            # O aplicativo pode ter ficado numa tela vazia de um teste anterior: começa de uma tela que existe.
            from urllib.parse import quote
            tentar("voltar para o início", lambda: app._ir_para("/home") or "pedido enviado")
            pagina.wait_for_timeout(7000)
            tentar("texto do início", lambda: " | ".join(l for l in pagina.evaluate(_LER_TEXTO).split("\n") if l.strip())[:700])
            tentar("retrato do início", lambda: pagina.evaluate(_RETRATO))
            guardar("1b-home")
            # A busca: o teste anterior mostrou que o endereço dela é "#/search/<texto>".
            busca = os.environ.get("QUEMCANTA_AMAZON_BUSCA", "") or "amor"
            antes = len(pagina.evaluate(_LER_TEXTO) or "")
            tentar(f"buscar pelo endereço: /search/{busca}", lambda: app._ir_para("/search/" + quote(busca)) or "pedido enviado")
            pagina.wait_for_timeout(9000)
            tentar("endereço da busca", lambda: pagina.url)
            if abs(len(pagina.evaluate(_LER_TEXTO) or "") - antes) < 40:  # a tela não mudou: tenta pelo campo, como uma pessoa
                tentar("buscar pelo campo", lambda: pagina.evaluate(_BUSCAR_PELO_CAMPO, busca))
                pagina.wait_for_timeout(9000)
                tentar("endereço da busca (pelo campo)", lambda: pagina.url)
            tentar("texto da busca", lambda: " | ".join(l for l in pagina.evaluate(_LER_TEXTO).split("\n") if l.strip())[:1500])
            tentar("retrato da busca", lambda: pagina.evaluate(_RETRATO))
            enderecos = tentar("endereços de álbum na tela da busca", lambda: pagina.evaluate(_ENDERECOS_DE_ALBUM)) or []
            guardar("2-busca")
            def experimentar_o_menu(nome):
                """Com a linha (ou o bloco) guardada: mostra os botões, abre o menu e, havendo "Créditos", abre a janela."""
                tentar("onde clicar", lambda: pagina.evaluate(_ONDE_CLICAR))
                itens = tentar("abrir o menu pelo botão mais à direita", lambda: app._abrir_o_menu_da_guardada())
                anotar(f"botões vistos: {app.botoes_vistos}")
                if not itens or interpretar_menu(itens) == "invalido":
                    onde = pagina.evaluate(_ONDE_CLICAR)
                    if onde:
                        tentar("clicar com o botão direito", lambda: pagina.clicar_com_o_direito(onde["linha"]["x"], onde["linha"]["y"]) or "clicado")
                        pagina.wait_for_timeout(1500)
                        itens = tentar("itens do menu de contexto", lambda: pagina.evaluate(_LER_MENU_ABERTO, list(SINAIS_DE_MENU)))
                tentar("leitura do menu", lambda: interpretar_menu(itens))
                guardar(f"{nome}-menu")
                if itens and interpretar_menu(itens) == "com_creditos":
                    item = tentar('onde está o item "Créditos"', lambda: pagina.evaluate(_ONDE_ESTA_O_TEXTO, "^cr[eé]ditos?$|^credits?$"))
                    if item:
                        tentar("clicar em Créditos", lambda: pagina.clicar(item["x"], item["y"]) or "clicado")
                        pagina.wait_for_timeout(3000)
                        tentar("texto da tela com a janela de créditos", lambda: " | ".join(l for l in pagina.evaluate(_LER_TEXTO).split("\n") if l.strip()))
                        blocos = tentar("blocos da janela de créditos", lambda: pagina.evaluate(_LER_CREDITOS))
                        tentar("créditos lidos", lambda: interpretar_creditos(blocos))
                        guardar(f"{nome}-creditos")
                tentar("fechar menu ou janela", lambda: pagina.keyboard.press("Escape") or "fechado")
                pagina.wait_for_timeout(800)
                return itens

            (destino / "2-busca-retrato.json").write_text(json.dumps(pagina.evaluate(_RETRATO), ensure_ascii=False, indent=1), encoding="utf-8")
            # 1. Uma música direto nos resultados da busca.
            musica = tentar('primeira música da seção "Músicas"', lambda: pagina.evaluate(_PRIMEIRO_DA_SECAO, "Músicas"))
            pagina.wait_for_timeout(800)
            if musica and tentar("bloco dessa música", lambda: pagina.evaluate(_ACHAR_ITEM, musica["texto"])):
                pagina.wait_for_timeout(700)
                experimentar_o_menu("3-musica")
            # 2. Um álbum: clicar nele mostra o endereço que o aplicativo usa e a lista de faixas com duração.
            capa = tentar('primeiro álbum da seção "Álbuns"', lambda: pagina.evaluate(_PRIMEIRO_DA_SECAO, "Álbuns"))
            pagina.wait_for_timeout(800)
            ponto = tentar("onde está esse álbum na tela", lambda: pagina.evaluate(_ONDE_ESTA_O_ALVO)) if capa else None
            if ponto:
                tentar("clicar no álbum", lambda: pagina.clicar(ponto["x"], ponto["y"]) or "clicado")
                pagina.wait_for_timeout(9000)
                tentar("endereço do álbum (o formato que o aplicativo usa)", lambda: pagina.url)
                tentar("texto do álbum", lambda: " | ".join(l for l in pagina.evaluate(_LER_TEXTO).split("\n") if l.strip())[:900])
                (destino / "4-album-retrato.json").write_text(json.dumps(pagina.evaluate(_RETRATO), ensure_ascii=False, indent=1), encoding="utf-8")
                guardar("4-album")
                if tentar("achar uma linha de faixa no álbum", lambda: pagina.evaluate(_ACHAR_LINHA, "")):
                    pagina.wait_for_timeout(700)
                    experimentar_o_menu("5-faixa")
                # 3. O mesmo álbum aberto pelo endereço, que é como a coleta de verdade vai chegar nele.
                import re
                codigo = (re.search(r"/album/detail/([A-Z0-9]+)", pagina.url) or [None, os.environ.get("QUEMCANTA_AMAZON_ALBUM", "")])[1]
                if codigo:
                    tentar("voltar para o início", lambda: app._ir_para("/home") or "pedido enviado")
                    pagina.wait_for_timeout(6000)
                    tentar(f"abrir o álbum {codigo} pelo endereço", lambda: app._ir_para(f"/album/detail/{codigo}?id={codigo}&asin={codigo}") or "pedido enviado")
                    pagina.wait_for_timeout(9000)
                    tentar("endereço depois de abrir pelo endereço", lambda: pagina.url)
                    guardar("6-album-pelo-endereco")
                    if tentar("achar uma linha de faixa no álbum aberto pelo endereço", lambda: pagina.evaluate(_ACHAR_LINHA, "")):
                        pagina.wait_for_timeout(700)
                        experimentar_o_menu("7-faixa")
    finally:
        app.__exit__()
    relatorio = destino / "diagnostico-amazon.txt"
    relatorio.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"\nDiagnóstico gravado em: {relatorio}")
    return relatorio


if __name__ == "__main__":
    diagnostico()
