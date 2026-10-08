"""Conversa direta com a porta de depuração de um programa feito com navegador embutido.

O Playwright sabe se conectar a um navegador inteiro. Um programa com navegador embutido (como o aplicativo
da Amazon Music) responde na mesma porta, mas não oferece tudo o que o Playwright exige de um navegador, e a
conexão dele falha. Aqui fala-se só com a tela do programa, pelo protocolo de depuração, com o mínimo: rodar
um script na página, navegar, mexer o mouse, apertar tecla e fotografar.

`PaginaCDP` imita o pedaço de uma página do Playwright que o resto do app usa (evaluate, content, screenshot,
url, goto, keyboard.press, wait_for_timeout, bring_to_front), para a captura de tela e a leitura de menus
funcionarem do mesmo jeito. Só usa a biblioteca padrão.
"""

import base64
import json
import os
import re
import socket
import struct
import time
from urllib.parse import urlsplit

import requests


class ErroDeConexao(Exception):
    pass


class _Canal:
    """Um WebSocket simples, sem criptografia, para falar com a porta de depuração neste mesmo computador."""

    def __init__(self, endereco: str, espera=30):
        partes = urlsplit(endereco)
        self.sock = socket.create_connection((partes.hostname, partes.port or 80), timeout=espera)
        chave = base64.b64encode(os.urandom(16)).decode()
        caminho = partes.path + (f"?{partes.query}" if partes.query else "")
        self.sock.sendall((f"GET {caminho} HTTP/1.1\r\nHost: {partes.hostname}:{partes.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {chave}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resposta = b""
        while b"\r\n\r\n" not in resposta:
            pedaco = self.sock.recv(4096)
            if not pedaco:
                raise ErroDeConexao("a porta de depuração fechou a conexão")
            resposta += pedaco
        if b" 101 " not in resposta.split(b"\r\n", 1)[0]:
            raise ErroDeConexao("a porta de depuração recusou a conexão: " + resposta.split(b"\r\n", 1)[0].decode(errors="ignore"))
        self._resto = resposta.split(b"\r\n\r\n", 1)[1]

    def _ler(self, quantos: int) -> bytes:
        while len(self._resto) < quantos:
            pedaco = self.sock.recv(65536)
            if not pedaco:
                raise ErroDeConexao("a conexão com o programa caiu")
            self._resto += pedaco
        dados, self._resto = self._resto[:quantos], self._resto[quantos:]
        return dados

    def enviar(self, texto: str, codigo=1):
        dados = texto.encode() if isinstance(texto, str) else texto
        mascara = os.urandom(4)
        cabecalho = bytes([0x80 | codigo])
        if len(dados) < 126:
            cabecalho += bytes([0x80 | len(dados)])
        elif len(dados) < 65536:
            cabecalho += bytes([0x80 | 126]) + struct.pack(">H", len(dados))
        else:
            cabecalho += bytes([0x80 | 127]) + struct.pack(">Q", len(dados))
        self.sock.sendall(cabecalho + mascara + bytes(b ^ mascara[i % 4] for i, b in enumerate(dados)))

    def receber(self) -> str:
        """A próxima mensagem de texto inteira (junta os pedaços e responde aos pings)."""
        mensagem = b""
        while True:
            primeiro, segundo = self._ler(2)
            tamanho = segundo & 0x7F
            if tamanho == 126:
                tamanho = struct.unpack(">H", self._ler(2))[0]
            elif tamanho == 127:
                tamanho = struct.unpack(">Q", self._ler(8))[0]
            mascara = self._ler(4) if segundo & 0x80 else None
            dados = self._ler(tamanho)
            if mascara:
                dados = bytes(b ^ mascara[i % 4] for i, b in enumerate(dados))
            codigo = primeiro & 0x0F
            if codigo == 9:  # ping
                self.enviar(dados, codigo=10)
                continue
            if codigo == 8:
                raise ErroDeConexao("o programa encerrou a conexão")
            if codigo in (0, 1, 2):
                mensagem += dados
                if primeiro & 0x80:
                    return mensagem.decode("utf-8", errors="replace")

    def fechar(self):
        try:
            self.sock.close()
        except OSError:
            pass


def telas(porta: int) -> list[dict]:
    """As telas que o programa tem abertas: [{"url", "title", "type", "webSocketDebuggerUrl"}]."""
    return requests.get(f"http://127.0.0.1:{porta}/json", timeout=5).json()


_FUNCAO = re.compile(r"\s*(async\s+)?(\(|function\b|[A-Za-z_$][\w$]*\s*=>)")
_TECLAS = {"Escape": (27, "Escape"), "Enter": (13, "Enter")}


class _Teclado:
    def __init__(self, pagina):
        self.pagina = pagina

    def press(self, tecla: str):
        codigo, nome = _TECLAS[tecla]
        for tipo in ("keyDown", "keyUp"):
            self.pagina.comando("Input.dispatchKeyEvent", type=tipo, key=nome, code=nome, windowsVirtualKeyCode=codigo,
                                nativeVirtualKeyCode=codigo)


class PaginaCDP:
    """A tela de um programa com navegador embutido, com os métodos de página que o app usa."""

    def __init__(self, endereco_do_canal: str, espera=30):
        self.canal, self.espera, self._numero = _Canal(endereco_do_canal, espera), espera, 0
        self.keyboard = _Teclado(self)
        self.comando("Page.enable")
        self.comando("Runtime.enable")

    def comando(self, metodo: str, **parametros) -> dict:
        self._numero += 1
        numero = self._numero
        self.canal.enviar(json.dumps({"id": numero, "method": metodo, "params": parametros}))
        limite = time.monotonic() + self.espera
        while time.monotonic() < limite:
            resposta = json.loads(self.canal.receber())
            if resposta.get("id") == numero:  # o resto são avisos da página, que não interessam aqui
                if "error" in resposta:
                    raise ErroDeConexao(f"{metodo}: {resposta['error'].get('message', resposta['error'])}")
                return resposta.get("result", {})
        raise ErroDeConexao(f"{metodo}: o programa não respondeu em {self.espera} segundos")

    def evaluate(self, script: str, *argumentos):
        """Roda o script na página e devolve o valor. Aceita função ("() => ...") ou expressão, como o Playwright."""
        if _FUNCAO.match(script) and ("=>" in script or "function" in script):
            script = f"({script})({', '.join(json.dumps(a, ensure_ascii=False) for a in argumentos)})"
        resultado = self.comando("Runtime.evaluate", expression=script, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in resultado:
            detalhe = resultado["exceptionDetails"]
            raise ErroDeConexao("o script falhou na página: " + str(detalhe.get("exception", {}).get("description") or detalhe.get("text"))[:300])
        return resultado.get("result", {}).get("value")

    @property
    def url(self) -> str:
        return self.evaluate("location.href") or ""

    def title(self) -> str:
        return self.evaluate("document.title") or ""

    def content(self) -> str:
        return self.evaluate("document.documentElement.outerHTML") or ""

    def goto(self, url: str, **_):
        self.comando("Page.navigate", url=url)

    def wait_for_timeout(self, milissegundos):
        time.sleep(milissegundos / 1000)

    def bring_to_front(self):
        self.comando("Page.bringToFront")

    def screenshot(self, type="png", path=None) -> bytes:
        imagem = base64.b64decode(self.comando("Page.captureScreenshot", format=type)["data"])
        if path:
            with open(path, "wb") as arquivo:
                arquivo.write(imagem)
        return imagem

    def mover_o_mouse(self, x: float, y: float):
        self.comando("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)

    def clicar(self, x: float, y: float):
        self.mover_o_mouse(x, y)
        for tipo in ("mousePressed", "mouseReleased"):
            self.comando("Input.dispatchMouseEvent", type=tipo, x=x, y=y, button="left", clickCount=1)

    def fechar(self):
        self.canal.fechar()
