"""Créditos nas plataformas: etapa 1, importar o relatório do titular.

Rode com: streamlit run creditos_app.py
(tela separada do app.py enquanto o fluxo novo é construído por etapas)
"""

import io
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from cantor import planilha
from cantor.matching import normalizar
from creditos import andamento, atualizacao, ecad, ubc
from creditos.versao import VERSAO
from creditos.captura import slug
from creditos.modelo import SITUACOES, Relatorio

# Onde ficam os casos (o que já foi lido e os prints): fora da pasta do programa, para uma atualização nunca tocar neles.
PASTA_DE_DADOS = Path(os.environ.get("QUEMCANTA_DADOS") or Path.home() / "Documents" / "Quem Canta")

ETAPAS = ["1. Buscar intérpretes", "2. Verificar créditos e tirar prints"]


@st.fragment(run_every=1)
def painel_do_andamento(rotulo="Coleta", parar="Parar coleta"):
    """A barra de progresso e o botão de parar. Atualiza sozinho a cada segundo, sem recarregar o resto da tela."""
    tarefa = andamento.atual()
    if tarefa is None or not tarefa.viva:
        st.rerun()  # terminou: a tela inteira é refeita, agora com o resultado
    retrato = tarefa.andamento.retrato()
    st.progress(retrato["fracao"], text=f"**{rotulo}: {round(retrato['fracao'] * 100)}%** · {retrato['restante']}")
    st.caption(f"Etapa {retrato['etapa']} de {retrato['etapas']}: {retrato['nome']} · {retrato['detalhe']}")
    st.caption(" · ".join(f"{nome}: {situacao}" for nome, situacao in retrato["situacoes"]))
    parando = retrato["parando"]
    if not parando and st.button(parar, help="O que já foi lido fica guardado: ao rodar de novo, continua de onde parou."):
        tarefa.pedir_parada()
        parando = True
    if parando:
        st.warning("Parando… o app termina a página que está aberta e para. O que já foi lido fica guardado.")


st.set_page_config(page_title="Créditos nas plataformas", layout="wide")
st.title("Créditos nas plataformas")

if "atualizacao" not in st.session_state:  # consulta sozinho uma vez por sessão; o botão consulta de novo
    st.session_state.atualizacao = atualizacao.consultar()
nova = st.session_state.atualizacao
if nova:
    with st.container(border=True):
        st.markdown(
            f"**Há uma versão nova do app: {nova['versao']}** (a instalada é a {VERSAO})."
            + (f" O que mudou: {nova['notas']}" if nova["notas"] else "")
        )
        if st.button("Baixar e instalar a versão nova"):
            try:
                escritos = atualizacao.instalar(nova)
            except atualizacao.FalhaNaAtualizacao as e:
                st.error(f"A atualização não foi instalada: {e}. O app continua na versão {VERSAO}.")
            else:
                st.session_state.atualizacao = None
                st.success(
                    f"Versão {nova['versao']} instalada ({len(escritos)} arquivos). **Feche a janela preta do programa e abra "
                    "o app de novo** para ela valer. Os casos e os prints não foram tocados."
                )
                if "requirements.txt" in escritos:
                    st.warning(
                        "Esta versão usa componentes novos. Antes de abrir o app de novo, dê dois cliques em "
                        "**Instalar.cmd**, na pasta do programa, e espere terminar."
                    )
                st.stop()
else:
    lado_da_versao, lado_do_botao = st.columns([5, 1])
    lado_da_versao.caption(f"Versão instalada: {VERSAO}")
    if lado_do_botao.button("Procurar atualização", help="Consulta se já saiu uma versão mais nova do app."):
        estado, publicada = atualizacao.verificar()
        if estado == atualizacao.NOVA:
            st.session_state.atualizacao = publicada
            st.rerun()
        elif estado == atualizacao.EM_DIA:
            st.success(f"Você já está na versão mais recente ({VERSAO}).")
        else:
            st.warning("Não consegui consultar agora. Confira a internet e tente de novo; o app funciona normalmente sem isso.")
st.caption(
    "Do relatório do titular à planilha, à lista da petição e às provas. O arquivo é lido só neste computador; "
    "o app nunca acessa o ECADNET nem o portal de associação nenhuma."
)

arquivo = st.file_uploader(
    "Relatório analítico do ECAD (PDF) ou relatório de obras em planilha (.xlsx ou .csv)", type=["pdf", "xlsx", "csv"]
)
if arquivo is not None and st.session_state.get("arquivo_id") != arquivo.file_id:
    try:
        if arquivo.name.lower().endswith(".pdf"):
            relatorio = ecad.ler_pdf(io.BytesIO(arquivo.getvalue()))
        else:
            relatorio = ubc.ler_planilha(planilha.ler_planilha(arquivo.getvalue(), arquivo.name))
    except Exception as e:  # arquivo de outro formato, PDF escaneado etc.
        st.error(f"Não consegui ler o relatório: {e}")
        st.stop()
    st.session_state.arquivo_id = arquivo.file_id
    st.session_state.relatorio = relatorio
    for chave in ("conferidos", "conferidos_fora", "conferida_id", "lista_de_interpretes", "coletas", "saidas"):
        st.session_state.pop(chave, None)  # relatório novo: o que era do anterior não vale mais

em_curso = andamento.atual()
if em_curso is not None and em_curso.viva and st.session_state.get("relatorio") is None:
    # A página foi recarregada no meio de uma coleta: a coleta continua, e a tela volta a acompanhá-la.
    st.session_state.relatorio = em_curso.dados["relatorio"]
    if em_curso.dados.get("conferidos"):
        st.session_state.conferidos = em_curso.dados["conferidos"]
relatorio: Relatorio | None = st.session_state.get("relatorio")
if relatorio is None:
    st.info("Envie o relatório para começar.")
    st.stop()

# --- resumo e conferência ---------------------------------------------------

st.subheader(relatorio.nome_titular or "Titular não identificado")
if relatorio.pseudonimo_titular:
    st.caption(f"Pseudônimo no relatório: {relatorio.pseudonimo_titular}")
com_coautoria = sum(len(o.autores) > 1 for o in relatorio.obras)
blocos = st.columns(4)
blocos[0].metric("Obras lidas", len(relatorio.obras))
blocos[1].metric("Obras declaradas no relatório", relatorio.total_declarado if relatorio.total_declarado is not None else "não informa")
blocos[2].metric("Com coautoria", com_coautoria)
blocos[3].metric("Origem", relatorio.origem, relatorio.emitido_em or None, delta_color="off")
if relatorio.total_declarado is not None and relatorio.total_declarado == len(relatorio.obras) and not relatorio.avisos:
    st.success("Contagem conferida: todas as obras declaradas no relatório foram lidas.")
for aviso in relatorio.avisos:
    st.warning(aviso)

# --- as duas etapas do trabalho ------------------------------------------------

em_curso = andamento.atual()
if em_curso is not None and "etapa" not in st.session_state:  # página recarregada: volta para a etapa que está rodando
    st.session_state.etapa = ETAPAS[0] if em_curso.dados.get("tipo") == "interpretes" else ETAPAS[1]
etapa = st.radio("Etapa", ETAPAS, horizontal=True, key="etapa", label_visibility="collapsed")
pasta_do_caso = PASTA_DE_DADOS / "casos" / slug(relatorio.pseudonimo_titular or relatorio.nome_titular or "caso")

if etapa == ETAPAS[0]:
    from creditos import interpretes as _interpretes
    from creditos import pipeline as _pipeline

    st.subheader("Buscar intérpretes")
    st.caption(
        "O app descobre quem gravou cada obra do relatório e devolve uma planilha só com isto: a obra e o intérprete. "
        "Mande a planilha para o compositor conferir e corrigir. A coleta de prints (etapa 2) vem depois."
    )
    obras_distintas = len({o.titulo for o in relatorio.obras})
    limite_de_obras = None
    if os.environ.get("QUEMCANTA_DEV"):  # só para quem desenvolve e demonstra o app
        quantas = st.number_input("Obras a buscar (só para teste)", min_value=0, max_value=obras_distintas,
                                  value=min(10, obras_distintas), step=5, help="0 = todas.")
        if quantas and quantas < obras_distintas:
            limite_de_obras, obras_distintas = int(quantas), int(quantas)
    st.info(
        f"**Tempo estimado: {_pipeline.texto_da_estimativa(_pipeline.estimar_minutos(obras_distintas, _interpretes.PLATAFORMAS))}** "
        f"para as {obras_distintas} obras. Nesta etapa nenhum navegador é aberto e nenhum print é tirado. O que já foi "
        "lido fica guardado: se parar no meio, continua de onde estava."
    )
    tarefa = andamento.atual()
    de_outra_etapa = tarefa is not None and tarefa.dados.get("tipo") != "interpretes"
    if tarefa is not None and not de_outra_etapa and not tarefa.viva:
        andamento.encerrar()
        if tarefa.erro is not None:
            st.error(f"A busca não terminou: {type(tarefa.erro).__name__}: {tarefa.erro}")
        elif tarefa.interrompida:
            st.warning("Busca interrompida. O que já foi lido ficou guardado: clique em **Buscar intérpretes** para continuar.")
        else:
            st.session_state.relatorio = relatorio = tarefa.dados["relatorio"]
            st.session_state.lista_de_interpretes = tarefa.resultado
        tarefa = None
    if de_outra_etapa:
        st.info("Há uma coleta de prints em andamento na etapa 2. Espere terminar, ou pare por lá, antes de buscar intérpretes.")
    elif tarefa is None and st.button("Buscar intérpretes", type="primary"):
        from creditos.classificador import Config

        nomes_do_relatorio = relatorio.nomes_artisticos()
        config = Config(nomes_confirmados=[n["nome"] for n in nomes_do_relatorio if n["titular"]])
        coautores = [n["nome"] for n in nomes_do_relatorio if not n["titular"]]
        tarefa = andamento.iniciar(
            lambda avisar: _interpretes.buscar(relatorio, config, pasta_do_caso, coautores if len(coautores) <= 10 else [],
                                              ao_avancar=avisar, limite=limite_de_obras),
            andamento.Andamento(obras_distintas, _interpretes.PLATAFORMAS, prints=False), tipo="interpretes", relatorio=relatorio,
        )
        tarefa.linha.join(0.3)
        if tarefa.viva:
            st.rerun()
        andamento.encerrar()
        if tarefa.erro is not None:
            st.error(f"A busca não terminou: {type(tarefa.erro).__name__}: {tarefa.erro}")
        elif not tarefa.interrompida:
            st.session_state.lista_de_interpretes = tarefa.resultado
        tarefa = None
    if tarefa is not None and not de_outra_etapa:
        painel_do_andamento("Busca", "Parar busca")
    lista = st.session_state.get("lista_de_interpretes")
    if lista:
        if limite_de_obras:
            lista = lista[:limite_de_obras]
        com = sum(bool(nomes) for _, nomes in lista)
        st.subheader("Resultado")
        st.download_button(
            "Planilha de intérpretes (.xlsx)", _interpretes.planilha(relatorio, lista),
            f"Intérpretes - {relatorio.pseudonimo_titular or relatorio.nome_titular}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary",
        )
        st.caption(
            f"{com} de {len(lista)} obras com intérprete encontrado. As outras {len(lista) - com} vão em branco, para o "
            "compositor preencher. O app não acha todos: a conferência do compositor faz parte do processo."
        )
        st.dataframe(pd.DataFrame([{"Obras": obra, "Intérpretes": nome.upper()} for obra, nomes in lista for nome in (nomes or [""])]),
                     hide_index=True)
    st.stop()

# --- conferir e ajustar (recolhido: quase nunca precisa mexer) ----------------

area_da_coleta = st.container()  # a coleta aparece aqui em cima, mas usa os ajustes definidos abaixo
st.divider()
st.subheader("Conferir e ajustar (opcional)")
st.caption("O app já usa tudo isto sozinho. Abra só se quiser conferir o que foi lido ou corrigir um nome.")
with st.expander(f"Nomes artísticos e intérpretes ({len(relatorio.nomes_artisticos())} pseudônimos no relatório)"):
    st.markdown(
        "As plataformas mostram o **nome artístico**, não o nome civil. Os pseudônimos abaixo vieram do próprio relatório "
        "e já entram como nomes pelos quais o titular e os coautores podem aparecer. **Desmarque** o que não for nome "
        "de pessoa usado em público (por exemplo, o nome de uma banda cadastrado como pseudônimo)."
    )
    nomes = relatorio.nomes_artisticos()
    if nomes:
        tabela = pd.DataFrame([
            {"usar": True, "nome artístico": n["nome"], "de quem": n["de"],
             "quem é": "titular" if n["titular"] else "coautor", "obras": n["obras"]}
            for n in nomes
        ])
        editada = st.data_editor(
            tabela, hide_index=True, disabled=["nome artístico", "de quem", "quem é", "obras"], key="nomes_do_relatorio",
            column_config={"usar": st.column_config.CheckboxColumn("usar", help="Desmarque para remover")},
        )
        usados = editada[editada["usar"]]
    else:
        st.caption("O relatório não traz pseudônimo nenhum.")
        usados = pd.DataFrame(columns=["nome artístico", "quem é"])
    extras = planilha.dividir_nomes_artisticos(st.text_input(
        "Outros nomes artísticos do titular (opcional)", placeholder="Ex.: Nome Artístico; Outro Nome",
        help="Nomes que o titular usa e que não estão no relatório. Separe com ponto e vírgula.",
    ))
    interpretes = planilha.dividir_nomes_artisticos(st.text_input(
        "Bandas e intérpretes que gravam o titular (opcional)", placeholder="Ex.: Nome da Banda; Nome do Cantor",
        key="interpretes_informados",
        help="Informados pelo compositor. Servem para ligar uma gravação ao titular quando a plataforma não mostra crédito.",
    ))
    do_titular = list(usados.loc[usados["quem é"] == "titular", "nome artístico"]) + extras
    st.session_state.config = {
        "nomes_confirmados": do_titular,
        "nomes_dos_coautores": list(usados.loc[usados["quem é"] == "coautor", "nome artístico"]),
        "interpretes": interpretes,
    }


grupos = relatorio.duplicidades()
with st.expander(f"Possíveis duplicidades ({len(grupos)})"):
    if grupos:
        st.markdown(
            "Obras com título igual ou parecido, ou que o próprio cadastro marca como duplicidade (DU) ou homônima (HO). "
            "Podem ser a mesma música cadastrada duas vezes. Na verificação, todos os registros de um título são "
            "considerados juntos, inclusive quando os coautores diferem."
        )
        st.dataframe(pd.DataFrame([
            {"grupo": i, "motivo": "título igual ou parecido" if g["motivo"] == "titulo" else "situação DU/HO no cadastro",
             "código": o.codigo, "título": o.titulo, "situação": o.situacao,
             "autores": "; ".join(a.pseudonimo or a.nome for a in o.autores)}
            for i, g in enumerate(grupos, start=1) for o in g["obras"]
        ]), hide_index=True)
    else:
        st.caption("Nenhuma.")


with st.expander(f"Obras lidas do relatório ({len(relatorio.obras)})"):
    contratuais = st.checkbox(
        "Mostrar dados contratuais (percentuais, CAE/IPI, editoras)",
        help="Esses dados ficam só neste computador: o filtro de saída impede que entrem em qualquer busca externa.",
    )
    linhas = []
    for o in relatorio.obras:
        linha = {
            "código": o.codigo, "ISWC": o.iswc, "título": o.titulo,
            "situação": " / ".join(f"{s} ({SITUACOES.get(s, '?')})" for s in o.situacoes) or o.situacao,
            "incluída em": o.inclusao,
            "autores": "; ".join(a.nome + (f" ({a.pseudonimo})" if a.pseudonimo and normalizar(a.pseudonimo) != normalizar(a.nome) else "") for a in o.autores),
        }
        if contratuais:
            linha["titulares e percentuais"] = "; ".join(
                f"{t.nome} [{t.categoria}] {'' if t.percentual is None else f'{t.percentual:g}%'} {t.cae}".strip() for t in o.titulares
            )
        linhas.append(linha)
    st.dataframe(pd.DataFrame(linhas), hide_index=True)
    st.download_button(
        "Baixar o relatório lido (JSON)",
        json.dumps({"relatorio": relatorio.para_dict(), "config": st.session_state.config}, ensure_ascii=False, indent=1),
        "relatorio_lido.json", "application/json",
        help="Contém os dados contratuais do relatório. Guarde só no escritório.",
    )

# --- coleta (aparece logo abaixo do resumo) -----------------------------------

with area_da_coleta:
    st.subheader("Verificar os créditos nas plataformas")
    st.caption(
        "O app descobre as gravações de cada obra, lê o compositor que cada plataforma mostra, compara com o relatório e "
        "tira o print das gravações sem crédito ou com crédito errado. Leva alguns minutos por plataforma; as que usam "
        "navegador vão devagar de propósito, para não serem bloqueadas. O que já foi lido fica guardado: se parar, é só "
        "coletar de novo."
    )
    st.caption(
        "Nomes usados nas buscas: **" + ", ".join(dict.fromkeys([relatorio.nome_titular, *do_titular])) + "**"
        + (f" + {len(st.session_state.config['nomes_dos_coautores'])} pseudônimos de coautores"
           if 0 < len(st.session_state.config["nomes_dos_coautores"]) <= 10 else "")
        + (f" + intérpretes informados: {', '.join(interpretes)}" if interpretes else "")
    )
    def confirmar_interpretes():
        """Passa os intérpretes marcados para o campo de intérpretes informados; a próxima coleta já os trata como confirmados."""
        atuais = planilha.dividir_nomes_artisticos(st.session_state.get("interpretes_informados", ""))
        st.session_state.interpretes_informados = "; ".join(atuais + st.session_state.get("presumidos_marcados", []))
        st.session_state.presumidos_marcados = []
        st.session_state.pop("coletas", None)
        st.session_state.pop("saidas", None)
        st.session_state.aviso_de_confirmacao = True

    if st.session_state.pop("aviso_de_confirmacao", False):
        st.success("Intérpretes confirmados. Clique em **Coletar e classificar** de novo: como tudo já foi lido, sai em instantes.")
    # A planilha de intérpretes é opcional: com ela, a coleta usa só os pares conferidos; sem ela, o app descobre sozinho.
    from creditos import interpretes as _interpretes

    conferida = st.file_uploader(
        "Planilha de intérpretes conferida pelo compositor (.xlsx, opcional)", type=["xlsx"], key="planilha_conferida",
        help="A planilha da etapa 1, depois que o compositor conferiu e corrigiu: a obra na primeira coluna e o intérprete "
        "na segunda. Com ela, o app verifica só esses intérpretes. Sem ela, ele descobre os intérpretes sozinho.",
    )
    if conferida is not None and st.session_state.get("conferida_id") != conferida.file_id:
        try:
            lidos, fora = _interpretes.ler_planilha(conferida.getvalue(), relatorio)
        except Exception as e:
            st.error(f"Não consegui ler a planilha de intérpretes: {e}")
        else:
            st.session_state.conferidos, st.session_state.conferidos_fora, st.session_state.conferida_id = lidos, fora, conferida.file_id
            st.session_state.pop("coletas", None)
            st.session_state.pop("saidas", None)
    conferidos = st.session_state.get("conferidos")
    if conferidos:
        sem = len({o.titulo for o in relatorio.obras}) - len(conferidos)
        st.success(
            f"Planilha lida: {sum(map(len, conferidos.values()))} intérpretes em {len(conferidos)} obras."
            + (f" As outras {sem} obras do relatório estão sem intérprete e não serão verificadas." if sem else "")
        )
        if st.session_state.get("conferidos_fora"):
            st.warning("Obras da planilha que não estão no relatório, e por isso ficam de fora: " + "; ".join(st.session_state.conferidos_fora))
    elif conferidos is not None:
        st.error("Nenhuma obra da planilha foi encontrada no relatório. Confira se a planilha é deste compositor.")
    else:
        st.caption(
            "Sem a planilha de intérpretes, o app descobre sozinho quem gravou cada obra. Com a planilha conferida pelo "
            "compositor (etapa 1), ele verifica só os intérpretes dela, e o resultado fica mais certeiro."
        )
    NOMES_DAS_PLATAFORMAS = {"deezer": "Deezer", "youtube": "YouTube Music", "spotify": "Spotify", "tidal": "Tidal",
                             "apple": "Apple Music (controle)", "vagalume": "Vagalume", "amazon": "Amazon Music (site)",
                             "amazon_app": "Amazon Music (aplicativo de desktop)"}
    from creditos import pipeline as _pipeline

    escolhidas = st.multiselect(
        "Plataformas", list(NOMES_DAS_PLATAFORMAS), default=[p for p in NOMES_DAS_PLATAFORMAS if p != "amazon_app"],
        format_func=NOMES_DAS_PLATAFORMAS.get,
        help="A Deezer roda sempre. O aplicativo de desktop da Amazon Music vem desmarcado: só funciona no Windows, com o "
        "aplicativo instalado e a conta do escritório já logada nele.",
    )
    if "amazon_app" in escolhidas:
        st.warning(
            "**Amazon Music (aplicativo de desktop):** é no aplicativo que a Amazon mostra o compositor. O app vai **fechar e "
            "abrir de novo o Amazon Music** para poder controlá-lo, e usa a conta que já estiver logada nele: não digita senha "
            "nem faz login. Não use o aplicativo enquanto a coleta roda."
        )
    mostrar_navegador = st.checkbox(
        "Mostrar a janela do navegador enquanto coleta", value=False,
        help="Desmarcado, o navegador que o app usa fica fora da tela e você pode trabalhar normalmente. Marque para "
        "acompanhar o robô (em uma demonstração, por exemplo); aí não clique dentro da janela, que um clique fecha o "
        "menu que o app abriu.",
    )
    tela_inteira = st.checkbox(
        "Print de tela inteira, com a barra de endereço e o relógio", value=False,
        help="O app fotografa o monitor inteiro onde a janela do navegador está, como num print feito à mão.",
    )
    if tela_inteira:
        st.warning(
            "Com esta opção, o navegador do app abre **visível**. Assim que ele abrir, **arraste a janela para um monitor "
            "que vai ficar livre, maximize-a** e trabalhe no outro. Não cubra nem minimize essa janela durante a coleta. "
            "Tudo o que estiver nesse monitor sai no print: feche ali o que não pode aparecer."
        )
    titulos_do_relatorio = len(conferidos) if conferidos else len({o.titulo for o in relatorio.obras})
    limite = None
    if os.environ.get("QUEMCANTA_DEV"):
        # Só para quem desenvolve e demonstra o app (variável QUEMCANTA_DEV). No pacote instalado isto não aparece:
        # quem envia um relatório quer todas as obras verificadas.
        dev = st.columns([1, 1, 3])
        quantas = dev[0].number_input("Obras a verificar (só para teste)", min_value=0, max_value=titulos_do_relatorio,
                                      value=min(10, titulos_do_relatorio), step=5, help="0 = todas.")
        do_fim = dev[1].radio("Quais", ["as primeiras", "as últimas"], horizontal=True) == "as últimas"
        if quantas and quantas < titulos_do_relatorio:
            limite = -int(quantas) if do_fim else int(quantas)
            titulos_do_relatorio = int(quantas)
    st.info(
        f"**Tempo estimado: {_pipeline.texto_da_estimativa(_pipeline.estimar_minutos(titulos_do_relatorio, escolhidas))}** "
        f"para as {titulos_do_relatorio} obras nas plataformas marcadas, se for a primeira coleta deste relatório. "
        "O computador pode ser usado normalmente enquanto isso, mas precisa ficar ligado. O que já foi lido fica "
        "guardado: se parar no meio ou coletar de novo, continua de onde estava."
    )

    def recolher(tarefa):
        """Pega o resultado da coleta que terminou (ou diz por que não terminou) e libera o botão de coletar."""
        andamento.encerrar()
        if tarefa.erro is not None:  # navegador que não abriu, rede fora do ar: mostra e deixa tentar de novo
            st.error(f"A coleta não terminou: {type(tarefa.erro).__name__}: {tarefa.erro}")
        elif tarefa.interrompida:
            st.warning(
                "Coleta interrompida. O que já foi lido ficou guardado: clique em **Coletar e classificar** para "
                "continuar de onde parou."
            )
        else:
            st.session_state.relatorio = tarefa.dados["relatorio"]
            st.session_state.coletas = tarefa.resultado
            st.session_state.pasta_do_caso = tarefa.dados["pasta"]
            st.session_state.pop("saidas", None)

    tarefa = andamento.atual()
    de_outra_etapa = tarefa is not None and tarefa.dados.get("tipo") == "interpretes"
    if de_outra_etapa:
        st.info("Há uma busca de intérpretes em andamento na etapa 1. Espere terminar, ou pare por lá, antes de coletar.")
        tarefa = None
    elif tarefa is not None and not tarefa.viva:
        recolher(tarefa)
        tarefa = None
    if tarefa is None and not de_outra_etapa and st.button("Coletar e classificar", type="primary"):
        from creditos import pipeline
        from creditos.classificador import Config

        config = Config(nomes_confirmados=do_titular, interpretes=interpretes)
        # Com muitos coautores, percorrer a discografia de cada um multiplicaria a coleta sem ajudar: os
        # intérpretes aparecem pela busca por título e pelos créditos que as plataformas mostram.
        coautores = st.session_state.config["nomes_dos_coautores"]
        # A coleta roda em outra linha de execução: o navegador automatizado não pode rodar na da tela.
        tarefa = andamento.iniciar(
            lambda avisar: pipeline.executar(
                relatorio, config, pasta_do_caso, coautores if len(coautores) <= 10 else [], ao_avancar=avisar,
                limite_youtube=limite, plataformas=tuple(escolhidas), mostrar_navegador=mostrar_navegador,
                tela_inteira=tela_inteira, conferidos=conferidos or None,
            ),
            andamento.Andamento(titulos_do_relatorio, escolhidas), tipo="coleta", relatorio=relatorio, pasta=str(pasta_do_caso),
            conferidos=conferidos,
        )
        tarefa.linha.join(0.3)  # o que já estava todo lido termina na hora, sem passar pela barra
        if tarefa.viva:
            st.rerun()  # refaz a tela já sem o botão de coletar, só com a barra
        recolher(tarefa)
        tarefa = None
    if tarefa is not None:
        painel_do_andamento()

    coletas = st.session_state.get("coletas")
    if coletas:
        from creditos import provas, saida

        # O que parou no meio aparece em destaque; o resto dos avisos fica recolhido no fim.
        for coleta in coletas:
            if coleta.interrompida:
                st.error(f"{coleta.plataforma}: {coleta.avisos[-1]}")
        parciais = [a for coleta in coletas for a in coleta.avisos if a.startswith("COLETA PARCIAL")]
        if parciais:
            st.caption("Coleta parcial: " + parciais[0].split(": ", 1)[1].rsplit(" foram buscadas", 1)[0] + " foram verificadas.")
        if "saidas" not in st.session_state:
            planilha_pronta = saida.planilha(relatorio, coletas)
            pacote, resumo, avisos = provas.pacote(relatorio, coletas, st.session_state.pasta_do_caso, planilha_pronta)
            st.session_state.saidas = {"planilha": planilha_pronta, "pacote": pacote, "resumo": resumo, "avisos": avisos}
        saidas = st.session_state.saidas
        nome_do_caso = relatorio.pseudonimo_titular or relatorio.nome_titular
        st.subheader("Resultado")
        botoes = st.columns(3)
        botoes[0].download_button(
            "Planilha de obras (.xlsx)", saidas["planilha"], f"Planilha de Obras - {nome_do_caso}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary",
        )
        botoes[1].download_button(
            "Listas da petição e provas (.zip)", saidas["pacote"], f"Provas - {nome_do_caso}.zip", "application/zip",
            help="Para cada plataforma com gravação sem crédito ou com crédito errado: a lista da petição em Word e o PDF "
            "com um print por página. A planilha também vai dentro.",
        )
        lidas = {provas.NOMES.get(k.plataforma, k.plataforma): sum(g.classificacao.status != "REVISAR - possível obra homônima de terceiro" for g in k.gravacoes) for k in coletas}
        st.dataframe(
            pd.DataFrame([
                {"Plataforma": r["plataforma"], "Gravações lidas": lidas.get(r["plataforma"], 0),
                 "Sem crédito ou crédito errado": r["firmes"], "A confirmar (fora da contagem)": r["a_confirmar"]}
                for r in saidas["resumo"]
            ]),
            hide_index=True,
        )
        ja_informados = {normalizar(n) for n in interpretes}
        presumidos = [i for i in {i["nome"]: i for coleta in coletas for i in coleta.interpretes_inferidos}.values()
                      if normalizar(i["nome"]) not in ja_informados]
        if presumidos:
            with st.container(border=True):
                st.markdown(
                    "**Estes artistas gravam o compositor?** O app notou que eles gravam várias obras do relatório, mas isso "
                    "é só um indício: um artista conhecido grava músicas de muitos autores, inclusive com o mesmo título. "
                    "Enquanto ninguém confirmar, as gravações deles sem crédito ficam em **A confirmar**, fora da contagem. "
                    "Marque só quem o compositor ou o escritório sabe que grava as obras dele."
                )
                st.multiselect(
                    "Intérpretes a confirmar", [i["nome"] for i in presumidos], key="presumidos_marcados",
                    format_func=lambda nome: next(f"{nome} ({i['obras']} títulos do relatório)" for i in presumidos if i["nome"] == nome),
                    placeholder="Marque os que de fato gravam o compositor",
                )
                st.button("Confirmar os marcados", on_click=confirmar_interpretes)
        tecnicos = [f"{coleta.plataforma}: {a}" for coleta in coletas for a in coleta.avisos
                    if not a.startswith("COLETA PARCIAL") and not coleta.interrompida] + [f"Provas: {a}" for a in saidas["avisos"]]
        erros = [f"{coleta.plataforma}: não foi possível ler {link} ({erro})" for coleta in coletas for link, erro in coleta.albuns_com_erro]
        with st.expander(f"Avisos e detalhes da execução ({len(tecnicos) + len(erros)} avisos)"):
            for texto in tecnicos + erros:
                st.warning(texto)
            st.dataframe(
                pd.DataFrame([(r, str(v)) for r, v in saida.contagens(relatorio, coletas)], columns=["", "valor"]), hide_index=True
            )
            st.caption(
                f"Os prints, com o HTML e a ficha de cada um, ficam em `{Path(st.session_state.pasta_do_caso).resolve()}`, uma "
                "pasta por plataforma. São prova documental unilateral: não substituem ata notarial."
            )


st.caption(f"Versão {VERSAO} · casos e prints em `{PASTA_DE_DADOS}`")
