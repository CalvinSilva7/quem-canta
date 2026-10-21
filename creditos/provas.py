"""O que vai para a petição: a lista de gravações sem crédito de uma plataforma e o PDF das provas.

Só entra na lista o que foi verificado e não está marcado para revisão. O que
depende de decisão do advogado (vínculo inferido, nome desconhecido, título
aproximado) sai em uma lista à parte, "a confirmar", e nunca na contagem.
"""

import io
from xml.sax.saxutils import escape
import json
from pathlib import Path

from cantor.matching import normalizar

from . import classificador as c
from .captura import sha256

_MINUSCULAS = {"a", "o", "e", "de", "da", "do", "das", "dos", "em", "na", "no", "para", "pra", "por", "com", "que", "se", "um", "uma"}


def titulo_de_obra(titulo: str) -> str:
    """"A DANCA DA PANELEIRA" -> "A Danca da Paneleira" (o relatório vem em maiúsculas e sem acento)."""
    palavras = str(titulo or "").lower().split()
    return " ".join(p if i and p in _MINUSCULAS else p.capitalize() for i, p in enumerate(palavras))


def itens_da_peticao(coletas, plataforma: str) -> tuple[list[dict], list[dict]]:
    """(lista firme, lista a confirmar) das gravações sem crédito ou com crédito errado na plataforma.

    Na lista a confirmar entram também o título com grafia diferente da do cadastro e o medley, quando o print foi
    tirado: a pessoa precisa conferir, e a prova já está guardada. Sem print (a homônima provável, que o app nem
    abre), o caso fica só na planilha.

    Cada item: {"n", "obra", "link", "interprete", "status", "fundamento", "provas", "gravacao"}.
    """
    firmes, a_confirmar = [], []
    for coleta in coletas:
        if coleta.plataforma != plataforma:
            continue
        for g in coleta.gravacoes:
            duvida_com_print = g.classificacao.status in (c.TITULO_APROXIMADO, c.MEDLEY) and bool(g.provas)
            if g.classificacao.status not in c.NEGATIVOS and not duvida_com_print:
                continue
            item = {"obra": titulo_de_obra(g.obra), "link": g.link, "interprete": g.interprete,
                    "status": g.classificacao.status, "fundamento": g.classificacao.fundamento, "provas": g.provas, "gravacao": g}
            (a_confirmar if g.classificacao.revisar or duvida_com_print else firmes).append(item)
    for lista in (firmes, a_confirmar):
        lista.sort(key=lambda i: (normalizar(i["obra"]), normalizar(i["interprete"]), i["link"]))
        for n, item in enumerate(lista, start=1):
            item["n"] = n
    return firmes, a_confirmar


def linha_da_peticao(item: dict) -> str:
    return f"{item['n']} - Música: {item['obra']} {item['link']} Interpretada por: {item['interprete']}"


def _link_clicavel(paragrafo, endereco: str):
    """Põe no parágrafo o endereço escrito por extenso, azul e sublinhado, que abre ao clicar."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE

    elo = OxmlElement("w:hyperlink")
    elo.set(qn("r:id"), paragrafo.part.relate_to(endereco, RELATIONSHIP_TYPE.HYPERLINK, is_external=True))
    trecho, formato = OxmlElement("w:r"), OxmlElement("w:rPr")
    cor, traco = OxmlElement("w:color"), OxmlElement("w:u")
    cor.set(qn("w:val"), "0563C1")
    traco.set(qn("w:val"), "single")
    formato.append(cor)
    formato.append(traco)
    texto = OxmlElement("w:t")
    texto.text = endereco
    trecho.append(formato)
    trecho.append(texto)
    elo.append(trecho)
    paragrafo._p.append(elo)


def tabela_da_lista(doc, itens, marca: str = "", com_motivo: bool = False):
    """A lista no formato da petição do escritório: uma linha por gravação, com "N-Música: TÍTULO" e "Interpretada
    por: Fulano" à esquerda e o link clicável à direita."""
    from docx.shared import Cm

    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    tabela = doc.add_table(rows=0, cols=2)
    tabela.style = "Table Grid"
    # As bordas escritas na própria tabela: nem todo programa que abre o arquivo conhece o estilo "Table Grid".
    bordas = OxmlElement("w:tblBorders")
    for lado in ("top", "left", "bottom", "right", "insideH", "insideV"):
        borda = OxmlElement(f"w:{lado}")
        for chave, valor in (("val", "single"), ("sz", "6"), ("space", "0"), ("color", "000000")):
            borda.set(qn(f"w:{chave}"), valor)
        bordas.append(borda)
    # O Word exige as propriedades da tabela numa ordem fixa: as bordas vêm antes de "tblLook". Fora de ordem, o Word
    # do Windows recusa o arquivo.
    propriedades = tabela._tbl.tblPr
    depois = next((filho for filho in propriedades if filho.tag in {qn(f"w:{t}") for t in ("shd", "tblLayout", "tblCellMar", "tblLook")}), None)
    if depois is not None:
        depois.addprevious(bordas)
    else:
        propriedades.append(bordas)
    for item in itens:
        esquerda, direita = tabela.add_row().cells
        esquerda.width, direita.width = Cm(6.5), Cm(10)
        linha = esquerda.paragraphs[0]
        linha.add_run(f"{marca}{item['n']}-Música: ")
        linha.add_run(str(item["obra"]).upper()).bold = True
        esquerda.add_paragraph("")
        esquerda.add_paragraph(f"Interpretada por: {item['interprete']}")
        _link_clicavel(direita.paragraphs[0], item["link"])
        if com_motivo:
            direita.add_paragraph(f"{item['status']}: {item['fundamento']}")
        for paragrafo in [*esquerda.paragraphs, *direita.paragraphs]:
            paragrafo.paragraph_format.space_after = Pt(0)
    return tabela


def docx_da_lista(relatorio, coletas, plataforma: str, nome_da_plataforma: str = "") -> bytes:
    """Documento simples só com a lista e as contagens, para colar na petição.

    Quando o escritório fornecer o modelo da peça, esta função passa a preencher o modelo.
    """
    from docx import Document

    firmes, a_confirmar = itens_da_peticao(coletas, plataforma)
    nome = nome_da_plataforma or plataforma.title()
    doc = Document()
    doc.add_heading(f"Obras sem menção ao compositor - {nome}", level=1)
    doc.add_paragraph(" - ".join(x for x in (relatorio.nome_titular, relatorio.pseudonimo_titular) if x))
    doc.add_paragraph(
        f"Há {len(firmes)} obras intelectuais sendo disponibilizadas na plataforma ({nome}) sem menção correta "
        "do nome do compositor. São elas:"
    )
    tabela_da_lista(doc, firmes)
    if a_confirmar:
        doc.add_heading("A confirmar antes de usar (não entram na contagem acima)", level=2)
        doc.add_paragraph(
            "Estas gravações dependem de conferência humana: o motivo está abaixo do link de cada uma e na aba "
            '"Gravações" da planilha.'
        )
        tabela_da_lista(doc, a_confirmar, marca="C", com_motivo=True)
    saida = io.BytesIO()
    doc.save(saida)
    return saida.getvalue()


def registro_da_captura(pasta, nome_base: str) -> dict:
    """O JSON da captura, com "integra": a imagem em disco ainda tem o hash registrado na hora?

    "imagem" diz qual arquivo vai para o PDF: o print de tela inteira, quando existe, ou a captura de página.
    """
    pasta = Path(pasta)
    arquivo = pasta / f"{nome_base}.json"
    if not arquivo.exists():
        return {}
    registro = json.loads(arquivo.read_text(encoding="utf-8"))
    registro["imagem"] = "tela" if "tela" in registro["arquivos"] else "png"
    imagem = pasta / registro["arquivos"][registro["imagem"]]
    registro["integra"] = imagem.exists() and sha256(imagem.read_bytes()) == registro["sha256"][registro["imagem"]]
    return registro


def paginas_de_prova(itens, plataforma: str, pasta, avisos: list, marca: str = "") -> list[tuple]:
    """As páginas de print de uma lista, na ordem: [(item, registro, parte, número)]. `marca` vai antes do número
    ("C3": a confirmar). Captura ausente ou com hash diferente do registrado não entra: vira aviso em `avisos`."""
    paginas = []
    for item in itens:
        numero = f"{marca}{item['n']}"
        registros = [r for r in (registro_da_captura(pasta, p) for p in item["provas"]) if r]
        registros = registros if plataforma in TODOS_OS_PRINTS else registros[:1]
        if not registros:
            avisos.append(f"{numero} ({item['obra']} / {item['interprete']}): sem captura de tela")
        for i, registro in enumerate(registros or [{}], start=1):
            parte = f" ({i} de {len(registros)})" if len(registros) > 1 else ""
            if registro and not registro["integra"]:
                avisos.append(f"{numero}{parte} ({item['obra']} / {item['interprete']}): a imagem foi alterada depois da captura (hash não confere)")
                registro = {}
            if registro:
                paginas.append((item, registro, parte, numero))
                if registro.get("tela_inteira", {}).get("erro"):
                    avisos.append(f"{numero}{parte} ({item['obra']} / {item['interprete']}): sem print de tela inteira, porque "
                                  f"{registro['tela_inteira']['erro']}; o PDF traz a captura de página")
    return paginas


def clipes_de(coletas, plataforma: str) -> list[dict]:
    """Os clipes que a coleta achou nesta plataforma (Tidal), para o print à mão."""
    return [k for coleta in coletas if coleta.plataforma == plataforma for k in getattr(coleta, "clipes", [])]


TEXTO_A_CONFIRMAR = ("Estas gravações não entram na contagem da lista da petição: dependem de conferência humana. Os prints "
                     "seguem a ordem da parte \"A confirmar\" da lista da petição (C1, C2...), onde está o motivo de cada uma.")
TITULO_DOS_CLIPES = "VÍDEOS COM CLIPE"
TEXTO_DOS_CLIPES = ("Estas músicas têm clipe no Tidal. Quando há clipe, a prova é o clipe: abra cada link no Google Chrome, toque o "
                    "vídeo, abra a aba \"Créditos\" e tire o print à mão. O clipe que junta várias músicas (medley) fica a "
                    "confirmar.")
PARTES_DOS_CLIPES = (
    (True, "Clipes do intérprete da planilha conferida: tirar o print"),
    (False, "Clipes de outro artista: conferir antes se é a mesma música (pode ser regravação ou outra música com o mesmo nome)"),
)


def linhas_dos_clipes(clipes: list[dict]) -> list[tuple[str, list[tuple[int, dict, str]]]]:
    """Os clipes nas duas partes da lista, numerados em sequência: [(título da parte, [(n, clipe, texto da linha)])]."""
    partes, n = [], 0
    for do_conferido, titulo in PARTES_DOS_CLIPES:
        linhas = []
        for clipe in (k for k in clipes if bool(k.get("do_conferido", True)) == do_conferido):
            n += 1
            texto = f" - Clipe: {clipe['titulo']} - {', '.join(clipe['artistas'])}" + (" (medley: a confirmar)" if clipe["medley"] else "")
            if not do_conferido:
                texto += f" - intérprete na planilha: {', '.join(clipe.get('conferidos') or []) or '(nenhum)'}"
            linhas.append((n, clipe, texto))
        if linhas:
            partes.append((titulo, linhas))
    return partes


def docx_de_provas(relatorio, coletas, plataforma: str, pasta, nome_da_plataforma: str = "") -> tuple[bytes, list[str]]:
    """As mesmas páginas do PDF de provas, em Word, para quem precisa editar: folha deitada, um print por página, só o
    print (sem número, música ou intérprete), e a parte "A confirmar antes de usar" no fim.

    O Word pode ser alterado por quem o abre; a prova íntegra continua sendo o print guardado na pasta do caso, com o
    SHA-256 no .json ao lado. Devolve (docx, avisos), com os mesmos avisos do PDF.
    """
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_BREAK
    from docx.shared import Cm
    from PIL import Image as Imagem

    firmes, a_confirmar = itens_da_peticao(coletas, plataforma)
    nome, pasta, avisos = nome_da_plataforma or plataforma.title(), Path(pasta), []
    doc = Document()
    secao = doc.sections[0]
    secao.orientation = WD_ORIENT.LANDSCAPE
    secao.page_width, secao.page_height = Cm(29.7), Cm(21.0)
    for lado in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(secao, lado, Cm(1.2))
    doc.core_properties.title = f"Provas - {nome}"
    paginas_escritas = 0

    def nova_pagina():
        nonlocal paginas_escritas
        if paginas_escritas:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        paginas_escritas += 1

    def pagina_do_print(item, registro, parte, numero):
        # Só o print: sem número, música ou intérprete em cima (pedido do escritório, 21/10/2026). A ordem das páginas
        # é a da lista da petição; o que é cada print está na lista e na planilha.
        nova_pagina()
        arquivo = pasta / registro["arquivos"][registro["imagem"]]
        with Imagem.open(arquivo) as imagem:
            largura, altura = imagem.size
        escala = min(27.3 / largura, 18.0 / altura)  # o espaço da folha deitada, tirando as margens
        doc.add_paragraph().add_run().add_picture(str(arquivo), width=Cm(largura * escala))

    for pagina in paginas_de_prova(firmes, plataforma, pasta, avisos):
        pagina_do_print(*pagina)
    if a_confirmar:
        nova_pagina()
        doc.add_heading("A confirmar antes de usar", level=1)
        doc.add_paragraph(TEXTO_A_CONFIRMAR)
        for pagina in paginas_de_prova(a_confirmar, plataforma, pasta, avisos, marca="C"):
            pagina_do_print(*pagina)
    clipes = clipes_de(coletas, plataforma)
    if clipes:
        nova_pagina()
        doc.add_heading(TITULO_DOS_CLIPES, level=1)
        doc.add_paragraph(TEXTO_DOS_CLIPES)
        for titulo_da_parte, linhas in linhas_dos_clipes(clipes):
            doc.add_heading(titulo_da_parte, level=2)
            for n, clipe, texto in linhas:
                linha = doc.add_paragraph()
                linha.add_run(f"{n} - Música: {clipe['obra']}").bold = True
                linha.add_run(texto)
                _link_clicavel(doc.add_paragraph(), clipe["link"])
    if not paginas_escritas:
        doc.add_paragraph(f"Provas - {nome}: nenhum print disponível.")
    saida = io.BytesIO()
    doc.save(saida)
    return saida.getvalue(), avisos


def pdf_de_provas(relatorio, coletas, plataforma: str, pasta, nome_da_plataforma: str = "") -> tuple[bytes, list[str]]:
    """PDF só com os prints, um por página, na ordem da lista da petição: sem número, música ou intérprete.

    Duas partes: as gravações firmes (as da contagem da petição) e, depois de uma página que separa, "A confirmar
    antes de usar", com o print de cada gravação que depende de conferência (na ordem da lista C1, C2...).

    Cada música leva um print, o primeiro válido. No aplicativo da Amazon Music a prova são dois (o menu da faixa
    e a janela de créditos): lá entram todos, um por página, com o mesmo número.

    Devolve (pdf, avisos). Captura ausente ou com hash diferente do registrado não entra como prova: vira aviso.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

    firmes, a_confirmar = itens_da_peticao(coletas, plataforma)
    nome, pasta, avisos = nome_da_plataforma or plataforma.title(), Path(pasta), []
    estilos = getSampleStyleSheet()
    largura, altura = landscape(A4)
    saida = io.BytesIO()
    doc = SimpleDocTemplate(saida, pagesize=(largura, altura), leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=1.2 * cm, bottomMargin=1.2 * cm, title=f"Provas - {nome}")
    titular = " - ".join(x for x in (relatorio.nome_titular, relatorio.pseudonimo_titular) if x)

    def montar(itens, marca=""):
        return paginas_de_prova(itens, plataforma, pasta, avisos, marca)

    def pagina_do_print(item, registro, parte, numero):
        imagem = Image(str(pasta / registro["arquivos"][registro["imagem"]]))
        escala = min((largura - 2.4 * cm) / imagem.imageWidth, (altura - 2.6 * cm) / imagem.imageHeight)
        imagem.drawWidth, imagem.drawHeight = imagem.imageWidth * escala, imagem.imageHeight * escala
        return [imagem]

    # Só o print, um por página: sem número, música ou intérprete em cima (pedido do escritório, 21/10/2026). A ordem é
    # a da lista da petição (o Word do pacote), que traz o número, a música, o intérprete e o link de cada um; a data,
    # a hora e o SHA-256 ficam no arquivo .json que acompanha cada captura.
    fluxo = []
    for pagina in montar(firmes):
        fluxo += ([PageBreak()] if fluxo else []) + pagina_do_print(*pagina)
    if a_confirmar:
        # Segunda parte: o que depende de conferência humana. Fica fora da contagem da petição, mas com o print à mão.
        fluxo += ([PageBreak()] if fluxo else []) + [
            Paragraph("A confirmar antes de usar", estilos["Title"]),
            Paragraph(TEXTO_A_CONFIRMAR.replace("não entram na contagem", "<b>não entram na contagem</b>"), estilos["BodyText"])]
        for pagina in montar(a_confirmar, marca="C"):
            fluxo += [PageBreak()] + pagina_do_print(*pagina)
    clipes = clipes_de(coletas, plataforma)
    if clipes:
        fluxo += ([PageBreak()] if fluxo else []) + [Paragraph(TITULO_DOS_CLIPES, estilos["Title"]),
                                                      Paragraph(TEXTO_DOS_CLIPES, estilos["BodyText"])]
        for titulo_da_parte, linhas in linhas_dos_clipes(clipes):
            fluxo.append(Paragraph(escape(titulo_da_parte), estilos["Heading2"]))
            for n, clipe, texto in linhas:
                fluxo.append(Paragraph(f"<b>{n} - Música: {escape(clipe['obra'])}</b>{escape(texto)}"
                                       f"<br/><link href=\"{clipe['link']}\" color=\"blue\">{clipe['link']}</link>", estilos["BodyText"]))
    if not fluxo:
        fluxo = [Paragraph(f"Provas - {nome}: nenhum print disponível.", estilos["BodyText"])]
    doc.build(fluxo)
    return saida.getvalue(), avisos


# Plataformas em que a prova de cada música são todos os prints tirados, e não só o primeiro.
TODOS_OS_PRINTS = {"AMAZON", "PALCO MP3", "DEEZER"}
# Onde cada plataforma guarda as capturas (dentro da pasta do caso) e como ela se chama nos documentos.
PASTAS = {"YOUTUBE": "youtube", "SPOTIFY": "spotify", "TIDAL": "tidal", "DEEZER": "deezer", "VAGALUME": "vagalume",
          "APPLE MUSIC": "apple-music", "AMAZON - SITE": "amazon-site", "AMAZON": "amazon-app", "CLARO": "claro", "PALCO MP3": "palco-mp3"}
NOMES = {"YOUTUBE": "YouTube Music", "SPOTIFY": "Spotify", "TIDAL": "Tidal", "DEEZER": "Deezer", "VAGALUME": "Vagalume",
         "APPLE MUSIC": "Apple Music", "AMAZON - SITE": "Amazon Music (site)", "AMAZON": "Amazon Music (aplicativo)", "CLARO": "Claro Música", "PALCO MP3": "Palco MP3"}


def pacote(relatorio, coletas, pasta_do_caso, planilha: bytes) -> tuple[bytes, list[dict], list[str]]:
    """Um .zip com a planilha e, para cada plataforma com gravação firme ou a confirmar, a lista da petição e as provas
    em PDF e em Word (o Word, para quem precisa editar; as mesmas páginas do PDF).

    Devolve (zip, resumo por plataforma [{"plataforma", "firmes", "a_confirmar"}], avisos).
    """
    import zipfile

    saida, resumo, avisos = io.BytesIO(), [], []
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as arquivo:
        arquivo.writestr("Planilha de Obras.xlsx", planilha)
        for coleta in coletas:
            firmes, a_confirmar = itens_da_peticao(coletas, coleta.plataforma)
            resumo.append({"plataforma": NOMES.get(coleta.plataforma, coleta.plataforma), "firmes": len(firmes), "a_confirmar": len(a_confirmar)})
            clipes = clipes_de(coletas, coleta.plataforma)
            if not firmes and not a_confirmar and not clipes:
                continue
            nome = NOMES.get(coleta.plataforma, coleta.plataforma.title())
            if firmes or a_confirmar:  # só com clipes, não há lista da petição: só a lista dos clipes, nas provas
                arquivo.writestr(f"{nome}/Lista da petição - {nome}.docx", docx_da_lista(relatorio, coletas, coleta.plataforma, nome))
            if firmes or any(item["provas"] for item in a_confirmar) or clipes:  # o PDF sai também só com "a confirmar" ou só com clipes
                pasta = Path(pasta_do_caso) / PASTAS.get(coleta.plataforma, "")
                pdf, do_pdf = pdf_de_provas(relatorio, coletas, coleta.plataforma, pasta, nome)
                arquivo.writestr(f"{nome}/Provas - {nome}.pdf", pdf)
                arquivo.writestr(f"{nome}/Provas - {nome}.docx", docx_de_provas(relatorio, coletas, coleta.plataforma, pasta, nome)[0])
                avisos += [f"{nome}: {a}" for a in do_pdf]  # os do Word são os mesmos
    return saida.getvalue(), resumo, avisos
