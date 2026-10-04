"""Mede a taxa de acerto do sistema contra uma planilha preenchida à mão.

Uso:
    python avaliar.py planilha_conferida.xlsx
    python avaliar.py planilha_conferida.csv --coluna-cantor "Intérprete" --erros erros.csv
    python avaliar.py musicas.xlsx --gabarito gabarito.xlsx

Com --gabarito, o cantor correto vem de outra planilha (casada pela coluna
"id", se as duas tiverem, ou pela ordem das linhas). Uma coluna "tambem_aceito"
pode listar outras respostas válidas, separadas por ponto e vírgula.

As correções manuais salvas no banco são ignoradas aqui, senão a avaliação
mediria as suas próprias respostas em vez do sistema.
"""

import argparse
import re
import sys
import time
from pathlib import Path

import pandas as pd

from cantor import planilha
from cantor.banco import Banco
from cantor.busca import NIVEIS, Buscador, ClienteHTTP, ErroDeRede, configurar_log
from cantor.matching import nomes_parecidos


ESPERADO, ACEITOS = "__esperado", "__aceitos"


def acertou(esperados: list[str], sugeridos: list[str], buscador: Buscador) -> bool:
    sugeridos = [s for s in sugeridos if s]
    if any(nomes_parecidos(e, s) for e in esperados for s in sugeridos):
        return True
    # "Tom Jobim" e "Antônio Carlos Jobim" são a mesma pessoa: confere pelos aliases.
    try:
        ids = set().union(*(buscador.ids_do_artista(e) for e in esperados))
        return any(ids & buscador.ids_do_artista(s) for s in sugeridos)
    except ErroDeRede:
        return False


def avaliar(df: pd.DataFrame, mapa: dict, buscador: Buscador, ao_avancar=None) -> pd.DataFrame:
    """Uma linha por música conferida, com o que o sistema respondeu."""
    conferidas = df[df[mapa["cantor"]].str.strip() != ""]
    linhas = []
    for i, (_, linha) in enumerate(conferidas.iterrows(), start=1):
        titulo = str(linha[mapa["titulo"]]).strip()
        esperado = str(linha[mapa["cantor"]]).strip()
        aceitos = [esperado] + [a.strip() for a in re.split(r"[;|]", str(linha.get(ACEITOS, ""))) if a.strip()]
        inicio, oficiais_antes = time.monotonic(), (buscador.consultas_oficiais, buscador.tempo_oficiais)
        iswc_antes = buscador.tempo_iswc
        r = buscador.resolver(titulo, planilha.texto_compositor(linha, mapa), planilha.texto_iswc(linha, mapa))
        linhas.append(
            {
                "titulo": titulo,
                "esperado": esperado,
                "sugerido": r.cantor_sugerido,
                "alternativas": "; ".join(r.alternativas),
                "confianca": r.confianca,
                "regra": r.regra,
                "segundos": time.monotonic() - inicio,
                "consultas_oficiais": buscador.consultas_oficiais - oficiais_antes[0],
                "segundos_oficiais": buscador.tempo_oficiais - oficiais_antes[1],
                "segundos_iswc": buscador.tempo_iswc - iswc_antes,
                "iswc_normalizado": r.iswc_normalizado,
                "iswc_encontrado_em": r.iswc_encontrado_em,
                "acerto": acertou(aceitos, [r.cantor_sugerido], buscador),
                "acerto_top3": acertou(aceitos, [r.cantor_sugerido] + r.alternativas, buscador),
                "erro_de_rede": r.observacao.startswith("erro de rede"),
                "observacao": r.observacao,
            }
        )
        if ao_avancar:
            ao_avancar(i, len(conferidas), titulo)
    return pd.DataFrame(linhas)


def resumir(detalhe: pd.DataFrame) -> pd.DataFrame:
    """Taxa de acerto por nível de confiança (linhas com erro de rede ficam de fora)."""
    validas = detalhe[~detalhe["erro_de_rede"]]
    linhas = []
    for nivel in reversed(NIVEIS):
        grupo = validas[validas["confianca"] == nivel]
        linhas.append(_linha_resumo(nivel, grupo, respondeu=nivel != "nao_encontrado"))
    linhas.append(_linha_resumo("TOTAL", validas, respondeu=True))
    return pd.DataFrame(linhas)


def resumir_por_regra(detalhe: pd.DataFrame) -> pd.DataFrame:
    """Acertos por regra, da mais usada para a menos (sem as linhas com erro de rede)."""
    validas = detalhe[~detalhe["erro_de_rede"]]
    grupos = validas.groupby(["regra", "confianca"], sort=False)
    tabela = grupos.agg(linhas=("acerto", "size"), acertos=("acerto", "sum"), top3=("acerto_top3", "sum")).reset_index()
    tabela["taxa"] = (tabela["acertos"] / tabela["linhas"]).map("{:.0%}".format)
    return tabela.sort_values(["linhas", "regra"], ascending=[False, True])[
        ["regra", "confianca", "linhas", "acertos", "taxa", "top3"]
    ]


def resumir_tempo(detalhe: pd.DataFrame) -> str:
    """Tempo por linha e quanto dele foi a verificação de lançamentos oficiais."""
    consultadas = detalhe[detalhe["segundos"] > 0.5]  # o resto veio do cache
    if consultadas.empty:
        return "Tempo: todas as linhas vieram do cache."
    verificadas = consultadas[consultadas["consultas_oficiais"] > 0]
    texto = (
        f"Tempo: {consultadas['segundos'].mean():.1f} s por linha em média "
        f"(mediana {consultadas['segundos'].median():.1f} s; {len(consultadas)} linhas consultadas nas APIs)."
    )
    if not verificadas.empty:
        texto += (
            f"\nVerificação de lançamentos oficiais: {len(verificadas)} linhas, "
            f"{verificadas['consultas_oficiais'].mean():.1f} consultas e "
            f"{verificadas['segundos_oficiais'].mean():.1f} s a mais por linha verificada "
            f"({consultadas['segundos_oficiais'].sum() / len(consultadas):.1f} s por linha na média geral)."
        )
    com_iswc = consultadas[consultadas["segundos_iswc"] > 0]
    if not com_iswc.empty:
        texto += (
            f"\nConsulta do ISWC (MusicBrainz, Credits.fm e Deezer por ISRC): {len(com_iswc)} linhas, "
            f"{com_iswc['segundos_iswc'].mean():.1f} s por linha com ISWC."
        )
    return texto


def resumir_iswc(detalhe: pd.DataFrame) -> str:
    """Em quantas linhas o ISWC foi achado em cada fonte."""
    com = detalhe[detalhe["iswc_encontrado_em"] != ""]
    if com.empty:
        return "ISWC: nenhuma linha com ISWC válido."
    contagem = com["iswc_encontrado_em"].value_counts()
    partes = ", ".join(f"{fonte}: {int(contagem.get(fonte, 0))}" for fonte in ["MB", "Credits.fm", "ambos", "nenhum"])
    divergentes = int(com["regra"].str.contains("iswc_titulo_diverge").sum())
    invalidos = int(detalhe["regra"].str.contains("iswc_invalido").sum())
    return f"ISWC em {len(com)} linhas | achado em {partes} | título divergente: {divergentes} | inválidos: {invalidos}"


def _linha_resumo(nome: str, grupo: pd.DataFrame, respondeu: bool) -> dict:
    n = len(grupo)

    def taxa(coluna):
        return f"{grupo[coluna].mean():.0%}" if n and respondeu else "-"

    return {
        "confianca": nome,
        "linhas": n,
        "acertos": int(grupo["acerto"].sum()) if respondeu else "-",
        "taxa": taxa("acerto"),
        "taxa_top3": taxa("acerto_top3"),
    }


def juntar_gabarito(df: pd.DataFrame, gabarito: pd.DataFrame, coluna_cantor=None, nome_da_planilha="") -> pd.DataFrame:
    """Acrescenta ao df as colunas com a resposta esperada vindas de outra planilha."""
    coluna = coluna_cantor or planilha.detectar_colunas(gabarito, papeis=("cantor",))["cantor"]
    if not coluna or coluna not in gabarito.columns:
        sys.exit(f"Não identifiquei a coluna do cantor no gabarito. Colunas: {', '.join(gabarito.columns)}")
    if "arquivo" in gabarito.columns:
        # Um gabarito para várias planilhas: fica só a parte cujo nome começa o nome desta.
        desta = gabarito[gabarito["arquivo"].map(lambda a: bool(a) and nome_da_planilha.startswith(a))]
        if desta.empty:
            sys.exit(f'O gabarito não tem linhas para "{nome_da_planilha}" na coluna "arquivo".')
        gabarito = desta.reset_index(drop=True)
    if "id" in df.columns and "id" in gabarito.columns:
        gabarito = gabarito.drop_duplicates("id").set_index("id").reindex(df["id"]).fillna("")
    elif len(gabarito) != len(df):
        sys.exit(f"O gabarito tem {len(gabarito)} linhas e a planilha tem {len(df)}; sem coluna 'id' não dá para casar.")
    df = df.copy()
    df[ESPERADO] = gabarito[coluna].to_numpy()
    df[ACEITOS] = gabarito["tambem_aceito"].to_numpy() if "tambem_aceito" in gabarito.columns else ""
    return df


def main():
    for saida in (sys.stdout, sys.stderr):
        saida.reconfigure(encoding="utf-8")  # o console do Windows não é UTF-8 por padrão
    ap = argparse.ArgumentParser(description="Taxa de acerto por nível de confiança.")
    ap.add_argument("arquivo", type=Path, help="planilha .xlsx ou .csv com o cantor correto preenchido")
    ap.add_argument("--coluna-titulo")
    ap.add_argument("--coluna-compositor")
    ap.add_argument("--coluna-cantor", help="coluna com o cantor conferido à mão")
    ap.add_argument("--gabarito", type=Path, help="planilha separada com o cantor correto")
    ap.add_argument("--sem-cache", action="store_true", help="consulta tudo de novo nas APIs")
    ap.add_argument("--sem-iswc", action="store_true", help="ignora a coluna de ISWC (para medir o ganho dela)")
    ap.add_argument("--sem-modo-relatorio", action="store_true", help="não trata a planilha como catálogo de um compositor só")
    ap.add_argument("--limite", type=int, help="avalia só as N primeiras linhas")
    ap.add_argument("--erros", type=Path, help="salva em CSV as linhas em que o sistema errou")
    ap.add_argument("--detalhe", type=Path, help="salva em CSV todas as linhas avaliadas")
    args = ap.parse_args()

    df = planilha.ler_planilha(args.arquivo.read_bytes(), args.arquivo.name)
    mapa = planilha.detectar_colunas(df, papeis=("compositor", "creditos", "cantor", "iswc", "titulo"))
    if args.sem_iswc:
        mapa["iswc"] = None
    if args.gabarito:
        gabarito = planilha.ler_planilha(args.gabarito.read_bytes(), args.gabarito.name)
        df = juntar_gabarito(df, gabarito, args.coluna_cantor, args.arquivo.stem)
        mapa["cantor"], args.coluna_cantor = ESPERADO, None
    elif "tambem_aceito" in df.columns:
        df[ACEITOS] = df["tambem_aceito"]
    if args.limite:
        df = df.head(args.limite)
    for papel, informada in [
        ("titulo", args.coluna_titulo),
        ("compositor", args.coluna_compositor),
        ("cantor", args.coluna_cantor),
    ]:
        if informada:
            if informada not in df.columns:
                sys.exit(f'Coluna "{informada}" não existe. Colunas: {", ".join(df.columns)}')
            mapa[papel] = informada
    for papel in ("titulo", "cantor"):
        if not mapa[papel]:
            sys.exit(f"Não identifiquei a coluna de {papel}. Use --coluna-{papel}. Colunas: {', '.join(df.columns)}")
    print(
        f"Colunas: título={mapa['titulo']!r}, compositor={mapa['compositor']!r}, "
        f"iswc={mapa.get('iswc')!r}, cantor={mapa['cantor']!r}"
    )

    configurar_log()
    buscador = Buscador(Banco(), ClienteHTTP(), usar_cache=not args.sem_cache, usar_correcoes=False)
    dono = None if args.sem_modo_relatorio else planilha.compositor_do_relatorio(df, mapa)
    if dono:
        catalogo = buscador.preparar_relatorio(dono)
        print(
            f"Modo relatório: {dono} é compositor de pelo menos 90% das linhas; "
            f"{len(catalogo['obras'])} obras listadas no MusicBrainz em {catalogo['segundos']:.1f} s"
            + (f" (falhou: {catalogo['erro']})" if catalogo["erro"] else "")
        )
    detalhe = avaliar(df, mapa, buscador, lambda i, n, t: print(f"  [{i}/{n}] {t}", file=sys.stderr))
    if detalhe.empty:
        sys.exit("Nenhuma linha com o cantor preenchido para comparar.")

    print()
    print(resumir(detalhe).to_string(index=False))
    print()
    print("Acertos por regra:")
    print(resumir_por_regra(detalhe).to_string(index=False))
    print()
    print(resumir_iswc(detalhe))
    print(resumir_tempo(detalhe))
    if args.detalhe:
        args.detalhe.write_bytes(planilha.para_csv(detalhe))
    erros_alta = detalhe[(detalhe["confianca"] == "alta") & ~detalhe["acerto"]]
    print(f"\nErros com confiança ALTA: {len(erros_alta)}")
    for _, e in erros_alta.iterrows():
        print(f"  - {e['titulo']}: esperado {e['esperado']!r}, sugerido {e['sugerido']!r} ({e['observacao']})")
    falhas = int(detalhe["erro_de_rede"].sum())
    if falhas:
        print(f"\n{falhas} linha(s) com erro de rede ficaram fora da conta. Rode de novo para completar.")
    erros = detalhe[~detalhe["acerto"] & ~detalhe["erro_de_rede"]]
    if args.erros:
        args.erros.write_bytes(planilha.para_csv(erros.drop(columns=["erro_de_rede", "segundos", "consultas_oficiais", "segundos_oficiais", "segundos_iswc"])))
        print(f"\n{len(erros)} linha(s) sem acerto salvas em {args.erros}")


if __name__ == "__main__":
    main()
