"""Roda o app.py de ponta a ponta, sem navegador e sem rede."""

from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from cantor.busca import Buscador, Resultado

ETAPA_DOS_INTERPRETES, ETAPA_DOS_PRINTS = "Buscar intérpretes", "Coleta de prints"
# A planilha que o compositor conferiu, já lida.
CONFERIDOS = {"LIGUE O RADIO": ["Banda do Baile"], "COISA FEITA": ["Cantora Original"]}


@pytest.fixture(autouse=True)
def _isolado(tmp_path, monkeypatch):
    monkeypatch.setenv("QUEMCANTA_DADOS", str(tmp_path / "dados-do-teste"))
    monkeypatch.setenv("QUEMCANTA_ATUALIZACAO", "")
    monkeypatch.setattr("creditos.atualizacao.endereco_de_consulta", lambda pasta=None: "")
    monkeypatch.setattr("creditos.andamento._ATUAL", None)  # nenhuma coleta de outro teste em curso


def test_modo_relatorio_aparece_na_tela_e_pode_ser_desligado(tmp_path, monkeypatch):
    monkeypatch.setattr("cantor.banco.CAMINHO_PADRAO", tmp_path / "app.db")
    monkeypatch.setattr("cantor.busca.configurar_log", lambda: None)
    preparados = []
    monkeypatch.setattr(
        Buscador, "preparar_relatorio",
        lambda self, nome, nomes_artisticos=(): preparados.append(nome) or {"obras": [], "erro": "", "segundos": 0.0},
    )
    monkeypatch.setattr(Buscador, "resolver", lambda self, titulo, compositor="", iswc="", **_: Resultado("Alguém", "media"))

    at = AppTest.from_file("../app.py", default_timeout=30)
    at.session_state["original"] = pd.DataFrame(
        {"Música": [f"Faixa {i}" for i in range(6)], "Compositor": ["Fulano de Tal"] * 6}, dtype=str
    )
    at.run()
    assert not at.exception
    assert any("Modo relatório ativo" in aviso.value and "Fulano de Tal" in aviso.value for aviso in at.info)
    caixa = next(c for c in at.checkbox if c.label == "Usar o modo relatório")
    assert caixa.value is True

    next(b for b in at.button if b.label == "Processar").click().run()
    assert not at.exception and preparados == ["Fulano de Tal"]

    next(c for c in at.checkbox if c.label == "Usar o modo relatório").uncheck().run()
    assert "resultado" not in at.session_state  # desligar o modo invalida o resultado anterior
    next(b for b in at.button if b.label == "Processar").click().run()
    assert not at.exception and preparados == ["Fulano de Tal"]  # desligado: não prepara de novo


def test_nomes_artisticos_sugeridos_so_valem_depois_de_confirmados(tmp_path, monkeypatch):
    monkeypatch.setattr("cantor.banco.CAMINHO_PADRAO", tmp_path / "app.db")
    monkeypatch.setattr("cantor.busca.configurar_log", lambda: None)
    preparados = []
    monkeypatch.setattr(
        Buscador, "preparar_relatorio",
        lambda self, nome, nomes_artisticos=(): preparados.append(list(nomes_artisticos)) or {"obras": [], "erro": "", "segundos": 0.0},
    )
    monkeypatch.setattr(Buscador, "sugerir_nomes_artisticos", lambda self, nome: ["Fulano Tal"])
    monkeypatch.setattr(Buscador, "resolver", lambda self, titulo, compositor="", iswc="", **_: Resultado("Banda Arco", "baixa"))

    at = AppTest.from_file("../app.py", default_timeout=30)
    at.session_state["original"] = pd.DataFrame(
        {"Música": [f"Faixa {i}" for i in range(6)], "Compositor": ["Fulano de Tal"] * 6}, dtype=str
    )
    at.run()
    next(b for b in at.button if b.label == "Sugerir nomes artísticos").click().run()
    assert not at.exception and at.multiselect[0].options == ["Fulano Tal"]
    next(b for b in at.button if b.label == "Processar").click().run()
    assert preparados == [[]]  # sugestão não confirmada não é usada

    recorrentes = next(m for m in at.multiselect if "várias linhas" in m.label)
    assert recorrentes.options == ["Banda Arco"]
    recorrentes.select("Banda Arco").run()
    next(b for b in at.button if b.key == "recorrentes_marcados_botao").click().run()
    assert not at.exception and at.session_state["nomes_artisticos"] == "Banda Arco"
    assert "resultado" not in at.session_state  # mudou a lista de nomes: o resultado anterior não vale mais
    next(b for b in at.button if b.label == "Processar").click().run()
    assert preparados == [[], ["Banda Arco"]]


def test_fluxo_processar_e_previa(tmp_path, monkeypatch):
    monkeypatch.setattr("cantor.banco.CAMINHO_PADRAO", tmp_path / "app.db")
    respostas = {
        "Wave": Resultado("Tom Jobim", "alta", [], "https://musicbrainz.org/recording/x", "obra"),
        "Amor": Resultado("Fulano", "baixa", ["Beltrano"], "https://musicbrainz.org/recording/y", "3 artistas"),
        "Nada": Resultado(observacao="sem resultados"),
    }
    monkeypatch.setattr("cantor.busca.configurar_log", lambda: None)  # não escreve no log de verdade
    monkeypatch.setattr(Buscador, "resolver", lambda self, titulo, compositor="", iswc="", **_: respostas[titulo])

    at = AppTest.from_file("../app.py", default_timeout=30)
    at.session_state["original"] = pd.DataFrame(
        {"Música": ["Wave", "Amor", "Nada"], "Autor": ["Tom Jobim", "", ""]}, dtype=str
    )
    at.run()
    assert not at.exception
    assert [s.value for s in at.selectbox] == ["Música", "Autor", "(nenhuma)", "(nenhuma)", "(nenhuma)"]

    next(b for b in at.button if b.label == "Processar").click().run()
    assert not at.exception
    resultado = at.session_state["resultado"]
    assert list(resultado["cantor_sugerido"]) == ["Tom Jobim", "Fulano", ""]
    assert list(resultado["confianca"]) == ["alta", "baixa", "nao_encontrado"]
    assert {m.label: m.value for m in at.metric} == {"Alta": "1", "Média": "0", "Baixa": "1", "Não encontrado": "1"}


def test_tela_de_importacao_mostra_resumo_nomes_artisticos_e_duplicidades():
    from tests.test_creditos import relatorio_de_teste

    at = AppTest.from_file("../creditos_app.py", default_timeout=30)
    at.session_state["relatorio"] = relatorio_de_teste()
    at.session_state["etapa"] = ETAPA_DOS_PRINTS
    at.session_state["conferidos"] = CONFERIDOS
    at.run()
    assert not at.exception
    assert {m.label: m.value for m in at.metric}["Obras lidas"] == "6"
    assert any("Contagem conferida" in s.value for s in at.success)
    assert at.session_state["config"]["nomes_confirmados"] == ["ZECA LIMA", "ZECA DO BAILE"]
    assert at.session_state["config"]["nomes_dos_coautores"] == ["CIDA DIAS"]
    assert any("duplicidades (2)" in e.label for e in at.expander)
    # O botão de coletar vem antes dos blocos de conferência, que ficam recolhidos.
    assert [s.value for s in at.subheader][-2:] == ["Verificar os créditos nas plataformas", "Conferir e ajustar (opcional)"]
    # Dados contratuais só aparecem se a pessoa pedir.
    assert all("titulares e percentuais" not in d.value.columns for d in at.dataframe)


def test_tela_coleta_e_oferece_planilha_e_pacote_de_provas(tmp_path, monkeypatch):
    from creditos import classificador as c, pipeline
    from tests.test_creditos import relatorio_de_teste

    monkeypatch.chdir(tmp_path)
    chamadas = []

    def executar_falso(relatorio, config, pasta, coautores=(), limite_youtube=None, ao_avancar=None, plataformas=None, **_):
        chamadas.append((config.nomes_confirmados, list(coautores), plataformas, limite_youtube))
        ao_avancar("Spotify: lendo faixa 1/1")
        sem = pipeline.Gravacao(plataforma="SPOTIFY", link="https://open.spotify.com/intl-pt/track/x1", titulo="Ligue o Rádio",
                                interprete="Banda do Baile", obra="LIGUE O RADIO", classificacao=c.Classificacao(c.SEM_CREDITOS, "motivo"))
        return [pipeline.Coleta("SPOTIFY", gravacoes=[sem]), pipeline.Coleta("DEEZER")]

    monkeypatch.setattr(pipeline, "executar", executar_falso)
    at = AppTest.from_file(str(Path(__file__).parent.parent / "creditos_app.py"), default_timeout=60)
    at.session_state["relatorio"] = relatorio_de_teste()
    at.session_state["etapa"] = ETAPA_DOS_PRINTS
    at.session_state["conferidos"] = CONFERIDOS
    at.run()
    assert at.multiselect[0].value == ["deezer", "youtube", "spotify", "tidal", "apple", "vagalume", "amazon"]  # todas, por padrão
    assert at.multiselect[0].options[-1] == "Amazon Music (aplicativo de desktop)"  # existe, mas vem desmarcado
    at.multiselect[0].set_value(["deezer", "spotify"]).run()
    next(b for b in at.button if b.label == "Coletar e classificar").click().run()
    assert not at.exception
    assert chamadas == [(["ZECA LIMA", "ZECA DO BAILE"], ["CIDA DIAS"], ("deezer", "spotify"), None)]  # sempre todas as obras
    assert not at.number_input  # não existe mais o campo de quantas obras verificar
    assert any("Tempo estimado: cerca de" in i.value for i in at.info)
    saidas = at.session_state["saidas"]
    assert saidas["resumo"][0] == {"plataforma": "Spotify", "firmes": 1, "a_confirmar": 0}
    import io, zipfile
    nomes = zipfile.ZipFile(io.BytesIO(saidas["pacote"])).namelist()
    assert nomes == ["Planilha de Obras.xlsx", "Spotify/Lista da petição - Spotify.docx", "Spotify/Provas - Spotify.pdf"]
    assert any("sem captura de tela" in a for a in saidas["avisos"])
    # Na tela: um resultado enxuto, com os avisos técnicos recolhidos.
    assert "Resultado" in [s.value for s in at.subheader] and not at.warning[:0]
    assert any("Avisos e detalhes da execução (1 avisos)" == e.label for e in at.expander)


def test_tela_pede_confirmacao_dos_interpretes_presumidos(tmp_path, monkeypatch):
    from creditos import classificador as c, pipeline
    from tests.test_creditos import relatorio_de_teste

    monkeypatch.chdir(tmp_path)
    recebidos = []

    def executar_falso(relatorio, config, pasta, coautores=(), **_):
        recebidos.append(list(config.interpretes))
        sem = pipeline.Gravacao(plataforma="SPOTIFY", link="https://open.spotify.com/intl-pt/track/x1", titulo="Ligue o Rádio", interprete="Cantor Popular",
                                obra="LIGUE O RADIO", classificacao=c.Classificacao(c.SEM_CREDITOS, "motivo", revisar="Cantor Popular" not in config.interpretes))
        return [pipeline.Coleta("SPOTIFY", gravacoes=[sem]),
                pipeline.Coleta("DEEZER", interpretes_inferidos=[{"nome": "Cantor Popular", "obras": 3, "com_autor": 3}])]

    monkeypatch.setattr(pipeline, "executar", executar_falso)
    at = AppTest.from_file(str(Path(__file__).parent.parent / "creditos_app.py"), default_timeout=60)
    at.session_state["relatorio"] = relatorio_de_teste()
    at.session_state["etapa"] = ETAPA_DOS_PRINTS
    at.session_state["conferidos"] = CONFERIDOS
    at.run()
    next(b for b in at.button if b.label == "Coletar e classificar").click().run()
    assert at.session_state["saidas"]["resumo"][0] == {"plataforma": "Spotify", "firmes": 0, "a_confirmar": 1}
    marcar = next(m for m in at.multiselect if m.label == "Intérpretes a confirmar")
    assert marcar.options == ["Cantor Popular (3 títulos do relatório)"]
    marcar.set_value(["Cantor Popular"]).run()
    next(b for b in at.button if b.label == "Confirmar os marcados").click().run()
    assert not at.exception and "coletas" not in at.session_state and at.session_state["interpretes_informados"] == "Cantor Popular"
    next(b for b in at.button if b.label == "Coletar e classificar").click().run()
    assert recebidos == [[], ["Cantor Popular"]]
    assert at.session_state["saidas"]["resumo"][0] == {"plataforma": "Spotify", "firmes": 1, "a_confirmar": 0}
    assert not [m for m in at.multiselect if m.label == "Intérpretes a confirmar"]  # confirmado: não pergunta de novo


def test_campo_de_quantas_obras_so_existe_para_quem_desenvolve(tmp_path, monkeypatch):
    from creditos import pipeline
    from tests.test_creditos import relatorio_de_teste

    monkeypatch.chdir(tmp_path)
    limites = []
    monkeypatch.setattr(pipeline, "executar", lambda *a, limite_youtube=None, **k: limites.append(limite_youtube) or [pipeline.Coleta("DEEZER")])

    def abrir():
        at = AppTest.from_file(str(Path(__file__).parent.parent / "creditos_app.py"), default_timeout=60)
        at.session_state["relatorio"] = relatorio_de_teste()
        at.session_state["etapa"] = ETAPA_DOS_PRINTS
        at.session_state["conferidos"] = CONFERIDOS
        return at.run()

    assert not abrir().number_input  # instalação normal: sempre todas as obras
    monkeypatch.setenv("QUEMCANTA_DEV", "1")
    at = abrir()
    at.number_input[0].set_value(1).run()
    next(b for b in at.button if b.label == "Coletar e classificar").click().run()
    next(r for r in at.radio if r.label == "Quais").set_value("as últimas").run()
    next(b for b in at.button if b.label == "Coletar e classificar").click().run()
    assert limites == [1, -1]


def _tela_de_creditos():
    return AppTest.from_file(str(Path(__file__).parent.parent / "creditos_app.py"), default_timeout=60)


def _botao(at, rotulo):
    return next(b for b in at.button if b.label == rotulo)


def test_botao_procurar_atualizacao_diz_em_dia_ou_sem_resposta(monkeypatch):
    from creditos import atualizacao
    monkeypatch.setattr(atualizacao, "verificar", lambda *a, **k: (atualizacao.EM_DIA, None))
    at = _tela_de_creditos().run()
    assert not at.exception and any("Versão instalada" in c.value for c in at.caption)
    _botao(at, "Procurar atualização").click().run()
    assert any("já está na versão mais recente" in s.value for s in at.success)
    monkeypatch.setattr(atualizacao, "verificar", lambda *a, **k: (atualizacao.SEM_RESPOSTA, None))
    _botao(at, "Procurar atualização").click().run()
    assert not at.success and any("Não consegui consultar" in w.value for w in at.warning)


def test_botao_procurar_atualizacao_acha_a_versao_nova_e_instala(monkeypatch):
    from creditos import atualizacao
    nova = {"versao": "99.0.0", "url": "https://exemplo/app.zip", "sha256": "a" * 64, "notas": "ficou melhor"}
    monkeypatch.setattr(atualizacao, "verificar", lambda *a, **k: (atualizacao.EM_DIA, None))
    at = _tela_de_creditos().run()
    monkeypatch.setattr(atualizacao, "verificar", lambda *a, **k: (atualizacao.NOVA, nova))
    _botao(at, "Procurar atualização").click().run()
    assert any("Há uma versão nova do app: 99.0.0" in m.value and "ficou melhor" in m.value for m in at.markdown)
    instaladas = []
    monkeypatch.setattr(atualizacao, "instalar", lambda publicada: instaladas.append(publicada) or ["creditos_app.py", "requirements.txt"])
    _botao(at, "Baixar e instalar a versão nova").click().run()
    assert instaladas == [nova]
    assert any("Versão 99.0.0 instalada" in s.value for s in at.success)
    assert any("Instalar.cmd" in w.value for w in at.warning)  # mudou o requirements: avisa para reinstalar


def test_atualizacao_que_falha_avisa_e_mantem_a_versao(monkeypatch):
    from creditos import atualizacao
    nova = {"versao": "99.0.0", "url": "https://exemplo/app.zip", "sha256": "a" * 64, "notas": ""}
    monkeypatch.setattr(atualizacao, "verificar", lambda *a, **k: (atualizacao.NOVA, nova))
    def falha(publicada):
        raise atualizacao.FalhaNaAtualizacao("o pacote baixado não confere com o hash publicado; nada foi instalado")
    monkeypatch.setattr(atualizacao, "instalar", falha)
    at = _tela_de_creditos().run()  # a consulta automática, ao abrir, já mostra o aviso
    _botao(at, "Baixar e instalar a versão nova").click().run()
    assert any("não foi instalada" in e.value and "não confere" in e.value for e in at.error)


def test_coleta_mostra_barra_de_progresso_e_pode_ser_parada(tmp_path, monkeypatch):
    import time
    from creditos import andamento, pipeline
    from tests.test_creditos import relatorio_de_teste

    monkeypatch.chdir(tmp_path)

    def executar_lento(relatorio, config, pasta, coautores=(), ao_avancar=None, **_):
        for i in range(1, 2000):
            ao_avancar(f"Deezer: lendo álbum {i}/2000")
            time.sleep(0.01)
        return [pipeline.Coleta("DEEZER")]

    monkeypatch.setattr(pipeline, "executar", executar_lento)
    at = _tela_de_creditos()
    at.session_state["relatorio"] = relatorio_de_teste()
    at.session_state["etapa"] = ETAPA_DOS_PRINTS
    at.session_state["conferidos"] = CONFERIDOS
    at.run()
    _botao(at, "Coletar e classificar").click().run()
    assert not at.exception
    tarefa = andamento.atual()
    assert tarefa is not None and tarefa.viva
    # Em curso: a barra, a etapa e o botão de parar; o botão de coletar some, para não abrir duas coletas.
    assert any(texto.startswith("Etapa 1 de") and "Deezer: lendo álbum" in texto for texto in (c.value for c in at.caption))
    assert any("Deezer: em andamento" in c.value for c in at.caption)
    assert "Coletar e classificar" not in [b.label for b in at.button]
    # Recarregar a página (sessão nova, sem relatório) reencontra a coleta em curso.
    outra = _tela_de_creditos().run()
    assert not outra.exception and "Parar coleta" in [b.label for b in outra.button]
    _botao(at, "Parar coleta").click().run()
    assert any("Parando" in w.value for w in at.warning)
    tarefa.linha.join(5)
    assert tarefa.interrompida
    at.run()
    assert any("Coleta interrompida" in w.value for w in at.warning) and "coletas" not in at.session_state
    assert "Coletar e classificar" in [b.label for b in at.button] and andamento.atual() is None


def test_coleta_que_falha_mostra_o_erro_e_deixa_tentar_de_novo(tmp_path, monkeypatch):
    from creditos import pipeline
    from tests.test_creditos import relatorio_de_teste

    monkeypatch.chdir(tmp_path)

    def quebra(*a, **k):
        raise RuntimeError("navegador não abriu")
    monkeypatch.setattr(pipeline, "executar", quebra)
    at = _tela_de_creditos()
    at.session_state["relatorio"] = relatorio_de_teste()
    at.session_state["etapa"] = ETAPA_DOS_PRINTS
    at.session_state["conferidos"] = CONFERIDOS
    at.run()
    _botao(at, "Coletar e classificar").click().run()
    assert any("A coleta não terminou: RuntimeError: navegador não abriu" in e.value for e in at.error)
    assert "coletas" not in at.session_state


def test_etapa_de_interpretes_devolve_so_a_planilha_de_obra_e_interprete(tmp_path, monkeypatch):
    import io
    import openpyxl
    from creditos import classificador as c, interpretes, pipeline
    from tests.test_creditos import relatorio_de_teste

    monkeypatch.chdir(tmp_path)
    chamadas = []

    def executar_falso(relatorio, config, pasta, coautores=(), plataformas=None, prints=True, ao_avancar=None, **_):
        chamadas.append((plataformas, prints, config.nomes_confirmados, list(coautores)))
        ao_avancar("Deezer: lendo álbum 1/1")
        def g(obra, interprete, status=c.OK):
            return pipeline.Gravacao(plataforma="DEEZER", link=f"https://www.deezer.com/track/{len(obra)}{len(interprete)}", titulo=obra,
                                     interprete=interprete, obra=obra, classificacao=c.Classificacao(status, "motivo"))
        return [pipeline.Coleta("DEEZER", gravacoes=[
            g("LIGUE O RADIO", "Banda do Baile"), g("LIGUE O RADIO", "Banda do Baile"), g("LIGUE O RADIO", "Fulana & Beltrano", c.SEM_CREDITOS),
            g("LIGUE O RADIO", "Cantor Famoso", c.HOMONIMA),  # homônimo de terceiro: não é intérprete do titular
            g("COISA FEITA; LIGUE O RADIO", "Fulana e Beltrano"),  # medley: conta para as duas obras; "e" e "&" são o mesmo nome
        ])]

    monkeypatch.setattr(pipeline, "executar", executar_falso)
    at = _tela_de_creditos()
    at.session_state["relatorio"] = relatorio_de_teste()
    at.run()
    # A primeira etapa é a de intérpretes: sem opções de plataforma, de print ou de navegador.
    assert at.button_group[0].value == ETAPA_DOS_INTERPRETES and not at.exception
    assert "Coletar e classificar" not in [b.label for b in at.button] and not at.multiselect and not at.checkbox
    _botao(at, "Buscar intérpretes").click().run()
    assert not at.exception
    assert chamadas == [(interpretes.PLATAFORMAS, False, ["ZECA LIMA", "ZECA DO BAILE"], ["CIDA DIAS"])]  # sem navegador, sem print
    lista = at.session_state["lista_de_interpretes"]
    # Banda do Baile tem o titular no crédito: certeza. A dupla só aparece sem crédito e num medley: fica em dúvida.
    assert lista[0] == ("LIGUE O RADIO", ["Banda do Baile", "Fulana & Beltrano"], {"Fulana & Beltrano"})
    assert lista[1] == ("COISA FEITA", ["Fulana e Beltrano"], {"Fulana e Beltrano"})
    assert any("Em vermelho" in m.value and "os 2 que ele só supõe" in m.value for m in at.markdown)
    assert any("2 de 5 obras com intérprete encontrado" in cap.value for cap in at.caption)
    # A planilha: nome do titular, cabeçalho, uma linha por par, e obra sem intérprete em branco. Nada além disso.
    ws = openpyxl.load_workbook(io.BytesIO(interpretes.planilha(relatorio_de_teste(), lista))).active
    linhas = [tuple(l[:2]) for l in ws.iter_rows(values_only=True)]
    assert linhas[:5] == [("ZECA LIMA", None), ("Obras", "Intérpretes"), ("LIGUE O RADIO", "BANDA DO BAILE"),
                          ("LIGUE O RADIO", "FULANA & BELTRANO"), ("COISA FEITA", "FULANA E BELTRANO")]
    assert linhas[5:] == [("BAILE NA SERRA", None), ("A DANCA DA PANELA", None), ("DANCA DA PANELLA", None)]
    # Em preto, o que o app tem certeza; em vermelho, o que ele só supõe; e uma legenda, fora das duas colunas.
    cor = lambda linha: (ws.cell(linha, 2).font.color.rgb if ws.cell(linha, 2).font.color else "preto")
    assert interpretes.VERMELHO not in str(cor(3)) and interpretes.VERMELHO in cor(4) and interpretes.VERMELHO in cor(5)
    assert "Em vermelho" in ws.cell(1, 4).value and ws.cell(2, 3).value is None
    # A etapa 2 continua lá, com a coleta de sempre.
    at.button_group[0].set_value(ETAPA_DOS_PRINTS).run()
    assert "Buscar intérpretes" not in [b.label for b in at.button] and "Coletar e classificar" in [b.label for b in at.button]


def test_planilha_de_interpretes_volta_a_ser_lida_depois_de_corrigida_pelo_compositor():
    import io
    import openpyxl
    from creditos import interpretes
    from tests.test_creditos import relatorio_de_teste

    rel = relatorio_de_teste()
    livro = openpyxl.load_workbook(io.BytesIO(interpretes.planilha(rel, [("LIGUE O RADIO", ["Banda do Baile"]), ("COISA FEITA", [])])))
    ws = livro.active
    ws.cell(4, 2, "Cantora Original")                       # o compositor preenche a obra que estava em branco
    ws.append(["Ligue o Rádio", "banda do baile"])          # repete um par, com outra grafia
    ws.append(["Ligue o Rádio", "Fulana & Beltrano"])       # acrescenta um intérprete
    ws.append(["Música Que Não É Dele", "Alguém"])          # e uma obra que não está no relatório
    saida = io.BytesIO(); livro.save(saida)
    pares, fora = interpretes.ler_planilha(saida.getvalue(), rel)
    assert pares == {"LIGUE O RADIO": ["BANDA DO BAILE", "Fulana & Beltrano"], "COISA FEITA": ["Cantora Original"]}
    assert fora == ["Música Que Não É Dele"]


def test_busca_de_interpretes_tem_barra_propria_e_nao_mistura_com_a_coleta(tmp_path, monkeypatch):
    import time
    from creditos import andamento, pipeline
    from tests.test_creditos import relatorio_de_teste

    monkeypatch.chdir(tmp_path)

    def executar_lento(relatorio, config, pasta, coautores=(), ao_avancar=None, **_):
        for i in range(1, 2000):
            ao_avancar(f"Deezer: lendo álbum {i}/2000")
            time.sleep(0.01)
        return []

    monkeypatch.setattr(pipeline, "executar", executar_lento)
    at = _tela_de_creditos()
    at.session_state["relatorio"] = relatorio_de_teste()
    at.run()
    _botao(at, "Buscar intérpretes").click().run()
    tarefa = andamento.atual()
    assert tarefa.viva and tarefa.dados["tipo"] == "interpretes" and tarefa.andamento.etapas == ["deezer", "apple"]  # sem etapa de prints
    assert "Parar busca" in [b.label for b in at.button] and any("Deezer: em andamento · Apple Music: na fila" == cap.value for cap in at.caption)
    # Na etapa 2, enquanto a busca roda, não dá para começar uma coleta.
    at.button_group[0].set_value(ETAPA_DOS_PRINTS).run()
    assert "Coletar e classificar" not in [b.label for b in at.button] and any("busca de intérpretes em andamento" in i.value for i in at.info)
    at.button_group[0].set_value(ETAPA_DOS_INTERPRETES).run()
    _botao(at, "Parar busca").click().run()
    tarefa.linha.join(5)
    at.run()
    assert any("Busca interrompida" in w.value for w in at.warning) and andamento.atual() is None


def test_etapa_dos_prints_usa_a_planilha_conferida_se_houver_e_funciona_sem_ela(tmp_path, monkeypatch):
    from creditos import pipeline
    from tests.test_creditos import relatorio_de_teste

    monkeypatch.chdir(tmp_path)
    recebidos = []
    monkeypatch.setattr(pipeline, "executar", lambda *a, conferidos=None, **k: recebidos.append(conferidos) or [pipeline.Coleta("DEEZER")])
    at = _tela_de_creditos()
    at.session_state["relatorio"] = relatorio_de_teste()
    at.session_state["etapa"] = ETAPA_DOS_PRINTS
    at.run()
    # Sem a planilha: a coleta está liberada, e o app descobre os intérpretes sozinho. A tela só explica a diferença.
    assert not at.exception and any("descobre sozinho" in cap.value for cap in at.caption)
    assert any("para as 5 obras" in i.value for i in at.info)
    _botao(at, "Coletar e classificar").click().run()
    assert recebidos == [None]
    # Com a planilha conferida: mostra o que foi lido, e a coleta recebe exatamente esses pares.
    at.session_state["conferidos"] = CONFERIDOS
    at.run()
    assert any("2 intérpretes em 2 obras" in m.value and "outras 3 obras" in m.value for m in at.success)
    assert any("para as 2 obras" in i.value for i in at.info)  # a estimativa de tempo conta só as obras com intérprete
    _botao(at, "Coletar e classificar").click().run()
    assert recebidos == [None, CONFERIDOS]


def test_as_duas_abas_aparecem_antes_de_enviar_qualquer_arquivo():
    at = _tela_de_creditos().run()
    assert not at.exception and at.button_group[0].options == [ETAPA_DOS_INTERPRETES, ETAPA_DOS_PRINTS]
    assert at.button_group[0].value == ETAPA_DOS_INTERPRETES and any("planilha de intérpretes" in c.value for c in at.caption)
    at.button_group[0].set_value(ETAPA_DOS_PRINTS).run()
    assert any("lista da petição" in c.value for c in at.caption) and any("Envie o relatório" in i.value for i in at.info)
