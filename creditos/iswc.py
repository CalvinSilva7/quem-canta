"""ISWC Network, para a réplica.

Os Termos de Uso do ISWC-Net permitem só consultas manuais, e o "não sou um robô" recusa o navegador do app. Por
isso o ISWC abre no Google Chrome da pessoa, com um perfil só dele: ela resolve o captcha; o app abre o link de
resultado de cada obra (um por clique, na mesma aba) e salva a página em PDF, como o Ctrl+P do Chrome, quando ela
pede. No fim, junta os PDFs num só ou faz um .zip com um PDF por obra."""

import re
from pathlib import Path

from cantor.matching import normalizar
from . import disco

BUSCA = "https://iswcnet.cisac.org/search"
PLATAFORMA, ETAPA = "ISWC Network", "iswc"


def endereco_da_busca(iswc: str) -> str:
    """O link de resultado que o próprio ISWC Network oferece (busca/?iswc=): aberto pela pessoa, no navegador dela,
    depois de ela ter passado pelo "não sou um robô", mostra o resultado daquele ISWC. O app nunca abre vários de
    uma vez: cada link é aberto quando a pessoa clica no botão."""
    compacto = re.sub(r"[^T0-9]", "", str(iswc or "").upper())
    return f"{BUSCA}?iswc={compacto}" if compacto else BUSCA


def _so_os_digitos(iswc: str) -> str:
    return re.sub(r"[^0-9]", "", str(iswc or ""))


def conferir_resultado(url: str, texto: str, iswc: str) -> str:
    """A janela mostra o resultado da busca por este ISWC? Devolve o motivo de não mostrar, ou "" se mostra."""
    if "iswcnet.cisac.org" not in url:
        return "a janela não está no ISWC Network"
    if "search results" not in normalizar(texto):
        if "terms of use" in normalizar(texto) or "agreement" in normalizar(texto):
            return "o ISWC Network está mostrando os Termos de Uso: aceite na janela e faça a busca"
        return "a janela ainda não mostra o resultado da busca: cole o ISWC, clique em Search e espere o resultado"
    if re.search(r"(^|\D)0 results", normalizar(texto)):
        return "a busca não achou nenhuma obra com este ISWC"
    digitos = _so_os_digitos(iswc)
    if digitos and digitos not in _so_os_digitos(texto):
        return f"o resultado na janela não é o do ISWC {iswc}: busque este ISWC e fotografe de novo"
    return ""


def _chrome() -> str | None:
    """O Chrome instalado no computador, se houver."""
    import os

    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if base and (Path(base) / "Google/Chrome/Application/chrome.exe").exists():
            return str(Path(base) / "Google/Chrome/Application/chrome.exe")
    return None


def _janelas_do_chrome() -> set[int]:
    """As janelas do Chrome à vista agora (identificadores do Windows)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    achadas = set()

    def olhar(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            classe = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, classe, 64)
            if classe.value == "Chrome_WidgetWin_1" and user32.GetWindowTextLengthW(hwnd) > 0:
                achadas.add(int(hwnd))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(olhar), 0)
    return achadas


def _monitor_da_janela(hwnd) -> tuple[int, int, int, int]:
    """O retângulo (esquerda, topo, direita, base) do monitor em que a janela está."""
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    monitor = user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)  # 2 = o monitor mais próximo
    info = Info(cbSize=ctypes.sizeof(Info))
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    r = info.rcMonitor
    return r.left, r.top, r.right, r.bottom


def outro_monitor(onde_esta_o_app, monitores) -> dict | None:
    """O monitor em que a janela do ISWC deve abrir: um que não seja o do app. Com um monitor só, nenhum."""
    esquerda, topo = onde_esta_o_app[0], onde_esta_o_app[1]
    return next((m for m in monitores if (m["left"], m["top"]) != (esquerda, topo)), None)


def _levar_para_o_outro_monitor(antes: set[int], app: int, espera=8) -> bool:
    """Acha a janela nova do Chrome e a maximiza num monitor que não é o do app. Devolve se conseguiu."""
    import ctypes
    import time

    import mss

    user32 = ctypes.windll.user32
    with mss.mss() as telas:
        monitores = [dict(m) for m in telas.monitors[1:]]
    destino = outro_monitor(_monitor_da_janela(app), monitores) if app else None
    if destino is None:
        return False
    fim = time.monotonic() + espera
    while time.monotonic() < fim:
        novas = _janelas_do_chrome() - antes
        if novas:
            hwnd = max(novas)
            user32.ShowWindow(hwnd, 9)  # restaura, para poder mover
            user32.SetWindowPos(hwnd, 0, destino["left"] + 40, destino["top"] + 40, 1200, 800, 0x0004)
            user32.ShowWindow(hwnd, 3)  # maximiza no monitor para onde foi
            return True
        time.sleep(0.2)
    return False


# O Chrome do ISWC: o Chrome de verdade da pessoa (não o navegador do app, que o "não sou um robô" recusa), aberto
# com um perfil só dele e uma porta local por onde o app pede duas coisas: abrir o endereço da obra e salvar a página
# em PDF (o mesmo "Imprimir > Salvar como PDF" do Chrome, com data, título, endereço e página). A busca e o "não sou um
# robô" continuam com a pessoa.
PORTA = 9339
PAPEL = {"paperWidth": 11.69, "paperHeight": 16.54}  # A3, o papel dos PDFs do ISWC feitos à mão
# Os PDFs feitos à mão saem com a janela do Chrome estreita: o site mostra o resultado em linhas ("ISWC:", "Original
# Title:", ...) e os nomes completos, e não na tabela larga, que corta os nomes. Na hora de salvar, o app faz a página
# se comportar como numa janela desta largura, também durante a impressão, e depois desfaz.
LARGURA_DA_JANELA = 1000
_COMO_JANELA_ESTREITA = r"""((largura) => {
  const vale = (texto) => {
    let algum = null;
    for (const p of texto.split(',').map(p => p.trim())) {
      if (/print|high-contrast|prefers/.test(p)) return null;
      const max = p.match(/max-width:\s*([\d.]+)px/), min = p.match(/min-width:\s*([\d.]+)px/);
      if (!max && !min) return null;
      const ok = (!max || largura <= +max[1]) && (!min || largura >= +min[1]);
      algum = algum || ok;
    }
    return algum;
  };
  const mudadas = [];
  for (const folha of document.styleSheets) {
    let regras; try { regras = folha.cssRules } catch (e) { continue }
    for (const r of regras) {
      if (!r.media) continue;
      const antes = r.media.mediaText, v = vale(antes);
      if (v === null) continue;
      r.media.mediaText = v ? 'all' : 'not all';
      mudadas.push([r, antes]);
    }
  }
  const original = Object.getOwnPropertyDescriptor(MediaQueryList.prototype, 'matches');
  Object.defineProperty(MediaQueryList.prototype, 'matches', {configurable: true, get() {
    const v = vale(this.media); return v === null ? original.get.call(this) : v;
  }});
  window.__desfazer = () => {
    mudadas.forEach(([r, antes]) => r.media.mediaText = antes);
    Object.defineProperty(MediaQueryList.prototype, 'matches', original);
  };
  return mudadas.length;
})"""


def perfil_do_chrome(pasta_do_caso) -> Path:
    return Path(pasta_do_caso).parent.parent / "perfil-iswc-chrome"


def _alvos(porta=PORTA) -> list[dict] | None:
    """As abas do Chrome do ISWC, ou None se ele não está aberto."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/json/list", timeout=2) as resposta:
            return [a for a in json.load(resposta) if a.get("type") == "page"]
    except Exception:
        return None


def chrome_aberto(porta=PORTA) -> bool:
    return _alvos(porta) is not None


def _comandos(alvo: dict):
    """Fala com uma aba do Chrome: devolve a função cdp(método, **parâmetros) e a conexão (para fechar)."""
    import itertools
    import json

    from websockets.sync.client import connect

    conexao = connect(alvo["webSocketDebuggerUrl"], max_size=None, open_timeout=10)
    numeros = itertools.count(1)

    def cdp(metodo, **parametros):
        numero = next(numeros)
        conexao.send(json.dumps({"id": numero, "method": metodo, "params": parametros}))
        while True:
            resposta = json.loads(conexao.recv(timeout=60))
            if resposta.get("id") == numero:
                if "error" in resposta:
                    raise RuntimeError(resposta["error"].get("message", "erro do Chrome"))
                return resposta.get("result", {})

    return cdp, conexao


def _aba_do_iswc(alvos: list[dict]) -> dict | None:
    return next((a for a in alvos if "iswcnet.cisac.org" in a.get("url", "")), alvos[0] if alvos else None)


def abrir_no_chrome(url: str, perfil, porta=PORTA, nome="o Chrome do ISWC") -> str:
    """Abre o endereço no Chrome do ISWC: se ele já está aberto, na mesma aba (não acumula janelas); se não, abre o
    Chrome, maximizado no monitor em que o app não está. Devolve onde abriu, para a tela dizer.

    Serve também para outro site com Chrome próprio (a Socinpro): outra `porta`, outro `perfil`, outro `nome`."""
    alvos = _alvos(porta)
    if alvos:
        cdp, conexao = _comandos(_aba_do_iswc(alvos))
        try:
            cdp("Page.navigate", url=url)
            cdp("Page.bringToFront")
        finally:
            conexao.close()
        return nome
    import ctypes
    import subprocess

    chrome = _chrome()
    if not chrome:
        raise RuntimeError("o Google Chrome não está instalado neste computador")
    try:
        antes, app = _janelas_do_chrome(), int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        antes, app = set(), 0
    Path(perfil).mkdir(parents=True, exist_ok=True)
    subprocess.Popen([chrome, f"--user-data-dir={Path(perfil).resolve()}", f"--remote-debugging-port={porta}", "--no-first-run",
                      "--no-default-browser-check", "--lang=pt-BR", "--new-window", url])
    try:
        movida = _levar_para_o_outro_monitor(antes, app)
    except Exception:
        movida = False
    return f"{nome}, no outro monitor" if movida else nome


def fechar_chrome(porta=PORTA):
    alvos = _alvos(porta)
    if alvos:
        cdp, conexao = _comandos(alvos[0])
        try:
            cdp("Browser.close")
        except Exception:
            pass
        finally:
            conexao.close()


def salvar_pdf(pasta, obra: str, iswc: str, nomes=(), porta=PORTA) -> dict:
    """Salva em PDF a página que está no Chrome do ISWC, se ela mostra o resultado deste ISWC.
    Devolve {"ok", "ficha" ou "motivo", "titular_na_pagina"}, como guardar_pdf."""
    import json
    import tempfile

    alvos = _alvos(porta)
    if alvos is None:
        return {"ok": False, "motivo": "o Chrome do ISWC não está aberto: clique em **Abrir no ISWC**"}
    aba = _aba_do_iswc(alvos)
    cdp, conexao = _comandos(aba)
    try:
        lido = cdp("Runtime.evaluate", expression="JSON.stringify([location.href, document.body ? document.body.innerText : ''])",
                   returnByValue=True)
        url, texto = json.loads(lido["result"]["value"])
        motivo = conferir_resultado(url, texto, iswc)
        if motivo:
            return {"ok": False, "motivo": motivo.replace("fotografe de novo", "salve de novo")}
        import base64
        import time

        cdp("Emulation.setDeviceMetricsOverride", width=LARGURA_DA_JANELA, height=900, deviceScaleFactor=1, mobile=False)
        try:
            time.sleep(1.5)  # o site refaz o resultado para a janela estreita
            cdp("Runtime.evaluate", expression=f"({_COMO_JANELA_ESTREITA})({LARGURA_DA_JANELA})")
            pdf = base64.b64decode(cdp("Page.printToPDF", displayHeaderFooter=True, printBackground=False, **PAPEL)["data"])
        finally:
            try:
                cdp("Runtime.evaluate", expression="window.__desfazer && window.__desfazer()")
                cdp("Emulation.clearDeviceMetricsOverride")
            except Exception:
                pass
    finally:
        conexao.close()
    with tempfile.TemporaryDirectory() as provisoria:
        arquivo = Path(provisoria) / "Search.pdf"
        arquivo.write_bytes(pdf)
        return guardar_pdf(pasta, obra, iswc, arquivo, nomes,
                           como="PDF da página, como o \"Imprimir > Salvar como PDF\" do Chrome, salvo pelo app quando a pessoa "
                                "pediu; o Chrome é o da pessoa e o \"não sou um robô\" foi resolvido por ela")


def texto_do_pdf(caminho) -> str:
    import pdfplumber

    with pdfplumber.open(str(caminho)) as pdf:
        return "\n".join(pagina.extract_text() or "" for pagina in pdf.pages)


def guardar_pdf(pasta, obra: str, iswc: str, origem, nomes=(), como="") -> dict:
    """Confere o PDF que a pessoa salvou no Chrome (Imprimir > Salvar como PDF) e, se for o resultado deste ISWC,
    leva-o para a pasta do caso com a ficha (hora, código de conferência, texto). O PDF não é alterado: o
    cabeçalho e o rodapé (data, título, endereço, página) são os do próprio Chrome.
    Devolve {"ok", "ficha" ou "motivo", "titular_na_pagina"}."""
    import json
    import shutil

    from . import captura
    from .replica import nomes_na_pagina
    from .versao import VERSAO

    origem = Path(origem)
    try:
        texto = texto_do_pdf(origem)
    except Exception as e:
        return {"ok": False, "motivo": f"não consegui ler o PDF {origem.name}: {type(e).__name__}"}
    motivo = conferir_resultado("iswcnet.cisac.org" if "iswcnet.cisac.org" in texto else "", texto, iswc)
    if motivo:
        return {"ok": False, "motivo": f"{origem.name}: {motivo.replace('fotografe de novo', 'salve o PDF de novo')}"}
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    quando = captura.agora()
    base = captura.nome_base(obra, "", PLATAFORMA, quando) + f"_{ETAPA}"
    extra = 2
    while (pasta / f"{base}.pdf").exists():
        base = f"{base.rsplit('__', 1)[0]}__{extra}"
        extra += 1
    conteudo = origem.read_bytes()
    shutil.move(str(origem), str(pasta / f"{base}.pdf"))  # sai dos Downloads: o próximo "Salvar" não pergunta se substitui
    nomes_achados = nomes_na_pagina(texto, nomes)
    ficha = {
        "captura": base, "plataforma": PLATAFORMA, "obra": obra, "interprete": "",
        "capturado_em": quando.isoformat(timespec="seconds"), "fuso": captura.FUSO, "versao_do_app": VERSAO,
        "como": como or "PDF salvo pela própria pessoa no Chrome (Imprimir > Salvar como PDF); a busca foi feita por ela",
        "arquivo_original": origem.name,
        "exibido": {"iswc": iswc, "titulo": obra, "nomes_do_titular_na_pagina": nomes_achados, "texto_lido": texto[:4000]},
        "arquivos": {"pdf": f"{base}.pdf"}, "sha256": {"pdf": captura.sha256(conteudo)},
    }
    disco.gravar_json((pasta / f"{base}.json"), ficha, indent=1)
    return {"ok": True, "ficha": ficha, "titular_na_pagina": bool(nomes_achados)}


def juntar_pdfs(caminhos) -> bytes:
    """Um PDF só, com as páginas de cada arquivo na ordem dada, sem mexer nelas. Usa o pypdfium2, que já vem com o
    pdfplumber: a atualização do app troca os arquivos, mas não instala biblioteca nova."""
    import io

    import pypdfium2 as pdfium

    junto = pdfium.PdfDocument.new()
    for caminho in caminhos:
        origem = pdfium.PdfDocument(str(caminho))
        try:
            junto.import_pages(origem)
        finally:
            origem.close()
    saida = io.BytesIO()
    junto.save(saida)
    junto.close()
    return saida.getvalue()


PROIBIDOS_NO_NOME = set('<>:"/|?*' + chr(92))  # chr(92) é a barra invertida


def nome_no_zip(posicao: int, total: int, obra: str, iswc: str) -> str:
    """"01 - COISA FEITA (T-123.456.789-0).pdf": a posição na ordem da lista e o que o Windows aceita em nome de arquivo."""
    titulo = "".join(c for c in obra or "" if c not in PROIBIDOS_NO_NOME and ord(c) >= 32).strip().rstrip(".")[:120] or "obra"
    return f"{posicao:0{max(2, len(str(total)))}d} - {titulo}" + (f" ({iswc})" if iswc else "") + ".pdf"


def zip_por_obra(itens) -> bytes:
    """Um .zip com um PDF por obra, na ordem dada: itens = [(obra, iswc, caminho do PDF)]. Os PDFs entram como foram
    salvos (o mesmo código de conferência da ficha)."""
    import io
    import zipfile

    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as pacote:
        for posicao, (obra, iswc, caminho) in enumerate(itens, 1):
            pacote.write(caminho, nome_no_zip(posicao, len(itens), obra, iswc))
    return saida.getvalue()


def copiar(texto: str) -> bool:
    """Põe o texto na área de transferência do Windows, para a pessoa colar na busca. Devolve se deu certo."""
    import subprocess
    import sys

    if sys.platform != "win32":
        return False
    try:
        subprocess.run(["clip"], input=str(texto).encode("utf-16-le"), check=True, timeout=5)
        return True
    except Exception:
        return False
