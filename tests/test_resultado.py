"""O resultado gravado no disco pela própria tarefa, e o cache que não quebra a coleta quando fica pela metade."""

import io
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from creditos import andamento, classificador as c, disco, pipeline, resultado
from tests.test_creditos import relatorio_de_teste

ETAPA_DOS_INTERPRETES, ETAPA_DOS_PRINTS = "Buscar intérpretes", "Coleta de prints"
CONFERIDOS = {"LIGUE O RADIO": ["Banda do Baile"], "COISA FEITA": ["Cantora Original"]}


@pytest.fixture(autouse=True)
def _isolado(tmp_path, monkeypatch):
    monkeypatch.setenv("QUEMCANTA_DADOS", str(tmp_path / "dados"))
    monkeypatch.setenv("QUEMCANTA_ATUALIZACAO", "")
    monkeypatch.setattr("creditos.atualizacao.endereco_de_consulta", lambda pasta=None: "")
    monkeypatch.setattr("creditos.andamento._ATUAL", None)
    monkeypatch.chdir(tmp_path)


def _tela():
    return AppTest.from_file(str(Path(__file__).parent.parent / "creditos_app.py"), default_timeout=60)


def _botao(at, rotulo):
    return next(b for b in at.button if b.label == rotulo)


def _esperar(at):
    """A tarefa roda em segundo plano e, no fim, grava o resultado no disco: espera ela acabar e refaz a tela."""
    from creditos import andamento

    tarefa = andamento.atual()
    if tarefa is not None:
        tarefa.linha.join(30)
        at.run()


def _salvos(tmp_path):
    return sorted(p.name for p in (tmp_path / "dados").glob("casos/*/resultado/*"))


def _coleta_do_spotify():
    g = pipeline.Gravacao(plataforma="SPOTIFY", link="https://open.spotify.com/intl-pt/track/x1", titulo="Ligue o Rádio",
                          interprete="Banda do Baile", obra="LIGUE O RADIO", classificacao=c.Classificacao(c.SEM_CREDITOS, "motivo"))
    return pipeline.Coleta("SPOTIFY", gravacoes=[g])


# --- disco ----------------------------------------------------------------------


def test_cache_vazio_ou_quebrado_e_descartado_em_vez_de_derrubar(tmp_path):
    vazio, quebrado = tmp_path / "vazio.json", tmp_path / "quebrado.json"
    vazio.write_text("")
    quebrado.write_text('{"a": [1, 2')
    assert disco.ler_json(vazio, {}) == {} and not vazio.exists()
    assert disco.ler_json(quebrado) is None and not quebrado.exists()
    assert disco.ler_json(tmp_path / "nao-existe.json", {"x": 1}) == {"x": 1} and disco.ler_json(None, []) == []


def test_gravacao_troca_o_arquivo_inteiro_e_nao_deixa_temporario(tmp_path):
    arquivo = tmp_path / "sub" / "dados.json"
    disco.gravar_json(arquivo, {"obra": "Ligue o Rádio"})
    disco.gravar_json(arquivo, {"obra": "Coisa Feita"})
    assert disco.ler_json(arquivo) == {"obra": "Coisa Feita"}
    assert [p.name for p in arquivo.parent.iterdir()] == ["dados.json"]


def test_deezer_com_cache_vazio_le_de_novo_em_vez_de_quebrar(tmp_path):
    from creditos.deezer import Deezer

    deezer = Deezer(cache=tmp_path / "deezer")
    (tmp_path / "deezer").mkdir()
    (tmp_path / "deezer" / "123.json").write_text("")  # o app fechou no meio da gravação
    (tmp_path / "deezer" / "_buscas.json").write_text("")
    assert deezer._do_cache("123") is None
    deezer._paginas = lambda caminho, maximo=1000, **p: [{"id": 9, "title": "Ligue o Rádio", "artist": {"name": "Banda"}, "album": {"id": 5}}]
    assert deezer.buscar_faixas("ligue o radio")[0]["album_id"] == "5"
    assert disco.ler_json(tmp_path / "deezer" / "_buscas.json")["ligue o radio"][0]["interprete"] == "Banda"


# --- pipeline: o que já terminou sai junto com a parada ---------------------------


def test_coleta_que_para_no_meio_devolve_as_plataformas_que_ja_terminaram(tmp_path, monkeypatch):
    from creditos.classificador import Config

    monkeypatch.setattr(pipeline, "coletar_deezer", lambda *a, **k: pipeline.Coleta("DEEZER"))

    def apple_parada(*a, ao_avancar=None, **k):
        raise andamento.Interrompida()

    monkeypatch.setattr(pipeline, "coletar_apple", apple_parada)
    with pytest.raises(andamento.Interrompida) as parada:
        pipeline.executar(relatorio_de_teste(), Config(nomes_confirmados=["ZECA LIMA"]), tmp_path / "caso",
                          plataformas=("deezer", "apple"), prints=False)
    assert [k.plataforma for k in parada.value.coletas_parciais] == ["DEEZER"]


# --- resultado ------------------------------------------------------------------------


def test_coleta_grava_planilha_e_zip_com_data_e_sem_apagar_a_anterior(tmp_path):
    rel, quando = relatorio_de_teste(), datetime(2026, 10, 23, 14, 5)
    salvo = resultado.salvar_coleta(rel, [_coleta_do_spotify()], tmp_path / "caso", agora=quando)
    de_novo = resultado.salvar_coleta(rel, [_coleta_do_spotify()], tmp_path / "caso", agora=quando, parcial=True)
    nomes = sorted(p.name for p in (tmp_path / "caso" / "resultado").iterdir())
    assert nomes == ["Planilha de Obras - ZECA LIMA - 2026-10-23 14h05 - PARCIAL.xlsx", "Planilha de Obras - ZECA LIMA - 2026-10-23 14h05.xlsx",
                     "Provas - ZECA LIMA - 2026-10-23 14h05 - PARCIAL.zip", "Provas - ZECA LIMA - 2026-10-23 14h05.zip"]
    assert Path(salvo["arquivos"][1]).read_bytes() == salvo["pacote"] and de_novo["parcial"]
    assert "Planilha de Obras.xlsx" in zipfile.ZipFile(io.BytesIO(salvo["pacote"])).namelist()
    # mesmo minuto, mesmo tipo: não sobrescreve
    resultado.salvar_coleta(rel, [_coleta_do_spotify()], tmp_path / "caso", agora=quando)
    assert (tmp_path / "caso" / "resultado" / "Provas - ZECA LIMA - 2026-10-23 14h05 (2).zip").exists()


def test_falha_ao_gravar_nao_derruba_a_coleta():
    painel = andamento.Andamento(1, ("deezer",))

    def quebra(produto, parcial):
        raise OSError("disco cheio")

    assert resultado.rodar_e_salvar(painel, lambda: ["coletas"], quebra) == ["coletas"]
    assert painel.salvo is None and painel.erro_ao_salvar == "OSError: disco cheio"


# --- na tela ---------------------------------------------------------------------------


def test_coleta_terminada_ja_fica_salva_no_disco(tmp_path, monkeypatch):
    def executar_falso(relatorio, config, pasta, coautores=(), ao_avancar=None, **_):
        ao_avancar("Spotify: lendo faixa 1/1")
        return [_coleta_do_spotify()]

    monkeypatch.setattr(pipeline, "executar", executar_falso)
    at = _tela()
    at.session_state["relatorio"] = relatorio_de_teste()
    at.session_state["etapa"] = ETAPA_DOS_PRINTS
    at.session_state["conferidos"] = CONFERIDOS
    at.run()
    _botao(at, "Coletar e classificar").click().run()
    _esperar(at)
    assert not at.exception
    salvos = _salvos(tmp_path)
    assert len(salvos) == 2 and salvos[0].startswith("Planilha de Obras - ZECA LIMA - ") and salvos[1].endswith(".zip")
    zip_salvo = next((tmp_path / "dados").glob("casos/*/resultado/*.zip"))
    assert zip_salvo.read_bytes() == at.session_state["saidas"]["pacote"]  # o do disco é o mesmo do botão
    assert any("Resultado salvo em" in s.value and "Não precisa clicar em baixar" in s.value for s in at.success)
    assert "Abrir pasta" in [b.label for b in at.button]


def test_coleta_parada_grava_o_parcial(tmp_path, monkeypatch):
    def executar_lento(relatorio, config, pasta, coautores=(), ao_avancar=None, **_):
        try:
            for i in range(1, 2000):
                ao_avancar(f"Spotify: lendo faixa {i}/2000")
                time.sleep(0.01)
        except BaseException as e:
            e.coletas_parciais = [_coleta_do_spotify()]  # o Spotify já tinha terminado, como no pipeline de verdade
            raise
        return []

    monkeypatch.setattr(pipeline, "executar", executar_lento)
    at = _tela()
    at.session_state["relatorio"] = relatorio_de_teste()
    at.session_state["etapa"] = ETAPA_DOS_PRINTS
    at.session_state["conferidos"] = CONFERIDOS
    at.run()
    _botao(at, "Coletar e classificar").click().run()
    tarefa = andamento.atual()
    _botao(at, "Parar coleta").click().run()
    tarefa.linha.join(5)
    at.run()
    assert any("Coleta interrompida" in w.value for w in at.warning)
    assert [n for n in _salvos(tmp_path) if n.endswith("PARCIAL.zip")]
    assert any("parcial" in i.value for i in at.info)


def test_coleta_que_terminou_sem_ninguem_olhando_aparece_ao_recarregar(tmp_path, monkeypatch):
    solta = threading.Event()

    def executar_que_espera(relatorio, config, pasta, coautores=(), ao_avancar=None, **_):
        ao_avancar("Spotify: lendo faixa 1/1")
        solta.wait(10)
        return [_coleta_do_spotify()]

    monkeypatch.setattr(pipeline, "executar", executar_que_espera)
    at = _tela()
    at.session_state["relatorio"] = relatorio_de_teste()
    at.session_state["etapa"] = ETAPA_DOS_PRINTS
    at.session_state["conferidos"] = CONFERIDOS
    at.run()
    _botao(at, "Coletar e classificar").click().run()
    tarefa = andamento.atual()
    solta.set()  # a coleta termina enquanto a aba está fechada (nenhuma sessão olhando)
    tarefa.linha.join(5)
    assert len(_salvos(tmp_path)) == 2  # já está no disco, antes de qualquer tela abrir
    outra = _tela().run()  # a aba é aberta de novo: sessão nova, sem relatório
    assert not outra.exception
    assert outra.session_state["coletas"] and "Resultado" in [s.value for s in outra.subheader]
    assert any("Resultado salvo em" in s.value for s in outra.success) and andamento.atual() is None


def test_busca_de_interpretes_grava_a_planilha_sozinha(tmp_path, monkeypatch):
    def executar_falso(relatorio, config, pasta, coautores=(), ao_avancar=None, **_):
        ao_avancar("Deezer: lendo álbum 1/1")
        g = pipeline.Gravacao(plataforma="DEEZER", link="https://www.deezer.com/track/1", titulo="LIGUE O RADIO",
                              interprete="Banda do Baile", obra="LIGUE O RADIO", classificacao=c.Classificacao(c.OK, "motivo"))
        return [pipeline.Coleta("DEEZER", gravacoes=[g])]

    monkeypatch.setattr(pipeline, "executar", executar_falso)
    at = _tela()
    at.session_state["relatorio"] = relatorio_de_teste()
    at.run()
    _botao(at, "Buscar intérpretes").click().run()
    _esperar(at)
    assert not at.exception
    salvos = _salvos(tmp_path)
    assert len(salvos) == 1 and salvos[0].startswith("Intérpretes - ZECA LIMA - ") and salvos[0].endswith(".xlsx")
    assert any("Resultado salvo em" in s.value for s in at.success)


def test_erro_na_coleta_vai_para_o_registro_com_a_linha(tmp_path, monkeypatch):
    def quebra(*a, **k):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(pipeline, "executar", quebra)
    at = _tela()
    at.session_state["relatorio"] = relatorio_de_teste()
    at.run()
    _botao(at, "Buscar intérpretes").click().run()
    _esperar(at)
    assert any("A busca não terminou" in e.value for e in at.error)
    registro = (tmp_path / "dados" / "erros.log").read_text(encoding="utf-8")
    assert "Traceback" in registro and "def quebra" not in registro and "raise ValueError" in registro
