"""Testes sem rede: as APIs são substituídas por respostas fixas."""

import logging

import pandas as pd
import pytest

from cantor import planilha
from cantor.banco import Banco
from cantor.busca import FONTE_MANUAL, VERSAO_CACHE, Buscador, ErroDeRede
from cantor.iswc import normalizar_iswc
from cantor.matching import dividir_compositores, nomes_parecidos, normalizar, titulos_parecidos

JOBIM = {"id": "id-jobim", "name": "Antônio Carlos Jobim"}
VINICIUS = {"id": "id-vinicius", "name": "Vinicius de Moraes"}


def gravacao(id_, titulo, artista, data="", id_artista=None):
    credito = [{"name": artista, "artist": {"id": id_artista or f"id-{normalizar(artista)}"}}]
    return {"id": id_, "title": titulo, "first-release-date": data, "artist-credit": credito}


def obra(id_, titulo, *autores, score=100):
    return {
        "id": id_,
        "title": titulo,
        "score": score,
        "relations": [{"type": "composer", "artist": a} for a in autores],
    }


def lancamento(data, *tipos_secundarios):
    """O cliente só pede lançamentos com status Official, então todos aqui são oficiais."""
    return {"date": data, "status": "Official", "release-group": {"secondary-types": list(tipos_secundarios)}}


def faixa(titulo, artista, rank=100):
    return {"title": titulo, "artist": {"name": artista}, "rank": rank, "link": f"https://deezer.com/{artista}"}


class ClienteFalso:
    """`deezer` pode ser uma lista (vale para qualquer consulta) ou {consulta: lista}."""

    def __init__(self, obras=(), artistas=(), da_obra=(), por_titulo=(), deezer=(), falhar=False, deezer_falha=False,
                 lancamentos=None, catalogo=(), obras_iswc=(), credits=None, faixas_isrc=None,
                 credits_falha=False, isrc_falha=False):
        self.obras_iswc = list(obras_iswc)  # resposta da busca de obra por ISWC no MusicBrainz
        self.credits_resposta = credits  # None = ISWC inexistente no Credits.fm
        self.faixas_isrc = faixas_isrc or {}  # {isrc: faixa do Deezer}
        self.credits_falha, self.isrc_falha = credits_falha, isrc_falha
        self.catalogo = list(catalogo)  # obras devolvidas ao listar as obras de um artista
        # {id da gravação: [lançamentos]}; sem entrada, vale um álbum oficial na data da gravação.
        self.lancamentos = lancamentos or {}
        self.datas = {g["id"]: g["first-release-date"] for g in da_obra}
        self.respostas = {
            "work": {"works": list(obras)},
            "artist": {"artists": list(artistas)},
            "browse": {"recordings": list(da_obra), "recording-count": len(da_obra)},
            "recording": {"recordings": list(por_titulo)},
        }
        self.faixas = deezer
        self.falhar = falhar
        self.deezer_falha = deezer_falha
        self.chamadas = []  # (tipo, consulta)

    def mb(self, recurso, **parametros):
        if self.falhar:
            raise ErroDeRede("HTTP 503 após 4 tentativas")
        if recurso == "release":
            gravacao = parametros["recording"]
            self.chamadas.append(("release", gravacao))
            padrao = [lancamento(self.datas.get(gravacao, ""))]
            lista = self.lancamentos.get(gravacao, padrao)
            return {"releases": lista, "release-count": len(lista)}
        if recurso == "work" and "artist" in parametros:
            self.chamadas.append(("catalogo", parametros["artist"]))
            return {"works": self.catalogo, "work-count": len(self.catalogo)}
        if recurso == "work" and str(parametros.get("query", "")).startswith("iswc:"):
            self.chamadas.append(("iswc_mb", parametros["query"]))
            return {"works": self.obras_iswc}
        tipo = "browse" if "work" in parametros else recurso
        self.chamadas.append((tipo, parametros.get("query") or parametros.get("work", "")))
        return self.respostas[tipo]

    def deezer(self, **parametros):
        self.chamadas.append(("deezer", parametros["q"]))
        if self.deezer_falha:
            raise ErroDeRede("Timeout após 4 tentativas")
        if isinstance(self.faixas, dict):
            return {"data": list(self.faixas.get(parametros["q"], []))}
        return {"data": list(self.faixas)}

    def credits(self, caminho, **parametros):
        self.chamadas.append(("credits", caminho))
        if self.credits_falha:
            raise ErroDeRede("HTTP 503 após 4 tentativas")
        return self.credits_resposta

    def deezer_isrc(self, isrc):
        self.chamadas.append(("deezer_isrc", isrc))
        if self.isrc_falha:
            raise ErroDeRede("Timeout após 4 tentativas")
        return self.faixas_isrc.get(isrc)

    def tipos(self):
        return [tipo for tipo, _ in self.chamadas]


@pytest.fixture
def banco(tmp_path):
    return Banco(tmp_path / "teste.db")


# --- matching ---------------------------------------------------------------


def test_normalizar_tira_acento_caixa_e_pontuacao():
    assert normalizar("  Águas de MARÇO! ") == "aguas de marco"


def test_titulo_ignora_parenteses():
    assert titulos_parecidos("Trem das Onze (ao vivo)", "trem das onze")
    assert not titulos_parecidos("Do Jeito Que a Vida Quer / Trem das Onze", "Trem das Onze")


def test_nomes_parecidos():
    assert nomes_parecidos("Vinícius de Moraes", "vinicius de moraes")
    assert nomes_parecidos("Moraes, Vinicius de", "Vinicius de Moraes")
    assert nomes_parecidos("Vinicius", "Vinicius de Moraes")
    assert not nomes_parecidos("Tom Jobim", "Antônio Carlos Jobim")
    assert not nomes_parecidos("Chico Buarque", "Caetano Veloso")


def test_dividir_compositores():
    assert dividir_compositores("Compositores: Tom Jobim / Vinicius de Moraes") == ["Tom Jobim", "Vinicius de Moraes"]
    assert dividir_compositores("Luiz Gonzaga e Humberto Teixeira") == ["Luiz Gonzaga", "Humberto Teixeira"]
    assert dividir_compositores("") == []


# --- com compositor: busca da obra -----------------------------------------


def test_busca_de_obra_filtra_pelo_compositor_e_cai_para_busca_sem_filtro(banco):
    cliente = ClienteFalso()
    Buscador(banco, cliente).resolver("Hello", "Lionel Richie / Outro Autor")
    consultas = [consulta for tipo, consulta in cliente.chamadas if tipo == "work"]
    assert 'artist:("Lionel Richie" OR "Outro Autor")' in consultas[0]
    assert len(consultas) == 2 and "artist:" not in consultas[1]


def test_apelido_do_compositor_resolvido_por_alias(banco):
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        artistas=[{**JOBIM, "aliases": [{"name": "Tom Jobim"}]}],
        da_obra=[gravacao("r1", "Wave", "Antônio Carlos Jobim", "1967", id_artista="id-jobim")],
    )
    r = Buscador(banco, cliente).resolver("Wave", "Tom Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("Antônio Carlos Jobim", "alta")
    assert "artist" in cliente.tipos()


def test_varias_obras_compativeis_baixa_para_media(banco):
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM), obra("w2", "Wave", JOBIM, score=80)],
        da_obra=[gravacao("r1", "Wave", "Antônio Carlos Jobim", "1967", id_artista="id-jobim")],
    )
    assert Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim").confianca == "media"


# --- com compositor: confiança alta exige um segundo sinal -----------------

TRIO_ANTIGO = gravacao("r1", "Wave", "Trio Antigo", "1960")
ORIGINAL = gravacao("r2", "Wave", "Cantora Original", "1975")


def test_mais_antiga_sem_segundo_sinal_fica_media(banco):
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        da_obra=[TRIO_ANTIGO, ORIGINAL],
        deezer=[faixa("Wave", "Cantora Original", 900)],
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("Trio Antigo", "media")
    assert "sem confirmação" in r.observacao
    assert r.alternativas[0] == "Cantora Original"


def test_unica_gravacao_vinculada_nao_conta_como_mais_gravado(banco):
    # Todo mundo com 1 gravação "empata" em primeiro, mas isso não é sinal de nada.
    cliente = ClienteFalso(obras=[obra("w1", "Wave", JOBIM)], da_obra=[TRIO_ANTIGO])
    assert Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim").confianca == "media"


def test_sinal_compositor_da_alta(banco):
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        da_obra=[gravacao("r1", "Wave", "Tom Jobim", "1967", id_artista="id-jobim"), ORIGINAL],
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("Tom Jobim", "alta")
    assert "também é compositor" in r.observacao
    assert "deezer" not in cliente.tipos()  # sinal já resolvido sem consultar o Deezer


def test_sinal_mais_gravado_da_alta(banco):
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        da_obra=[TRIO_ANTIGO, gravacao("r3", "Wave", "Trio Antigo", "1990"), ORIGINAL],
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("Trio Antigo", "alta")
    assert "mais gravou" in r.observacao


def test_sinal_mais_popular_no_deezer_da_alta(banco):
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        da_obra=[TRIO_ANTIGO, ORIGINAL],
        deezer=[faixa("Wave", "Cantora Original", 300), faixa("Wave", "Trio Antigo", 900)],
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("Trio Antigo", "alta")
    assert "Deezer" in r.observacao


def test_compositor_mais_popular_no_deezer_vence_a_mais_antiga_sem_confirmacao(banco):
    # A gravação do próprio compositor não está vinculada à obra no MusicBrainz.
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        da_obra=[TRIO_ANTIGO],
        deezer=[faixa("Wave", "Antônio Carlos Jobim", 900), faixa("Wave", "Trio Antigo", 100)],
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("Antônio Carlos Jobim", "media")
    assert r.alternativas == ["Trio Antigo"]
    assert r.fonte_link.startswith("https://deezer.com")


def test_empate_de_data_prefere_quem_tambem_e_compositor(banco):
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        da_obra=[
            gravacao("r1", "Wave", "Trio Qualquer", "1963"),
            gravacao("r2", "Wave", "Tom Jobim", "1963", id_artista="id-jobim"),
        ],
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("Tom Jobim", "media")
    assert "empatada com Trio Qualquer" in r.observacao


def test_empate_de_data_sem_compositor_fica_media(banco):
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        da_obra=[gravacao("r1", "Wave", "Artista A", "1967"), gravacao("r2", "Wave", "Artista B", "1967-05-01")],
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert r.confianca == "media" and "empatada" in r.observacao


# --- com compositor: só lançamento oficial data a gravação -----------------


def _obra_com_gravacao_antiga_do_compositor(**lancamentos):
    return ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        da_obra=[
            gravacao("r1", "Wave", "Dupla Compositora", "2014-07-07", id_artista="id-jobim"),
            gravacao("r2", "Wave", "Banda Original", "2016-05-27"),
            gravacao("r3", "Wave", "Banda Original", "2019"),
            gravacao("r4", "Wave", "Regravação", "2021"),
        ],
        lancamentos=lancamentos,
    )


@pytest.mark.parametrize(
    "da_mais_antiga",
    [
        [lancamento("2014-07-07", "Demo")],
        [],  # nenhum lançamento com status Official: só bootleg ou promocional
    ],
)
def test_gravacao_so_em_demo_ou_lancamento_nao_oficial_nao_e_a_original(banco, da_mais_antiga):
    cliente = _obra_com_gravacao_antiga_do_compositor(r1=da_mais_antiga)
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("Banda Original", "alta")
    assert r.regra == "obra_mais_gravado+nao_oficial_ignorada"
    assert "Dupla Compositora, 2014-07-07" in r.observacao and "próxima oficial" in r.observacao
    # Confere a mais antiga e a seguinte; as posteriores não podem mudar a resposta.
    assert [g for tipo, g in cliente.chamadas if tipo == "release"] == ["r1", "r2"]


def test_ao_vivo_oficial_conta_para_a_data_mas_ser_compositor_nao_confirma(banco):
    cliente = _obra_com_gravacao_antiga_do_compositor(r1=[lancamento("2014-07-07", "Live")])
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca, r.regra) == ("Dupla Compositora", "media", "obra_sem_confirmacao+ao_vivo")
    assert "ao vivo oficial" in r.observacao and r.alternativas[0] == "Banda Original"


def test_ao_vivo_oficial_com_segundo_sinal_da_alta(banco):
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        da_obra=[
            gravacao("r1", "Wave", "Banda Acústica", "1997"),
            gravacao("r2", "Wave", "Banda Acústica", "2005"),
            gravacao("r3", "Wave", "Tom Jobim", "2001", id_artista="id-jobim"),
        ],
        lancamentos={"r1": [lancamento("1997", "Live")]},
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca, r.regra) == ("Banda Acústica", "alta", "obra_mais_gravado+ao_vivo")


def test_coletanea_oficial_conta_para_a_data(banco):
    cliente = _obra_com_gravacao_antiga_do_compositor(r1=[lancamento("2014-07-07", "Compilation")])
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca, r.regra) == ("Dupla Compositora", "alta", "obra_compositor")


def test_gravacao_que_saiu_ao_vivo_e_em_album_nao_e_tratada_como_ao_vivo(banco):
    cliente = _obra_com_gravacao_antiga_do_compositor(r1=[lancamento("2014-07-07", "Live"), lancamento("2014-09-01")])
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.regra) == ("Dupla Compositora", "obra_compositor")
    assert "2014-07-07" in r.observacao


def test_bootleg_anterior_ao_album_nao_antecipa_a_data(banco):
    # r2 circulou em bootleg em 2010 (o MusicBrainz informa 2010), mas oficialmente só saiu em 2016.
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        da_obra=[gravacao("r2", "Wave", "Banda Tardia", "2010"), gravacao("r1", "Wave", "Tom Jobim", "2012", id_artista="id-jobim")],
        lancamentos={"r2": [lancamento("2016-05-27")]},
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("Tom Jobim", "alta")


def test_lancamento_oficial_so_com_o_ano_conta_como_empate(banco):
    # O single oficial "2010" pode ter saído antes de 2010-11-01: não dá para dizer que o outro veio primeiro.
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        da_obra=[gravacao("r1", "Wave", "Tom Jobim", "2010-10-21", id_artista="id-jobim"), gravacao("r2", "Wave", "Outro Grupo", "2010-11-01")],
        lancamentos={"r1": [lancamento("2010-11-29"), lancamento("2010")]},
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca, r.regra) == ("Tom Jobim", "media", "obra_compositor+empate_compositor")


def test_limite_de_verificacoes_rebaixa_para_media(banco):
    antigas = [gravacao(f"r{i}", "Wave", f"Grupo Numero {i}", f"19{50 + i}") for i in range(9)]
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)],
        da_obra=antigas + [gravacao("r99", "Wave", "Tom Jobim", "1990", id_artista="id-jobim")],
        lancamentos={g["id"]: [lancamento(g["first-release-date"], "Demo")] for g in antigas},
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert r.confianca == "media" and "oficial_nao_verificado" in r.regra
    assert cliente.tipos().count("release") == 8


# --- modo relatório: todas as linhas são do mesmo compositor ---------------

ARTISTA_JOBIM = {**JOBIM, "aliases": [{"name": "Tom Jobim"}]}
DO_JOBIM = gravacao("r1", "Wave", "Tom Jobim", "1967", id_artista="id-jobim")


def _em_modo_relatorio(banco, cliente, nome="Antônio Carlos Jobim"):
    buscador = Buscador(banco, cliente)
    buscador.preparar_relatorio(nome)
    return buscador


def test_detecta_modo_relatorio_pelo_compositor_em_90_por_cento_das_linhas():
    mapa = {"titulo": "titulo", "compositor": "compositor", "creditos": None}
    df = pd.DataFrame({"titulo": list("abcdefghij"), "compositor": ["Fulano"] * 8 + ["FULANO / Parceiro", "Outro"]})
    assert planilha.compositor_do_relatorio(df, mapa) == "Fulano"
    df.loc[8, "compositor"] = "Mais Um"  # agora só 80% das linhas
    assert planilha.compositor_do_relatorio(df, mapa) is None
    assert planilha.compositor_do_relatorio(df.head(4), mapa) is None  # planilha pequena demais para concluir


def test_relatorio_ser_o_compositor_nao_e_segundo_sinal(banco):
    respostas = dict(obras=[obra("w1", "Wave", JOBIM)], artistas=[ARTISTA_JOBIM], da_obra=[DO_JOBIM, ORIGINAL])
    normal = Buscador(banco, ClienteFalso(**respostas)).resolver("Wave", "Antônio Carlos Jobim")
    assert (normal.confianca, normal.regra) == ("alta", "obra_compositor")
    # Mesmo banco: o cache do modo normal não pode ser reaproveitado no modo relatório.
    r = _em_modo_relatorio(banco, ClienteFalso(**respostas)).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca, r.regra) == ("Tom Jobim", "media", "obra_sem_confirmacao+relatorio")


def test_relatorio_compositor_mais_popular_nao_vence_a_mais_antiga(banco):
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM)], artistas=[ARTISTA_JOBIM], da_obra=[TRIO_ANTIGO],
        deezer=[faixa("Wave", "Antônio Carlos Jobim", 900), faixa("Wave", "Trio Antigo", 100)],
    )
    r = _em_modo_relatorio(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca, r.regra) == ("Trio Antigo", "media", "obra_sem_confirmacao+relatorio")


def test_relatorio_parceiro_continua_valendo_como_compositor(banco):
    cliente = ClienteFalso(
        obras=[obra("w1", "Wave", JOBIM, VINICIUS)], artistas=[ARTISTA_JOBIM],
        da_obra=[gravacao("r1", "Wave", "Vinicius de Moraes", "1967", id_artista="id-vinicius"), ORIGINAL],
    )
    r = _em_modo_relatorio(banco, cliente).resolver("Wave", "Antônio Carlos Jobim / Vinicius de Moraes")
    assert (r.cantor_sugerido, r.confianca, r.regra) == ("Vinicius de Moraes", "alta", "obra_compositor+relatorio")


def test_relatorio_compositor_nao_soma_pontos_na_busca_por_titulo(banco):
    respostas = dict(
        artistas=[ARTISTA_JOBIM],
        por_titulo=[gravacao("r1", "Wave", "Cantora Original"), gravacao("r2", "Wave", "Tom Jobim", id_artista="id-jobim")],
        deezer=[faixa("Wave", "Cantora Original"), faixa("Wave", "Tom Jobim")],
    )
    normal = Buscador(banco, ClienteFalso(**respostas)).resolver("Wave", "Antônio Carlos Jobim")
    assert (normal.cantor_sugerido, normal.confianca, normal.regra) == ("Tom Jobim", "media", "sem_obra_compositor+mb_deezer")
    cliente = ClienteFalso(**respostas)
    r = _em_modo_relatorio(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("Cantora Original", "baixa")
    assert r.regra == "sem_obra_palpite+mb_deezer+relatorio" and r.alternativas == ["Tom Jobim"]
    assert ("deezer", "Antônio Carlos Jobim Wave") not in cliente.chamadas  # sem busca dirigida pelo dono do relatório


def test_relatorio_compositor_so_serve_de_ultimo_desempate(banco):
    cliente = ClienteFalso(
        artistas=[ARTISTA_JOBIM],
        por_titulo=[gravacao("r1", "Wave", "Outra Cantora"), gravacao("r2", "Wave", "Tom Jobim", id_artista="id-jobim")],
    )
    r = _em_modo_relatorio(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca, r.regra) == ("Tom Jobim", "baixa", "sem_obra_palpite+mb+relatorio")


def test_relatorio_acha_a_obra_na_lista_do_compositor_e_evita_homonimo(banco):
    cliente = ClienteFalso(
        artistas=[ARTISTA_JOBIM],
        catalogo=[obra("w-dele", "Ainda Bem (versão do disco)", JOBIM), obra("w-outra-dele", "Outra Música", JOBIM)],
        obras=[obra("w-homonima", "Ainda Bem", VINICIUS)],  # o que a busca geral devolveria
        da_obra=[gravacao("r1", "Ainda Bem", "Cantora Original", "2011"), gravacao("r2", "Ainda Bem", "Cantora Original", "2015")],
    )
    r = _em_modo_relatorio(banco, cliente).resolver("Ainda bem", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("Cantora Original", "alta")
    assert r.regra == "obra_mais_gravado+catalogo+relatorio"
    assert ("browse", "w-dele") in cliente.chamadas and "work" not in cliente.tipos()  # sem busca geral


def test_relatorio_obra_sem_autor_cadastrado_fica_no_maximo_media(banco):
    cliente = ClienteFalso(
        artistas=[ARTISTA_JOBIM],
        catalogo=[{"id": "w1", "title": "Wave", "relations": []}],  # ligada a ele só por uma gravação
        da_obra=[gravacao("r1", "Wave", "Cantora Original", "2011"), gravacao("r2", "Wave", "Cantora Original", "2015")],
    )
    r = _em_modo_relatorio(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("Cantora Original", "media")
    assert r.regra == "obra_mais_gravado+catalogo_sem_autor+relatorio" and "sem autor cadastrado" in r.observacao


def test_relatorio_obra_de_outro_autor_que_ele_so_gravou_e_ignorada(banco):
    cliente = ClienteFalso(
        artistas=[ARTISTA_JOBIM],
        catalogo=[obra("w-alheia", "Wave", VINICIUS)],
        por_titulo=[gravacao("r1", "Wave", "Fulano")],
    )
    r = _em_modo_relatorio(banco, cliente).resolver("Wave", "Antônio Carlos Jobim")
    assert r.regra == "sem_obra_palpite+mb+relatorio" and "work" in cliente.tipos()  # caiu na busca geral


def test_lista_de_obras_do_compositor_fica_em_cache(banco):
    primeiro = ClienteFalso(artistas=[ARTISTA_JOBIM], catalogo=[obra("w1", "Wave", JOBIM)])
    assert len(_em_modo_relatorio(banco, primeiro).relatorio["obras"]) == 1
    segundo = ClienteFalso(artistas=[ARTISTA_JOBIM])
    assert len(_em_modo_relatorio(banco, segundo).relatorio["obras"]) == 1
    assert "catalogo" in primeiro.tipos() and "catalogo" not in segundo.tipos()


def test_falha_ao_listar_as_obras_nao_impede_o_modo_relatorio(banco):
    buscador = _em_modo_relatorio(banco, ClienteFalso(falhar=True))
    assert buscador.relatorio["erro"] and buscador.relatorio["obras"] == []


def test_gabarito_com_coluna_arquivo_usa_so_as_linhas_da_planilha():
    from avaliar import ESPERADO, juntar_gabarito

    df = pd.DataFrame({"id": ["1", "2"], "titulo": ["a", "b"]})
    gabarito = pd.DataFrame(
        {"arquivo": ["rel_x"] * 2 + ["rel_y"] * 2, "id": ["1", "2"] * 2, "cantor_esperado": ["A", "B", "C", "D"]}
    )
    assert list(juntar_gabarito(df, gabarito, nome_da_planilha="rel_y_com_cantor")[ESPERADO]) == ["C", "D"]


# --- com compositor, sem obra: o compositor continua valendo ---------------


def test_sem_obra_candidato_que_e_compositor_vence_com_media(banco):
    cliente = ClienteFalso(
        por_titulo=[gravacao("r1", "Garçom", "Banda Famosa"), gravacao("r2", "Garçom", "Fulano de Tal")],
        deezer={"Garçom": [faixa("Garçom", "Banda Famosa", 900)]},
    )
    r = Buscador(banco, cliente).resolver("Garçom", "Fulano de Tal")
    assert (r.cantor_sugerido, r.confianca) == ("Fulano de Tal", "media")
    assert "nenhuma obra" in r.observacao and "também é compositor" in r.observacao
    assert r.alternativas == ["Banda Famosa"]


def test_sem_obra_busca_no_deezer_com_o_compositor(banco):
    cliente = ClienteFalso(
        deezer={
            "O Sol": [faixa("O Sol", "Banda Famosa", 900)],
            "Fulano de Tal O Sol": [faixa("O Sol", "Fulano de Tal", 50), faixa("O Sol", "Outro Qualquer", 500)],
        }
    )
    r = Buscador(banco, cliente).resolver("O Sol", "Fulano de Tal")
    assert ("deezer", "Fulano de Tal O Sol") in cliente.chamadas
    assert (r.cantor_sugerido, r.confianca) == ("Fulano de Tal", "media")
    assert "fonte: Deezer" in r.observacao


def test_sem_obra_compositor_reconhecido_pelo_apelido(banco):
    cliente = ClienteFalso(
        artistas=[{**JOBIM, "aliases": [{"name": "Tom Jobim"}]}],
        por_titulo=[gravacao("r1", "Wave", "Banda Famosa"), gravacao("r2", "Wave", "A. C. Jobim", id_artista="id-jobim")],
        deezer={"Wave": [faixa("Wave", "Banda Famosa", 900)]},
    )
    r = Buscador(banco, cliente).resolver("Wave", "Tom Jobim")
    assert (r.cantor_sugerido, r.confianca) == ("A. C. Jobim", "media")


def test_sem_obra_e_sem_compositor_entre_os_candidatos_fica_baixa(banco):
    cliente = ClienteFalso(obras=[obra("w1", "Wave", VINICIUS)], por_titulo=[gravacao("r1", "Wave", "Fulano", "2001")])
    r = Buscador(banco, cliente).resolver("Wave", "Chico Buarque")
    assert (r.cantor_sugerido, r.confianca) == ("Fulano", "baixa")
    assert "nenhuma obra" in r.observacao


# --- só título: candidatos do MusicBrainz e do Deezer ----------------------


def test_so_titulo_um_artista_no_musicbrainz_e_media(banco):
    cliente = ClienteFalso(por_titulo=[gravacao("r1", "Wave", "Fulano"), gravacao("r2", "Outra Coisa", "Beltrano")])
    r = Buscador(banco, cliente).resolver("Wave")
    assert (r.cantor_sugerido, r.confianca, r.alternativas) == ("Fulano", "media", [])
    assert "fonte: MusicBrainz" in r.observacao


def test_so_titulo_candidato_so_do_deezer_e_baixa(banco):
    r = Buscador(banco, ClienteFalso(deezer=[faixa("Wave", "Fulano")])).resolver("Wave")
    assert (r.cantor_sugerido, r.confianca) == ("Fulano", "baixa")
    assert "fonte: Deezer" in r.observacao and r.fonte_link.startswith("https://deezer.com")


def test_artista_ausente_do_musicbrainz_entra_pelo_deezer(banco):
    cliente = ClienteFalso(
        por_titulo=[gravacao(f"r{i}", "Bad Guy", nome) for i, nome in enumerate(["Alfa Um", "Beta Dois", "Gama Tres"])],
        deezer=[faixa("bad guy", "Artista Famosa"), faixa("Bad Guy", "Outra Banda")],
    )
    r = Buscador(banco, cliente).resolver("Bad Guy")
    assert (r.cantor_sugerido, r.confianca) == ("Artista Famosa", "baixa")
    assert "5 artistas" in r.observacao and "fonte: Deezer" in r.observacao


def test_posicao_no_deezer_pesa_mais_que_contagem_do_musicbrainz(banco):
    cliente = ClienteFalso(
        por_titulo=[gravacao(f"r{i}", "Zombie", "Muito Gravado") for i in range(5)] + [gravacao("r9", "Zombie", "Popular")],
        deezer=[faixa("Zombie", "Popular"), faixa("Zombie", "Terceiro"), faixa("Zombie", "Quarto")],
    )
    r = Buscador(banco, cliente).resolver("Zombie")
    assert r.cantor_sugerido == "Popular" and "fonte: MusicBrainz e Deezer" in r.observacao


def test_mesmo_artista_creditado_de_dois_jeitos_e_um_candidato_so(banco):
    cliente = ClienteFalso(
        por_titulo=[
            gravacao("r1", "Taj Mahal", "Jorge Ben", "1972", id_artista="id-ben"),
            gravacao("r2", "Taj Mahal", "Jorge Ben Jor", "2000", id_artista="id-ben"),
        ]
    )
    r = Buscador(banco, cliente).resolver("Taj Mahal")
    assert (r.cantor_sugerido, r.confianca, r.alternativas) == ("Jorge Ben", "media", [])


def test_versoes_derivadas_sao_descartadas(banco):
    cliente = ClienteFalso(
        por_titulo=[gravacao("r1", "Zombie", "Piano Tribute Players")],
        deezer=[
            faixa("Zombie (Karaoke Version)", "Karaoke Hits"),
            faixa("Zombie (Instrumental)", "Banda de Apoio"),
            faixa("Zombie - Remix", "DJ Qualquer"),
            faixa("Zombie", "Originally Performed By Fulano"),
            faixa("Zombie", "Banda Certa"),
        ],
    )
    r = Buscador(banco, cliente).resolver("Zombie")
    assert (r.cantor_sugerido, r.alternativas) == ("Banda Certa", [])


def test_marcador_que_faz_parte_do_titulo_nao_descarta(banco):
    r = Buscador(banco, ClienteFalso(deezer=[faixa("Cover Me", "Fulano")])).resolver("Cover Me")
    assert r.cantor_sugerido == "Fulano"


def test_sem_evidencia_fica_vazio(banco):
    r = Buscador(banco, ClienteFalso()).resolver("Wave")
    assert (r.cantor_sugerido, r.confianca) == ("", "nao_encontrado")


# --- cache, correções e erros de rede --------------------------------------


def test_cache_evita_segunda_consulta(banco):
    cliente = ClienteFalso(por_titulo=[gravacao("r1", "Wave", "Fulano")])
    buscador = Buscador(banco, cliente)
    buscador.resolver("Wave")
    antes = len(cliente.chamadas)
    assert buscador.resolver("  WAVE ").cantor_sugerido == "Fulano"
    assert len(cliente.chamadas) == antes


def test_cache_de_versao_antiga_e_refeito(banco):
    banco.salvar_cache("wave", "", {"cantor_sugerido": "Resposta Velha", "confianca": "alta", "v": VERSAO_CACHE - 1})
    cliente = ClienteFalso(por_titulo=[gravacao("r1", "Wave", "Fulano")])
    assert Buscador(banco, cliente).resolver("Wave").cantor_sugerido == "Fulano"


def test_erro_do_musicbrainz_nao_vai_para_o_cache(banco):
    r = Buscador(banco, ClienteFalso(falhar=True)).resolver("Wave")
    assert r.confianca == "nao_encontrado" and r.observacao.startswith("erro de rede")
    assert banco.contar_cache() == 0


def test_erro_do_deezer_aparece_na_observacao_no_log_e_nao_vai_para_o_cache(banco, caplog):
    cliente = ClienteFalso(por_titulo=[gravacao("r1", "Wave", "Fulano")], deezer_falha=True)
    with caplog.at_level(logging.ERROR, logger="quem_canta"):
        r = Buscador(banco, cliente).resolver("Wave")
    assert r.cantor_sugerido == "Fulano" and "Deezer indisponível" in r.observacao
    assert "Deezer falhou" in caplog.text
    assert banco.contar_cache() == 0


def test_correcao_manual_tem_prioridade(banco):
    cliente = ClienteFalso(por_titulo=[gravacao("r1", "Wave", "Fulano")])
    buscador = Buscador(banco, cliente)
    buscador.resolver("Wave")
    buscador.corrigir("Wave", "", "Tom Jobim")
    r = Buscador(banco, cliente).resolver("wave")
    assert (r.cantor_sugerido, r.confianca, r.fonte_link) == ("Tom Jobim", "alta", FONTE_MANUAL)
    assert Buscador(banco, cliente, usar_correcoes=False).resolver("Wave").cantor_sugerido == "Fulano"


# --- planilha ---------------------------------------------------------------


def test_detecta_colunas_com_nomes_variados():
    df = pd.DataFrame(columns=["Nome da Música", "Autor(es)", "Créditos", "Ano"])
    assert planilha.detectar_colunas(df) == {
        "compositor": "Autor(es)",
        "creditos": "Créditos",
        "iswc": None,
        "titulo": "Nome da Música",
    }
    # "Código da obra" é ISWC, não título (apesar de conter "obra").
    assert planilha.detectar_colunas(pd.DataFrame(columns=["Código da Obra", "Música"]))["iswc"] == "Código da Obra"
    assert planilha.detectar_colunas(pd.DataFrame(columns=["A", "B"]))["titulo"] is None


def test_csv_com_ponto_e_virgula_e_saida_com_colunas_novas():
    df = planilha.ler_planilha("Título;Compositor\nÁguas de Março;Tom Jobim\n".encode("cp1252"), "x.csv")
    assert list(df.columns) == ["Título", "Compositor"] and df.loc[0, "Título"] == "Águas de Março"
    linha = Buscador.__new__(Buscador)._resultado_manual("Elis Regina").para_linha()
    saida = planilha.juntar(df, [linha])
    assert list(saida.columns)[:2] == ["Título", "Compositor"] and saida.loc[0, "cantor_sugerido"] == "Elis Regina"
    relida = pd.read_excel(pd.io.common.BytesIO(planilha.para_xlsx(saida)), dtype=str)
    assert relida.shape == (1, 10) and relida.loc[0, "regra"] == "correcao_manual"


# --- coluna "regra" ---------------------------------------------------------


def test_regra_identifica_o_caminho_e_os_sinais(banco):
    def regra(titulo, compositor="", **respostas):
        return Buscador(banco, ClienteFalso(**respostas)).resolver(titulo, compositor).regra

    jobim = gravacao("r1", "Wave", "Tom Jobim", "1967", id_artista="id-jobim")
    uma_obra = [obra("w1", "Wave", JOBIM)]
    assert regra("Wave", "Antônio Carlos Jobim", obras=uma_obra, da_obra=[jobim, ORIGINAL]) == "obra_compositor"
    assert regra("Wave 2", "Antônio Carlos Jobim", obras=[obra("w1", "Wave 2", JOBIM), obra("w2", "Wave 2", JOBIM)],
                 da_obra=[jobim]) == "obra_compositor+varias_obras"
    assert regra("Wave 3", "Antônio Carlos Jobim", obras=[obra("w1", "Wave 3", JOBIM)],
                 da_obra=[TRIO_ANTIGO, gravacao("r3", "Wave", "Trio Antigo", "1990"), ORIGINAL]) == "obra_mais_gravado"
    assert regra("Wave 4", "Antônio Carlos Jobim", obras=[obra("w1", "Wave 4", JOBIM)], da_obra=[TRIO_ANTIGO, ORIGINAL],
                 deezer=[faixa("Wave 4", "Trio Antigo")]) == "obra_popular_deezer"
    assert regra("Wave 5", "Antônio Carlos Jobim", obras=[obra("w1", "Wave 5", JOBIM)],
                 da_obra=[TRIO_ANTIGO, ORIGINAL]) == "obra_sem_confirmacao"
    assert regra("Wave 6", "Antônio Carlos Jobim", obras=[obra("w1", "Wave 6", JOBIM)], da_obra=[TRIO_ANTIGO],
                 deezer=[faixa("Wave 6", "Antônio Carlos Jobim")]) == "obra_compositor_popular"
    assert regra("Wave 7", "Antônio Carlos Jobim", obras=[obra("w1", "Wave 7", JOBIM)],
                 da_obra=[gravacao("r1", "Wave", "Artista A", "1967"), gravacao("r2", "Wave", "Artista B", "1967")],
                 ) == "obra_sem_confirmacao+empate"
    assert regra("Wave 8", "Antônio Carlos Jobim", obras=[obra("w1", "Wave 8", JOBIM)],
                 da_obra=[gravacao("r1", "Wave", "Trio Qualquer", "1963"), jobim | {"first-release-date": "1963"}],
                 ) == "obra_compositor+empate_compositor"
    assert regra("Wave 9", "Antônio Carlos Jobim", obras=[obra("w1", "Wave 9", JOBIM)],
                 da_obra=[gravacao("r1", "Wave", "Sem Data")]) == "obra_sem_data"
    assert regra("Garçom", "Fulano de Tal", por_titulo=[gravacao("r2", "Garçom", "Fulano de Tal")]) == "sem_obra_compositor+mb"
    assert regra("Garçom 2", "Fulano de Tal", deezer=[faixa("Garçom 2", "Outra Banda")]) == "sem_obra_palpite+deezer"
    assert regra("So Um", por_titulo=[gravacao("r1", "So Um", "Fulano")]) == "so_titulo_unico+mb"
    assert regra("Varios", por_titulo=[gravacao("r1", "Varios", "Fulano")],
                 deezer=[faixa("Varios", "Fulano"), faixa("Varios", "Beltrano")]) == "so_titulo_varios+mb_deezer"
    assert regra("Nada") == "nao_encontrado"
    assert regra("") == "sem_titulo"
    assert regra("Caiu", falhar=True) == "erro_de_rede"
    assert regra("Sem Deezer", por_titulo=[gravacao("r1", "Sem Deezer", "Fulano")],
                 deezer_falha=True) == "so_titulo_unico+mb+deezer_falhou"


def test_avaliacao_mostra_acertos_por_regra():
    from avaliar import resumir_por_regra

    detalhe = pd.DataFrame(
        {
            "regra": ["obra_compositor", "obra_compositor", "so_titulo_varios+mb", "erro_de_rede"],
            "confianca": ["alta", "alta", "baixa", "nao_encontrado"],
            "acerto": [True, False, True, False],
            "acerto_top3": [True, True, True, False],
            "erro_de_rede": [False, False, False, True],
        }
    )
    tabela = resumir_por_regra(detalhe)
    assert tabela.to_dict("records") == [
        {"regra": "obra_compositor", "confianca": "alta", "linhas": 2, "acertos": 1, "taxa": "50%", "top3": 2},
        {"regra": "so_titulo_varios+mb", "confianca": "baixa", "linhas": 1, "acertos": 1, "taxa": "100%", "top3": 1},
    ]


# --- ISWC ---------------------------------------------------------------------

ISWC_OK = "T-004.317.880-8"  # dígito verificador correto


def obra_iswc(titulo="Wave"):
    return obra("w-iswc", titulo, JOBIM)


def credits_com(titulo="Wave", *isrcs):
    return {"title": titulo, "alternative_titles": [], "recordings": [{"isrc": i} for i in isrcs]}


def faixa_isrc(artista, data="2000-01-01"):
    return {"artist": {"name": artista}, "release_date": data, "link": f"https://deezer.com/track/{artista}"}


@pytest.mark.parametrize("entrada", ["T-004.317.880-8", "T-004317880-8", "T0043178808", " t 004 317 880 8 "])
def test_iswc_aceita_os_formatos_comuns(entrada):
    assert normalizar_iswc(entrada) == ("T-004.317.880-8", "")


def test_iswc_digito_verificador_e_formato():
    assert normalizar_iswc("T-307199825-5")[0] == "T-307.199.825-5"
    codigo, erro = normalizar_iswc("T-004317880-7")
    assert codigo == "" and "dígito verificador" in erro
    codigo, erro = normalizar_iswc("T-12345")
    assert codigo == "" and "formato" in erro
    assert normalizar_iswc("") == ("", "")


def test_iswc_invalido_e_anotado_e_a_busca_segue_sem_ele(banco):
    cliente = ClienteFalso(por_titulo=[gravacao("r1", "Wave", "Fulano")])
    r = Buscador(banco, cliente).resolver("Wave", "", "T-004317880-7")
    assert r.cantor_sugerido == "Fulano" and r.regra.endswith("+iswc_invalido")
    assert r.observacao.startswith("ISWC inválido") and "iswc_mb" not in cliente.tipos()


def test_obra_pelo_iswc_substitui_a_busca_por_titulo_e_compositor(banco):
    cliente = ClienteFalso(
        obras_iswc=[obra_iswc()], obras=[obra("w-homonima", "Wave", VINICIUS)],
        da_obra=[gravacao("r1", "Wave", "Cantora Original", "1970"), gravacao("r2", "Wave", "Cantora Original", "1980")],
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim", ISWC_OK)
    assert (r.cantor_sugerido, r.confianca, r.regra) == ("Cantora Original", "alta", "obra_mais_gravado+iswc_mb")
    assert (r.iswc_normalizado, r.iswc_encontrado_em) == (ISWC_OK, "MB")
    assert "work" not in cliente.tipos() and ("browse", "w-iswc") in cliente.chamadas


def test_iswc_com_titulo_que_nao_confere_e_ignorado(banco):
    cliente = ClienteFalso(obras_iswc=[obra_iswc("Outra Música Qualquer")], por_titulo=[gravacao("r1", "Wave", "Fulano")])
    r = Buscador(banco, cliente).resolver("Wave", "", ISWC_OK)
    assert r.cantor_sugerido == "Fulano" and r.regra.endswith("+iswc_titulo_diverge")
    assert "ISWC e título não conferem" in r.observacao and r.iswc_encontrado_em == "MB"


def test_iswc_nao_encontrado_segue_a_logica_atual(banco):
    cliente = ClienteFalso(por_titulo=[gravacao("r1", "Wave", "Fulano")])
    r = Buscador(banco, cliente).resolver("Wave", "", ISWC_OK)
    assert r.cantor_sugerido == "Fulano" and r.regra == "so_titulo_unico+mb+iswc_nao_encontrado"
    assert r.iswc_encontrado_em == "nenhum"


def test_mb_e_credits_concordam_da_alta(banco):
    cliente = ClienteFalso(
        obras_iswc=[obra_iswc()],
        da_obra=[gravacao("r1", "Wave", "Cantora Original", "1970"), gravacao("r2", "Wave", "Regravação", "1990")],
        credits=credits_com("WAVE", "BRXXX7000001", "BRXXX9000002"),
        faixas_isrc={"BRXXX7000001": faixa_isrc("Cantora Original"), "BRXXX9000002": faixa_isrc("Regravação")},
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim", ISWC_OK)
    assert (r.cantor_sugerido, r.confianca) == ("Cantora Original", "alta")
    assert r.regra == "obra_sem_confirmacao+iswc_ambos+fontes_concordam" and r.iswc_encontrado_em == "ambos"
    assert "Credits.fm confirma" in r.observacao and "CC BY 4.0" in r.observacao


def test_mb_e_credits_discordam_fica_media_com_a_outra_resposta_nas_alternativas(banco):
    cliente = ClienteFalso(
        obras_iswc=[obra_iswc()],
        da_obra=[gravacao("r1", "Wave", "Cantora Original", "1970"), gravacao("r2", "Wave", "Cantora Original", "1975")],
        credits=credits_com("Wave", "BRXXX6500001"),
        faixas_isrc={"BRXXX6500001": faixa_isrc("Outro Artista")},
    )
    r = Buscador(banco, cliente).resolver("Wave", "Antônio Carlos Jobim", ISWC_OK)
    assert (r.cantor_sugerido, r.confianca) == ("Cantora Original", "media")
    assert r.regra == "obra_mais_gravado+iswc_ambos+fontes_discordam" and r.alternativas[0] == "Outro Artista"


def test_obra_so_no_credits_usa_o_isrc_mais_antigo_e_fica_media(banco):
    cliente = ClienteFalso(
        credits=credits_com("Wave", "BRXXX0500003", "BRXXX9600001", "XX-invalido"),
        faixas_isrc={"BRXXX9600001": faixa_isrc("Cantora Original", "2010-01-01"), "BRXXX0500003": faixa_isrc("Regravação")},
    )
    r = Buscador(banco, cliente).resolver("Wave", "", ISWC_OK)
    assert (r.cantor_sugerido, r.confianca, r.regra) == ("Cantora Original", "media", "iswc_isrc_mais_antigo+iswc_credits")
    assert r.alternativas == ["Regravação"] and r.iswc_encontrado_em == "Credits.fm"
    assert "1996" in r.observacao  # o ano vem do ISRC, não da data de relançamento no Deezer


def test_falha_no_musicbrainz_por_iswc_vira_erro_de_rede(banco):
    r = Buscador(banco, ClienteFalso(falhar=True)).resolver("Wave", "", ISWC_OK)
    assert r.regra == "erro_de_rede" and banco.contar_cache() == 0


def test_falha_no_credits_segue_so_com_o_musicbrainz_e_nao_vai_para_o_cache(banco, caplog):
    cliente = ClienteFalso(
        obras_iswc=[obra_iswc()], credits_falha=True,
        da_obra=[gravacao("r1", "Wave", "Cantora Original", "1970"), gravacao("r2", "Wave", "Cantora Original", "1980")],
    )
    with caplog.at_level(logging.ERROR, logger="quem_canta"):
        r = Buscador(banco, cliente).resolver("Wave", "", ISWC_OK)
    assert r.cantor_sugerido == "Cantora Original" and r.iswc_encontrado_em == "MB"
    assert "Credits.fm indisponível" in r.observacao and "Credits.fm falhou" in caplog.text
    assert banco.contar_cache() == 0


def test_falha_no_deezer_por_isrc_e_avisada_e_nao_vai_para_o_cache(banco):
    cliente = ClienteFalso(credits=credits_com("Wave", "BRXXX9600001"), isrc_falha=True,
                           por_titulo=[gravacao("r1", "Wave", "Fulano")])
    r = Buscador(banco, cliente).resolver("Wave", "", ISWC_OK)
    assert "Deezer indisponível" in r.observacao and banco.contar_cache() == 0
    assert r.cantor_sugerido == "Fulano" and "obra definida pelo ISWC" in r.observacao


def test_isrc_sem_faixa_no_deezer_nao_e_falha(banco):
    cliente = ClienteFalso(credits=credits_com("Wave", "BRXXX9600001", "BRXXX9700002"),
                           faixas_isrc={"BRXXX9700002": faixa_isrc("Cantora")})
    r = Buscador(banco, cliente).resolver("Wave", "", ISWC_OK)
    assert r.cantor_sugerido == "Cantora" and banco.contar_cache() == 1


def test_resposta_com_iswc_tem_cache_proprio(banco):
    sem = Buscador(banco, ClienteFalso(por_titulo=[gravacao("r1", "Wave", "Fulano")])).resolver("Wave")
    cliente = ClienteFalso(credits=credits_com("Wave", "BRXXX9600001"), faixas_isrc={"BRXXX9600001": faixa_isrc("Cantora")})
    com = Buscador(banco, cliente).resolver("Wave", "", ISWC_OK)
    assert (sem.cantor_sugerido, com.cantor_sugerido) == ("Fulano", "Cantora")


def test_planilha_com_dados_do_credits_leva_a_atribuicao():
    df = pd.DataFrame({"titulo": ["Wave"], "iswc_encontrado_em": ["Credits.fm"]})
    abas = pd.read_excel(pd.io.common.BytesIO(planilha.para_xlsx(df)), sheet_name=None)
    assert "CC BY 4.0" in abas["fontes"].iloc[0, 0]
    sem = pd.read_excel(pd.io.common.BytesIO(planilha.para_xlsx(df.assign(iswc_encontrado_em="MB"))), sheet_name=None)
    assert "fontes" not in sem
