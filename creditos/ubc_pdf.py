"""Leitura do "Relatório Analítico de Obras" da UBC em PDF (o mesmo relatório que `ubc.py` lê já em planilha).

Layout: no topo, "UBC: <código> ECAD: <código> TITULAR: <nome>". Cada obra é uma linha (código UBC, código ECAD,
ISWC, título, data, nacional, situação) seguida do seu próprio cabeçalho de titulares e de uma linha por titular
(código UBC, código ECAD, nome, território, categoria, data do contrato, participação, link). No fim, "Total de
registros: N". Diferente do relatório do ECAD, o título vem centralizado na coluna: por isso a linha da obra é
separada pelo formato de cada pedaço (códigos à esquerda, ISWC, data, SIM/NÃO, situação) e não só pela posição.

O arquivo é sempre importado à mão pelo usuário: o app nunca acessa o portal da UBC para baixar relatório.
"""

import re
import unicodedata

from cantor.matching import normalizar

from .ecad import RelatorioIlegivel, _conferir
from .modelo import Obra, Relatorio, Titular
from .ubc import _SITUACOES

TOLERANCIA = 4  # pontos: na linha do titular, o dado começa alinhado com o rótulo da coluna
_ISWC = re.compile(r"T-?\d{3}\.?\d{3}\.?\d{3}-?\d$")
_DATA = re.compile(r"\d{2}/\d{2}/\d{4}$")
_PERCENTUAL = re.compile(r"\d+(?:[.,]\d+)?%$")
_NACIONAL = {"SIM", "NAO"}
# A UBC escreve a categoria por extenso; o resto do app usa as siglas do ECAD (ver CATEGORIAS_DE_AUTOR).
_CATEGORIAS = {
    "COMPOSITOR/AUTOR": "CA", "AUTOR/COMPOSITOR": "CA", "COMPOSITOR": "C", "AUTOR": "A", "VERSIONISTA": "V",
    "ADAPTADOR": "AD", "ARRANJADOR": "AR", "TRADUTOR": "T", "EDITOR": "E", "SUBEDITOR": "SE", "SUB-EDITOR": "SE",
    "ADMINISTRADOR": "AM",
}
_COLUNAS_DO_TITULAR = {"UBC": "codigo_ubc", "ECAD": "codigo", "TITULAR": "nome", "TERRITORIO": "territorio",
                       "CATEGORIA": "categoria", "DT.": "contrato", "PARTIC.": "percentual", "LINK": "link"}


def _rotulo(texto: str) -> str:
    """Rótulo de coluna sem acento e em maiúsculas: "TÍTULO" e "TITULO" valem o mesmo."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sem_acento if not unicodedata.combining(c)).upper()


def e_relatorio_da_ubc(linhas) -> bool:
    """O cabeçalho das obras começa por "UBC ECAD ISWC": é o relatório da UBC, não o do ECAD."""
    return any([_rotulo(t) for _, t in linha[:3]] == ["UBC", "ECAD", "ISWC"] for linha in linhas)


def _percentual(texto: str):
    numeros = (texto or "").rstrip("%").replace(",", ".")
    try:
        return float(numeros)
    except ValueError:
        return None


def _codigos(palavras, x_ubc: float, x_ecad: float) -> dict:
    """{"ubc": ..., "ecad": ...}: cada código vai para a coluna cujo rótulo está mais perto."""
    codigos = {}
    for x, texto in palavras:
        codigos.setdefault("ubc" if abs(x - x_ubc) <= abs(x - x_ecad) else "ecad", texto)
    return codigos


def _obra(linha, colunas: dict) -> Obra | None:
    """A linha da obra, pedaço por pedaço: códigos, ISWC, título, data, SIM/NÃO e situação."""
    palavras = list(linha)
    limite_dos_codigos = colunas["ISWC"] - 2 * TOLERANCIA
    codigos = []
    while palavras and palavras[0][1].isdigit() and palavras[0][0] < limite_dos_codigos:
        codigos.append(palavras.pop(0))
    iswc = palavras.pop(0)[1] if palavras and _ISWC.match(palavras[0][1]) else ""
    meio_da_situacao = (colunas["NACIONAL"] + colunas["SITUACAO"]) / 2 if "SITUACAO" in colunas else 1e9
    situacao = []
    while palavras and palavras[-1][0] >= meio_da_situacao and not _DATA.match(palavras[-1][1]):
        situacao.insert(0, palavras.pop()[1])
    nacional = palavras.pop()[1] if palavras and normalizar(palavras[-1][1]).upper() in _NACIONAL else ""
    data = palavras.pop()[1] if palavras and _DATA.match(palavras[-1][1]) else ""
    titulo = " ".join(t for _, t in palavras)
    if not codigos or not titulo:
        return None
    codigo = _codigos(codigos, colunas["UBC"], colunas["ECAD"])
    situacao = " ".join(situacao)
    return Obra(
        codigo=codigo.get("ecad", ""), iswc=iswc, titulo=titulo, associacao="UBC",
        situacao=_SITUACOES.get(normalizar(situacao), situacao.upper()), nacional=nacional, inclusao=data,
        # o código UBC fica guardado só para o aviso de obra sem código ECAD (ver interpretar)
        tipo=f"UBC {codigo.get('ubc', '')}".strip(),
    )


def _titular(linha, colunas: list[tuple[float, str]]) -> Titular | None:
    campos = {}
    for x, texto in linha:
        campo = next(nome for inicio, nome in reversed(colunas) if x + TOLERANCIA >= inicio)
        campos[campo] = f"{campos[campo]} {texto}" if campo in campos else texto
    categoria = campos.get("categoria", "")
    if not categoria:
        return None
    return Titular(
        codigo=campos.get("codigo", "") or campos.get("codigo_ubc", ""), nome=campos.get("nome", ""),
        categoria=_CATEGORIAS.get(categoria.upper(), categoria), percentual=_percentual(campos.get("percentual", "")),
        contrato=campos.get("contrato", ""), link=campos.get("link", ""),
    )


def interpretar(linhas) -> Relatorio:
    relatorio = Relatorio(origem="UBC")
    colunas_obra = colunas_titular = None
    obra, ignoradas, sem_ecad, categorias_estranhas = None, 0, [], set()
    for linha in linhas:
        textos = [t for _, t in linha]
        rotulos = [_rotulo(t) for t in textos]
        junto = " ".join(textos)
        if rotulos[:3] == ["UBC", "ECAD", "ISWC"]:
            colunas_obra = {}
            for x, rotulo in zip((x for x, _ in linha), rotulos):
                colunas_obra.setdefault(rotulo, x)
            continue
        if rotulos[:3] == ["UBC", "ECAD", "TITULAR"]:
            achadas = {}
            for x, rotulo in zip((x for x, _ in linha), rotulos):
                if rotulo in _COLUNAS_DO_TITULAR:
                    achadas.setdefault(_COLUNAS_DO_TITULAR[rotulo], x)
            colunas_titular = sorted((x, campo) for campo, x in achadas.items())
            colunas_titular[0] = (0.0, colunas_titular[0][1])  # o código pode começar um pouco antes do rótulo
            continue
        cabecalho = re.match(r"UBC:\s*(\d*)\s*ECAD:\s*(\d*)\s*TITULAR:\s*(.+)$", junto)
        if cabecalho:
            relatorio.codigo_titular, relatorio.nome_titular = cabecalho.group(2), cabecalho.group(3).strip()
            continue
        total = re.search(r"Total de registros:\s*(\d+)", junto, re.IGNORECASE)
        if total:
            relatorio.total_declarado = int(total.group(1))
            continue
        emissao = re.search(r"Emissão:\s*(\d{2}/\d{2}/\d{4})", junto)
        if emissao:
            relatorio.emitido_em = emissao.group(1)
            continue
        if not textos[0].isdigit():
            continue
        if any(_PERCENTUAL.match(t) for t in textos):
            titular = _titular(linha, colunas_titular) if colunas_titular else None
            if obra is None or titular is None:
                ignoradas += 1
                continue
            if titular.categoria not in _CATEGORIAS.values():
                categorias_estranhas.add(titular.categoria)
            obra.titulares.append(titular)
            continue
        nova = _obra(linha, colunas_obra) if colunas_obra else None
        if nova is None:
            ignoradas += 1
            continue
        if not nova.codigo:
            sem_ecad.append(nova)
            nova.codigo = nova.tipo  # sem código ECAD, vale o da UBC: obras sem código seriam tratadas como uma só
        nova.tipo = ""
        obra = nova
        relatorio.obras.append(obra)
    if not relatorio.obras:
        raise RelatorioIlegivel("nenhuma obra reconhecida no relatório da UBC")
    _conferir(relatorio, ignoradas)
    relatorio.avisos += [f'obra "{o.titulo}" sem código ECAD no relatório: usado o código da UBC ({o.codigo})'
                         for o in sem_ecad]
    relatorio.avisos += [f'categoria de titular não reconhecida: "{c}" (não foi contada como autor)'
                         for c in sorted(categorias_estranhas)]
    return relatorio
