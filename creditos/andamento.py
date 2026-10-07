"""Andamento da coleta: a barra de progresso, o tempo que falta e o pedido de parada.

A coleta roda em outra linha de execução e vai avisando o que faz ("Spotify: lendo faixa 3/40"). O Andamento
recebe esses avisos, sabe em que etapa a coleta está e quanto dela já passou, e é por ele que a tela pede para
parar: o próximo aviso depois do pedido interrompe a coleta. Como cada página lida já fica guardada, parar no
meio não perde nada.
"""

import re
import threading
import time

from . import pipeline

ETAPAS = ["deezer", "apple", "vagalume", "youtube", "spotify", "tidal"]  # a ordem em que a coleta acontece
PRINTS = "prints"
NOMES = {"deezer": "Deezer", "apple": "Apple Music", "vagalume": "Vagalume", "youtube": "YouTube Music",
         "spotify": "Spotify", "tidal": "Tidal", PRINTS: "Prints das provas"}
_PELO_NOME = {nome.lower(): etapa for etapa, nome in NOMES.items()}
# Que pedaço de cada etapa cada tipo de aviso ocupa: (palavra do aviso, começo, fim).
FASES = {
    "deezer": [("discografia", 0.0, 0.1), ("busca por título", 0.1, 0.4), ("lendo álbum", 0.4, 1.0)],
    "apple": [("buscando", 0.0, 0.4), ("lendo álbum", 0.4, 1.0)],
    "vagalume": [("procurando", 0.0, 1.0)],
    "youtube": [("buscando", 0.0, 0.3), ("lendo faixa", 0.3, 1.0)],
    "spotify": [("buscando", 0.0, 0.3), ("lendo faixa", 0.3, 1.0)],
    "tidal": [("procurando", 0.0, 0.5), ("lendo álbum", 0.5, 0.9), ("print", 0.9, 1.0)],
    PRINTS: [("print", 0.0, 1.0)],
}
_CONTAGEM = re.compile(r"(\d+)\s*/\s*(\d+)\s*$")


class Interrompida(BaseException):
    """A pessoa pediu para parar. Não é erro: herda de BaseException para atravessar os `except Exception` da coleta."""


class Andamento:
    """Recebe os avisos da coleta (é o `ao_avancar` dela) e responde quanto já foi feito."""

    def __init__(self, obras: int, plataformas, agora=time.monotonic):
        pedidas = set(plataformas) | {"deezer"}
        self.etapas = [e for e in ETAPAS if e in pedidas] + [PRINTS]
        self.pesos = {e: pipeline.SEGUNDOS_POR_OBRA.get(e, 0) * obras + pipeline.SEGUNDOS_FIXOS.get(e, 0) for e in self.etapas}
        self.pesos[PRINTS] = 30 + 2 * obras
        self.estimativa = sum(self.pesos.values())  # segundos, para quando ainda não dá para medir
        self.agora, self.inicio = agora, agora()
        self.parar = threading.Event()
        self.etapa, self.fase, self.avisos_na_fase = self.etapas[0], "", 0
        self.fracao, self.detalhe = 0.0, "Iniciando…"

    def __call__(self, texto: str) -> None:
        if self.parar.is_set():
            raise Interrompida()
        nome, _, resto = str(texto).partition(": ")
        etapa = _PELO_NOME.get(nome.strip().lower())
        if etapa is None:  # aviso sem plataforma (do navegador, por exemplo): só troca o texto
            self.detalhe = str(texto)
            return
        plataforma = NOMES[etapa]
        if "print" in resto and etapa in ("deezer", "apple", "vagalume"):
            etapa = PRINTS  # os prints dessas três são tirados no fim, depois de todas as leituras
        if etapa not in self.etapas:
            return
        if self.etapas.index(etapa) > self.etapas.index(self.etapa):
            self.etapa, self.fase, self.avisos_na_fase = etapa, "", 0
        self.detalhe = f"{plataforma}: {_CONTAGEM.sub(lambda m: f'{m[1]} de {m[2]}', resto)}"
        if etapa != self.etapa:  # aviso atrasado de uma etapa que já passou
            return
        fase = next((f for f in FASES[etapa] if f[0] in resto), None)
        if fase is None:
            return
        if fase[0] != self.fase:
            self.fase, self.avisos_na_fase = fase[0], 0
        self.avisos_na_fase += 1
        contagem = _CONTAGEM.search(resto)
        if contagem and int(contagem[2]):
            dentro = (int(contagem[1]) - 1) / int(contagem[2])  # o aviso anuncia o item que vai começar
        else:
            dentro = min(self.avisos_na_fase / 8, 0.9)  # sem contagem: avança um pouco a cada aviso
        na_etapa = fase[1] + (fase[2] - fase[1]) * max(0.0, min(dentro, 1.0))
        antes = sum(self.pesos[e] for e in self.etapas[: self.etapas.index(etapa)])
        self.fracao = max(self.fracao, (antes + self.pesos[etapa] * na_etapa) / (sum(self.pesos.values()) or 1))

    def segundos_restantes(self) -> float:
        decorrido = self.agora() - self.inicio
        if self.fracao >= 0.03 and decorrido >= 15:
            return decorrido * (1 - self.fracao) / self.fracao
        return max(self.estimativa - decorrido, 60)

    def texto_do_restante(self) -> str:
        minutos = self.segundos_restantes() / 60
        if minutos < 1:
            return "falta menos de 1 minuto"
        texto = pipeline.texto_da_estimativa(max(1, round(minutos)))
        return ("faltam " if not texto.endswith(("1 minuto", "1 hora")) else "falta ") + texto

    def retrato(self) -> dict:
        """O que a tela mostra: a fração, a etapa, o texto do momento e a situação de cada etapa."""
        posicao = self.etapas.index(self.etapa)
        return {
            "fracao": min(self.fracao, 0.99),
            "etapa": posicao + 1, "etapas": len(self.etapas), "nome": NOMES[self.etapa], "detalhe": self.detalhe,
            "restante": self.texto_do_restante(), "parando": self.parar.is_set(),
            "situacoes": [(NOMES[e], "concluída" if i < posicao else "em andamento" if i == posicao else "na fila")
                          for i, e in enumerate(self.etapas)],
        }


class Tarefa:
    """Uma coleta rodando em segundo plano. Guarda o resultado, o erro ou o fato de ter sido interrompida."""

    def __init__(self, funcao, andamento: Andamento, **dados):
        self.andamento, self.dados = andamento, dados
        self.resultado, self.erro, self.interrompida = None, None, False
        self.linha = threading.Thread(target=self._rodar, args=(funcao,), daemon=True)  # fechar o app encerra a coleta
        self.linha.start()

    def _rodar(self, funcao):
        try:
            self.resultado = funcao(self.andamento)
        except Interrompida:
            self.interrompida = True
        except Exception as e:  # navegador que não abriu, rede fora do ar: a tela mostra e deixa tentar de novo
            self.erro = e

    @property
    def viva(self) -> bool:
        return self.linha.is_alive()

    def pedir_parada(self) -> None:
        self.andamento.parar.set()


# Uma coleta por vez, e ela não é da aba do navegador: recarregar a página reencontra a coleta em curso.
_ATUAL: Tarefa | None = None


def atual() -> Tarefa | None:
    return _ATUAL


def iniciar(funcao, andamento: Andamento, **dados) -> Tarefa:
    global _ATUAL
    if _ATUAL is not None and _ATUAL.viva:
        return _ATUAL
    _ATUAL = Tarefa(funcao, andamento, **dados)
    return _ATUAL


def encerrar() -> None:
    """Esquece a coleta terminada, depois que a tela já pegou o resultado."""
    global _ATUAL
    if _ATUAL is not None and not _ATUAL.viva:
        _ATUAL = None
