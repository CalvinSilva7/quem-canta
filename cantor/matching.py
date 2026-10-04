"""Normalização de texto e comparação aproximada de títulos e nomes."""

import re
import unicodedata

from rapidfuzz import fuzz

LIMIAR_NOME = 90  # abaixo disso, nomes curtos que diferem em uma letra passam por iguais
LIMIAR_TITULO = 90

_ROTULOS = re.compile(
    r"\b(compositor(es|a|as)?|autor(es|a|as)?|composi[cç][aã]o|letra|m[uú]sica|"
    r"cr[eé]ditos?|composer|written by)\s*:",
    re.IGNORECASE,
)
_SEPARADORES = re.compile(r"\s*[/;,&|\n]\s*|\s+(?:e|and|feat\.?)\s+", re.IGNORECASE)
_ENTRE_PARENTESES = re.compile(r"\([^)]*\)|\[[^\]]*\]")


def normalizar(texto) -> str:
    """Sem acentos, minúsculo, sem pontuação e com espaços simples."""
    if texto is None or texto != texto:  # None ou NaN
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]|_", " ", s.casefold())
    return " ".join(s.split())


def normalizar_titulo(titulo) -> str:
    """Como `normalizar`, mas ignora trechos entre parênteses ("(ao vivo)")."""
    if titulo is None or titulo != titulo:
        return ""
    sem_parenteses = normalizar(_ENTRE_PARENTESES.sub(" ", str(titulo)))
    return sem_parenteses or normalizar(titulo)


def chave_consulta(titulo, compositor) -> tuple[str, str]:
    """Chave usada no cache e nas correções manuais."""
    return normalizar_titulo(titulo), normalizar(compositor)


def titulos_parecidos(a, b, limiar: int = LIMIAR_TITULO) -> bool:
    na, nb = normalizar_titulo(a), normalizar_titulo(b)
    if not na or not nb:
        return False
    return na == nb or fuzz.ratio(na, nb) >= limiar


def nomes_parecidos(a, b, limiar: int = LIMIAR_NOME, parcial: bool = True) -> bool:
    """Compara nomes de pessoas/grupos ignorando ordem das palavras.

    Com `parcial`, aceita um nome contido no outro ("Vinicius" ~ "Vinicius de
    Moraes"). Apelidos ("Tom Jobim" ~ "Antônio Carlos Jobim") não são
    resolvidos aqui: isso depende dos aliases do MusicBrainz (ver busca.py).
    """
    na, nb = normalizar(a), normalizar(b)
    if not na or not nb:
        return False
    if na == nb or fuzz.token_sort_ratio(na, nb) >= limiar:
        return True
    if not parcial:
        return False
    menor, maior = sorted((set(na.split()), set(nb.split())), key=len)
    return menor <= maior and any(len(palavra) >= 3 for palavra in menor)


def dividir_compositores(texto) -> list[str]:
    """Separa "Tom Jobim / Vinicius de Moraes" em nomes individuais."""
    if texto is None or texto != texto:
        return []
    sem_rotulos = _ROTULOS.sub(" ", str(texto))
    nomes, vistos = [], set()
    for parte in _SEPARADORES.split(sem_rotulos):
        chave = normalizar(parte)
        if chave and chave not in vistos:
            vistos.add(chave)
            nomes.append(parte.strip())
    return nomes
