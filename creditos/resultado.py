"""O resultado gravado no disco pela própria coleta, sem ninguém precisar clicar em baixar.

A busca de intérpretes e a coleta de prints rodam em segundo plano. Quando terminam, gravam o que produziram em
<pasta do caso>/resultado/, com data e hora no nome (uma rodada nova nunca apaga a anterior). Se pararem no meio,
pelo botão de parar ou por um erro, gravam o que as plataformas já terminadas deram, com "PARCIAL" no nome.
Assim o resultado existe mesmo que a aba do navegador tenha fechado ou o PC tenha bloqueado a tela: a sessão da
tela pode morrer, a tarefa não.
"""

import re
from datetime import datetime
from pathlib import Path

from . import disco

PASTA = "resultado"


def pasta_do_resultado(pasta_do_caso) -> Path:
    return Path(pasta_do_caso) / PASTA


def _nome_do_caso(relatorio) -> str:
    nome = relatorio.pseudonimo_titular or relatorio.nome_titular or "caso"
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", nome).strip(" .") or "caso"  # o que o Windows não aceita em nome de arquivo


def _caminho(pasta: Path, nome: str, extensao: str) -> Path:
    """Um caminho livre: se já houver um arquivo com esse nome (duas rodadas no mesmo minuto), ganha (2), (3)…"""
    caminho, n = pasta / f"{nome}{extensao}", 2
    while caminho.exists():
        caminho, n = pasta / f"{nome} ({n}){extensao}", n + 1
    return caminho


def _base(relatorio, rotulo, parcial, agora) -> str:
    return f"{rotulo} - {_nome_do_caso(relatorio)} - {(agora or datetime.now()):%Y-%m-%d %Hh%M}" + (" - PARCIAL" if parcial else "")


def salvar_coleta(relatorio, coletas, pasta_do_caso, parcial=False, agora=None) -> dict:
    """Grava a planilha de obras e o .zip das provas. Devolve o mesmo que a tela oferece para baixar, mais os caminhos."""
    from . import provas, saida

    planilha = saida.planilha(relatorio, coletas)
    pacote, resumo, avisos = provas.pacote(relatorio, coletas, pasta_do_caso, planilha)
    pasta = pasta_do_resultado(pasta_do_caso)
    arquivos = [
        disco.gravar_bytes(_caminho(pasta, _base(relatorio, "Planilha de Obras", parcial, agora), ".xlsx"), planilha),
        disco.gravar_bytes(_caminho(pasta, _base(relatorio, "Provas", parcial, agora), ".zip"), pacote),
    ]
    return {"planilha": planilha, "pacote": pacote, "resumo": resumo, "avisos": avisos,
            "arquivos": [str(a) for a in arquivos], "parcial": parcial}


def salvar_interpretes(relatorio, lista, pasta_do_caso, parcial=False, agora=None) -> dict:
    """Grava a planilha de intérpretes (a mesma do botão de baixar)."""
    from . import interpretes

    planilha = interpretes.planilha(relatorio, lista)
    arquivo = disco.gravar_bytes(_caminho(pasta_do_resultado(pasta_do_caso), _base(relatorio, "Intérpretes", parcial, agora), ".xlsx"),
                                 planilha)
    return {"planilha": planilha, "arquivos": [str(arquivo)], "parcial": parcial}


def rodar_e_salvar(andamento, rodar, salvar, do_parcial=None):
    """Roda `rodar()` e grava o que ela devolveu com `salvar(produto, parcial)`.

    Se `rodar` parar no meio (parada ou erro) e a exceção trouxer `coletas_parciais` (ver pipeline.executar), grava o
    parcial, `do_parcial(coletas)` antes, e deixa a exceção seguir. O que foi gravado fica em `andamento.salvo`; uma
    falha ao gravar fica em `andamento.erro_ao_salvar` e nunca derruba a coleta, que continua com o resultado na tela.
    """
    try:
        produto = rodar()
    except BaseException as e:
        parciais = getattr(e, "coletas_parciais", None)
        if parciais:
            _gravar(andamento, lambda: salvar(do_parcial(parciais) if do_parcial else parciais, True))
        raise
    _gravar(andamento, lambda: salvar(produto, False))
    return produto


def _gravar(andamento, gravar):
    andamento.detalhe = "Gravando o resultado no disco…"  # montar o PDF e o Word das provas leva alguns segundos
    try:
        andamento.salvo = gravar()
    except Exception as e:  # disco cheio, pasta sem permissão: a tela avisa, e o botão de baixar continua lá
        andamento.erro_ao_salvar = f"{type(e).__name__}: {e}"
