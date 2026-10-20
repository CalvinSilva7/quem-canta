"""Réplica: busca alternativa nas consultas web das associações (Socinpro, UBC), para a obra que não aparece no Ecadnet.

Pedido do jurídico (20/10/2026): na maioria dos casos a obra está no Ecadnet; quando não está, as associações ligadas
ao ECAD às vezes têm o cadastro, e o print delas entra como prova complementar. Só para esses casos: não é para
coletar sempre.

As consultas são públicas, mas pedem o "não sou um robô" (reCAPTCHA) a cada pesquisa. Por isso, como no ISWC, o app
usa o Google Chrome da pessoa (perfil e porta próprios de cada site) e nunca resolve a verificação. O app preenche o
título; a pessoa resolve a verificação; o app pesquisa (quando o site pede a verificação antes do Pesquisar), acha a
obra nos resultados pelo código ECAD (o mesmo do relatório), abre os detalhes e, quando a pessoa pede, fotografa o
monitor inteiro.

O print fica na pasta do Ecadnet, com a etapa do Ecadnet e a plataforma da associação na ficha. Cada associação tem
lista e PDF próprios na tela ("Socinpro - <titular>.pdf", "UBC - <titular>.pdf"): não se misturam com o Ecadnet.

Cada site entra como uma `ConsultaWeb` (endereço, porta, e os três scripts da página: preencher, ler a tela, abrir a
obra). Os módulos `socinpro` e `ubc_web` só montam a sua e expõem as funções que a tela usa.
"""

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path

from cantor.matching import normalizar

from . import captura, ecadnet, iswc
from .replica import nomes_na_pagina

# offsetParent não serve: as janelas dos sites (verificação, detalhes) ficam em posição fixa na tela
VISIVEL = ("const visivel = e => !!e && getComputedStyle(e).display !== 'none' && "
           "getComputedStyle(e).visibility !== 'hidden' && e.getClientRects().length > 0;")


def titulo_para_buscar(titulo: str) -> str:
    """O título sem o que vem entre parênteses (o ECAD põe ali o título alternativo)."""
    return " ".join(str(titulo or "").split("(")[0].split()) or str(titulo or "")


def _avaliar(cdp, expressao: str, *argumentos):
    chamada = f"({expressao})({', '.join(json.dumps(a) for a in argumentos)})"
    resposta = cdp("Runtime.evaluate", expression=chamada, returnByValue=True, awaitPromise=True)
    if "exceptionDetails" in resposta:
        raise RuntimeError(resposta["exceptionDetails"].get("text", "erro na página"))
    return resposta.get("result", {}).get("value")


def _trazer_para_frente(titulo: str) -> None:
    """Põe a janela do Chrome com esta página por cima das outras (no Windows), para o print do monitor não sair
    coberto. Sem tirar do tamanho em que está (maximizada continua maximizada)."""
    import sys

    if sys.platform != "win32" or not titulo:
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        for janela in iswc._janelas_do_chrome():
            texto = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(janela, texto, 512)
            if texto.value.startswith(titulo[:60]):
                user32.ShowWindow(janela, 9 if user32.IsIconic(janela) else 5)  # 9: reabre a minimizada; 5: só mostra
                # O Windows só deixa trazer janela para a frente logo depois de uma tecla; o Alt solto não faz nada
                user32.keybd_event(0x12, 0, 0, 0)
                user32.SetForegroundWindow(janela)
                user32.keybd_event(0x12, 0, 2, 0)
                return
    except Exception:
        pass


@dataclass(frozen=True)
class ConsultaWeb:
    plataforma: str          # "Socinpro"; vai na ficha e no nome do PDF
    site: str
    host: str                # para achar a aba certa
    porta: int
    pasta_do_perfil: str     # perfil do Chrome, fora dos casos
    campo_do_codigo: str     # rótulo do código ECAD nos detalhes ("ECAD OBRA", "ECAD")
    js_preencher: str        # (titulo) => '' ou o motivo; na Socinpro também clica em Pesquisar
    js_estado: str           # () => {verificacao, pode_pesquisar, linhas, detalhes, campos, titulo_pesquisado, ...}
    js_abrir: str            # (codigo) => '' ou o motivo
    js_clicar_pesquisar: str = ""  # só onde a verificação vem antes do Pesquisar (UBC)
    como_resolver: str = "marque o **não sou um robô**"

    @property
    def nome(self) -> str:
        return f"o Chrome da {self.plataforma}"

    @property
    def botao_buscar(self) -> str:
        return f"Buscar na {self.plataforma}"

    def perfil_do_chrome(self, pasta_do_caso) -> Path:
        return Path(pasta_do_caso).parent.parent / self.pasta_do_perfil

    def chrome_aberto(self) -> bool:
        return iswc.chrome_aberto(self.porta)

    def fechar_chrome(self):
        iswc.fechar_chrome(self.porta)

    def _aba(self):
        alvos = iswc._alvos(self.porta)
        if not alvos:
            return None
        return next((a for a in alvos if self.host in a.get("url", "")), alvos[0])

    def _rodar(self, expressao: str, *argumentos):
        aba = self._aba()
        if aba is None:
            return None
        cdp, conexao = iswc._comandos(aba)
        try:
            return _avaliar(cdp, expressao, *argumentos)
        finally:
            conexao.close()

    def estado(self) -> dict | None:
        return self._rodar(self.js_estado)

    def pesquisa_desta_obra(self, na_tela: dict, titulo: str) -> bool:
        """A verificação ou os resultados que estão na tela são da pesquisa deste título? (Senão, são da obra
        anterior e é preciso pesquisar de novo.)"""
        return bool(na_tela) and normalizar(na_tela.get("titulo_pesquisado", "")) == normalizar(titulo_para_buscar(titulo))

    def pesquisar(self, titulo: str, perfil, espera_da_pagina=30) -> dict:
        """Abre a consulta no Chrome do site e preenche o título (na Socinpro, já clica em Pesquisar).
        Devolve {"ok", "motivo", "onde"}."""
        onde = iswc.abrir_no_chrome(self.site, perfil, self.porta, self.nome)
        fim = time.monotonic() + espera_da_pagina
        motivo = f"a consulta da {self.plataforma} não carregou"
        while time.monotonic() < fim:  # o Chrome pode estar abrindo agora
            time.sleep(1)
            aba = self._aba()
            if aba is None or self.host not in aba.get("url", ""):
                continue
            cdp, conexao = iswc._comandos(aba)
            try:
                if _avaliar(cdp, "() => document.readyState") != "complete":
                    continue
                time.sleep(1)  # a página monta os campos depois de carregar
                motivo = _avaliar(cdp, self.js_preencher, titulo_para_buscar(titulo))
            except Exception as e:
                motivo = f"a página não respondeu ({type(e).__name__})"
                continue
            finally:
                conexao.close()
            if not motivo:
                break
        return {"ok": not motivo, "motivo": motivo, "onde": onde}

    def _da_obra(self, na_tela: dict, codigo: str) -> bool:
        return bool(na_tela.get("detalhes")) and na_tela.get("campos", {}).get(self.campo_do_codigo) == codigo

    def esperar_e_abrir(self, codigo: str, espera=180) -> dict:
        """Espera a pessoa resolver o "não sou um robô", pesquisa (se o site pede a verificação antes) e abre os
        detalhes da obra deste código ECAD. Devolve {"ok", "motivo", "codigos"}: sem a obra nos resultados, `codigos`
        diz quais vieram (para a tela mostrar)."""
        fim = time.monotonic() + espera
        ultimo, pesquisou_em = {}, None
        while time.monotonic() < fim:
            # Enquanto a pessoa resolve a verificação, olha a página de longe (a cada 2 s), sem mexer nela
            time.sleep(2 if ultimo.get("verificacao") else 1)
            try:
                ultimo = self.estado() or {}
            except Exception:
                continue
            if self._da_obra(ultimo, codigo):
                return {"ok": True, "motivo": ""}
            if self.js_clicar_pesquisar and ultimo.get("pode_pesquisar") and pesquisou_em is None:
                motivo = self._rodar(self.js_clicar_pesquisar)  # verificação resolvida: o app clica em Pesquisar
                if motivo:
                    return {"ok": False, "motivo": motivo}
                pesquisou_em = time.monotonic()
                continue
            if not ultimo.get("linhas"):
                if pesquisou_em is not None and time.monotonic() - pesquisou_em > 30 and not ultimo.get("carregando"):
                    break  # pesquisou e nada veio
                continue  # a pessoa ainda está na verificação (ou a pesquisa não voltou)
            motivo = self._rodar(self.js_abrir, codigo)
            if motivo:
                return {"ok": False, "motivo": motivo, "codigos": [l["codigo"] for l in ultimo["linhas"]]}
            espera_dos_detalhes = time.monotonic() + 15
            while time.monotonic() < espera_dos_detalhes:
                time.sleep(0.5)
                if self._da_obra(self.estado() or {}, codigo):
                    return {"ok": True, "motivo": ""}
            return {"ok": False, "motivo": "cliquei nos detalhes da obra, mas eles não abriram: abra na janela e clique em Fotografar"}
        if ultimo.get("verificacao") and pesquisou_em is None:
            return {"ok": False, "motivo": f"o \"não sou um robô\" não foi resolvido a tempo: resolva e clique em "
                                           f"**{self.botao_buscar}** de novo"}
        return {"ok": False, "motivo": f"a pesquisa não trouxe resultados (a obra pode não estar na {self.plataforma})"}

    def conferir_detalhes(self, dados: dict, codigo: str, titulo: str = "") -> str:
        """Vazio se a tela mostra os detalhes desta obra; senão, o motivo."""
        if not dados.get("detalhes"):
            return f"os detalhes da obra não estão abertos na janela da {self.plataforma}"
        aberto = dados.get("campos", {}).get(self.campo_do_codigo)
        if aberto != codigo:
            return f"os detalhes abertos são de outra obra ({self.campo_do_codigo} {aberto or 'sem código'}, e esta é {codigo})"
        return ""

    def fotografar(self, pasta, obra: str, codigo: str, nomes=()) -> dict:
        """Print do monitor inteiro com os detalhes da obra abertos, mais o print e o HTML da página e a ficha.
        O print do monitor é obrigatório (mostra a data e a hora): sem ele, nada é guardado.
        Devolve {"ok", "ficha" ou "motivo", "titular_na_pagina"}, como a réplica do Ecadnet."""
        aba = self._aba()
        if aba is None:
            return {"ok": False, "motivo": f"{self.nome} não está aberto: clique em **{self.botao_buscar}**"}
        cdp, conexao = iswc._comandos(aba)
        try:
            dados = _avaliar(cdp, self.js_estado)
            motivo = self.conferir_detalhes(dados, codigo, obra)
            if motivo:
                return {"ok": False, "motivo": motivo}
            cdp("Page.bringToFront")
            _trazer_para_frente(aba.get("title", ""))
            time.sleep(0.8)

            def tentar():
                pagina = base64.b64decode(cdp("Page.captureScreenshot", format="png")["data"])
                medidas = _avaliar(cdp, captura._MEDIDAS_DA_JANELA)
                janela = captura.janela_da_pagina(lambda js: _avaliar(cdp, js))
                return pagina, *captura.fotografar_o_monitor_da_pagina(pagina, medidas, janela)

            pagina, foto, sobre_a_tela = tentar()
            if foto is None:
                time.sleep(1)
                pagina, foto, sobre_a_tela = tentar()
            html = _avaliar(cdp, "() => document.documentElement.outerHTML").encode("utf-8")
        finally:
            conexao.close()
        if foto is None:
            return {"ok": False, "motivo": f"não consegui fotografar o monitor inteiro ({sobre_a_tela.get('erro', 'motivo desconhecido')}). "
                                           f"Deixe {self.nome} à vista, sem nada por cima, e clique em Fotografar de novo"}

        pasta = Path(pasta)
        pasta.mkdir(parents=True, exist_ok=True)
        quando = captura.agora()
        base = captura.nome_base(obra, "", self.plataforma, quando) + f"_{ecadnet.ETAPA}"
        extra = 2
        while (pasta / f"{base}.json").exists():
            base = f"{base.rsplit('__', 1)[0]}__{extra}"
            extra += 1
        texto = dados.get("texto_dos_detalhes", "")
        nomes_achados = nomes_na_pagina(texto, nomes)
        arquivos = {"png": f"{base}.png", "html": f"{base}.html", "tela": f"{base}_tela.png"}
        (pasta / arquivos["png"]).write_bytes(pagina)
        (pasta / arquivos["html"]).write_bytes(html)
        (pasta / arquivos["tela"]).write_bytes(foto)
        ficha = {
            "captura": base, "url": dados.get("url"), "plataforma": self.plataforma, "obra": obra, "interprete": "",
            "capturado_em": quando.isoformat(timespec="seconds"), "fuso": captura.FUSO, "versao_do_app": captura.VERSAO_DO_APP,
            "como": f"Consulta web pública da {self.plataforma}, busca alternativa da réplica (a obra não estava no Ecadnet). "
                    "O app preencheu o título e abriu os detalhes; o \"não sou um robô\" foi resolvido pela pessoa, no Chrome dela",
            "exibido": {"codigo_da_obra": codigo, "fonte": self.plataforma, "titulo": obra, "campos": dados.get("campos", {}),
                        "nomes_do_titular_na_pagina": nomes_achados, "texto": texto[:4000]},
            "tela_inteira": sobre_a_tela, "carimbo_no_print": False, "arquivos": arquivos,
            "sha256": {chave: captura.sha256((pasta / nome).read_bytes()) for chave, nome in arquivos.items()},
        }
        (pasta / f"{base}.json").write_text(json.dumps(ficha, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True, "ficha": ficha, "titular_na_pagina": bool(nomes_achados)}
