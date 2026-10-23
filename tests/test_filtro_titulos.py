"""O "%" não é vigiado pelo filtro (decisão do dono, 23/10/2026); percentual do cadastro continua sem sair."""

import pytest

from creditos.filtro import DadoSensivel, FiltroDeSaida


def _filtro():
    return FiltroDeSaida(termos={"50,00", "12345.67.89.01"}, liberados=["100% VOCE", "Zeca Lima"])


def test_porcentagem_em_qualquer_texto_pode_sair():
    filtro = _filtro()
    for texto in ("100% VOCE Zeca Lima", "Grupo 10 %", "https://www.exemplo.com/search/100%25%20VOCE/tracks", "musica 100%", "25%"):
        assert filtro.motivo(texto) == "", texto
    filtro.conferir("100% VOCE", "Outro Grupo 100%")


def test_percentual_do_cadastro_e_codigos_continuam_barrados():
    filtro = _filtro()
    assert filtro.motivo("100% VOCE 50,00") == "código, data ou percentual do cadastro"
    with pytest.raises(DadoSensivel):
        filtro.conferir("Zeca Lima 12345.67.89.01")


def test_busca_barrada_no_spotify_so_pula_aquela_busca():
    from creditos import pipeline
    from creditos.classificador import Config
    from tests.test_creditos import relatorio_de_teste

    class SpotifyQueBarra:
        so_o_que_ja_foi_lido = False
        nav = type("Nav", (), {"navegacoes": 0})()

        def buscar(self, consulta):
            raise DadoSensivel("editora ou administradora")

    coleta = pipeline.coletar_spotify(relatorio_de_teste(), Config(nomes_confirmados=["ZECA LIMA"]), SpotifyQueBarra(),
                                      pares={"LIGUE O RADIO": ["Banda do Baile"]}, limite_de_obras=1)
    assert not coleta.interrompida and any("o filtro de saída barrou (editora ou administradora)" in a for a in coleta.avisos)
