"""Ecadnet, para a réplica: a pessoa faz o login na janela do app; para cada obra, o app abre a página
dela pelo código do relatório (sem busca) e fotografa quando a pessoa pede. A janela é a de `replica`."""

import re
from pathlib import Path

from cantor.matching import normalizar

SITE = "https://ecadnet.ecad.org.br/"
OBRA = SITE + "consulta/detalhes/obras-musicais/{}"
PLATAFORMA, ETAPA = "Ecadnet", "ecadnet"
SEM_DETALHE = "o Ecadnet não mostra a página desta obra"


def perfil_do_ecadnet(pasta_do_caso) -> Path:
    """Onde o navegador do app guarda a sessão do Ecadnet: na pasta de dados, fora de qualquer caso."""
    return Path(pasta_do_caso).parent.parent / "perfil-ecadnet"


def endereco_da_obra(codigo: str) -> str:
    """O endereço da obra no Ecadnet. É o mesmo número da coluna "CÓD. OBRA" do relatório analítico."""
    numero = re.sub(r"\D", "", str(codigo or ""))
    if not numero:
        raise ValueError("a obra não tem código no relatório")
    return OBRA.format(numero)


def conferir_pagina(url: str, texto: str, codigo: str, titulo: str) -> str:
    """A janela mostra a página desta obra, carregada? Devolve o motivo de não estar, ou "" se está."""
    numero = re.sub(r"\D", "", str(codigo or ""))
    if "/obras-musicais/" not in url:
        if "login" in normalizar(texto) or "bem-vindo ao ecadnet" in normalizar(texto) or "cadastr" in normalizar(texto):
            return "o Ecadnet está pedindo o login: faça o login na janela e abra a obra de novo"
        return "a janela não está na página de uma obra"
    if numero and not url.rstrip("/").endswith(numero):
        return "a janela está na página de outra obra"
    if "nao ha resultados nesta busca" in normalizar(texto):
        return (SEM_DETALHE + ' (o site respondeu "Não há resultados nesta busca"): o Ecadnet não mostra cadastros '
                "pendentes de identificação ou em conflito. Se quiser registrar isso, clique em Fotografar mesmo assim")
    if "detalhes da obra musical" not in normalizar(texto):
        return "a página da obra ainda não carregou"
    if titulo and normalizar(titulo) not in normalizar(texto):
        return f'a página não mostra o título "{titulo}"'
    return ""
