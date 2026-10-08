"""Primeira etapa do escritório: quem gravou cada obra do titular.

A busca usa a Deezer e a Apple Music, que mostram o compositor de cada gravação sem precisar de navegador:
uma gravação entra quando o crédito traz um autor do relatório, ou quando o intérprete já está ligado ao
titular. O resultado é só a lista, obra por obra, numa planilha simples: ela vai para o compositor conferir
e corrigir antes de qualquer coleta de prints. Por isso não leva status, confiança nem link.
"""

import io

from cantor.matching import normalizar

from . import classificador as c
from . import pipeline

PLATAFORMAS = ("deezer", "apple")
# O que não é intérprete confirmado de obra nenhuma: homônimo de terceiro, leitura que falhou, faixa fora do relatório.
_FORA = (c.HOMONIMA, c.ERRO_TECNICO, c.INDISPONIVEL, c.FORA_DO_REPERTORIO)


def _chave(nome: str) -> str:
    return " ".join(normalizar(str(nome or "").replace("&", " e ")).split())


def por_obra(relatorio, coletas) -> list[tuple[str, list[str]]]:
    """[(título, [intérpretes])], na ordem do relatório, um título por linha. Obra sem gravação vem com a lista vazia.

    Os intérpretes de cada obra saem do mais gravado para o menos; o mesmo nome escrito com "e" e com "&" conta uma vez.
    """
    achados = {}  # título -> {chave do nome: [nome como aparece, quantas gravações]}
    for coleta in coletas:
        for g in coleta.gravacoes:
            if g.classificacao.status in _FORA or not str(g.interprete or "").strip():
                continue
            for titulo in g.obra.split("; "):
                item = achados.setdefault(titulo, {}).setdefault(_chave(g.interprete), [g.interprete.strip(), 0])
                item[1] += 1
    saida = []
    for titulo in dict.fromkeys(o.titulo for o in relatorio.obras):
        nomes = sorted(achados.get(titulo, {}).values(), key=lambda item: (-item[1], normalizar(item[0])))
        saida.append((titulo, [nome for nome, _ in nomes]))
    return saida


def buscar(relatorio, config, pasta, nomes_dos_coautores=(), ao_avancar=None, limite=None) -> list[tuple[str, list[str]]]:
    """Roda a busca (Deezer e Apple Music, sem navegador e sem prints) e devolve a lista de `por_obra`."""
    coletas = pipeline.executar(relatorio, config, pasta, nomes_dos_coautores, plataformas=PLATAFORMAS, prints=False,
                                ao_avancar=ao_avancar, limite_youtube=limite)
    return por_obra(relatorio, coletas)


def planilha(relatorio, lista) -> bytes:
    """A planilha de intérpretes: o nome do titular, o cabeçalho e uma linha por (obra, intérprete).

    Obra sem intérprete encontrado sai numa linha só, com a segunda coluna em branco, para o compositor preencher.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    livro = Workbook()
    ws = livro.active
    ws.title = "Intérpretes"
    titulo = Font(name="Bookman Old Style", bold=True, color="FFFFFF", size=10.5)
    corpo = Font(name="Verdana", size=9.5)
    ws.append([(relatorio.pseudonimo_titular or relatorio.nome_titular or "").upper()])
    ws.append(["Obras", "Intérpretes"])
    for linha in ws.iter_rows(min_row=1, max_row=2, max_col=2):
        for celula in linha:
            celula.font, celula.fill = titulo, PatternFill("solid", fgColor="1B3A6B")
            celula.alignment = Alignment(horizontal="center")
    for obra, nomes in lista:
        for nome in nomes or [""]:
            ws.append([obra, nome.upper() if nome else None])
    for linha in ws.iter_rows(min_row=3, max_col=2):
        for celula in linha:
            celula.font = corpo
    ws.freeze_panes = "A3"
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 46, 40
    saida = io.BytesIO()
    livro.save(saida)
    return saida.getvalue()


def ler_planilha(conteudo: bytes, relatorio) -> tuple[dict, list[str]]:
    """Lê a planilha de intérpretes (a nossa, ou a que o compositor devolveu corrigida).

    Devolve ({título do relatório: [intérpretes]}, [obras da planilha que não estão no relatório]). Vale qualquer
    planilha com a obra na primeira coluna e o intérprete na segunda; linhas de título e de cabeçalho são ignoradas.
    """
    from openpyxl import load_workbook

    por_titulo = {normalizar(o.titulo): o.titulo for o in relatorio.obras}
    pares, fora = {}, []
    for obra, interprete, *_ in load_workbook(io.BytesIO(conteudo), read_only=True).worksheets[0].iter_rows(min_col=1, max_col=3, values_only=True):
        obra, interprete = str(obra or "").strip(), str(interprete or "").strip()
        if not obra or not interprete or normalizar(obra) in ("obras", "obra"):
            continue
        titulo = por_titulo.get(normalizar(obra))
        if titulo is None:
            fora.append(obra)
        elif _chave(interprete) not in {_chave(n) for n in pares.setdefault(titulo, [])}:
            pares[titulo].append(interprete)
    return pares, list(dict.fromkeys(fora))
