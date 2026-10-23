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

import base64
import hashlib
import io
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .versao import VERSAO
from . import disco

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
  // Uma janela modal da página (os créditos do Spotify, por exemplo) fica numa camada acima de tudo e escurece o
  // resto, inclusive esta faixa. Como "popover", a faixa entra nessa mesma camada, por cima da janela, e sai nítida.
  try {
    if (faixa.showPopover) {
      faixa.setAttribute('popover', 'manual');
      faixa.style.cssText += ';inset:0 0 auto 0;margin:0;width:auto;height:auto;max-width:none;border:0;border-bottom:2px solid #b00000;overflow:visible';
      faixa.showPopover();
    }
  } catch (e) {}
}"""
_TRADUZIDA = """() => document.documentElement.className.includes('translated-') ||
  !!document.querySelector('font[style*="vertical-align"]')"""
_DATA_DO_SERVIDOR = """async () => {
  try { const r = await fetch(location.origin + '/favicon.ico?_=' + Date.now(), {method: 'HEAD', cache: 'no-store'}); return r.headers.get('date') || ''; }
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


def texto_do_carimbo(url, quando: datetime, identificador, data_do_servidor="", so_a_url=False) -> str:
    """O que vai escrito na faixa. No print de tela inteira (`so_a_url`), só o endereço: a data e a hora já aparecem
    no relógio do sistema, e os outros dados ficam no .json da captura. Na captura de página, que não mostra nem a
    barra de endereço nem o relógio, a faixa traz tudo."""
    if so_a_url:
        return f"URL: {url}"
    partes = [
        f"URL: {url}",
        f"Capturado em: {quando.strftime('%d/%m/%Y %H:%M:%S %z')} ({FUSO})   Captura: {identificador}   {VERSAO_DO_APP}",
    ]
    if data_do_servidor:
        partes[1] += f"   Data informada pelo servidor (HTTP Date): {data_do_servidor}"
    return "\n".join(partes)


def _serve_a_ficha(pasta, ficha: dict) -> bool:
    """Com o print de tela inteira pedido, só serve a captura feita nesse modo: a que traz a foto do monitor, ou a
    que tentou e registrou por que não deu. A que foi tirada sem a opção é refeita. Sem a opção, qualquer uma serve."""
    if not TELA_INTEIRA:
        return True
    tela = ficha.get("arquivos", {}).get("tela", "")
    return bool(tela and (Path(pasta) / tela).is_file()) or "tela_inteira" in ficha


def serve(pasta, provas) -> bool:
    """As capturas guardadas (nomes-base) servem para esta coleta? Ver `_serve_a_ficha`. Lista vazia serve."""
    for base in provas or []:
        arquivo = Path(pasta) / f"{base}.json"
        try:
            ficha = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not _serve_a_ficha(pasta, ficha):
            return False
    return True


def capturas_guardadas(pasta) -> list[dict]:
    """As fichas (.json) das capturas já salvas na pasta, cada uma só se o PNG ainda estiver lá (e, com o print de
    tela inteira pedido, só as feitas nesse modo)."""
    fichas = []
    for arquivo in sorted(Path(pasta).glob("*.json")):
        try:
            ficha = json.loads(arquivo.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if (isinstance(ficha, dict) and "captura" in ficha and (Path(pasta) / ficha.get("arquivos", {}).get("png", "")).is_file()
                and _serve_a_ficha(pasta, ficha)):
            fichas.append(ficha)
    return fichas


def capturar(pagina, pasta, obra, interprete, plataforma, exibido: dict, etapa="", carimbo=True) -> dict:
    """Carimba a página como está, salva PNG, HTML e JSON e devolve o conteúdo do JSON.

    `carimbo=False`: o print sai sem a faixa no topo (os mesmos dados ficam só no JSON).

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
    if carimbo:
        pagina.evaluate(_CARIMBAR, texto_do_carimbo(pagina.url, quando, base, data_do_servidor, so_a_url=TELA_INTEIRA))
    html = pagina.content().encode("utf-8")
    # Sem carimbo (réplica), o print vem do próprio Chrome: é o único que sai certo com o zoom do navegador mudado
    imagem = pagina.screenshot(type="png") if carimbo else _print_da_pagina(pagina)
    tela, da_tela = _tela_inteira(pagina, imagem, pela_posicao=not carimbo) if TELA_INTEIRA else (None, None)
    pagina.evaluate("() => { const faixa = document.getElementById('__carimbo_de_captura'); if (faixa) faixa.remove(); }")
    (pasta / f"{base}.png").write_bytes(imagem)
    (pasta / f"{base}.html").write_bytes(html)
    registro = {
        "captura": base, "url": pagina.url, "plataforma": plataforma, "obra": obra, "interprete": interprete,
        "capturado_em": quando.isoformat(timespec="seconds"), "fuso": FUSO, "data_do_servidor": data_do_servidor,
        "exibido": exibido, "versao_do_app": VERSAO_DO_APP, "carimbo_no_print": carimbo,
        "arquivos": {"png": f"{base}.png", "html": f"{base}.html"},
        "sha256": {"png": sha256(imagem), "html": sha256(html)},
    }
    if da_tela is not None:
        registro["tela_inteira"] = da_tela  # o monitor fotografado, ou o motivo de não ter sido possível
        if tela is None and not carimbo:
            _guardar_diagnostico(pasta, base, imagem, da_tela)
    if tela is not None:
        (pasta / f"{base}_tela.png").write_bytes(tela)
        registro["arquivos"]["tela"], registro["sha256"]["tela"] = f"{base}_tela.png", sha256(tela)
    disco.gravar_json((pasta / f"{base}.json"), registro, indent=1)
    return registro


def _guardar_diagnostico(pasta, base, imagem: bytes, da_tela: dict, maximo=10) -> None:
    """Quando o print do monitor falha, guarda o print da página e as medidas em `diagnostico/`, para entender o
    motivo (monitor diferente, escala do Windows, zoom). Fica só na pasta do caso, no computador; só os últimos 10."""
    try:
        pasta = Path(pasta) / "diagnostico"
        pasta.mkdir(exist_ok=True)
        (pasta / f"{base}_pagina.png").write_bytes(imagem)
        disco.gravar_json((pasta / f"{base}.json"), da_tela, indent=1)
        for velho in sorted(pasta.glob("*.json"))[:-maximo]:
            velho.unlink(missing_ok=True)
            (pasta / f"{velho.stem}_pagina.png").unlink(missing_ok=True)
    except OSError:
        pass


def descartar(pasta, base) -> None:
    """Apaga uma captura (PNG, HTML, JSON e a foto do monitor) que não vai servir de prova."""
    for nome in (f"{base}.png", f"{base}.html", f"{base}.json", f"{base}_tela.png"):
        (Path(pasta) / nome).unlink(missing_ok=True)


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


_MEDIDAS_DA_JANELA = """() => [window.screenX, window.screenY, window.outerWidth, window.outerHeight,
  window.innerWidth, window.innerHeight, window.devicePixelRatio || 1]"""
# Os níveis de zoom do Chrome (Ctrl + / Ctrl -)
_ZOOMS = (0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5)


def _print_da_pagina(pagina) -> bytes:
    """O que a janela mostra da página, tirado pelo próprio Chrome (CDP). Com o zoom do navegador abaixo de 100%,
    o print do Playwright sai maior que a janela, com a página num canto e o resto em branco."""
    try:
        sessao = pagina.context.new_cdp_session(pagina)
        try:
            return base64.b64decode(sessao.send("Page.captureScreenshot", {"format": "png"})["data"])
        finally:
            sessao.detach()
    except Exception:  # navegador sem esse comando
        return pagina.screenshot(type="png")


def posicao_da_pagina(medidas, largura_do_print: int | None = None) -> tuple[float, float, float]:
    """Onde a área da página está na área de trabalho, em pixels da tela: (esquerda, topo, pixels da tela por pixel
    do print). É o canto da janela mais as bordas e barras dela. Sem `largura_do_print`, vale um pixel do print por
    pixel da página.

    O zoom do navegador (a pessoa diminui para caber mais na tela) muda `innerWidth` e `devicePixelRatio`, mas não
    `screenX` nem `outerWidth`, que ficam em pixels do sistema. O zoom é o nível do Chrome mais perto da razão entre
    as duas larguras (as bordas laterais, de até uns 16 pixels, não chegam a mudar o nível)."""
    x, y, largura_fora, altura_fora, largura_dentro, altura_dentro, escala = medidas
    zoom = min(_ZOOMS, key=lambda z: abs(z - (largura_fora - 8) / largura_dentro))
    sistema = escala / zoom  # a escala do Windows (100%, 125%, 150%...)
    borda = (largura_fora * sistema - largura_dentro * escala) / 2
    por_pixel_do_print = largura_dentro * escala / largura_do_print if largura_do_print else escala
    return x * sistema + borda, y * sistema + altura_fora * sistema - altura_dentro * escala - borda, por_pixel_do_print


def janela_da_pagina(avaliar) -> tuple[int, int, int, int] | None:
    """Onde está a janela desta página, em pixels de verdade, perguntando ao Windows (`tela.retangulo_da_janela`).

    Para não confundir com outra janela aberta na mesma página (o Chrome da pessoa, outro teste), a página ganha por
    um instante um título único; o título de antes volta logo em seguida, antes de qualquer print do monitor.
    `avaliar(js)` roda um script na página (Playwright ou CDP)."""
    import secrets
    import time

    from . import tela

    marca = f"quem-canta-{secrets.token_hex(6)}"
    try:
        antes = avaliar("() => document.title")
        avaliar(f"() => {{ document.title = {json.dumps(marca)}; }}")
    except Exception:
        return None
    try:
        for _ in range(15):  # o Windows leva um instante para mostrar o título novo
            time.sleep(0.1)
            janela = tela.retangulo_da_janela(marca)
            if janela:
                return janela
        return None
    finally:
        try:
            avaliar(f"() => {{ document.title = {json.dumps(antes)}; }}")
            time.sleep(0.3)  # a aba volta a mostrar o título certo antes do print
        except Exception:
            pass


def fotografar_o_monitor_da_pagina(imagem: bytes, medidas, janela=None) -> tuple[bytes | None, dict]:
    """O monitor em que a página (`imagem`, o print da página) está à vista, achada sem a faixa de carimbo.

    Primeiro pela `janela` que o próprio Windows informa (`janela_da_pagina`: pixels de verdade, vale com qualquer
    resolução, escala e mistura de monitores); depois pela conta com as medidas do navegador; por último, procurando
    alguns pixels em volta.
    """
    from PIL import Image

    from . import tela

    largura, altura = Image.open(io.BytesIO(imagem)).size
    esquerda, topo, escala = posicao_da_pagina(medidas, largura)
    candidatos = []
    if janela:
        a_esquerda, _, a_direita, embaixo = janela
        borda = max(0.0, ((a_direita - a_esquerda) - largura * escala) / 2)  # a página fica embaixo, centrada
        candidatos.append((a_esquerda + borda, embaixo - altura * escala - borda))
    candidatos.append((esquerda, topo))
    dados = {}
    for x, y in candidatos:
        foto, dados = tela.fotografar_na_posicao(imagem, x, y, escala)
        if foto is not None:
            return foto, {**dados, "achada_pela": "janela" if janela and (x, y) == candidatos[0] else "conta"}
    foto, dados_de_perto = tela.fotografar_na_posicao(imagem, *candidatos[0], escala, procurar=12)
    if foto is not None:
        return foto, dados_de_perto
    return None, {**dados, "medidas": list(medidas), "janela": list(janela) if janela else None,
                  "posicoes_tentadas": [[round(x), round(y)] for x, y in candidatos], "escala": escala}


def _tela_inteira(pagina, imagem: bytes, pela_posicao=False) -> tuple[bytes | None, dict]:
    """Fotografa o monitor em que a página está à vista, com a faixa de carimbo ainda na tela. Sem a faixa
    (`pela_posicao`), a página é achada no monitor pela posição que a janela informa.

    Se a janela estiver coberta ou minimizada, tenta uma vez reabri-la e trazê-la para a frente. Falha aqui nunca derruba a captura:
    a captura de página continua valendo, e o motivo fica na ficha.
    """
    from . import tela

    try:
        pagina.wait_for_timeout(250)  # dá tempo de o sistema desenhar a faixa na janela

        def tentar(captura_da_pagina):
            if not pela_posicao:
                return tela.fotografar(captura_da_pagina)
            janela = janela_da_pagina(pagina.evaluate)
            return fotografar_o_monitor_da_pagina(captura_da_pagina, pagina.evaluate(_MEDIDAS_DA_JANELA), janela)

        foto, dados = tentar(imagem)
        if foto is None:
            _restaurar_janela(pagina)
            pagina.bring_to_front()
            pagina.wait_for_timeout(1000)
            foto, dados = tentar(_print_da_pagina(pagina) if pela_posicao else pagina.screenshot(type="png"))
        return foto, dados
    except tela.SemComponente as e:
        return None, {"erro": str(e)}
    except Exception as e:  # sistema sem permissão de gravar a tela, monitor desligado no meio etc.
        return None, {"erro": f"não foi possível fotografar a tela ({type(e).__name__}: {e})"}
