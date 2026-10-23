"""Ler e gravar no disco sem deixar arquivo pela metade.

`write_text` zera o arquivo antes de escrever: se o app fecha (ou o PC hiberna) nesse instante, sobra um arquivo
vazio, e a próxima leitura quebra com "JSONDecodeError: Expecting value: line 1 column 1 (char 0)". Aqui a
gravação vai para um arquivo temporário e só então toma o lugar do original, de uma vez. E a leitura de um cache
ilegível não derruba a coleta: o arquivo é descartado e a página é lida de novo.

No Windows, a troca pode ser recusada por um instante ("PermissionError: [WinError 5] Acesso negado") quando outro
programa está com o arquivo aberto: o antivírus conferindo, ou o Google Drive / OneDrive sincronizando a pasta
Documentos. Por isso a troca é tentada de novo algumas vezes, e o cache que mesmo assim não puder ser guardado não
para a coleta: a leitura só é refeita numa próxima rodada.
"""

import itertools
import json
import os
import threading
import time
from pathlib import Path

ESPERAS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.5, 2.5)  # ~5,5 s no total: o bastante para o antivírus ou a sincronização soltar
_contador = itertools.count()


def _trocar(temporario: Path, arquivo: Path, esperas=ESPERAS):
    for espera in (*esperas, None):
        try:
            os.replace(temporario, arquivo)
            return
        except PermissionError:
            if espera is None:
                raise
            time.sleep(espera)


def gravar_bytes(arquivo, conteudo: bytes, esperas=ESPERAS) -> Path:
    """Grava `conteudo` em `arquivo` (criando a pasta), trocando o original só quando a escrita terminou."""
    arquivo = Path(arquivo)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    # nome único por gravação: duas linhas de execução gravando o mesmo arquivo não usam o mesmo temporário
    temporario = arquivo.with_name(f"{arquivo.name}.{os.getpid()}-{threading.get_ident()}-{next(_contador)}.tmp")
    try:
        with open(temporario, "wb") as saida:
            saida.write(conteudo)
            saida.flush()
            os.fsync(saida.fileno())
        _trocar(temporario, arquivo, esperas)
    finally:
        try:
            temporario.unlink(missing_ok=True)
        except OSError:
            pass
    return arquivo


def gravar_json(arquivo, dados, esperas=ESPERAS, **opcoes) -> Path | None:
    """Grava o cache. Se o Windows não deixar (arquivo preso por outro programa), segue sem ele e devolve None."""
    try:
        return gravar_bytes(arquivo, json.dumps(dados, ensure_ascii=False, **opcoes).encode("utf-8"), esperas)
    except PermissionError:
        return None


def ler_json(arquivo, padrao=None):
    """O JSON de `arquivo`, ou `padrao` se ele não existir ou estiver ilegível (o ilegível é apagado)."""
    if arquivo is None:
        return padrao
    arquivo = Path(arquivo)
    if not arquivo.exists():
        return padrao
    try:
        texto = arquivo.read_text(encoding="utf-8")
    except OSError:  # preso por outro programa agora: não é cache quebrado, não apaga
        return padrao
    try:
        return json.loads(texto)
    except ValueError:
        try:
            arquivo.unlink()  # cache corrompido: some, e o que ele guardava é lido de novo
        except OSError:
            pass
        return padrao
