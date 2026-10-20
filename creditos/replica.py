"""Prints da réplica: a pessoa conduz, o app abre a página e fotografa quando ela pede.

Uma janela do navegador do app fica aberta, com um perfil próprio para cada site (a sessão fica guardada nele, como
na Deezer e na Claro Música). A pessoa faz nela o que o site pede (login, aceite dos termos, a busca) e, conferida a
página, pede o print. O app nunca digita senha nem resolve o "não sou um robô": se aparecer, a pessoa resolve na
janela.

O Playwright só pode ser usado na linha de execução que o abriu. Por isso a janela vive numa linha própria, que
recebe os pedidos da tela por uma fila e devolve a resposta de cada um.
"""

import json
import queue
import re
import threading
import time
from pathlib import Path

from cantor.matching import normalizar

from . import captura


def nomes_na_pagina(texto: str, nomes) -> list[str]:
    """Os nomes do titular que aparecem no texto da página. Vale também com as palavras em outra ordem
    ("LIMA ZECA" para "Zeca Lima", como o ISWC escreve)."""
    pagina = normalizar(texto)
    palavras_da_pagina = set(pagina.split())
    achados = []
    for nome in nomes:
        palavras = normalizar(nome or "").split()
        if palavras and (" ".join(palavras) in pagina or (len(palavras) >= 2 and set(palavras) <= palavras_da_pagina)):
            achados.append(nome)
    return achados


class Janela:
    """A janela de um site, numa linha de execução própria. `pedir` manda um pedido e espera a resposta."""

    def __init__(self, perfil, pasta, inicio: str, plataforma: str, etapa: str, tela_inteira=False):
        self.perfil, self.pasta, self.tela_inteira = Path(perfil), Path(pasta), tela_inteira
        self.inicio, self.plataforma, self.etapa = inicio, plataforma, etapa
        self._pedidos = queue.Queue()
        self._linha = threading.Thread(target=self._rodar, daemon=True)
        self.situacao = "abrindo a janela"
        self.erro = ""

    @property
    def viva(self) -> bool:
        return self._linha.is_alive()

    def iniciar(self):
        self._linha.start()
        return self

    def pedir(self, acao: str, espera=90, **dados) -> dict:
        if not self.viva:
            return {"ok": False, "motivo": f"a janela do {self.plataforma} não está aberta"}
        resposta, pronto = {}, threading.Event()
        self._pedidos.put((acao, dados, resposta, pronto))
        if not pronto.wait(espera):
            return {"ok": False, "motivo": "a janela demorou demais para responder"}
        return resposta

    def _rodar(self):
        from playwright.sync_api import sync_playwright

        from .navegador import maximizar, preparar_asyncio, tamanho_da_janela

        preparar_asyncio()
        antes = captura.TELA_INTEIRA
        try:
            with sync_playwright() as pw:
                captura.TELA_INTEIRA = self.tela_inteira  # para a janela abrir maximizada no monitor livre
                argumentos = ["--lang=pt-BR", "--disable-features=Translate", "--mute-audio"] + (tamanho_da_janela() or ["--window-size=1280,800"])
                captura.TELA_INTEIRA = antes
                contexto = pw.chromium.launch_persistent_context(
                    str(self.perfil), headless=False, args=argumentos, no_viewport=True, locale="pt-BR", timezone_id=captura.FUSO)
                contexto.add_init_script(f"({captura.TRAVA_DE_TRADUCAO})()")
                pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
                if self.tela_inteira:
                    captura.TELA_INTEIRA = True
                    maximizar(pagina)
                    captura.TELA_INTEIRA = antes
                pagina.goto(self.inicio, wait_until="domcontentloaded", timeout=60000)
                self.situacao = "janela aberta"
                while contexto.pages:
                    try:
                        acao, dados, resposta, pronto = self._pedidos.get(timeout=1)
                    except queue.Empty:
                        continue
                    pagina = contexto.pages[-1]
                    try:
                        if acao == "fechar":
                            resposta.update(ok=True)
                            pronto.set()
                            break
                        resposta.update(getattr(self, "_" + acao)(pagina, **dados))
                    except Exception as e:
                        resposta.update(ok=False, motivo=f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
                    pronto.set()
                try:
                    contexto.close()
                except Exception:
                    pass
        except Exception as e:
            self.erro = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
        finally:
            captura.TELA_INTEIRA = antes
            self.situacao = "janela fechada"

    def _abrir(self, pagina, url: str, conferir=None, espera=30) -> dict:
        """Abre o endereço e, com `conferir(url, texto)`, espera a página ficar pronta (motivo vazio)."""
        pagina.goto(url, wait_until="domcontentloaded", timeout=60000)
        if conferir is None:
            return {"ok": True, "url": pagina.url}
        fim = time.monotonic() + espera
        motivo = "a página ainda não carregou"
        while time.monotonic() < fim:  # o site monta o conteúdo depois de abrir
            pagina.wait_for_timeout(1000)
            motivo = conferir(pagina.url, pagina.inner_text("body"))
            if not motivo or "login" in motivo or "não mostra a página" in motivo:  # resposta final do site: não adianta esperar
                break
        return {"ok": not motivo, "motivo": motivo, "url": pagina.url}

    def _fotografar(self, pagina, obra: str, conferir, nomes=(), exibido=None) -> dict:
        """Confere a página com `conferir(url, texto)` e, se estiver certa, tira o print com o carimbo."""
        texto = pagina.inner_text("body")
        motivo = conferir(pagina.url, texto)
        if motivo and "não mostra a página" not in motivo:
            return {"ok": False, "motivo": motivo}
        # a página certa, mas o site diz que não mostra a obra: o print vale como registro disso, com o aviso na ficha
        exibido = {**(exibido or {}), **({"aviso": motivo} if motivo else {}), "titulo": obra, "nomes_do_titular_na_pagina": nomes_na_pagina(texto, nomes), "texto": texto[:4000]}
        antes = captura.TELA_INTEIRA
        captura.TELA_INTEIRA = self.tela_inteira
        try:
            if self.tela_inteira:  # a caixa pode ter sido marcada depois de a janela abrir
                from .navegador import maximizar

                maximizar(pagina)
                pagina.wait_for_timeout(500)
            # sem a faixa do carimbo: na réplica o print vai como a página aparece; os dados do carimbo ficam no .json
            ficha = captura.capturar(pagina, self.pasta, obra, "", self.plataforma, exibido, etapa=self.etapa, carimbo=False)
        finally:
            captura.TELA_INTEIRA = antes
        if self.tela_inteira and not ficha.get("arquivos", {}).get("tela"):
            # o print do monitor inteiro é obrigatório (mostra a data e a hora): sem ele, o print não vale
            captura.descartar(self.pasta, ficha["captura"])
            return {"ok": False, "motivo": "não consegui fotografar o monitor inteiro ("
                    + ficha.get("tela_inteira", {}).get("erro", "motivo desconhecido")
                    + "). Deixe a janela do Ecadnet à vista, sem nada por cima, e clique em Fotografar de novo"}
        return {"ok": True, "ficha": ficha, "titular_na_pagina": bool(exibido["nomes_do_titular_na_pagina"]), "aviso": motivo}


def titulos_da_lista(conteudo: bytes) -> list[str]:
    """Os títulos das obras de uma lista da petição (.docx). A parte "A confirmar antes de usar" fica de fora: ela
    não entra na petição. Dois formatos:

    - o que o app gera: linhas "N-Música: TÍTULO";
    - a tabela feita à mão no Word: "Nº | obra (e, na linha de baixo da mesma célula, o intérprete) | link".
      Vale a primeira linha da segunda coluna das linhas cuja primeira coluna é só o número."""
    import io
    import re

    import docx

    from docx.oxml.ns import qn

    def texto_de(elemento) -> str:
        return "".join(t.text or "" for t in elemento.iter(qn("w:t"))).strip()

    titulos = []
    corpo = docx.Document(io.BytesIO(conteudo)).element.body
    # Na ordem do documento: parágrafos (também os de dentro das tabelas) e linhas de tabela
    for elemento in corpo.iter(qn("w:p"), qn("w:tr")):
        if elemento.tag == qn("w:tr"):
            celulas = elemento.findall(qn("w:tc"))
            if len(celulas) >= 2 and re.fullmatch(r"\d+", texto_de(celulas[0])):
                titulo = next((linha for linha in map(texto_de, celulas[1].iter(qn("w:p"))) if linha), "")
                if titulo and not re.match(r"^\d+\s*-\s*M[uú]sica:", titulo):
                    titulos.append(titulo)
            continue
        texto = texto_de(elemento)
        if normalizar(texto).startswith("a confirmar antes de usar"):
            break
        achado = re.match(r"^\d+\s*-\s*M[uú]sica:\s*(.+?)(?=\s+https?://|\s+Interpretada por:|\n|$)", texto)
        if achado:
            titulos += [t.strip() for t in achado[1].split("; ") if t.strip()]  # medley: "OBRA A; OBRA B"
    return list(dict.fromkeys(titulos))


_ARTIGO_OU_MONTAGEM = re.compile(r"^(?:o|a|os|as|mtg|montagem)\s+")
SEMELHANCA_MINIMA = 0.85


def _formas_do_titulo(titulo) -> set[str]:
    """Os jeitos de escrever o mesmo título que o ECAD usa: o título sem parênteses, o que está dentro deles (título
    alternativo: "PISTA TEST (PISTA TESTE)"), o começo antes de um parêntese cortado ("TÍTULO (PRIMEIRO"), cada
    parte de "A - B", e tudo isso sem artigo ("O RIO VIROU") nem "MTG" (montagem) no começo."""
    from .classificador import _titulo_base

    texto = str(titulo or "")
    pedacos = [texto, texto.split("(")[0], *re.findall(r"\(([^()]*)\)?", texto), *re.split(r"\s+[-–—]\s+", texto)]
    formas = {_titulo_base(p) for p in pedacos} | {normalizar(p) for p in pedacos}
    formas |= {_ARTIGO_OU_MONTAGEM.sub("", f) for f in formas}
    return {f for f in formas if len(f) >= 3}


def cruzar(titulos, relatorio) -> tuple[list, list[str], dict[str, list[str]]]:
    """As obras do relatório que estão na lista da petição, na ordem do relatório; os títulos da lista que o
    relatório não tem; e, para conferência, os que só bateram com o título escrito de outro jeito (lista -> títulos
    do relatório). Título repetido no relatório (obras diferentes com o mesmo nome) traz todas.

    Decisão do dono (20/10/2026): música que está na lista e no relatório entra, mesmo com o título escrito diferente
    ("TOADA DE LOUCA" e "TOADA DE LOUCO (TOADA DE LOUCA)"). Em ordem: título igual; uma das formas igual
    (`_formas_do_titulo`); uma forma começando pela outra; e, por último, formas quase iguais (letra trocada).
    Vale a primeira regra que achar alguma obra.
    """
    from difflib import SequenceMatcher

    from .classificador import _titulo_base

    formas_das_obras = [(o, _titulo_base(o.titulo), _formas_do_titulo(o.titulo)) for o in relatorio.obras]
    escolhidas, fora, aproximados = set(), [], {}
    for titulo in dict.fromkeys(titulos):
        base, formas = _titulo_base(titulo), _formas_do_titulo(titulo)
        regras = (
            lambda b, f: b == base,
            lambda b, f: bool(f & formas),
            lambda b, f: any(len(x) >= 8 and len(y) >= 8 and (x.startswith(y) or y.startswith(x)) for x in f for y in formas),
            lambda b, f: any(SequenceMatcher(None, x, y).ratio() >= SEMELHANCA_MINIMA for x in f for y in formas),
        )
        for numero, regra in enumerate(regras):
            achadas = [i for i, (_o, b, f) in enumerate(formas_das_obras) if regra(b, f)]
            if achadas:
                escolhidas.update(achadas)
                if numero:
                    aproximados[titulo] = [formas_das_obras[i][0].titulo for i in achadas]
                break
        else:
            fora.append(titulo)
    return [formas_das_obras[i][0] for i in sorted(escolhidas)], fora, aproximados


def prints_da_ultima_rodada(fichas, chave, minutos=3) -> list[dict]:
    """De cada obra (`chave(ficha)`), os prints da última rodada: o mais recente e os tirados até `minutos` antes dele
    (a pessoa às vezes tira dois ou três prints seguidos da mesma obra). Prints de rodadas mais antigas ficam de fora.
    Devolve na ordem em que foram tirados."""
    from datetime import datetime, timedelta

    def quando(f):
        try:
            return datetime.fromisoformat(f.get("capturado_em", ""))
        except ValueError:
            return None

    validas = [f for f in fichas if quando(f) is not None]
    ultimo = {}
    for f in validas:
        if chave(f) not in ultimo or quando(f) > ultimo[chave(f)]:
            ultimo[chave(f)] = quando(f)
    return sorted((f for f in validas if quando(f) >= ultimo[chave(f)] - timedelta(minutes=minutos)), key=quando)


def pdf_dos_prints(imagens) -> bytes:
    """Um PDF com uma imagem por página (A4 deitado, a imagem inteira, centrada, sem mexer nela)."""
    import io

    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    largura, altura = landscape(A4)
    margem = 20
    saida = io.BytesIO()
    folha = canvas.Canvas(saida, pagesize=(largura, altura))
    for imagem in imagens:
        leitor = ImageReader(str(imagem))
        w, h = leitor.getSize()
        escala = min((largura - 2 * margem) / w, (altura - 2 * margem) / h)
        folha.drawImage(leitor, (largura - w * escala) / 2, (altura - h * escala) / 2, w * escala, h * escala)
        folha.showPage()
    folha.save()
    return saida.getvalue()


JANELAS: dict[str, Janela] = {}  # a janela aberta de cada site, guardada entre um clique e outro na tela


def janela(chave: str) -> Janela | None:
    aberta = JANELAS.get(chave)
    return aberta if aberta is not None and aberta.viva else None


def abrir_janela(chave: str, **parametros) -> Janela:
    if janela(chave) is None:
        JANELAS[chave] = Janela(**parametros).iniciar()
    return JANELAS[chave]


def prints_guardados(pasta, etapa: str) -> list[dict]:
    """As fichas dos prints já tirados neste caso, dos mais novos para os mais velhos."""
    fichas = []
    for arquivo in sorted(Path(pasta).glob(f"*_{etapa}*.json"), reverse=True):
        try:
            fichas.append(json.loads(arquivo.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return fichas


# ---------------------------------------------------------------- ordem e exclusão dos prints (pedido do dono, 20/10/2026)

ORDEM = "ordem.json"
REMOVIDOS = "removidos"


def em_ordem(fichas, pasta) -> list[dict]:
    """As fichas na ordem que a pessoa escolheu (ordem.json da pasta) e, depois, as que ela ainda não mexeu, na ordem
    em que vieram (a da fila)."""
    try:
        escolhida = json.loads((Path(pasta) / ORDEM).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        escolhida = []
    posicao = {captura: i for i, captura in enumerate(escolhida) if isinstance(captura, str)}
    return sorted(fichas, key=lambda f: posicao.get(f.get("captura"), len(posicao)))  # sort estável: o resto como veio


def mover(pasta, fichas_em_ordem, captura: str, passo: int) -> None:
    """Sobe (passo -1) ou desce (passo +1) um print na ordem do PDF e guarda a ordem inteira."""
    ordem = [f.get("captura") for f in fichas_em_ordem]
    if captura not in ordem:
        return
    i = ordem.index(captura)
    j = max(0, min(len(ordem) - 1, i + passo))
    ordem[i], ordem[j] = ordem[j], ordem[i]
    (Path(pasta) / ORDEM).write_text(json.dumps(ordem, ensure_ascii=False, indent=1), encoding="utf-8")


def remover_print(pasta, captura: str) -> int:
    """Tira o print da lista e do PDF único. Os arquivos não são apagados: vão para a subpasta `removidos` (são
    provas; se foi engano, basta trazê-los de volta). Devolve quantos arquivos foram movidos."""
    import shutil

    pasta = Path(pasta)
    ficha_json = pasta / f"{captura}.json"
    if "/" in captura or "\\" in captura or not ficha_json.is_file():
        return 0
    try:
        arquivos = list(json.loads(ficha_json.read_text(encoding="utf-8")).get("arquivos", {}).values())
    except (OSError, ValueError):
        arquivos = []
    destino = pasta / REMOVIDOS
    destino.mkdir(exist_ok=True)
    movidos = 0
    for nome in [*arquivos, ficha_json.name]:
        origem = pasta / Path(str(nome)).name
        if origem.is_file():
            alvo, n = destino / origem.name, 2
            while alvo.exists():  # o mesmo print removido duas vezes (trazido de volta e removido de novo)
                alvo, n = destino / f"{origem.stem} ({n}){origem.suffix}", n + 1
            shutil.move(str(origem), str(alvo))
            movidos += 1
    return movidos
