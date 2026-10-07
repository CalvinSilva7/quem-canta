"""Do relatório à lista de gravações classificadas: descobrir, coletar, classificar.

A unidade é a GRAVAÇÃO (um link de faixa em uma plataforma), nunca a obra: a
mesma obra costuma ter várias gravações na mesma plataforma, cada uma com o seu
crédito. Deduplica-se por link.

Uso:  python -m creditos.pipeline relatorio.pdf --saida planilha.xlsx
"""

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from cantor.matching import normalizar

from . import classificador as c
from .classificador import Classificacao, Config, Item, classificar
from .deezer import Deezer
from .filtro import FiltroDeSaida
from .modelo import Relatorio

ALBUNS_POR_BUSCA = 40  # álbuns abertos por obra na busca por título (o complemento da discografia)
OBRAS_PARA_VINCULO = 3  # títulos distintos do relatório que um intérprete precisa ter para o vínculo ser inferido
ANOS_ANTES_DO_CADASTRO = 5  # lançamento tão anterior à inclusão da obra é suspeita de homônimo


@dataclass
class Gravacao:
    plataforma: str = ""
    link: str = ""
    titulo: str = ""  # como a plataforma exibe
    interprete: str = ""
    album: str = ""
    isrc: str = ""
    fornecedor: str = ""
    lancamento: str = ""
    creditos: list[str] = field(default_factory=list)
    origem: str = ""  # como a gravação foi achada
    vinculo: str = ""  # por que se entende que a gravação é do repertório do titular
    obra: str = ""  # título no relatório
    classificacao: Classificacao | None = None
    coletado_em: str = ""
    provas: list[str] = field(default_factory=list)  # nomes-base das capturas de tela desta gravação


@dataclass
class Coleta:
    plataforma: str
    gravacoes: list[Gravacao] = field(default_factory=list)
    fora_do_repertorio: int = 0  # faixas lidas que não são do relatório
    albuns_lidos: int = 0
    albuns_com_erro: list[tuple[str, str]] = field(default_factory=list)  # (link, erro)
    avisos: list[str] = field(default_factory=list)
    interpretes_inferidos: list[dict] = field(default_factory=list)  # [{"nome", "obras", "com_autor"}]
    sementes: list[str] = field(default_factory=list)
    requisicoes: int = 0
    bloqueadas: int = 0
    segundos: float = 0.0
    sem_vinculo_nao_abertos: int = 0  # resultados de busca com o título, de artista sem ligação com o titular
    interrompida: str = ""  # preenchido quando a fila parou antes do fim (captcha, login, bloqueio)


def filtro_do_relatorio(relatorio: Relatorio, config: Config) -> FiltroDeSaida:
    liberados = [o.titulo for o in relatorio.obras] + [relatorio.nome_titular, relatorio.pseudonimo_titular]
    liberados += [x for o in relatorio.obras for a in o.autores for x in (a.nome, a.pseudonimo)]
    liberados += [*config.nomes_confirmados, *config.nomes_a_confirmar, *config.interpretes]
    return FiltroDeSaida(relatorio.termos_sensiveis(), liberados)


def sementes_de_busca(relatorio: Relatorio, config: Config, nomes_dos_coautores=()) -> list[str]:
    """Nomes cuja discografia vale percorrer: o titular (pseudônimos e nomes confirmados), os pseudônimos
    dos coautores e os intérpretes informados. Sem repetição, na ordem de importância."""
    nomes = [relatorio.pseudonimo_titular, *config.nomes_confirmados, relatorio.nome_titular, *config.interpretes,
             *nomes_dos_coautores]
    unicos = {}
    for nome in nomes:
        if normalizar(nome):
            unicos.setdefault(normalizar(nome), nome)
    return list(unicos.values())


def coletar_deezer(relatorio: Relatorio, config: Config, deezer: Deezer, nomes_dos_coautores=(), ao_avancar=None,
                   limite_de_obras=None) -> Coleta:
    inicio = time.monotonic()
    coleta = Coleta("DEEZER", sementes=sementes_de_busca(relatorio, config, nomes_dos_coautores))
    avisar = ao_avancar or (lambda texto: None)
    albuns = {}  # id -> como foi achado

    for nome in coleta.sementes:  # 1. discografia de cada nome
        avisar(f"Deezer: discografia de {nome}")
        try:
            for artista in deezer.artistas(nome):
                for album_id in deezer.albuns_do_artista(artista["id"]):
                    albuns.setdefault(album_id, f"discografia de {artista['nome']}")
        except Exception as e:  # falha de rede ou bloqueio do filtro: registra e segue com o resto
            coleta.avisos.append(f'não foi possível listar a discografia de "{nome}": {e}')

    titulos = _titulos(relatorio, limite_de_obras, coleta, "Deezer (busca por título)")
    for i, (base, titulo) in enumerate(titulos, start=1):  # 2. busca por título: pega o que a discografia não pega
        avisar(f"Deezer: busca por título {i}/{len(titulos)}")
        consultas = [titulo] + [f"{titulo} {n}" for n in coleta.sementes[:2]]
        achados = []
        for consulta in consultas:
            try:
                achados += [f for f in deezer.buscar_faixas(consulta) if c._titulo_base(f["titulo"]) == base]
            except Exception as e:
                coleta.avisos.append(f'busca por "{titulo}" falhou: {e}')
        for faixa in achados:
            if faixa["album_id"] and (faixa["album_id"] in albuns or sum(v == f"busca por título ({titulo})" for v in albuns.values()) < ALBUNS_POR_BUSCA):
                albuns.setdefault(faixa["album_id"], f"busca por título ({titulo})")

    vistos, itens = set(), []
    for i, (album_id, origem) in enumerate(albuns.items(), start=1):  # 3. créditos, álbum a álbum
        avisar(f"Deezer: lendo álbum {i}/{len(albuns)}")
        album = deezer.album(album_id)
        if album.erro:
            coleta.albuns_com_erro.append((f"https://www.deezer.com/br/album/{album_id}", album.erro))
            continue
        coleta.albuns_lidos += 1
        if album.aviso:
            coleta.avisos.append(f'álbum "{album.titulo}" ({album_id}): {album.aviso}; a faixa que faltou fica NÃO VERIFICADA')
        for faixa in album.faixas:
            if faixa.link in vistos:
                continue
            vistos.add(faixa.link)
            item = Item(titulo=faixa.titulo, interprete=faixa.interprete, creditos=faixa.creditos, plataforma="DEEZER",
                        link=faixa.link, isrc=faixa.isrc, fonte=album.fornecedor, album=album.titulo)
            itens.append((item, album, origem))

    classificados = [(item, album, origem, classificar(item, relatorio, config)) for item, album, origem in itens]
    inferidos = _interpretes_com_vinculo_inferido(classificados, relatorio, config)
    config_presumido = Config(config.nomes_confirmados, config.nomes_a_confirmar, config.interpretes,
                              [i["nome"] for i in inferidos.values()])
    coleta.interpretes_inferidos = sorted(inferidos.values(), key=lambda i: -i["obras"])
    agora = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")
    for item, album, origem, resultado in classificados:
        if resultado.status == c.HOMONIMA and normalizar(item.interprete) in inferidos:
            # O intérprete grava outras obras do titular: a gravação é reavaliada com essa presunção. Sem crédito
            # nenhum, entra "a confirmar"; com crédito de outras pessoas, continua como possível homônima.
            resultado = classificar(item, relatorio, config_presumido)
        vinculo = resultado.vinculo
        if resultado.status == c.FORA_DO_REPERTORIO:
            coleta.fora_do_repertorio += 1
            continue
        _conferir_datas(resultado, album.lancamento, relatorio)
        obras = [o for o in relatorio.obras if o.codigo in resultado.obras] or [o for o in relatorio.obras if c._titulo_base(o.titulo) == c._titulo_base(item.titulo)]
        coleta.gravacoes.append(Gravacao(
            plataforma="DEEZER", link=item.link, titulo=item.titulo, interprete=item.interprete, album=album.titulo,
            isrc=item.isrc, fornecedor=album.fornecedor, lancamento=album.lancamento, creditos=item.creditos,
            origem=origem, vinculo=vinculo, obra="; ".join(dict.fromkeys(o.titulo for o in obras)),
            classificacao=resultado, coletado_em=agora,
        ))
    coleta.requisicoes, coleta.segundos = deezer.requisicoes, time.monotonic() - inicio
    coleta.bloqueadas = deezer.filtro.bloqueadas if deezer.filtro else 0
    return coleta


def _interpretes_com_vinculo_inferido(classificados, relatorio, config) -> dict:
    """Intérpretes que ninguém informou, mas que gravam o titular de forma recorrente.

    Critério: pelo menos OBRAS_PARA_VINCULO títulos distintos do relatório (título exato) e, em pelo menos
    uma dessas gravações, um autor do relatório no crédito exibido. É o que separa "a banda que grava o
    compositor" de "um artista qualquer com uma música de mesmo nome".
    """
    por_interprete = {}
    for item, _, _, resultado in classificados:
        if resultado.titulo != "exato":
            continue
        dados = por_interprete.setdefault(normalizar(item.interprete), {"nome": item.interprete, "titulos": set(), "com_autor": set()})
        chave = tuple(resultado.obras)
        dados["titulos"].add(chave)
        if resultado.autores_reconhecidos:
            dados["com_autor"].add(chave)
    return {
        nome: {"nome": d["nome"], "obras": len(d["titulos"]), "com_autor": len(d["com_autor"])}
        for nome, d in por_interprete.items()
        if nome and len(d["titulos"]) >= OBRAS_PARA_VINCULO and d["com_autor"]
    }


def _conferir_datas(resultado: Classificacao, lancamento: str, relatorio: Relatorio):
    """Sinal local: gravação lançada muito antes de a obra entrar no cadastro pode ser homônima.

    A data de inclusão não sai do app e não é repetida na nota; só muda algo quando nenhum autor do
    relatório foi reconhecido no crédito.
    """
    if resultado.autores_reconhecidos or not lancamento[:4].isdigit():
        return
    anos = []
    for obra in relatorio.obras:
        if obra.codigo in resultado.obras:
            ano = next((p for p in obra.inclusao.replace("-", "/").split("/") if len(p) == 4 and p.isdigit()), "")
            if ano:
                anos.append(int(ano))
    if anos and min(anos) - int(lancamento[:4]) > ANOS_ANTES_DO_CADASTRO:
        resultado.revisar = True
        resultado.fundamento += (
            f"; ATENÇÃO: lançamento de {lancamento[:4]}, mais de {ANOS_ANTES_DO_CADASTRO} anos antes de a obra entrar "
            "no cadastro: conferir se não é obra homônima"
        )


def _titulos(relatorio: Relatorio, limite, coleta: Coleta, onde: str) -> list[tuple[str, str]]:
    """[(título normalizado, título do relatório)], sem repetição. Com `limite`, só as N primeiras (N positivo)
    ou as N últimas (N negativo), e a coleta fica marcada como parcial."""
    titulos = list({c._titulo_base(o.titulo): o.titulo for o in relatorio.obras}.items())
    if limite and abs(limite) < len(titulos):
        quais = "primeiras" if limite > 0 else "últimas"
        coleta.avisos.append(f"COLETA PARCIAL: só as {abs(limite)} {quais} obras de {len(titulos)} foram buscadas em {onde}")
        return titulos[:limite] if limite > 0 else titulos[limite:]
    return titulos


def _par_conhecido(pares: dict, titulo: str, interprete: str) -> bool:
    """O par (esta obra, este intérprete) já foi provado em outra plataforma, por um autor do relatório no crédito?"""
    return any(c.mesmo_artista(parte, n) for n in pares.get(titulo, []) for parte in c.partes_do_interprete(interprete))


def _citado(nome: str, titulo_do_video: str) -> bool:
    """O título do vídeo cita o intérprete como um trecho inteiro ("Banda Tal - Música"), e não como parte de outro nome."""
    import re
    trechos = re.split(r"\s*(?:[-–—|,/&()\[\]]|\bfeat\.?|\bpart\.?|\bft\.?|\be\b|\bx\b)\s*", titulo_do_video, flags=re.IGNORECASE)
    return any(c.mesmo_artista(trecho, nome) for trecho in trechos if trecho.strip())


def _classificar_faixas(coleta: Coleta, relatorio: Relatorio, config: Config, pares: dict, lidas, recorrentes=()) -> None:
    """Classifica faixas lidas em qualquer plataforma e acrescenta as do repertório à coleta.

    `lidas`: [(faixa, álbum, origem)], em que faixa tem titulo, interprete, creditos, link e isrc, e álbum tem
    titulo, fornecedor e lancamento. Homônimo sem nenhum vínculo com o titular é só contado: quem cuida dos
    possíveis homônimos é a aba de pendências, alimentada pela Deezer.
    """
    config_local = Config(config.nomes_confirmados, config.nomes_a_confirmar, config.interpretes, list(recorrentes))
    agora = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")
    vistos = {g.link for g in coleta.gravacoes}
    for faixa, album, origem in lidas:
        if not faixa.link or faixa.link in vistos:
            continue
        vistos.add(faixa.link)
        tipo, obras = c.casar_titulo(faixa.titulo, relatorio)
        if not tipo:
            coleta.fora_do_repertorio += 1
            continue
        titulo_da_obra = "; ".join(dict.fromkeys(o.titulo for o in obras))
        item = Item(titulo=faixa.titulo, interprete=faixa.interprete, creditos=faixa.creditos, plataforma=coleta.plataforma,
                    link=faixa.link, isrc=getattr(faixa, "isrc", ""), fonte=album.fornecedor, album=album.titulo,
                    coleta=getattr(faixa, "coleta", "ok"), erro=getattr(faixa, "erro", ""),
                    vinculo=getattr(faixa, "vinculo", False) or any(_par_conhecido(pares, o.titulo, faixa.interprete) for o in obras))
        resultado = classificar(item, relatorio, config_local)
        if resultado.status == c.HOMONIMA:
            coleta.sem_vinculo_nao_abertos += 1
            continue
        _conferir_datas(resultado, album.lancamento or "", relatorio)
        if getattr(faixa, "nota", ""):
            resultado.fundamento += "; " + faixa.nota
        coleta.gravacoes.append(Gravacao(
            plataforma=coleta.plataforma, link=faixa.link, titulo=faixa.titulo, interprete=faixa.interprete,
            album=album.titulo, isrc=getattr(faixa, "isrc", ""), fornecedor=album.fornecedor, lancamento=album.lancamento,
            creditos=faixa.creditos, origem=origem, vinculo=resultado.vinculo, obra=titulo_da_obra,
            classificacao=resultado, coletado_em=agora, provas=list(getattr(faixa, "provas", [])),
        ))


def _consultas(titulo: str, principais, pares: dict) -> list[str]:
    da_obra = [n for n in pares.get(titulo, []) if not any(c.mesmo_artista(n, p) for p in principais)]
    return [f"{titulo} {n}" for n in [*principais, *da_obra]] + [titulo]


def _principais(relatorio: Relatorio, config: Config, recorrentes) -> list[str]:
    do_titular = [n for n in dict.fromkeys([relatorio.pseudonimo_titular, *config.nomes_confirmados]) if n]
    return [n for n in dict.fromkeys([*recorrentes[:1], *config.interpretes[:2], *do_titular[:1]]) if n]


def coletar_apple(relatorio: Relatorio, config: Config, apple, recorrentes=(), pares=None, limite_de_obras=None,
                  por_obra=15, ao_avancar=None) -> Coleta:
    """Apple Music: busca pela API pública do iTunes e lê o compositor de cada faixa na página do álbum."""
    inicio, pares, avisar = time.monotonic(), pares or {}, ao_avancar or (lambda texto: None)
    principais = _principais(relatorio, config, recorrentes)
    coleta = Coleta("APPLE MUSIC", sementes=principais)
    albuns = {}
    titulos = _titulos(relatorio, limite_de_obras, coleta, "Apple Music")
    for i, (base, titulo) in enumerate(titulos, start=1):
        avisar(f"Apple Music: buscando {i}/{len(titulos)}")
        abertos = 0
        for consulta in _consultas(titulo, principais, pares):
            try:
                achadas = apple.buscar_faixas(consulta)
            except Exception as e:
                coleta.avisos.append(f'busca por "{consulta}" falhou: {e}')
                continue
            for faixa in achadas:
                if c._titulo_base(faixa["titulo"]) == base and faixa["album_id"] and faixa["album_id"] not in albuns and abertos < por_obra:
                    albuns[faixa["album_id"]] = f'busca "{consulta}"'
                    abertos += 1
    lidas = []
    for i, (album_id, origem) in enumerate(albuns.items(), start=1):
        avisar(f"Apple Music: lendo álbum {i}/{len(albuns)}")
        album = apple.album(album_id)
        if album.erro:
            coleta.albuns_com_erro.append((f"https://music.apple.com/br/album/{album_id}", album.erro))
            continue
        coleta.albuns_lidos += 1
        if album.aviso:
            coleta.avisos.append(f'álbum "{album.titulo}" ({album_id}): {album.aviso}')
        lidas += [(faixa, album, origem) for faixa in album.faixas]
    _classificar_faixas(coleta, relatorio, config, pares, lidas, recorrentes)
    coleta.requisicoes, coleta.segundos = apple.requisicoes, time.monotonic() - inicio
    coleta.bloqueadas = apple.filtro.bloqueadas if apple.filtro else 0
    return coleta


def _ligados(relatorio, config, recorrentes, pares, titulo) -> list[str]:
    do_titular = [n for n in dict.fromkeys([relatorio.pseudonimo_titular, *config.nomes_confirmados]) if n]
    return [n for n in dict.fromkeys([*recorrentes, *config.interpretes, *do_titular, *pares.get(titulo, [])]) if n]


def _parar(coleta: Coleta, erro: Exception):
    coleta.interrompida = f"{type(erro).__name__}: {erro}"
    coleta.avisos.append(
        f"A coleta de {coleta.plataforma} PAROU antes do fim ({coleta.interrompida}). O que já foi lido está salvo; "
        "rode de novo mais tarde para continuar de onde parou. Nada foi contornado."
    )


def coletar_spotify(relatorio: Relatorio, config: Config, spotify, recorrentes=(), pares=None, limite_de_obras=None,
                    por_obra=8, ao_avancar=None) -> Coleta:
    """Spotify: busca "título + intérprete" e abre a janela de créditos de cada faixa de quem tem ligação com o titular."""
    from types import SimpleNamespace
    from cantor.matching import nomes_parecidos
    from .captura import PaginaTraduzida
    from .navegador import Bloqueio

    inicio, pares, avisar = time.monotonic(), pares or {}, ao_avancar or (lambda texto: None)
    principais = _principais(relatorio, config, recorrentes)
    coleta = Coleta("SPOTIFY", sementes=principais)
    fila, vistos, lidas = [], set(), []
    try:
        titulos = _titulos(relatorio, limite_de_obras, coleta, "Spotify")
        for i, (base, titulo) in enumerate(titulos, start=1):
            avisar(f"Spotify: buscando {i}/{len(titulos)}")
            ligados, abertos = _ligados(relatorio, config, recorrentes, pares, titulo), 0
            for consulta in _consultas(titulo, principais, pares)[:-1]:  # sem a busca só pelo título: traria só homônimos
                for cand in spotify.buscar(consulta):
                    if cand.faixa in vistos or c._titulo_base(cand.titulo) != base:
                        continue
                    vistos.add(cand.faixa)
                    if not any(c.mesmo_artista(parte, n) for n in ligados for parte in c.partes_do_interprete(cand.interprete)):
                        coleta.sem_vinculo_nao_abertos += 1
                    elif abertos < por_obra:
                        abertos += 1
                        fila.append((cand, titulo, consulta))
        for i, (cand, titulo, consulta) in enumerate(fila, start=1):
            avisar(f"Spotify: lendo faixa {i}/{len(fila)}")
            leitura = spotify.ler(cand, titulo)
            faixa = SimpleNamespace(titulo=leitura.titulo or cand.titulo, interprete=cand.interprete, creditos=leitura.creditos,
                                    link=leitura.link, coleta=leitura.coleta, erro=leitura.erro, provas=leitura.provas)
            album = SimpleNamespace(titulo=cand.album, fornecedor=leitura.fornecedor, lancamento="")
            lidas.append((faixa, album, f'busca "{consulta}"'))
    except (Bloqueio, PaginaTraduzida) as e:
        _parar(coleta, e)
    _classificar_faixas(coleta, relatorio, config, pares, lidas, recorrentes)
    coleta.albuns_lidos, coleta.requisicoes, coleta.segundos = len(lidas), spotify.nav.navegacoes, time.monotonic() - inicio
    return coleta


def coletar_tidal(relatorio: Relatorio, config: Config, tidal, recorrentes=(), pares=None, limite_de_obras=None,
                  ao_avancar=None, nomes_no_maximo=8) -> Coleta:
    """Tidal: percorre a discografia de quem grava o titular e lê a página de créditos de cada álbum.

    Depois de classificar, volta às faixas sem crédito ou com crédito errado e tira o print da página de
    créditos, com a faixa contornada.
    """
    from .captura import PaginaTraduzida
    from .navegador import Bloqueio

    inicio, pares, avisar = time.monotonic(), pares or {}, ao_avancar or (lambda texto: None)
    coleta = Coleta("TIDAL")
    titulos = dict(_titulos(relatorio, limite_de_obras, coleta, "Tidal"))
    interpretes = _principais(relatorio, config, recorrentes)
    for titulo in titulos.values():  # mais os intérpretes que outra plataforma já mostrou para as obras buscadas
        interpretes += [n for n in pares.get(titulo, []) if n not in interpretes]
    coleta.sementes = list({normalizar(n): n for n in reversed(interpretes)}.values())[::-1][:nomes_no_maximo]
    lidas, de_onde = [], {}
    try:
        albuns = {}
        for nome in coleta.sementes:
            avisar(f"Tidal: procurando {nome}")
            for album_id in tidal.albuns_de(nome):
                albuns.setdefault(album_id, f"discografia de {nome}")
        for i, (album_id, origem) in enumerate(albuns.items(), start=1):
            avisar(f"Tidal: lendo álbum {i}/{len(albuns)}")
            album = tidal.album(album_id)
            if album.erro:
                coleta.albuns_com_erro.append((f"https://tidal.com/album/{album_id}/credits", album.erro))
                continue
            coleta.albuns_lidos += 1
            if album.aviso:
                coleta.avisos.append(f'álbum "{album.titulo}" ({album_id}): {album.aviso}; a faixa que faltou fica NÃO VERIFICADA')
            for faixa in album.faixas:
                if c._titulo_base(faixa.titulo) in titulos:  # só as obras buscadas nesta rodada
                    lidas.append((faixa, album, origem))
                    de_onde[faixa.link] = (album_id, faixa)
        _classificar_faixas(coleta, relatorio, config, pares, lidas, recorrentes)
        from . import captura as _captura
        ja_tiradas = {(f.get("url"), str((f.get("exibido") or {}).get("faixa_no_album", ""))): f["captura"]
                      for f in _captura.capturas_guardadas(tidal.pasta)}
        for g in coleta.gravacoes:
            if g.classificacao.status in c.NEGATIVOS and not g.provas:
                album_id, faixa = de_onde[g.link]
                guardada = ja_tiradas.get((f"https://tidal.com/album/{album_id}/credits", str(faixa.numero)))
                if guardada:
                    g.provas.append(guardada)
        negativas = [] if getattr(tidal, "so_o_que_ja_foi_lido", False) else [
            g for g in coleta.gravacoes if g.classificacao.status in c.NEGATIVOS and not g.provas]
        for i, g in enumerate(negativas, start=1):
            avisar(f"Tidal: print {i}/{len(negativas)}")
            album_id, faixa = de_onde[g.link]
            try:
                g.provas.append(tidal.capturar(album_id, faixa, g.obra))
            except (Bloqueio, PaginaTraduzida):
                raise
            except Exception as e:
                coleta.avisos.append(f'print de "{g.titulo}" não pôde ser tirado: {type(e).__name__}')
    except (Bloqueio, PaginaTraduzida) as e:
        _parar(coleta, e)
        if not coleta.gravacoes:
            _classificar_faixas(coleta, relatorio, config, pares, lidas, recorrentes)
    coleta.requisicoes, coleta.segundos = tidal.nav.navegacoes, time.monotonic() - inicio
    return coleta


def coletar_vagalume(relatorio: Relatorio, config: Config, vagalume, recorrentes=(), pares=None, limite_de_obras=None,
                     ao_avancar=None, nomes_no_maximo=12) -> Coleta:
    """Vagalume: procura as obras na página de cada intérprete conhecido e lê o bloco de autoria da letra."""
    from types import SimpleNamespace

    inicio, pares, avisar = time.monotonic(), pares or {}, ao_avancar or (lambda texto: None)
    coleta = Coleta("VAGALUME")
    titulos = dict(_titulos(relatorio, limite_de_obras, coleta, "Vagalume"))
    interpretes = _principais(relatorio, config, recorrentes)
    for titulo in titulos.values():
        interpretes += [n for n in pares.get(titulo, []) if n not in interpretes]
    coleta.sementes = list({normalizar(n): n for n in reversed(interpretes)}.values())[::-1][:nomes_no_maximo]
    lidas = []
    for nome in coleta.sementes:
        avisar(f"Vagalume: procurando {nome}")
        try:
            letras = vagalume.letras_de(nome)
        except Exception as e:
            coleta.avisos.append(f'página de "{nome}" no Vagalume não pôde ser lida: {e}')
            continue
        for endereco, titulo in letras.items():
            if c._titulo_base(titulo) not in titulos:
                continue
            letra = vagalume.letra(endereco)
            coleta.albuns_lidos += 1
            if letra.erro:
                coleta.albuns_com_erro.append((letra.link, letra.erro))
                continue
            nota = f'bloco de autoria do site: "{letra.bloco}"'
            if letra.desconhecido:
                nota = "o site AFIRMA que o compositor é desconhecido (negativa expressa de autoria, citando o ECAD); " + nota
            faixa = SimpleNamespace(titulo=titulo, interprete=nome, creditos=letra.creditos, link=letra.link, nota=nota)
            lidas.append((faixa, SimpleNamespace(titulo="", fornecedor="", lancamento=""), f"página de {nome} no Vagalume"))
    _classificar_faixas(coleta, relatorio, config, pares, lidas, recorrentes)
    coleta.requisicoes, coleta.segundos = vagalume.requisicoes, time.monotonic() - inicio
    return coleta


def reaproveitar_prints(coleta: Coleta, pasta, endereco=None) -> None:
    """Liga cada gravação negativa ao print que já existe dela na pasta (mesmo endereço, mesmos créditos na tela):
    numa nova rodada, o que já foi fotografado não é fotografado de novo."""
    from . import captura

    def fim(url):  # o último trecho do endereço identifica a faixa, mesmo quando a plataforma redireciona
        return str(url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]

    por_endereco = {}
    for ficha in captura.capturas_guardadas(pasta):
        por_endereco.setdefault(fim(ficha.get("url")), ficha)
    for g in coleta.gravacoes:
        if g.classificacao.status in c.NEGATIVOS and not g.provas:
            ficha = por_endereco.get(fim(endereco(g.link) if endereco else g.link))
            if ficha and {normalizar(n) for n in (ficha.get("exibido") or {}).get("creditos_lidos", [])} == {normalizar(n) for n in g.creditos}:
                g.provas.append(ficha["captura"])


def capturar_paginas(coleta: Coleta, navegador, pasta, preparar=None, ao_avancar=None, endereco=None, reclassificar=None) -> None:
    """Print das gravações sem crédito ou com crédito errado de uma plataforma lida sem navegador (Deezer, Apple
    Music, Vagalume): abre o link de cada uma, deixa `preparar(navegador)` abrir a tela de créditos, e captura.

    Se `preparar` devolver os autores que a tela mostra e eles forem diferentes dos que tinham sido lidos nos dados
    da página, vale a tela: a gravação é reclassificada, e só continua com print se continuar negativa.
    """
    from . import captura
    from .navegador import Bloqueio

    avisar = ao_avancar or (lambda texto: None)
    negativas = [g for g in coleta.gravacoes if g.classificacao.status in c.NEGATIVOS and not g.provas]
    try:
        for i, g in enumerate(negativas, start=1):
            avisar(f"{coleta.plataforma.title()}: print {i}/{len(negativas)}")
            try:
                navegador.ir(endereco(g.link) if endereco else g.link, pausa=(4, 7))
                navegador.pagina.wait_for_timeout(3500)
                navegador.conferir(navegador.pagina.evaluate("document.body.innerText.slice(0, 1500)"))
                na_tela = preparar(navegador) if preparar else None
                if na_tela is not None and {normalizar(n) for n in na_tela} != {normalizar(n) for n in g.creditos} and reclassificar:
                    antes = ", ".join(g.creditos) or "nenhum autor"
                    g.creditos = list(na_tela)
                    g.classificacao = reclassificar(g)
                    g.classificacao.fundamento += (
                        f"; os dados da página do álbum traziam {antes}, mas a tela de créditos da música exibe "
                        f"{', '.join(na_tela) or 'nenhum autor'}: vale o que a tela mostra"
                    )
                    if g.classificacao.status not in c.NEGATIVOS:
                        continue
                exibido = {"titulo": g.titulo, "interprete": g.interprete, "creditos_lidos": g.creditos,
                           "creditos_na_tela": na_tela, "constatacao": g.classificacao.fundamento}
                g.provas.append(captura.capturar(navegador.pagina, pasta, g.obra, g.interprete, coleta.plataforma.lower(), exibido, "creditos")["captura"])
            except (Bloqueio, captura.PaginaTraduzida):
                raise
            except Exception as e:
                coleta.avisos.append(f'print de "{g.titulo}" ({g.interprete}) não pôde ser tirado: {type(e).__name__}: {str(e).splitlines()[0][:100]}')
    except (Bloqueio, captura.PaginaTraduzida) as e:
        _parar(coleta, e)


def abrir_creditos_da_deezer(navegador):
    """Na página da faixa: recusa os cookies, abre o menu e o painel "Consulte créditos musicais"."""
    import re
    pagina = navegador.pagina
    navegador.clicar_se_houver('[data-testid="gdpr-btn-refuse-all"]')
    pagina.get_by_role("button", name="Exibir menu").first.click()
    pagina.get_by_text(re.compile(r"cr[eé]ditos musicais", re.I)).first.click()
    pagina.wait_for_timeout(1800)
    navegador.conferir()


def pagina_da_musica_na_apple(link: str) -> str:
    """O compositor não aparece na página do álbum: aparece na página da música, em "Composição e letra"."""
    import re
    faixa = re.search(r"[?&]i=(\d+)", link)
    return f"https://music.apple.com/br/song/{faixa.group(1)}" if faixa else link


def abrir_creditos_da_apple(navegador):
    """Na página da música: rola até os créditos e contorna a seção. Sem ela na tela, não há print."""
    pagina = navegador.pagina
    pagina.wait_for_selector('[data-testid="cell-title"]', timeout=20000)
    secoes = pagina.evaluate("""() => {
      const titulos = [...document.querySelectorAll('[data-testid="cell-title"]')];
      const alvo = titulos.find(e => /composi/i.test(e.textContent)) || titulos.find(e => /interpreta/i.test(e.textContent));
      if (!alvo) return null;
      const secao = alvo.closest('[data-testid="section-container"]') || alvo.parentElement;
      secao.scrollIntoView({block: 'center'});
      secao.style.outline = '3px solid #d00000';
      return titulos.map(e => e.textContent.trim());
    }""")
    if secoes is None:
        raise ValueError("a página da música não mostrou a seção de créditos")
    pagina.wait_for_timeout(600)
    return autores_na_pagina_da_apple(pagina.evaluate("""() => {
      const titulo = [...document.querySelectorAll('[data-testid="cell-title"]')].find(e => /composi/i.test(e.textContent));
      const secao = titulo && (titulo.closest('[data-testid="section-container"]') || titulo.parentElement);
      return secao ? secao.innerText.split('\\n').map(l => l.trim()).filter(Boolean) : [];
    }"""))


_PAPEIS_DA_APPLE = {"composicao", "letra", "composicao e letra", "compositor", "letrista", "autor", "autores", "arranjo"}


def autores_na_pagina_da_apple(linhas) -> list[str]:
    """Linhas da seção "Composição e letra" (título, nome, papel, nome, papel...) -> só os nomes."""
    nomes = []
    for linha in linhas or []:
        if normalizar(linha) not in _PAPEIS_DA_APPLE and linha not in nomes:
            nomes.append(linha)
    return nomes


def abrir_autoria_do_vagalume(navegador):
    navegador.pagina.evaluate("""() => { const b = document.querySelector('#author'); if (b) { b.scrollIntoView({block: 'center'}); b.style.outline = '3px solid #d00000'; } }""")
    navegador.pagina.wait_for_timeout(500)


def interpretes_conhecidos(coletas) -> tuple[list[str], dict]:
    """O que as plataformas já coletadas ensinam sobre quem grava o titular.

    Devolve (intérpretes recorrentes, {título da obra: [intérpretes com autor do relatório no crédito]}).
    """
    recorrentes, pares = [], {}
    for coleta in coletas:
        recorrentes += [i["nome"] for i in coleta.interpretes_inferidos if i["nome"] not in recorrentes]
        for g in coleta.gravacoes:
            if g.classificacao.autores_reconhecidos and g.classificacao.titulo == "exato":
                for titulo in g.obra.split("; "):
                    if g.interprete not in pares.setdefault(titulo, []):
                        pares[titulo].append(g.interprete)
    return recorrentes, pares


def _titulo_do_video(titulo: str, relatorio: Relatorio) -> str:
    """Vídeo costuma vir como "Intérprete - Título": devolve a parte que é título de obra do relatório."""
    if c.casar_titulo(titulo, relatorio)[0] == "exato":
        return titulo
    import re
    for parte in re.split(r"\s+[-–—|]\s+", titulo):
        if c.casar_titulo(parte, relatorio)[0] == "exato":
            return parte
    return titulo


def coletar_youtube(relatorio: Relatorio, config: Config, ytm, recorrentes=(), pares=None, por_obra=12, ao_avancar=None,
                    limite_de_obras=None) -> Coleta:
    """YouTube Music: busca "título + intérprete" e, para cada resultado ligado ao titular, lê o menu da faixa.

    `recorrentes` e `pares` vêm de `interpretes_conhecidos` (o que outra plataforma já mostrou). Só são
    abertas as faixas de artistas com ligação conhecida com o titular, ou que citam um deles no título do
    vídeo: resultado com o mesmo título e artista sem relação é contado, não aberto.
    """
    from cantor.matching import nomes_parecidos
    from .ytmusic import Bloqueio, LINK
    from .captura import PaginaTraduzida
    from .filtro import DadoSensivel

    inicio, pares = time.monotonic(), pares or {}
    avisar = ao_avancar or (lambda texto: None)
    do_titular = [n for n in dict.fromkeys([relatorio.pseudonimo_titular, *config.nomes_confirmados]) if n]
    principais = [n for n in dict.fromkeys([*recorrentes[:1], *config.interpretes[:2], *do_titular[:1]]) if n]
    coleta = Coleta("YOUTUBE", sementes=principais)
    config_local = Config(config.nomes_confirmados, config.nomes_a_confirmar, config.interpretes, list(recorrentes))
    titulos = _titulos(relatorio, limite_de_obras, coleta, "YouTube Music")
    fila, vistos = [], set()  # (candidato, título da obra, nome que liga ao titular, consulta)
    try:
        for i, (base, titulo) in enumerate(titulos, start=1):
            avisar(f"YouTube Music: buscando {i}/{len(titulos)}")
            da_obra = [n for n in pares.get(titulo, []) if not any(c.mesmo_artista(n, p) for p in principais)]
            ligados = [*principais, *recorrentes, *config.interpretes, *do_titular, *pares.get(titulo, [])]
            consultas = [(f"{titulo} {n}", ("Músicas", "Vídeos") if k == 0 else ("Músicas",)) for k, n in enumerate(principais)]
            consultas += [(f"{titulo} {n}", ("Músicas",)) for n in da_obra]
            abertos = 0
            for consulta, abas in consultas:
                try:
                    achados = ytm.buscar(consulta, abas)
                except DadoSensivel as e:  # a consulta levaria dado sensível: não sai, e fica registrado
                    coleta.avisos.append(f'busca "{consulta}" não foi feita: o filtro de saída barrou ({e})')
                    continue
                for cand in achados:
                    if cand.video in vistos or c.casar_titulo(_titulo_do_video(cand.titulo, relatorio), relatorio)[0] != "exato":
                        continue
                    if c._titulo_base(_titulo_do_video(cand.titulo, relatorio)) != base:
                        continue
                    elo = next((n for n in ligados if any(c.mesmo_artista(parte, n) for parte in c.partes_do_interprete(cand.interprete))
                                or _citado(n, cand.titulo)), "")
                    vistos.add(cand.video)
                    if not elo:
                        coleta.sem_vinculo_nao_abertos += 1
                    elif abertos < por_obra:
                        abertos += 1
                        fila.append((cand, titulo, elo, consulta))
        for i, (cand, titulo, elo, consulta) in enumerate(fila, start=1):
            avisar(f"YouTube Music: lendo faixa {i}/{len(fila)}")
            leitura = ytm.ler(cand.video, titulo, cand)
            mostrado = _titulo_do_video(leitura.titulo or cand.titulo, relatorio)
            do_canal = any(c.mesmo_artista(parte, elo) for parte in c.partes_do_interprete(leitura.interprete or cand.interprete))
            item = Item(
                titulo=mostrado, interprete=elo if not do_canal else (leitura.interprete or cand.interprete),
                creditos=leitura.creditos, coleta=leitura.coleta, erro=leitura.erro, vinculo=_par_conhecido(pares, titulo, elo),
                plataforma="YOUTUBE", link=LINK.format(cand.video), fonte=leitura.fornecedor, album=leitura.detalhe,
            )
            if leitura.coleta == "ok" and c._titulo_base(mostrado) != c._titulo_base(titulo):
                item.coleta, item.erro = "erro", f'a página abriu "{leitura.titulo}", que não é o título buscado'
            resultado = classificar(item, relatorio, config_local)
            if resultado.status == c.FORA_DO_REPERTORIO:
                coleta.fora_do_repertorio += 1
                continue
            if not do_canal:
                resultado.fundamento += (
                    f'; é um vídeo publicado pelo canal "{leitura.interprete or cand.interprete}", e o intérprete '
                    f'"{elo}" foi reconhecido pelo título do vídeo'
                )
                resultado.vinculo = (resultado.vinculo + "; " if resultado.vinculo else "") + f'intérprete "{elo}" citado no título do vídeo'
            coleta.gravacoes.append(Gravacao(
                plataforma="YOUTUBE", link=item.link, titulo=leitura.titulo or cand.titulo, interprete=item.interprete,
                album=leitura.detalhe or cand.detalhe, fornecedor=leitura.fornecedor, creditos=leitura.creditos,
                origem=f'busca "{consulta}" ({cand.tipo})', vinculo=resultado.vinculo, obra=titulo,
                classificacao=resultado, coletado_em=leitura.lido_em, provas=leitura.provas,
            ))
    except (Bloqueio, PaginaTraduzida) as e:
        coleta.interrompida = f"{type(e).__name__}: {e}"
        coleta.avisos.append(
            f"A coleta do YouTube Music PAROU antes do fim ({coleta.interrompida}). O que já foi lido está salvo; "
            "rode de novo mais tarde para continuar de onde parou. Nada foi contornado."
        )
    coleta.albuns_lidos = len(coleta.gravacoes)
    coleta.requisicoes, coleta.segundos = ytm.navegacoes, time.monotonic() - inicio
    coleta.bloqueadas = ytm.filtro.bloqueadas if ytm.filtro else 0
    return coleta


# Tempo da primeira coleta, medido nos dois primeiros casos (um de 47 e um de 119 títulos, 10 a 20 obras cada).
# Segundos por obra em cada plataforma, mais um tempo fixo. É uma ordem de grandeza, não uma promessa: depende
# de quantas gravações cada obra tem e de quantos intérpretes aparecem.
SEGUNDOS_POR_OBRA = {"deezer": 10, "apple": 16, "youtube": 35, "spotify": 20, "tidal": 1, "vagalume": 0}
SEGUNDOS_FIXOS = {"deezer": 30, "apple": 10, "youtube": 20, "spotify": 20, "tidal": 420, "vagalume": 30}


def estimar_minutos(obras: int, plataformas) -> int:
    """Minutos estimados para a primeira coleta de `obras` títulos nas plataformas pedidas (a Deezer roda sempre)."""
    pedidas = set(plataformas) | {"deezer"}
    segundos = sum(SEGUNDOS_POR_OBRA.get(p, 0) * obras + SEGUNDOS_FIXOS.get(p, 0) for p in pedidas)
    return max(1, round(segundos / 60))


def texto_da_estimativa(minutos: int) -> str:
    """"cerca de 25 minutos", "cerca de 1h30", arredondando para não fingir precisão."""
    if minutos < 10:
        return f"cerca de {minutos} minuto{'s' if minutos > 1 else ''}"
    if minutos < 60:
        return f"cerca de {round(minutos / 5) * 5} minutos"
    arredondado = round(minutos / 10) * 10
    horas, resto = divmod(arredondado, 60)
    return f"cerca de {horas}h{resto:02d}" if resto else f"cerca de {horas} hora{'s' if horas > 1 else ''}"


TODAS = ("deezer", "apple", "youtube", "spotify", "tidal", "vagalume")
ORDEM = ["YOUTUBE", "SPOTIFY", "TIDAL", "DEEZER", "VAGALUME", "APPLE MUSIC"]  # a das colunas da planilha


def executar(relatorio: Relatorio, config: Config, pasta, nomes_dos_coautores=(), youtube=False, limite_youtube=None,
             por_obra=12, ao_avancar=None, so_o_que_ja_foi_lido=False, plataformas=None, prints=True,
             mostrar_navegador=True, tela_inteira=False) -> list[Coleta]:
    """O fluxo inteiro, do relatório às coletas de cada plataforma, com as capturas de tela.

    `pasta` é a pasta do caso: cada plataforma guarda ali o que já leu e os seus prints (pasta/<plataforma>).
    `plataformas`: nomes de TODAS; sem isso, vale Deezer e, com `youtube`, o YouTube Music. `limite_youtube` é o
    número de obras buscadas em cada plataforma (a discografia da Deezer é sempre percorrida inteira).
    A Deezer vem primeiro porque ensina às outras quem grava o titular. As coletas saem na ordem da planilha.
    `tela_inteira`: cada print ganha também a foto do monitor inteiro; a janela do navegador fica visível.
    """
    from . import captura

    captura.TELA_INTEIRA, antes = bool(tela_inteira), captura.TELA_INTEIRA
    try:
        return _executar(relatorio, config, pasta, nomes_dos_coautores, youtube, limite_youtube, por_obra, ao_avancar,
                         so_o_que_ja_foi_lido, plataformas, prints, mostrar_navegador or tela_inteira)
    finally:
        captura.TELA_INTEIRA = antes


def _executar(relatorio, config, pasta, nomes_dos_coautores, youtube, limite_youtube, por_obra, ao_avancar,
              so_o_que_ja_foi_lido, plataformas, prints, mostrar_navegador) -> list[Coleta]:
    from .navegador import Navegador

    pasta, limite = Path(pasta), limite_youtube
    pedidas = set(plataformas) if plataformas is not None else {"deezer"} | ({"youtube"} if youtube else set())
    filtro = filtro_do_relatorio(relatorio, config)
    deezer = Deezer(filtro=filtro, cache=pasta / "deezer")
    coletas = [coletar_deezer(relatorio, config, deezer, nomes_dos_coautores, ao_avancar=ao_avancar,
                              limite_de_obras=limite if plataformas is not None else None)]
    recorrentes, pares = interpretes_conhecidos(coletas)
    if "apple" in pedidas:
        from .apple import AppleMusic
        coletas.append(coletar_apple(relatorio, config, AppleMusic(filtro=filtro, cache=pasta / "apple"), recorrentes, pares, limite,
                                     ao_avancar=ao_avancar))
        recorrentes, pares = interpretes_conhecidos(coletas)  # a Apple costuma revelar mais intérpretes
    if "vagalume" in pedidas:
        from .vagalume import Vagalume
        coletas.append(coletar_vagalume(relatorio, config, Vagalume(filtro=filtro, cache=pasta / "vagalume"), recorrentes, pares, limite,
                                        ao_avancar=ao_avancar))
    if "youtube" in pedidas:
        from .ytmusic import YouTubeMusic
        with YouTubeMusic(pasta / "youtube", filtro=filtro, ao_avancar=ao_avancar, escondido=not mostrar_navegador,
                          visivel=not so_o_que_ja_foi_lido, so_o_que_ja_foi_lido=so_o_que_ja_foi_lido) as ytm:
            coletas.append(coletar_youtube(relatorio, config, ytm, recorrentes, pares, por_obra, ao_avancar, limite))
    for coleta in coletas:  # o que já foi fotografado em outra rodada é reaproveitado
        if coleta.plataforma in ("DEEZER", "APPLE MUSIC", "VAGALUME"):
            reaproveitar_prints(coleta, pasta / coleta.plataforma.lower().replace(" ", "-"),
                                pagina_da_musica_na_apple if coleta.plataforma == "APPLE MUSIC" else None)
    com_tela = pedidas & {"spotify", "tidal"}
    falta_print = prints and not so_o_que_ja_foi_lido and any(
        g.classificacao.status in c.NEGATIVOS and not g.provas for k in coletas if k.plataforma in ("DEEZER", "APPLE MUSIC", "VAGALUME")
        for g in k.gravacoes)
    if com_tela or falta_print:
        with Navegador(filtro=filtro, visivel=not so_o_que_ja_foi_lido, ao_avancar=ao_avancar, escondido=not mostrar_navegador) as nav:
            if "spotify" in pedidas:
                from .spotify import Spotify
                coletas.append(coletar_spotify(relatorio, config, Spotify(nav, pasta / "spotify", so_o_que_ja_foi_lido),
                                               recorrentes, pares, limite, ao_avancar=ao_avancar))
            if "tidal" in pedidas:
                from .tidal import Tidal
                coletas.append(coletar_tidal(relatorio, config, Tidal(nav, pasta / "tidal", so_o_que_ja_foi_lido),
                                             recorrentes, pares, limite, ao_avancar=ao_avancar))
            if falta_print:
                preparos = {"DEEZER": abrir_creditos_da_deezer, "VAGALUME": abrir_autoria_do_vagalume,
                            "APPLE MUSIC": abrir_creditos_da_apple}
                config_local = Config(config.nomes_confirmados, config.nomes_a_confirmar, config.interpretes, list(recorrentes))

                def reclassificar(g):
                    return classificar(Item(titulo=g.titulo, interprete=g.interprete, creditos=g.creditos,
                                            vinculo=bool(g.vinculo) and not g.classificacao.presumido,
                                            plataforma=g.plataforma, link=g.link), relatorio, config_local)

                for coleta in coletas:
                    if coleta.plataforma in preparos:
                        capturar_paginas(coleta, nav, pasta / coleta.plataforma.lower().replace(" ", "-"), preparos[coleta.plataforma],
                                         ao_avancar, pagina_da_musica_na_apple if coleta.plataforma == "APPLE MUSIC" else None,
                                         reclassificar)
    return sorted(coletas, key=lambda k: ORDEM.index(k.plataforma) if k.plataforma in ORDEM else 99)


def main():
    from . import ecad, saida, ubc
    from cantor import planilha

    ap = argparse.ArgumentParser(description="Relatório do titular -> gravações classificadas -> planilha do escritório.")
    ap.add_argument("relatorio", type=Path, help="relatório analítico do ECAD (PDF) ou planilha de obras")
    ap.add_argument("--saida", type=Path, required=True, help="planilha .xlsx a gerar")
    ap.add_argument("--nomes", default="", help='outros nomes artísticos do titular, separados por ";"')
    ap.add_argument("--interpretes", default="", help='bandas e intérpretes que gravam o titular, separados por ";"')
    ap.add_argument("--sem-coautores", action="store_true", help="não percorrer a discografia dos pseudônimos dos coautores")
    ap.add_argument("--cache", type=Path, default=Path("dados/deezer"), help="pasta do cache de álbuns lidos")
    ap.add_argument("--youtube", type=Path, help="pasta para as provas do YouTube Music (liga a coleta; abre o navegador)")
    ap.add_argument("--so-youtube-ate", type=int, default=12, help="máximo de faixas abertas por obra no YouTube Music")
    ap.add_argument("--limite-youtube", type=int, help="buscar no YouTube Music só as N primeiras obras (teste rápido)")
    ap.add_argument("--sem-novas-leituras", action="store_true",
                    help="YouTube Music: montar as saídas só com o que já foi lido (o resto fica NÃO VERIFICADO)")
    args = ap.parse_args()

    if args.relatorio.suffix.lower() == ".pdf":
        relatorio = ecad.ler_pdf(args.relatorio)
    else:
        relatorio = ubc.ler_planilha(planilha.ler_planilha(args.relatorio.read_bytes(), args.relatorio.name))
    nomes = relatorio.nomes_artisticos()
    config = Config(
        nomes_confirmados=[n["nome"] for n in nomes if n["titular"]] + planilha.dividir_nomes_artisticos(args.nomes),
        interpretes=planilha.dividir_nomes_artisticos(args.interpretes),
    )
    coautores = [] if args.sem_coautores else [n["nome"] for n in nomes if not n["titular"]]
    deezer = Deezer(filtro=filtro_do_relatorio(relatorio, config), cache=args.cache)
    avisar = lambda t: print(" ", t, file=sys.stderr, flush=True)
    coletas = [coletar_deezer(relatorio, config, deezer, coautores, ao_avancar=avisar)]
    if args.youtube:
        from .ytmusic import YouTubeMusic

        recorrentes, pares = interpretes_conhecidos(coletas)
        with YouTubeMusic(args.youtube, filtro=filtro_do_relatorio(relatorio, config), ao_avancar=avisar,
                          visivel=not args.sem_novas_leituras, so_o_que_ja_foi_lido=args.sem_novas_leituras) as ytm:
            coletas.insert(0, coletar_youtube(relatorio, config, ytm, recorrentes, pares, args.so_youtube_ate, avisar,
                                              args.limite_youtube))
    args.saida.write_bytes(saida.planilha(relatorio, coletas))
    print(saida.resumo_em_texto(relatorio, coletas))
    if args.youtube:
        from . import provas

        base = args.saida.with_suffix("")
        Path(f"{base} - lista da petição (YouTube Music).docx").write_bytes(
            provas.docx_da_lista(relatorio, coletas, "YOUTUBE", "YouTube Music"))
        pdf, avisos = provas.pdf_de_provas(relatorio, coletas, "YOUTUBE", args.youtube, "YouTube Music")
        Path(f"{base} - provas (YouTube Music).pdf").write_bytes(pdf)
        firmes, a_confirmar = provas.itens_da_peticao(coletas, "YOUTUBE")
        print(f"Lista da petição (YouTube Music): {len(firmes)} firmes, {len(a_confirmar)} a confirmar")
        for aviso in avisos:
            print("AVISO nas provas:", aviso)
    print(f"Planilha salva em {args.saida}")


if __name__ == "__main__":
    main()
