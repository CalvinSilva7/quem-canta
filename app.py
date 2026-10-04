"""Quem canta? — app Streamlit. Rode com: streamlit run app.py"""

import os

import pandas as pd
import streamlit as st

from cantor import planilha
from cantor.banco import Banco
from cantor.busca import ATRIBUICAO_CREDITS, COLUNAS_SAIDA, Buscador, ClienteHTTP, configurar_log
from cantor.matching import chave_consulta

NENHUMA = "(nenhuma)"
CORES = {"baixa": "rgba(255, 193, 7, 0.25)", "nao_encontrado": "rgba(220, 53, 69, 0.25)"}
ROTULOS = {"alta": "Alta", "media": "Média", "baixa": "Baixa", "nao_encontrado": "Não encontrado"}

st.set_page_config(page_title="Quem canta?", page_icon="🎤", layout="wide")
st.title("🎤 Quem canta?")
st.caption("Preenche o intérprete das músicas de uma planilha usando MusicBrainz e Deezer.")

banco = Banco()
configurar_log()

# --- barra lateral ----------------------------------------------------------

with st.sidebar:
    st.header("Configurações")
    contato = st.text_input(
        "Contato para o MusicBrainz",
        value=os.environ.get("QUEMCANTA_CONTATO", ""),
        placeholder="seu e-mail ou site",
        help="Vai no cabeçalho User-Agent. O MusicBrainz pede um contato para avisar em caso de problema.",
    )
    ignorar_cache = st.checkbox("Ignorar cache nesta execução", help="Consulta tudo de novo nas APIs.")
    st.divider()
    st.write(f"Consultas em cache: **{banco.contar_cache()}**")
    if st.button("Limpar cache"):
        banco.limpar_cache()
        st.rerun()
    correcoes = banco.listar_correcoes()
    with st.expander(f"Correções manuais ({len(correcoes)})"):
        if correcoes:
            st.dataframe(
                pd.DataFrame(correcoes, columns=["título", "compositor", "cantor", "salvo em"]),
                hide_index=True,
            )
        else:
            st.write("Nenhuma ainda. Edite o cantor na prévia para criar uma.")

buscador = Buscador(banco, ClienteHTTP(contato), usar_cache=not ignorar_cache)

# --- 1. upload --------------------------------------------------------------

arquivo = st.file_uploader("Planilha com as músicas", type=["xlsx", "csv"])
if arquivo is not None and st.session_state.get("arquivo_id") != arquivo.file_id:
    try:
        st.session_state.original = planilha.ler_planilha(arquivo.getvalue(), arquivo.name)
    except Exception as e:  # arquivo corrompido, formato inesperado etc.
        st.error(f"Não consegui ler o arquivo: {e}")
        st.stop()
    st.session_state.arquivo_id = arquivo.file_id
    st.session_state.nome_arquivo = arquivo.name.rsplit(".", 1)[0]
    st.session_state.pop("resultado", None)

df = st.session_state.get("original")
if df is None:
    st.info("Envie um arquivo .xlsx ou .csv para começar.")
    st.stop()
if df.empty:
    st.warning("A planilha está vazia.")
    st.stop()

# --- 2. mapeamento de colunas ----------------------------------------------

detectadas = planilha.detectar_colunas(df)
colunas = list(df.columns)


def escolher(rotulo, papel, obrigatoria=False):
    opcoes = colunas if obrigatoria else [NENHUMA] + colunas
    padrao = detectadas[papel]
    indice = opcoes.index(padrao) if padrao in opcoes else None if obrigatoria else 0
    escolha = st.selectbox(rotulo, opcoes, index=indice, placeholder="Escolha a coluna")
    return None if escolha == NENHUMA else escolha


with st.expander("Colunas da planilha", expanded=detectadas["titulo"] is None):
    if detectadas["titulo"] is None:
        st.warning("Não identifiquei a coluna do título. Escolha abaixo.")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        col_titulo = escolher("Título (obrigatória)", "titulo", obrigatoria=True)
    with c2:
        col_compositor = escolher("Compositor", "compositor")
    with c3:
        col_creditos = escolher("Créditos", "creditos")
    with c4:
        col_iswc = escolher("ISWC (código da obra)", "iswc")
    st.dataframe(df.head(5), hide_index=True)

mapa = {"titulo": col_titulo, "compositor": col_compositor, "creditos": col_creditos, "iswc": col_iswc}
if col_titulo is None:
    st.stop()
if mapa != st.session_state.get("mapa"):
    # Mudou o mapeamento: o resultado anterior não vale mais.
    st.session_state.mapa = mapa
    st.session_state.pop("resultado", None)

# --- modo relatório ---------------------------------------------------------

dono = planilha.compositor_do_relatorio(df, mapa)
modo_relatorio = False
if dono:
    st.info(
        f"**Modo relatório ativo:** {dono} aparece como compositor em pelo menos 90% das linhas. "
        "Nesse modo, ser o compositor não conta como evidência de quem gravou a música, "
        "e as obras dele são listadas de uma vez no MusicBrainz antes de processar."
    )
    modo_relatorio = st.checkbox(
        "Usar o modo relatório", value=True, help="Desmarque se a planilha não for o catálogo de um compositor só."
    )
if modo_relatorio != st.session_state.get("modo_relatorio"):
    # Ligou ou desligou o modo: o resultado anterior não vale mais.
    st.session_state.modo_relatorio = modo_relatorio
    st.session_state.pop("resultado", None)

# --- 3. processamento -------------------------------------------------------

st.caption(
    f"{len(df)} linhas. Cada música nova leva de 2 a 5 segundos (o MusicBrainz permite 1 consulta por segundo); "
    "as que já foram consultadas saem do cache na hora. Se interromper, basta processar de novo: nada se perde."
)
if st.button("Processar", type="primary"):
    barra = st.progress(0.0, text="Iniciando…")
    if modo_relatorio:
        barra.progress(0.0, text=f"Listando as obras de {dono} no MusicBrainz…")
        catalogo = buscador.preparar_relatorio(dono)
        if catalogo["erro"]:
            st.warning(f"Não consegui listar as obras de {dono} ({catalogo['erro']}); cada título será buscado sozinho.")
    linhas = []
    for i, (_, linha) in enumerate(df.iterrows(), start=1):
        titulo = str(linha[col_titulo]).strip()
        barra.progress(i / len(df), text=f"{i}/{len(df)} — {titulo}")
        resultado_linha = buscador.resolver(
            titulo, planilha.texto_compositor(linha, mapa), planilha.texto_iswc(linha, mapa)
        )
        linhas.append(resultado_linha.para_linha())
    barra.empty()
    st.session_state.resultado = planilha.juntar(df, linhas)
    st.session_state.versao_editor = st.session_state.get("versao_editor", 0) + 1

resultado = st.session_state.get("resultado")
if resultado is None:
    st.stop()

# --- 4. prévia --------------------------------------------------------------

st.subheader("Prévia")
contagem = resultado["confianca"].value_counts()
for bloco, (nivel, rotulo) in zip(st.columns(4), ROTULOS.items()):
    bloco.metric(rotulo, int(contagem.get(nivel, 0)))

so_duvidosas = st.checkbox("Mostrar só baixa confiança e não encontradas")
visivel = resultado[resultado["confianca"].isin(CORES)] if so_duvidosas else resultado
st.caption(
    "Linhas amarelas: baixa confiança. Vermelhas: não encontrado. "
    "Dê dois cliques em **cantor_sugerido** para corrigir; a correção fica salva e vale nas próximas planilhas."
)


def destacar(linha):
    cor = CORES.get(linha["confianca"])
    return [f"background-color: {cor}" if cor else ""] * len(linha)


editado = st.data_editor(
    visivel.style.apply(destacar, axis=1),
    disabled=[c for c in resultado.columns if c != "cantor_sugerido"],
    column_config={"fonte_link": st.column_config.LinkColumn("fonte_link")},
    hide_index=True,
    key=f"editor_{st.session_state.get('versao_editor', 0)}",
)

novos = editado["cantor_sugerido"].fillna("").astype(str).str.strip()
alteradas = novos[novos != visivel["cantor_sugerido"]]
if not alteradas.empty:
    chaves = resultado.apply(
        lambda l: chave_consulta(l[col_titulo], planilha.texto_compositor(l, mapa)), axis=1
    )
    for indice, cantor in alteradas.items():
        linha = resultado.loc[indice]
        corrigido = buscador.corrigir(linha[col_titulo], planilha.texto_compositor(linha, mapa), cantor)
        # A correção vale para todas as linhas com o mesmo título e compositor.
        alvo = chaves[indice]
        mesmas = chaves[chaves.map(lambda chave: chave == alvo)].index
        # O ISWC é dado da linha, não da resposta: a correção não mexe nele.
        valores = corrigido.para_linha()
        colunas = [c for c in COLUNAS_SAIDA if not c.startswith("iswc_")]
        resultado.loc[mesmas, colunas] = [valores[c] for c in colunas]
    st.session_state.versao_editor += 1
    st.session_state.aviso = "Correção salva."
    st.rerun()
if "aviso" in st.session_state:
    st.toast(st.session_state.pop("aviso"))

# --- 5. download ------------------------------------------------------------

st.subheader("Baixar")
if planilha.usa_credits(resultado):
    st.caption(f"{ATRIBUICAO_CREDITS}. A atribuição também vai na aba \"fontes\" do Excel.")
nome = f"{st.session_state.get('nome_arquivo', 'planilha')}_com_cantor"
b1, b2, _ = st.columns([1, 1, 4])
b1.download_button(
    "Excel (.xlsx)",
    planilha.para_xlsx(resultado),
    f"{nome}.xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
)
b2.download_button("CSV", planilha.para_csv(resultado), f"{nome}.csv", "text/csv")
