"""Dois navegadores do app abertos ao mesmo tempo na mesma linha de execução, como na coleta completa.

Na 0.5.73 e na 0.5.74, o navegador da Amazon pelo site (o Google Chrome, para a música tocar) abria um segundo
Playwright com o navegador comum (Spotify, Tidal, Palco MP3) ainda aberto. O Playwright recusa ("Playwright Sync API
inside the asyncio loop") e a Amazon saía sem nenhuma leitura. O teste da época abria só a Amazon, e passou.

Estes testes abrem navegadores de verdade, invisíveis, sem internet (about:blank).
"""

import pytest

from creditos.navegador import Navegador


def _abre(nav):
    nav.pagina.goto("about:blank")
    return nav.pagina.evaluate("1 + 1") == 2


@pytest.fixture(autouse=True)
def _precisa_do_navegador_do_app():
    try:
        with Navegador(visivel=False) as nav:
            nav.pagina
    except Exception as e:  # sem o Chromium do Playwright instalado neste computador
        pytest.skip(f"navegador do app indisponível: {type(e).__name__}")


def test_segundo_navegador_abre_junto_do_primeiro_ja_aberto():
    with Navegador(visivel=False) as nav:
        assert _abre(nav)  # o Spotify já usou o navegador comum
        with Navegador(visivel=False, junto_de=nav) as da_amazon:
            assert _abre(da_amazon)
        assert _abre(nav)  # fechar o da Amazon não derruba o comum (o Palco MP3 ainda usa)


def test_segundo_navegador_abre_antes_do_primeiro():
    with Navegador(visivel=False) as nav:
        with Navegador(visivel=False, junto_de=nav) as da_amazon:
            assert _abre(da_amazon)  # a Amazon foi a primeira a precisar de janela
        assert _abre(nav)


def test_sem_junto_de_o_segundo_navegador_falha():
    """O erro da coleta do dia 03/11, para não voltar sem querer."""
    with Navegador(visivel=False) as nav:
        assert _abre(nav)
        with Navegador(visivel=False) as outro:
            with pytest.raises(Exception, match="asyncio loop"):
                outro.pagina


def test_coleta_abre_a_amazon_junto_do_navegador_comum():
    import inspect

    from creditos import pipeline

    # Sem o junto_de, a Amazon volta a sair vazia em toda coleta completa
    assert "toca_musica=True, junto_de=nav" in inspect.getsource(pipeline)
