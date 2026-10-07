"""Aviso e instalação de versão nova.

O app consulta um endereço que devolve um JSON pequeno (por padrão, o arquivo `versao-publicada.json`
do repositório; outro endereço pode ser dado em `atualizacao.json`, na pasta do app, ou na variável
QUEMCANTA_ATUALIZACAO):

    {"versao": "0.5.0", "url": "https://.../quem-canta-0.5.0-windows.zip",
     "sha256": "<hash do zip>", "notas": "o que mudou"}

Se a versão publicada for maior que a instalada, a tela avisa. Instalar é baixar o zip, conferir
o hash e copiar os arquivos do programa por cima dos antigos. A pasta de dados dos casos e o
ambiente (.venv) nunca são tocados. Para desligar a consulta: QUEMCANTA_ATUALIZACAO=desligada.
"""

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import requests

from .versao import VERSAO

PASTA_DO_APP = Path(__file__).resolve().parent.parent
PRESERVADOS = {".venv", "dados", "atualizacao.json"}  # nunca substituídos por uma atualização
REPOSITORIO = "CalvinSilva7/quem-canta"
ENDERECO_PADRAO = f"https://raw.githubusercontent.com/{REPOSITORIO}/main/versao-publicada.json"
NOVA, EM_DIA, SEM_RESPOSTA = "nova", "em dia", "sem resposta"


class FalhaNaAtualizacao(Exception):
    pass


def endereco_de_consulta(pasta=PASTA_DO_APP) -> str:
    if os.environ.get("QUEMCANTA_ATUALIZACAO"):
        return os.environ["QUEMCANTA_ATUALIZACAO"].strip()
    arquivo = Path(pasta) / "atualizacao.json"
    if arquivo.exists():
        try:
            proprio = str(json.loads(arquivo.read_text(encoding="utf-8")).get("consultar_em", "")).strip()
        except (ValueError, AttributeError):
            proprio = ""
        if proprio:
            return proprio
    return ENDERECO_PADRAO


def endereco_do_pacote(versao: str = VERSAO) -> str:
    """Onde o zip de uma versão é publicado: um anexo da release `v<versão>` do repositório."""
    return f"https://github.com/{REPOSITORIO}/releases/download/v{versao}/quem-canta-{versao}-windows.zip"


def _numeros(versao: str) -> tuple:
    return tuple(int(p) for p in str(versao).strip().lstrip("vV").split(".") if p.isdigit())


def mais_nova(publicada: str, instalada: str = VERSAO) -> bool:
    return bool(_numeros(publicada)) and _numeros(publicada) > _numeros(instalada)


def verificar(endereco: str = "", espera=6) -> tuple[str, dict | None]:
    """Pergunta pela versão publicada. Devolve (NOVA, dados da versão), (EM_DIA, None) ou (SEM_RESPOSTA, None).

    Falha de rede ou resposta malformada é SEM_RESPOSTA: ficar sem saber de uma atualização nunca pode
    impedir o app de abrir, e nunca vira "você está em dia".
    """
    endereco = endereco or endereco_de_consulta()
    if not endereco.startswith("https://"):
        return SEM_RESPOSTA, None
    try:
        publicada = requests.get(endereco, timeout=espera).json()
    except (requests.RequestException, ValueError):
        return SEM_RESPOSTA, None
    if not isinstance(publicada, dict) or not _numeros(publicada.get("versao", "")):
        return SEM_RESPOSTA, None
    if not mais_nova(publicada["versao"]):
        return EM_DIA, None
    return NOVA, {"versao": str(publicada["versao"]), "url": str(publicada.get("url", "")),
                  "sha256": str(publicada.get("sha256", "")).lower(), "notas": str(publicada.get("notas", ""))}


def consultar(endereco: str = "", espera=4) -> dict | None:
    """A versão publicada, se for mais nova que a instalada; senão None."""
    return verificar(endereco, espera)[1]


def instalar(publicada: dict, pasta=PASTA_DO_APP, baixar=None) -> list[str]:
    """Baixa o pacote, confere o hash e copia os arquivos do programa para `pasta`. Devolve o que mudou
    (arquivo igual ao que já está instalado não é regravado).

    Recusa pacote sem hash, com hash diferente, com endereço que não seja https ou com caminho que saia da pasta.
    """
    if not publicada.get("url", "").startswith("https://"):
        raise FalhaNaAtualizacao("o endereço do pacote não é https")
    if len(publicada.get("sha256", "")) != 64:
        raise FalhaNaAtualizacao("a atualização não informa o hash do pacote; não é instalada sem conferência")
    try:
        conteudo = baixar(publicada["url"]) if baixar else requests.get(publicada["url"], timeout=120).content
    except requests.RequestException as e:
        raise FalhaNaAtualizacao(f"não foi possível baixar o pacote ({type(e).__name__})") from e
    if hashlib.sha256(conteudo).hexdigest() != publicada["sha256"]:
        raise FalhaNaAtualizacao("o pacote baixado não confere com o hash publicado; nada foi instalado")
    pasta, escritos = Path(pasta).resolve(), []
    try:
        pacote = zipfile.ZipFile(io.BytesIO(conteudo))
    except zipfile.BadZipFile as e:
        raise FalhaNaAtualizacao("o pacote baixado não é um zip válido") from e
    nomes = [n for n in pacote.namelist() if not n.endswith("/")]
    raiz = nomes[0].split("/")[0] + "/" if nomes and all(n.startswith(nomes[0].split("/")[0] + "/") for n in nomes) else ""
    for nome in nomes:
        relativo = nome[len(raiz):]
        if not relativo or relativo.split("/")[0] in PRESERVADOS:
            continue
        destino = (pasta / relativo).resolve()
        if pasta not in destino.parents:
            raise FalhaNaAtualizacao(f"o pacote tem um caminho fora da pasta do app: {nome}")
        novo = pacote.read(nome)
        if destino.is_file() and destino.read_bytes() == novo:
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(novo)
        escritos.append(relativo)
    return escritos
