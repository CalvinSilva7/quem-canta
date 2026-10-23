"""O "%" que faz parte de um título ou de um nome é público; percentual do cadastro continua sem sair."""

import pytest

from creditos.filtro import DadoSensivel, FiltroDeSaida


def _filtro():
    return FiltroDeSaida(termos={"50,00", "12345.67.89.01"}, liberados=["100% VOCE", "Grupo 10 %", "Zeca Lima"])


def test_percentual_do_titulo_ou_do_nome_pode_sair_em_qualquer_forma():
    filtro = _filtro()
    for texto in ("100% VOCE Zeca Lima", "100% você grupo 10%", "https://www.exemplo.com/search/100% VOCE/tracks", "100 % voce"):
        assert filtro.motivo(texto) == "", texto
    filtro.conferir("100% VOCE", "Grupo 10%")


def test_outro_percentual_e_o_cadastro_continuam_barrados():
    filtro = _filtro()
    assert filtro.motivo("100% VOCE 50%") == "percentual"  # o 50% não está em título nem nome
    assert filtro.motivo("1100% VOCE") == "percentual"  # número diferente, mesmo fim
    assert filtro.motivo("100% VOCE 50,00") == "código, data ou percentual do cadastro"
    assert FiltroDeSaida().motivo("musica 100%") == "percentual"  # sem título liberado, nada muda
    with pytest.raises(DadoSensivel):
        filtro.conferir("Zeca Lima 25%")


def test_busca_barrada_no_spotify_so_pula_aquela_busca(monkeypatch):
    from creditos import pipeline
    from creditos.classificador import Config
    from tests.test_creditos import relatorio_de_teste

    class SpotifyQueBarra:
        so_o_que_ja_foi_lido = False
        nav = type("Nav", (), {"navegacoes": 0})()

        def buscar(self, consulta):
            raise DadoSensivel("percentual")

    coleta = pipeline.coletar_spotify(relatorio_de_teste(), Config(nomes_confirmados=["ZECA LIMA"]), SpotifyQueBarra(),
                                      pares={"LIGUE O RADIO": ["Banda do Baile"]}, limite_de_obras=1)
    assert not coleta.interrompida and any("o filtro de saída barrou (percentual)" in a for a in coleta.avisos)
