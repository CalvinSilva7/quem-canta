"""Palco MP3 (palcomp3.com.br): o compositor aparece na linha "Composição:", logo abaixo do título da música.

O site abre sem login. A música é achada pela busca do próprio site (a caixa "O que você quer ouvir?", na aba
"Músicas"), que devolve o título, o artista e o endereço de cada resultado. O print é o do escritório: a
página da música, com o título, a linha de composição e o tocador embaixo.

A linha de composição é cortada pelo site quando os nomes não cabem ("Fulano de T... Ver tudo"). A lista
inteira está nos dados da própria página (o campo de compositor daquela música) e é ela que vale para a
leitura; mas o print da página, sozinho, não mostra todos os nomes. Nesses casos o app tira um segundo print,
da página de créditos que o "Ver tudo" abre (creditos.palcomp3.com.br), depois que ela termina de carregar.

Sem linha de composição na página, a música fica sem crédito; se a página não for a da música pedida, é erro.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cantor.matching import normalizar

from . import captura

SITE = "https://www.palcomp3.com.br/"
PLATAFORMA = "PALCO MP3"
SEM_AUTOR = ("desconhecido", "desconhecida", "nao informado", "nao informada", "sem informacao", "-")

# A caixa de busca: os resultados são links "/<artista>/<música>/" com o título e o artista no mesmo bloco.
_LER_RESULTADOS = """() => {
  const campos = [...document.querySelectorAll('#search-container input[type="text"], input[placeholder*="quer ouvir"]')].filter(c => c.getBoundingClientRect().width > 0);
  const campo = campos.find(c => c.closest('#search-container')) || campos[0];
  const base = campo ? campo.getBoundingClientRect().bottom : 100;
  const vistos = {}, saida = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '', caixa = a.getBoundingClientRect();
    if (!/^\\/[^\\/]+\\/[^\\/]+\\/?$/.test(href) || /^\\/(mp3|estilo|top|playlist|podcast|busca)\\//.test(href)) continue;
    if (!caixa.width || caixa.top < base || caixa.top > innerHeight - 110) continue;  // fora da caixa de busca (ou no tocador)
    let bloco = a;
    for (let i = 0; i < 4 && bloco.parentElement && (bloco.innerText || '').split('\\n').filter(l => l.trim()).length < 2; i++) bloco = bloco.parentElement;
    const linhas = (bloco.innerText || '').split('\\n').map(l => l.trim()).filter(Boolean);
    if (linhas.length < 2 || vistos[href]) continue;
    vistos[href] = true;
    saida.push({endereco: href, titulo: linhas[0], artista: linhas[1]});
  }
  return saida;
}"""
_ONDE_ESTA_A_ABA = """(nome) => {
  const campos = [...document.querySelectorAll('#search-container input[type="text"], input[placeholder*="quer ouvir"]')].filter(c => c.getBoundingClientRect().width > 0);
  const campo = campos.find(c => c.closest('#search-container')) || campos[0];
  const base = campo ? campo.getBoundingClientRect().bottom : 100;
  const aba = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === nome && b.getBoundingClientRect().top >= base - 4
    && b.getBoundingClientRect().width > 0);
  if (!aba) return null; const q = aba.getBoundingClientRect(); return {x: q.x + q.width / 2, y: q.y + q.height / 2}; }"""
# A página da música: o título, o artista, a linha de composição como aparece e o endereço do "Ver tudo".
_LER_MUSICA = """() => {
  const linha = [...document.querySelectorAll('article span, article p, article div')].filter(e => /^Composi[cç][aã]o\\s*:/i.test((e.textContent || '').trim())
    && (e.textContent || '').length < 400).pop();
  const artigo = (linha && linha.closest('article')) || document.querySelector('article');
  const titulo = artigo ? artigo.querySelector('h1') : null;
  const ver = linha ? linha.querySelector('a[href*="creditos.palcomp3"]') : null;
  const artista = [...document.querySelectorAll('h1')].find(h => !artigo || !artigo.contains(h));
  let exibido = linha ? linha.textContent : '';
  if (ver) exibido = exibido.replace(ver.textContent, '');
  return {titulo: titulo ? titulo.textContent.trim() : '', artista: artista ? artista.textContent.trim() : '',
          linha: exibido.replace(/^\\s*Composi[cç][aã]o\\s*:/i, '').trim(), ver_tudo: ver ? ver.getAttribute('href') : ''};
}"""


@dataclass
class Candidato:
    endereco: str  # "/<artista>/<música>/"
    titulo: str = ""
    interprete: str = ""

    @property
    def faixa(self):
        return self.endereco.strip("/").replace("/", "--")

    @property
    def link(self):
        return SITE + self.endereco.strip("/") + "/"


@dataclass
class Leitura:
    faixa: str
    link: str = ""
    coleta: str = "ok"  # "ok", "erro" ou "indisponivel"
    erro: str = ""
    titulo: str = ""
    interprete: str = ""
    linha: str = ""  # a linha de composição como aparece na página (sem o "Composição:")
    cortada: bool = False
    creditos: list[str] = field(default_factory=list)
    provas: list[str] = field(default_factory=list)
    lido_em: str = ""


def interpretar_resultados(itens) -> list[Candidato]:
    """Os resultados da caixa de busca -> candidatos, sem repetição."""
    candidatos, vistos = [], set()
    for item in itens or []:
        endereco, titulo = (item.get("endereco") or "").strip(), (item.get("titulo") or "").strip()
        if not re.fullmatch(r"/[^/]+/[^/]+/?", endereco) or not titulo or endereco.rstrip("/") in vistos:
            continue
        vistos.add(endereco.rstrip("/"))
        candidatos.append(Candidato(endereco, titulo, (item.get("artista") or "").strip()))
    return candidatos


def nomes(texto: str) -> list[str]:
    """Os nomes de uma linha de composição: separados por vírgula, barra ou ponto e vírgula. "Desconhecido" não é nome."""
    partes = [p.strip(" .") for p in re.split(r"[,/;]", texto or "")]
    return list(dict.fromkeys(p for p in partes if p and normalizar(p) not in SEM_AUTOR))


def compositor_nos_dados(html: str, codigo: str) -> str | None:
    """O campo de compositor daquela música nos dados que a própria página traz (a lista inteira, sem corte).

    None quando a página não traz o dado dessa música; texto vazio quando traz e está em branco.
    """
    achado = re.search(r'\\?"musicID\\?"\s*:\s*' + re.escape(str(codigo)) + r'\s*,\s*\\?"composer\\?"\s*:\s*(null|\\?"(.*?)\\?")\s*,\s*\\?"title\\?"',
                       html or "", re.S)
    if not achado:
        return None
    if achado[1] == "null":
        return ""
    try:  # o texto está escrito como dentro de um JSON (às vezes, de um JSON dentro de outro)
        valor = json.loads('"' + achado[2].replace('\\\\', '\\') + '"')
    except ValueError:
        valor = achado[2]
    return valor.strip()


def interpretar_linha(linha: str, nos_dados: str | None) -> tuple[list[str], bool, str]:
    """(nomes, a linha estava cortada?, erro). Linha inteira vale como está. Cortada ("Fulano de T..."), vale a lista
    dos dados da página, desde que comece pelo mesmo trecho que aparece: senão é erro, e não um palpite."""
    limpa = (linha or "").strip()
    cortada = limpa.endswith("...") or limpa.endswith("…")
    if not cortada:
        return nomes(limpa), False, ""
    visto = normalizar(limpa.rstrip(".… "))
    if not nos_dados or not normalizar(nos_dados).startswith(visto):
        return [], True, "a linha de composição está cortada na página e os dados dela não trazem a lista inteira"
    return nomes(nos_dados), True, ""


class Palco:
    """Uso: `Palco(navegador, pasta)`; `buscar(consulta)` e `ler(candidato, obra)`."""

    def __init__(self, navegador, pasta, so_o_que_ja_foi_lido=False):
        self.nav, self.pasta = navegador, Path(pasta)
        self.cache = self.pasta / "leituras"
        self.so_o_que_ja_foi_lido = so_o_que_ja_foi_lido

    def _no_site(self):
        pagina = self.nav.pagina
        if "palcomp3.com.br" not in pagina.url or "creditos." in pagina.url:
            self.nav.ir(SITE, pausa=(4, 7))
            pagina.wait_for_selector('input[placeholder*="quer ouvir"]', timeout=30000)
            pagina.wait_for_timeout(1500)
        self.nav.clicar_se_houver(nome="Ok, entendi")

    def buscar(self, consulta: str) -> list[Candidato]:
        arquivo = self.cache / "_buscas.json"
        guardadas = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}
        if consulta in guardadas:
            return [Candidato(**c) for c in guardadas[consulta]]
        if self.so_o_que_ja_foi_lido:
            return []
        if self.nav.filtro:
            self.nav.filtro.conferir(consulta)
        from .navegador import Bloqueio

        try:
            candidatos = self._buscar(consulta)
        except (Bloqueio, captura.PaginaTraduzida):
            raise
        except Exception:  # a caixa de busca não respondeu: tenta uma vez de novo, a partir da página inicial
            self.nav.ir(SITE, pausa=(4, 7))
            self.nav.pagina.wait_for_timeout(3000)
            try:
                candidatos = self._buscar(consulta)
            except (Bloqueio, captura.PaginaTraduzida):
                raise
            except Exception:
                return []  # não guarda: a próxima coleta tenta de novo
        guardadas[consulta] = [asdict(c) for c in candidatos]
        self.cache.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(guardadas, ensure_ascii=False), encoding="utf-8")
        return candidatos

    def _buscar(self, consulta: str) -> list[Candidato]:
        self._no_site()
        pagina = self.nav.pagina
        self.nav.navegacoes += 1
        # A busca abre numa janela por cima da página, com um campo próprio: se ela ficou aberta, é nele que se digita.
        da_janela = pagina.locator('#search-container input[type="text"]')
        campo = da_janela.first if da_janela.count() and da_janela.first.is_visible() else pagina.locator('input[placeholder*="quer ouvir"]').first
        campo.click(timeout=8000)
        pagina.keyboard.press("ControlOrMeta+a")
        pagina.keyboard.press("Backspace")
        pagina.keyboard.type(consulta, delay=45)
        pagina.wait_for_timeout(3500)
        aba = pagina.evaluate(_ONDE_ESTA_A_ABA, "Músicas")
        if aba:  # a aba só das músicas traz mais resultados do que os "principais"
            pagina.mouse.click(aba["x"], aba["y"])
            pagina.wait_for_timeout(2500)
        self.nav.conferir(pagina.evaluate("document.body.innerText.slice(0, 1500)"))
        return interpretar_resultados(pagina.evaluate(_LER_RESULTADOS))

    def ler(self, cand: Candidato, obra: str) -> Leitura:
        """Abre a página da música, lê a linha de composição e tira o print. Leitura completa fica guardada."""
        arquivo = self.cache / f"{cand.faixa}.json"
        if arquivo.exists():
            guardada = Leitura(**json.loads(arquivo.read_text(encoding="utf-8")))
            if captura.serve(self.pasta, guardada.provas) or self.so_o_que_ja_foi_lido:  # print sem tela inteira é refeito
                return guardada
        leitura = Leitura(faixa=cand.faixa, link=cand.link, titulo=cand.titulo, interprete=cand.interprete,
                          lido_em=captura.agora().isoformat(timespec="seconds"))
        if self.so_o_que_ja_foi_lido:
            leitura.coleta, leitura.erro = "erro", "faixa ainda não lida: a coleta foi interrompida antes dela"
            return leitura
        from .navegador import Bloqueio

        try:
            self._ler(leitura, cand, obra)
        except (Bloqueio, captura.PaginaTraduzida):
            raise
        except Exception as e:  # timeout, elemento que sumiu: erro técnico, nunca "sem créditos"
            leitura.coleta, leitura.erro = "erro", f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
        if leitura.coleta == "ok":
            self.cache.mkdir(parents=True, exist_ok=True)
            arquivo.write_text(json.dumps(asdict(leitura), ensure_ascii=False), encoding="utf-8")
        return leitura

    def _ler(self, leitura: Leitura, cand: Candidato, obra: str):
        pagina = self.nav.pagina
        self.nav.ir(cand.link, pausa=(4, 7))
        try:
            pagina.wait_for_selector("article h1", timeout=25000)
        except Exception:
            self.nav.conferir(pagina.evaluate("document.body.innerText.slice(0, 1500)"))
            leitura.coleta, leitura.erro = "indisponivel", "a página da música não carregou"
            return
        pagina.wait_for_timeout(2500)
        self.nav.clicar_se_houver(nome="Ok, entendi")
        lido = pagina.evaluate(_LER_MUSICA)
        if cand.endereco.strip("/").lower() not in pagina.url.lower():
            # O site leva alguns endereços de música para outra música do mesmo artista (a pedida saiu do ar).
            leitura.coleta = "indisponivel"
            leitura.erro = f'o Palco MP3 não mantém a página desta música: leva para outra ("{lido["titulo"]}")'
            return
        if normalizar(lido["titulo"]) != normalizar(cand.titulo):
            leitura.coleta, leitura.erro = "erro", f'a página aberta é de "{lido["titulo"]}", e não da música pedida'
            return
        leitura.titulo, leitura.interprete, leitura.linha = lido["titulo"], lido["artista"] or cand.interprete, lido["linha"]
        codigo = (re.search(r"/faixa/(\d+)", lido["ver_tudo"] or "") or [None, ""])[1]
        leitura.creditos, leitura.cortada, erro = interpretar_linha(lido["linha"], compositor_nos_dados(pagina.content(), codigo) if codigo else None)
        if erro:
            leitura.coleta, leitura.erro = "erro", erro
            return
        self.nav.conferir()
        pagina.evaluate("() => window.scrollTo(0, 0)")
        pagina.mouse.move(8, 400)
        pagina.wait_for_timeout(600)
        exibido = {"titulo": leitura.titulo, "interprete": leitura.interprete, "linha_de_composicao": leitura.linha,
                   "compositores": leitura.creditos, "linha_cortada_pelo_site": leitura.cortada}
        if not leitura.creditos:
            exibido["constatacao"] = "a página da música não traz nome nenhum na linha de composição"
        leitura.provas.append(captura.capturar(pagina, self.pasta, obra, leitura.interprete, "palco-mp3", exibido, "musica")["captura"])
        if leitura.cortada and lido["ver_tudo"]:
            # A página corta os nomes: o segundo print é o da página de créditos, que mostra todos.
            self.nav.ir(lido["ver_tudo"], pausa=(4, 7))
            for _ in range(15):  # ela confere os créditos aos poucos, com um aviso de andamento
                pagina.wait_for_timeout(3000)
                texto = normalizar(pagina.evaluate("document.body.innerText.slice(0, 4000)"))
                if "verificando os creditos" not in texto and "etapas" not in texto:
                    break
            self.nav.conferir()
            exibido = dict(exibido, pagina="créditos e informações autorais (o que o \"Ver tudo\" da música abre)")
            leitura.provas.append(captura.capturar(pagina, self.pasta, obra, leitura.interprete, "palco-mp3", exibido, "creditos")["captura"])
