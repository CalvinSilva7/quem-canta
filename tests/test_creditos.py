"""Testes sem rede do fluxo de créditos: importação do relatório, filtro de saída e classificador.

Todos os nomes, títulos e códigos daqui são inventados.
"""

import pandas as pd
import pytest

from creditos import classificador as c
from creditos.classificador import Config, Item, classificar
from creditos.ecad import RelatorioIlegivel, interpretar
from creditos.filtro import DadoSensivel, FiltroDeSaida
from creditos.modelo import Obra, Relatorio, Titular
from creditos.ubc import ler_planilha

# --- relatório do ECAD montado como o PDF entrega: palavras com a posição horizontal ---

X_OBRA = {"codigo": 30, "iswc": 77, "titulo": 142, "ass": 305, "situacao": 367, "tipo": 425, "nacional": 489, "inclusao": 525}
X_TITULAR = {"codigo": 30, "nome": 80, "pseudonimo": 226, "cae": 308, "ass": 369, "cat": 430, "pct": 445, "contrato": 468, "link": 560}


def _palavras(x, texto):
    """Espalha as palavras de um campo a partir de x, como no PDF (cerca de 6 pontos por letra)."""
    saida = []
    for palavra in str(texto).split():
        saida.append((x, palavra))
        x += 5 * len(palavra) + 4
    return saida


def linha(posicoes, **campos):
    return sorted(p for campo, texto in campos.items() if texto != "" for p in _palavras(posicoes[campo], texto))


CABECALHO = [
    [(130, "RELATÓRIO"), (204, "ANALÍTICO")],
    _palavras(43, "TITULAR: 1000 JOSE CARLOS SOUZA LIMA") + _palavras(324, "PSEUDÔNIMO: ZECA LIMA") + _palavras(471, "CATEGORIA: TODAS"),
    [(30, "CÓD."), (51, "OBRA"), (77, "ISWC"), (139, "TÍTULO"), (171, "PRINCIPAL"), (305, "ASS."), (325, "RESPONS."),
     (367, "SITUAÇÃO*"), (418, "TIPO"), (441, "OBRA"), (474, "NACIONAL"), (525, "INCLUSÃO")],
    [(473, "CONTRATO")],
    [(30, "CÓDIGO"), (80, "NOME"), (104, "DO"), (118, "TITULAR"), (226, "PSEUDÔNIMO"), (308, "CAE"), (369, "ASSOCIAÇÃO"),
     (425, "CAT"), (445, "(%)"), (469, "INÍCIO"), (501, "/"), (509, "FIM"), (544, "LINK")],
]
DONO = dict(codigo="1000", nome="JOSE CARLOS SOUZA LIMA", pseudonimo="ZECA LIMA", cae="00111.22.33.44", ass="AAA", cat="CA", link="1")
PARCEIRA = dict(codigo="2000", nome="MARIA APARECIDA DIAS", pseudonimo="CIDA DIAS", cae="00555.66.77.88", ass="BBB", cat="CA", link="2")
EDITORA = dict(codigo="3000", nome="EDITORA INVENTADA LTDA", pseudonimo="INVENTADA", cae="00999.00.11.22", ass="CCC", cat="E",
               pct="10,00", contrato="05/03/21", link="3")


def obra(codigo, titulo, iswc="T-000.000.001-0", situacao="LB", inclusao="10/06/2005"):
    return linha(X_OBRA, codigo=codigo, iswc=iswc, titulo=titulo, ass="AAA", situacao=situacao, tipo="ORIGINAL", nacional="SIM", inclusao=inclusao)


def rodape(total):
    return [_palavras(357, f"TOTAL DE OBRAS DO TITULAR: {total}"), _palavras(28, "19/02/2026 - 14:04") + [(540, "Página"), (563, "1")]]


def relatorio_de_teste() -> Relatorio:
    linhas = CABECALHO + [
        obra("11", "LIGUE O RADIO"),
        linha(X_TITULAR, **DONO, pct="100,"),
        obra("12", "COISA FEITA", iswc="- . . -"),
        linha(X_TITULAR, **DONO, pct="50,00"),
        linha(X_TITULAR, **PARCEIRA, pct="40,00"),
        linha(X_TITULAR, **EDITORA),
        obra("13", "BAILE NA SERRA", situacao="BL/DU"),
        linha(X_TITULAR, **DONO, pct="100,"),
        obra("14", "BAILE NA SERRA", situacao="BL/DU"),
        linha(X_TITULAR, **{**DONO, "pseudonimo": "ZECA DO BAILE"}, pct="50,00"),
        linha(X_TITULAR, **{**PARCEIRA, "pseudonimo": ""}, pct="50,00"),
        obra("15", "A DANCA DA PANELA"),
        linha(X_TITULAR, **DONO, pct="100,"),
        obra("16", "DANCA DA PANELLA"),
        linha(X_TITULAR, **DONO, pct="100,"),
    ] + rodape(6)
    return interpretar(linhas)


@pytest.fixture
def rel():
    return relatorio_de_teste()


# --- importação -------------------------------------------------------------


def test_le_obras_titulares_e_separa_nome_de_pseudonimo_pela_coluna(rel):
    assert (rel.codigo_titular, rel.nome_titular, rel.pseudonimo_titular) == ("1000", "JOSE CARLOS SOUZA LIMA", "ZECA LIMA")
    assert (len(rel.obras), rel.total_declarado, rel.emitido_em, rel.avisos) == (6, 6, "19/02/2026 14:04", [])
    feita = rel.obras[1]
    assert (feita.codigo, feita.titulo, feita.iswc, feita.situacao, feita.inclusao) == ("12", "COISA FEITA", "", "LB", "10/06/2005")
    assert [(t.nome, t.pseudonimo, t.categoria, t.percentual) for t in feita.titulares] == [
        ("JOSE CARLOS SOUZA LIMA", "ZECA LIMA", "CA", 50.0),
        ("MARIA APARECIDA DIAS", "CIDA DIAS", "CA", 40.0),
        ("EDITORA INVENTADA LTDA", "INVENTADA", "E", 10.0),
    ]
    assert feita.titulares[2].contrato == "05/03/21" and [a.nome for a in feita.autores] == ["JOSE CARLOS SOUZA LIMA", "MARIA APARECIDA DIAS"]
    assert rel.obras[0].titulares[0].percentual == 100.0  # o PDF corta "100,00" em "100,"
    assert rel.obras[3].titulares[1].pseudonimo == ""  # pseudônimo em branco não puxa o CAE para o lugar dele
    assert rel.obras[2].situacoes == ["BL", "DU"]


def test_situacao_impressa_fora_da_linha_da_obra_e_reaproveitada():
    sem = [p for p in obra("11", "LIGUE O RADIO") if p[1] != "LB"]
    antes = interpretar(CABECALHO + [[(367, "LB")], sem, linha(X_TITULAR, **DONO, pct="100,")] + rodape(1))
    depois = interpretar(CABECALHO + [sem, [(367, "DU")], linha(X_TITULAR, **DONO, pct="100,")] + rodape(1))
    assert (antes.obras[0].situacao, depois.obras[0].situacao) == ("LB", "DU") and not antes.avisos


def test_contagem_que_nao_bate_vira_aviso_e_arquivo_estranho_e_recusado():
    faltando = interpretar(CABECALHO + [obra("11", "LIGUE O RADIO"), linha(X_TITULAR, **DONO, pct="100,")] + rodape(2))
    assert any(a.startswith("CONTAGEM_DIVERGENTE") for a in faltando.avisos)
    sem_titular = interpretar(CABECALHO + [obra("11", "LIGUE O RADIO")] + rodape(1))
    assert any("sem nenhum titular" in a for a in sem_titular.avisos)
    with pytest.raises(RelatorioIlegivel):
        interpretar([[(30, "qualquer"), (80, "coisa")]])


def test_pseudonimos_do_titular_e_dos_coautores_viram_nomes_artisticos(rel):
    nomes = rel.nomes_artisticos()
    assert [(n["nome"], n["titular"], n["obras"]) for n in nomes] == [
        ("ZECA LIMA", True, 5), ("ZECA DO BAILE", True, 1), ("CIDA DIAS", False, 1),
    ]
    assert all(n["nome"] != "INVENTADA" for n in nomes)  # pseudônimo de editora não é nome artístico
    assert rel.pseudonimos()["1000"] == ["ZECA LIMA", "ZECA DO BAILE"]


def test_sinaliza_titulos_iguais_ou_parecidos_e_situacao_de_duplicidade(rel):
    grupos = [(g["motivo"], [o.codigo for o in g["obras"]]) for g in rel.duplicidades()]
    assert grupos == [("titulo", ["13", "14"]), ("titulo", ["15", "16"])]
    sozinha = interpretar(CABECALHO + [obra("11", "LIGUE O RADIO", situacao="HO"), linha(X_TITULAR, **DONO, pct="100,")] + rodape(1))
    assert [(g["motivo"], g["obras"][0].codigo) for g in sozinha.duplicidades()] == [("situacao", "11")]


def test_relatorio_vai_e_volta_de_json(rel):
    assert Relatorio.de_dict(rel.para_dict()) == rel


def test_planilha_no_formato_da_ubc_vira_o_mesmo_modelo():
    df = pd.DataFrame({
        "titulo": ["Ligue o Rádio", "Coisa Feita", ""],
        "compositor": ["José Carlos Souza Lima"] * 3,
        "coautores": ["", "Maria Aparecida Dias, Outro Parceiro", ""],
        "iswc": ["T-000.000.001-0", "", ""],
        "cod_ecad": ["11", "12", ""],
        "data_cadastro": ["2005-06-10", "2006-01-02", ""],
        "situacao": ["LIBERADA", "EM DUPLICIDADE", ""],
        "editores": ["", "Editora Inventada Ltda", ""],
    })
    rel = ler_planilha(df)
    assert (rel.origem, rel.nome_titular, len(rel.obras)) == ("UBC", "José Carlos Souza Lima", 2)
    feita = rel.obras[1]
    assert (feita.codigo, feita.situacao, feita.inclusao) == ("12", "DU", "2006-01-02")
    assert [a.nome for a in feita.autores] == ["José Carlos Souza Lima", "Maria Aparecida Dias", "Outro Parceiro"]
    assert [t.nome for t in feita.titulares if not t.e_autor] == ["Editora Inventada Ltda"]
    with pytest.raises(ValueError):
        ler_planilha(pd.DataFrame({"compositor": ["x"]}))


# --- filtro de saída --------------------------------------------------------


def test_filtro_bloqueia_dado_sensivel_e_libera_titulo_e_autores(rel):
    liberados = [o.titulo for o in rel.obras] + [x for o in rel.obras for a in o.autores for x in (a.nome, a.pseudonimo)]
    filtro = FiltroDeSaida(rel.termos_sensiveis(), liberados)
    for texto in ["https://api.exemplo/search?q=LIGUE O RADIO ZECA LIMA", "q=coisa feita Maria Aparecida Dias", "isrc:BRXXX2100001"]:
        assert filtro.motivo(texto) == ""
    for texto in ["q=00111.22.33.44", "obra 12 codigo=3000", "q=Editora Inventada Ltda", "partes 50,00", "25 %", "cpf 123.456.789-09",
                  "inclusao=10/06/2005", "contrato 05/03/21"]:
        assert filtro.motivo(texto), texto
    with pytest.raises(DadoSensivel):
        filtro.conferir("https://api.exemplo/search", {"q": "LIGUE O RADIO"}, "cae=00111.22.33.44")
    assert filtro.bloqueadas == 1


def test_filtro_nao_confunde_codigo_curto_nem_numero_dentro_de_outro(rel):
    filtro = FiltroDeSaida(rel.termos_sensiveis())
    assert filtro.motivo("track/11") == "" and filtro.motivo("album/130001") == ""  # "11", "12"... são curtos demais para vigiar


# --- classificador: estado da coleta ---------------------------------------


def test_erro_tecnico_nunca_vira_sem_creditos(rel):
    assert classificar(Item(titulo="Ligue o Rádio", coleta="erro", erro="timeout"), rel).status == c.ERRO_TECNICO
    assert classificar(Item(titulo="Ligue o Rádio", coleta="indisponivel"), rel).status == c.INDISPONIVEL
    assert classificar(Item(titulo="Ligue o Rádio", coleta="qualquer coisa"), rel).status == c.ERRO_TECNICO


def test_link_que_abre_outro_interprete_e_link_divergente(rel):
    item = Item(titulo="Ligue o Rádio", interprete="Banda Estranha", interprete_esperado="Zeca Lima", creditos=[])
    r = classificar(item, rel)
    assert r.status == c.LINK_DIVERGENTE and r.revisar


# --- classificador: título --------------------------------------------------


def test_titulo_exato_ignora_caixa_acento_parenteses_e_sufixo_de_versao(rel):
    for titulo in ["Ligue o Rádio", "LIGUE O RADIO (Ao Vivo)", "Ligue o Rádio - Ao Vivo", "Ligue o Rádio - Remasterizado 2010"]:
        r = classificar(Item(titulo=titulo, interprete="Zeca Lima", creditos=["Zeca Lima"]), rel)
        assert (r.status, r.titulo, r.obras) == (c.OK, "exato", ["11"]), titulo


def test_titulo_fora_do_relatorio_nao_gera_acusacao(rel):
    r = classificar(Item(titulo="Outra Música Qualquer", interprete="Zeca Lima"), rel)
    assert r.status == c.FORA_DO_REPERTORIO and not r.revisar


def test_titulo_aproximado_nunca_sustenta_resultado_negativo(rel):
    # "Largue o Rádio" casa com "LIGUE O RADIO" por semelhança: pode ser outra música.
    sem = classificar(Item(titulo="Largue o Rádio", interprete="Zeca Lima"), rel)
    assert (sem.status, sem.sugestao, sem.revisar, sem.titulo) == (c.TITULO_APROXIMADO, c.SEM_CREDITOS, True, "aproximado")
    errado = classificar(Item(titulo="Coisa Feia", interprete="Zeca Lima", creditos=["Zeca Lima Banda"]), rel, Config(interpretes=["Zeca Lima Banda"]))
    assert (errado.status, errado.sugestao) == (c.TITULO_APROXIMADO, c.VIOLACAO)
    # Com o titular creditado, o título aproximado não atrapalha: o resultado é positivo.
    ok = classificar(Item(titulo="Largue o Rádio", interprete="Zeca Lima", creditos=["Zeca Lima"]), rel)
    assert ok.status == c.OK and "aproximado" in ok.fundamento


def test_medley_e_dividido_e_vai_para_revisao(rel):
    for titulo in ["Ligue o Rádio / Outra Música", "Pot-Pourri: Ligue o Rádio - Coisa Feita", "Medley Ligue o Rádio"]:
        r = classificar(Item(titulo=titulo, interprete="Zeca Lima", creditos=["Fulano"]), rel)
        assert (r.status, r.revisar) == (c.MEDLEY, True), titulo
        assert "LIGUE O RADIO" in r.fundamento
    assert classificar(Item(titulo="Uma / Outra", interprete="Zeca Lima"), rel).status == c.FORA_DO_REPERTORIO


# --- classificador: crédito exibido ----------------------------------------


@pytest.mark.parametrize("credito", ["Zeca Lima", "ZECA LIMA", "José Carlos Souza Lima", "José Lima", "Jose Souza Lima", "Zeca do Baile"])
def test_titular_creditado_pelo_nome_pseudonimo_ou_nome_abreviado_e_ok(rel, credito):
    r = classificar(Item(titulo="Ligue o Rádio", interprete="Banda Qualquer", creditos=[credito]), rel)
    assert (r.status, r.revisar) == (c.OK, False)


def test_sem_creditos_exige_tela_lida_e_vinculo_com_o_titular(rel):
    r = classificar(Item(titulo="Coisa Feita", interprete="Zeca Lima", creditos=[]), rel)
    assert (r.status, r.revisar, r.coautores_omitidos) == (c.SEM_CREDITOS, False, ["MARIA APARECIDA DIAS"])
    # Mesmo título, sem crédito, com um intérprete que ninguém ligou ao titular: pode ser música de outra pessoa.
    estranho = classificar(Item(titulo="Coisa Feita", interprete="Banda Estranha", creditos=[]), rel)
    assert (estranho.status, estranho.revisar) == (c.HOMONIMA, True)
    for item, config in [
        (Item(titulo="Coisa Feita", interprete="Banda Estranha", vinculo=True), Config()),
        (Item(titulo="Coisa Feita", interprete="Banda Estranha"), Config(interpretes=["Banda Estranha"])),
        (Item(titulo="Coisa Feita", interprete="Cida Dias"), Config()),  # a coautora cantando
    ]:
        assert classificar(item, rel, config).status == c.SEM_CREDITOS


def test_banda_ou_interprete_no_campo_de_autor_e_violacao_sem_pedir_revisao(rel):
    r = classificar(Item(titulo="Coisa Feita", interprete="Banda do Baile", creditos=["Banda do Baile"]), rel,
                    Config(interpretes=["Banda do Baile"]))
    assert (r.status, r.revisar) == (c.VIOLACAO, False)
    assert "pessoa física" in r.fundamento and r.coautores_omitidos == ["JOSE CARLOS SOUZA LIMA", "MARIA APARECIDA DIAS"]


def test_interprete_sem_vinculo_que_credita_a_si_mesmo_e_homonimo_e_nunca_violacao(rel):
    # Caso real da primeira rodada: um cantor famoso com uma música de mesmo título, composta por ele.
    r = classificar(Item(titulo="Ligue o Rádio", interprete="Cantor Famoso", creditos=["Cantor Famoso"]), rel)
    assert (r.status, r.revisar, r.vinculo) == (c.HOMONIMA, True, "")
    varios = classificar(Item(titulo="Ligue o Rádio", interprete="Cantor Famoso", creditos=["Cantor Famoso, Outro Autor"]), rel)
    assert varios.status == c.HOMONIMA


def test_vinculo_pelo_interprete_exige_o_nome_inteiro(rel):
    # "Zeca" não é "Zeca Lima", e "Maria Dias" (outra pessoa) não é a coautora "Maria Aparecida Dias".
    for interprete in ["Zeca", "Lima", "Cida"]:
        assert classificar(Item(titulo="Coisa Feita", interprete=interprete), rel).status == c.HOMONIMA, interprete
    r = classificar(Item(titulo="Coisa Feita", interprete="ZECA LIMA"), rel)
    assert r.status == c.SEM_CREDITOS and "Zeca Lima".upper() in r.vinculo.upper()


def test_nome_parecido_que_e_de_outro_autor_do_relatorio_nao_e_grafia():
    # Dois irmãos no relatório: a obra é só de um, e a plataforma credita o outro.
    irmao = Titular(codigo="1001", nome="JOAO CARLOS SOUZA LIMA", pseudonimo="JOAO LIMA", categoria="CA")
    dono = Titular(codigo="1000", nome="JOSE CARLOS SOUZA LIMA", pseudonimo="ZECA LIMA", categoria="CA")
    rel = Relatorio(codigo_titular="1000", nome_titular=dono.nome, pseudonimo_titular=dono.pseudonimo, obras=[
        Obra(codigo="1", titulo="SO DELE", titulares=[dono]),
        Obra(codigo="2", titulo="DOS DOIS", titulares=[dono, irmao]),
    ])
    r = classificar(Item(titulo="Só Dele", interprete="Banda do Baile", creditos=["João Carlos Souza Lima"]), rel)
    assert (r.status, r.revisar) == (c.VIOLACAO, True) and "outra pessoa" in r.fundamento
    assert r.coautores_omitidos == ["JOSE CARLOS SOUZA LIMA"] and r.vinculo == "autor do relatório no crédito exibido"
    # A grafia errada do próprio titular continua sendo divergência.
    assert classificar(Item(titulo="Só Dele", interprete="Zeca Lima", creditos=["José Carlos Sousa Lima"]), rel).status == c.GRAFIA


def test_nome_de_pessoa_desconhecido_e_violacao_mas_sempre_com_revisao(rel):
    r = classificar(Item(titulo="Ligue o Rádio", interprete="Zeca Lima", creditos=["Santino Prado"]), rel)
    assert (r.status, r.revisar) == (c.VIOLACAO, True) and "outro nome artístico" in r.fundamento
    # Sem vínculo nenhum com o titular, nem violação é: pode ser obra homônima.
    r = classificar(Item(titulo="Ligue o Rádio", interprete="Banda Estranha", creditos=["Santino Prado"]), rel)
    assert (r.status, r.revisar) == (c.HOMONIMA, True)


def test_so_o_coautor_creditado_e_violacao_contra_o_titular(rel):
    r = classificar(Item(titulo="Coisa Feita", interprete="Banda Estranha", creditos=["Cida Dias"]), rel)
    assert (r.status, r.revisar, r.coautores_omitidos) == (c.VIOLACAO, False, ["JOSE CARLOS SOUZA LIMA"])


def test_coautor_omitido_sai_na_nota_mesmo_quando_o_titular_esta_creditado(rel):
    r = classificar(Item(titulo="Coisa Feita", interprete="Zeca Lima", creditos=["Zeca Lima"]), rel)
    assert r.status == c.OK and r.coautores_omitidos == ["MARIA APARECIDA DIAS"] and "MARIA APARECIDA DIAS" in r.fundamento
    completo = classificar(Item(titulo="Coisa Feita", interprete="Zeca Lima", creditos=["Zeca Lima, Cida Dias"]), rel)
    assert completo.status == c.OK and completo.coautores_omitidos == []
    extra = classificar(Item(titulo="Coisa Feita", interprete="Zeca Lima", creditos=["Zeca Lima", "Cida Dias", "Santino Prado"]), rel)
    assert (extra.status, extra.revisar) == (c.OK, True) and "Santino Prado" in extra.fundamento


@pytest.mark.parametrize("credito", ["Zecca Lima", "Zeca Lyma", "José Carlos Sousa Lima"])
def test_grafia_errada_do_nome_ou_pseudonimo_e_divergencia(rel, credito):
    r = classificar(Item(titulo="Ligue o Rádio", interprete="Zeca Lima", creditos=[credito]), rel)
    assert (r.status, r.revisar) == (c.GRAFIA, True)


def test_nome_artistico_so_vale_como_ok_depois_de_confirmado(rel):
    item = Item(titulo="Ligue o Rádio", interprete="Zeca Lima", creditos=["Carlinhos do Acordeon"])
    assert classificar(item, rel).status == c.VIOLACAO  # desconhecido: violação com revisão
    a_confirmar = classificar(item, rel, Config(nomes_a_confirmar=["Carlinhos do Acordeon"]))
    assert (a_confirmar.status, a_confirmar.revisar) == (c.OK_VARIANTE, True)
    assert classificar(item, rel, Config(nomes_confirmados=["Carlinhos do Acordeon"])).status == c.OK
    # Combinação de pseudônimo com sobrenome ("Zeca Souza") identifica o autor, mas pede confirmação.
    combinado = classificar(Item(titulo="Ligue o Rádio", interprete="Zeca Lima", creditos=["Zeca Souza"]), rel)
    assert (combinado.status, combinado.revisar) == (c.OK_VARIANTE, True)


def test_registros_em_duplicidade_com_autorias_diferentes_sao_todos_considerados(rel):
    # "BAILE NA SERRA" tem dois registros: um só do titular, outro com a coautora.
    r = classificar(Item(titulo="Baile na Serra", interprete="Zeca Lima", creditos=["Maria Aparecida Dias"]), rel)
    assert (r.status, r.obras) == (c.VIOLACAO, ["13", "14"]) and "2 registros" in r.fundamento
    ok = classificar(Item(titulo="Baile na Serra", interprete="Zeca Lima", creditos=["Zeca do Baile"]), rel)
    assert ok.status == c.OK and ok.coautores_omitidos == ["MARIA APARECIDA DIAS"]


def test_titulo_do_relatorio_sem_o_titular_entre_os_autores_nao_acusa_ninguem():
    rel = Relatorio(codigo_titular="1000", nome_titular="JOSE CARLOS SOUZA LIMA", obras=[
        Obra(codigo="9", titulo="MUSICA ALHEIA", titulares=[Titular(codigo="2000", nome="MARIA APARECIDA DIAS", categoria="CA")]),
    ])
    assert classificar(Item(titulo="Música Alheia", interprete="Zeca Lima"), rel).status == c.HOMONIMA


# --- Deezer e pipeline, com a plataforma trocada por respostas fixas --------

import json as _json

from creditos import pipeline, saida
from creditos.deezer import Album, Deezer, ErroDeColeta, Faixa, interpretar_pagina


def pagina_de_album(album_id, faixas, declaradas=None, selo="Selo Inventado"):
    estado = {
        "DATA": {"ALB_ID": str(album_id), "ALB_TITLE": f"Álbum {album_id}", "ART_NAME": "Banda do Baile", "LABEL_NAME": selo,
                 "UPC": "1", "NUMBER_TRACK": str(len(faixas) if declaradas is None else declaradas), "ORIGINAL_RELEASE_DATE": "2010-05-01"},
        "SONGS": {"data": [
            {"SNG_ID": str(i), "SNG_TITLE": t, "VERSION": v, "ART_NAME": "Banda do Baile", "ISRC": f"BRXXX10000{i:02d}",
             "SNG_CONTRIBUTORS": papeis, "DURATION": "180"}
            for i, (t, v, papeis) in enumerate(faixas, start=1)
        ]},
    }
    return f"<html><script>window.__DZR_APP_STATE__ = {_json.dumps(estado)}</script><p>resto</p></html>"


def test_le_creditos_isrc_e_fornecedor_do_estado_da_pagina():
    album = interpretar_pagina(pagina_de_album(7, [
        ("Ligue o Rádio", "", {"composer": ["Zeca Lima"], "author": ["Zeca Lima", "Cida Dias"], "main_artist": ["Banda do Baile"]}),
        ("Coisa Feita", "(Ao Vivo)", []),  # sem contribuidores, a página manda uma lista vazia
    ]), "7")
    assert (album.fornecedor, album.lancamento, album.faixas_declaradas, album.aviso) == ("Selo Inventado", "2010-05-01", 2, "")
    um, dois = album.faixas
    assert (um.creditos, um.isrc, um.link) == (["Zeca Lima", "Cida Dias"], "BRXXX1000001", "https://www.deezer.com/track/1")
    assert (dois.titulo, dois.creditos) == ("Coisa Feita (Ao Vivo)", [])


def test_guardas_da_pagina_erro_nunca_vira_album_sem_creditos():
    with pytest.raises(ErroDeColeta, match="em vez do 8"):
        interpretar_pagina(pagina_de_album(7, []), "8")  # a página devolveu outro álbum
    with pytest.raises(ErroDeColeta, match="estado"):
        interpretar_pagina("<html>faça login</html>", "7")
    faltando = interpretar_pagina(pagina_de_album(7, [("Ligue o Rádio", "", {})], declaradas=2), "7")
    assert faltando.aviso.startswith("CONTAGEM_DIVERGENTE")


class DeezerFalso(Deezer):
    """Mesma classe, sem rede: discografias, buscas e álbuns vêm de dicionários."""

    def __init__(self, discografias=None, buscas=None, albuns=None, filtro=None):
        super().__init__(filtro=filtro)
        self.discografias, self.buscas, self.lidos = discografias or {}, buscas or {}, albuns or {}
        self.pedidos = []

    def artistas(self, nome, maximo=3):
        self.pedidos.append(("artista", nome))
        return [{"id": nome, "nome": nome, "albuns": 1, "fas": 1}] if nome in self.discografias else []

    def albuns_do_artista(self, artista_id):
        return self.discografias[artista_id]

    def buscar_faixas(self, consulta, maximo=300):
        self.pedidos.append(("busca", consulta))
        return self.buscas.get(consulta, [])

    def album(self, album_id):
        return self.lidos.get(album_id) or Album(id=album_id, erro="HTTP 503 após 3 tentativas")


def _album(album_id, *faixas, selo="Selo Inventado", lancamento="2010-05-01"):
    return Album(id=album_id, titulo=f"Álbum {album_id}", fornecedor=selo, lancamento=lancamento, faixas_declaradas=len(faixas),
                 faixas=[Faixa(id=f"{album_id}{i}", titulo=t, interprete=quem, creditos=list(cred), isrc=f"BR{album_id}{i}")
                         for i, (t, quem, cred) in enumerate(faixas)])


def _coletar(rel, deezer, **config):
    return pipeline.coletar_deezer(rel, Config(nomes_confirmados=["ZECA LIMA"], **config), deezer)


def test_pipeline_classifica_cada_gravacao_e_deduplica_por_link(rel):
    deezer = DeezerFalso(
        discografias={"ZECA LIMA": ["a1", "a2"]},
        albuns={
            "a1": _album("a1", ("Ligue o Rádio", "Zeca Lima", ["Zeca Lima"]), ("Coisa Feita", "Zeca Lima", []),
                         ("Música de Outro", "Zeca Lima", ["Outro"])),
            "a2": _album("a2", ("Ligue o Rádio", "Zeca Lima", []), selo="Agregador Inventado"),  # a mesma obra, outro cadastro
        },
    )
    coleta = _coletar(rel, deezer)
    assert [(g.obra, g.classificacao.status, g.fornecedor) for g in coleta.gravacoes] == [
        ("LIGUE O RADIO", c.OK, "Selo Inventado"), ("COISA FEITA", c.SEM_CREDITOS, "Selo Inventado"),
        ("LIGUE O RADIO", c.SEM_CREDITOS, "Agregador Inventado"),
    ]
    assert (coleta.fora_do_repertorio, coleta.albuns_lidos, coleta.albuns_com_erro) == (1, 2, [])
    assert all(g.isrc and g.link.startswith("https://www.deezer.com/track/") and g.coletado_em for g in coleta.gravacoes)


def test_album_que_nao_abre_vira_erro_no_resumo_e_nunca_gravacao_sem_credito(rel):
    coleta = _coletar(rel, DeezerFalso(discografias={"ZECA LIMA": ["a1"]}))
    assert coleta.gravacoes == [] and coleta.albuns_com_erro == [("https://www.deezer.com/br/album/a1", "HTTP 503 após 3 tentativas")]


def test_homonimo_de_terceiro_vai_para_pendencias_e_nao_entra_na_planilha(rel):
    achado = {"id": "x", "titulo": "Ligue o Rádio", "interprete": "Cantor Famoso", "album_id": "b1"}
    deezer = DeezerFalso(buscas={"LIGUE O RADIO": [achado]},
                         albuns={"b1": _album("b1", ("Ligue o Rádio", "Cantor Famoso", ["Cantor Famoso"]))})
    coleta = _coletar(rel, deezer)
    assert [(g.interprete, g.classificacao.status, g.origem) for g in coleta.gravacoes] == [
        ("Cantor Famoso", c.HOMONIMA, "busca por título (LIGUE O RADIO)")]
    linhas = saida.linhas_por_obra(rel, [coleta])
    assert all(l["interprete"] == "(nenhuma gravação encontrada)" for l in linhas)


def test_interprete_recorrente_ganha_vinculo_inferido_sempre_marcado_para_confirmar(rel):
    banda = "Banda do Baile"
    deezer = DeezerFalso(
        buscas={t: [{"id": t, "titulo": t, "interprete": banda, "album_id": "d1"}] for t in ["LIGUE O RADIO"]},
        albuns={"d1": _album("d1", ("Ligue o Rádio", banda, ["Zeca Lima"]), ("Coisa Feita", banda, ["Zeca Lima"]),
                             ("Baile na Serra", banda, []), ("A Dança da Panela", banda, []))},
    )
    coleta = _coletar(rel, deezer)
    assert coleta.interpretes_inferidos == [{"nome": banda, "obras": 4, "com_autor": 2}]
    sem = [g for g in coleta.gravacoes if g.classificacao.status == c.SEM_CREDITOS]
    assert len(sem) == 2 and all(g.classificacao.revisar and g.classificacao.presumido and "PRESUMIDO" in g.classificacao.fundamento for g in sem)
    # Com menos títulos, ou sem nenhum autor reconhecido, não há inferência: fica como possível homônimo.
    pouco = DeezerFalso(buscas=deezer.buscas, albuns={"d1": _album("d1", ("Ligue o Rádio", banda, ["Zeca Lima"]), ("Baile na Serra", banda, []))})
    assert [g.classificacao.status for g in _coletar(rel, pouco).gravacoes] == [c.OK, c.HOMONIMA]


def test_lancamento_muito_anterior_ao_cadastro_pede_revisao_sem_revelar_a_data(rel):
    deezer = DeezerFalso(discografias={"ZECA LIMA": ["a1"]},
                         albuns={"a1": _album("a1", ("Coisa Feita", "Zeca Lima", []), lancamento="1990-01-01")})
    (g,) = _coletar(rel, deezer).gravacoes
    assert g.classificacao.status == c.SEM_CREDITOS and g.classificacao.revisar
    assert "homônima" in g.classificacao.fundamento and "2005" not in g.classificacao.fundamento


def test_nenhuma_requisicao_leva_dado_sensivel(rel):
    config = Config(nomes_confirmados=["ZECA LIMA"])
    deezer = DeezerFalso(filtro=pipeline.filtro_do_relatorio(rel, config))
    pipeline.coletar_deezer(rel, config, deezer, nomes_dos_coautores=["CIDA DIAS"])
    assert ("artista", "CIDA DIAS") in deezer.pedidos and ("busca", "COISA FEITA ZECA LIMA") in deezer.pedidos
    sensiveis = rel.termos_sensiveis()
    assert not any(termo in consulta for _, consulta in deezer.pedidos for termo in sensiveis if len(termo) >= 4)
    assert all(deezer.filtro.motivo(consulta) == "" for _, consulta in deezer.pedidos)


def test_planilha_no_formato_do_escritorio(rel):
    import io
    from openpyxl import load_workbook

    deezer = DeezerFalso(discografias={"ZECA LIMA": ["a1", "a2"]}, albuns={
        "a1": _album("a1", ("Ligue o Rádio", "Zeca Lima", ["Zeca Lima"]), ("Coisa Feita", "Zeca Lima", [])),
        "a2": _album("a2", ("Coisa Feita", "Zeca Lima", [])),
    })
    coleta = _coletar(rel, deezer)
    wb = load_workbook(io.BytesIO(saida.planilha(rel, [coleta])))
    assert wb.sheetnames == ["Obras", "Gravações", "Pendências - homônimos", "Resumo"]
    ws = wb["Obras"]
    assert ws["A1"].value == "JOSE CARLOS SOUZA LIMA - ZECA LIMA" and ws["A1"].fill.fgColor.rgb.endswith("1B3A6B")
    assert [x.value for x in ws[2]] == ["Obras", "Intérpretes"] + saida.PLATAFORMAS
    deezer_col = saida.PLATAFORMAS.index("DEEZER") + 3
    por_obra = {ws.cell(r, 1).value: ws.cell(r, deezer_col).value for r in range(3, ws.max_row)}
    assert por_obra["LIGUE O RADIO"] == "tem créditos"
    assert por_obra["COISA FEITA"] == "https://www.deezer.com/track/a11 -- https://www.deezer.com/track/a20"
    assert por_obra["BAILE NA SERRA"] == "N/A"
    assert ws.cell(ws.max_row, deezer_col).value == "2 obras"
    assert ws.cell(3, saida.PLATAFORMAS.index("NAPSTER") + 3).value == "plataforma encerrada"
    assert ws.cell(3, saida.PLATAFORMAS.index("SPOTIFY") + 3).value == saida.NAO_COLETADO
    gravacoes = [[x.value for x in linha] for linha in wb["Gravações"].iter_rows(min_row=2)]
    assert len(gravacoes) == 3 and {g[10] for g in gravacoes} == {c.OK, c.SEM_CREDITOS}
    assert "Gravações do repertório: verificadas / coletadas: 3 / 3" in saida.resumo_em_texto(rel, [coleta])


# --- YouTube Music: interpretação do que a tela mostra (sem navegador) -------

from datetime import datetime
from zoneinfo import ZoneInfo

from creditos import captura, provas, ytmusic

MENU_SEM = ["Criar mix", "Tocar a seguir", "Adicionar à fila", "Salvar na playlist", "Ir para a página do artista", "Compartilhar", "Denunciar"]
MENU_COM = MENU_SEM[:5] + ["Mostrar créditos da música"] + MENU_SEM[5:]


def test_menu_sem_o_item_de_creditos_so_vale_se_o_menu_da_faixa_abriu_mesmo():
    assert ytmusic.interpretar_menu(MENU_SEM) == "sem_creditos"
    assert ytmusic.interpretar_menu(MENU_COM) == "com_creditos"
    assert ytmusic.interpretar_menu(["Ver créditos", "Compartilhar"]) == "com_creditos"
    # Menu que não abriu, ou outro menu da página (conta, configurações): nunca é "sem créditos".
    for itens in [None, [], ["Fazer login", "Configurações", "Termos de Serviço"]]:
        assert ytmusic.interpretar_menu(itens) == "invalido"


def test_janela_de_creditos_e_lida_por_secao_com_cada_nome_no_seu_elemento():
    secoes = [["rotulo", "Interpretação de"], ["nome", "Banda do Baile"], ["rotulo", "Composição de"], ["nome", "Zeca Lima"],
              ["nome", "Cida Dias"], ["rotulo", "Metadados de música fornecidos por"], ["nome", "Selo Inventado"]]
    por_rotulo = ytmusic.interpretar_janela(secoes)
    assert por_rotulo["Composição de"] == ["Zeca Lima", "Cida Dias"]
    assert ytmusic.autores_e_fornecedor(por_rotulo) == (["Zeca Lima", "Cida Dias"], "Selo Inventado")
    assert ytmusic.autores_e_fornecedor(ytmusic.interpretar_janela(secoes[:2])) == ([], "")  # janela sem "Composição de"


def test_linha_do_player_e_resultados_da_busca():
    assert ytmusic.interpretar_linha_do_player("Banda do Baile\n • \nÁlbum Tal\n • \n2012") == ("Banda do Baile", "Álbum Tal • 2012")
    itens = [
        {"titulo": "Ligue o Rádio", "colunas": ["Ligue o Rádio", "Banda do Baile • Álbum Tal • 3:09"], "link": "watch?v=AbC_123-xyz&list=RD", "artistas": ["Banda do Baile"]},
        {"titulo": "Um álbum", "colunas": ["Um álbum"], "link": "", "artistas": []},
    ]
    (cand,) = ytmusic.interpretar_resultados(itens, "Música")
    assert (cand.video, cand.titulo, cand.interprete, cand.detalhe, cand.tipo) == ("AbC_123-xyz", "Ligue o Rádio", "Banda do Baile", "Álbum Tal • 3:09", "Música")
    assert cand.link == "https://music.youtube.com/watch?v=AbC_123-xyz"


def test_captcha_login_e_consentimento_param_a_fila():
    assert ytmusic.sinal_de_bloqueio("https://www.google.com/sorry/index?continue=x")
    assert ytmusic.sinal_de_bloqueio("https://accounts.google.com/ServiceLogin")
    assert ytmusic.sinal_de_bloqueio("https://music.youtube.com/watch?v=x", "Faça login para confirmar que você não é um robô")
    assert ytmusic.sinal_de_bloqueio("https://music.youtube.com/watch?v=x", "Banda do Baile · Compartilhar") == ""


def test_nome_do_arquivo_e_carimbo_da_captura():
    quando = datetime(2026, 3, 2, 15, 56, 7, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert captura.nome_base("A Dança da Panela", "Banda do Baile", "YouTube Music", quando) == "a-danca-da-panela_banda-do-baile_youtube-music_2026-03-02_15h56"
    carimbo = captura.texto_do_carimbo("https://music.youtube.com/watch?v=x", quando, "abc", "Mon, 02 Mar 2026 18:56:07 GMT")
    assert "URL: https://music.youtube.com/watch?v=x" in carimbo and "02/03/2026 15:56:07 -0300 (America/Sao_Paulo)" in carimbo
    assert "Captura: abc" in carimbo and "HTTP Date): Mon, 02 Mar 2026 18:56:07 GMT" in carimbo


class YouTubeFalso:
    """Busca e leitura com respostas fixas, no lugar do navegador."""
    filtro, navegacoes = None, 0

    def __init__(self, buscas, leituras, bloquear_em=None):
        self.buscas, self.leituras, self.bloquear_em, self.lidos = buscas, leituras, bloquear_em, []

    def buscar(self, consulta, abas):
        return [ytmusic.Candidato(*c_) for c_ in self.buscas.get(consulta, [])]

    def ler(self, video, obra, esperado=None):
        if video == self.bloquear_em:
            raise ytmusic.Bloqueio("a plataforma desviou para www.google.com")
        self.lidos.append(video)
        return self.leituras[video]


def _leitura(video, titulo, interprete, menu, creditos=(), fornecedor=""):
    return ytmusic.Leitura(video=video, titulo=titulo, interprete=interprete, menu=menu, creditos=list(creditos),
                           tem_item_de_creditos=bool(creditos), fornecedor=fornecedor, provas=[f"prova-{video}"])


def _youtube(rel, ytm):
    return pipeline.coletar_youtube(rel, Config(nomes_confirmados=["ZECA LIMA"]), ytm, recorrentes=["Banda do Baile"])


def test_youtube_so_abre_faixa_de_quem_tem_ligacao_com_o_titular(rel):
    ytm = YouTubeFalso(
        buscas={"LIGUE O RADIO Banda do Baile": [
            ("v1", "Ligue o Rádio", "Banda do Baile", "Álbum • 3:00", "Música"),
            ("v2", "Banda do Baile - Ligue o Rádio", "Canal de Fã", "5 mil visualizações", "Vídeo"),
            ("v3", "Ligue o Rádio", "Cantor Famoso", "Outro Álbum", "Música"),  # homônimo: conta, mas não abre
            ("v4", "Outra Música", "Banda do Baile", "Álbum", "Música"),
        ]},
        leituras={"v1": _leitura("v1", "Ligue o Rádio", "Banda do Baile", MENU_COM, ["Zeca Lima"], "Selo Inventado"),
                  "v2": _leitura("v2", "Banda do Baile - Ligue o Rádio", "Canal de Fã", MENU_SEM)},
    )
    coleta = _youtube(rel, ytm)
    assert ytm.lidos == ["v1", "v2"] and coleta.sem_vinculo_nao_abertos == 1
    ok, sem = coleta.gravacoes
    assert (ok.classificacao.status, ok.fornecedor, ok.link) == (c.OK, "Selo Inventado", "https://music.youtube.com/watch?v=v1")
    assert (sem.classificacao.status, sem.interprete, sem.provas) == (c.SEM_CREDITOS, "Banda do Baile", ["prova-v2"])
    assert 'canal "Canal de Fã"' in sem.classificacao.fundamento and "título do vídeo" in sem.vinculo


def test_youtube_erro_de_leitura_e_pagina_trocada_nunca_viram_sem_creditos(rel):
    ytm = YouTubeFalso(
        buscas={"LIGUE O RADIO Banda do Baile": [("v1", "Ligue o Rádio", "Banda do Baile", "", "Música"), ("v2", "Ligue o Rádio", "Banda do Baile", "", "Música")]},
        leituras={"v1": ytmusic.Leitura(video="v1", coleta="erro", erro="o menu de ações da faixa não abriu"),
                  "v2": _leitura("v2", "Uma Música Completamente Diferente", "Banda do Baile", MENU_SEM)},
    )
    assert [g.classificacao.status for g in _youtube(rel, ytm).gravacoes] == [c.ERRO_TECNICO, c.ERRO_TECNICO]


def test_youtube_bloqueio_para_a_fila_guarda_o_que_ja_leu_e_avisa(rel):
    ytm = YouTubeFalso(
        buscas={"LIGUE O RADIO Banda do Baile": [("v1", "Ligue o Rádio", "Banda do Baile", "", "Música")],
                "COISA FEITA Banda do Baile": [("v2", "Coisa Feita", "Banda do Baile", "", "Música")]},
        leituras={"v1": _leitura("v1", "Ligue o Rádio", "Banda do Baile", MENU_SEM)}, bloquear_em="v2",
    )
    coleta = _youtube(rel, ytm)
    assert len(coleta.gravacoes) == 1 and coleta.interrompida.startswith("Bloqueio") and "PAROU" in coleta.avisos[-1]


# --- saídas para a petição ---------------------------------------------------


def _coleta_para_peticao(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    from PIL import Image as Pil

    def gravacao(n, obra, status, revisar=False, com_prova=True):
        base = f"prova-{n}"
        if com_prova:
            destino = io.BytesIO()
            Pil.new("RGB", (400, 220), (20 * n, 30, 60)).save(destino, "PNG")
            (tmp_path / f"{base}.png").write_bytes(destino.getvalue())
            (tmp_path / f"{base}.json").write_text(_json.dumps({
                "captura": base, "capturado_em": "2026-03-02T15:56:07-03:00", "fuso": "America/Sao_Paulo", "data_do_servidor": "",
                "arquivos": {"png": f"{base}.png", "html": f"{base}.html"}, "sha256": {"png": captura.sha256(destino.getvalue()), "html": "x"},
            }))
        return pipeline.Gravacao(plataforma="YOUTUBE", link=f"https://music.youtube.com/watch?v=v{n}", titulo=obra, interprete="Banda do Baile",
                                 obra=obra, provas=[base], classificacao=c.Classificacao(status, "motivo", revisar=revisar))
    return pipeline.Coleta("YOUTUBE", gravacoes=[
        gravacao(1, "LIGUE O RADIO", c.SEM_CREDITOS), gravacao(2, "COISA FEITA", c.VIOLACAO), gravacao(3, "BAILE NA SERRA", c.OK),
        gravacao(4, "A DANCA DA PANELA", c.SEM_CREDITOS, revisar=True), gravacao(5, "DANCA DA PANELLA", c.SEM_CREDITOS, com_prova=False),
    ])


import io


def test_lista_da_peticao_so_conta_o_que_esta_firme(rel, tmp_path):
    coleta = _coleta_para_peticao(tmp_path)
    firmes, a_confirmar = provas.itens_da_peticao([coleta], "YOUTUBE")
    assert [provas.linha_da_peticao(i) for i in firmes] == [
        "1 - Música: Coisa Feita https://music.youtube.com/watch?v=v2 Interpretada por: Banda do Baile",
        "2 - Música: Danca da Panella https://music.youtube.com/watch?v=v5 Interpretada por: Banda do Baile",
        "3 - Música: Ligue o Radio https://music.youtube.com/watch?v=v1 Interpretada por: Banda do Baile",
    ]
    assert [i["obra"] for i in a_confirmar] == ["A Danca da Panela"]
    from docx import Document
    texto = "\n".join(p.text for p in Document(io.BytesIO(provas.docx_da_lista(rel, [coleta], "YOUTUBE", "YouTube Music"))).paragraphs)
    assert "Há 3 obras intelectuais" in texto and "A confirmar antes de usar" in texto and "1 - Música: Coisa Feita" in texto


def test_pdf_de_provas_tem_indice_e_um_print_por_pagina_e_recusa_imagem_alterada(rel, tmp_path):
    import pdfplumber

    coleta = _coleta_para_peticao(tmp_path)
    pdf, avisos = provas.pdf_de_provas(rel, [coleta], "YOUTUBE", tmp_path, "YouTube Music")
    with pdfplumber.open(io.BytesIO(pdf)) as lido:
        paginas = [p.extract_text() for p in lido.pages]
    assert len(paginas) == 3 and "Coleta de provas - YouTube Music" in paginas[0] and "SEM CAPTURA" in paginas[0]
    assert "1 - Música: Coisa Feita" in paginas[1] and "3 - Música: Ligue o Radio" in paginas[2]
    assert avisos == ["2 (Danca da Panella / Banda do Baile): sem captura de tela"]
    (tmp_path / "prova-1.png").write_bytes(b"imagem trocada depois")
    _, avisos = provas.pdf_de_provas(rel, [coleta], "YOUTUBE", tmp_path)
    assert any("hash não confere" in a for a in avisos)


def test_url_codificada_nao_e_confundida_com_percentual(rel):
    from urllib.parse import quote, unquote

    filtro = FiltroDeSaida(rel.termos_sensiveis())
    url = "https://music.youtube.com/search?q=" + quote("A DANÇA DA PANELA 3 Zeca Lima")
    assert filtro.motivo(url) == "percentual"  # é por isso que o coletor confere o texto decodificado
    assert filtro.motivo(unquote(url)) == ""


# --- Apple Music, Spotify, Tidal e Vagalume: interpretação sem rede ------------

from creditos import apple, navegador, spotify, tidal, vagalume


def _pagina_da_apple(album_id, faixas, declaradas=None, endereco=None):
    secoes = [
        {"itemKind": "containerDetailHeaderLockup", "items": [{"title": "Álbum Tal", "subtitleLinks": [{"title": "Banda do Baile"}],
                                                               "trackCount": len(faixas) if declaradas is None else declaradas,
                                                               "quaternaryTitle": "Sertanejo · 2012"}]},
        {"itemKind": "trackLockup", "items": [
            {"title": t, "artistName": "Banda do Baile", "composer": comp, "trackNumber": i,
             "contentDescriptor": {"url": f"https://music.apple.com/br/album/x/{album_id}?i={i}"}}
            for i, (t, comp) in enumerate(faixas, start=1)]},
        {"itemKind": "containerDetailTracklistFooterLockup", "items": [{"description": "1 de maio de 2012 2 músicas ℗ 2012 Selo Inventado"}]},
    ]
    estado = _json.dumps({"data": [{"data": {"sections": secoes}}]})
    endereco = endereco or f"https://music.apple.com/br/album/album-tal/{album_id}"
    return f'<meta property="og:url" content="{endereco}"><script type="application/json" id="serialized-server-data">{estado}</script>'


def test_apple_le_o_compositor_de_cada_faixa_e_confere_album_e_contagem():
    album = apple.interpretar_pagina(_pagina_da_apple(55, [("Ligue o Rádio", "Zeca Lima & Cida Dias"), ("Coisa Feita", None)]), "55")
    assert (album.titulo, album.artista, album.lancamento, album.fornecedor, album.aviso) == ("Álbum Tal", "Banda do Baile", "2012", "℗ 2012 Selo Inventado", "")
    assert [(f.titulo, f.creditos, f.link) for f in album.faixas] == [
        ("Ligue o Rádio", ["Zeca Lima", "Cida Dias"], "https://music.apple.com/br/album/x/55?i=1"),
        ("Coisa Feita", [], "https://music.apple.com/br/album/x/55?i=2"),
    ]
    # A Apple redireciona para um endereço com o nome do álbum: a guarda é o final do og:url, não o endereço pedido.
    with pytest.raises(apple.ErroDeColeta, match="não é a do álbum 56"):
        apple.interpretar_pagina(_pagina_da_apple(55, [("X", "Y")]), "56")
    with pytest.raises(apple.ErroDeColeta):
        apple.interpretar_pagina('<meta property="og:url" content="https://music.apple.com/br/album/a/55">sem estado', "55")
    assert apple.interpretar_pagina(_pagina_da_apple(55, [("X", "Y")], declaradas=3), "55").aviso.startswith("CONTAGEM_DIVERGENTE")


def test_spotify_separa_secao_nome_e_papel_e_sem_secao_de_composicao_nao_ha_autor():
    partes = [["texto", "Artista"], ["nome", "Banda do Baile"], ["texto", "Artista principal"], ["texto", "Composição e letra"],
              ["nome", "Zeca Lima"], ["texto", "Autores"], ["nome", "Cida Dias"], ["texto", "Autores"], ["texto", "Fontes"], ["texto", "2000 Selo Inventado"]]
    secoes = spotify.interpretar_janela(partes)
    assert secoes == {"Artista": ["Banda do Baile"], "Composição e letra": ["Zeca Lima", "Cida Dias"], "Fontes": ["2000 Selo Inventado"]}
    assert spotify.autores_e_fornecedor(secoes) == (["Zeca Lima", "Cida Dias"], "2000 Selo Inventado")
    sem = spotify.interpretar_janela(partes[:3] + partes[8:])  # a janela abre, mas não tem a seção de composição
    assert spotify.autores_e_fornecedor(sem) == ([], "2000 Selo Inventado")
    linhas = [{"titulo": "Ligue o Rádio", "faixa": "abc123", "artistas": ["Banda do Baile", "Convidado"], "album": "Álbum Tal"}, {"titulo": "", "faixa": ""}]
    (cand,) = spotify.interpretar_linhas(linhas)
    assert (cand.link, cand.interprete) == ("https://open.spotify.com/intl-pt/track/abc123", "Banda do Baile, Convidado")


def test_tidal_autores_sao_composer_e_lyricist_e_fornecedor_e_producer_e_publisher():
    dados = {"titulo": "Álbum Tal", "artista": "Banda do Baile", "contagem": "2 MÚSICAS", "data": "19 de janeiro de 2024", "faixas": [
        {"id": "901", "numero": "1", "titulo": "Ligue o Rádio", "interprete": "Banda do Baile",
         "celulas": [["Producer", ["Selo Inventado"]], ["Composer", ["Zeca Lima", "Cida Dias"]], ["Lyricist", ["Zeca Lima"]], ["Music Publisher", ["Editora X"]]]},
        {"id": "902", "numero": "2", "titulo": "Coisa Feita", "interprete": "", "celulas": []},
    ]}
    album = tidal.interpretar_album(dados, "77")
    assert (album.lancamento, album.faixas_declaradas, album.fornecedor, album.aviso) == ("2024", 2, "Selo Inventado, Editora X", "")
    um, dois = album.faixas
    assert (um.creditos, um.link) == (["Zeca Lima", "Cida Dias"], "https://tidal.com/album/77/track/901")
    assert (dois.creditos, dois.interprete) == ([], "Banda do Baile")
    assert tidal.interpretar_album({**dados, "contagem": "3 MÚSICAS"}, "77").aviso.startswith("CONTAGEM_DIVERGENTE")
    with pytest.raises(ValueError):
        tidal.interpretar_album({"faixas": []}, "77")  # página sem faixa é erro, nunca álbum sem créditos


PAGINA_DO_VAGALUME = (
    "<h1>Banda do Baile</h1><div>letra</div><small class=styleDesc id=author><span class=tit-CA><b>Compositores:</b> "
    "José Carlos Souza Lima (Zeca Lima) (<a href=https://x target=_blank>ABC</a>), Maria Aparecida Dias (<a href=https://y>DEF</a>)</span>"
    "<span class=tit-E><b>Editores:</b> Editora X (<a href=https://x>ABC</a>)</span></small><p>rodapé Para autores</p>"
)


def test_vagalume_le_so_o_bloco_de_autoria_e_registra_a_negativa_expressa():
    letra = vagalume.interpretar_pagina(PAGINA_DO_VAGALUME, "https://www.vagalume.com.br/banda/ligue-o-radio.html")
    assert letra.creditos == ["José Carlos Souza Lima", "Maria Aparecida Dias"] and not letra.desconhecido
    assert "Compositores: José Carlos Souza Lima (Zeca Lima)" in letra.bloco and "Editores" in letra.bloco
    negada = vagalume.interpretar_pagina("<small id=author><span class=tit-CA><b>Compositor:</b> Desconhecido no ECAD</span></small>", "x")
    assert (negada.creditos, negada.desconhecido) == ([], True)
    with pytest.raises(vagalume.ErroDeColeta):  # sem o bloco, nada se conclui (o rodapé "Para autores" não conta)
        vagalume.interpretar_pagina("<p>rodapé Para autores</p>", "x")
    lista = vagalume.interpretar_lista('<a href=/banda-do-baile/ligue-o-radio.html>Ligue o Rádio</a><a href="/outra/x.html">X</a>', "banda-do-baile")
    assert lista == {"/banda-do-baile/ligue-o-radio.html": "Ligue o Rádio"}


def test_bloqueio_generico_login_captcha_e_desvio():
    for url in ["https://accounts.spotify.com/pt-BR/login", "https://account.deezer.com/login", "https://login.tidal.com/x"]:
        assert navegador.sinal_de_bloqueio(url), url
    assert navegador.sinal_de_bloqueio("https://tidal.com/album/1", "Verify you are human to continue")
    assert navegador.sinal_de_bloqueio("https://open.spotify.com/intl-pt/track/x", "Compartilhar · Ver créditos") == ""


class _VagalumeFalso:
    filtro, requisicoes = None, 0

    def letras_de(self, nome):
        return {"/banda-do-baile/coisa-feita.html": "Coisa Feita", "/banda-do-baile/outra.html": "Outra"} if nome == "Banda do Baile" else {}

    def letra(self, endereco):
        return vagalume.Letra(link="https://www.vagalume.com.br" + endereco, creditos=[], desconhecido=True, bloco="Compositor: Desconhecido no ECAD")


def test_vagalume_negativa_expressa_entra_como_sem_creditos_com_a_nota(rel):
    coleta = pipeline.coletar_vagalume(rel, Config(nomes_confirmados=["ZECA LIMA"]), _VagalumeFalso(), recorrentes=["Banda do Baile"])
    (g,) = coleta.gravacoes
    assert (g.plataforma, g.obra, g.classificacao.status) == ("VAGALUME", "COISA FEITA", c.SEM_CREDITOS)
    assert "AFIRMA que o compositor é desconhecido" in g.classificacao.fundamento


def test_pacote_de_provas_tem_uma_pasta_por_plataforma_com_gravacao_negativa(rel, tmp_path):
    import zipfile

    youtube = _coleta_para_peticao(tmp_path / "youtube")
    sem_nada = pipeline.Coleta("TIDAL")
    pacote, resumo, avisos = provas.pacote(rel, [youtube, sem_nada], tmp_path, b"planilha")
    assert zipfile.ZipFile(io.BytesIO(pacote)).namelist() == [
        "Planilha de Obras.xlsx", "YouTube Music/Lista da petição - YouTube Music.docx", "YouTube Music/Provas - YouTube Music.pdf"]
    assert resumo == [{"plataforma": "YouTube Music", "firmes": 3, "a_confirmar": 1}, {"plataforma": "Tidal", "firmes": 0, "a_confirmar": 0}]
    assert avisos == ["YouTube Music: 2 (Danca da Panella / Banda do Baile): sem captura de tela"]


def test_apple_o_print_e_da_pagina_da_musica_e_a_tela_manda_quando_diverge_dos_dados(rel, tmp_path, monkeypatch):
    assert pipeline.pagina_da_musica_na_apple("https://music.apple.com/br/album/x/55?i=777") == "https://music.apple.com/br/song/777"
    assert pipeline.autores_na_pagina_da_apple(["Composição E Letra", "Zeca Lima", "Composição", "Cida Dias", "Letra"]) == ["Zeca Lima", "Cida Dias"]

    class NavegadorFalso:
        class pagina:
            url = "https://music.apple.com/br/song/777"
            wait_for_timeout = staticmethod(lambda ms: None)
            evaluate = staticmethod(lambda *a: "")
        abertos = []
        def ir(self, url, pausa=None): self.abertos.append(url)
        def conferir(self, texto=""): pass

    capturas = []
    monkeypatch.setattr(captura, "capturar", lambda pagina, pasta, obra, interprete, plataforma, exibido, etapa="": capturas.append(exibido) or {"captura": f"print-{len(capturas)}"})
    sem = lambda n: pipeline.Gravacao(plataforma="APPLE MUSIC", link=f"https://music.apple.com/br/album/x/55?i={n}", titulo="Ligue o Rádio",
                                      interprete="Zeca Lima", obra="LIGUE O RADIO", vinculo="x", classificacao=c.Classificacao(c.SEM_CREDITOS, "sem compositor nos dados do álbum"))
    coleta = pipeline.Coleta("APPLE MUSIC", gravacoes=[sem(1), sem(2), sem(3)])
    telas = iter([[], ["Zeca Lima"], ["Banda do Baile"]])  # igual aos dados; a tela mostra o titular; a tela mostra outro nome
    reclassificar = lambda g: classificar(Item(titulo=g.titulo, interprete=g.interprete, creditos=g.creditos, vinculo=True), rel)
    nav = NavegadorFalso()
    pipeline.capturar_paginas(coleta, nav, tmp_path, lambda n: next(telas), None, pipeline.pagina_da_musica_na_apple, reclassificar)
    igual, creditada, outro = coleta.gravacoes
    assert nav.abertos == ["https://music.apple.com/br/song/1", "https://music.apple.com/br/song/2", "https://music.apple.com/br/song/3"]
    assert (igual.classificacao.status, igual.provas) == (c.SEM_CREDITOS, ["print-1"])
    assert (creditada.classificacao.status, creditada.provas) == (c.OK, []) and "vale o que a tela mostra" in creditada.classificacao.fundamento
    assert (outro.classificacao.status, outro.creditos, outro.provas) == (c.VIOLACAO, ["Banda do Baile"], ["print-2"])


# --- ligação provada x ligação presumida (os dois erros do primeiro caso real) ---


def test_nome_de_uma_palavra_nunca_casa_com_nome_maior():
    assert c.mesmo_artista("Banda do Baile", "Grupo Banda do Baile") and c.mesmo_artista("BANDA DO BAILE", "banda do baile")
    assert not c.mesmo_artista("Fulano", "Beltrano Fulano") and not c.mesmo_artista("Fulano", "Oficial Beltrano Fulano")
    assert c.mesmo_artista("Fulano", "FULANO")
    assert c.partes_do_interprete("Fulano, Beltrano & Sicrano feat. Outro") == ["Fulano", "Beltrano", "Sicrano", "Outro"]
    assert pipeline._citado("Banda do Baile", "Banda do Baile - Ligue o Rádio")
    assert pipeline._citado("Fulano", "Ligue o Rádio | Fulano (Ao Vivo)")
    assert not pipeline._citado("Fulano", "Ligue o Rádio | Beltrano Fulano - coreografia")


def test_artista_que_so_grava_outras_obras_do_titular_nao_sustenta_acusacao(rel):
    presumindo = Config(nomes_confirmados=["ZECA LIMA"], presumidos=["Cantor Popular"])
    # Mesmo título, cantado por quem grava outras obras do titular, com compositores que não são os do relatório:
    # é a marca de uma música homônima, e não de um crédito errado.
    outra = classificar(Item(titulo="Ligue o Rádio", interprete="Cantor Popular, Convidado",
                             creditos=["Cantor Popular", "Convidado", "Outro Autor Qualquer"]), rel, presumindo)
    assert (outra.status, outra.revisar) == (c.HOMONIMA, True)
    # Sem crédito nenhum: pode ser a obra, mas só com a presunção não entra em contagem nenhuma.
    sem = classificar(Item(titulo="Ligue o Rádio", interprete="Cantor Popular"), rel, presumindo)
    assert (sem.status, sem.revisar, sem.presumido) == (c.SEM_CREDITOS, True, True) and "PRESUMIDO" in sem.fundamento
    # Quando alguém confirma o intérprete, ou o par já foi provado em outra plataforma, o resultado é firme.
    confirmado = classificar(Item(titulo="Ligue o Rádio", interprete="Cantor Popular"), rel, Config(interpretes=["Cantor Popular"]))
    provado = classificar(Item(titulo="Ligue o Rádio", interprete="Cantor Popular", vinculo=True), rel, presumindo)
    assert [(k.status, k.revisar, k.presumido) for k in (confirmado, provado)] == [(c.SEM_CREDITOS, False, False)] * 2
    # E um autor do relatório no crédito prova a ligação por si só.
    creditado = classificar(Item(titulo="Coisa Feita", interprete="Cantor Popular", creditos=["Cida Dias"]), rel, presumindo)
    assert (creditado.status, creditado.revisar) == (c.VIOLACAO, False)


def test_youtube_nao_liga_pelo_sobrenome_e_presuncao_fica_a_confirmar(rel):
    ytm = YouTubeFalso(
        buscas={"LIGUE O RADIO Fulano": [
            ("v1", "Ligue o Rádio", "Fulano", "Álbum", "Música"),
            ("v2", "Beltrano Fulano - Ligue o Rádio", "Oficial Beltrano Fulano", "2 mil visualizações", "Vídeo"),  # outro artista
        ], "COISA FEITA Fulano": [("v3", "Coisa Feita", "Fulano", "Álbum", "Música")]},
        leituras={"v1": _leitura("v1", "Ligue o Rádio", "Fulano", MENU_SEM), "v3": _leitura("v3", "Coisa Feita", "Fulano", MENU_SEM)},
    )
    coleta = pipeline.coletar_youtube(rel, Config(nomes_confirmados=["ZECA LIMA"]), ytm, recorrentes=["Fulano"],
                                      pares={"COISA FEITA": ["Fulano"]})
    assert ytm.lidos == ["v1", "v3"] and coleta.sem_vinculo_nao_abertos == 1  # o vídeo do homônimo de sobrenome nem é aberto
    presumida, provada = coleta.gravacoes
    assert (presumida.classificacao.status, presumida.classificacao.presumido) == (c.SEM_CREDITOS, True)
    assert (provada.classificacao.status, provada.classificacao.revisar) == (c.SEM_CREDITOS, False)  # par provado em outra plataforma
    firmes, a_confirmar = provas.itens_da_peticao([coleta], "YOUTUBE")
    assert [i["obra"] for i in firmes] == ["Coisa Feita"] and [i["obra"] for i in a_confirmar] == ["Ligue o Radio"]


def test_print_ja_tirado_e_reaproveitado_na_rodada_seguinte(tmp_path):
    (tmp_path / "p1.png").write_bytes(b"png")
    ficha = {"captura": "p1", "url": "https://music.apple.com/br/song/nome-da-musica/777", "arquivos": {"png": "p1.png"},  # com o redirecionamento
             "exibido": {"creditos_lidos": ["Banda do Baile"]}}
    (tmp_path / "p1.json").write_text(_json.dumps(ficha))
    (tmp_path / "solta.json").write_text(_json.dumps({"captura": "p2", "url": "x", "arquivos": {"png": "sumiu.png"}}))
    assert [f["captura"] for f in captura.capturas_guardadas(tmp_path)] == ["p1"]  # ficha sem a imagem não vale
    g = lambda n, creditos: pipeline.Gravacao(plataforma="APPLE MUSIC", link=f"https://music.apple.com/br/album/x/55?i={n}", creditos=creditos,
                                              classificacao=c.Classificacao(c.VIOLACAO, "motivo"))
    coleta = pipeline.Coleta("APPLE MUSIC", gravacoes=[g(777, ["Banda do Baile"]), g(777, ["Outro Nome"]), g(778, ["Banda do Baile"])])
    pipeline.reaproveitar_prints(coleta, tmp_path, pipeline.pagina_da_musica_na_apple)
    # Só a gravação do mesmo endereço e com os mesmos créditos que estavam na tela quando o print foi tirado.
    assert [x.provas for x in coleta.gravacoes] == [["p1"], [], []]


# --- estimativa de tempo e atualização do app ---------------------------------

from creditos import atualizacao


def test_estimativa_de_tempo_cresce_com_obras_e_plataformas_e_nao_finge_precisao():
    todas = pipeline.estimar_minutos(119, pipeline.TODAS)
    assert todas > pipeline.estimar_minutos(119, ("deezer", "apple")) > pipeline.estimar_minutos(10, ("deezer", "apple"))
    assert pipeline.estimar_minutos(10, ()) == pipeline.estimar_minutos(10, ("deezer",))  # a Deezer roda sempre
    assert pipeline.texto_da_estimativa(3) == "cerca de 3 minutos" and pipeline.texto_da_estimativa(1) == "cerca de 1 minuto"
    assert pipeline.texto_da_estimativa(22) == "cerca de 20 minutos"
    assert pipeline.texto_da_estimativa(171) == "cerca de 2h50" and pipeline.texto_da_estimativa(118) == "cerca de 2 horas"


def _pacote(arquivos: dict, raiz="quem-canta-9.9.9/") -> bytes:
    import zipfile
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w") as z:
        for nome, conteudo in arquivos.items():
            z.writestr(raiz + nome, conteudo)
    return saida.getvalue()


def test_so_avisa_quando_a_versao_publicada_e_maior(monkeypatch):
    assert atualizacao.mais_nova("0.10.0", "0.9.5") and atualizacao.mais_nova("v1.0", "0.9.9")
    assert not atualizacao.mais_nova("0.4.0", "0.4.0") and not atualizacao.mais_nova("0.3.9", "0.4.0") and not atualizacao.mais_nova("", "0.4.0")

    class Resposta:
        def __init__(self, dados): self.dados = dados
        def json(self): return self.dados
    publicada = {"versao": "99.0.0", "url": "https://exemplo/app.zip", "sha256": "A" * 64, "notas": "melhorias"}
    monkeypatch.setattr(atualizacao.requests, "get", lambda url, timeout: Resposta(publicada))
    assert atualizacao.consultar("https://exemplo/versao.json") == {**publicada, "sha256": "a" * 64}
    monkeypatch.setattr(atualizacao.requests, "get", lambda url, timeout: Resposta({"versao": "0.0.1"}))
    assert atualizacao.consultar("https://exemplo/versao.json") is None
    assert atualizacao.consultar("http://sem-https/versao.json") is None

    def fora_do_ar(url, timeout):
        raise atualizacao.requests.ConnectionError()
    monkeypatch.setattr(atualizacao.requests, "get", fora_do_ar)
    assert atualizacao.consultar("https://exemplo/versao.json") is None  # sem rede, o app abre do mesmo jeito


def test_atualizacao_troca_o_programa_e_nunca_toca_nos_dados_nem_no_ambiente(tmp_path):
    (tmp_path / "dados").mkdir()
    (tmp_path / "dados" / "caso.txt").write_text("prints do cliente")
    (tmp_path / "creditos").mkdir()
    (tmp_path / "creditos" / "versao.py").write_text("antiga")
    (tmp_path / "atualizacao.json").write_text('{"consultar_em": "https://do-escritorio"}')
    zip_novo = _pacote({"creditos/versao.py": "nova", "creditos_app.py": "tela nova", "dados/caso.txt": "APAGARIA",
                        ".venv/x.txt": "não entra", "atualizacao.json": "{}"})
    publicada = {"versao": "9.9.9", "url": "https://exemplo/app.zip", "sha256": captura.sha256(zip_novo)}
    escritos = atualizacao.instalar(publicada, tmp_path, baixar=lambda url: zip_novo)
    assert sorted(escritos) == ["creditos/versao.py", "creditos_app.py"]
    assert (tmp_path / "creditos" / "versao.py").read_text() == "nova"
    assert (tmp_path / "dados" / "caso.txt").read_text() == "prints do cliente" and not (tmp_path / ".venv").exists()
    assert "do-escritorio" in (tmp_path / "atualizacao.json").read_text()
    assert atualizacao.endereco_de_consulta(tmp_path) == "https://do-escritorio"


def test_endereco_de_consulta_tem_padrao_e_pode_ser_trocado_ou_desligado(tmp_path, monkeypatch):
    monkeypatch.delenv("QUEMCANTA_ATUALIZACAO", raising=False)
    assert atualizacao.endereco_de_consulta(tmp_path) == atualizacao.ENDERECO_PADRAO  # sem arquivo
    (tmp_path / "atualizacao.json").write_text('{"consultar_em": ""}')
    assert atualizacao.endereco_de_consulta(tmp_path) == atualizacao.ENDERECO_PADRAO  # arquivo vazio, como no pacote
    (tmp_path / "atualizacao.json").write_text("isto não é json")
    assert atualizacao.endereco_de_consulta(tmp_path) == atualizacao.ENDERECO_PADRAO
    (tmp_path / "atualizacao.json").write_text('{"consultar_em": "https://outro/versao.json"}')
    assert atualizacao.endereco_de_consulta(tmp_path) == "https://outro/versao.json"
    monkeypatch.setenv("QUEMCANTA_ATUALIZACAO", "desligada")
    assert atualizacao.endereco_de_consulta(tmp_path) == "desligada"
    monkeypatch.setattr(atualizacao.requests, "get", lambda url, timeout: pytest.fail("não podia consultar"))
    assert atualizacao.verificar(atualizacao.endereco_de_consulta(tmp_path)) == (atualizacao.SEM_RESPOSTA, None)
    assert atualizacao.ENDERECO_PADRAO.startswith("https://raw.githubusercontent.com/")
    assert atualizacao.endereco_do_pacote("1.2.3").startswith("https://github.com/") and "/v1.2.3/" in atualizacao.endereco_do_pacote("1.2.3")


def test_verificar_separa_versao_nova_em_dia_e_sem_resposta(monkeypatch):
    class Resposta:
        def __init__(self, dados): self.dados = dados
        def json(self):
            if isinstance(self.dados, Exception):
                raise self.dados
            return self.dados
    def com(dados):
        monkeypatch.setattr(atualizacao.requests, "get", lambda url, timeout: Resposta(dados))
        return atualizacao.verificar("https://exemplo/versao.json")
    estado, publicada = com({"versao": "99.0.0", "url": "https://exemplo/app.zip", "sha256": "A" * 64})
    assert estado == atualizacao.NOVA and publicada["versao"] == "99.0.0" and publicada["sha256"] == "a" * 64
    assert com({"versao": atualizacao.VERSAO}) == (atualizacao.EM_DIA, None)
    assert com({"versao": "0.0.1"}) == (atualizacao.EM_DIA, None)
    # Resposta que não é a esperada (página de erro, lista, sem versão) nunca vira "em dia".
    for ruim in ({}, [], {"versao": "abc"}, "404: Not Found", ValueError("não é json")):
        assert com(ruim) == (atualizacao.SEM_RESPOSTA, None)


def test_atualizacao_nao_regrava_arquivo_igual(tmp_path):
    (tmp_path / "requirements.txt").write_text("streamlit")
    (tmp_path / "creditos_app.py").write_text("tela velha")
    pacote = _pacote({"requirements.txt": "streamlit", "creditos_app.py": "tela nova"})
    publicada = {"url": "https://exemplo/app.zip", "sha256": captura.sha256(pacote)}
    assert atualizacao.instalar(publicada, tmp_path, baixar=lambda url: pacote) == ["creditos_app.py"]
    assert atualizacao.instalar(publicada, tmp_path, baixar=lambda url: pacote) == []  # instalar de novo não muda nada


def test_pacote_do_windows_nao_leva_dados_nem_o_arquivo_de_versao_publicada(tmp_path, monkeypatch):
    import zipfile
    import empacotar
    (tmp_path / "app").mkdir()
    for nome in ("creditos_app.py", "requirements.txt", "Instalar.cmd", "atualizacao.json"):
        (tmp_path / "app" / nome).write_text("x\n")
    (tmp_path / "app" / "dados").mkdir()
    (tmp_path / "app" / "dados" / "caso.pdf").write_text("relatório de cliente")
    (tmp_path / "app" / "versao-publicada.json").write_text("{}")
    monkeypatch.setattr(empacotar, "AQUI", tmp_path / "app")
    monkeypatch.setattr(empacotar.sys, "argv", ["empacotar.py", str(tmp_path), "o", "que", "mudou"])
    empacotar.main()
    pacote = tmp_path / f"quem-canta-{atualizacao.VERSAO}-windows.zip"
    nomes = [n.split("/", 1)[1] for n in zipfile.ZipFile(pacote).namelist()]
    assert sorted(nomes) == ["Instalar.cmd", "atualizacao.json", "creditos_app.py", "requirements.txt"]
    publicada = _json.loads((tmp_path / "app" / "versao-publicada.json").read_text())
    assert publicada == {"versao": atualizacao.VERSAO, "url": atualizacao.endereco_do_pacote(), "notas": "o que mudou",
                         "sha256": captura.sha256(pacote.read_bytes())}
    # O que o empacotar publica é exatamente o que o app instalado aceita instalar.
    (tmp_path / "instalado").mkdir()
    assert sorted(atualizacao.instalar(publicada, tmp_path / "instalado", baixar=lambda url: pacote.read_bytes())) == [
        "Instalar.cmd", "creditos_app.py", "requirements.txt"]


def test_atualizacao_recusa_pacote_sem_hash_adulterado_ou_que_sai_da_pasta(tmp_path):
    zip_bom = _pacote({"creditos_app.py": "tela"})
    for publicada, motivo in [
        ({"url": "https://x/app.zip", "sha256": ""}, "hash"),
        ({"url": "http://x/app.zip", "sha256": "a" * 64}, "https"),
        ({"url": "https://x/app.zip", "sha256": "b" * 64}, "não confere"),
    ]:
        with pytest.raises(atualizacao.FalhaNaAtualizacao, match=motivo):
            atualizacao.instalar(publicada, tmp_path, baixar=lambda url: zip_bom)
    malicioso = _pacote({"../../fora.txt": "x"})
    with pytest.raises(atualizacao.FalhaNaAtualizacao, match="fora da pasta"):
        atualizacao.instalar({"url": "https://x/app.zip", "sha256": captura.sha256(malicioso)}, tmp_path, baixar=lambda url: malicioso)
    assert not list(tmp_path.glob("**/*.py")) and not (tmp_path.parent / "fora.txt").exists()


def test_limite_negativo_pega_as_ultimas_obras(rel):
    coleta = pipeline.Coleta("X")
    assert [t for _, t in pipeline._titulos(rel, 2, coleta, "X")] == ["LIGUE O RADIO", "COISA FEITA"]
    assert [t for _, t in pipeline._titulos(rel, -2, coleta, "X")] == ["A DANCA DA PANELA", "DANCA DA PANELLA"]
    assert len(pipeline._titulos(rel, None, coleta, "X")) == 5 and "últimas obras de 5" in coleta.avisos[-1]
