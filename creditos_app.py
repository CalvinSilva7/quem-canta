"""Créditos nas plataformas: a tela, com duas abas (buscar intérpretes e coleta de prints).

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
from creditos import andamento, atualizacao, ecad, resultado, ubc
from creditos.versao import VERSAO
from creditos.captura import slug
from creditos.modelo import SITUACOES, Relatorio

# Onde ficam os casos (o que já foi lido e os prints): fora da pasta do programa, para uma atualização nunca tocar neles.
PASTA_DE_DADOS = Path(os.environ.get("QUEMCANTA_DADOS") or Path.home() / "Documents" / "Quem Canta")

ETAPAS = ["Buscar intérpretes", "Coleta de prints", "Réplica (ECAD)", "Réplica (ISWC)", "Tabela de obras"]  # as abas do app


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


def abrir_pasta(pasta):
    """Abre a pasta no Explorador de Arquivos (o app roda no próprio computador de quem usa)."""
    import subprocess
    import sys

    pasta = str(Path(pasta).resolve())
    if hasattr(os, "startfile"):
        os.startfile(pasta)  # Windows
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", pasta])


def guardar_o_salvo(tarefa, chave):
    """Leva para a tela o que a tarefa gravou no disco (ou por que não gravou), e registra o erro, se houve."""
    st.session_state[chave] = {"salvo": tarefa.andamento.salvo, "erro": tarefa.andamento.erro_ao_salvar}
    if tarefa.erro is not None:
        registrar_erro(tarefa)


def registrar_erro(tarefa):
    """Acrescenta o erro, com o traceback, ao erros.log da pasta de dados: é o que mostra em que linha quebrou."""
    from datetime import datetime

    try:
        PASTA_DE_DADOS.mkdir(parents=True, exist_ok=True)
        with open(PASTA_DE_DADOS / "erros.log", "a", encoding="utf-8") as registro:
            registro.write(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} · versão {VERSAO} · {tarefa.dados.get('tipo', '')}\n"
                           f"{tarefa.detalhe_do_erro or repr(tarefa.erro)}\n")
    except OSError:
        pass


def mostrar_o_salvo(chave):
    """Onde ficou o resultado gravado pela própria tarefa, com o botão de abrir a pasta."""
    estado = st.session_state.get(chave)
    if not estado:
        return
    salvo = estado["salvo"]
    if salvo:
        pasta = Path(salvo["arquivos"][0]).parent
        nomes = " e ".join(f"**{Path(a).name}**" for a in salvo["arquivos"])
        texto = (f"Resultado **parcial** (só as plataformas que terminaram) salvo em `{pasta}`: {nomes}." if salvo["parcial"]
                 else f"Resultado salvo em `{pasta}`: {nomes}. Não precisa clicar em baixar.")
        (st.info if salvo["parcial"] else st.success)(texto)
        if st.button("Abrir pasta", key=f"{chave}_abrir"):
            try:
                abrir_pasta(pasta)
            except Exception as e:
                st.warning(f"Não consegui abrir a pasta ({e}). Ela fica em `{pasta}`.")
    if estado["erro"]:
        st.warning(f"O resultado não foi salvo sozinho no disco ({estado['erro']}). Use os botões de baixar.")


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
# As duas abas ficam no topo, antes de qualquer coisa: uma só busca intérpretes, a outra faz a coleta de prints.
em_curso = andamento.atual()
if em_curso is not None and "etapa" not in st.session_state:  # página recarregada: volta para a aba que está rodando
    st.session_state.etapa = ETAPAS[0] if em_curso.dados.get("tipo") == "interpretes" else ETAPAS[1]
st.session_state.setdefault("etapa", ETAPAS[0])
etapa = st.segmented_control("Aba", ETAPAS, key="etapa", label_visibility="collapsed") or ETAPAS[0]
st.caption(
    "Envie o relatório do compositor e receba a planilha de intérpretes: a obra e quem gravou, para ele conferir e corrigir. "
    "O arquivo é lido só neste computador; o app nunca acessa o ECADNET nem o portal de associação nenhuma."
    if etapa == ETAPAS[0] else
    "Do relatório do titular à planilha de obras, à lista da petição e às provas. O arquivo é lido só neste computador; "
    "o app nunca acessa o ECADNET nem o portal de associação nenhuma."
    if etapa == ETAPAS[1] else
    "Os prints do Ecadnet para a réplica. O relatório é lido só neste computador. O Ecadnet abre numa janela em que você "
    "faz o login; o app só abre a obra e fotografa quando você pede."
    if etapa == ETAPAS[2] else
    "Os prints do ISWC Network para a réplica. O relatório é lido só neste computador. Quem busca no ISWC é você; o app "
    "abre a página, copia o ISWC e fotografa quando você pede."
    if etapa == ETAPAS[3] else
    "A tabela de obras: o relatório analítico, o ISWC e o Ecadnet de cada obra, lado a lado, no modelo do escritório. "
    "Tudo é lido só neste computador; o app não acessa site nenhum nesta aba."
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
    for chave in ("conferidos", "conferidos_fora", "conferida_id", "lista_de_interpretes", "coletas", "saidas",
                  "salvo_interpretes", "salvo_coleta"):
        st.session_state.pop(chave, None)  # relatório novo: o que era do anterior não vale mais

em_curso = andamento.atual()
if em_curso is not None and st.session_state.get("relatorio") is None:
    # A página foi recarregada no meio de uma coleta, ou depois que ela terminou sem ninguém olhando: a tela volta a
    # acompanhá-la, ou pega o resultado que ficou esperando.
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

# --- a aba de intérpretes ------------------------------------------------------

pasta_do_caso = PASTA_DE_DADOS / "casos" / slug(relatorio.pseudonimo_titular or relatorio.nome_titular or "caso")

if etapa == ETAPAS[0]:
    from creditos import interpretes as _interpretes
    from creditos import pipeline as _pipeline

    st.subheader("Buscar intérpretes")
    st.caption(
        "O app descobre quem gravou cada obra do relatório e devolve uma planilha só com isto: a obra e o intérprete. "
        "Mande a planilha para o compositor conferir e corrigir. A coleta de prints fica na outra aba."
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
    def recolher_busca(tarefa):
        """Pega a lista da busca que terminou (ou diz por que não terminou) e libera o botão de buscar."""
        andamento.encerrar()
        guardar_o_salvo(tarefa, "salvo_interpretes")
        if tarefa.erro is not None:
            st.error(f"A busca não terminou: {type(tarefa.erro).__name__}: {tarefa.erro}")
        elif tarefa.interrompida:
            st.warning("Busca interrompida. O que já foi lido ficou guardado: clique em **Buscar intérpretes** para continuar.")
        else:
            st.session_state.relatorio = tarefa.dados["relatorio"]
            st.session_state.lista_de_interpretes = tarefa.resultado

    tarefa = andamento.atual()
    de_outra_etapa = tarefa is not None and tarefa.dados.get("tipo") != "interpretes"
    if tarefa is not None and not de_outra_etapa and not tarefa.viva:
        recolher_busca(tarefa)
        relatorio = st.session_state.relatorio
        tarefa = None
    if de_outra_etapa and not tarefa.viva:
        st.info("A coleta de prints terminou: abra a aba **Coleta de prints** para ver o resultado.")
    elif de_outra_etapa:
        st.info("Há uma coleta de prints em andamento na outra aba. Espere terminar, ou pare por lá, antes de buscar intérpretes.")
    elif tarefa is None and st.button("Buscar intérpretes", type="primary"):
        from creditos.classificador import Config

        nomes_do_relatorio = relatorio.nomes_artisticos()
        config = Config(nomes_confirmados=[n["nome"] for n in nomes_do_relatorio if n["titular"]])
        coautores = [n["nome"] for n in nomes_do_relatorio if not n["titular"]]
        st.session_state.pop("salvo_interpretes", None)

        def buscar(avisar, relatorio=relatorio, pasta=pasta_do_caso):
            # A própria busca grava a planilha no disco ao terminar, ou a parcial se parar: não depende da tela.
            return resultado.rodar_e_salvar(
                avisar,
                lambda: _interpretes.buscar(relatorio, config, pasta, coautores if len(coautores) <= 10 else [],
                                            ao_avancar=avisar, limite=limite_de_obras),
                lambda lista, parcial: resultado.salvar_interpretes(relatorio, lista, pasta, parcial),
                lambda coletas: _interpretes.por_obra(relatorio, coletas),
            )

        tarefa = andamento.iniciar(
            buscar, andamento.Andamento(obras_distintas, _interpretes.PLATAFORMAS, prints=False), tipo="interpretes",
            relatorio=relatorio,
        )
        tarefa.linha.join(0.3)
        if tarefa.viva:
            st.rerun()
        recolher_busca(tarefa)
        tarefa = None
    if tarefa is not None and not de_outra_etapa:
        painel_do_andamento("Busca", "Parar busca")
    mostrar_o_salvo("salvo_interpretes")
    lista = st.session_state.get("lista_de_interpretes")
    if lista:
        if limite_de_obras:
            lista = lista[:limite_de_obras]
        com = sum(bool(nomes) for _, nomes, *_ in lista)
        em_duvida = sum(len(resto[0]) for _, _, *resto in lista if resto)
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
        if em_duvida:
            st.markdown(
                f"**Em preto**, o intérprete de que o app tem certeza. :red[**Em vermelho**], os {em_duvida} que ele só supõe: "
                "o compositor confirma ou apaga."
            )
        tabela = pd.DataFrame([{"Obras": obra, "Intérpretes": nome.upper(), "_duvida": bool(resto and nome in resto[0])}
                               for obra, nomes, *resto in lista for nome in (nomes or [""])])
        st.dataframe(
            tabela.style.apply(lambda linha: ["", f"color: #{_interpretes.VERMELHO}" if linha["_duvida"] else "", ""], axis=1),
            hide_index=True, column_order=["Obras", "Intérpretes"],
        )
    st.stop()

# --- as abas da réplica: Ecadnet e ISWC assistidos (em teste) ------------------


def aba_da_replica(site: str):
    """A aba de um site da réplica: a janela do site, a obra da vez e os botões de abrir e fotografar.

    `site` é "ecadnet" ou "iswc". Os dois funcionam igual: a pessoa conduz e o app fotografa quando ela pede. No
    Ecadnet o app abre a página da obra pelo código do relatório; no ISWC (os termos do site só permitem consulta
    manual) o app abre a busca e deixa o ISWC pronto para colar, e quem busca é a pessoa.
    """
    from creditos import ecadnet as _ecadnet
    from creditos import iswc as _iswc
    from creditos import replica as _replica

    do_ecadnet = site == "ecadnet"
    nome_do_site = "Ecadnet" if do_ecadnet else "ISWC Network"
    pasta = pasta_do_caso / ("ecadnet" if do_ecadnet else "iswc")
    etapa_do_print = _ecadnet.ETAPA if do_ecadnet else _iswc.ETAPA
    st.subheader(f"Prints do {nome_do_site} (em teste)")
    st.caption(
        "Você conduz e o app fotografa. Abra a janela, faça o login no Ecadnet nela (o app não digita nem guarda senha) e, "
        "abra a primeira obra. Conferida a página, clique em **Fotografar** (quantas vezes precisar, rolando a página "
        "entre um print e outro) e depois em **Próxima obra →**, que já abre a seguinte. Se aparecer o \"não sou um "
        "robô\", resolva na própria janela."
        if do_ecadnet else
        "Você conduz e o app salva. O \"não sou um robô\" do ISWC Network recusa o navegador do app, por isso o ISWC "
        "abre no **Google Chrome**, com um perfil só para ele. Clique em **Abrir no ISWC**: o Chrome abre no resultado "
        "daquele ISWC (na primeira vez, aceite os termos e resolva o \"não sou um robô\" lá). Com o resultado na tela, "
        "clique em **Salvar PDF** aqui: o app confere se é o resultado deste ISWC e salva a página em PDF, igual ao "
        "\"Imprimir > Salvar como PDF\" do Chrome. **Próxima obra →** abre a seguinte na mesma janela. No fim, "
        "**Juntar os PDFs** gera um PDF só."
    )
    # A réplica só precisa das obras da petição (as sem crédito). A lista da petição diz quais são; o relatório dá o
    # código (o endereço no Ecadnet) e o ISWC de cada uma. A lista vale para as duas abas da réplica.
    listas = st.file_uploader("Lista(s) da petição (.docx): só as obras dela entram na fila", type=["docx"],
                              accept_multiple_files=True, key=f"{site}_listas",
                              help="A lista que a coleta gera para cada plataforma. Pode enviar várias: o app junta as obras de todas. "
                              "A parte \"A confirmar antes de usar\" fica de fora.")
    if listas:
        assinatura = sorted((f.name, f.size) for f in listas)
        if st.session_state.get("replica_listas_lidas") != assinatura:
            titulos = []
            for arquivo in listas:
                try:
                    titulos += _replica.titulos_da_lista(arquivo.getvalue())
                except Exception as e:
                    st.error(f"Não consegui ler {arquivo.name}: {e}")
            st.session_state.replica_titulos = list(dict.fromkeys(titulos))
            st.session_state.replica_listas_lidas = assinatura
            st.session_state.replica_nomes_das_listas = [f.name for f in listas]
            for chave_da_aba in ("ecadnet_indice", "iswc_indice"):
                st.session_state[chave_da_aba] = 0
    titulos_da_peticao = st.session_state.get("replica_titulos")
    if titulos_da_peticao == []:
        st.error("Não achei nenhuma obra na(s) lista(s). O app lê a lista que ele mesmo gera (\"1-Música: TÍTULO\") "
                 "ou uma tabela do Word com o número na primeira coluna e o título na segunda.")
    elif titulos_da_peticao is not None:
        obras, fora_do_relatorio, aproximados = _replica.cruzar(titulos_da_peticao, relatorio)
        st.success(f"Lista da petição: {len(titulos_da_peticao)} títulos ({', '.join(st.session_state.replica_nomes_das_listas)}) → "
                   f"{len(obras)} obras do relatório" + (" (há títulos com mais de um registro no relatório: entram todos)"
                                                          if len(obras) > len(titulos_da_peticao) else ""))
        if aproximados:
            st.info(f"{len(aproximados)} títulos entraram com o nome escrito de outro jeito no relatório (confira na hora do print): "
                    + "; ".join(f"{lista} → {' / '.join(rel)}" for lista, rel in aproximados.items()))
        if fora_do_relatorio:
            st.warning(f"{len(fora_do_relatorio)} títulos da lista não estão neste relatório: " + "; ".join(fora_do_relatorio))
        if st.button("Esquecer a lista e usar todas as obras do relatório", key=f"{site}_esquecer"):
            for nome in ("replica_titulos", "replica_listas_lidas", "replica_nomes_das_listas"):
                st.session_state.pop(nome, None)
            st.rerun()
    else:
        obras = relatorio.obras
        st.info("Sem lista da petição, a fila tem **todas** as obras do relatório. Envie a lista para fotografar só as que entram na petição.")
    obras = list({o.codigo or o.titulo: o for o in obras}.values())
    if do_ecadnet:
        obras = [o for o in obras if o.codigo]
        if not obras:
            st.info("Este relatório não traz o código das obras (CÓD. OBRA): sem ele, o app não sabe o endereço de cada obra no Ecadnet.")
            return
    else:
        sem_iswc = [o for o in obras if not o.iswc]
        obras = [o for o in obras if o.iswc]
        if sem_iswc:
            st.caption(f"{len(sem_iswc)} obras do relatório não têm ISWC e ficam de fora desta lista: "
                       + "; ".join(o.titulo for o in sem_iswc[:30]) + ("…" if len(sem_iswc) > 30 else ""))
        if not obras:
            st.info("Nenhuma obra deste relatório tem ISWC.")
            return

    aberta = _replica.janela(site)
    colunas = st.columns([2, 2, 3])
    if not do_ecadnet and _iswc.chrome_aberto():
        colunas[2].caption("Chrome do ISWC: aberto")
        if colunas[0].button("Fechar o Chrome do ISWC"):
            _iswc.fechar_chrome()
            st.rerun()
    if do_ecadnet:
        # O print da réplica é sempre o do monitor inteiro: precisa mostrar a data e a hora do relógio do Windows
        if aberta is not None:
            aberta.tela_inteira = True
        if aberta is None:
            if colunas[0].button(f"Abrir janela do {nome_do_site}", type="primary"):
                _replica.abrir_janela(site, perfil=_ecadnet.perfil_do_ecadnet(pasta_do_caso), pasta=pasta, inicio=_ecadnet.SITE,
                                      plataforma=_ecadnet.PLATAFORMA, etapa=_ecadnet.ETAPA, tela_inteira=True)
                st.rerun()
        else:
            colunas[2].caption(f"Janela: {aberta.situacao}")
            if colunas[0].button(f"Fechar a janela do {nome_do_site}"):
                aberta.pedir("fechar", espera=20)
                st.rerun()
        erro_da_janela = st.container()  # lugar fixo, pelo mesmo motivo dos avisos abaixo
        if _replica.JANELAS.get(site) is not None and _replica.JANELAS[site].erro:
            erro_da_janela.error(f"A janela do {nome_do_site} não abriu: {_replica.JANELAS[site].erro}")

    chave = f"{site}_indice"
    st.session_state.setdefault(chave, 0)
    indice = min(st.session_state[chave], len(obras) - 1)
    if do_ecadnet:
        rotulo = lambda i: f"{i + 1}. {obras[i].titulo} (cód. {obras[i].codigo})"
    else:
        rotulo = lambda i: f"{i + 1}. {obras[i].titulo} ({obras[i].iswc})"
    indice = st.selectbox(f"Obra ({len(obras)} na lista)", range(len(obras)), index=indice, format_func=rotulo)
    st.session_state[chave] = indice
    obra = obras[indice]
    if do_ecadnet:
        st.caption(f"Endereço: {_ecadnet.endereco_da_obra(obra.codigo)}")
        conferir = lambda url, texto, obra=obra: _ecadnet.conferir_pagina(url, texto, obra.codigo, obra.titulo)
        exibido = {"codigo_da_obra": obra.codigo}
    else:
        st.code(obra.iswc, language=None)
        conferir = lambda url, texto, obra=obra: _iswc.conferir_resultado(url, texto, obra.iswc)
        exibido = {"iswc": obra.iswc}
    nomes = [n for n in dict.fromkeys([relatorio.nome_titular, relatorio.pseudonimo_titular]) if n]
    # O que aconteceu no clique anterior, num lugar fixo: com ou sem recado, os botões ficam na mesma posição (se ela
    # muda, a tela mostra os botões duplicados enquanto o clique seguinte roda)
    avisos = st.container()
    for tipo, texto in st.session_state.pop(f"{site}_recado", []):
        getattr(avisos, tipo)(texto)
    ultima = indice >= len(obras) - 1
    botoes = st.columns(3)
    abrir = botoes[0].button("Abrir esta obra" if do_ecadnet else "Abrir no ISWC", disabled=do_ecadnet and aberta is None)
    fotografar = botoes[1].button("Fotografar" if do_ecadnet else "Salvar PDF", type="primary", disabled=do_ecadnet and aberta is None)
    if botoes[2].button("Próxima obra →", disabled=ultima):
        st.session_state[chave] = indice + 1
        if not do_ecadnet:
            # ISWC: abre o resultado da obra seguinte no Chrome do ISWC, na mesma aba
            seguinte = obras[indice + 1]
            _iswc.copiar(seguinte.iswc)
            try:
                onde = _iswc.abrir_no_chrome(_iswc.endereco_da_busca(seguinte.iswc), _iswc.perfil_do_chrome(pasta_do_caso))
                st.session_state[f"{site}_recado"] = [("info", f"Aberta: **{seguinte.titulo}** ({seguinte.iswc}) no {onde}. "
                                                               "Com o resultado na tela, clique em **Salvar PDF**.")]
            except Exception as e:
                st.session_state[f"{site}_recado"] = [("warning", f"{seguinte.titulo} não abriu: {e}. Clique em **Abrir no ISWC**.")]
        elif aberta is not None:
            # Ecadnet: passa para a obra seguinte e já abre a página dela (o print fica no botão Fotografar, que pode
            # ser usado mais de uma vez na mesma obra)
            seguinte = obras[indice + 1]
            with st.spinner(f"Abrindo a próxima obra: {seguinte.titulo}…"):
                aberta_a_seguinte = aberta.pedir("abrir", url=_ecadnet.endereco_da_obra(seguinte.codigo),
                                                 conferir=lambda url, texto: _ecadnet.conferir_pagina(url, texto, seguinte.codigo, seguinte.titulo))
            if aberta_a_seguinte.get("ok"):
                st.session_state[f"{site}_recado"] = [("info", f"Aberta: **{seguinte.titulo}**. Confira na janela e clique em **Fotografar**.")]
            else:
                st.session_state[f"{site}_recado"] = [("warning", f"{seguinte.titulo} não abriu: {aberta_a_seguinte.get('motivo')}. "
                                                                  "Clique em **Abrir esta obra** para tentar de novo.")]
        st.rerun()
    if abrir:
        if do_ecadnet:
            with st.spinner("Abrindo a obra no Ecadnet…"):
                resposta = aberta.pedir("abrir", url=_ecadnet.endereco_da_obra(obra.codigo), conferir=conferir)
            if resposta.get("ok"):
                st.success("Página da obra aberta. Confira na janela e clique em **Fotografar**.")
            else:
                st.warning(f"Não deu para abrir a obra: {resposta.get('motivo')}")
        else:
            _iswc.copiar(obra.iswc)  # se o site pedir a busca de novo, é só colar
            try:
                onde = _iswc.abrir_no_chrome(_iswc.endereco_da_busca(obra.iswc), _iswc.perfil_do_chrome(pasta_do_caso))
                st.success(f"Resultado do ISWC aberto no {onde}. Se o site pedir o \"não sou um robô\", resolva lá (o ISWC "
                           "também ficou copiado, se precisar colar). Com o resultado na tela, clique em **Salvar PDF**.")
            except Exception as e:
                st.warning(f"Não deu para abrir o ISWC: {e}")
    if fotografar and not do_ecadnet:
        with st.spinner("Conferindo o resultado e salvando o PDF…"):
            try:
                resposta = _iswc.salvar_pdf(pasta, obra.titulo, obra.iswc, nomes)
            except Exception as e:
                resposta = {"ok": False, "motivo": f"o Chrome do ISWC não respondeu ({type(e).__name__}): feche-o e clique em **Abrir no ISWC**"}
    elif fotografar:
        with st.spinner("Fotografando…"):
            resposta = aberta.pedir("fotografar", obra=obra.titulo, conferir=conferir, nomes=nomes, exibido=exibido)
    if fotografar:
        recado = []
        if resposta.get("ok"):
            ficha = resposta["ficha"]
            recado.append(("success", f"{obra.titulo}: {'print' if do_ecadnet else 'PDF'} guardado (`{next(iter(ficha['arquivos'].values()))}`)"
                           + (f" (monitor {ficha['monitor']})" if ficha.get("monitor") else "")
                           + ("" if resposta.get("titular_na_pagina") or resposta.get("aviso")
                              else " — atenção: o nome do titular não foi achado no texto da página")))
            if resposta.get("aviso"):
                recado.append(("warning", "Este print registra que " + resposta["aviso"].split(". Se quiser")[0] + "."))
            if ficha.get("tela_inteira", {}).get("erro"):
                recado.append(("warning", f"Print de tela inteira não saiu: {ficha['tela_inteira']['erro']}"))
            if do_ecadnet:
                recado.append(("info", "Precisa de outro print desta obra? Role a página na janela e clique em **Fotografar** de novo. "
                                       + ("Esta é a última obra da lista." if ultima else "Senão, clique em **Próxima obra →**.")))
        else:
            recado.append(("warning", f"{'Não fotografei' if do_ecadnet else 'Não guardei'}: {resposta.get('motivo')}"))
        for tipo, texto in recado:
            getattr(st, tipo)(texto)
    if do_ecadnet:
        # Busca alternativa (pedido do jurídico, 20/10/2026): só para a obra que não aparece no Ecadnet. Uma por associação,
        # todas iguais na tela (ver creditos/consulta_web.py)
        from creditos import socinpro as _socinpro
        from creditos import ubc_web as _ubc_web

        for _assoc in (_socinpro, _ubc_web):
            sigla = _assoc.CONSULTA.plataforma
            chave_assoc = sigla.lower()
            with st.expander(f"Não achou no Ecadnet? Busca alternativa na {sigla}", expanded=bool(st.session_state.get(f"{chave_assoc}_usada"))):
                st.markdown(f"1. **Buscar na {sigla}**: o app abre a consulta no Chrome e preenche o título. "
                            f"2. Na janela da {sigla}, {_assoc.CONSULTA.como_resolver}: o app acha a obra pelo código ECAD "
                            f"({obra.codigo}) e abre os detalhes. 3. **Fotografar na {sigla}**: o print vai para a lista e o PDF "
                            f"da {sigla}, separados dos do Ecadnet.")
                alternativa = st.columns(2)
                buscar = alternativa[0].button(f"Buscar na {sigla}", key=f"{chave_assoc}_buscar")
                fotografar_aqui = alternativa[1].button(f"Fotografar na {sigla}", type="primary", key=f"{chave_assoc}_fotografar")
                if buscar:
                    st.session_state[f"{chave_assoc}_usada"] = True
                    _iswc.copiar(_socinpro.titulo_para_buscar(obra.titulo))  # se precisar pesquisar de novo à mão, é só colar
                    # Pesquisa desta obra já em andamento (verificação ou resultados na tela): não pesquisa de novo, só
                    # espera e abre a obra. Os resultados da obra anterior podem continuar na tela: esses não servem
                    na_tela = (_assoc.estado() if _assoc.chrome_aberto() else None) or {}
                    ja_aberta = na_tela.get("detalhes") and na_tela.get("campos", {}).get(_assoc.CONSULTA.campo_do_codigo) == obra.codigo
                    em_andamento = _assoc.pesquisa_desta_obra(na_tela, obra.titulo) and (na_tela.get("verificacao") or na_tela.get("linhas"))
                    pesquisa = {"ok": True}
                    if not ja_aberta and not em_andamento:
                        with st.spinner(f"Abrindo a consulta da {sigla} e preenchendo o título…"):
                            pesquisa = _assoc.pesquisar(obra.titulo, _assoc.perfil_do_chrome(pasta_do_caso))
                    if not pesquisa["ok"]:
                        st.warning(f"Não consegui pesquisar na {sigla}: {pesquisa['motivo']}. O título ficou copiado para colar na janela.")
                    elif ja_aberta:
                        st.success(f"Os detalhes desta obra já estão abertos na {sigla}. Clique em **Fotografar na {sigla}**.")
                    else:
                        with st.spinner(f"Na janela da {sigla}, {_assoc.CONSULTA.como_resolver}. Esperando para abrir a obra…"):
                            aberta_la = _assoc.esperar_e_abrir(obra.codigo)
                        if aberta_la["ok"]:
                            st.success(f"Detalhes da obra abertos na {sigla}. Confira na janela e clique em **Fotografar na {sigla}**.")
                        else:
                            codigos = aberta_la.get("codigos")
                            st.warning(f"{aberta_la['motivo']}."
                                       + (f" Códigos ECAD nos resultados: {', '.join(c or '—' for c in codigos[:20])}. Se a obra for uma "
                                          "delas com outro código, abra os detalhes na janela e clique em Fotografar." if codigos else ""))
                if fotografar_aqui:
                    with st.spinner(f"Fotografando a {sigla}…"):
                        foto_la = _assoc.fotografar(pasta, obra.titulo, obra.codigo, nomes)
                    if foto_la["ok"]:
                        st.success(f"{obra.titulo}: print da {sigla} guardado (`{foto_la['ficha']['arquivos']['tela']}`). "
                                   f"Ele vai para a lista e o PDF da {sigla}, logo abaixo.")
                        if not foto_la["titular_na_pagina"]:
                            st.warning("Atenção: o nome do titular não foi achado nos detalhes da obra.")
                    else:
                        st.warning(f"Não fotografei: {foto_la['motivo']}")
    guardados = _replica.prints_guardados(pasta, etapa_do_print)
    identificacao = "código" if do_ecadnet else "ISWC"
    campo = "codigo_da_obra" if do_ecadnet else "iswc"
    chave_da_obra = lambda f: f.get("exibido", {}).get(campo)
    ordem = {(o.codigo if do_ecadnet else o.iswc): i for i, o in enumerate(obras)}
    titular = relatorio.pseudonimo_titular or relatorio.nome_titular or "titular"

    def lista_e_pdf(fichas, nome, prefixo, arquivo, juntar, rotulo_do_botao, nome_do_pdf):
        """Uma lista de prints (na ordem do PDF, com ↑ ↓ ✕) e o botão que junta só eles num PDF. `nome` já com o
        artigo ("do Ecadnet", "da Socinpro").
        Ecadnet e Socinpro têm uma cada (pedido do dono, 20/10/2026: os dois não se misturam no mesmo PDF)."""
        validos = [f for f in fichas if f.get("arquivos", {}).get(arquivo) and (pasta / f["arquivos"][arquivo]).exists()]
        if do_ecadnet:
            # só os prints do monitor inteiro (os que mostram a data e a hora); os antigos, só da página, ficam de fora.
            # De cada obra, os prints da última rodada (às vezes são dois ou três seguidos)
            escolhidos = _replica.prints_da_ultima_rodada(validos, chave_da_obra)
        else:
            # uma página por obra: se a obra foi salva mais de uma vez, vale a mais recente
            mais_recente = {}
            for f in sorted(validos, key=lambda f: f.get("capturado_em", "")):
                mais_recente[chave_da_obra(f)] = f
            escolhidos = list(mais_recente.values())
        escolhidos.sort(key=lambda f: ordem.get(chave_da_obra(f), len(obras)))  # sort estável: na mesma obra, na ordem tirada
        escolhidos = _replica.em_ordem(escolhidos, pasta)  # a ordem que a pessoa escolheu nos botões vale por cima
        fora_da_fila = [f for f in escolhidos if chave_da_obra(f) not in ordem]
        no_pdf = [f for f in escolhidos if chave_da_obra(f) in ordem] if do_ecadnet else escolhidos
        if not fichas:
            return
        with st.expander(f"Prints {nome} já tirados neste caso ({len(fichas)}; {len(no_pdf)} no PDF único)"):
            st.caption("Na ordem do PDF único. **↑ ↓** mudam a ordem; **✕** tira o print da lista e do PDF "
                       f"(os arquivos não são apagados: vão para a subpasta `{_replica.REMOVIDOS}`).")
            for posicao, f in enumerate(escolhidos):
                captura_id = f.get("captura", "")
                fora = chave_da_obra(f) not in ordem
                colunas = st.columns([6, 3, 3, 1, 1, 1])
                colunas[0].markdown(f"{posicao + 1}. {f.get('obra')}"
                                    + (" — *fora desta fila*" + (", não entra no PDF" if do_ecadnet else ", vai no fim") if fora else ""))
                colunas[1].write(f"{identificacao}: {chave_da_obra(f) or '—'}")
                colunas[2].write(str(f.get("capturado_em", ""))[:19].replace("T", " "))
                if colunas[3].button("↑", key=f"{prefixo}_sobe_{captura_id}", disabled=posicao == 0, help="Subir na ordem do PDF"):
                    _replica.mover(pasta, escolhidos, captura_id, -1)
                    st.rerun()
                if colunas[4].button("↓", key=f"{prefixo}_desce_{captura_id}", disabled=posicao == len(escolhidos) - 1,
                                     help="Descer na ordem do PDF"):
                    _replica.mover(pasta, escolhidos, captura_id, +1)
                    st.rerun()
                if colunas[5].button("✕", key=f"{prefixo}_tira_{captura_id}", help="Tirar este print da lista e do PDF"):
                    _replica.remover_print(pasta, captura_id)
                    st.session_state.pop(f"{prefixo}_junto", None)  # o PDF juntado antes ficou velho
                    st.session_state.pop(f"{prefixo}_zip_feito", None)
                    st.rerun()
            deixados = len(validos) - len(escolhidos)
            if deixados:
                st.caption(f"{deixados} prints mais antigos de obras fotografadas de novo não entram no PDF (continuam na pasta).")
            st.caption(f"Pasta: `{pasta.resolve()}`")
        if not escolhidos:
            return
        if do_ecadnet:
            st.markdown(f"**PDF único {nome}**: {len(no_pdf)} prints de {len({chave_da_obra(f) for f in no_pdf})} das "
                        f"{len(obras)} obras da fila" + (f". {len(fora_da_fila)} prints de obras fora desta fila não entram" if fora_da_fila else ""))
        else:
            st.markdown(f"**PDF único {nome}**: {len(no_pdf)} obras" + (f" ({len(fora_da_fila)} fora desta fila)" if fora_da_fila else ""))
        if st.button(rotulo_do_botao, disabled=not no_pdf, key=f"{prefixo}_juntar"):
            junto = juntar([pasta / f["arquivos"][arquivo] for f in no_pdf])
            destino = pasta_do_caso / nome_do_pdf
            destino.write_bytes(junto)
            st.session_state[f"{prefixo}_junto"] = (destino.name, junto)
            st.success(f"PDF único gerado ({len(no_pdf)} páginas): `{destino.resolve()}`")
        if st.session_state.get(f"{prefixo}_junto"):
            nome_do_junto, junto = st.session_state[f"{prefixo}_junto"]
            st.download_button(f"Baixar {nome_do_junto}", junto, file_name=nome_do_junto, mime="application/pdf", key=f"{prefixo}_baixar")
        if do_ecadnet:
            return
        # ISWC: além do PDF único, um .zip com um PDF por obra, na mesma ordem (pedido do dono, 29/10/2026)
        nome_do_zip = nome_do_pdf.removesuffix(".pdf") + " (um PDF por obra).zip"
        if st.button("Gerar o ZIP (um PDF por obra)", disabled=not no_pdf, key=f"{prefixo}_zip"):
            pacote = _iswc.zip_por_obra([(f.get("obra", ""), chave_da_obra(f), pasta / f["arquivos"][arquivo]) for f in no_pdf])
            destino = pasta_do_caso / nome_do_zip
            destino.write_bytes(pacote)
            st.session_state[f"{prefixo}_zip_feito"] = pacote
            st.success(f"ZIP gerado ({len(no_pdf)} PDFs, um por obra): `{destino.resolve()}`")
        if st.session_state.get(f"{prefixo}_zip_feito"):
            st.download_button(f"Baixar {nome_do_zip}", st.session_state[f"{prefixo}_zip_feito"], file_name=nome_do_zip,
                               mime="application/zip", key=f"{prefixo}_baixar_zip")

    if do_ecadnet:
        associacoes = ("Socinpro", "UBC")  # as buscas alternativas: cada uma com lista e PDF próprios
        lista_e_pdf([f for f in guardados if f.get("plataforma") not in associacoes], f"do {nome_do_site}", site, "tela",
                    _replica.pdf_dos_prints, "Juntar os prints em PDF", f"Ecadnet - {titular}.pdf")
        for sigla in associacoes:
            lista_e_pdf([f for f in guardados if f.get("plataforma") == sigla], f"da {sigla}", sigla.lower(), "tela",
                        _replica.pdf_dos_prints, f"Juntar os prints da {sigla} em PDF", f"{sigla} - {titular}.pdf")
    else:
        lista_e_pdf(guardados, f"do {nome_do_site}", site, "pdf", _iswc.juntar_pdfs, "Juntar os PDFs", f"ISWC - {titular}.pdf")



def aba_da_tabela():
    """A tabela de obras a partir dos arquivos que o escritório já tem: o relatório (enviado lá em cima), os PDFs do
    ISWC e os prints do Ecadnet. O app acha cada obra, recorta, mostra para conferir e monta o Word."""
    from creditos import tabela_obras as _tabela

    st.subheader("Tabela de obras")
    st.caption("Uma linha por obra do ISWC, na ordem do relatório e com o título do relatório: o recorte do relatório "
               "analítico, o do ISWC e o do Ecadnet. Obra sem print do Ecadnet fica com a célula vazia.")
    if arquivo is None or not arquivo.name.lower().endswith(".pdf"):
        st.warning("Para a tabela, envie lá em cima o **relatório analítico do ECAD em PDF**: é dele que sai a 2ª coluna.")
        return
    do_iswc = st.file_uploader("PDFs do ISWC: um PDF com todas as obras, vários PDFs, ou o .zip de um PDF por obra",
                               type=["pdf", "zip"], accept_multiple_files=True, key="tabela_iswc")
    do_ecadnet = st.file_uploader("Prints do Ecadnet: o Word ou o PDF com os prints de tela (ou as imagens, ou um .zip)",
                                  type=["docx", "pdf", "png", "jpg", "jpeg", "zip"], accept_multiple_files=True,
                                  key="tabela_ecadnet")
    chave = (arquivo.file_id, tuple(f.file_id for f in do_iswc or []), tuple(f.file_id for f in do_ecadnet or []))
    feito = st.session_state.get("tabela")
    if feito and feito["chave"] != chave:
        feito = None  # arquivos trocados: a tabela montada antes não vale mais
    if st.button("Montar a tabela", type="primary", disabled=not do_iswc, key="tabela_montar"):
        try:
            with st.status("Montando a tabela…", expanded=True) as situacao:
                st.write("Recortando o relatório e o ISWC…")
                pdfs = [pdf for f in do_iswc for pdf in _tabela.pdfs_de(f.name, f.getvalue())]
                linhas, obras, avisos = _tabela.preparar(arquivo.getvalue(), pdfs)
                prints = []
                if do_ecadnet:
                    arquivos = [im for f in do_ecadnet for im in _tabela.imagens_de(f.name, f.getvalue())]
                    barra = st.progress(0.0, text=f"Lendo os prints do Ecadnet (0 de {len(arquivos)})")
                    prints = _tabela.ler_prints(arquivos, lambda feitos, total: barra.progress(
                        feitos / total, text=f"Lendo os prints do Ecadnet ({feitos} de {total})"))
                    _tabela.ligar_prints(prints, obras, {_tabela.digitos(linha.iswc) for linha in linhas})
                situacao.update(label="Tabela montada: confira abaixo", state="complete", expanded=False)
        except _tabela.LeitorIndisponivel as e:
            st.error("O leitor de texto do Windows não respondeu, e é com ele que o app sabe de que obra é cada print do "
                     f"Ecadnet ({e}). Monte a tabela sem os prints do Ecadnet, ou avise quem cuida do app.")
            return
        except Exception as e:  # arquivo de outro formato, PDF escaneado etc.
            st.error(f"Não consegui montar a tabela: {type(e).__name__}: {e}")
            return
        feito = {"chave": chave, "linhas": linhas, "avisos": avisos, "prints": prints}
        st.session_state.tabela = feito
        st.session_state.pop("tabela_word", None)
    if not feito:
        return
    linhas, prints = feito["linhas"], feito["prints"]
    for aviso in feito["avisos"]:
        st.warning(aviso)

    # A ligação de cada print do Ecadnet: a pessoa pode trocar a obra (ou deixar o print de fora)
    nomes = {_tabela.digitos(linha.iswc): f"{i + 1}. {linha.titulo} ({linha.iswc})" for i, linha in enumerate(linhas)}
    opcoes = ["", *nomes]
    if prints:
        sem_obra = [p for p in prints if not p.obra]
        with st.expander(f"Prints do Ecadnet: {len(prints)} lidos, {len(prints) - len(sem_obra)} na tabela, "
                         f"{len(sem_obra)} de fora (obras que não estão no ISWC)"):
            st.caption("Cada print, a obra a que o app o ligou e como: pelo código da obra no endereço, pelo ISWC, pelo "
                       "título, ou como continuação (página rolada) do print anterior. Troque a obra se estiver errada.")
            for n, p in enumerate(prints):
                colunas = st.columns([2, 3])
                if p.recorte:
                    colunas[0].image(p.recorte, width=260)
                rotulo = f"Print {n + 1}" + (f": {p.titulo}" if p.titulo else "") + (f" · código {p.codigo}" if p.codigo else "")
                escolhida = colunas[1].selectbox(
                    rotulo, opcoes, index=opcoes.index(p.obra) if p.obra in opcoes else 0, key=f"tabela_print_{n}",
                    format_func=lambda o: nomes.get(o, "(fora da tabela)"))  # fmt: skip
                colunas[1].caption(f"Ligado por: {p.como or 'nada (não reconhecido)'}")
                if escolhida != p.obra:
                    p.obra, p.como = escolhida, "pessoa"
                    st.session_state.pop("tabela_word", None)
    _tabela.preencher_ecadnet(linhas, prints)

    com_ecadnet = sum(1 for linha in linhas if linha.ecadnet)
    st.markdown(f"**{len(linhas)} obras na tabela**, {com_ecadnet} com Ecadnet e {len(linhas) - com_ecadnet} sem.")
    with st.expander("Conferir obra por obra"):
        for i, linha in enumerate(linhas):
            colunas = st.columns([2, 3, 3, 4])
            colunas[0].markdown(f"{i + 1}. **{linha.titulo}**")
            for imagem in linha.relatorio:
                colunas[1].image(imagem, use_container_width=True)
            if linha.iswc_imagem:
                colunas[2].image(linha.iswc_imagem, use_container_width=True)
            for imagem in linha.ecadnet:
                colunas[3].image(imagem, use_container_width=True)
            if not linha.ecadnet:
                colunas[3].caption("sem Ecadnet")
    titular = relatorio.pseudonimo_titular or relatorio.nome_titular or "titular"
    nome_do_word = f"Tabela de obras - {titular}.docx"
    if st.button("Gerar a tabela (Word)", key="tabela_gerar"):
        conteudo = _tabela.montar_docx(linhas)
        pasta_do_caso.mkdir(parents=True, exist_ok=True)
        (pasta_do_caso / nome_do_word).write_bytes(conteudo)
        st.session_state.tabela_word = conteudo
        st.success(f"Tabela gerada: `{(pasta_do_caso / nome_do_word).resolve()}`")
    if st.session_state.get("tabela_word"):
        st.download_button(f"Baixar {nome_do_word}", st.session_state.tabela_word, file_name=nome_do_word,
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", key="tabela_baixar")


if etapa == ETAPAS[4]:
    aba_da_tabela()
    st.caption(f"Versão {VERSAO} · casos e prints em `{PASTA_DE_DADOS}`")
    st.stop()

if etapa in ETAPAS[2:4]:
    aba_da_replica("ecadnet" if etapa == ETAPAS[2] else "iswc")
    st.caption(f"Versão {VERSAO} · casos e prints em `{PASTA_DE_DADOS}`")
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
    # A planilha de intérpretes conferida é exigida: a coleta verifica só os pares (obra, intérprete) dela.
    from creditos import interpretes as _interpretes

    conferida = st.file_uploader(
        "Planilha de intérpretes conferida pelo compositor (.xlsx)", type=["xlsx"], key="planilha_conferida",
        help="A planilha da aba de intérpretes, depois que o compositor conferiu e corrigiu: a obra na primeira coluna e o intérprete "
        "na segunda. O app verifica só esses intérpretes. Sem ela, a coleta de prints não começa.",
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
        st.warning(
            "**Falta a planilha de intérpretes conferida.** Envie no campo acima a planilha da aba \"Buscar intérpretes\", depois "
            "de conferida pelo compositor. A coleta de prints só começa com ela: é ela que diz quem gravou cada obra."
        )
    # A Apple Music e o Vagalume não entram na coleta de prints: o escritório não usa prints dessas duas. A Apple
    # Music continua valendo na busca de intérpretes, na outra aba.
    NOMES_DAS_PLATAFORMAS = {"deezer": "Deezer (com login)", "youtube": "YouTube Music", "spotify": "Spotify", "tidal": "Tidal",
                             "palco": "Palco MP3", "amazon": "Amazon Music (aplicativo de desktop e site)",
                             "claro": "Claro Música (com login)"}
    from creditos import pipeline as _pipeline

    escolhidas = st.multiselect(
        "Plataformas", list(NOMES_DAS_PLATAFORMAS), default=[p for p in NOMES_DAS_PLATAFORMAS if p not in ("amazon", "claro")],
        format_func=NOMES_DAS_PLATAFORMAS.get,
        help="Só as plataformas marcadas são verificadas. A Amazon Music vem desmarcada: começa pelo aplicativo de desktop, que só funciona no "
        "Windows, com o aplicativo instalado e a conta do escritório já logada nele. A Claro Música também vem desmarcada: só mostra as "
        "músicas a quem está logado. A Deezer tira os prints logada na conta.",
    )
    if "deezer" in escolhidas:
        st.warning(
            "**Deezer:** os prints são tirados com a conta da Deezer logada. Clique no botão abaixo **uma vez**: abre uma "
            "janela do navegador do app na Deezer. Faça o login nela (o app não digita nem guarda senha) e **feche a "
            "janela**. A sessão fica guardada neste computador para as próximas coletas."
        )
        if st.button("Entrar na Deezer", disabled=andamento.atual() is not None and andamento.atual().viva):
            import threading

            from creditos import deezer as _deezer

            threading.Thread(target=_deezer.entrar, args=(_pipeline.perfil_da_deezer(pasta_do_caso),), daemon=True).start()
            st.info("Janela aberta. Faça o login na Deezer, feche a janela e só então clique em **Coletar e classificar**.")
    if "tidal" in escolhidas:
        from creditos import tidal_api as _tidal_api

        if _tidal_api.chaves(pasta_do_caso.parent.parent) is None:
            st.warning(
                "**Tidal sem as chaves da API:** sem elas, o app procura as músicas pela busca do site, que sem login acha "
                f"pouca coisa. Crie o arquivo `{_tidal_api.ARQUIVO_DAS_CHAVES}` em `{pasta_do_caso.parent.parent}` com as "
                "linhas TIDAL_CLIENT_ID=... e TIDAL_CLIENT_SECRET=... (as chaves do escritório, de developer.tidal.com)."
            )
        else:
            st.caption("Tidal: as músicas são achadas pelo código ISRC (API oficial do Tidal, a partir da Deezer); os créditos "
                       "são lidos e fotografados na página do Tidal, sem login.")
    if "claro" in escolhidas:
        st.warning(
            "**Claro Música:** o site só mostra as músicas a quem está logado. Clique no botão abaixo **uma vez**: abre uma "
            "janela do navegador do app na Claro Música. Faça o login nela (o app não digita nem guarda senha) e **feche a "
            "janela**. A sessão fica guardada neste computador para as próximas coletas."
        )
        if st.button("Entrar na Claro Música", disabled=andamento.atual() is not None and andamento.atual().viva):
            import threading

            from creditos import claro as _claro

            threading.Thread(target=_claro.entrar, args=(_pipeline.perfil_da_claro(pasta_do_caso),), daemon=True).start()
            st.info("Janela aberta. Faça o login na Claro Música, feche a janela e só então clique em **Coletar e classificar**.")
    if "amazon" in escolhidas:
        st.warning(
            "**Amazon Music:** primeiro o aplicativo de desktop, que é onde a Amazon mostra o compositor: dois prints por "
            "música (o menu e a janela de créditos). Depois, só para as músicas em que o compositor não aparece no "
            "aplicativo, o print da mesma música no site, que não tem tela de créditos. O app vai **fechar e abrir de novo "
            "o Amazon Music** para poder controlá-lo, e usa a conta que já estiver logada nele: não digita senha nem faz "
            "login. Não use o aplicativo enquanto a coleta roda."
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
            "Com esta opção, o navegador do app abre **visível e já maximizado**. Com dois monitores, ele abre no que **não** "
            "é o principal: trabalhe no principal. Com um monitor só, ele ocupa a tela toda. Não cubra nem minimize essa "
            "janela durante a coleta. Tudo o que estiver nesse monitor sai no print: feche ali o que não pode aparecer."
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

    with st.expander("Recomeçar do zero (apagar o que foi guardado)"):
        st.markdown(
            "O app guarda o que já leu e os prints que já tirou, para não repetir trabalho. Para **tirar tudo de novo** "
            "(depois de uma atualização do app, ou para mudar o formato do print), apague aqui o que foi guardado das "
            "plataformas marcadas acima. Vale só para este compositor."
        )
        com_algo = [p for p in escolhidas if any((pasta_do_caso / n).is_dir() for n in _pipeline.PASTAS_DO_CASO.get(p, []))]
        if not com_algo:
            st.caption("Não há nada guardado das plataformas marcadas.")
        else:
            st.write("Será apagado: " + ", ".join(NOMES_DAS_PLATAFORMAS[p] for p in com_algo) + ".")
            if {"deezer", "apple"} & set(com_algo):
                st.warning("A Deezer e a Apple Music também guardam o que a **busca de intérpretes** leu. Apagando, uma busca "
                           "longa terá de ser refeita.")
            confirmado = st.checkbox("Sim, apagar as leituras e os prints dessas plataformas. Isso não pode ser desfeito.", key="confirma_limpeza")
            ocupado = andamento.atual() is not None and andamento.atual().viva
            if st.button("Apagar o que foi guardado", disabled=not confirmado or ocupado):
                apagadas = _pipeline.limpar_o_guardado(pasta_do_caso, com_algo)
                st.session_state.pop("coletas", None)
                st.session_state.pop("saidas", None)
                st.session_state.pop("confirma_limpeza", None)
                st.session_state.aviso_de_limpeza = len(apagadas)
                st.rerun()
    if st.session_state.pop("aviso_de_limpeza", None):
        st.success("Apagado. A próxima coleta lê e fotografa tudo de novo nessas plataformas.")

    def recolher(tarefa):
        """Pega o resultado da coleta que terminou (ou diz por que não terminou) e libera o botão de coletar."""
        andamento.encerrar()
        guardar_o_salvo(tarefa, "salvo_coleta")
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
            salvo = tarefa.andamento.salvo
            if salvo and not salvo["parcial"]:  # o que foi gravado é o que a tela oferece: não monta de novo
                st.session_state.saidas = {k: salvo[k] for k in ("planilha", "pacote", "resumo", "avisos")}

    tarefa = andamento.atual()
    de_outra_etapa = tarefa is not None and tarefa.dados.get("tipo") == "interpretes"
    if de_outra_etapa:
        st.info("A busca de intérpretes terminou: abra a aba **Buscar intérpretes** para ver o resultado." if not tarefa.viva else
                "Há uma busca de intérpretes em andamento na outra aba. Espere terminar, ou pare por lá, antes de coletar.")
        tarefa = None
    elif tarefa is not None and not tarefa.viva:
        recolher(tarefa)
        tarefa = None
    if not escolhidas:
        st.warning("Marque pelo menos uma plataforma para coletar.")
    if tarefa is None and not de_outra_etapa and conferidos and escolhidas and st.button("Coletar e classificar", type="primary"):
        from creditos import pipeline
        from creditos.classificador import Config

        config = Config(nomes_confirmados=do_titular, interpretes=interpretes)
        # Com muitos coautores, percorrer a discografia de cada um multiplicaria a coleta sem ajudar: os
        # intérpretes aparecem pela busca por título e pelos créditos que as plataformas mostram.
        coautores = st.session_state.config["nomes_dos_coautores"]
        st.session_state.pop("salvo_coleta", None)

        def coletar(avisar, relatorio=relatorio, pasta=pasta_do_caso):
            # A própria coleta grava a planilha e o .zip no disco ao terminar, ou os parciais se parar: o resultado
            # existe mesmo que a aba do navegador feche ou ninguém esteja olhando a tela.
            return resultado.rodar_e_salvar(
                avisar,
                lambda: pipeline.executar(
                    relatorio, config, pasta, coautores if len(coautores) <= 10 else [], ao_avancar=avisar,
                    limite_youtube=limite, plataformas=tuple(escolhidas), mostrar_navegador=mostrar_navegador,
                    tela_inteira=tela_inteira, conferidos=conferidos or None,
                ),
                lambda coletas, parcial: resultado.salvar_coleta(relatorio, coletas, pasta, parcial),
            )

        # A coleta roda em outra linha de execução: o navegador automatizado não pode rodar na da tela.
        tarefa = andamento.iniciar(
            coletar,
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
    mostrar_o_salvo("salvo_coleta")

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

    # Coletas feitas em separado (ex.: YouTube e Spotify num dia, Palco MP3 em outro): juntar sem copiar e colar colunas
    with st.expander("Juntar planilhas de coletas feitas em separado"):
        from creditos import juntar as _juntar

        st.markdown(
            "Rodou as plataformas em dias ou vezes diferentes? Envie aqui as **planilhas de obras** dessas coletas e o app "
            "monta uma planilha só. **Não copie e cole colunas de uma planilha na outra**: cada coleta tem linhas "
            "diferentes (uma obra pode ter 5 intérpretes numa e 1 na outra), e a coluna colada põe os links nas obras "
            "erradas. Aqui cada gravação é colocada pela obra e pelo intérprete."
        )
        st.caption(
            "Vale o que o app coletou (aba Gravações de cada planilha): o que foi colado ou digitado à mão na aba Obras não "
            "entra. Se a mesma plataforma vier em duas planilhas, fica a coleta mais recente."
        )
        enviadas = st.file_uploader("Planilhas de obras geradas pelo app (.xlsx)", type=["xlsx"], accept_multiple_files=True,
                                    key="planilhas_para_juntar")
        if enviadas and len(enviadas) >= 2:
            try:
                lidas = [_juntar.ler(arquivo.getvalue(), arquivo.name) for arquivo in enviadas]
                junta = _juntar.juntar(lidas, relatorio)
            except _juntar.PlanilhaInvalida as e:
                st.error(str(e))
            else:
                st.success("Planilha montada com: " + "; ".join(f"**{p}** (de {nome})" for p, nome in junta.de_onde.items()) + ".")
                for aviso in junta.avisos:
                    st.warning(aviso)
                st.download_button(
                    "Baixar a planilha juntada (.xlsx)", junta.planilha,
                    f"Planilha de Obras - {relatorio.pseudonimo_titular or relatorio.nome_titular} (juntada).xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary", key="baixar_juntada",
                )
                st.caption("Os prints de cada coleta continuam na pasta do caso: as listas e os PDFs das provas de cada "
                           "plataforma saem da coleta em que ela foi feita.")
        elif enviadas:
            st.info("Envie pelo menos duas planilhas.")


st.caption(f"Versão {VERSAO} · casos e prints em `{PASTA_DE_DADOS}`")
