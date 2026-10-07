"""Leitura do "Relatório analítico de titular autoral e suas obras" do ECAD (PDF).

O relatório é uma tabela de largura fixa: cada obra ocupa uma linha, seguida de
uma linha por titular. Nome e pseudônimo são texto livre lado a lado, então só
dá para separar um do outro pela posição horizontal. Por isso o parser trabalha
com "linhas de palavras com posição" ([(x, texto), ...]) e descobre as colunas
pelo cabeçalho de cada página, sem depender de medidas fixas.

O arquivo é sempre importado à mão pelo usuário: o app nunca acessa o ECADNET.

Uso:  python -m creditos.ecad relatorio.pdf --output obras.json
"""

import argparse
import json
import re
import sys

from .modelo import SITUACOES, Obra, Relatorio, Titular

TOLERANCIA = 4  # pontos: o dado pode começar um pouco antes do rótulo da coluna
ENTRE_LINHAS = 3  # palavras com até essa diferença de altura estão na mesma linha
_COLUNAS_DA_OBRA = {"ISWC": "iswc", "TÍTULO": "titulo", "ASS.": "associacao", "SITUAÇÃO*": "situacao",
                    "TIPO": "tipo", "NACIONAL": "nacional", "INCLUSÃO": "inclusao"}
_COLUNAS_DO_TITULAR = {"NOME": "nome", "PSEUDÔNIMO": "pseudonimo", "CAE": "cae", "ASSOCIAÇÃO": "associacao",
                       "CAT": "categoria", "(%)": "percentual", "INÍCIO": "contrato", "LINK": "link"}
_DATA = re.compile(r"\d{2}/\d{2}/\d{4}$")
_CABECALHO_DO_TITULAR = re.compile(r"TITULAR:\s*(\d+)\s+(.*?)\s*PSEUDÔNIMO:\s*(.*?)\s*CATEGORIA:")


class RelatorioIlegivel(Exception):
    """O arquivo não tem o formato esperado. Nunca devolver um relatório pela metade como se fosse inteiro."""


def linhas_do_pdf(caminho) -> list[list[tuple[float, str]]]:
    """Palavras de cada linha do PDF, com a posição horizontal: [[(x, texto), ...], ...]."""
    import pdfplumber  # só é preciso para ler o PDF; o resto do módulo funciona sem ele

    linhas = []
    with pdfplumber.open(caminho) as pdf:
        for pagina in pdf.pages:
            palavras = sorted(pagina.extract_words(x_tolerance=1.5, y_tolerance=2), key=lambda p: (p["top"], p["x0"]))
            atual, altura = [], None
            for p in palavras:
                if altura is not None and p["top"] - altura > ENTRE_LINHAS:
                    linhas.append(sorted(atual))
                    atual = []
                if not atual:
                    altura = p["top"]
                atual.append((p["x0"], p["text"]))
            if atual:
                linhas.append(sorted(atual))
    return linhas


def _colunas(linha, rotulos: dict) -> list[tuple[float, str]]:
    """[(x inicial, campo)] a partir da linha de cabeçalho; o código ocupa o que vem antes da primeira coluna."""
    achadas = {}
    for x, texto in linha:
        if texto in rotulos:
            achadas.setdefault(rotulos[texto], x)
    return [(0.0, "codigo")] + sorted((x, campo) for campo, x in achadas.items())


def _separar(linha, colunas) -> dict:
    campos = {}
    for x, texto in linha:
        campo = next(nome for inicio, nome in reversed(colunas) if x + TOLERANCIA >= inicio)
        campos[campo] = f"{campos[campo]} {texto}" if campo in campos else texto
    return campos


def _e_situacao(texto: str) -> bool:
    """LB, DU... ou uma combinação delas, como "BL/DU"."""
    partes = (texto or "").split("/")
    return bool(texto) and all(p in SITUACOES for p in partes)


def _percentual(texto: str):
    numeros = re.sub(r"[^\d,]", "", texto or "").rstrip(",")  # "100," é como o PDF corta "100,00"
    return float(numeros.replace(",", ".")) if numeros else None


def interpretar(linhas) -> Relatorio:
    """Monta o relatório a partir das linhas de palavras com posição."""
    relatorio = Relatorio(origem="ECAD")
    colunas_obra = colunas_titular = None
    obra, situacao_solta, ignoradas = None, "", 0
    for linha in linhas:
        textos = [t for _, t in linha]
        junto = " ".join(textos)
        if textos[0] == "CÓD." and "ISWC" in textos:
            colunas_obra = _colunas(linha, _COLUNAS_DA_OBRA)
            continue
        if textos[0] == "CÓDIGO" and "CAE" in textos:
            colunas_titular = _colunas(linha, _COLUNAS_DO_TITULAR)
            continue
        cabecalho = _CABECALHO_DO_TITULAR.search(junto)
        if cabecalho:
            relatorio.codigo_titular, relatorio.nome_titular, relatorio.pseudonimo_titular = cabecalho.groups()
            continue
        total = re.search(r"TOTAL DE OBRAS DO TITULAR:\s*(\d+)", junto)
        if total:
            relatorio.total_declarado = int(total.group(1))
            continue
        emissao = re.match(r"(\d{2}/\d{2}/\d{4}) - (\d{2}:\d{2})", junto)
        if emissao:
            relatorio.emitido_em = " ".join(emissao.groups())
            continue
        if len(textos) == 1 and _e_situacao(textos[0]):
            # A situação às vezes sai um pouco acima ou abaixo da linha da obra e vira uma linha só dela.
            if obra is not None and not obra.situacao and not obra.titulares:
                obra.situacao = textos[0]
            else:
                situacao_solta = textos[0]
            continue
        if not textos[0].isdigit() or colunas_obra is None or colunas_titular is None:
            if textos[0].isdigit():
                ignoradas += 1
            continue
        if _DATA.match(textos[-1]) and linha[-1][0] + TOLERANCIA >= dict((c, x) for x, c in colunas_obra)["inclusao"]:
            campos = _separar(linha, colunas_obra)
            obra = Obra(
                codigo=campos.get("codigo", ""),
                iswc=campos.get("iswc", "") if re.search(r"\d", campos.get("iswc", "")) else "",
                titulo=campos.get("titulo", ""),
                associacao=campos.get("associacao", ""),
                situacao=campos.get("situacao", "") or situacao_solta,
                tipo=campos.get("tipo", ""),
                nacional=campos.get("nacional", ""),
                inclusao=campos.get("inclusao", ""),
            )
            situacao_solta = ""
            relatorio.obras.append(obra)
            continue
        campos = _separar(linha, colunas_titular)
        if obra is None or not campos.get("categoria"):
            ignoradas += 1
            continue
        obra.titulares.append(Titular(
            codigo=campos.get("codigo", ""),
            nome=campos.get("nome", ""),
            pseudonimo=campos.get("pseudonimo", ""),
            cae=campos.get("cae", ""),
            associacao=campos.get("associacao", ""),
            categoria=campos.get("categoria", ""),
            percentual=_percentual(campos.get("percentual", "")),
            contrato=campos.get("contrato", ""),
            link=campos.get("link", ""),
        ))
    _conferir(relatorio, ignoradas)
    return relatorio


def _conferir(relatorio: Relatorio, ignoradas: int):
    """Guardas: o que não bate vira aviso visível, nunca silêncio."""
    if not relatorio.obras:
        raise RelatorioIlegivel("nenhuma obra reconhecida: o arquivo não parece um relatório analítico do ECAD")
    if relatorio.total_declarado is None:
        relatorio.avisos.append("o relatório não traz a linha TOTAL DE OBRAS DO TITULAR; a contagem não pôde ser conferida")
    elif relatorio.total_declarado != len(relatorio.obras):
        relatorio.avisos.append(
            f"CONTAGEM_DIVERGENTE: o relatório declara {relatorio.total_declarado} obras e foram lidas {len(relatorio.obras)}"
        )
    if ignoradas:
        relatorio.avisos.append(f"{ignoradas} linha(s) com código não foram reconhecidas como obra nem como titular")
    for obra in relatorio.obras:
        if not obra.titulares:
            relatorio.avisos.append(f'obra {obra.codigo} ("{obra.titulo}") sem nenhum titular lido')
        elif not any(relatorio.e_o_titular(t) for t in obra.titulares):
            relatorio.avisos.append(f'obra {obra.codigo} ("{obra.titulo}"): o titular do relatório não está entre os titulares')
        if not _e_situacao(obra.situacao):
            relatorio.avisos.append(f'obra {obra.codigo} ("{obra.titulo}") com situação não reconhecida: "{obra.situacao}"')


def ler_pdf(caminho) -> Relatorio:
    return interpretar(linhas_do_pdf(caminho))


def main():
    ap = argparse.ArgumentParser(description="Converte o relatório analítico do ECAD (PDF) em JSON.")
    ap.add_argument("arquivo")
    ap.add_argument("--output", help="arquivo JSON de saída (contém dados sensíveis: guarde só no escritório)")
    args = ap.parse_args()
    relatorio = ler_pdf(args.arquivo)
    com_coautoria = sum(len(o.autores) > 1 for o in relatorio.obras)
    print(f"{len(relatorio.obras)} obras lidas (o relatório declara {relatorio.total_declarado}); "
          f"{com_coautoria} com coautoria; {len(relatorio.duplicidades())} grupo(s) de possível duplicidade")
    for aviso in relatorio.avisos:
        print("AVISO:", aviso, file=sys.stderr)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as saida:
            json.dump(relatorio.para_dict(), saida, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
