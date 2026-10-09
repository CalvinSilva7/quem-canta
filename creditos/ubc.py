"""Relatório de obras em planilha (o "Relatório Analítico de Obras" da UBC já convertido em colunas).

Colunas reconhecidas pelo cabeçalho: título, compositor (o titular), coautores,
pseudônimo, ISWC, código ECAD, data de cadastro, situação e editores. Só o
título é obrigatório. A aba de duplicidades do arquivo não é lida: as possíveis
duplicidades são apontadas pelo próprio app (ver Relatorio.duplicidades).
"""

from collections import Counter

import pandas as pd

from cantor.matching import dividir_compositores, normalizar

from .modelo import Obra, Relatorio, Titular

_SINONIMOS = {
    "titulo": ["titulo", "titulo da obra", "obra", "musica"],
    "compositor": ["compositor", "titular", "autor"],
    "coautores": ["coautores", "coautor", "parceiros", "demais autores"],
    "pseudonimo": ["pseudonimo", "nome artistico"],
    "iswc": ["iswc"],
    "codigo": ["cod ecad", "codigo ecad", "codigo da obra", "cod obra"],
    "inclusao": ["data cadastro", "data de cadastro", "data de inclusao", "inclusao"],
    "situacao": ["situacao"],
    "editores": ["editores", "editor", "editoras", "editora"],
}


# A planilha traz a situação por extenso; o resto do app usa as siglas do ECAD.
_SITUACOES = {"liberada": "LB", "liberado": "LB", "bloqueada": "BL", "duplicidade": "DU", "em duplicidade": "DU",
              "homonima": "HO", "dominio publico": "DP", "composta": "CO", "em conflito": "EC"}


def _mapear(df: pd.DataFrame) -> dict:
    cabecalhos = {normalizar(c): c for c in df.columns}
    return {papel: next((cabecalhos[s] for s in nomes if s in cabecalhos), None) for papel, nomes in _SINONIMOS.items()}


def ler_planilha(df: pd.DataFrame) -> Relatorio:
    mapa = _mapear(df)
    if not mapa["titulo"]:
        raise ValueError(f"a planilha não tem coluna de título. Colunas: {', '.join(map(str, df.columns))}")

    def celula(linha, papel):
        coluna = mapa[papel]
        valor = linha[coluna] if coluna else ""
        return "" if valor is None or valor != valor else str(valor).strip()

    relatorio = Relatorio(origem="UBC")
    donos = Counter(celula(l, "compositor") for _, l in df.iterrows() if celula(l, "compositor"))
    if donos:
        relatorio.nome_titular = donos.most_common(1)[0][0]
    for numero, (_, linha) in enumerate(df.iterrows(), start=1):
        if not celula(linha, "titulo"):
            continue
        obra = Obra(
            # sem código na planilha, vale o número da linha: obras com o código em branco seriam tratadas como uma só
            codigo=celula(linha, "codigo") or f"linha-{numero}", iswc=celula(linha, "iswc"), titulo=celula(linha, "titulo"),
            situacao=_SITUACOES.get(normalizar(celula(linha, "situacao")), celula(linha, "situacao").upper()),
            inclusao=celula(linha, "inclusao"),
        )
        if celula(linha, "compositor"):
            obra.titulares.append(Titular(nome=celula(linha, "compositor"), pseudonimo=celula(linha, "pseudonimo"), categoria="CA"))
        obra.titulares += [Titular(nome=n, categoria="CA") for n in dividir_compositores(celula(linha, "coautores"))]
        obra.titulares += [Titular(nome=n, categoria="E") for n in dividir_compositores(celula(linha, "editores"))]
        relatorio.obras.append(obra)
    pseudonimos = Counter(
        t.pseudonimo for o in relatorio.obras for t in o.titulares if t.pseudonimo and relatorio.e_o_titular(t)
    )
    if pseudonimos:
        relatorio.pseudonimo_titular = pseudonimos.most_common(1)[0][0]
    relatorio.total_declarado = None
    if not relatorio.nome_titular:
        relatorio.avisos.append("a planilha não tem coluna de compositor: não dá para saber quem é o titular")
    return relatorio
