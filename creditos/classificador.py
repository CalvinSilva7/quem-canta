"""Classifica o crédito que a plataforma exibe em uma gravação, contra o relatório do titular.

Segue a metodologia do escritório. As regras que não podem ser quebradas:

- Erro técnico nunca vira "SEM CRÉDITOS": vira "NÃO VERIFICADO".
- O crédito exibido é comparado com o conjunto de autores do relatório (nome
  civil, pseudônimos e coautores de TODOS os registros do título, inclusive as
  duplicidades), nunca com um nome fixo.
- Título só aproximado nunca sustenta sozinho um resultado negativo.
- Banda ou intérprete no campo de autor é violação: autor é pessoa física.
- Nome de pessoa desconhecido sempre pede revisão: pode ser o próprio autor
  com outro nome artístico.

Tudo aqui é local e determinístico: nenhuma função deste módulo acessa a rede.
"""

import re
from dataclasses import dataclass, field

from cantor.matching import dividir_compositores, nomes_parecidos, normalizar, normalizar_titulo

from .modelo import TITULO_PARECIDO, Obra, Relatorio, chave_do_autor, semelhanca

SEM_CREDITOS = "SEM CRÉDITOS"
VIOLACAO = "VIOLAÇÃO - crédito errado"
OK = "OK - creditado corretamente"
LINK_DIVERGENTE = "LINK DIVERGENTE - outra gravação/intérprete"
ERRO_TECNICO = "NÃO VERIFICADO - erro técnico"
INDISPONIVEL = "NÃO VERIFICADO - página indisponível"
OK_VARIANTE = "OK - nome artístico/variante do autor (confirmar)"
GRAFIA = "DIVERGÊNCIA - grafia do nome/pseudônimo"
MEDLEY = "REVISAR - medley com obra(s) do titular"
HOMONIMA = "REVISAR - possível obra homônima de terceiro"
TITULO_APROXIMADO = "REVISAR - título aproximado"
# Não está na tabela da metodologia: a discografia de um intérprete traz faixas que não são do titular.
FORA_DO_REPERTORIO = "FORA DO REPERTÓRIO - título não consta do relatório"
PLATAFORMA_ENCERRADA = "PLATAFORMA ENCERRADA - sem coleta"

NEGATIVOS = {SEM_CREDITOS, VIOLACAO}
GRAFIA_PARECIDA = 0.8
_PARTICULAS = {"de", "da", "do", "das", "dos", "e", "di", "del", "la"}
_VERSAO = re.compile(
    r"^(ao vivo( .*)?|live( .*)?|acustic[oa]|remaster\w*|versao .*|instrumental|playback|"
    r"bonus( track)?|radio edit|single|faixa bonus|.*remix.*)( \d{4})?$"
)
_MARCA_DE_MEDLEY = re.compile(r"\b(pot ?pourri|pout ?pourri|medley|mix de)\b")


@dataclass
class Item:
    """Uma gravação em uma plataforma, como foi coletada."""
    titulo: str = ""
    interprete: str = ""
    creditos: list[str] = field(default_factory=list)  # nomes exibidos no campo de compositor/autor; vazio = sem o campo
    coleta: str = "ok"  # "ok", "erro" (timeout, bloqueio, parse) ou "indisponivel" (página fora do ar, obra removida)
    erro: str = ""
    interprete_esperado: str = ""  # quando o link veio de um mapeamento obra -> intérprete
    vinculo: bool = False  # a gravação já foi ligada à obra do titular por outra via (ISRC, lista oficial, mapeamento)
    conferido: bool = False  # o compositor conferiu que este intérprete gravou a obra (planilha de intérpretes)
    plataforma: str = ""
    link: str = ""
    isrc: str = ""
    fonte: str = ""  # fornecedor do metadado (Fontes / fornecidos por / PRODUCER / LABEL_NAME)
    album: str = ""


@dataclass
class Config:
    """O que o usuário confirmou sobre o titular."""
    nomes_confirmados: list[str] = field(default_factory=list)  # nomes artísticos confirmados do titular
    nomes_a_confirmar: list[str] = field(default_factory=list)  # variantes vistas nas plataformas, ainda sem confirmação
    interpretes: list[str] = field(default_factory=list)  # bandas e intérpretes que sabidamente gravam o titular
    # Intérpretes que o app PRESUME que gravam o titular (gravam outras obras dele). Presunção nunca sustenta
    # resultado firme: um artista popular grava obras de muitos autores, inclusive homônimas.
    presumidos: list[str] = field(default_factory=list)


@dataclass
class Classificacao:
    status: str
    fundamento: str
    revisar: bool = False
    titulo: str = ""  # "exato", "aproximado", "medley" ou ""
    obras: list[str] = field(default_factory=list)  # códigos dos registros considerados
    coautores_omitidos: list[str] = field(default_factory=list)
    sugestao: str = ""  # o status que valeria se o título fosse exato (só em TÍTULO APROXIMADO)
    autores_reconhecidos: int = 0  # quantos autores do relatório aparecem no crédito exibido
    vinculo: str = ""  # o que liga a gravação ao titular (vazio = nada além do título)
    presumido: bool = False  # a única ligação é presumida: fora de qualquer contagem até alguém confirmar


def partes_do_interprete(texto) -> list[str]:
    """"Fulano, Beltrano & Sicrano feat. Outro" -> cada artista do crédito."""
    partes = re.split(r"\s*(?:,|&|/|\bfeat\.?|\bpart\.?|\bft\.?|\be\b|\bx\b)\s*", str(texto or ""), flags=re.IGNORECASE)
    return [p.strip() for p in partes if p.strip()] or [str(texto or "").strip()]


def mesmo_artista(a, b) -> bool:
    """O mesmo artista, com tolerância só para nome composto: "Banda Tal" ~ "Grupo Banda Tal".

    Um nome de uma palavra só nunca casa com um nome maior: "Fulano" não é "Beltrano Fulano".
    """
    if nomes_parecidos(a, b, parcial=False):
        return True
    ta, tb = set(normalizar(a).split()), set(normalizar(b).split())
    menor, maior = sorted((ta, tb), key=len)
    return len(menor) >= 2 and menor <= maior


def _termos(nome) -> list[str]:
    return [t for t in normalizar(nome).split() if t not in _PARTICULAS]


def _titulo_base(titulo) -> str:
    """Título sem parênteses e sem o sufixo de versão ("Tal Música - Ao Vivo")."""
    partes = re.split(r"\s+[-–—]\s+", str(titulo or ""))
    while len(partes) > 1 and _VERSAO.match(normalizar(partes[-1])):
        partes.pop()
    return normalizar_titulo(" - ".join(partes))


def _partes_de_medley(titulo) -> list[str]:
    texto = str(titulo or "")
    separadores = r"[/+;|]"
    if _MARCA_DE_MEDLEY.search(normalizar(texto)):
        texto = re.sub(r"(?i)pot[- ]?pourri|pout[- ]?pourri|medley|mix de", " ", texto)
        separadores = r"[/+;|,:]|\s[-–—]\s"
    partes = [_titulo_base(p) for p in re.split(separadores, texto)]
    return [p for p in partes if p]


def casar_titulo(titulo, relatorio: Relatorio) -> tuple[str, list[Obra]]:
    """("exato" | "medley" | "aproximado" | "", registros do relatório com esse título)."""
    alvo = _titulo_base(titulo)
    if not alvo:
        return "", []
    por_titulo = {}
    for obra in relatorio.obras:
        por_titulo.setdefault(_titulo_base(obra.titulo), []).append(obra)
    if alvo in por_titulo:
        return "exato", por_titulo[alvo]
    partes = _partes_de_medley(titulo)
    if len(partes) > 1 or (partes and _MARCA_DE_MEDLEY.search(normalizar(titulo))):
        achadas = [o for p in dict.fromkeys(partes) for o in por_titulo.get(p, [])]
        if achadas:
            return "medley", achadas
    parecidas = {t: semelhanca(alvo, t) for t in por_titulo}
    melhor = max(parecidas.values(), default=0)
    if melhor >= TITULO_PARECIDO:
        return "aproximado", [o for t, nota in parecidas.items() if nota == melhor for o in por_titulo[t]]
    return "", []


def comparar_nome(exibido, nome_civil, pseudonimos=(), confirmados=(), a_confirmar=()) -> str:
    """Como o nome exibido se relaciona com um autor: "igual", "abreviado", "variante", "grafia" ou "".

    - igual: o nome civil, um pseudônimo do relatório ou um nome artístico confirmado.
    - abreviado: dois termos ou mais, o primeiro igual ao do nome civil, todos tirados dele.
    - variante: nome ainda não confirmado, ou combinação de termos do nome civil e dos pseudônimos.
    - grafia: quase igual a um dos nomes (similaridade de 0,8 ou mais), com letra trocada.
    """
    alvo = normalizar(exibido)
    if not alvo:
        return ""
    oficiais = [nome_civil, *pseudonimos, *confirmados]
    if alvo in {normalizar(n) for n in oficiais if n}:
        return "igual"
    termos, civil = _termos(exibido), _termos(nome_civil)
    if len(termos) >= 2 and civil and termos[0] == civil[0] and set(termos) <= set(civil):
        return "abreviado"
    if alvo in {normalizar(n) for n in a_confirmar if n}:
        return "variante"
    conhecidos = set(civil) | {t for n in [*pseudonimos, *confirmados] for t in _termos(n)}
    if len(termos) >= 2 and set(termos) <= conhecidos:
        return "variante"
    if any(semelhanca(alvo, n) >= GRAFIA_PARECIDA for n in oficiais if n):
        return "grafia"
    if len(termos) >= 2:
        # Termo a termo: "Fulanno Souza" para "Fulano Souza". Termos curtos só valem se forem idênticos.
        def parecido(termo):
            return termo in conhecidos or (
                len(termo) >= 4 and any(len(c) >= 4 and semelhanca(termo, c) >= GRAFIA_PARECIDA for c in conhecidos)
            )
        if all(parecido(t) for t in termos):
            return "grafia"
    return ""


def classificar(item: Item, relatorio: Relatorio, config: Config | None = None) -> Classificacao:
    config = config or Config()
    if item.coleta == "erro":
        return Classificacao(ERRO_TECNICO, f"a coleta falhou ({item.erro or 'erro não informado'}); reprocessar", revisar=True)
    if item.coleta == "indisponivel":
        return Classificacao(INDISPONIVEL, item.erro or "a página não carregou ou a faixa foi removida", revisar=True)
    if item.coleta != "ok":
        return Classificacao(ERRO_TECNICO, f'estado de coleta desconhecido: "{item.coleta}"', revisar=True)
    if item.interprete_esperado and not nomes_parecidos(item.interprete, item.interprete_esperado):
        return Classificacao(
            LINK_DIVERGENTE,
            f'o link abre uma gravação de "{item.interprete or "(sem intérprete)"}", e a linha esperava '
            f'"{item.interprete_esperado}"; remapear antes de concluir qualquer coisa',
            revisar=True,
        )

    tipo, obras = casar_titulo(item.titulo, relatorio)
    codigos = [o.codigo for o in obras]
    if not tipo:
        return Classificacao(FORA_DO_REPERTORIO, f'"{item.titulo}" não corresponde a nenhum título do relatório')
    if tipo == "medley":
        return Classificacao(
            MEDLEY, "a faixa junta mais de uma música; contém do relatório: " + "; ".join(sorted({o.titulo for o in obras})),
            revisar=True, titulo=tipo, obras=codigos,
        )

    resultado = _comparar_creditos(item, relatorio, config, obras)
    resultado.titulo, resultado.obras = tipo, codigos
    if tipo == "aproximado":
        exibido = "; ".join(sorted({o.titulo for o in obras}))
        if resultado.status in NEGATIVOS:
            # Título aproximado nunca sustenta sozinho um resultado negativo.
            return Classificacao(
                TITULO_APROXIMADO,
                f'o título exibido ("{item.titulo}") só se parece com o do relatório ("{exibido}"); se for a mesma '
                f"obra, o resultado seria {resultado.status}: {resultado.fundamento}",
                revisar=True, titulo=tipo, obras=codigos, coautores_omitidos=resultado.coautores_omitidos,
                sugestao=resultado.status,
            )
        resultado.fundamento += f'; título exibido ("{item.titulo}") aproximado do relatório ("{exibido}")'
    return resultado


def _comparar_creditos(item: Item, relatorio: Relatorio, config: Config, obras: list[Obra]) -> Classificacao:
    pseudonimos = relatorio.pseudonimos()
    autores = {}  # chave -> (Titular, é o dono do relatório?)
    for obra in obras:
        for autor in obra.autores:
            autores.setdefault(chave_do_autor(autor), (autor, relatorio.e_o_titular(autor)))
    if not any(dono for _, dono in autores.values()):
        # O titular não consta como autor de nenhum registro lido: não dá para acusar ninguém com isso.
        return Classificacao(HOMONIMA, "o titular do relatório não está entre os autores dos registros com esse título", revisar=True)

    nomes_do_titular = [relatorio.nome_titular, relatorio.pseudonimo_titular, *config.nomes_confirmados,
                        *pseudonimos.get(relatorio.codigo_titular or normalizar(relatorio.nome_titular), [])]
    ligados = [*config.interpretes, *nomes_do_titular] + [
        n for autor, _ in autores.values() for n in (autor.nome, *pseudonimos.get(chave_do_autor(autor), []))
    ]
    # Ligação PROVADA: o intérprete é o titular ou um autor (nome inteiro, nunca parcial: "Fulano" não é o autor
    # "Fulano de Tal"), é um intérprete informado, ou a gravação já foi ligada à obra por outra via.
    artistas = partes_do_interprete(item.interprete)
    casado = next((n for n in ligados if n and any(nomes_parecidos(a, n, parcial=False) for a in artistas)), "") or next(
        (n for n in config.interpretes if n and any(mesmo_artista(a, n) for a in artistas)), "")
    vinculo = "informado ou já mapeado" if item.vinculo else f'intérprete "{item.interprete}" = "{casado}"' if casado else ""
    # Ligação PRESUMIDA: o intérprete só é alguém que grava outras obras do titular.
    presumido = "" if vinculo else next((n for n in config.presumidos if n and any(mesmo_artista(a, n) for a in artistas)), "")
    varios = len(obras) > 1 and len({tuple(sorted(chave_do_autor(a) for a in o.autores)) for o in obras}) > 1
    sufixo = f" ({len(obras)} registros do título no relatório, com autorias diferentes: considerados todos)" if varios else ""

    exibidos = [n for texto in item.creditos for n in dividir_compositores(texto)]
    if not exibidos:
        if presumido:
            return Classificacao(
                SEM_CREDITOS,
                "a tela de créditos foi lida e não traz compositor nem autor; ATENÇÃO: vínculo com o titular PRESUMIDO, a "
                f'confirmar: "{presumido}" grava outras obras dele, mas nada prova que esta gravação é a obra do relatório '
                "e não uma homônima" + sufixo,
                revisar=True, presumido=True, vinculo=f'presumido: "{presumido}" grava outras obras do titular',
                coautores_omitidos=[a.nome for a, dono in autores.values() if not dono],
            )
        if not vinculo:
            return Classificacao(
                HOMONIMA,
                f'título igual e nenhum crédito, mas o intérprete "{item.interprete}" não tem vínculo conhecido com o '
                "titular: pode ser obra homônima de terceiro" + sufixo, revisar=True,
            )
        return Classificacao(
            SEM_CREDITOS, "a tela de créditos foi lida e não traz compositor nem autor" + sufixo,
            coautores_omitidos=[a.nome for a, dono in autores.values() if not dono], vinculo=vinculo,
        )

    # Cada nome exibido: a que autor do relatório corresponde, e como.
    ordem = {"igual": 0, "abreviado": 1, "variante": 2, "grafia": 3}
    achados, desconhecidos = {}, []  # chave do autor -> melhor relação
    for exibido in exibidos:
        melhor = None
        for chave, (autor, dono) in autores.items():
            relacao = comparar_nome(
                exibido, autor.nome, pseudonimos.get(chave, []),
                config.nomes_confirmados if dono else (), config.nomes_a_confirmar if dono else (),
            )
            if relacao and (melhor is None or ordem[relacao] < ordem[melhor[1]]):
                melhor = (chave, relacao)
        if melhor is None:
            desconhecidos.append(exibido)
        elif melhor[0] not in achados or ordem[melhor[1]] < ordem[achados[melhor[0]]]:
            achados[melhor[0]] = melhor[1]

    # Um nome "parecido" com o do titular pode ser outra pessoa que o próprio relatório conhece (um parente
    # coautor de outras obras, por exemplo): aí não é grafia errada, é crédito dado a outro.
    outros = _outros_autores_do_relatorio(relatorio, set(autores), pseudonimos)
    de_outro = []
    for exibido in list(exibidos):
        dono_do_nome = next((nome for nome, apelidos in outros if comparar_nome(exibido, nome, apelidos) in ("igual", "abreviado")), "")
        if not dono_do_nome:
            continue
        for chave, (autor, _) in list(autores.items()):
            if achados.get(chave) in ("variante", "grafia") and comparar_nome(exibido, autor.nome, pseudonimos.get(chave, [])) == achados[chave]:
                del achados[chave]
                de_outro.append(f"{exibido} (no relatório, autor de outras obras: {dono_do_nome})")
    do_titular = next((achados[c] for c, (_, dono) in autores.items() if dono and c in achados), None)
    resultado = _decidir(item, autores, achados, desconhecidos, do_titular, exibidos, vinculo, sufixo, config, de_outro)
    resultado.autores_reconhecidos = len(achados) + len(de_outro)
    resultado.vinculo = "autor do relatório no crédito exibido" if resultado.autores_reconhecidos else vinculo
    return resultado


def _outros_autores_do_relatorio(relatorio, chaves_desta_obra, pseudonimos) -> list[tuple[str, list[str]]]:
    outros = {}
    for obra in relatorio.obras:
        for autor in obra.autores:
            chave = chave_do_autor(autor)
            if chave not in chaves_desta_obra:
                outros.setdefault(chave, (autor.nome, pseudonimos.get(chave, [])))
    return list(outros.values())


def _decidir(item, autores, achados, desconhecidos, do_titular, exibidos, vinculo, sufixo, config, de_outro=()) -> Classificacao:
    omitidos = [a.nome for c, (a, dono) in autores.items() if not dono and c not in achados]
    exibido = ", ".join(exibidos)
    notas = []
    if omitidos:
        notas.append("coautores do relatório não exibidos: " + ", ".join(omitidos))
    if desconhecidos and do_titular:
        notas.append("nome(s) exibido(s) que não estão no relatório: " + ", ".join(desconhecidos))
    nota = ("; " + "; ".join(notas) if notas else "") + sufixo

    if do_titular in ("igual", "abreviado"):
        como = "pelo nome do relatório" if do_titular == "igual" else "com o nome civil abreviado"
        return Classificacao(
            OK, f'o titular aparece no crédito ("{exibido}"), {como}' + nota,
            revisar=bool(desconhecidos), coautores_omitidos=omitidos,
        )
    if do_titular == "variante":
        return Classificacao(
            OK_VARIANTE, f'o crédito ("{exibido}") usa uma variante do nome do titular que ainda não foi confirmada' + nota,
            revisar=True, coautores_omitidos=omitidos,
        )
    if do_titular == "grafia":
        return Classificacao(
            GRAFIA, f'o crédito ("{exibido}") traz o nome ou pseudônimo do titular com a grafia errada' + nota,
            revisar=True, coautores_omitidos=omitidos,
        )

    # Daqui em diante o titular não está no crédito.
    todos_omitidos = [a.nome for c, (a, _) in autores.items() if c not in achados]
    if de_outro:
        return Classificacao(
            VIOLACAO,
            f'o crédito ("{exibido}") é de outra pessoa, e não do titular: {"; ".join(de_outro)}; autores do relatório '
            f"omitidos: {', '.join(todos_omitidos)}; conferir também se o cadastro da obra está completo" + sufixo,
            revisar=True, coautores_omitidos=todos_omitidos,
        )
    bandas = [n for n in desconhecidos if any(nomes_parecidos(n, b, parcial=False) for b in [item.interprete, *config.interpretes] if b)]
    if bandas and not vinculo and not achados:
        # Intérprete sem vínculo que credita a si mesmo: é o caso típico da música homônima de outro autor.
        return Classificacao(
            HOMONIMA,
            f'título igual, mas o intérprete ("{item.interprete}") não tem vínculo com o titular e o crédito é dele '
            "mesmo: pode ser obra homônima de terceiro" + sufixo, revisar=True,
        )
    if bandas:
        return Classificacao(
            VIOLACAO,
            f'o campo de autor traz o intérprete ou a banda ("{", ".join(bandas)}"), e autor é sempre pessoa física '
            f"(art. 11 da Lei 9.610/98); autores do relatório omitidos: {', '.join(todos_omitidos)}" + sufixo,
            coautores_omitidos=todos_omitidos,
        )
    if achados and not desconhecidos:
        return Classificacao(
            VIOLACAO,
            f'o crédito ("{exibido}") traz só o(s) coautor(es) e omite o titular; autores do relatório omitidos: '
            f"{', '.join(todos_omitidos)}" + sufixo,
            coautores_omitidos=todos_omitidos,
        )
    if not vinculo and not achados:
        return Classificacao(
            HOMONIMA,
            f'título igual, mas nem o intérprete ("{item.interprete}") nem o crédito ("{exibido}") têm vínculo com o '
            "titular: pode ser obra homônima de terceiro" + sufixo, revisar=True,
        )
    if item.conferido:
        # O compositor já disse que este intérprete gravou a obra dele: crédito só com outros nomes é crédito errado.
        return Classificacao(
            VIOLACAO,
            f'o crédito ("{exibido}") não traz o titular, e o compositor conferiu que este intérprete gravou a obra; '
            f"autores do relatório omitidos: {', '.join(todos_omitidos)}" + sufixo,
            coautores_omitidos=todos_omitidos,
        )
    return Classificacao(
        VIOLACAO,
        f'o crédito ("{exibido}") não traz o titular; antes de usar, pesquisar se "{", ".join(desconhecidos)}" não é '
        f"o próprio autor com outro nome artístico; autores do relatório omitidos: {', '.join(todos_omitidos)}" + sufixo,
        revisar=True, coautores_omitidos=todos_omitidos,
    )
