"""Captura de tela com valor de prova: carimbo na própria imagem, HTML e hashes.

Módulo separado do coletor de propósito: o mesmo carimbo e o mesmo pacote de
arquivos servem para o robô e, depois, para um modo assistido em que a pessoa
navega e o app só salva.

Cada captura gera três arquivos com o mesmo nome-base:
  .png   a tela, com uma faixa no topo trazendo URL, data e hora (com fuso), a
         data informada pelo servidor da plataforma e o identificador da captura;
  .html  o HTML da página naquele momento;
  .json  URL, título e intérprete exibidos, datas, versão do app e o SHA-256 do
         PNG e do HTML.
A faixa substitui a barra de endereço e o relógio do sistema, que não aparecem
em captura de página. Com o print de tela inteira ligado (TELA_INTEIRA), a captura
ganha um quarto arquivo, <nome>_tela.png: a foto do monitor onde a janela está,
com a barra de endereço e o relógio (ver tela.py).
É prova documental unilateral: não substitui ata notarial.
"""

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .versao import VERSAO

VERSAO_DO_APP = f"quem-canta/creditos {VERSAO}"
TELA_INTEIRA = False  # ligado pela coleta quando a pessoa pede o print de tela inteira
FUSO = "America/Sao_Paulo"

_CARIMBAR = """(texto) => {
  // escrita antiga de propósito: o navegador embutido no aplicativo da Amazon Music não entende a abreviada
  const anterior = document.getElementById('__carimbo_de_captura'); if (anterior) anterior.remove();
  const faixa = document.createElement('div');
  faixa.id = '__carimbo_de_captura';
  faixa.setAttribute('translate', 'no');
  faixa.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#fff;color:#000;' +
    'font:11px/1.4 Menlo,Consolas,monospace;padding:4px 8px;border-bottom:2px solid #b00000;' +
    'white-space:pre-wrap;word-break:break-all;pointer-events:none';
  faixa.textContent = texto;
  document.documentElement.appendChild(faixa);
}"""
_TRADUZIDA = """() => document.documentElement.className.includes('translated-') ||
  !!document.querySelector('font[style*="vertical-align"]')"""
_DATA_DO_SERVIDOR = """async () => {
  try { const r = await fetch(location.origin + '/favicon.ico', {method: 'HEAD', cache: 'no-store'}); return r.headers.get('date') || ''; }
  catch (e) { return ''; }
}"""
# Roda antes de qualquer script da página: impede a tradução automática, que adultera nomes próprios.
TRAVA_DE_TRADUCAO = """() => {
  const travar = () => {
    document.documentElement.setAttribute('translate', 'no');
    if (document.head && !document.querySelector('meta[name=google][content=notranslate]')) {
      const m = document.createElement('meta'); m.name = 'google'; m.content = 'notranslate'; document.head.appendChild(m);
    }
  };
  travar(); document.addEventListener('DOMContentLoaded', travar);
}"""


class PaginaTraduzida(Exception):
    """O navegador traduziu a página: o que está na tela não é o que a plataforma publicou. Não se captura."""


def slug(texto, maximo=40) -> str:
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", str(texto or "")) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")[:maximo].strip("-") or "sem-nome"


def nome_base(obra, interprete, plataforma, quando: datetime) -> str:
    """<obra>_<interprete>_<plataforma>_<AAAA-MM-DD_HHhMM>"""
    return f"{slug(obra)}_{slug(interprete)}_{slug(plataforma)}_{quando.strftime('%Y-%m-%d_%Hh%M')}"


def agora() -> datetime:
    return datetime.now(ZoneInfo(FUSO))


def sha256(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def texto_do_carimbo(url, quando: datetime, identificador, data_do_servidor="") -> str:
    partes = [
        f"URL: {url}",
        f"Capturado em: {quando.strftime('%d/%m/%Y %H:%M:%S %z')} ({FUSO})   Captura: {identificador}   {VERSAO_DO_APP}",
    ]
    if data_do_servidor:
        partes[1] += f"   Data informada pelo servidor (HTTP Date): {data_do_servidor}"
    return "\n".join(partes)


def capturas_guardadas(pasta) -> list[dict]:
    """As fichas (.json) das capturas já salvas na pasta, cada uma só se o PNG ainda estiver lá."""
    fichas = []
    for arquivo in sorted(Path(pasta).glob("*.json")):
        try:
            ficha = json.loads(arquivo.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(ficha, dict) and "captura" in ficha and (Path(pasta) / ficha.get("arquivos", {}).get("png", "")).is_file():
            fichas.append(ficha)
    return fichas


def capturar(pagina, pasta, obra, interprete, plataforma, exibido: dict, etapa="") -> dict:
    """Carimba a página como está, salva PNG, HTML e JSON e devolve o conteúdo do JSON.

    `exibido`: o que o coletor leu na tela (título, intérprete, itens do menu...), que vai para o JSON.
    Levanta PaginaTraduzida em vez de capturar uma página adulterada pela tradução do navegador.
    """
    if pagina.evaluate(_TRADUZIDA):
        raise PaginaTraduzida(pagina.url)
    _restaurar_janela(pagina)  # janela minimizada não é desenhada: a captura ficaria esperando até dar erro
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    quando = agora()
    base = nome_base(obra, interprete, plataforma, quando) + (f"_{etapa}" if etapa else "")
    numero = 2
    while (pasta / f"{base}.png").exists():  # duas gravações da mesma obra no mesmo minuto
        base = f"{base.rsplit('__', 1)[0]}__{numero}"
        numero += 1
    data_do_servidor = pagina.evaluate(_DATA_DO_SERVIDOR)
    pagina.evaluate(_CARIMBAR, texto_do_carimbo(pagina.url, quando, base, data_do_servidor))
    html = pagina.content().encode("utf-8")
    imagem = pagina.screenshot(type="png")
    tela, da_tela = _tela_inteira(pagina, imagem) if TELA_INTEIRA else (None, None)
    pagina.evaluate("() => { const faixa = document.getElementById('__carimbo_de_captura'); if (faixa) faixa.remove(); }")
    (pasta / f"{base}.png").write_bytes(imagem)
    (pasta / f"{base}.html").write_bytes(html)
    registro = {
        "captura": base, "url": pagina.url, "plataforma": plataforma, "obra": obra, "interprete": interprete,
        "capturado_em": quando.isoformat(timespec="seconds"), "fuso": FUSO, "data_do_servidor": data_do_servidor,
        "exibido": exibido, "versao_do_app": VERSAO_DO_APP,
        "arquivos": {"png": f"{base}.png", "html": f"{base}.html"},
        "sha256": {"png": sha256(imagem), "html": sha256(html)},
    }
    if da_tela is not None:
        registro["tela_inteira"] = da_tela  # o monitor fotografado, ou o motivo de não ter sido possível
    if tela is not None:
        (pasta / f"{base}_tela.png").write_bytes(tela)
        registro["arquivos"]["tela"], registro["sha256"]["tela"] = f"{base}_tela.png", sha256(tela)
    (pasta / f"{base}.json").write_text(json.dumps(registro, ensure_ascii=False, indent=1), encoding="utf-8")
    return registro


def _restaurar_janela(pagina) -> None:
    """Se a janela do navegador foi minimizada, volta a abri-la (no mesmo lugar e tamanho de antes)."""
    try:
        sessao = pagina.context.new_cdp_session(pagina)
        janela = sessao.send("Browser.getWindowForTarget")
        if janela.get("bounds", {}).get("windowState") == "minimized":
            sessao.send("Browser.setWindowBounds", {"windowId": janela["windowId"], "bounds": {"windowState": "normal"}})
            pagina.wait_for_timeout(700)  # o sistema leva um instante para reabrir a janela
        sessao.detach()
    except Exception:  # navegador sem esse comando: segue só com a tentativa de trazer a janela para a frente
        pass


def _tela_inteira(pagina, imagem: bytes) -> tuple[bytes | None, dict]:
    """Fotografa o monitor em que a página está à vista, com a faixa de carimbo ainda na tela.

    Se a janela estiver coberta ou minimizada, tenta uma vez reabri-la e trazê-la para a frente. Falha aqui nunca derruba a captura:
    a captura de página continua valendo, e o motivo fica na ficha.
    """
    from . import tela

    try:
        pagina.wait_for_timeout(250)  # dá tempo de o sistema desenhar a faixa na janela
        foto, dados = tela.fotografar(imagem)
        if foto is None:
            _restaurar_janela(pagina)
            pagina.bring_to_front()
            pagina.wait_for_timeout(1000)
            foto, dados = tela.fotografar(pagina.screenshot(type="png"))
        return foto, dados
    except tela.SemComponente as e:
        return None, {"erro": str(e)}
    except Exception as e:  # sistema sem permissão de gravar a tela, monitor desligado no meio etc.
        return None, {"erro": f"não foi possível fotografar a tela ({type(e).__name__}: {e})"}
