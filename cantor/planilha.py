"""Leitura da planilha, detecção de colunas e geração do arquivo de saída."""

import io
from collections import Counter

import pandas as pd

from .busca import ATRIBUICAO_CREDITS, COLUNAS_SAIDA
from .matching import dividir_compositores, normalizar

# Modo relatório: a planilha é o catálogo de um compositor só.
RELATORIO_FRACAO = 0.9
RELATORIO_MINIMO_DE_LINHAS = 5
RECORRENCIA_MINIMA = 2  # linhas em que um artista precisa aparecer para ser sugerido como nome artístico

# Em ordem de preferência. A comparação é feita com o cabeçalho normalizado.
SINONIMOS = {
    "compositor": ["compositor", "compositores", "autor", "autores", "autoria", "composicao", "composer", "writer"],
    "creditos": ["creditos", "credito", "credits"],
    "titulo": ["titulo", "musica", "nome da musica", "cancao", "faixa", "obra", "nome", "title", "song", "track"],
    "cantor": ["cantor", "cantora", "interprete", "artista", "singer", "artist"],
    "iswc": ["iswc", "codigo da obra", "codigo iswc", "cod obra", "work code"],
    "data_cadastro": ["data de cadastro", "data cadastro", "data do cadastro", "cadastro", "data de registro", "data registro"],
}


def ler_planilha(conteudo: bytes, nome_arquivo: str) -> pd.DataFrame:
    """Lê .xlsx ou .csv como texto puro (sem converter números nem datas)."""
    if nome_arquivo.lower().endswith((".xlsx", ".xlsm")):
        df = pd.read_excel(io.BytesIO(conteudo), dtype=str)
    else:
        df = _ler_csv(conteudo)
    df = df.dropna(how="all").fillna("").reset_index(drop=True)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _ler_csv(conteudo: bytes) -> pd.DataFrame:
    try:
        texto = conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = conteudo.decode("cp1252")  # CSV salvo pelo Excel no Windows
    # sep=None faz o pandas descobrir se o separador é vírgula, ponto e vírgula ou tab.
    return pd.read_csv(io.StringIO(texto), dtype=str, sep=None, engine="python")


def detectar_colunas(df: pd.DataFrame, papeis=("compositor", "creditos", "iswc", "data_cadastro", "titulo")) -> dict:
    """Devolve {papel: nome da coluna ou None}. Cada coluna serve a um papel só.

    "titulo" fica por último de propósito: assim "nome do compositor" não é
    confundido com o título por conter "nome", nem "código da obra" por conter "obra".
    """
    livres = {c: normalizar(c) for c in df.columns if c not in COLUNAS_SAIDA}
    achadas = {}
    for papel in papeis:
        achadas[papel] = _achar(livres, SINONIMOS[papel])
        livres.pop(achadas[papel], None)
    return achadas


def _achar(colunas: dict, sinonimos: list[str]):
    for sinonimo in sinonimos:  # 1ª passada: cabeçalho idêntico
        for coluna, norm in colunas.items():
            if norm == sinonimo:
                return coluna
    for sinonimo in sinonimos:  # 2ª passada: cabeçalho que contém a palavra
        for coluna, norm in colunas.items():
            if f" {sinonimo} " in f" {norm} ":
                return coluna
    return None


def texto_compositor(linha, mapa: dict) -> str:
    """Compositor da linha; se estiver vazio, usa a coluna de créditos."""
    for papel in ("compositor", "creditos"):
        coluna = mapa.get(papel)
        if coluna and str(linha[coluna]).strip():
            return str(linha[coluna]).strip()
    return ""


def texto_iswc(linha, mapa: dict) -> str:
    coluna = mapa.get("iswc")
    return str(linha[coluna]).strip() if coluna else ""


def texto_data_cadastro(linha, mapa: dict) -> str:
    """Data de cadastro da obra. Só é usada dentro do app (ver Buscador.resolver): nunca vai para as APIs."""
    coluna = mapa.get("data_cadastro")
    return str(linha[coluna]).strip() if coluna else ""


def compositor_do_relatorio(df: pd.DataFrame, mapa: dict):
    """Nome do compositor presente em pelo menos 90% das linhas, ou None.

    Conta por nome, não pelo texto inteiro da célula: "Fulano" e "Fulano /
    Parceiro" são linhas do mesmo relatório.
    """
    if len(df) < RELATORIO_MINIMO_DE_LINHAS:
        return None
    contagem, grafia = Counter(), {}
    for _, linha in df.iterrows():
        for nome in {normalizar(n): n for n in dividir_compositores(texto_compositor(linha, mapa))}.items():
            contagem[nome[0]] += 1
            grafia.setdefault(*nome)
    if not contagem:
        return None
    nome, linhas = contagem.most_common(1)[0]
    return grafia[nome] if linhas / len(df) >= RELATORIO_FRACAO else None


def dividir_nomes_artisticos(texto) -> list[str]:
    """"Fulano; Banda Tal" -> ["Fulano", "Banda Tal"], sem repetidos."""
    nomes = {}
    for parte in str(texto or "").replace("\n", ";").split(";"):
        if normalizar(parte):
            nomes.setdefault(normalizar(parte), parte.strip())
    return list(nomes.values())


def artistas_recorrentes(resultado: pd.DataFrame, ignorar=(), minimo: int = RECORRENCIA_MINIMA) -> list[str]:
    """Artistas sugeridos (ou nas alternativas) em várias linhas, do mais frequente para o menos.

    Em um relatório de um compositor só, quem se repete costuma ser ele mesmo
    com o nome artístico. É só uma sugestão, para o usuário confirmar.
    """
    contagem, grafia = Counter(), {}
    for _, linha in resultado.iterrows():
        nomes = [str(linha.get("cantor_sugerido", ""))] + str(linha.get("alternativas", "")).split(";")
        for norm, nome in {normalizar(n): n.strip() for n in nomes if normalizar(n)}.items():
            contagem[norm] += 1
            grafia.setdefault(norm, nome)
    ignorados = {normalizar(i) for i in ignorar}
    return [grafia[n] for n, linhas in contagem.most_common() if linhas >= minimo and n not in ignorados]


def juntar(df: pd.DataFrame, linhas: list[dict]) -> pd.DataFrame:
    """Planilha original + colunas novas (substitui as de uma execução anterior)."""
    novas = pd.DataFrame(linhas, columns=COLUNAS_SAIDA, index=df.index)
    return pd.concat([df.drop(columns=COLUNAS_SAIDA, errors="ignore"), novas], axis=1)


def usa_credits(df: pd.DataFrame) -> bool:
    return "iswc_encontrado_em" in df.columns and df["iswc_encontrado_em"].isin(["Credits.fm", "ambos"]).any()


def para_xlsx(df: pd.DataFrame) -> bytes:
    saida = io.BytesIO()
    with pd.ExcelWriter(saida) as escritor:
        df.to_excel(escritor, index=False)
        if usa_credits(df):
            # A licença CC BY 4.0 do Credits.fm exige atribuição junto com os dados.
            pd.DataFrame({"fontes": [ATRIBUICAO_CREDITS]}).to_excel(escritor, sheet_name="fontes", index=False)
    return saida.getvalue()


def para_csv(df: pd.DataFrame) -> bytes:
    # Ponto e vírgula + BOM: abre direto no Excel em português.
    return df.to_csv(index=False, sep=";").encode("utf-8-sig")
