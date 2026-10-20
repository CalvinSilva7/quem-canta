"""Réplica: busca alternativa na consulta web da Socinpro, para a obra que não aparece no Ecadnet.

Pedido do jurídico (20/10/2026): na maioria dos casos a obra está no Ecadnet; quando não está, a Socinpro (associação
ligada ao ECAD) às vezes tem o cadastro, e o print dela entra como prova complementar. Só para esses casos: não é
para coletar sempre.

A consulta é pública, mas pede o "não sou um robô" (reCAPTCHA) a cada pesquisa. Por isso, como no ISWC, o app usa o
Google Chrome da pessoa (perfil e porta próprios) e nunca resolve a verificação: ele preenche o título e clica em
Pesquisar; a pessoa marca o "não sou um robô" e clica em Validar; o app acha a obra nos resultados pelo código
ECAD OBRA (o mesmo do relatório), abre os detalhes e, quando a pessoa pede, fotografa o monitor inteiro.

O print fica na pasta do Ecadnet, com a etapa do Ecadnet: entra no PDF único do Ecadnet, no lugar da obra.
"""

import base64
import json
import time
from pathlib import Path


from cantor.matching import normalizar

from . import captura, ecadnet, iswc
from .replica import nomes_na_pagina

SITE = "https://associado.socinpro.org.br/portal-web/pages/public/consulta.xhtml"
PLATAFORMA = "Socinpro"
PORTA = 9341
NOME = "o Chrome da Socinpro"

# Preenche só o título (os outros campos em branco, "Começando com...") e clica em Pesquisar
_PESQUISAR = """(titulo) => {
  // offsetParent não serve: as janelas do site (verificação, detalhes) ficam em posição fixa na tela
  const visivel = e => !!e && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden' && e.getClientRects().length > 0;
  const rotulo = [...document.querySelectorAll('label')].find(l => visivel(l) && l.innerText.trim() === 'Titulo da Obra');
  const campos = [...document.querySelectorAll('form input[type=text]')].filter(e => visivel(e) && !e.id.endsWith('_focus'));
  const campo = (rotulo && rotulo.htmlFor && document.getElementById(rotulo.htmlFor)) || campos[0];
  if (!campo) return 'não achei o campo do título';
  campos.forEach(c => { if (c !== campo) c.value = ''; });
  campo.focus();
  campo.value = titulo;
  campo.dispatchEvent(new Event('input', {bubbles: true}));
  campo.dispatchEvent(new Event('change', {bubbles: true}));
  const botao = [...document.querySelectorAll('button')].find(b => visivel(b) && b.innerText.trim() === 'Pesquisar');
  if (!botao) return 'não achei o botão Pesquisar';
  botao.click();
  return '';
}"""

# O que está na tela: verificação aberta? resultados (com o código ECAD OBRA de cada linha)? detalhes da obra abertos?
_ESTADO = """() => {
  // offsetParent não serve: as janelas do site (verificação, detalhes) ficam em posição fixa na tela
  const visivel = e => !!e && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden' && e.getClientRects().length > 0;
  const caixa = document.querySelector('#dialogCaptcha');
  const detalhes = document.querySelector('#dialogObra');
  const campos = {};
  if (visivel(detalhes)) {
    detalhes.querySelectorAll('label').forEach(l => {
      const proximo = l.parentElement && l.parentElement.querySelector('input');
      if (proximo) campos[l.innerText.trim()] = (proximo.value || '').trim();
    });
  }
  const linhas = [];
  for (const tabela of document.querySelectorAll('table')) {
    const cabecalhos = [...tabela.querySelectorAll('thead th')].map(th => th.innerText.trim().toUpperCase());
    const coluna = cabecalhos.findIndex(c => c.startsWith('ECAD OBRA'));
    if (coluna < 0 || !visivel(tabela)) continue;
    tabela.querySelectorAll('tbody tr').forEach((tr, i) => {
      const celulas = [...tr.querySelectorAll('td')];
      if (celulas.length > coluna) linhas.push({i, codigo: celulas[coluna].innerText.trim(), texto: tr.innerText.replace(/\\s+/g, ' ').trim()});
    });
  }
  // O título que está no campo de busca: diz de qual obra são a verificação e os resultados que estão na tela
  const rotulo = [...document.querySelectorAll('label')].find(l => visivel(l) && l.innerText.trim() === 'Titulo da Obra');
  const campo = rotulo && rotulo.parentElement && rotulo.parentElement.querySelector('input[type=text]');
  return {verificacao: visivel(caixa), detalhes: visivel(detalhes), campos, titulo_pesquisado: campo ? campo.value.trim() : '',
          texto_dos_detalhes: visivel(detalhes) ? detalhes.innerText : '', linhas, url: location.href};
}"""

# Abre os detalhes da linha cujo código ECAD OBRA é este (o botão da lupa da linha)
_ABRIR_DETALHES = """(codigo) => {
  for (const tabela of document.querySelectorAll('table')) {
    const cabecalhos = [...tabela.querySelectorAll('thead th')].map(th => th.innerText.trim().toUpperCase());
    const coluna = cabecalhos.findIndex(c => c.startsWith('ECAD OBRA'));
    if (coluna < 0) continue;
    for (const tr of tabela.querySelectorAll('tbody tr')) {
      const celulas = [...tr.querySelectorAll('td')];
      if (celulas.length > coluna && celulas[coluna].innerText.trim() === codigo) {
        const botao = tr.querySelector('button, a');
        if (!botao) return 'a linha da obra não tem o botão de detalhes';
        botao.click();
        return '';
      }
    }
  }
  return 'a obra não está nos resultados';
}"""


def perfil_do_chrome(pasta_do_caso) -> Path:
    return Path(pasta_do_caso).parent.parent / "perfil-socinpro-chrome"


def titulo_para_buscar(titulo: str) -> str:
    """O título sem o que vem entre parênteses (o ECAD põe ali o título alternativo; a busca é "Começando com")."""
    return " ".join(str(titulo or "").split("(")[0].split()) or str(titulo or "")


def chrome_aberto(porta=PORTA) -> bool:
    return iswc.chrome_aberto(porta)


def fechar_chrome(porta=PORTA):
    iswc.fechar_chrome(porta)


def _aba(porta=PORTA):
    alvos = iswc._alvos(porta)
    if not alvos:
        return None
    return next((a for a in alvos if "socinpro.org.br" in a.get("url", "")), alvos[0])


def _avaliar(cdp, expressao: str, *argumentos):
    chamada = f"({expressao})({', '.join(json.dumps(a) for a in argumentos)})"
    resposta = cdp("Runtime.evaluate", expression=chamada, returnByValue=True, awaitPromise=True)
    if "exceptionDetails" in resposta:
        raise RuntimeError(resposta["exceptionDetails"].get("text", "erro na página"))
    return resposta.get("result", {}).get("value")


def estado(porta=PORTA) -> dict | None:
    aba = _aba(porta)
    if aba is None:
        return None
    cdp, conexao = iswc._comandos(aba)
    try:
        return _avaliar(cdp, _ESTADO)
    finally:
        conexao.close()


def pesquisa_desta_obra(na_tela: dict, titulo: str) -> bool:
    """A verificação ou os resultados que estão na tela são da pesquisa deste título? (Senão, são da obra anterior e
    é preciso pesquisar de novo.)"""
    return bool(na_tela) and normalizar(na_tela.get("titulo_pesquisado", "")) == normalizar(titulo_para_buscar(titulo))


def pesquisar(titulo: str, perfil, porta=PORTA, espera_da_pagina=30) -> dict:
    """Abre a consulta (no Chrome da Socinpro), preenche o título e clica em Pesquisar. Devolve {"ok", "motivo", "onde"}."""
    onde = iswc.abrir_no_chrome(SITE, perfil, porta, NOME)
    fim = time.monotonic() + espera_da_pagina
    motivo = "a consulta da Socinpro não carregou"
    while time.monotonic() < fim:  # o Chrome pode estar abrindo agora
        time.sleep(1)
        aba = _aba(porta)
        if aba is None or "socinpro.org.br" not in aba.get("url", ""):
            continue
        cdp, conexao = iswc._comandos(aba)
        try:
            if _avaliar(cdp, "() => document.readyState") != "complete":
                continue
            time.sleep(0.5)  # a página monta os campos depois de carregar
            motivo = _avaliar(cdp, _PESQUISAR, titulo_para_buscar(titulo))
        except Exception as e:
            motivo = f"a página não respondeu ({type(e).__name__})"
            continue
        finally:
            conexao.close()
        if not motivo:
            break
    return {"ok": not motivo, "motivo": motivo, "onde": onde}


def esperar_e_abrir(codigo: str, porta=PORTA, espera=180) -> dict:
    """Espera a pessoa resolver o "não sou um robô" e abre os detalhes da obra deste código ECAD.

    Devolve {"ok", "motivo", "codigos"}: sem a obra nos resultados, `codigos` diz quais vieram (para a tela mostrar)."""
    fim = time.monotonic() + espera
    ultimo = {}
    while time.monotonic() < fim:
        time.sleep(1)
        try:
            ultimo = estado(porta) or {}
        except Exception:
            continue
        if ultimo.get("detalhes") and ultimo.get("campos", {}).get("ECAD OBRA") == codigo:
            return {"ok": True, "motivo": ""}
        if ultimo.get("verificacao") or not ultimo.get("linhas"):
            continue  # a pessoa ainda está no "não sou um robô" (ou a pesquisa não voltou)
        aba = _aba(porta)
        cdp, conexao = iswc._comandos(aba)
        try:
            motivo = _avaliar(cdp, _ABRIR_DETALHES, codigo)
        finally:
            conexao.close()
        if motivo:
            return {"ok": False, "motivo": motivo, "codigos": [l["codigo"] for l in ultimo["linhas"]]}
        espera_dos_detalhes = time.monotonic() + 15
        while time.monotonic() < espera_dos_detalhes:
            time.sleep(0.5)
            ultimo = estado(porta) or {}
            if ultimo.get("detalhes") and ultimo.get("campos", {}).get("ECAD OBRA") == codigo:
                return {"ok": True, "motivo": ""}
        return {"ok": False, "motivo": "cliquei na lupa da obra, mas os detalhes não abriram: abra na janela e clique em Fotografar"}
    if ultimo.get("verificacao"):
        return {"ok": False, "motivo": "o \"não sou um robô\" não foi resolvido a tempo: resolva, clique em Validar e depois em "
                                       "**Buscar na Socinpro** de novo"}
    return {"ok": False, "motivo": "a pesquisa não trouxe resultados (a obra pode não estar na Socinpro)"}


def conferir_detalhes(dados: dict, codigo: str, titulo: str) -> str:
    """Vazio se a tela mostra os detalhes desta obra; senão, o motivo."""
    if not dados.get("detalhes"):
        return "os detalhes da obra não estão abertos na janela da Socinpro (clique na lupa da obra)"
    if dados.get("campos", {}).get("ECAD OBRA") != codigo:
        aberto = dados.get("campos", {}).get("ECAD OBRA") or "sem código"
        return f"os detalhes abertos são de outra obra (ECAD OBRA {aberto}, e esta é {codigo})"
    return ""


def _trazer_para_frente(titulo: str) -> None:
    """Põe a janela do Chrome com esta página por cima das outras (no Windows), para o print do monitor não sair
    coberto. Sem tirar do tamanho em que está (maximizada continua maximizada)."""
    import sys

    if sys.platform != "win32" or not titulo:
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        for janela in iswc._janelas_do_chrome():
            texto = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(janela, texto, 512)
            if texto.value.startswith(titulo[:60]):
                user32.ShowWindow(janela, 9 if user32.IsIconic(janela) else 5)  # 9: reabre a minimizada; 5: só mostra
                # O Windows só deixa trazer janela para a frente logo depois de uma tecla; o Alt solto não faz nada
                user32.keybd_event(0x12, 0, 0, 0)
                user32.SetForegroundWindow(janela)
                user32.keybd_event(0x12, 0, 2, 0)
                return
    except Exception:
        pass


def fotografar(pasta, obra: str, codigo: str, nomes=(), porta=PORTA) -> dict:
    """Print do monitor inteiro com os detalhes da obra abertos, mais o print e o HTML da página e a ficha.
    O print do monitor é obrigatório (mostra a data e a hora): sem ele, nada é guardado.
    Devolve {"ok", "ficha" ou "motivo", "titular_na_pagina"}, como a réplica do Ecadnet."""
    from . import tela
    from PIL import Image
    import io

    aba = _aba(porta)
    if aba is None:
        return {"ok": False, "motivo": f"{NOME} não está aberto: clique em **Buscar na Socinpro**"}
    cdp, conexao = iswc._comandos(aba)
    try:
        dados = _avaliar(cdp, _ESTADO)
        motivo = conferir_detalhes(dados, codigo, obra)
        if motivo:
            return {"ok": False, "motivo": motivo}
        cdp("Page.bringToFront")
        _trazer_para_frente(aba.get("title", ""))
        time.sleep(0.8)

        def tentar():
            pagina = base64.b64decode(cdp("Page.captureScreenshot", format="png")["data"])
            medidas = _avaliar(cdp, captura._MEDIDAS_DA_JANELA)
            esquerda, topo, escala = captura.posicao_da_pagina(medidas, Image.open(io.BytesIO(pagina)).width)
            return pagina, *tela.fotografar_na_posicao(pagina, esquerda, topo, escala)

        pagina, foto, sobre_a_tela = tentar()
        if foto is None:
            time.sleep(1)
            pagina, foto, sobre_a_tela = tentar()
        html = _avaliar(cdp, "() => document.documentElement.outerHTML").encode("utf-8")
    finally:
        conexao.close()
    if foto is None:
        return {"ok": False, "motivo": f"não consegui fotografar o monitor inteiro ({sobre_a_tela.get('erro', 'motivo desconhecido')}). "
                                       f"Deixe {NOME} à vista, sem nada por cima, e clique em Fotografar de novo"}

    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    quando = captura.agora()
    base = captura.nome_base(obra, "", PLATAFORMA, quando) + f"_{ecadnet.ETAPA}"
    extra = 2
    while (pasta / f"{base}.json").exists():
        base = f"{base.rsplit('__', 1)[0]}__{extra}"
        extra += 1
    texto = dados.get("texto_dos_detalhes", "")
    nomes_achados = nomes_na_pagina(texto, nomes)
    arquivos = {"png": f"{base}.png", "html": f"{base}.html", "tela": f"{base}_tela.png"}
    (pasta / arquivos["png"]).write_bytes(pagina)
    (pasta / arquivos["html"]).write_bytes(html)
    (pasta / arquivos["tela"]).write_bytes(foto)
    ficha = {
        "captura": base, "url": dados.get("url"), "plataforma": PLATAFORMA, "obra": obra, "interprete": "",
        "capturado_em": quando.isoformat(timespec="seconds"), "fuso": captura.FUSO, "versao_do_app": captura.VERSAO_DO_APP,
        "como": "Consulta web pública da Socinpro, busca alternativa da réplica (a obra não estava no Ecadnet). O app "
                "preencheu o título e abriu os detalhes; o \"não sou um robô\" foi resolvido pela pessoa, no Chrome dela",
        "exibido": {"codigo_da_obra": codigo, "fonte": PLATAFORMA, "titulo": obra, "campos": dados.get("campos", {}),
                    "nomes_do_titular_na_pagina": nomes_achados, "texto": texto[:4000]},
        "tela_inteira": sobre_a_tela, "carimbo_no_print": False, "arquivos": arquivos,
        "sha256": {chave: captura.sha256((pasta / nome).read_bytes()) for chave, nome in arquivos.items()},
    }
    (pasta / f"{base}.json").write_text(json.dumps(ficha, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "ficha": ficha, "titular_na_pagina": bool(nomes_achados)}
