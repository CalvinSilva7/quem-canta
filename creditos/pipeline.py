"""Do relatório à lista de gravações classificadas: descobrir, coletar, classificar.

A unidade é a GRAVAÇÃO (um link de faixa em uma plataforma), nunca a obra: a
mesma obra costuma ter várias gravações na mesma plataforma, cada uma com o seu
crédito. Deduplica-se por link.

Uso:  python -m creditos.pipeline relatorio.pdf --saida planilha.xlsx
"""

import argparse
import re
import sys
import time
from collections import Counter
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
    clipes: list[dict] = field(default_factory=list)  # clipes (Tidal) para o print à mão: [{"obra", "titulo", "artistas", "link", "medley"}]


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
    conhecidos = pares.get(titulo, [])
    if any(c.mesmo_artista(parte, n) for n in conhecidos for parte in c.partes_do_interprete(interprete)):
        return True
    # Dupla de nomes de uma palavra só ("Fulano e Beltrano"): a divisão em partes a desfaz; compara o nome inteiro.
    return any(_palavras(n) == _palavras(interprete) or _nome_dentro(n, interprete) or _mesma_grafia(n, interprete)
               or _e_o_conferido(n, interprete) for n in conhecidos)


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


def _consulta_enxuta(consulta: str) -> str:
    """A mesma consulta sem as palavras que atrapalham a busca de algumas plataformas: "feat", "part." e o "e" ou "&"
    das duplas. No Spotify, "Música Fulano Feat Beltrano" e "Música Fulano e Beltrano" não trazem a faixa;
    "Música Fulano Beltrano" traz, em primeiro lugar."""
    import re
    limpa = re.sub(r"(?i)\b(feat|ft|part|participa[cç][aã]o)\b\.?", " ", consulta)
    return " ".join(p for p in re.sub(r"[&,]", " ", limpa).split() if p.lower() != "e")


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
                    por_interprete=4, por_obra=40, ao_avancar=None) -> Coleta:
    """Spotify: busca "título + intérprete" e abre a janela de créditos de cada faixa de quem tem ligação com o titular.

    De cada obra, até `por_interprete` faixas de cada artista (os mesmos artistas escritos de jeitos diferentes contam
    juntos) e, no total, `por_obra`. O limite era por obra (8): numa obra com muitos intérpretes, as versões ao vivo
    de uns gastavam as vagas e os outros nem eram abertos (e saíam "N/A" na planilha). O que o limite cortar vira aviso."""
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
            ligados, abertos, por_artista, cortadas = _ligados(relatorio, config, recorrentes, pares, titulo), 0, Counter(), Counter()
            consultas = _consultas(titulo, principais, pares)[:-1]  # sem a busca só pelo título: traria só homônimos
            # mais a forma enxuta de cada uma, quando muda alguma coisa (ver `_consulta_enxuta`)
            consultas = list(dict.fromkeys(q for consulta in consultas for q in (consulta, _consulta_enxuta(consulta))))
            for consulta in consultas:
                for cand in spotify.buscar(consulta):
                    if cand.faixa in vistos or c._titulo_base(cand.titulo) != base:
                        continue
                    vistos.add(cand.faixa)
                    # A mesma comparação de nomes das outras plataformas: dupla de nomes de uma palavra ("Fulano e
                    # Beltrano"), "&" por "e" e letra dobrada não fazem outro artista.
                    if not any(any(c.mesmo_artista(parte, n) for parte in c.partes_do_interprete(cand.interprete))
                               or _nome_dentro(n, cand.interprete) or _mesma_grafia(n, cand.interprete)
                               or (n in pares.get(titulo, []) and (_palavra_do_nome(n, cand.interprete) or _e_o_conferido(n, cand.interprete)))
                               for n in ligados):
                        coleta.sem_vinculo_nao_abertos += 1
                    elif abertos < por_obra and por_artista[c.chave_do_interprete(cand.interprete)] < por_interprete:
                        abertos += 1
                        por_artista[c.chave_do_interprete(cand.interprete)] += 1
                        fila.append((cand, titulo, consulta))
                    else:
                        cortadas[cand.interprete] += 1
            if cortadas:
                coleta.avisos.append(
                    f'"{titulo}": {_faixas_nao_abertas(sum(cortadas.values()))} do Spotify pelo limite de {por_interprete} por '
                    f"intérprete ({', '.join(f'{n} ({q})' for n, q in cortadas.most_common())}). Confira à mão se faltar alguma."
                )
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


def _faixas_nao_abertas(n: int) -> str:
    return "1 faixa não foi aberta" if n == 1 else f"{n} faixas não foram abertas"


def _fila_da_amazon(relatorio, config, amazon, recorrentes, pares, limite_de_obras, por_interprete, coleta, avisar, nome,
                    so_o_titulo=False) -> list:
    """As faixas da Amazon Music a abrir: [(candidato, título da obra, consulta)]. A busca é a do site, que não pede
    login e funciona melhor só com o título; por isso vai o título e, depois, o título com cada intérprete.
    De cada intérprete entram até `por_interprete` faixas por obra."""
    return [item for _, _, grupo in _filas_da_amazon(relatorio, config, amazon, recorrentes, pares, limite_de_obras, por_interprete,
                                                       coleta, avisar, nome, so_o_titulo) for item in grupo]


def _filas_da_amazon(relatorio, config, amazon, recorrentes, pares, limite_de_obras, por_interprete, coleta, avisar, nome,
                     so_o_titulo=False):
    """O mesmo que `_fila_da_amazon`, obra por obra: dá (número da obra, total de obras, faixas dela) assim que a
    busca daquela obra termina, para quem quiser abrir as faixas antes de buscar a obra seguinte."""
    principais = _principais(relatorio, config, recorrentes)
    vistos = set()
    titulos = _titulos(relatorio, limite_de_obras, coleta, nome)
    for i, (base, titulo) in enumerate(titulos, start=1):
        avisar(f"{nome}: buscando {i}/{len(titulos)}")
        ligados, por_nome, cortadas = _ligados(relatorio, config, recorrentes, pares, titulo), {}, Counter()
        # `so_o_titulo`: a busca já devolve a lista inteira de músicas com aquele título (Claro Música). Só quando um
        # intérprete da obra não aparece nela (título comum demais) é que vai a busca do título com o nome dele.
        consultas = [titulo, *([f"{titulo} {n}" for n in pares.get(titulo, [])] if so_o_titulo else _consultas(titulo, principais, pares)[:-1])]
        for consulta in consultas:
            if so_o_titulo and consulta != titulo and all(tuple(_palavras(n)) in por_nome for n in pares.get(titulo, [])):
                continue
            for cand in amazon.buscar(consulta):
                # o título é limpo também do nome do artista daquele resultado ("Banda Tal - Música")
                if cand.faixa in vistos or not _e_da_obra(_titulo_do_video(cand.titulo, relatorio, [*ligados, cand.interprete]), titulo, relatorio) == "exato":
                    continue
                vistos.add(cand.faixa)
                elo = next((n for n in ligados if any(c.mesmo_artista(parte, n) for parte in c.partes_do_interprete(cand.interprete))
                            or _nome_dentro(n, cand.interprete) or _mesma_grafia(n, cand.interprete)
                            or (n in pares.get(titulo, []) and (_palavra_do_nome(n, cand.interprete) or _e_o_conferido(n, cand.interprete)))), "")
                if not elo:
                    coleta.sem_vinculo_nao_abertos += 1
                elif len(por_nome.setdefault(tuple(_palavras(elo)), [])) < por_interprete:
                    por_nome[tuple(_palavras(elo))].append((cand, titulo, consulta))
                else:
                    cortadas[elo] += 1
        if so_o_titulo and hasattr(amazon, "buscar_pelo_artista"):
            # Último recurso, para o intérprete da obra que a busca não trouxe: a página do artista e os álbuns dele.
            for n in pares.get(titulo, []):
                if por_nome.get(tuple(_palavras(n))):
                    continue
                mesmo = lambda exibido, n=n: (any(c.mesmo_artista(parte, n) for parte in c.partes_do_interprete(exibido))
                                              or _nome_dentro(n, exibido) or _mesma_grafia(n, exibido))
                da_obra = lambda exibido, ligados=ligados: _e_da_obra(_titulo_do_video(exibido, relatorio, ligados), titulo, relatorio) == "exato"
                for cand in amazon.buscar_pelo_artista(n, mesmo, da_obra, no_maximo=por_interprete):
                    if cand.faixa not in vistos:
                        vistos.add(cand.faixa)
                        por_nome.setdefault(tuple(_palavras(n)), []).append((cand, titulo, f"página do artista {n}"))
        if cortadas:
            coleta.avisos.append(
                f'"{titulo}": {_faixas_nao_abertas(sum(cortadas.values()))} de {nome} pelo limite de {por_interprete} por '
                f"intérprete ({', '.join(f'{n} ({q})' for n, q in cortadas.most_common())}). Confira à mão se faltar alguma versão."
            )
        yield i, len(titulos), [item for grupo in por_nome.values() for item in grupo]


def coletar_amazon(relatorio: Relatorio, config: Config, amazon, recorrentes=(), pares=None, limite_de_obras=None,
                   por_interprete=3, ao_avancar=None, fila=None) -> Coleta:
    """Amazon Music na web: acha a faixa de cada intérprete ligado ao titular e fotografa o menu dela, que não tem
    item de créditos. É um print por música. Com `fila` ([(candidato, título da obra, consulta)]), não busca nada:
    abre só essas faixas (as que o aplicativo de desktop mostrou sem o titular, em `coletar_amazon_completa`)."""
    from types import SimpleNamespace
    from .captura import PaginaTraduzida
    from .navegador import Bloqueio

    inicio, pares, avisar = time.monotonic(), pares or {}, ao_avancar or (lambda texto: None)
    coleta = Coleta("AMAZON - SITE", sementes=_principais(relatorio, config, recorrentes))
    lidas = []
    try:
        if fila is None:
            fila = _fila_da_amazon(relatorio, config, amazon, recorrentes, pares, limite_de_obras, por_interprete, coleta, avisar, "Amazon Music")
        for i, (cand, titulo, consulta) in enumerate(fila, start=1):
            avisar(f"Amazon Music: lendo faixa {i}/{len(fila)}")
            leitura = amazon.ler(cand, titulo)
            faixa = SimpleNamespace(
                titulo=leitura.titulo or cand.titulo, interprete=cand.interprete, creditos=[], link=leitura.link or cand.link,
                coleta=leitura.coleta, erro=leitura.erro, provas=leitura.provas,
                nota="na versão web da Amazon Music não existe tela de créditos: o print mostra o menu da faixa, sem esse item")
            lidas.append((faixa, SimpleNamespace(titulo=leitura.album, fornecedor="", lancamento=""), f'busca "{consulta}"'))
    except (Bloqueio, PaginaTraduzida) as e:
        _parar(coleta, e)
    _classificar_faixas(coleta, relatorio, config, pares, lidas, recorrentes)
    coleta.albuns_lidos, coleta.requisicoes, coleta.segundos = len(lidas), amazon.nav.navegacoes, time.monotonic() - inicio
    return coleta


def coletar_claro(relatorio: Relatorio, config: Config, claro, recorrentes=(), pares=None, limite_de_obras=None,
                  por_interprete=3, ao_avancar=None) -> Coleta:
    """Claro Música: acha a faixa de cada intérprete ligado ao titular pela lista de músicas da busca, abre o
    álbum e lê os autores no cartão de informações da faixa. É um print por música. Exige a sessão logada."""
    from types import SimpleNamespace
    from .captura import PaginaTraduzida
    from .claro import SemLogin, autores as autores_do_cartao
    from .navegador import Bloqueio

    inicio, pares, avisar = time.monotonic(), pares or {}, ao_avancar or (lambda texto: None)
    coleta = Coleta("CLARO", sementes=_principais(relatorio, config, recorrentes))
    lidas = []
    try:
        fila = _fila_da_amazon(relatorio, config, claro, recorrentes, pares, limite_de_obras, por_interprete, coleta, avisar,
                               "Claro Música", so_o_titulo=True)
        for i, (cand, titulo, consulta) in enumerate(fila, start=1):
            avisar(f"Claro Música: lendo faixa {i}/{len(fila)}")
            leitura = claro.ler(cand, titulo)
            # os autores saem sempre do cartão guardado: assim uma leitura antiga ganha as correções da separação dos nomes
            creditos = autores_do_cartao(leitura.informacoes) if leitura.informacoes else leitura.creditos
            faixa = SimpleNamespace(titulo=leitura.titulo or cand.titulo, interprete=cand.interprete, creditos=creditos,
                                    link=cand.link, coleta=leitura.coleta, erro=leitura.erro, provas=leitura.provas,
                                    nota='lido em "Informações e créditos", no menu da faixa')
            lidas.append((faixa, SimpleNamespace(titulo=leitura.album, fornecedor=leitura.gravadora, lancamento=leitura.ano),
                          f'busca "{consulta}"'))
    except (Bloqueio, PaginaTraduzida, SemLogin) as e:
        _parar(coleta, e)
    except Exception as e:  # erro técnico inesperado: só a Claro para, com o motivo; as outras plataformas seguem
        _parar(coleta, e)
    nao_carregaram = getattr(claro, "nao_carregaram", [])
    if nao_carregaram:
        coleta.avisos.append(
            f"{len(nao_carregaram)} páginas da Claro Música não abriram (o site demorou demais), nem na segunda tentativa: "
            + "; ".join(nao_carregaram[:20]) + ("…" if len(nao_carregaram) > 20 else "")
            + ". As músicas que dependiam delas podem não ter sido achadas: rode a coleta de novo para tentar outra vez.")
    _classificar_faixas(coleta, relatorio, config, pares, lidas, recorrentes)
    coleta.albuns_lidos, coleta.requisicoes, coleta.segundos = len(lidas), claro.nav.navegacoes, time.monotonic() - inicio
    return coleta


def coletar_palco(relatorio: Relatorio, config: Config, palco, recorrentes=(), pares=None, limite_de_obras=None,
                  por_interprete=3, ao_avancar=None) -> Coleta:
    """Palco MP3: acha a música de cada intérprete ligado ao titular pela busca do site e lê a linha "Composição:"
    da página dela. Um print por música; dois quando o site corta a linha (a página de créditos mostra o resto)."""
    from types import SimpleNamespace
    from .captura import PaginaTraduzida
    from .navegador import Bloqueio

    inicio, pares, avisar = time.monotonic(), pares or {}, ao_avancar or (lambda texto: None)
    coleta = Coleta("PALCO MP3", sementes=_principais(relatorio, config, recorrentes))
    lidas = []
    try:
        fila = _fila_da_amazon(relatorio, config, palco, recorrentes, pares, limite_de_obras, por_interprete, coleta, avisar, "Palco MP3")
        for i, (cand, titulo, consulta) in enumerate(fila, start=1):
            avisar(f"Palco MP3: lendo faixa {i}/{len(fila)}")
            leitura = palco.ler(cand, titulo)
            # No Palco quem publica escreve o título como quer ("03 Tal Música - Banda Tal 2014"). A faixa já foi
            # aceita na busca pelo título limpo; é ele que vale para classificar, e o exibido fica na observação.
            exibido = leitura.titulo or cand.titulo
            limpo = _titulo_do_video(exibido, relatorio, [*_ligados(relatorio, config, recorrentes, pares, titulo), cand.interprete])
            faixa = SimpleNamespace(titulo=limpo if _e_da_obra(limpo, titulo, relatorio) == "exato" else exibido,
                                    interprete=leitura.interprete or cand.interprete,
                                    creditos=leitura.creditos, link=cand.link, coleta=leitura.coleta, erro=leitura.erro, provas=leitura.provas,
                                    nota='lido na linha "Composição:" da página da música' + (
                                        "; a página corta a linha, e os nomes vieram dos dados dela (ver o print dos créditos)" if leitura.cortada else "")
                                    + (f'; título como aparece no Palco MP3: "{exibido}"' if normalizar(exibido) != normalizar(limpo) else ""))
            lidas.append((faixa, SimpleNamespace(titulo="", fornecedor="", lancamento=""), f'busca "{consulta}"'))
    except (Bloqueio, PaginaTraduzida) as e:
        _parar(coleta, e)
    _classificar_faixas(coleta, relatorio, config, pares, lidas, recorrentes)
    coleta.albuns_lidos, coleta.requisicoes, coleta.segundos = len(lidas), palco.nav.navegacoes, time.monotonic() - inicio
    return coleta


# As pastas em que cada plataforma guarda, dentro do caso, o que leu e os prints que tirou.
PASTAS_DO_CASO = {"deezer": ["deezer"], "apple": ["apple", "apple-music"], "youtube": ["youtube"], "spotify": ["spotify"],
                  "tidal": ["tidal"], "vagalume": ["vagalume"], "amazon": ["amazon-app", "amazon-site"], "amazon_app": ["amazon-app"], "claro": ["claro"], "palco": ["palco-mp3"]}


def limpar_o_guardado(pasta_do_caso, plataformas) -> list[str]:
    """Apaga o que as plataformas escolhidas guardaram neste caso (leituras e prints). Devolve as pastas apagadas.

    Só mexe nas pastas dessas plataformas, dentro da pasta do caso: outros casos, as outras plataformas e a sessão
    logada da Claro Música e da Deezer (que fica fora do caso) não são tocados.
    """
    import shutil

    apagadas = []
    for plataforma in plataformas:
        for nome in PASTAS_DO_CASO.get(plataforma, []):
            pasta = Path(pasta_do_caso) / nome
            if pasta.is_dir():
                shutil.rmtree(pasta)
                apagadas.append(nome)
    return apagadas


def perfil_da_claro(pasta_do_caso) -> Path:
    """Onde o navegador do app guarda a sessão da Claro Música: na pasta de dados, fora de qualquer caso."""
    return Path(pasta_do_caso).parent.parent / "perfil-claro"


def perfil_da_deezer(pasta_do_caso) -> Path:
    """Onde o navegador do app guarda a sessão da Deezer: na pasta de dados, fora de qualquer caso."""
    return Path(pasta_do_caso).parent.parent / "perfil-deezer"


def coletar_amazon_app(relatorio: Relatorio, config: Config, amazon, aplicativo, recorrentes=(), pares=None, limite_de_obras=None,
                       por_interprete=3, ao_avancar=None, abertas=None) -> Coleta:
    """Amazon Music no aplicativo de desktop: as faixas são achadas pela busca do site (`amazon`) e abertas no
    aplicativo (`aplicativo`), que é onde aparece o item "Créditos" com os compositores. Dois prints por música.
    Em `abertas` (uma lista), ficam as faixas que o aplicativo abriu: [(candidato, título da obra, consulta)]."""
    from types import SimpleNamespace
    from .amazon_app import SEM_CREDITO_NA_JANELA, AplicativoIndisponivel
    from .captura import PaginaTraduzida
    from .navegador import Bloqueio

    inicio, pares, avisar = time.monotonic(), pares or {}, ao_avancar or (lambda texto: None)
    nome = "Amazon Music (aplicativo)"
    coleta = Coleta("AMAZON", sementes=_principais(relatorio, config, recorrentes))
    lidas = []
    try:
        # Música por música (decisão do dono, 15/10/2026): cada obra é buscada no site e as faixas dela já vão para o
        # aplicativo, antes da obra seguinte. Assim o aplicativo abre nos primeiros minutos, e não depois de horas de busca.
        for i, total, faixas in _filas_da_amazon(relatorio, config, amazon, recorrentes, pares, limite_de_obras, por_interprete,
                                                 coleta, avisar, nome):
            for cand, titulo, consulta in faixas:
                avisar(f"{nome}: lendo faixa da obra {i}/{total}")
                leitura = aplicativo.ler(cand, titulo)
                if abertas is not None:
                    abertas.append((cand, titulo, consulta))
                # leitura guardada por versão antiga pode trazer, como se fosse nome, o aviso "Créditos indisponíveis" da janela
                creditos = [n for n in leitura.creditos if not normalizar(n).startswith(SEM_CREDITO_NA_JANELA)]
                faixa = SimpleNamespace(titulo=leitura.titulo or cand.titulo, interprete=cand.interprete, creditos=creditos,
                                        link=cand.link, coleta=leitura.coleta, erro=leitura.erro, provas=leitura.provas,
                                        nota="lido no aplicativo de desktop da Amazon Music; o link é o da mesma faixa no site")
                lidas.append((faixa, SimpleNamespace(titulo=leitura.album, fornecedor="", lancamento=""), f'busca "{consulta}"'))
    except (Bloqueio, PaginaTraduzida, AplicativoIndisponivel) as e:
        _parar(coleta, e)
    _classificar_faixas(coleta, relatorio, config, pares, lidas, recorrentes)
    coleta.albuns_lidos, coleta.requisicoes, coleta.segundos = len(lidas), aplicativo.navegacoes, time.monotonic() - inicio
    return coleta


def coletar_amazon_completa(relatorio: Relatorio, config: Config, amazon, aplicativo, recorrentes=(), pares=None,
                            limite_de_obras=None, por_interprete=3, ao_avancar=None) -> list[Coleta]:
    """Amazon Music como o escritório faz à mão (decisão do dono, 15/10/2026): primeiro o aplicativo de desktop, em
    todas as músicas, com os dois prints (menu e janela de créditos). Depois, só para as músicas em que o titular
    não aparece no aplicativo (sem créditos ou crédito de outros), o print do menu da mesma faixa no site, que não
    tem tela de créditos. Saem duas coletas, "AMAZON" (aplicativo) e "AMAZON - SITE", cada uma com as suas provas."""
    abertas = []
    no_aplicativo = coletar_amazon_app(relatorio, config, amazon, aplicativo, recorrentes, pares, limite_de_obras,
                                       por_interprete, ao_avancar, abertas=abertas)
    negativas = {g.link for g in no_aplicativo.gravacoes if g.classificacao.status in c.NEGATIVOS}
    fila = [item for item in abertas if item[0].link in negativas]
    no_site = coletar_amazon(relatorio, config, amazon, recorrentes, pares, limite_de_obras, por_interprete, ao_avancar, fila=fila)
    if no_aplicativo.interrompida:
        no_site.avisos.append("O aplicativo de desktop parou antes do fim: o site só fotografou as músicas que o aplicativo já "
                              "tinha lido sem o titular. Rode de novo para continuar.")
    return [no_aplicativo, no_site]


def _isrcs_para_o_tidal(titulos: dict, pares: dict, conhecidas, deezer, interpretes, so_os_pares, avisar, coleta) -> list[tuple]:
    """[(ISRC, título da obra, de onde veio)] das gravações das obras buscadas. Primeiro as que a Deezer já leu nesta
    rodada (`conhecidas`: [(obra, intérprete, ISRC)]); para as obras que ficaram sem nenhuma, a busca da Deezer por
    "título + intérprete" (os conferidos; sem planilha, os principais do titular), só com título igual e intérprete
    parecido."""
    from cantor.matching import nomes_parecidos

    from .deezer import ErroDeColeta

    pedidos = {}
    for obra, interprete, isrc in conhecidas or []:
        base = c._titulo_base(obra)
        if isrc and base in titulos:
            pedidos.setdefault(str(isrc).upper(), (titulos[base], f"gravação de {interprete} lida na Deezer"))
    if deezer is not None:
        cobertas = {c._titulo_base(t) for t, _ in pedidos.values()}
        faltam = [(base, titulo) for base, titulo in titulos.items() if base not in cobertas]
        for i, (base, titulo) in enumerate(faltam, start=1):
            avisar(f"Tidal: procurando a gravação na Deezer {i}/{len(faltam)}")
            for nome in pares.get(titulo) or ([] if so_os_pares else interpretes[:3]):
                try:
                    candidatas = deezer.buscar_faixas(f"{titulo} {nome}", maximo=50)
                    boas = [x for x in candidatas if c._titulo_base(x["titulo"]) == base
                            and nomes_parecidos(nome, x["interprete"], parcial=True)][:4]
                    for x in boas:
                        isrc = deezer.isrc(x["id"])
                        if isrc:
                            pedidos.setdefault(isrc.upper(), (titulo, f"{x['interprete']}, achada na busca da Deezer"))
                except ErroDeColeta as e:
                    coleta.avisos.append(f'Tidal: a busca da Deezer por "{titulo}" falhou ({e}); a obra fica sem procurar')
    achadas = {c._titulo_base(t) for t, _ in pedidos.values()}
    sem_isrc = [t for base, t in titulos.items() if base not in achadas]
    if sem_isrc:
        coleta.avisos.append(f"Tidal: {len(sem_isrc)} obras sem gravação achada na Deezer (de onde vem o código ISRC) não foram "
                             "procuradas no Tidal: " + "; ".join(sem_isrc[:40]) + ("…" if len(sem_isrc) > 40 else ""))
    return [(isrc, titulo, origem) for isrc, (titulo, origem) in pedidos.items()]


def _artista_no_tidal(nome: str, deezer, api) -> set:
    """O artista no Tidal (identificadores), achado pelo nome: o artista com esse nome na Deezer, o ISRC de faixas
    dele e as mesmas faixas no Tidal. Com mais de uma faixa, vale o artista que está em todas (tira as participações)."""
    for artista in deezer.artistas(nome, maximo=1):
        grupos = []
        for faixa in deezer.faixas_do_artista(artista["id"], maximo=3):
            isrc = deezer.isrc(faixa)
            ids = {a for f in (api.faixas(isrc) if isrc else []) for a in f.get("artistas") or []}
            if ids:
                grupos.append(ids)
        if grupos:
            comum = set.intersection(*grupos)
            return comum or grupos[0]
    return set()


def _clipes_do_tidal(api, relatorio: Relatorio, titulos: dict, artistas, avisar, coleta: Coleta, pares=None) -> None:
    """Os clipes (vídeos) do Tidal das obras buscadas, achados pela API a partir dos artistas das faixas, vão para
    `coleta.clipes`: o print deles é tirado à mão (decisão do escritório, 16/10/2026). O vídeo do Tidal só toca no
    Google Chrome de verdade (proteção contra cópia), e o navegador do app mostraria a tela preta. A lista sai no fim
    das provas do Tidal, com o link de cada clipe; o clipe que junta várias músicas vem marcado como medley. O clipe
    de um artista que não é o intérprete conferido da obra (`pares`) pode ser outra música com o mesmo nome: vai
    para uma parte separada, "conferir se é a mesma música"."""
    from .tidal_api import ErroNaAPI

    clipes = {}
    for i, artista in enumerate(sorted(artistas), start=1):
        avisar(f"Tidal: procurando clipes {i}/{len(artistas)}")
        try:
            for clipe in api.clipes(artista):
                clipes.setdefault(clipe["id"], clipe)
        except ErroNaAPI as e:
            coleta.avisos.append(f"Tidal: a lista de clipes de um artista não veio ({e}); os clipes dele ficam sem procurar")
    for clipe in clipes.values():
        tipo, obras = c.casar_titulo(clipe["titulo"], relatorio)
        if tipo and any(c._titulo_base(o.titulo) in titulos for o in obras):
            conferidos = list(dict.fromkeys(n for o in obras for n in (pares or {}).get(o.titulo, [])))
            # a planilha às vezes junta dois intérpretes na mesma célula com vírgula ("Fulano e Beltrano, Sicrano")
            separados = {o.titulo: [p for n in (pares or {}).get(o.titulo, []) for p in re.split(r"\s*,\s*", n) if p] for o in obras}
            do_conferido = any(_par_conhecido(separados, o.titulo, a) for o in obras for a in clipe.get("artistas") or [])
            coleta.clipes.append({"obra": "; ".join(dict.fromkeys(o.titulo for o in obras)), "titulo": clipe["titulo"],
                                  "artistas": clipe.get("artistas") or [], "link": f"https://tidal.com/video/{clipe['id']}",
                                  "medley": tipo == "medley", "do_conferido": do_conferido, "conferidos": conferidos})
    coleta.clipes.sort(key=lambda k: (normalizar(k["obra"]), normalizar(k["titulo"])))
    if coleta.clipes:
        coleta.avisos.append(f"Tidal: {len(coleta.clipes)} clipes das obras buscadas. O print deles é à mão: a lista com os links "
                             "está no fim das provas do Tidal")


def coletar_tidal(relatorio: Relatorio, config: Config, tidal, recorrentes=(), pares=None, limite_de_obras=None,
                  ao_avancar=None, nomes_no_maximo=8, so_os_pares=False, api=None, deezer=None, conhecidas=None) -> Coleta:
    """Tidal: lê a página de créditos de cada álbum (sem login) e tira o print das faixas sem crédito ou com crédito
    errado, na tela do tocador com a aba de créditos aberta.

    Como achar as faixas: com a API oficial (`api`, chaves do escritório), pelo ISRC de cada gravação, que vem da
    Deezer (`conhecidas`, as lidas nesta rodada, e `deezer`, a busca para as obras que faltarem). Sem as chaves, o
    jeito antigo: a discografia de quem grava o titular, pela busca do site, que sem login acha pouco. Com
    `so_os_pares` (planilha conferida pelo compositor), valem os intérpretes da planilha.
    """
    from .captura import PaginaTraduzida
    from .navegador import Bloqueio
    from .tidal_api import ErroNaAPI

    inicio, pares, avisar = time.monotonic(), pares or {}, ao_avancar or (lambda texto: None)
    coleta = Coleta("TIDAL")
    titulos = dict(_titulos(relatorio, limite_de_obras, coleta, "Tidal"))
    interpretes = _principais(relatorio, config, recorrentes)
    for titulo in titulos.values():  # mais os intérpretes que outra plataforma já mostrou para as obras buscadas
        interpretes += [n for n in pares.get(titulo, []) if n not in interpretes]
    coleta.sementes = list({normalizar(n): n for n in reversed(interpretes)}.values())[::-1][:nomes_no_maximo]
    if so_os_pares:
        coleta.sementes = list({normalizar(n): n for titulo in titulos.values() for n in pares.get(titulo, [])}.values())
    lidas, de_onde = [], {}
    try:
        albuns, pedidas, artistas = {}, {}, set()  # álbum -> de onde veio; álbum -> {faixa: ISRC}; artistas (só pela API)
        if api is not None:
            coleta.sementes = []
            pedidos = _isrcs_para_o_tidal(titulos, pares, conhecidas, deezer, interpretes, so_os_pares, avisar, coleta)
            for i, (isrc, titulo, origem) in enumerate(pedidos, start=1):
                avisar(f"Tidal: procurando a gravação {i}/{len(pedidos)} pelo ISRC")
                try:
                    achadas = api.faixas(isrc)
                except ErroNaAPI as e:
                    coleta.avisos.append(f"Tidal: a API parou de responder ({e}); as gravações que faltavam ficam sem procurar")
                    break
                for faixa in achadas:
                    albuns.setdefault(faixa["album"], f"ISRC {isrc} ({origem})")
                    pedidas.setdefault(faixa["album"], {})[faixa["id"]] = isrc
                    artistas.update(faixa.get("artistas") or [])
            # Para os clipes, também os artistas das obras que ficaram sem gravação na Deezer (sem ISRC): achados pelo
            # nome do intérprete conferido, por outras faixas dele.
            if deezer is not None:
                from .deezer import ErroDeColeta

                cobertas = {c._titulo_base(t) for _, t, _ in pedidos}
                nomes = list(dict.fromkeys(n for base, t in titulos.items() if base not in cobertas
                                           for n in (pares.get(t) or ([] if so_os_pares else interpretes[:3]))))
                for i, nome in enumerate(nomes, start=1):
                    avisar(f"Tidal: procurando o artista {i}/{len(nomes)} para os clipes")
                    try:
                        artistas.update(_artista_no_tidal(nome, deezer, api))
                    except (ErroNaAPI, ErroDeColeta):
                        continue
        else:
            coleta.avisos.append("Tidal sem as chaves da API: as faixas foram procuradas pela busca do site, que sem login acha "
                                 "pouco. Com o arquivo chaves-tidal.txt na pasta de dados, o app procura pelo ISRC")
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
            quais = pedidas.get(album_id)
            for faixa in album.faixas:
                # pelo ISRC, só as faixas pedidas (a página do álbum lista também as outras); sempre só as obras buscadas
                if quais is not None and faixa.id not in quais:
                    continue
                if c._titulo_base(faixa.titulo) in titulos:
                    if quais is not None:
                        faixa.isrc = quais[faixa.id]
                    lidas.append((faixa, album, origem))
                    de_onde[faixa.link] = (album_id, faixa)
        if api is not None and artistas:
            _clipes_do_tidal(api, relatorio, titulos, artistas, avisar, coleta, pares)
        _classificar_faixas(coleta, relatorio, config, pares, lidas, recorrentes)
        from . import captura as _captura
        # print com a aba de créditos vazia (antes da 0.5.52, a música sem créditos podia sair sem a aba aberta) é refeito
        ja_tiradas = {(f.get("url"), str((f.get("exibido") or {}).get("faixa_no_album", ""))): f["captura"]
                      for f in _captura.capturas_guardadas(tidal.pasta) if (f.get("exibido") or {}).get("creditos")}
        for g in coleta.gravacoes:
            if g.classificacao.status in c.NEGATIVOS and not g.provas and g.link in de_onde:  # o clipe já vem fotografado
                album_id, faixa = de_onde[g.link]
                # só o print no formato do tocador (endereço da faixa): o antigo, da página de créditos do álbum, é refeito
                guardada = ja_tiradas.get((f"https://tidal.com/album/{album_id}/track/{faixa.id}", str(faixa.numero)))
                if guardada:
                    g.provas.append(guardada)
        negativas = [] if getattr(tidal, "so_o_que_ja_foi_lido", False) else [
            g for g in coleta.gravacoes if g.classificacao.status in c.NEGATIVOS and not g.provas and g.link in de_onde]
        for i, g in enumerate(negativas, start=1):
            avisar(f"Tidal: print {i}/{len(negativas)}")
            album_id, faixa = de_onde[g.link]
            try:
                g.provas.append(tidal.capturar(album_id, faixa, g.obra))
            except (Bloqueio, PaginaTraduzida):
                raise
            except Exception as e:
                coleta.avisos.append(f'print de "{g.titulo}" ({g.link}) não pôde ser tirado: {type(e).__name__}: '
                                     f'{str(e).splitlines()[0][:160] if str(e) else ""}')
    except (Bloqueio, PaginaTraduzida) as e:
        _parar(coleta, e)
        if not coleta.gravacoes:
            _classificar_faixas(coleta, relatorio, config, pares, lidas, recorrentes)
    coleta.requisicoes = tidal.nav.navegacoes + (api.requisicoes if api is not None else 0)
    coleta.segundos = time.monotonic() - inicio
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


def reaproveitar_prints(coleta: Coleta, pasta, endereco=None, etapas=None) -> None:
    """Liga cada gravação negativa ao print que já existe dela na pasta (mesmo endereço, mesmos créditos na tela):
    numa nova rodada, o que já foi fotografado não é fotografado de novo.

    `etapas` (ex.: ("menu", "creditos") na Deezer): a prova são vários prints, um por etapa. Só reaproveita se houver
    o de todas, o mais recente de cada; faltando um, a música é fotografada de novo.
    """
    from . import captura

    def fim(url):  # o último trecho do endereço identifica a faixa, mesmo quando a plataforma redireciona
        return str(url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]

    def mesmos_creditos(ficha, g):
        return {normalizar(n) for n in (ficha.get("exibido") or {}).get("creditos_lidos", [])} == {normalizar(n) for n in g.creditos}

    por_endereco = {}
    for ficha in captura.capturas_guardadas(pasta):
        por_endereco.setdefault(fim(ficha.get("url")), []).append(ficha)
    for g in coleta.gravacoes:
        if g.classificacao.status in c.NEGATIVOS and not g.provas:
            fichas = [f for f in por_endereco.get(fim(endereco(g.link) if endereco else g.link), []) if mesmos_creditos(f, g)]
            if not etapas:
                g.provas += [f["captura"] for f in fichas[:1]]
                continue
            ultimas = {}
            for f in sorted(fichas, key=lambda f: f.get("capturado_em", "")):
                ultimas[(f.get("exibido") or {}).get("etapa")] = f["captura"]
            if all(e in ultimas for e in etapas):
                g.provas += [ultimas[e] for e in etapas]


def capturar_paginas(coleta: Coleta, navegador, pasta, preparar=None, ao_avancar=None, endereco=None, reclassificar=None,
                     com_o_menu=False) -> None:
    """Print das gravações sem crédito ou com crédito errado de uma plataforma lida sem navegador (Deezer, Apple
    Music, Vagalume): abre o link de cada uma, deixa `preparar(navegador)` abrir a tela de créditos, e captura.

    Se `preparar` devolver os autores que a tela mostra e eles forem diferentes dos que tinham sido lidos nos dados
    da página, vale a tela: a gravação é reclassificada, e só continua com print se continuar negativa.

    `com_o_menu` (Deezer): a prova são dois prints, o menu aberto um clique antes da tela de créditos e a tela.
    `preparar` recebe `antes_do_clique`, que fotografa o menu.
    """
    from . import captura
    from .deezer import SemLogin
    from .navegador import Bloqueio, PerfilEmUso

    # param a fila da plataforma; qualquer outro erro só tira o print daquela gravação
    param = (Bloqueio, captura.PaginaTraduzida, SemLogin, PerfilEmUso)
    avisar = ao_avancar or (lambda texto: None)
    negativas = [g for g in coleta.gravacoes if g.classificacao.status in c.NEGATIVOS and not g.provas]
    try:
        for i, g in enumerate(negativas, start=1):
            avisar(f"{coleta.plataforma.title()}: print {i}/{len(negativas)}")
            menu = []  # o print do menu desta gravação (só o último, se o menu foi aberto mais de uma vez)

            def fotografar_o_menu(g=g, menu=menu):
                for base in menu:
                    captura.descartar(pasta, base)
                exibido = {"titulo": g.titulo, "interprete": g.interprete, "creditos_lidos": g.creditos, "etapa": "menu",
                           "constatacao": 'menu da faixa aberto, com o item "Consulte créditos musicais" destacado'}
                menu[:] = [captura.capturar(navegador.pagina, pasta, g.obra, g.interprete, coleta.plataforma.lower(), exibido, "menu")["captura"]]

            try:
                navegador.ir(endereco(g.link) if endereco else g.link, pausa=(4, 7))
                navegador.pagina.wait_for_timeout(3500)
                navegador.conferir(navegador.pagina.evaluate("document.body.innerText.slice(0, 1500)"))
                if not preparar:
                    na_tela = None
                else:
                    na_tela = preparar(navegador, antes_do_clique=fotografar_o_menu) if com_o_menu else preparar(navegador)
                if na_tela is not None and {normalizar(n) for n in na_tela} != {normalizar(n) for n in g.creditos} and reclassificar:
                    antes = ", ".join(g.creditos) or "nenhum autor"
                    g.creditos = list(na_tela)
                    g.classificacao = reclassificar(g)
                    g.classificacao.fundamento += (
                        f"; os dados da página do álbum traziam {antes}, mas a tela de créditos da música exibe "
                        f"{', '.join(na_tela) or 'nenhum autor'}: vale o que a tela mostra"
                    )
                    if g.classificacao.status not in c.NEGATIVOS:
                        for base in menu:
                            captura.descartar(pasta, base)
                        continue
                if com_o_menu and not menu:
                    raise RuntimeError("o menu da faixa não foi fotografado antes de abrir os créditos")
                exibido = {"titulo": g.titulo, "interprete": g.interprete, "creditos_lidos": g.creditos,
                           "creditos_na_tela": na_tela, "constatacao": g.classificacao.fundamento}
                if com_o_menu:
                    exibido["etapa"] = "creditos"
                creditos = captura.capturar(navegador.pagina, pasta, g.obra, g.interprete, coleta.plataforma.lower(), exibido, "creditos")["captura"]
                g.provas += menu + [creditos]
            except param:
                for base in menu:  # print do menu sem o da tela de créditos não serve de prova
                    captura.descartar(pasta, base)
                raise
            except Exception as e:
                for base in menu:
                    captura.descartar(pasta, base)
                coleta.avisos.append(f'print de "{g.titulo}" ({g.interprete}) não pôde ser tirado: {type(e).__name__}: {str(e).splitlines()[0][:100]}')
    except param as e:
        _parar(coleta, e)


# O elemento é o que está por cima no ponto central dele? (um painel que deslizou para fora continua na página)
_POR_CIMA = """(e) => { const r = e.getBoundingClientRect(); if (!r.width || !r.height) return false;
  const t = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return !!t && (t === e || e.contains(t) || t.contains(e)); }"""


def abrir_creditos_da_deezer(navegador, antes_do_clique=None):
    """Na página da faixa: recusa os cookies, abre o menu e o painel "Consulte créditos musicais".

    Só devolve com o painel à vista. O menu abre com animação: clicar no item cedo demais não abre o painel e ainda
    rola a página, e o print sairia sem os créditos. Por isso espera o item aparecer, confere o painel e tenta de novo.

    `antes_do_clique()`: chamado com o menu aberto e o item de créditos destacado (o mouse em cima), um clique antes
    do painel: é o primeiro print do escritório. Se o painel não abrir, o menu é aberto e fotografado de novo.
    """
    import re
    pagina = navegador.pagina
    navegador.clicar_se_houver('[data-testid="gdpr-btn-refuse-all"]')
    item = pagina.get_by_text(re.compile(r"cr[eé]ditos musicais", re.I)).first
    voltar = pagina.get_by_text(re.compile(r"^\s*voltar\s*$", re.I))
    for _ in range(3):
        pagina.evaluate("() => window.scrollTo(0, 0)")
        pagina.wait_for_timeout(500)
        pagina.get_by_role("button", name="Exibir menu").first.click()
        item.wait_for(state="visible", timeout=8000)
        pagina.wait_for_timeout(900)  # a animação do menu
        if antes_do_clique:
            item.hover()  # o item fica destacado, como no print feito à mão
            pagina.wait_for_timeout(400)
            if not item.evaluate(_POR_CIMA):  # menu ainda se mexendo ou coberto: fecha e tenta de novo
                pagina.keyboard.press("Escape")
                pagina.wait_for_timeout(700)
                continue
            antes_do_clique()
        item.click()
        pagina.wait_for_timeout(1800)
        # painel aberto: o item do menu saiu de cena e o cabeçalho "Voltar" do painel é o que está por cima
        # (o menu e o painel deslizam um sobre o outro: os dois continuam "visíveis" para o navegador)
        if not item.evaluate(_POR_CIMA) and any(voltar.nth(n).evaluate(_POR_CIMA) for n in range(voltar.count())):
            navegador.conferir()
            return
        pagina.keyboard.press("Escape")
        pagina.wait_for_timeout(700)
    raise RuntimeError("o painel de créditos da Deezer não abriu")


def abrir_creditos_da_deezer_logada(navegador, antes_do_clique=None):
    """Como `abrir_creditos_da_deezer`, mas antes confere que o navegador está logado na conta da Deezer."""
    from .deezer import conferir_login

    conferir_login(navegador)
    return abrir_creditos_da_deezer(navegador, antes_do_clique)


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


PENDENCIAS_POR_OBRA = 15  # acima disso o título é comum demais para a lista de vídeos desconhecidos ajudar
# Vídeo que não é a gravação do intérprete, e sim outra pessoa cantando, tocando ou legendando: fica para o fim.
_NUMEROS = {"1": "um", "2": "dois", "3": "tres", "4": "quatro", "5": "cinco", "6": "seis", "7": "sete", "8": "oito", "9": "nove", "10": "dez"}
_DERIVADO = ("cover", "karaoke", "playback", "tipografia", "cifra", "coreografia", "como tocar", "aula", "tutorial", "status")


def _e_karaoke(titulo_do_video: str, obra: str) -> bool:
    """Vídeo de karaokê: não é a gravação do intérprete e fica fora da coleta. Se a própria obra tem "karaokê" no
    título, a palavra não serve de sinal."""
    return "karaoke" in _palavras(titulo_do_video) and "karaoke" not in _palavras(obra)


def _visualizacoes(detalhe: str) -> float:
    """"1,1 mi de visualizações" -> 1100000; "58 mil visualizações" -> 58000. Sem a informação, zero."""
    import re
    achado = re.search(r"(\d[\d.,]*)\s*(mil|mi|bi)?\s*(?:de\s+)?visualiza", str(detalhe or "").lower())
    if not achado:
        return 0.0
    try:
        numero = float(achado[1].replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0
    return numero * {"mil": 1e3, "mi": 1e6, "bi": 1e9}.get(achado[2] or "", 1)


def _escolher_videos(candidatos: list, por_interprete: int, por_obra: int) -> list:
    """De todos os vídeos de uma obra, quais abrir: alguns de CADA intérprete, e os melhores de cada um.

    `candidatos`: [(candidato, título, intérprete que o liga ao titular, consulta)]. Sem isto, um intérprete com
    dezenas de vídeos tomaria todo o limite da obra e os outros ficariam sem nenhum. Dentro de cada intérprete
    vêm primeiro a faixa de álbum e os vídeos do canal dele, depois os de outros canais, dos mais vistos para os
    menos vistos; cover, karaokê e parecidos ficam por último. A escolha é por rodadas (o primeiro de cada
    intérprete, depois o segundo...), para o limite da obra nunca deixar um intérprete de fora.
    """
    grupos = {}
    for item in candidatos:
        cand, _, elo, _ = item
        do_canal = any(c.mesmo_artista(parte, elo) for parte in c.partes_do_interprete(cand.interprete)) or _nome_dentro(elo, cand.interprete)
        derivado = any(palavra in normalizar(cand.titulo) for palavra in _DERIVADO)
        grupos.setdefault(tuple(_palavras(elo)), []).append(((derivado, not do_canal, cand.tipo != "Música", -_visualizacoes(cand.detalhe)), item))
    filas = [[item for _, item in sorted(grupo, key=lambda par: par[0])][:por_interprete] for grupo in grupos.values()]
    escolhidos = []
    for rodada in range(por_interprete):
        escolhidos += [fila[rodada] for fila in filas if rodada < len(fila)]
    return escolhidos[:por_obra]


# Palavras que acompanham o nome de um artista sem fazer parte dele.
_GENERICAS = {"banda", "grupo", "trio", "dupla", "forro", "mc", "dj", "oficial", "cantor", "cantora"}
# Abreviações que valem pelo nome por extenso.
_POR_EXTENSO = {"cia": "companhia", "cia.": "companhia"}


def _distam_uma_letra(a: str, b: str) -> bool:
    """As duas palavras são iguais a menos de UMA letra trocada, a mais ou a menos?"""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    menor, maior = sorted((a, b), key=len)
    return any(maior[:i] + maior[i + 1:] == menor for i in range(len(maior)))


def _quase_o_mesmo_nome(a: str, b: str) -> bool:
    """O mesmo nome de artista a menos de UMA letra em UMA palavra de 5 letras ou mais ("Fulanera" e "Fulaneira"),
    com abreviação por extenso ("Cia" e "Companhia") e sem as letras dobradas. Só serve para o intérprete que o
    compositor conferiu para aquela obra, e sempre junto do título exato: sozinho, confundiria artistas diferentes."""
    import re
    enxuto = lambda nome: [re.sub(r"(.)\1+", r"\1", _POR_EXTENSO.get(p, p)) for p in _palavras(nome)]
    x, y = enxuto(a), enxuto(b)
    if not x or len(x) != len(y):
        return False
    diferentes = [(p, q) for p, q in zip(x, y) if p != q]
    return not diferentes or (len(diferentes) == 1 and min(map(len, diferentes[0])) >= 5 and _distam_uma_letra(*diferentes[0]))


def _e_o_conferido(nome: str, exibido: str) -> bool:
    """O artista exibido é o intérprete conferido `nome`, com a tolerância de `_quase_o_mesmo_nome`? Compara o nome
    inteiro e cada parte dele ("Fulano feat Beltrano") com o exibido e cada parte dele."""
    de_um_lado = [nome, *c.partes_do_interprete(nome)]
    do_outro = [exibido, *c.partes_do_interprete(exibido)]
    # nunca por um nome de uma palavra só: "Fulana" não é a dupla "Fulana & Beltrano"
    return any(_quase_o_mesmo_nome(a, b) for a in de_um_lado for b in do_outro if len(_palavras(a)) >= 2 and len(_palavras(b)) >= 2)


def _palavras(texto) -> list[str]:
    """As palavras do texto, sem acento nem pontuação, com "&" lido como "e"."""
    return normalizar(str(texto or "").replace("&", " e ")).split()


def _mesma_grafia(a: str, b: str) -> bool:
    """O mesmo nome, a menos de letra dobrada ("Beltrano" e "Beltranno") e de "e" por "&"."""
    import re
    sem_dobras = lambda nome: [re.sub(r"(.)\1+", r"\1", palavra) for palavra in _palavras(nome)]
    return bool(_palavras(a)) and sem_dobras(a) == sem_dobras(b)


def _palavra_do_nome(nome: str, exibido: str) -> bool:
    """Nome de uma palavra só (de 5 letras ou mais) que é uma das palavras do nome exibido: "Fulanos" e "Forró Fulanos".
    Só serve para o intérprete que o compositor conferiu para aquela obra, e sempre junto do título exato."""
    palavras = _palavras(nome)
    return len(palavras) == 1 and len(palavras[0]) >= 5 and palavras[0] in _palavras(exibido)


def _nome_dentro(nome: str, texto: str) -> bool:
    """O nome (de duas palavras ou mais) aparece inteiro no texto, como palavras seguidas?

    Serve para duplas e bandas, que a divisão por "e" e "&" parte ao meio: "Fulano e Beltrano - Música".
    Nome de uma palavra só nunca casa por aqui: seria fácil confundir com parte de outro nome.
    """
    alvo, onde = _palavras(nome), _palavras(texto)
    return len(alvo) >= 2 and any(onde[i:i + len(alvo)] == alvo for i in range(len(onde) - len(alvo) + 1))


def _titulo_do_video(titulo: str, relatorio: Relatorio, nomes=()) -> str:
    """Vídeo costuma vir como "Intérprete - Título", às vezes com o nome grudado ou com "[Álbum Tal]" no fim.

    Devolve o trecho que é título de obra do relatório. Se nenhum trecho for igual a um título, devolve o que
    mais se aproxima: o título inteiro quando é um medley, ou o trecho parecido. `nomes` são os intérpretes e
    nomes do titular já conhecidos, que podem ser tirados do começo ou do fim do título do vídeo.
    """
    import re

    def sem_nomes(texto):
        palavras = _palavras(texto)
        for nome in sorted(nomes, key=len, reverse=True):
            for alvo in (["grupo", *_palavras(nome)], _palavras(nome)):
                if len(alvo) >= 2 and palavras[:len(alvo)] == alvo and len(palavras) > len(alvo):
                    return " ".join(palavras[len(alvo):])
                if len(alvo) >= 2 and palavras[-len(alvo):] == alvo and len(palavras) > len(alvo):
                    return " ".join(palavras[:-len(alvo)])
        return ""

    limpo = re.sub(r"\s*\[[^\]]*\]\s*", " ", str(titulo or "")).strip()
    candidatos = [titulo, limpo, *re.split(r"\s*[-–—|:]\s+|\s+[-–—|]\s*", limpo)]
    # "Banda Tal / Música": a barra separa o artista do título, e não duas músicas. Vale quando um dos dois trechos é
    # só o nome de um artista conhecido (com ou sem "banda", "grupo"...) e o outro é um título do relatório.
    trechos = [t.strip() for t in limpo.split("/") if t.strip()]
    if len(trechos) == 2:
        nomes_soltos = [_palavras(n) for n in nomes if _palavras(n)]
        e_artista = lambda t: [p for p in _palavras(t) if p not in _GENERICAS] in nomes_soltos or _palavras(t) in nomes_soltos
        for artista, resto in (trechos, trechos[::-1]):
            if e_artista(artista) and c.casar_titulo(resto, relatorio)[0] == "exato":
                return resto
    # "Música ( Clipe Oficial ) Part. Fulano" -> "Música": sem os parênteses e sem a participação no fim.
    candidatos += [re.sub(r"(?i)\s+(?:part|participa[cç][aã]o|feat|ft)\b\.?.*$", "", re.sub(r"\s*\([^)]*\)\s*", " ", x)).strip()
                   for x in list(candidatos)]
    # "Música 2016/2017" -> "Música": o ano solto no fim não faz parte do título.
    candidatos += [re.sub(r"\s+(?:19|20)\d\d(?:\s*/\s*(?:19|20)?\d\d)?\s*$", "", x).strip() for x in list(candidatos)]
    candidatos += [sem_nomes(x) for x in list(candidatos)]
    # "Depois das 3" -> "Depois das tres": o cadastro costuma trazer o número por extenso.
    candidatos += [" ".join(_NUMEROS.get(p, p) for p in _palavras(x)) for x in list(candidatos) if any(p in _NUMEROS for p in _palavras(x))]
    # O título da obra inteiro dentro do título do vídeo, cercado de sobras ("FULANO - TAL MÚSICA [NOVA] setembro 2016").
    # Vale para título de três palavras ou mais, ou de duas quando o vídeo também cita um nome conhecido; e só se
    # for uma obra só, para não desmanchar um medley.
    palavras_do_video = _palavras(limpo)
    cita_nome = any(set(_palavras(n)) <= set(palavras_do_video) for n in nomes if _palavras(n))
    dentro = {o.titulo for o in relatorio.obras
              if (len(_palavras(o.titulo)) >= 3 or (len(_palavras(o.titulo)) == 2 and cita_nome))
              and any(palavras_do_video[i:i + len(_palavras(o.titulo))] == _palavras(o.titulo) for i in range(len(palavras_do_video)))}
    # O mesmo, com o título escrito junto no cadastro e separado no vídeo (ou o contrário): "TALMUSICA" e "Tal Música".
    # Só com um nome conhecido no vídeo e título de seis letras ou mais.
    if cita_nome:
        for o in relatorio.obras:
            junto = "".join(_palavras(o.titulo))
            if len(junto) >= 6 and any("".join(palavras_do_video[i:j]) == junto for i in range(len(palavras_do_video))
                                       for j in range(i + 1, min(i + 6, len(palavras_do_video)) + 1)):
                dentro.add(o.titulo)
    if len(dentro) == 1 and c.casar_titulo(titulo, relatorio)[0] != "medley":
        candidatos.append(dentro.pop())
    # Um trecho que é só o nome de um artista conhecido não é título: sem isto, a banda "Fulana" casaria com a obra "Fulano".
    artistas = [alvo for nome in nomes for alvo in (_palavras(nome), ["grupo", *_palavras(nome)], ["banda", *_palavras(nome)])]
    candidatos = [x for x in dict.fromkeys(candidatos) if x and x.strip() and (x == titulo or _palavras(x) not in artistas)]
    tipos = {x: c.casar_titulo(x, relatorio)[0] for x in candidatos}
    if _palavras(titulo) in artistas or (candidatos[1:] and c.casar_titulo(titulo, relatorio)[0] != "exato"):
        tipos[titulo] = "" if tipos[titulo] == "aproximado" else tipos[titulo]  # o título inteiro só vale se for exato ou medley
    for quero in ("exato", "medley", "aproximado"):
        achado = next((x for x in candidatos if tipos[x] == quero), None)
        if achado:
            return achado
    return titulo


def _e_da_obra(titulo_exibido: str, titulo_da_obra: str, relatorio: Relatorio) -> str:
    """Como o título exibido se liga a esta obra: "exato", "medley", "aproximado" ou ""."""
    tipo, obras = c.casar_titulo(titulo_exibido, relatorio)
    if tipo == "exato":  # junto ou separado ("Talcoisa" e "Tal Coisa"), o título é o mesmo
        return tipo if c._titulo_base(titulo_exibido).replace(" ", "") == c._titulo_base(titulo_da_obra).replace(" ", "") else ""
    return tipo if tipo and any(c._titulo_base(o.titulo) == c._titulo_base(titulo_da_obra) for o in obras) else ""


def coletar_youtube(relatorio: Relatorio, config: Config, ytm, recorrentes=(), pares=None, por_obra=80, ao_avancar=None,
                    limite_de_obras=None, so_os_pares=False, por_interprete=6) -> Coleta:
    """YouTube Music: busca "título + intérprete" e, para cada resultado ligado ao titular, lê o menu da faixa.

    `recorrentes` e `pares` vêm de `interpretes_conhecidos` (o que outra plataforma já mostrou). Só são
    abertas as faixas de artistas com ligação conhecida com o titular, ou que citam um deles no título do
    vídeo. Vídeo com o título exato e canal sem relação conhecida não é aberto, mas vai para as pendências:
    pode ser um intérprete que o app não conhece.

    A busca olha as abas "Músicas" e "Vídeos" para cada intérprete e também só pelo título: clipes, shows
    e envios de fãs ficam em "Vídeos", e é neles que o crédito mais falta. Título escrito de outro jeito e
    medley com a obra são lidos quando o intérprete é conhecido; a classificação os deixa "a revisar".

    `so_os_pares`: quando os intérpretes de cada obra já foram informados (em `pares`), busca só "título +
    intérprete informado", sem as buscas de descoberta. É bem mais rápido: serve para ir direto aos prints.

    De cada obra são abertos até `por_interprete` vídeos de cada intérprete (ver _escolher_videos) e, no total,
    até `por_obra`.
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
    karaokes = 0
    try:
        for i, (base, titulo) in enumerate(titulos, start=1):
            avisar(f"YouTube Music: buscando {i}/{len(titulos)}")
            da_obra = [n for n in pares.get(titulo, []) if not any(c.mesmo_artista(n, p) for p in principais)]
            ligados = [*principais, *recorrentes, *config.interpretes, *do_titular, *pares.get(titulo, [])]
            if so_os_pares:
                consultas = [(f"{titulo} {n}", ("Músicas", "Vídeos")) for n in pares.get(titulo, [])]
            else:
                consultas = [(f"{titulo} {n}", ("Músicas", "Vídeos")) for n in [*principais, *da_obra]]
                consultas.append((titulo, ("Músicas", "Vídeos")))
            da_vez, sem_ligacao = [], []
            for consulta, abas in consultas:
                try:
                    achados = ytm.buscar(consulta, abas)
                except DadoSensivel as e:  # a consulta levaria dado sensível: não sai, e fica registrado
                    coleta.avisos.append(f'busca "{consulta}" não foi feita: o filtro de saída barrou ({e})')
                    continue
                for cand in achados:
                    if cand.video in vistos:
                        continue
                    if _e_karaoke(cand.titulo, titulo):  # decisão do dono, 15/10/2026: karaokê não entra na coleta
                        vistos.add(cand.video)
                        karaokes += 1
                        continue
                    ligacao = _e_da_obra(_titulo_do_video(cand.titulo, relatorio, ligados), titulo, relatorio)
                    if not ligacao:
                        continue
                    da_planilha = pares.get(titulo, [])
                    def elo_com(artista):
                        return next((n for n in ligados if any(c.mesmo_artista(parte, n) for parte in c.partes_do_interprete(artista))
                                     or _nome_dentro(n, artista) or (n in da_planilha and (_palavra_do_nome(n, artista) or _e_o_conferido(n, artista)))), "")
                    elo = elo_com(cand.interprete) or next((n for n in ligados if _citado(n, cand.titulo) or _nome_dentro(n, cand.titulo)), "")
                    if not elo and ligacao == "exato" and da_planilha and hasattr(ytm, "canal_do_video"):
                        # O YouTube Music às vezes lista os músicos no lugar do nome da banda. O canal que publicou o
                        # vídeo desfaz a dúvida: se for o intérprete conferido, o vídeo é dele.
                        canal = ytm.canal_do_video(cand.video)
                        elo = elo_com(canal) if canal else ""
                    vistos.add(cand.video)
                    if not elo:
                        coleta.sem_vinculo_nao_abertos += 1
                        # Canal desconhecido: não dá para afirmar nada, mas alguém precisa olhar.
                        motivo = {"exato": "com o título da obra", "medley": "de medley que cita a obra",
                                  "aproximado": "com título parecido com o da obra"}[ligacao]
                        sem_ligacao.append(Gravacao(
                            plataforma="YOUTUBE", link=LINK.format(cand.video), titulo=cand.titulo, interprete=cand.interprete,
                            album=cand.detalhe, creditos=["(vídeo não aberto)"], origem=f'busca "{consulta}" ({cand.tipo})',
                            obra=titulo, classificacao=c.Classificacao(
                                c.HOMONIMA, f'vídeo {motivo}, publicado por "{cand.interprete}", que o app não conseguiu ligar ao '
                                "titular; não foi aberto. Se esse artista grava o titular, informe o nome e colete de novo",
                                revisar=True),
                        ))
                    else:
                        da_vez.append((cand, titulo, elo, consulta))
            fila += _escolher_videos(da_vez, por_interprete, por_obra)
            if len(sem_ligacao) <= PENDENCIAS_POR_OBRA:
                coleta.gravacoes += sem_ligacao
            else:  # título comum: listar dezenas de homônimos só esconderia os casos que importam
                coleta.avisos.append(
                    f'"{titulo}" é um título comum no YouTube Music: {len(sem_ligacao)} vídeos de canais sem ligação conhecida '
                    "com o titular não foram listados nas pendências. Se algum intérprete estiver faltando, informe o nome dele"
                )
        for i, (cand, titulo, elo, consulta) in enumerate(fila, start=1):
            avisar(f"YouTube Music: lendo faixa {i}/{len(fila)}")
            leitura = ytm.ler(cand.video, titulo, cand)
            mostrado = _titulo_do_video(leitura.titulo or cand.titulo, relatorio, [elo, *do_titular, *recorrentes])
            do_canal = any(c.mesmo_artista(parte, elo) for parte in c.partes_do_interprete(leitura.interprete or cand.interprete)) \
                or _nome_dentro(elo, leitura.interprete or cand.interprete)
            item = Item(
                titulo=mostrado, interprete=elo if not do_canal else (leitura.interprete or cand.interprete),
                creditos=leitura.creditos, coleta=leitura.coleta, erro=leitura.erro, vinculo=_par_conhecido(pares, titulo, elo),
                plataforma="YOUTUBE", link=LINK.format(cand.video), fonte=leitura.fornecedor, album=leitura.detalhe,
            )
            if leitura.coleta == "ok" and not _e_da_obra(mostrado, titulo, relatorio):
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
    if karaokes:
        coleta.avisos.append(f"{karaokes} vídeos de karaokê achados na busca do YouTube Music não foram abertos nem listados.")
    coleta.albuns_lidos = len(coleta.gravacoes)
    coleta.requisicoes, coleta.segundos = ytm.navegacoes, time.monotonic() - inicio
    coleta.bloqueadas = ytm.filtro.bloqueadas if ytm.filtro else 0
    return coleta


# Tempo da primeira coleta, medido nos dois primeiros casos (um de 47 e um de 119 títulos, 10 a 20 obras cada).
# Segundos por obra em cada plataforma, mais um tempo fixo. É uma ordem de grandeza, não uma promessa: depende
# de quantas gravações cada obra tem e de quantos intérpretes aparecem.
SEGUNDOS_POR_OBRA = {"deezer": 10, "apple": 16, "youtube": 35, "spotify": 20, "tidal": 30, "vagalume": 0, "amazon": 10, "amazon_app": 35, "claro": 25, "palco": 25}
SEGUNDOS_FIXOS = {"deezer": 30, "apple": 10, "youtube": 20, "spotify": 20, "tidal": 20, "vagalume": 30, "amazon": 20, "amazon_app": 60, "claro": 20, "palco": 20}


def com_as_etapas_da_amazon(plataformas) -> set:
    """A Amazon Music é uma escolha só, mas roda em duas etapas: o aplicativo de desktop e, depois, o site."""
    pedidas = set(plataformas)
    return pedidas | {"amazon_app"} if "amazon" in pedidas else pedidas


def estimar_minutos(obras: int, plataformas) -> int:
    """Minutos estimados para a primeira coleta de `obras` títulos nas plataformas pedidas."""
    pedidas = com_as_etapas_da_amazon(plataformas)
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


TODAS = ("deezer", "apple", "youtube", "spotify", "tidal", "vagalume", "amazon", "palco")
ORDEM = ["YOUTUBE", "SPOTIFY", "TIDAL", "DEEZER", "CLARO", "PALCO MP3", "VAGALUME", "AMAZON", "AMAZON - SITE", "APPLE MUSIC"]  # a das colunas da planilha


def executar(relatorio: Relatorio, config: Config, pasta, nomes_dos_coautores=(), youtube=False, limite_youtube=None,
             por_obra=80, ao_avancar=None, so_o_que_ja_foi_lido=False, plataformas=None, prints=True,
             mostrar_navegador=True, tela_inteira=False, conferidos=None) -> list[Coleta]:
    """O fluxo inteiro, do relatório às coletas de cada plataforma, com as capturas de tela.

    `pasta` é a pasta do caso: cada plataforma guarda ali o que já leu e os seus prints (pasta/<plataforma>).
    `plataformas`: nomes de TODAS; sem isso, vale Deezer e, com `youtube`, o YouTube Music. `limite_youtube` é o
    número de obras buscadas em cada plataforma (a discografia da Deezer é sempre percorrida inteira).
    A Deezer vem primeiro porque ensina às outras quem grava o titular. As coletas saem na ordem da planilha.
    `tela_inteira`: cada print ganha também a foto do monitor inteiro; a janela do navegador fica visível.
    `conferidos`: {título: [intérpretes]} da planilha que o compositor conferiu. Com ela, o app não descobre nem
    presume intérprete nenhum: verifica só esses pares, e as obras sem intérprete na planilha ficam de fora.
    """
    from . import captura, navegador

    captura.TELA_INTEIRA, antes = bool(tela_inteira), captura.TELA_INTEIRA
    # Título de obra ou nome de intérprete igual a um aviso de bloqueio (existe música com esse nome) não é aviso.
    navegador.NAO_E_AVISO, avisos_antes = navegador.do_caso(
        [o.titulo for o in relatorio.obras] + [n for nomes in (conferidos or {}).values() for n in nomes]), navegador.NAO_E_AVISO
    try:
        return _executar(relatorio, config, pasta, nomes_dos_coautores, youtube, limite_youtube, por_obra, ao_avancar,
                         so_o_que_ja_foi_lido, plataformas, prints, mostrar_navegador or tela_inteira, conferidos=conferidos)
    finally:
        captura.TELA_INTEIRA, navegador.NAO_E_AVISO = antes, avisos_antes


def _so_os_conferidos(coleta: Coleta, relatorio: Relatorio, config: Config, conferidos: dict) -> Coleta:
    """Deixa na coleta só o que o compositor conferiu: par (obra, intérprete) da planilha vale como ligação provada;
    resultado negativo de intérprete que não está na planilha para aquela obra sai da conta e vai para as pendências."""
    for g in coleta.gravacoes:
        k = g.classificacao
        do_par = any(_par_conhecido(conferidos, titulo, g.interprete) for titulo in g.obra.split("; "))
        if do_par and (k.status == c.HOMONIMA or (k.status in c.NEGATIVOS and k.revisar)):
            novo = classificar(Item(titulo=g.titulo, interprete=g.interprete, creditos=g.creditos, vinculo=True, conferido=True,
                                    plataforma=g.plataforma, link=g.link), relatorio, config)
            if novo.status != c.FORA_DO_REPERTORIO:
                g.classificacao, g.vinculo = novo, "intérprete conferido pelo compositor"
        elif not do_par and k.status in c.NEGATIVOS:
            g.classificacao = Classificacao(
                c.HOMONIMA, f'o intérprete "{g.interprete}" não está na planilha conferida para esta obra'
                + ("; o crédito exibido traz autor do relatório: vale perguntar ao compositor se ele gravou"
                   if k.autores_reconhecidos else "; pode ser obra homônima de terceiro"), revisar=True)
    coleta.interpretes_inferidos = []  # nada de intérprete presumido: quem não está na planilha não entra
    return coleta


def _executar(relatorio, config, pasta, nomes_dos_coautores, youtube, limite_youtube, por_obra, ao_avancar,
              so_o_que_ja_foi_lido, plataformas, prints, mostrar_navegador, conferidos=None) -> list[Coleta]:
    from .navegador import Navegador

    pasta, limite = Path(pasta), limite_youtube
    pedidas = set(plataformas) if plataformas is not None else {"deezer"} | ({"youtube"} if youtube else set())
    sem_interprete = []
    if conferidos is not None:
        import copy
        sem_interprete = [t for t in dict.fromkeys(o.titulo for o in relatorio.obras) if not conferidos.get(t)]
        relatorio = copy.copy(relatorio)
        relatorio.obras = [o for o in relatorio.obras if conferidos.get(o.titulo)]
        nomes_dos_coautores = []  # sem descoberta: os intérpretes já vieram conferidos
    ajustar = (lambda coleta: _so_os_conferidos(coleta, relatorio, config, conferidos)) if conferidos is not None else (lambda coleta: coleta)
    filtro = filtro_do_relatorio(relatorio, config)
    # Sem a planilha conferida, é a Deezer que descobre os intérpretes para as outras plataformas: roda sempre. Com a
    # planilha, os intérpretes já vêm dela, e a Deezer só roda se tiver sido pedida, como qualquer outra.
    coletas = []
    if "deezer" in pedidas or conferidos is None:
        deezer = Deezer(filtro=filtro, cache=pasta / "deezer")
        coletas.append(ajustar(coletar_deezer(relatorio, config, deezer, nomes_dos_coautores, ao_avancar=ao_avancar,
                                              limite_de_obras=limite if plataformas is not None else None)))
    recorrentes, pares = ([], dict(conferidos)) if conferidos is not None else interpretes_conhecidos(coletas)
    if "apple" in pedidas:
        from .apple import AppleMusic
        coletas.append(ajustar(coletar_apple(relatorio, config, AppleMusic(filtro=filtro, cache=pasta / "apple"), recorrentes, pares, limite,
                                             ao_avancar=ao_avancar)))
        if conferidos is None:
            recorrentes, pares = interpretes_conhecidos(coletas)  # a Apple costuma revelar mais intérpretes
    if "vagalume" in pedidas:
        from .vagalume import Vagalume
        coletas.append(ajustar(coletar_vagalume(relatorio, config, Vagalume(filtro=filtro, cache=pasta / "vagalume"), recorrentes, pares,
                                                limite, ao_avancar=ao_avancar)))
    if "youtube" in pedidas:
        from .ytmusic import YouTubeMusic
        with YouTubeMusic(pasta / "youtube", filtro=filtro, ao_avancar=ao_avancar, escondido=not mostrar_navegador,
                          visivel=not so_o_que_ja_foi_lido, so_o_que_ja_foi_lido=so_o_que_ja_foi_lido) as ytm:
            coletas.append(coletar_youtube(relatorio, config, ytm, recorrentes, pares, por_obra, ao_avancar, limite,
                                           so_os_pares=conferidos is not None))
    if "claro" in pedidas:
        # A Claro Música só mostra o catálogo a quem está logado: usa um navegador à parte, com o perfil em que a
        # pessoa fez o login. As outras plataformas seguem no navegador limpo, sem sessão nenhuma.
        from .claro import Claro
        from .navegador import PerfilEmUso
        try:
            with Navegador(filtro=filtro, visivel=not so_o_que_ja_foi_lido, ao_avancar=ao_avancar, escondido=not mostrar_navegador,
                           perfil=perfil_da_claro(pasta)) as nav_da_claro:
                coletas.append(ajustar(coletar_claro(relatorio, config, Claro(nav_da_claro, pasta / "claro", so_o_que_ja_foi_lido),
                                                     recorrentes, pares, limite, ao_avancar=ao_avancar)))
        except PerfilEmUso as e:  # não derruba as outras plataformas: a Claro fica parada, com o motivo
            parada = Coleta("CLARO")
            _parar(parada, e)
            coletas.append(parada)
    for coleta in coletas:  # o que já foi fotografado em outra rodada é reaproveitado
        if coleta.plataforma in ("DEEZER", "APPLE MUSIC", "VAGALUME"):
            reaproveitar_prints(coleta, pasta / coleta.plataforma.lower().replace(" ", "-"),
                                pagina_da_musica_na_apple if coleta.plataforma == "APPLE MUSIC" else None,
                                ("menu", "creditos") if coleta.plataforma == "DEEZER" else None)
    com_tela = pedidas & {"spotify", "tidal", "amazon", "amazon_app", "palco"}

    def falta_print_em(*plataformas):
        return prints and not so_o_que_ja_foi_lido and any(
            g.classificacao.status in c.NEGATIVOS and not g.provas for k in coletas if k.plataforma in plataformas for g in k.gravacoes)

    falta_print, falta_print_da_deezer = falta_print_em("APPLE MUSIC", "VAGALUME"), falta_print_em("DEEZER")
    config_local = Config(config.nomes_confirmados, config.nomes_a_confirmar, config.interpretes, list(recorrentes))

    def reclassificar(g):
        return classificar(Item(titulo=g.titulo, interprete=g.interprete, creditos=g.creditos,
                                vinculo=bool(g.vinculo) and not g.classificacao.presumido,
                                plataforma=g.plataforma, link=g.link), relatorio, config_local)

    if com_tela or falta_print:
        with Navegador(filtro=filtro, visivel=not so_o_que_ja_foi_lido, ao_avancar=ao_avancar, escondido=not mostrar_navegador) as nav:
            if "spotify" in pedidas:
                from .spotify import Spotify
                coletas.append(ajustar(coletar_spotify(relatorio, config, Spotify(nav, pasta / "spotify", so_o_que_ja_foi_lido),
                                                       recorrentes, pares, limite, ao_avancar=ao_avancar)))
            if "tidal" in pedidas:
                from .tidal import Tidal
                from .tidal_api import TidalAPI, chaves as chaves_do_tidal

                # Com as chaves da API (pasta de dados), as faixas são achadas pelo ISRC: o das gravações que a Deezer
                # leu nesta rodada e, para as obras que faltarem, o da busca da Deezer.
                chave = chaves_do_tidal(Path(pasta).parent.parent)
                api = TidalAPI(chave, filtro=filtro, cache=pasta / "tidal" / "leituras") if chave and not so_o_que_ja_foi_lido else None
                conhecidas = [(g.obra, g.interprete, g.isrc) for k in coletas if k.plataforma == "DEEZER" for g in k.gravacoes
                              if g.isrc and g.classificacao.status != c.HOMONIMA]
                coletas.append(ajustar(coletar_tidal(relatorio, config, Tidal(nav, pasta / "tidal", so_o_que_ja_foi_lido),
                                                     recorrentes, pares, limite, ao_avancar=ao_avancar,
                                                     so_os_pares=conferidos is not None, api=api,
                                                     deezer=Deezer(filtro=filtro, cache=pasta / "deezer") if api else None,
                                                     conhecidas=conhecidas)))
            if "palco" in pedidas:
                from .palco import Palco
                coletas.append(ajustar(coletar_palco(relatorio, config, Palco(nav, pasta / "palco-mp3", so_o_que_ja_foi_lido),
                                                     recorrentes, pares, limite, ao_avancar=ao_avancar)))
            if pedidas & {"amazon", "amazon_app"}:
                # Uma Amazon só: primeiro o aplicativo de desktop, depois o site para as que vieram sem o titular.
                from .amazon import AmazonWeb
                from .amazon_app import AmazonApp
                with AmazonApp(pasta / "amazon-app", ao_avancar, so_o_que_ja_foi_lido) as aplicativo:
                    coletas += [ajustar(k) for k in coletar_amazon_completa(
                        relatorio, config, AmazonWeb(nav, pasta / "amazon-site", so_o_que_ja_foi_lido), aplicativo,
                        recorrentes, pares, limite, ao_avancar=ao_avancar)]
            if falta_print:
                preparos = {"VAGALUME": abrir_autoria_do_vagalume, "APPLE MUSIC": abrir_creditos_da_apple}
                for coleta in coletas:
                    if coleta.plataforma in preparos:
                        capturar_paginas(coleta, nav, pasta / coleta.plataforma.lower().replace(" ", "-"), preparos[coleta.plataforma],
                                         ao_avancar, pagina_da_musica_na_apple if coleta.plataforma == "APPLE MUSIC" else None,
                                         reclassificar)
    if falta_print_da_deezer:
        # O print da Deezer é tirado logado na conta: um navegador à parte, com o perfil em que a pessoa fez o login
        # (como na Claro Música). Sem a sessão, só os prints da Deezer param, com o motivo.
        with Navegador(filtro=filtro, visivel=not so_o_que_ja_foi_lido, ao_avancar=ao_avancar, escondido=not mostrar_navegador,
                       perfil=perfil_da_deezer(pasta)) as nav_da_deezer:
            for coleta in coletas:
                if coleta.plataforma == "DEEZER":
                    capturar_paginas(coleta, nav_da_deezer, pasta / "deezer", abrir_creditos_da_deezer_logada, ao_avancar, None, reclassificar,
                                     com_o_menu=True)
    coletas = sorted(coletas, key=lambda k: ORDEM.index(k.plataforma) if k.plataforma in ORDEM else 99)
    if sem_interprete and coletas:
        coletas[0].avisos.append(
            f"{len(sem_interprete)} obras estão sem intérprete na planilha conferida e não foram verificadas: "
            + "; ".join(sem_interprete[:40]) + ("…" if len(sem_interprete) > 40 else ""))
    return coletas


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
