"""O "Relatório Analítico de Obras" da UBC em PDF, montado como o pdfplumber entrega: palavras com a posição.

Todos os nomes, títulos e códigos daqui são inventados. As posições são as de um relatório de verdade.
"""

import pytest

from creditos import ubc_pdf
from creditos.ecad import RelatorioIlegivel
from tests.test_creditos import CABECALHO as CABECALHO_DO_ECAD


def _palavras(x, texto):
    saida = []
    for palavra in str(texto).split():
        saida.append((x, palavra))
        x += 5 * len(palavra) + 4
    return saida


TOPO = [
    _palavras(399, "Relatório Analítico de Obras"),
    _palavras(65, "UBC: 900001") + _palavras(239, "ECAD: 1000") + _palavras(407, "TITULAR: JOSE CARLOS SOUZA LIMA"),
]
CABECALHO_OBRAS = [(78.5, "UBC"), (117.7, "ECAD"), (165.3, "ISWC"), (264.5, "TITULO"), (295.2, "DA"), (308.0, "OBRA"),
                   (404.4, "DATA"), (440.6, "NACIONAL"), (502.2, "SITUACAO")]
CABECALHO_TITULARES = [(70.3, "UBC"), (115.4, "ECAD"), (160.0, "TITULAR"), (279.7, "TERRITÓRIO"), (337.7, "CATEGORIA"),
                       (428.5, "DT."), (439.4, "CONTRATO"), (494.9, "PARTIC."), (535.5, "LINK")]
RODAPE = [_palavras(22.5, "Data de Emissão: 18/09/2026") + _palavras(528, "Página 1 de 1")]


def obra(ubc, ecad, iswc, titulo, x_titulo, data="11/02/2022", situacao="LIBERADA"):
    linha = [(68.5, ubc), (112.4, ecad)] + ([(153.0, iswc)] if iswc else []) + _palavras(x_titulo, titulo)
    return linha + [(396.3, data), (454.5, "SIM")] + _palavras(491.3 if " " in situacao else 504.4, situacao)


def titular(ubc, ecad, nome, categoria, pct, contrato="", link="1"):
    linha = [(70.3, ubc), (115.4, ecad)] + _palavras(160.0, nome) + [(279.7, "BRAZIL"), (337.7, categoria)]
    return linha + ([(428.5, contrato)] if contrato else []) + [(494.9, pct), (535.5, link)]


def relatorio_inventado(total=3):
    return TOPO + [
        CABECALHO_OBRAS,
        obra("900011", "11", "T0000000011", "26 COISAS", 271.8),  # título começando por número
        CABECALHO_TITULARES,
        titular("900002", "2000", "MARIA APARECIDA DIAS", "COMPOSITOR/AUTOR", "37.50%"),
        titular("900001", "1000", "JOSE CARLOS SOUZA LIMA", "COMPOSITOR/AUTOR", "37.50%"),
        titular("900003", "3000", "JOSE CARLOS SOUZA LIMA", "EDITOR", "25.00%", "12/08/2021"),  # a editora dele
        obra("900012", "12", "", "COISA FEITA", 267.6, situacao="EM DUPLICIDADE"),  # sem ISWC
        CABECALHO_TITULARES,
        titular("900001", "1000", "JOSE CARLOS SOUZA LIMA", "COMPOSITOR/AUTOR", "50.00%"),
    ] + RODAPE + [
        CABECALHO_OBRAS,  # página nova: os titulares da obra anterior continuam sem cabeçalho próprio
        titular("900004", "4000", "EDITORA INVENTADA LTDA", "ADMINISTRADOR", "50.00%", "09/04/2025"),
        obra("900013", "13", "T0000000013", "LIGUE O RADIO", 255.4),
        CABECALHO_TITULARES,
        titular("900001", "1000", "JOSE CARLOS SOUZA LIMA", "COMPOSITOR/AUTOR", "100.00%"),
        _palavras(466, f"Total de registros: {total}"),
    ] + RODAPE


def test_reconhece_o_layout_da_ubc_e_nao_confunde_com_o_do_ecad():
    assert ubc_pdf.e_relatorio_da_ubc(relatorio_inventado())
    assert not ubc_pdf.e_relatorio_da_ubc(CABECALHO_DO_ECAD)


def test_le_obras_e_titulares_do_pdf_da_ubc():
    rel = ubc_pdf.interpretar(relatorio_inventado())
    assert (rel.origem, rel.codigo_titular, rel.nome_titular, rel.emitido_em) == ("UBC", "1000", "JOSE CARLOS SOUZA LIMA", "18/09/2026")
    assert rel.avisos == []
    assert [(o.codigo, o.iswc, o.titulo, o.situacao, o.inclusao) for o in rel.obras] == [
        ("11", "T0000000011", "26 COISAS", "LB", "11/02/2022"),
        ("12", "", "COISA FEITA", "DU", "11/02/2022"),
        ("13", "T0000000013", "LIGUE O RADIO", "LB", "11/02/2022"),
    ]
    coisas, feita, _ = rel.obras
    assert [(a.nome, a.percentual) for a in coisas.autores] == [("MARIA APARECIDA DIAS", 37.5), ("JOSE CARLOS SOUZA LIMA", 37.5)]
    editora = coisas.titulares[2]
    assert (editora.categoria, editora.contrato, editora.e_autor, rel.e_o_titular(editora)) == ("E", "12/08/2021", False, False)
    assert [(t.nome, t.categoria) for t in feita.titulares] == [("JOSE CARLOS SOUZA LIMA", "CA"), ("EDITORA INVENTADA LTDA", "AM")]


def test_contagem_que_nao_bate_e_arquivo_estranho():
    rel = ubc_pdf.interpretar(relatorio_inventado(total=4))
    assert any(a.startswith("CONTAGEM_DIVERGENTE") for a in rel.avisos)
    with pytest.raises(RelatorioIlegivel):
        ubc_pdf.interpretar(TOPO + [CABECALHO_OBRAS] + RODAPE)
