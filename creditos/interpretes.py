"""Primeira etapa do escritório: quem gravou cada obra do titular.

A busca usa a Deezer e a Apple Music, que mostram o compositor de cada gravação sem precisar de navegador:
uma gravação entra quando o crédito traz um autor do relatório, ou quando o intérprete já está ligado ao
titular. O resultado é só a lista, obra por obra, numa planilha simples: ela vai para o compositor conferir
e corrigir antes de qualquer coleta de prints. Por isso não leva status nem link.

A única marca é a cor: em preto, o intérprete de que o app tem certeza; em vermelho, o que ele só supõe.
Há certeza quando alguma gravação daquele intérprete traz o próprio titular no crédito, ou traz outro autor
da obra sem ressalva nenhuma. Quando a ligação é só presumida, ou pede revisão (crédito de um coautor que
pode ser homônimo, medley, título parecido), o nome sai em vermelho.
"""

import io

from cantor.matching import normalizar

from . import classificador as c
from . import pipeline

PLATAFORMAS = ("deezer", "apple")
VERMELHO = "C00000"
# O que não é intérprete confirmado de obra nenhuma: homônimo de terceiro, leitura que falhou, faixa fora do relatório.
_FORA = (c.HOMONIMA, c.ERRO_TECNICO, c.INDISPONIVEL, c.FORA_DO_REPERTORIO)


def _chave(nome: str) -> str:
    return " ".join(normalizar(str(nome or "").replace("&", " e ")).split())


def _da_certeza(g) -> bool:
    """Esta gravação prova que o intérprete gravou a obra do titular?"""
    k = g.classificacao
    if k.status == c.OK:  # o próprio titular está no crédito
        return True
    return k.status in c.NEGATIVOS and not k.revisar and bool(k.autores_reconhecidos)  # outro autor da obra, sem ressalva


def por_obra(relatorio, coletas) -> list[tuple[str, list[str], set[str]]]:
    """[(título, [intérpretes], {intérpretes sem certeza})], na ordem do relatório, um título por linha.

    Obra sem gravação vem com a lista vazia. Os intérpretes saem do mais gravado para o menos; o mesmo nome escrito
    com "e" e com "&" conta uma vez. O terceiro item diz quais nomes o app só supõe (ver `_da_certeza`).
    """
    achados = {}  # título -> {chave do nome: [nome como aparece, quantas gravações, há certeza?]}
    for coleta in coletas:
        for g in coleta.gravacoes:
            if g.classificacao.status in _FORA or not str(g.interprete or "").strip():
                continue
            for titulo in g.obra.split("; "):
                item = achados.setdefault(titulo, {}).setdefault(_chave(g.interprete), [g.interprete.strip(), 0, False])
                item[1] += 1
                item[2] = item[2] or (_da_certeza(g) and "; " not in g.obra)  # medley não dá certeza sobre nenhuma das obras
    saida = []
    for titulo in dict.fromkeys(o.titulo for o in relatorio.obras):
        nomes = sorted(achados.get(titulo, {}).values(), key=lambda item: (-item[1], normalizar(item[0])))
        saida.append((titulo, [nome for nome, _, _ in nomes], {nome for nome, _, certo in nomes if not certo}))
    return saida


def buscar(relatorio, config, pasta, nomes_dos_coautores=(), ao_avancar=None, limite=None) -> list[tuple[str, list[str], set[str]]]:
    """Roda a busca (Deezer e Apple Music, sem navegador e sem prints) e devolve a lista de `por_obra`."""
    coletas = pipeline.executar(relatorio, config, pasta, nomes_dos_coautores, plataformas=PLATAFORMAS, prints=False,
                                ao_avancar=ao_avancar, limite_youtube=limite)
    return por_obra(relatorio, coletas)


def planilha(relatorio, lista) -> bytes:
    """A planilha de intérpretes: o nome do titular, o cabeçalho e uma linha por (obra, intérprete).

    Obra sem intérprete encontrado sai numa linha só, com a segunda coluna em branco, para o compositor preencher.
    Intérprete de que o app não tem certeza sai em vermelho; os demais, em preto. `lista` aceita itens de dois
    campos (obra, intérpretes), todos em preto, ou de três, com o conjunto dos nomes sem certeza.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    livro = Workbook()
    ws = livro.active
    ws.title = "Intérpretes"
    titulo = Font(name="Bookman Old Style", bold=True, color="FFFFFF", size=10.5)
    corpo = Font(name="Verdana", size=9.5)
    em_duvida = Font(name="Verdana", size=9.5, color=VERMELHO)
    ws.append([(relatorio.pseudonimo_titular or relatorio.nome_titular or "").upper()])
    ws.append(["Obras", "Intérpretes"])
    for linha in ws.iter_rows(min_row=1, max_row=2, max_col=2):
        for celula in linha:
            celula.font, celula.fill = titulo, PatternFill("solid", fgColor="1B3A6B")
            celula.alignment = Alignment(horizontal="center")
    ha_duvida = False
    for obra, nomes, *resto in lista:
        duvidosos = resto[0] if resto else set()
        for nome in nomes or [""]:
            ws.append([obra, nome.upper() if nome else None])
            ws.cell(ws.max_row, 1).font = corpo
            ws.cell(ws.max_row, 2).font = em_duvida if nome in duvidosos else corpo
            ha_duvida = ha_duvida or nome in duvidosos
    if ha_duvida:
        ws.cell(1, 4, "Em vermelho: o app não tem certeza de que este intérprete gravou a obra. Confirme ou apague.").font = em_duvida
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
