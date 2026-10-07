"""Filtro de saída: nenhum dado sensível do relatório pode ir em requisição externa.

Título, nomes de autores e pseudônimos podem ser pesquisados. Percentuais,
CAE/IPI, códigos do cadastro, datas de contrato e de inclusão, editoras e
documentos pessoais (CPF, RG) ficam só no app.
"""

import re

from cantor.matching import normalizar

_CPF = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
_CAE = re.compile(r"(?<!\d)\d{5}\.\d{2}\.\d{2}\.\d{2}(?!\d)")
_PERCENTUAL = re.compile(r"\d\s?%")


class DadoSensivel(Exception):
    """A requisição levaria um dado que não pode sair do app."""


class FiltroDeSaida:
    def __init__(self, termos=(), liberados=()):
        """`termos`: valores sensíveis do relatório. `liberados`: títulos e nomes de autores, que podem
        coincidir com um termo sensível (editora com o nome do próprio autor) e mesmo assim podem sair."""
        livres = {normalizar(l) for l in liberados if normalizar(l)}
        self.codigos = {t for t in termos if re.fullmatch(r"[\d./,-]+", t) and len(re.sub(r"\D", "", t)) >= 4}
        self.nomes = {normalizar(t) for t in termos if t not in self.codigos and len(normalizar(t)) >= 4} - livres
        self.livres = livres
        self.bloqueadas = 0

    def motivo(self, texto: str) -> str:
        """Por que o texto não pode sair, ou "" se pode."""
        texto = str(texto)
        if _CPF.search(texto) or _CAE.search(texto):
            return "documento ou CAE/IPI"
        if _PERCENTUAL.search(texto):
            return "percentual"
        if any(re.search(rf"(?<![\w.,/-]){re.escape(c)}(?![\w.,/-])", texto) for c in self.codigos):
            return "código, data ou percentual do cadastro"
        normal = f" {normalizar(texto)} "
        for nome in self.nomes:
            if f" {nome} " in normal and not any(nome in livre and f" {livre} " in normal for livre in self.livres):
                return "editora ou administradora"
        return ""

    def conferir(self, *partes):
        """Levanta DadoSensivel se alguma parte da requisição (URL, parâmetros, corpo) tiver dado sensível."""
        for parte in partes:
            motivo = self.motivo(parte)
            if motivo:
                self.bloqueadas += 1
                raise DadoSensivel(motivo)
