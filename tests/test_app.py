"""Roda o app.py de ponta a ponta, sem navegador e sem rede."""

import pandas as pd
from streamlit.testing.v1 import AppTest

from cantor.busca import Buscador, Resultado


def test_modo_relatorio_aparece_na_tela_e_pode_ser_desligado(tmp_path, monkeypatch):
    monkeypatch.setattr("cantor.banco.CAMINHO_PADRAO", tmp_path / "app.db")
    monkeypatch.setattr("cantor.busca.configurar_log", lambda: None)
    preparados = []
    monkeypatch.setattr(
        Buscador, "preparar_relatorio",
        lambda self, nome: preparados.append(nome) or {"obras": [], "erro": "", "segundos": 0.0},
    )
    monkeypatch.setattr(Buscador, "resolver", lambda self, titulo, compositor="", iswc="": Resultado("Alguém", "media"))

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


def test_fluxo_processar_e_previa(tmp_path, monkeypatch):
    monkeypatch.setattr("cantor.banco.CAMINHO_PADRAO", tmp_path / "app.db")
    respostas = {
        "Wave": Resultado("Tom Jobim", "alta", [], "https://musicbrainz.org/recording/x", "obra"),
        "Amor": Resultado("Fulano", "baixa", ["Beltrano"], "https://musicbrainz.org/recording/y", "3 artistas"),
        "Nada": Resultado(observacao="sem resultados"),
    }
    monkeypatch.setattr("cantor.busca.configurar_log", lambda: None)  # não escreve no log de verdade
    monkeypatch.setattr(Buscador, "resolver", lambda self, titulo, compositor="", iswc="": respostas[titulo])

    at = AppTest.from_file("../app.py", default_timeout=30)
    at.session_state["original"] = pd.DataFrame(
        {"Música": ["Wave", "Amor", "Nada"], "Autor": ["Tom Jobim", "", ""]}, dtype=str
    )
    at.run()
    assert not at.exception
    assert [s.value for s in at.selectbox] == ["Música", "Autor", "(nenhuma)", "(nenhuma)"]

    next(b for b in at.button if b.label == "Processar").click().run()
    assert not at.exception
    resultado = at.session_state["resultado"]
    assert list(resultado["cantor_sugerido"]) == ["Tom Jobim", "Fulano", ""]
    assert list(resultado["confianca"]) == ["alta", "baixa", "nao_encontrado"]
    assert {m.label: m.value for m in at.metric} == {"Alta": "1", "Média": "0", "Baixa": "1", "Não encontrado": "1"}
