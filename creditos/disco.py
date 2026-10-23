"""Ler e gravar no disco sem deixar arquivo pela metade.

`write_text` zera o arquivo antes de escrever: se o app fecha (ou o PC hiberna) nesse instante, sobra um arquivo
vazio, e a próxima leitura quebra com "JSONDecodeError: Expecting value: line 1 column 1 (char 0)". Aqui a
gravação vai para um arquivo temporário e só então toma o lugar do original, de uma vez. E a leitura de um cache
ilegível não derruba a coleta: o arquivo é descartado e a página é lida de novo.
"""

import json
import os
from pathlib import Path


def gravar_bytes(arquivo, conteudo: bytes) -> Path:
    """Grava `conteudo` em `arquivo` (criando a pasta), trocando o original só quando a escrita terminou."""
    arquivo = Path(arquivo)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_name(f"{arquivo.name}.{os.getpid()}.tmp")
    try:
        with open(temporario, "wb") as saida:
            saida.write(conteudo)
            saida.flush()
            os.fsync(saida.fileno())
        os.replace(temporario, arquivo)
    finally:
        temporario.unlink(missing_ok=True)
    return arquivo


def gravar_json(arquivo, dados, **opcoes) -> Path:
    return gravar_bytes(arquivo, json.dumps(dados, ensure_ascii=False, **opcoes).encode("utf-8"))


def ler_json(arquivo, padrao=None):
    """O JSON de `arquivo`, ou `padrao` se ele não existir ou estiver ilegível (o ilegível é apagado)."""
    if arquivo is None:
        return padrao
    arquivo = Path(arquivo)
    if not arquivo.exists():
        return padrao
    try:
        return json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        try:
            arquivo.unlink()  # cache corrompido: some, e o que ele guardava é lido de novo
        except OSError:
            pass
        return padrao
