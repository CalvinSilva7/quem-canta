"""Desligar o computador no fim da coleta, e o registro da coleta na pasta do compositor.

Quem deixa a coleta rodando à noite marca a caixinha: quando a coleta termina, ou para no meio (erro, captcha, pedido
de parada), o resultado já está gravado no disco (ver creditos.resultado) e o app pede ao Windows para desligar daqui
a 5 minutos, de forma forçada: um programa com algo sem salvar não segura o computador ligado. Nos 5 minutos, quem
ainda estiver ali cancela pelo botão da tela ou pelo aviso do Windows.

O registro (`Registro da coleta.txt`, na pasta do resultado) diz o que aconteceu em cada coleta: quando, o que foi
gravado, os avisos das plataformas, o erro com o traceback e se o desligamento foi pedido. É o arquivo que a pessoa
manda para quem cuida do app quando algo dá errado.
"""

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import resultado

SEGUNDOS = 300  # o tempo que o Windows espera antes de desligar: dá para cancelar
ARQUIVO = "Registro da coleta.txt"
MENSAGEM = ("A coleta do Quem Canta terminou e o resultado está salvo. O computador será desligado. "
            "Para cancelar, use o botão Cancelar o desligamento no Quem Canta.")


def _windows(argumentos, rodar=subprocess.run) -> str:
    """Roda o `shutdown` do Windows. Devolve "" se deu certo, ou o motivo de não ter dado."""
    if sys.platform != "win32":
        return "o desligamento automático só funciona no Windows"
    try:
        # o shutdown responde na página de código do console (a "oem"): com outra, os acentos saem trocados
        feito = rodar(["shutdown", *argumentos], capture_output=True, text=True, encoding="oem", errors="replace", timeout=30,
                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:  # nunca derruba nada: o motivo vai para a tela e para o registro
        return f"{type(e).__name__}: {e}"
    if feito.returncode == 0:
        return ""
    return (feito.stderr or feito.stdout or "").strip() or f"o Windows respondeu com o código {feito.returncode}"


def pedir(segundos=SEGUNDOS, rodar=subprocess.run) -> str:
    """Pede ao Windows para desligar daqui a `segundos`, forçado (/f: fecha os programas sem perguntar)."""
    return _windows(["/s", "/f", "/t", str(int(segundos)), "/c", MENSAGEM], rodar)


def cancelar(rodar=subprocess.run) -> str:
    """Cancela o desligamento pedido (o mesmo que `shutdown /a`)."""
    return _windows(["/a"], rodar)


def _situacao(tarefa) -> str:
    if tarefa.interrompida:
        return "PAROU NO MEIO (pedido de parada)"
    if tarefa.erro is not None:
        return f"PAROU NO MEIO (erro: {type(tarefa.erro).__name__}: {tarefa.erro})"
    return "terminou"


def texto_do_registro(tarefa, fim: datetime, versao: str, desligar: bool, desligamento: str = "") -> str:
    """O bloco de uma coleta no registro. `desligamento` é o que `pedir` respondeu ("" = pedido aceito)."""
    dados, andamento = tarefa.dados, tarefa.andamento
    relatorio = dados.get("relatorio")
    linhas = [
        "=" * 78,
        f"Coleta de {tarefa.comecou:%d/%m/%Y %H:%M:%S} até {fim:%d/%m/%Y %H:%M:%S} · Quem Canta versão {versao}",
        f"Compositor: {(relatorio.pseudonimo_titular or relatorio.nome_titular) if relatorio else ''}",
        f"Plataformas: {', '.join(andamento.etapas)}",
        f"Títulos a verificar: {len(dados['conferidos']) if dados.get('conferidos') else len({o.titulo for o in relatorio.obras}) if relatorio else ''}",
        f"Situação: {_situacao(tarefa)}",
    ]
    if tarefa.interrompida or tarefa.erro is not None:
        retrato = andamento.retrato()
        linhas.append(f"Onde parou: etapa {retrato['etapa']} de {retrato['etapas']} ({retrato['nome']}) · {retrato['detalhe']}")
    salvo = andamento.salvo
    if salvo:
        linhas.append("Resultado gravado" + (" (PARCIAL)" if salvo["parcial"] else "") + ":")
        linhas += [f"  {arquivo}" for arquivo in salvo["arquivos"]]
    else:
        linhas.append("Resultado gravado: nenhum")
    if andamento.erro_ao_salvar:
        linhas.append(f"Erro ao gravar o resultado: {andamento.erro_ao_salvar}")
    coletas = tarefa.resultado if tarefa.resultado is not None else tarefa.parciais
    for coleta in coletas or []:
        linhas.append(f"{coleta.plataforma}: {len(coleta.gravacoes)} gravações lidas"
                      + (f" · parou: {coleta.interrompida}" if coleta.interrompida else ""))
        linhas += [f"  aviso: {aviso}" for aviso in coleta.avisos]
        linhas += [f"  não foi possível ler {link} ({erro})" for link, erro in coleta.albuns_com_erro]
    if tarefa.detalhe_do_erro:
        linhas += ["Detalhe do erro:", tarefa.detalhe_do_erro.rstrip()]
    if not desligar:
        linhas.append("Desligar o computador no fim: não marcado")
    elif desligamento:
        linhas.append(f"Desligar o computador no fim: o Windows NÃO aceitou ({desligamento})")
    else:
        linhas.append(f"Desligar o computador no fim: pedido às {fim:%H:%M:%S}, para as "
                      f"{fim + timedelta(seconds=SEGUNDOS):%H:%M:%S} (forçado)")
    return "\n".join(linhas) + "\n\n"


def registrar(tarefa, versao: str, desligar: bool, desligamento: str = "", agora=datetime.now) -> Path | None:
    """Acrescenta a coleta ao registro, na pasta do resultado do compositor. Nunca derruba nada."""
    try:
        pasta = resultado.pasta_do_resultado(tarefa.dados["pasta"])
        pasta.mkdir(parents=True, exist_ok=True)
        arquivo = pasta / ARQUIVO
        with open(arquivo, "a", encoding="utf-8") as registro:
            registro.write(texto_do_registro(tarefa, agora(), versao, desligar, desligamento))
        return arquivo
    except Exception:
        return None


def ao_terminar(versao: str, desligar: bool, rodar=subprocess.run):
    """O que a coleta faz ao acabar, terminada ou parada, já com o resultado gravado: pede o desligamento (se marcado)
    e escreve o registro. Roda na linha da coleta, sem depender da tela estar aberta."""
    def fim(tarefa):
        tarefa.desligamento = pedir(rodar=rodar) if desligar else None
        tarefa.registro = registrar(tarefa, versao, desligar, tarefa.desligamento or "")
    return fim
