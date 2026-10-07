"""Roda o app.py de ponta a ponta, sem navegador e sem rede."""

from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from cantor.busca import Buscador, Resultado


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
    at.run()
    assert at.multiselect[0].value == ["deezer", "youtube", "spotify", "tidal", "apple", "vagalume"]  # todas, por padrão
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
        return at.run()

    assert not abrir().number_input  # instalação normal: sempre todas as obras
    monkeypatch.setenv("QUEMCANTA_DEV", "1")
    at = abrir()
    at.number_input[0].set_value(2).run()
    next(b for b in at.button if b.label == "Coletar e classificar").click().run()
    at.radio[0].set_value("as últimas").run()
    next(b for b in at.button if b.label == "Coletar e classificar").click().run()
    assert limites == [2, -2]


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
    at.run()
    _botao(at, "Coletar e classificar").click().run()
    assert any("A coleta não terminou: RuntimeError: navegador não abriu" in e.value for e in at.error)
    assert "coletas" not in at.session_state
