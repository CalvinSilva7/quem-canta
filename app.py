"""Quem canta? — app Streamlit. Rode com: streamlit run app.py"""

import os

import pandas as pd
import streamlit as st

from cantor import planilha
from cantor.banco import Banco
from cantor.busca import ATRIBUICAO_CREDITS, COLUNAS_SAIDA, Buscador, ClienteHTTP, configurar_log
from cantor.matching import chave_consulta, normalizar

NENHUMA = "(nenhuma)"
CORES = {"baixa": "rgba(255, 193, 7, 0.25)", "nao_encontrado": "rgba(220, 53, 69, 0.25)"}
ROTULOS = {"alta": "Alta", "media": "Média", "baixa": "Baixa", "nao_encontrado": "Não encontrado"}

st.set_page_config(page_title="Quem canta?", layout="wide")
st.title("Quem canta?")
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
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        col_titulo = escolher("Título (obrigatória)", "titulo", obrigatoria=True)
    with c2:
        col_compositor = escolher("Compositor", "compositor")
    with c3:
        col_creditos = escolher("Créditos", "creditos")
    with c4:
        col_iswc = escolher("ISWC (código da obra)", "iswc")
    with c5:
        col_cadastro = escolher("Data de cadastro", "data_cadastro")
        st.caption(
            "Opcional. Use se o relatório tiver a data em que a obra foi cadastrada (como o da UBC). "
            "Se a gravação mais antiga achada for de mais de 3 anos depois, o app avisa que a original "
            "pode estar faltando. A data fica só aqui: não é enviada a nenhum serviço."
        )
    st.dataframe(df.head(5), hide_index=True)

mapa = {
    "titulo": col_titulo, "compositor": col_compositor, "creditos": col_creditos, "iswc": col_iswc,
    "data_cadastro": col_cadastro,
}
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
        f"**Modo relatório ativo.** Esta planilha parece ser o relatório de um compositor só: **{dono}**, "
        "que aparece como compositor em pelo menos 90% das linhas. Nesse modo o app "
        "busca as obras dele de uma vez no MusicBrainz e não usa \"ele é o compositor\" como pista de quem gravou "
        "(ele é o autor de todas as linhas, isso não diferencia nada)."
    )
    modo_relatorio = st.checkbox(
        "Usar o modo relatório", value=True, help="Desmarque se a planilha não for o catálogo de um compositor só."
    )


def nomes_confirmados() -> list[str]:
    return planilha.dividir_nomes_artisticos(st.session_state.get("nomes_artisticos", ""))


def adicionar_nomes(chave):
    """Passa as sugestões marcadas pelo usuário para o campo de nomes artísticos."""
    st.session_state.nomes_artisticos = "; ".join(nomes_confirmados() + st.session_state.get(chave, []))
    st.session_state[chave] = []
    st.session_state.nomes_mudaram = True


def confirmar_sugestoes(rotulo, sugestoes, chave):
    """Sugestões nunca valem sozinhas: só entram depois que o usuário marca e confirma."""
    ja = {normalizar(n) for n in nomes_confirmados()}
    pendentes = [s for s in sugestoes if normalizar(s) not in ja]
    if pendentes:
        st.multiselect(rotulo, pendentes, key=chave, placeholder="Marque só os que são o próprio compositor")
        st.button("Adicionar aos nomes artísticos", key=f"{chave}_botao", on_click=adicionar_nomes, args=(chave,))
    return pendentes


if modo_relatorio:
    with st.container(border=True):
        st.markdown("#### O compositor também canta? Informe o nome artístico dele")
        st.markdown(
            f"O relatório traz o nome civil (**{dono}**), mas o Deezer e o MusicBrainz só conhecem o "
            "**nome artístico**. Sem essa ligação, o app não percebe quando é o próprio compositor cantando "
            "e acaba chutando o artista mais famoso que tem uma música com o mesmo título."
        )
        quando, como = st.columns(2)
        quando.markdown(
            "**Quando preencher**\n"
            "- O compositor grava as próprias músicas com um nome diferente do que está na planilha.\n"
            "- Ele faz ou fez parte de uma banda, dupla ou projeto.\n"
            "- Você processou e quase tudo saiu com confiança **baixa** ou com artistas sem relação com ele.\n\n"
            "**Quando deixar vazio**\n"
            "- Ele é só autor (letrista) e não costuma gravar.\n"
            "- Você não tem certeza do nome: um nome errado traz a discografia de outra pessoa."
        )
        como.markdown(
            "**Como usar**\n"
            "1. Digite abaixo o nome artístico, exatamente como aparece nas plataformas de música. "
            "Mais de um? Separe com ponto e vírgula.\n"
            "2. Não sabe o nome? Clique em **Sugerir nomes artísticos**, ou processe a planilha uma vez: "
            "abaixo da prévia o app mostra os artistas que mais se repetiram.\n"
            "3. Clique em **Processar**. Sempre que mudar os nomes, processe de novo.\n\n"
            "**O que muda no resultado**\n"
            "- As músicas que ele mesmo lançou passam a ser reconhecidas pela discografia dele.\n"
            "- Regravações recentes de outros artistas deixam de passar por \"gravação original\"."
        )
        st.text_input(
            "Nomes artísticos, projetos e grupos do compositor",
            key="nomes_artisticos",
            placeholder="Ex.: Nome Artístico; Nome da Banda",
            help="Um ou mais nomes, separados por ponto e vírgula. Valem como o próprio compositor, e a discografia "
            "de cada um (Deezer e MusicBrainz) é comparada com os títulos da planilha.",
        )
        if nomes_confirmados():
            st.success(
                "O app vai tratar **" + "**, **".join(nomes_confirmados()) + f"** como o próprio {dono} "
                "e procurar os títulos da planilha na discografia " + ("deles." if len(nomes_confirmados()) > 1 else "dele.")
            )
        else:
            st.warning(f"Nenhum nome artístico informado: o app vai procurar só por \"{dono}\".")
        if st.button(
            "Sugerir nomes artísticos",
            help="Procura no Deezer e no MusicBrainz artistas cujo nome é formado só por palavras do nome do "
            "compositor. Não acha apelidos nem nomes de banda: para esses, processe uma vez e veja a lista "
            "abaixo da prévia.",
        ):
            st.session_state.sugestoes_do_nome = buscador.sugerir_nomes_artisticos(dono)
            if not st.session_state.sugestoes_do_nome:
                st.caption(
                    "Não achei nenhum artista com nome formado só por palavras do nome do compositor. "
                    "Se ele usa um apelido ou nome de banda, digite acima, ou processe uma vez e veja as sugestões "
                    "abaixo da prévia."
                )
        confirmar_sugestoes(
            "Artistas com nome formado por palavras do nome do compositor (marque só se for ele mesmo)",
            st.session_state.get("sugestoes_do_nome", []), "sugestoes_marcadas",
        )
artisticos = nomes_confirmados() if modo_relatorio else []
if (modo_relatorio, artisticos) != st.session_state.get("modo_relatorio"):
    # Ligou ou desligou o modo, ou mudaram os nomes artísticos: o resultado anterior não vale mais.
    st.session_state.modo_relatorio = (modo_relatorio, artisticos)
    st.session_state.pop("resultado", None)
if st.session_state.pop("nomes_mudaram", False):
    st.warning("Nomes artísticos atualizados. Clique em **Processar** de novo para o resultado levar isso em conta.")

# --- 3. processamento -------------------------------------------------------

st.caption(
    f"{len(df)} linhas. Cada música nova leva de 2 a 5 segundos (o MusicBrainz permite 1 consulta por segundo); "
    "as que já foram consultadas saem do cache na hora. Se interromper, basta processar de novo: nada se perde."
)
if st.button("Processar", type="primary"):
    barra = st.progress(0.0, text="Iniciando…")
    if modo_relatorio:
        barra.progress(0.0, text=f"Listando as obras de {dono} no MusicBrainz…")
        catalogo = buscador.preparar_relatorio(dono, artisticos)
        if catalogo["erro"]:
            st.warning(
                f"Não consegui listar as obras e a discografia de {dono} ({catalogo['erro']}); "
                "cada título será buscado sozinho."
            )
    linhas = []
    for i, (_, linha) in enumerate(df.iterrows(), start=1):
        titulo = str(linha[col_titulo]).strip()
        barra.progress(i / len(df), text=f"{i}/{len(df)} — {titulo}")
        resultado_linha = buscador.resolver(
            titulo, planilha.texto_compositor(linha, mapa), planilha.texto_iswc(linha, mapa),
            data_cadastro=planilha.texto_data_cadastro(linha, mapa),
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

if modo_relatorio and [
    r for r in planilha.artistas_recorrentes(resultado) if normalizar(r) not in {normalizar(n) for n in artisticos}
]:
    with st.container(border=True):
        st.markdown("#### Algum destes artistas é o próprio compositor?")
        st.markdown(
            f"Estes nomes apareceram em várias linhas do resultado. Em um relatório de um compositor só, quem se "
            f"repete muitas vezes costuma ser ele mesmo com o nome artístico, ou a banda dele. Se algum for "
            f"**{dono}**, marque, clique em **Adicionar** e depois em **Processar** de novo. "
            "Não marque intérpretes que apenas gravaram músicas dele."
        )
        confirmar_sugestoes(
            "Artistas que apareceram em várias linhas (podem ser nomes artísticos do compositor)",
            planilha.artistas_recorrentes(resultado), "recorrentes_marcados",
        )

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
