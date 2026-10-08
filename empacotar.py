"""Monta o pacote de instalação para Windows: python empacotar.py [pasta de destino] [o que mudou]

Gera quem-canta-<versão>-windows.zip e grava `versao-publicada.json`, com o SHA-256 do pacote. Para os apps
instalados avisarem da atualização: anexar o zip à release `v<versão>` do repositório e subir o
`versao-publicada.json` para a branch main. Nada de dados de clientes entra no pacote.
"""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

from creditos.atualizacao import endereco_do_pacote
from creditos.versao import VERSAO

AQUI = Path(__file__).resolve().parent
ENTRAM = ["creditos_app.py", "app.py", "avaliar.py", "requirements.txt", "README.md", "LEIA-ME.txt", "Instalar.cmd",
          "Abrir Quem Canta.cmd", "Testar Amazon.cmd", "atualizacao.json", "cantor", "creditos", ".streamlit", "exemplos"]
FORA = {"__pycache__", ".pytest_cache", ".DS_Store"}
TEXTO_DO_WINDOWS = {".cmd", ".txt"}  # o Bloco de Notas e o cmd esperam fim de linha CRLF


def arquivos():
    for nome in ENTRAM:
        caminho = AQUI / nome
        if caminho.is_file():
            yield caminho
        elif caminho.is_dir():
            yield from (p for p in sorted(caminho.rglob("*")) if p.is_file() and not FORA.intersection(p.parts))


def main():
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else AQUI
    pacote = destino / f"quem-canta-{VERSAO}-windows.zip"
    raiz = f"quem-canta-{VERSAO}/"
    with zipfile.ZipFile(pacote, "w", zipfile.ZIP_DEFLATED) as z:
        for caminho in arquivos():
            conteudo = caminho.read_bytes()
            if caminho.suffix.lower() in TEXTO_DO_WINDOWS:
                conteudo = conteudo.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            z.writestr(raiz + caminho.relative_to(AQUI).as_posix(), conteudo)
    resumo = hashlib.sha256(pacote.read_bytes()).hexdigest()
    print(f"{pacote}  ({pacote.stat().st_size / 1024:.0f} KB, {len(zipfile.ZipFile(pacote).namelist())} arquivos)")
    publicada = {"versao": VERSAO, "url": endereco_do_pacote(VERSAO), "sha256": resumo, "notas": " ".join(sys.argv[2:])}
    (AQUI / "versao-publicada.json").write_text(json.dumps(publicada, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("versao-publicada.json gravado:")
    print(json.dumps(publicada, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
