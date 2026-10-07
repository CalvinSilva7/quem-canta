"""Consulta ao MusicBrainz (ws/2) e ao Deezer para descobrir quem canta uma música."""

import itertools
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field

import requests

from .banco import CAMINHO_PADRAO
from .iswc import normalizar_iswc
from .matching import (
    chave_consulta,
    dividir_compositores,
    nomes_parecidos,
    normalizar,
    titulos_parecidos,
)

VERSAO = "0.2"
# Muda quando a lógica muda: respostas em cache de versões anteriores são refeitas.
VERSAO_CACHE = 12
MB_API = "https://musicbrainz.org/ws/2/"
MB_SITE = "https://musicbrainz.org"
DEEZER_API = "https://api.deezer.com/search"
DEEZER_BASE = "https://api.deezer.com/"
DEEZER_FAIXA_POR_ISRC = "https://api.deezer.com/track/isrc:"
DEEZER_SEM_DADOS = 800  # código de "não encontrado" no corpo da resposta
CREDITS_API = "https://api.credits.fm/v1/"
INTERVALO_CREDITS = 0.3
ISRCS_CONFERIDOS_NO_DEEZER = 10  # as gravações de ISRC mais antigo, por linha
ATRIBUICAO_CREDITS = (
    "Dados de obras e gravações por ISWC: Credits.fm (https://credits.fm), "
    "licença CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)"
)

INTERVALO_MB = 1.1  # segundos entre requisições (o limite do MusicBrainz é 1 req/s)
STATUS_REPETIVEIS = {429, 500, 502, 503, 504}
DEEZER_COTA_EXCEDIDA = 4
MAX_OBRAS = 3  # obras compatíveis cujas gravações são consultadas
MAX_COMPOSITORES_DEEZER = 3  # compositores da linha pesquisados no Deezer
# Para datar a "gravação mais antiga" só valem lançamentos com status Official
# (bootleg e promocional ficam de fora) cujo grupo não seja demo. Ao vivo oficial
# conta: no Brasil vários lançamentos originais são acústicos ao vivo.
TIPOS_QUE_NAO_DATAM = {"demo"}
# Lançamento sem status cadastrado no MusicBrainz não é bootleg: só fica de fora o que está marcado assim.
STATUS_NAO_OFICIAIS = {"bootleg", "promotion", "pseudo-release", "withdrawn", "cancelled"}
# Data de cadastro da obra (vem da planilha e só é usada aqui dentro, nunca vai para as APIs): se a
# gravação mais antiga encontrada é de mais de ANOS_APOS_CADASTRO anos depois, a original deve estar faltando.
ANOS_APOS_CADASTRO = 3
MAX_PAGINAS_CATALOGO = 30  # 100 obras por página ao listar as obras de um compositor
MAX_VERIFICACOES = 8  # gravações conferidas por linha (1 consulta cada)
# Discografia de um nome artístico do compositor (modo relatório), listada uma vez e guardada em cache.
MAX_ARTISTAS_DISCOGRAFIA = 3  # artistas com esse nome no Deezer (e ids no MusicBrainz)
MAX_ALBUNS_DISCOGRAFIA = 300
MAX_PAGINAS_DISCOGRAFIA = 10  # 100 gravações por página no MusicBrainz
MAX_CONSULTAS_SUGESTAO = 12  # combinações de palavras do nome civil pesquisadas ao sugerir nomes artísticos
LANCAMENTOS_POR_CONSULTA = 100
MAX_FAIXAS_DEEZER = 25  # faixas com o título pedido aproveitadas por consulta

# Pesos da pontuação dos candidatos quando a busca é pelo título (sem obra
# confirmada). Sem compositor, o máximo é 80.
PESO_COMPOSITOR = 100  # nome bate com um compositor da linha; maior que a soma dos outros, então sempre vence
PESO_DEEZER = 40  # 1º resultado do Deezer vale 40 e cai linearmente até o 25º; pesa mais que o MusicBrainz
PESO_MB = 25  # 2,5 por gravação no MusicBrainz, até GRAVACOES_TETO; contagem absoluta, porque
# "1 gravação contra 3" não quer dizer nada, e "12 gravações" quer
GRAVACOES_TETO = 10
PESO_DATA = 15  # gravação datada mais antiga vale 15; a 2ª, 10; a 3ª, 5

# Versões que não são do intérprete da música: descartadas na busca por título.
MARCADORES = re.compile(
    r"\b(karaoke|remix|remixes|remixed|tribute|tributo|instrumental|cover|covers|made famous by|"
    r"originally performed|in the style of|backing tracks?|playback)\b"
)

TIPOS_AUTORIA = {"composer", "lyricist", "writer", "librettist"}
ARTISTAS_IGNORADOS = {"various artists", "various", "unknown", "no artist"}
NIVEIS = ["nao_encontrado", "baixa", "media", "alta"]
FONTE_MANUAL = "correção manual"
SEM_DATA = (9999, 99, 99)  # depois de qualquer data de verdade (ver _chave_data)
COLUNAS_SAIDA = [
    "cantor_sugerido", "confianca", "alternativas", "fonte_link", "observacao", "regra",
    "iswc_normalizado", "iswc_encontrado_em",
]

# Identificadores da coluna "regra": dizem qual caminho e quais sinais geraram a
# sugestão. São estáveis de propósito (servem para medir acerto por regra): ao
# mudar a lógica de uma regra, crie um identificador novo em vez de reaproveitar.
#
#   correcao_manual          correção salva pelo usuário
#   obra_compositor          obra confirmada; a gravação mais antiga é de um compositor
#   obra_compositor_parcial  ... é de uma dupla, grupo ou parceria em que um compositor só participa
#   obra_mais_gravado        ... é de quem mais gravou a obra (2 gravações ou mais)
#   obra_popular_deezer      ... é do artista mais popular no Deezer para o título
#   obra_sem_confirmacao     ... sem nenhum segundo sinal
#   obra_compositor_popular  mais antiga sem confirmação; sugerido o compositor, que é o mais popular no Deezer
#   obra_sem_data            obra confirmada, mas nenhuma gravação tem data
#   sem_obra_compositor      compositor não confirmado por obra; o candidato também é compositor da linha
#   sem_obra_palpite         compositor não confirmado por obra; melhor candidato pelo título
#   so_titulo_unico          linha só com título; um único artista
#   so_titulo_varios         linha só com título; vários artistas, escolhido pela pontuação
#   discografia              modo relatório: título achado na discografia de um nome artístico confirmado
#   obra_discografia         modo relatório: obra confirmada sem segundo sinal; sugerido o nome artístico
#                            confirmado, que tem o título na discografia antes da gravação mais antiga vinculada
#   palpite_titulo           modo relatório: vários artistas e o escolhido não tem relação com o compositor
#   nao_encontrado, sem_titulo, erro_de_rede, apagado_manualmente
#
# Sufixos, separados por "+":
#   varias_obras       mais de uma obra compatível (gravações somadas)
#   truncada           obra com mais gravações do que as analisadas
#   relatorio          modo relatório: o compositor da planilha não conta como evidência
#   catalogo           obra achada na lista de obras do compositor, com ele como autor
#   catalogo_sem_autor obra achada nessa lista, ligada a ele só por gravação (sem autor cadastrado)
#   ao_vivo            a gravação mais antiga só existe em lançamento ao vivo oficial
#   nao_oficial_ignorada  havia gravação mais antiga só em bootleg, promocional ou demo
#   status_nao_cadastrado  a gravação mais antiga só tem lançamentos sem status no MusicBrainz (contam como válidos)
#   cadastro_anterior  a obra foi cadastrada mais de 3 anos antes da gravação mais antiga encontrada (teto: média)
#   oficial_nao_verificado  o limite de verificações acabou antes de conferir todas as mais antigas
#   varios_nomes       o título está na discografia de mais de um nome artístico confirmado
#   empate             data mais antiga empatada com outro artista
#   empate_compositor  empate resolvido a favor de quem também é compositor
#   mb, deezer, mb_deezer  de onde veio o candidato na busca por título
#   deezer_falhou      uma fonte secundária (Deezer ou Credits.fm) não respondeu nesta linha
#   iswc_mb, iswc_credits, iswc_ambos  ISWC achado (com título compatível) no MusicBrainz, no Credits.fm ou nos dois
#   iswc_invalido, iswc_nao_encontrado, iswc_titulo_diverge  ISWC não usado, e por quê
#   fontes_concordam / fontes_discordam  MusicBrainz e Credits.fm apontam ou não o mesmo intérprete
#
#   iswc_isrc_mais_antigo  (base) obra só no Credits.fm: intérprete da gravação de ISRC mais antigo
SIGLA_FONTE = {"MusicBrainz": "mb", "Deezer": "deezer", "MusicBrainz e Deezer": "mb_deezer"}

log = logging.getLogger("quem_canta")

# Compartilhado entre todas as sessões do app: o limite é por IP, não por usuário.
_trava_mb = threading.Lock()
_ultima_mb = 0.0


def configurar_log(caminho=None):
    """Grava avisos e erros das APIs em dados/quem_canta.log."""
    if log.handlers:
        return
    caminho = caminho or CAMINHO_PADRAO.with_suffix(".log")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    arquivo = logging.FileHandler(caminho, encoding="utf-8")
    arquivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(arquivo)
    log.setLevel(logging.INFO)


class ErroDeRede(Exception):
    """A API não respondeu mesmo depois das novas tentativas."""


@dataclass
class Resultado:
    cantor_sugerido: str = ""
    confianca: str = "nao_encontrado"
    alternativas: list[str] = field(default_factory=list)
    fonte_link: str = ""
    observacao: str = ""
    regra: str = ""
    iswc_normalizado: str = ""
    iswc_encontrado_em: str = ""  # MB, Credits.fm, ambos, nenhum (vazio se a linha não tem ISWC)
    ano_gravacao: int = 0  # ano da gravação mais antiga em que a sugestão se baseou (0 = desconhecido); não vai para a planilha

    def para_linha(self) -> dict:
        linha = asdict(self)
        linha["alternativas"] = "; ".join(self.alternativas)
        return linha


def montar_user_agent(contato: str = "") -> str:
    contato = (contato or os.environ.get("QUEMCANTA_CONTATO", "")).strip()
    return f"QuemCanta/{VERSAO} ({contato or 'uso pessoal, sem contato configurado'})"


class ClienteHTTP:
    """GET com JSON, limite de 1 req/s para o MusicBrainz e retry com backoff."""

    def __init__(self, contato: str = "", tentativas: int = 4, espera_base: float = 2.0, timeout: float = 20):
        self.tentativas = tentativas
        self.espera_base = espera_base
        self.timeout = timeout
        self.sessao = requests.Session()
        self.sessao.headers.update({"User-Agent": montar_user_agent(contato), "Accept": "application/json"})

    def mb(self, recurso: str, **parametros) -> dict:
        return self._get(MB_API + recurso, {**parametros, "fmt": "json"}, limitar=True)

    def deezer(self, **parametros) -> dict:
        return self._get(DEEZER_API, parametros, limitar=False)

    def deezer_api(self, caminho: str, **parametros) -> dict:
        """GET em outro recurso do Deezer (busca de artista, álbuns do artista, faixas do álbum)."""
        return self._get(DEEZER_BASE + caminho, parametros, limitar=False)

    def deezer_isrc(self, isrc: str):
        """Faixa do Deezer com esse ISRC, ou None se o Deezer não tiver."""
        return self._get(DEEZER_FAIXA_POR_ISRC + isrc, {}, limitar=False, ausente_e_none=True)

    def credits(self, caminho: str, **parametros):
        """GET na API do Credits.fm; None quando o código não existe lá (HTTP 404)."""
        time.sleep(INTERVALO_CREDITS)
        return self._get(CREDITS_API + caminho, parametros, limitar=False, ausente_e_none=True)

    def _aguardar_vez(self):
        global _ultima_mb
        with _trava_mb:
            falta = INTERVALO_MB - (time.monotonic() - _ultima_mb)
            if falta > 0:
                time.sleep(falta)
            _ultima_mb = time.monotonic()

    def _get(self, url: str, parametros: dict, limitar: bool, ausente_e_none: bool = False):
        erro = ""
        for tentativa in range(self.tentativas):
            if tentativa:
                log.warning("tentativa %d falhou (%s) em %s %s", tentativa, erro, url, parametros)
                time.sleep(espera)
            espera = self.espera_base * 2**tentativa
            if limitar:
                self._aguardar_vez()
            try:
                resposta = self.sessao.get(url, params=parametros, timeout=self.timeout)
            except requests.RequestException as e:
                erro = type(e).__name__
                continue
            if resposta.status_code in STATUS_REPETIVEIS:
                erro = f"HTTP {resposta.status_code}"
                pedido = resposta.headers.get("Retry-After", "")
                if pedido.isdigit():
                    espera = max(espera, float(pedido))
                continue
            if resposta.status_code == 404 and ausente_e_none:
                return None
            if resposta.status_code >= 400:
                raise ErroDeRede(f"HTTP {resposta.status_code} em {url}")
            try:
                dados = resposta.json()
            except ValueError:
                erro = "resposta que não é JSON"
                continue
            # O Deezer devolve HTTP 200 com um objeto "error" no corpo.
            falha = dados.get("error") if isinstance(dados, dict) else None
            if isinstance(falha, dict):
                erro = f"Deezer: {falha.get('message', 'erro')}"
                if falha.get("code") == DEEZER_COTA_EXCEDIDA:
                    continue
                if falha.get("code") == DEEZER_SEM_DADOS and ausente_e_none:
                    return None
                raise ErroDeRede(erro)
            return dados
        raise ErroDeRede(f"{erro} após {self.tentativas} tentativas")


def _frase(texto: str) -> str:
    """Texto pronto para ir entre aspas em uma consulta Lucene."""
    return str(texto).replace("\\", " ").replace('"', " ").strip()


def _credito(gravacao: dict) -> str:
    """Junta o artist-credit como aparece no disco: "Stan Getz & João Gilberto"."""
    partes = gravacao.get("artist-credit") or []
    return "".join(p.get("name", "") + p.get("joinphrase", "") for p in partes).strip()


def _chave_data(data: str):
    """"1963-05" -> (1963, 5, 99). Partes ausentes vão para o fim do período."""
    partes = [int(p) for p in (data or "").split("-") if p.isdigit()][:3]
    return tuple(partes + [99] * (3 - len(partes))) if partes else None


def _empate(a: tuple, b: tuple) -> bool:
    """Mesmo ano e a precisão das datas não permite dizer qual veio primeiro."""
    return a[0] == b[0] and (a == b or 99 in a or 99 in b)


def _limitar(confianca: str, teto: str) -> str:
    return min(confianca, teto, key=NIVEIS.index)


def _versao_derivada(titulo_da_linha: str, *textos) -> bool:
    """Karaokê, remix, tributo etc., a não ser que a palavra faça parte do título pedido."""
    achados = set(MARCADORES.findall(normalizar(" ".join(t for t in textos if t))))
    return bool(achados - set(MARCADORES.findall(normalizar(titulo_da_linha))))


def _ano_do_isrc(isrc: str):
    """Ano de referência do ISRC (CC-XXX-AA-NNNNN), ou None se o código não tem esse formato."""
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}\d{7}", isrc or ""):
        return None
    aa = int(isrc[5:7])
    return 2000 + aa if aa <= time.localtime().tm_year % 100 else 1900 + aa


def _agrupar_por_artista(gravacoes: list[dict]) -> list[dict]:
    """Um candidato por artista, com a gravação mais antiga e os ids do MusicBrainz."""
    grupos = {}
    for g in gravacoes:
        nome = _credito(g)
        if not normalizar(nome) or normalizar(nome) in ARTISTAS_IGNORADOS:
            continue
        # Agrupa pelo id do artista: "Jorge Ben" e "Jorge Ben Jor" são o mesmo
        # artista creditado de dois jeitos.
        ids = {p["artist"]["id"] for p in g.get("artist-credit") or [] if "artist" in p}
        chave = tuple(sorted(ids)) or normalizar(nome)
        grupo = grupos.setdefault(
            chave,
            {"nome": nome, "gravacoes": 0, "data": None, "data_txt": "", "ids": ids, "ao_vivo": False,
             "sem_status": False, "link": f"{MB_SITE}/recording/{g['id']}"},
        )
        grupo["gravacoes"] += 1
        data = _chave_data(g.get("first-release-date"))
        if data and (grupo["data"] is None or data < grupo["data"]):
            grupo.update(
                data=data, data_txt=g["first-release-date"], link=f"{MB_SITE}/recording/{g['id']}",
                ao_vivo=bool(g.get("so_ao_vivo")), sem_status=bool(g.get("sem_status")),
            )
    return list(grupos.values())


class Buscador:
    def __init__(self, banco, cliente=None, usar_cache=True, usar_correcoes=True, max_paginas=15):
        self.banco = banco
        self.cliente = cliente or ClienteHTTP()
        self.usar_cache = usar_cache
        self.usar_correcoes = usar_correcoes
        self.max_paginas = max_paginas  # 100 gravações por página
        self._falhas = []  # APIs secundárias que falharam na linha atual
        self._artistas = {}  # nome normalizado -> {id: [nomes]}, para não reler o banco
        # Custo acumulado da verificação de lançamentos oficiais e do ISWC (para medir o impacto).
        self.consultas_oficiais = 0
        self.tempo_oficiais = 0.0
        self.tempo_iswc = 0.0
        # Modo relatório (ver preparar_relatorio): None ou
        # {"nome", "ids", "nomes", "obras", "artisticos", "discografia", "erro"}.
        self.relatorio = None

    # --- API pública --------------------------------------------------------

    def resolver(self, titulo, compositor="", iswc="", data_cadastro="") -> Resultado:
        """`data_cadastro` (da planilha) só entra na conferência local do fim: não vai para as APIs nem para o cache."""
        return self._conferir_cadastro(self._resolver(titulo, compositor, iswc), data_cadastro)

    @staticmethod
    def _conferir_cadastro(resultado: Resultado, data_cadastro) -> Resultado:
        """Obra cadastrada muito antes da gravação mais antiga encontrada: a original deve estar faltando na base."""
        cadastro = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", str(data_cadastro or ""))
        if not cadastro or not resultado.ano_gravacao or resultado.regra == "correcao_manual":
            return resultado
        if resultado.ano_gravacao - int(cadastro.group()) <= ANOS_APOS_CADASTRO:
            return resultado
        resultado.confianca = _limitar(resultado.confianca, "media")
        resultado.regra += "+cadastro_anterior"
        resultado.observacao += (
            f"; possível original ausente na base: a obra foi cadastrada mais de {ANOS_APOS_CADASTRO} anos "
            f"antes da gravação mais antiga encontrada ({resultado.ano_gravacao})"
        )
        return resultado

    def _resolver(self, titulo, compositor="", iswc="") -> Resultado:
        chave = chave_consulta(titulo, compositor)
        if not chave[0]:
            return Resultado(observacao="linha sem título", regra="sem_titulo")
        if self.usar_correcoes:
            cantor = self.banco.obter_correcao(*chave)
            if cantor:
                return self._resultado_manual(cantor)
        codigo, erro_iswc = normalizar_iswc(iswc)
        if self.relatorio:
            # No modo relatório a mesma linha pode ter outra resposta: cache separado.
            artisticos = ",".join(sorted(normalizar(n) for n in self.relatorio.get("artisticos", [])))
            chave = (chave[0], f"{chave[1]} |relatorio" + (f" |artisticos={artisticos}" if artisticos else ""))
        if codigo or erro_iswc:
            chave = (chave[0], f"{chave[1]} |iswc={codigo or 'invalido'}")
        if self.usar_cache:
            salvo = self.banco.obter_cache(*chave)
            if salvo and salvo.pop("v", None) == VERSAO_CACHE:
                return Resultado(**salvo)
        self._falhas = []
        try:
            resultado = self._consultar(str(titulo), str(compositor or ""), codigo)
        except ErroDeRede as e:
            # Não vai para o cache: na próxima execução a linha é tentada de novo.
            log.error("linha %r não resolvida: %s", titulo, e)
            return Resultado(observacao=f"erro de rede: {e}", regra="erro_de_rede")
        if erro_iswc:
            resultado.regra += "+iswc_invalido"
            resultado.observacao = "; ".join(x for x in [f"ISWC inválido ({erro_iswc}), ignorado", resultado.observacao] if x)
        if self.relatorio and resultado.cantor_sugerido:
            resultado.regra += "+relatorio"
        if self._falhas:
            # Resposta incompleta: avisa na observação e também não guarda no cache.
            resultado.observacao = "; ".join([resultado.observacao] + self._falhas)
            resultado.regra += "+deezer_falhou"
        else:
            self.banco.salvar_cache(*chave, {**asdict(resultado), "v": VERSAO_CACHE})
        return resultado

    def corrigir(self, titulo, compositor, cantor) -> Resultado:
        """Grava (ou apaga, se `cantor` vier vazio) uma correção manual."""
        chave = chave_consulta(titulo, compositor)
        cantor = str(cantor or "").strip()
        if not cantor:
            self.banco.apagar_correcao(*chave)
            return Resultado(observacao="cantor apagado manualmente", regra="apagado_manualmente")
        self.banco.salvar_correcao(*chave, cantor)
        return self._resultado_manual(cantor)

    def preparar_relatorio(self, nome: str, nomes_artisticos=()) -> dict:
        """Liga o modo relatório: todas as linhas são obras do compositor `nome`.

        Nesse modo "o artista é o compositor" deixa de ser evidência (seria um
        empurrão automático para o dono do relatório) e as obras dele são
        listadas de uma vez no MusicBrainz, para achar cada título nessa lista.

        `nomes_artisticos` são os nomes, projetos e grupos com que ele aparece
        nas bases, confirmados pelo usuário: valem como o próprio compositor em
        todas as regras, e a discografia de cada um é listada de uma vez.
        """
        artisticos = list({normalizar(n): str(n).strip() for n in nomes_artisticos if normalizar(n)}.values())
        self.relatorio = {
            "nome": nome, "ids": set(), "nomes": list(artisticos), "obras": [], "erro": "", "segundos": 0.0,
            "artisticos": artisticos, "discografia": [],
        }
        inicio = time.monotonic()
        try:
            obras = {}
            for n in [nome] + artisticos:
                conhecidos = self._artistas_conhecidos(n)
                self.relatorio["ids"] |= set(conhecidos)
                self.relatorio["nomes"] += [x for ns in conhecidos.values() for x in ns]
                obras.update((o["id"], o) for o in self._obras_do_artista(n, list(conhecidos)))
                self.relatorio["obras"] = list(obras.values())
            self.relatorio["discografia"] = [{"nome": n, "faixas": self._discografia(n)} for n in artisticos]
        except ErroDeRede as e:
            log.error("não foi possível listar as obras de %r: %s", nome, e)
            self.relatorio["erro"] = str(e)
        self.relatorio["segundos"] = time.monotonic() - inicio
        return self.relatorio

    def desligar_relatorio(self):
        self.relatorio = None

    def _obras_do_artista(self, nome: str, ids: list[str]) -> list[dict]:
        """Obras ligadas ao artista no MusicBrainz: [{"id", "title", "nomes", "autores"}].

        A listagem traz tanto as obras em que ele é autor quanto as que ele só
        gravou; "autores" ({id: nome}) permite separar os dois casos.
        """
        chave = normalizar(nome)
        salvo = self.banco.obter_obras(chave)
        if salvo and salvo.get("v") == VERSAO_CACHE:
            return salvo["obras"]
        obras = {}
        for mbid in ids[:MAX_OBRAS]:
            for pagina in range(MAX_PAGINAS_CATALOGO):
                dados = self.cliente.mb("work", artist=mbid, inc="artist-rels+aliases", limit=100, offset=pagina * 100)
                lote = dados.get("works", [])
                for o in lote:
                    obras[o["id"]] = {
                        "id": o["id"],
                        "title": o.get("title", ""),
                        "nomes": [x for x in [o.get("title")] + [a.get("name") for a in o.get("aliases") or []] if x],
                        "autores": {
                            rel["artist"]["id"]: rel["artist"].get("name", "")
                            for rel in o.get("relations", [])
                            if rel.get("type") in TIPOS_AUTORIA and "artist" in rel
                        },
                    }
                if not lote or (pagina + 1) * 100 >= dados.get("work-count", 0):
                    break
        self.banco.salvar_obras(chave, {"v": VERSAO_CACHE, "obras": list(obras.values())})
        return list(obras.values())

    def _discografia(self, nome: str) -> list[dict]:
        """Faixas lançadas com esse nome artístico: [{"titulo", "artista", "link", "data", "fonte"}].

        Vem do Deezer (álbuns do artista e as faixas de cada um) e do
        MusicBrainz, se o artista existir lá. É listada uma vez só e fica em cache.
        """
        chave = normalizar(nome)
        salvo = self.banco.obter_discografia(chave)
        if salvo and salvo.get("v") == VERSAO_CACHE:
            return salvo["faixas"]
        faixas = []
        achados = self.cliente.deezer_api("search/artist", q=nome, limit=25).get("data", [])
        for artista in [a for a in achados if nomes_parecidos(nome, a.get("name"), parcial=False)][:MAX_ARTISTAS_DISCOGRAFIA]:
            for album in self._paginas_deezer(f"artist/{artista['id']}/albums", MAX_ALBUNS_DISCOGRAFIA):
                for f in self._paginas_deezer(f"album/{album['id']}/tracks", 500):
                    faixas.append({
                        "titulo": f.get("title", ""), "artista": (f.get("artist") or {}).get("name") or artista["name"],
                        "link": f.get("link", ""), "data": album.get("release_date") or "", "fonte": "Deezer",
                    })
        for mbid, nomes in list(self._artistas_conhecidos(nome).items())[:MAX_ARTISTAS_DISCOGRAFIA]:
            for pagina in range(MAX_PAGINAS_DISCOGRAFIA):
                dados = self.cliente.mb("recording", artist=mbid, limit=100, offset=pagina * 100)
                lote = dados.get("recordings", [])
                for g in lote:
                    faixas.append({
                        "titulo": g.get("title", ""), "artista": nomes[0], "link": f"{MB_SITE}/recording/{g['id']}",
                        "data": g.get("first-release-date") or "", "fonte": "MusicBrainz",
                    })
                if not lote or (pagina + 1) * 100 >= dados.get("recording-count", 0):
                    break
        self.banco.salvar_discografia(chave, {"v": VERSAO_CACHE, "faixas": faixas})
        return faixas

    def _paginas_deezer(self, caminho: str, maximo: int) -> list[dict]:
        itens = []
        while len(itens) < maximo:
            dados = self.cliente.deezer_api(caminho, limit=100, index=len(itens))
            lote = dados.get("data", [])
            itens += lote
            if not lote or not dados.get("next"):
                break
        return itens

    def sugerir_nomes_artisticos(self, nome_civil: str) -> list[str]:
        """Artistas do Deezer e do MusicBrainz cujo nome usa só palavras do nome civil.

        "Fulano Souza" para "José Fulano de Souza Lima", por exemplo. São só
        sugestões: quem decide se o artista é mesmo o compositor é o usuário.
        """
        palavras = str(nome_civil).split()
        permitidas = set(normalizar(nome_civil).split())
        combinacoes = [" ".join(c) for tamanho in (2, 3) for c in itertools.combinations(palavras, tamanho)]
        achados = {}
        for consulta in combinacoes[:MAX_CONSULTAS_SUGESTAO]:
            nomes = []
            try:
                nomes += [a.get("name") for a in self.cliente.deezer_api("search/artist", q=consulta, limit=10).get("data", [])]
                dados = self.cliente.mb("artist", query=f'artist:"{_frase(consulta)}"', limit=10)
                nomes += [a.get("name") for a in dados.get("artists", [])]
            except ErroDeRede as e:
                log.error("sugestão de nomes artísticos falhou para %r: %s", consulta, e)
            for nome in nomes:
                do_nome = normalizar(nome).split()
                if len(do_nome) >= 2 and set(do_nome) < permitidas:
                    achados.setdefault(normalizar(nome), nome)
        return list(achados.values())

    def _nome_do_cliente(self, nome: str) -> bool:
        """`nome` é o compositor do relatório (pelo nome, por um alias ou por um nome artístico confirmado)?"""
        r = self.relatorio
        return bool(r) and (
            nomes_parecidos(nome, r["nome"]) or any(nomes_parecidos(nome, n, parcial=False) for n in r["nomes"])
        )

    def _e_cliente(self, artista: dict) -> bool:
        return bool(self.relatorio) and (
            bool(artista["ids"] & self.relatorio["ids"]) or self._nome_do_cliente(artista["nome"])
        )

    def ids_do_artista(self, nome: str) -> set[str]:
        """Ids do MusicBrainz dos artistas que têm esse nome ou apelido."""
        return set(self._artistas_conhecidos(nome))

    @staticmethod
    def _resultado_manual(cantor: str) -> Resultado:
        return Resultado(cantor, "alta", [], FONTE_MANUAL, "corrigido manualmente", "correcao_manual")

    # --- estratégia ---------------------------------------------------------

    def _consultar(self, titulo: str, compositor: str, iswc: str = "") -> Resultado:
        nomes = dividir_compositores(compositor)
        if iswc:
            inicio = time.monotonic()
            try:
                resultado, sufixo, encontrado_em, aviso = self._por_iswc(titulo, nomes, iswc)
            finally:
                self.tempo_iswc += time.monotonic() - inicio
            if resultado is None:
                # ISWC sem uso: segue a lógica de sempre e registra o motivo.
                resultado = self._sem_iswc(titulo, nomes)
                resultado.observacao = "; ".join(x for x in [aviso, resultado.observacao] if x)
            resultado.regra = "+".join(x for x in [resultado.regra, sufixo] if x)
            resultado.iswc_normalizado, resultado.iswc_encontrado_em = iswc, encontrado_em
            return resultado
        return self._sem_iswc(titulo, nomes)

    def _sem_iswc(self, titulo: str, nomes: list[str]) -> Resultado:
        avisos = []
        if nomes:
            resultado, aviso = self._por_obra(titulo, nomes)
            if resultado:
                return resultado
            avisos = [aviso]
        # A discografia dos nomes artísticos confirmados vem antes; a busca por título fica de reserva.
        return self._por_discografia(titulo, avisos) or self._por_titulo(titulo, nomes, avisos=avisos)

    # --- modo relatório: título na discografia de um nome artístico confirmado ---

    def _por_discografia(self, titulo: str, avisos: list[str]):
        """Resultado se o título está na discografia de um nome artístico confirmado; senão None.

        Um nome só com o título: média. Alta só quando Deezer e MusicBrainz
        listam, os dois, o título na discografia dele. Mais de um nome
        confirmado com o título: baixa, porque não dá para saber qual deles vale.
        """
        fortes = self._na_discografia(titulo)
        if not fortes:
            return None
        escolhido = fortes[0]
        fontes = escolhido["fontes_txt"]
        obs = avisos + [
            f'título na discografia de "{escolhido["confirmado"]}" (nome artístico confirmado do compositor) '
            f'no {fontes}' + (f', lançado em {escolhido["data_txt"]}' if escolhido["data_txt"] else "")
        ]
        sufixos = [SIGLA_FONTE[fontes]]
        if len(fortes) > 1:
            confianca = "baixa"
            sufixos.append("varios_nomes")
            obs.append(f"o título também está na discografia de {fortes[1]['confirmado']}; conferir qual deles vale")
        elif len(escolhido["fontes"]) > 1:
            confianca = "alta"
            obs.append("confirmação: as duas bases listam o título na discografia dele")
        else:
            confianca = "media"
            obs.append("uma base só, sem outra fonte que confirme")
        return Resultado(
            escolhido["nome"], confianca, [f["nome"] for f in fortes[1:3]], escolhido["link"], "; ".join(obs),
            "+".join(["discografia"] + sufixos), ano_gravacao=0 if escolhido["data"] == SEM_DATA else escolhido["data"][0],
        )

    def _na_discografia(self, titulo: str) -> list[dict]:
        """Nomes artísticos confirmados que têm o título na discografia, do lançamento mais antigo para o mais novo."""
        fortes = []
        for discografia in (self.relatorio or {}).get("discografia", []):
            iguais = [
                f for f in discografia["faixas"]
                if titulos_parecidos(f["titulo"], titulo) and not _versao_derivada(titulo, f["titulo"])
            ]
            if not iguais:
                continue
            primeira = min(iguais, key=lambda f: _chave_data(f["data"]) or SEM_DATA)
            fontes = {f["fonte"] for f in iguais}
            fortes.append({
                "nome": primeira["artista"] or discografia["nome"], "confirmado": discografia["nome"],
                "fontes": fontes, "fontes_txt": "MusicBrainz e Deezer" if len(fontes) > 1 else next(iter(fontes)),
                "data": _chave_data(primeira["data"]) or SEM_DATA, "data_txt": primeira["data"], "link": primeira["link"],
            })
        return sorted(fortes, key=lambda f: f["data"])

    # --- com ISWC: a obra fica definida pelo código ------------------------

    def _por_iswc(self, titulo: str, nomes: list[str], iswc: str):
        """Devolve (resultado ou None, sufixo da regra, onde o ISWC foi achado, aviso)."""
        obras_mb = self.cliente.mb("work", query=f'iswc:"{iswc}"', limit=5).get("works", [])
        credits = self._credits_iswc(iswc)
        no_mb, no_credits = bool(obras_mb), credits is not None
        encontrado_em = {(True, True): "ambos", (True, False): "MB", (False, True): "Credits.fm"}.get(
            (no_mb, no_credits), "nenhum"
        )
        if encontrado_em == "nenhum":
            return None, "iswc_nao_encontrado", encontrado_em, "ISWC não encontrado no MusicBrainz nem no Credits.fm"

        mb_confere = [o for o in obras_mb if self._titulo_da_obra_confere(o, titulo)]
        credits_confere = no_credits and any(
            titulos_parecidos(t, titulo)
            for t in [credits.get("title") or credits.get("song_title")] + list(credits.get("alternative_titles") or [])
            if t
        )
        if not mb_confere and not credits_confere:
            return None, "iswc_titulo_diverge", encontrado_em, "ISWC e título não conferem; ISWC ignorado"
        sufixo = "iswc_ambos" if mb_confere and credits_confere else "iswc_mb" if mb_confere else "iswc_credits"

        candidatos_isrc = self._original_por_isrc(credits) if credits_confere else []
        resultado = None
        if mb_confere:
            compativeis = [
                (1, obra, {
                    rel["artist"]["id"]: rel["artist"].get("name", "")
                    for rel in obra.get("relations", [])
                    if rel.get("type") in TIPOS_AUTORIA and "artist" in rel
                })
                for obra in mb_confere
            ]
            resultado, _ = self._por_obra(titulo, nomes, compativeis=compativeis, origem="iswc")
        if resultado and candidatos_isrc:
            resultado, concordancia = self._cruzar(resultado, candidatos_isrc)
            return resultado, f"{sufixo}+{concordancia}", encontrado_em, ""
        if resultado:
            return resultado, sufixo, encontrado_em, ""
        if candidatos_isrc:
            return self._resultado_por_isrc(candidatos_isrc), sufixo, encontrado_em, ""
        aviso = "obra definida pelo ISWC, mas sem gravações com artista nas fontes"
        return None, sufixo, encontrado_em, aviso

    def _credits_iswc(self, iswc: str):
        """Obra no Credits.fm com as gravações (ISRC); None se não existe lá ou se a API falhou."""
        try:
            credits = self.cliente.credits(f"iswc/{iswc}", include="recordings", depth=2, limit=-1, contribute="false")
        except ErroDeRede as e:
            log.error("Credits.fm falhou para %s: %s", iswc, e)
            self._falhas.append("Credits.fm indisponível nesta consulta (ver log); ISWC conferido só no MusicBrainz")
            return None
        # Para um ISWC que não conhece, o Credits.fm responde HTTP 200 com um registro
        # vazio (sem título e sem gravações) em vez de 404: isso é "não encontrado".
        if credits is not None and not (
            credits.get("title") or credits.get("song_title") or credits.get("alternative_titles")
            or credits.get("recordings")
        ):
            return None
        return credits

    def _original_por_isrc(self, credits: dict) -> list[dict]:
        """Intérpretes das gravações do ISWC no Credits.fm, da mais antiga para a mais nova.

        A ordem usa o ano do ISRC (posições 6 e 7 do código: o ano em que ele foi
        atribuído, que acompanha o primeiro lançamento) e, no empate, a data do
        lançamento no Deezer. O artista vem do Deezer, consultado pelo ISRC.
        """
        isrcs = {}
        for gravacao in credits.get("recordings") or []:
            isrc = (gravacao.get("isrc") if isinstance(gravacao, dict) else str(gravacao)).strip().upper()
            ano = _ano_do_isrc(isrc)
            if ano:
                isrcs[isrc] = ano
        grupos = {}
        for isrc in sorted(isrcs, key=isrcs.get)[:ISRCS_CONFERIDOS_NO_DEEZER]:
            try:
                faixa = self.cliente.deezer_isrc(isrc)
            except ErroDeRede as e:
                log.error("Deezer falhou para o ISRC %s: %s", isrc, e)
                if not self._falhas:
                    self._falhas.append("Deezer indisponível nesta consulta (ver log); gravações do ISWC incompletas")
                break
            artista = ((faixa or {}).get("artist") or {}).get("name", "")
            if not artista or normalizar(artista) in ARTISTAS_IGNORADOS:
                continue
            chave = (isrcs[isrc], faixa.get("release_date") or "9999")
            grupo = grupos.setdefault(normalizar(artista), {"nome": artista, "chave": chave, "isrcs": 0, "isrc": isrc,
                                                            "link": faixa.get("link", "")})
            grupo["isrcs"] += 1
            if chave < grupo["chave"]:
                grupo.update(chave=chave, isrc=isrc, link=faixa.get("link", ""))
        return sorted(grupos.values(), key=lambda g: (g["chave"], -g["isrcs"]))

    def _cruzar(self, resultado: Resultado, candidatos_isrc: list[dict]):
        """Compara o intérprete do MusicBrainz com o do Credits.fm para a mesma obra.

        Devolve (resultado ajustado, "fontes_concordam" ou "fontes_discordam").
        """
        ano_mais_antigo = candidatos_isrc[0]["chave"][0]
        mais_antigos = [c for c in candidatos_isrc if c["chave"][0] == ano_mais_antigo]
        if resultado.cantor_sugerido and any(nomes_parecidos(c["nome"], resultado.cantor_sugerido) for c in mais_antigos):
            # Duas fontes independentes apontam o mesmo intérprete para a obra do ISWC.
            resultado.confianca = "alta"
            resultado.observacao += (
                f"; Credits.fm confirma: a gravação de ISRC mais antigo ({ano_mais_antigo}) também é desse artista"
                f" (dados Credits.fm, CC BY 4.0)"
            )
            return resultado, "fontes_concordam"
        resultado.confianca = _limitar(resultado.confianca, "media")
        outro = candidatos_isrc[0]
        resultado.observacao += (
            f"; Credits.fm aponta outro artista: {outro['nome']} (ISRC {outro['isrc']}, {ano_mais_antigo})"
            f" (dados Credits.fm, CC BY 4.0)"
        )
        if not any(nomes_parecidos(outro["nome"], a) for a in resultado.alternativas):
            resultado.alternativas = ([outro["nome"]] + resultado.alternativas)[:2]
        return resultado, "fontes_discordam"

    def _resultado_por_isrc(self, candidatos_isrc: list[dict]) -> Resultado:
        """Obra só no Credits.fm: uma fonte só, então no máximo média."""
        primeiro = candidatos_isrc[0]
        obs = [
            f"obra definida pelo ISWC no Credits.fm; gravação de ISRC mais antigo: {primeiro['isrc']} "
            f"({primeiro['chave'][0]}), artista conferido no Deezer (dados Credits.fm, CC BY 4.0)"
        ]
        if len(candidatos_isrc) > 1 and candidatos_isrc[1]["chave"][0] == primeiro["chave"][0]:
            obs.append(f"mesmo ano de ISRC que {candidatos_isrc[1]['nome']}")
        return Resultado(
            primeiro["nome"], "media", [c["nome"] for c in candidatos_isrc[1:3]], primeiro["link"], "; ".join(obs),
            "iswc_isrc_mais_antigo",
        )

    # --- compositores e apelidos -------------------------------------------

    def _artistas_conhecidos(self, nome: str) -> dict[str, list[str]]:
        """{id: [nome e aliases]} dos artistas do MusicBrainz que atendem por `nome`."""
        chave = normalizar(nome)
        if chave in self._artistas:
            return self._artistas[chave]
        conhecidos = self.banco.obter_artista(chave)
        if not isinstance(conhecidos, dict):  # ausente, ou no formato da versão anterior
            n = _frase(nome)
            artistas = self.cliente.mb("artist", query=f'artist:"{n}" OR alias:"{n}"', limit=10).get("artists", [])
            conhecidos = {}
            for a in artistas:
                nomes = [x for x in [a.get("name")] + [al.get("name") for al in a.get("aliases") or []] if x]
                if any(nomes_parecidos(nome, x, parcial=False) for x in nomes):
                    conhecidos[a["id"]] = nomes
            self.banco.salvar_artista(chave, conhecidos)
        self._artistas[chave] = conhecidos
        return conhecidos

    def _e_compositor(self, artista: dict, nomes_da_linha: list[str], autores: dict) -> bool:
        """O artista é um dos compositores, ou tem um deles no crédito? `autores` = {id: nome} vindos da obra."""
        return self._grau_de_compositor(artista, nomes_da_linha, autores) > 0

    def _grau_de_compositor(self, artista: dict, nomes_da_linha: list[str], autores: dict) -> int:
        """0: não é compositor. 1: um compositor só participa do crédito. 2: o crédito é o compositor.

        O grau 1 é a dupla, o grupo ou a parceria que tem um compositor dentro
        ("Fulana e Beltrano" para o autor "Beltrano"): ele pode ter regravado a
        música anos depois, então isso não confirma que a gravação é a original.

        No modo relatório o dono do relatório não conta: ele é compositor de
        todas as linhas, então isso não diz nada sobre quem gravou primeiro.
        Parceiros dele continuam contando.
        """
        if self.relatorio:
            if self._e_cliente(artista):
                return 0
            nomes_da_linha = [n for n in nomes_da_linha if not self._nome_do_cliente(n)]
            autores = {i: n for i, n in autores.items() if i not in self.relatorio["ids"]}
        ids, nome = artista["ids"], artista["nome"]
        palavras = set(normalizar(nome).split())

        def e_ele(outro):  # o mesmo nome, ou um nome mais curto da mesma pessoa ("Vinicius" ~ "Vinicius de Moraes")
            return nomes_parecidos(nome, outro, parcial=False) or (
                nomes_parecidos(nome, outro) and palavras <= set(normalizar(outro).split())
            )

        ids_dos_autores = set(autores)
        declarados = list(autores.values()) + nomes_da_linha
        if (ids and ids <= ids_dos_autores) or any(e_ele(n) for n in declarados):
            return 2
        participa = bool(ids & ids_dos_autores) or any(nomes_parecidos(nome, n) for n in declarados)
        # "Tom Jobim" não se parece com "Antônio Carlos Jobim": resolve pelos aliases.
        for declarado in nomes_da_linha:
            conhecidos = self._artistas_conhecidos(declarado)
            ids_dos_autores |= conhecidos.keys()
            if any(nomes_parecidos(nome, n, parcial=False) for ns in conhecidos.values() for n in ns):
                return 2
        if ids & ids_dos_autores:
            return 2 if ids <= ids_dos_autores else 1
        return 1 if participa else 0

    # --- com compositor: obra -> gravações ---------------------------------

    def _por_obra(self, titulo: str, nomes: list[str], compativeis=None, origem=""):
        """`compativeis` já definidas (pelo ISWC) dispensam a busca da obra."""
        if not compativeis and self.relatorio and any(self._nome_do_cliente(n) for n in nomes):
            compativeis, origem = self._obras_do_catalogo(titulo)
        if not compativeis:
            compativeis, origem = self._obras_compativeis(titulo, nomes), ""
        if not compativeis:
            return None, "nenhuma obra com esse título e compositor no MusicBrainz"
        # Várias obras com mesmo título e autor costumam ser a mesma música
        # cadastrada duas vezes (ou uma versão em outro idioma): soma as gravações.
        gravacoes, total, autores = [], 0, {}
        for _, obra, autores_da_obra in compativeis[:MAX_OBRAS]:
            lote, quantas = self._gravacoes_da_obra(obra["id"])
            gravacoes += lote
            total += quantas
            autores.update(autores_da_obra)
        if not _agrupar_por_artista(gravacoes):
            return None, "obra encontrada no MusicBrainz, mas sem gravações vinculadas"
        gravacoes, ignoradas, incompleto = self._so_oficiais(gravacoes)
        artistas = _agrupar_por_artista(gravacoes)
        obra = compativeis[0][1]
        resultado = self._resultado_da_obra(
            titulo, nomes, obra, autores, artistas, len(gravacoes), total, len(compativeis), ignoradas, incompleto, origem
        )
        return resultado, ""

    def _obras_do_catalogo(self, titulo: str):
        """Procura o título na lista de obras do compositor do relatório.

        Devolve ([(1, obra, autores)], origem). Prefere as obras em que ele consta
        como autor ("catalogo"); na falta, as que não têm autor nenhum e estão
        ligadas a ele por uma gravação ("catalogo_sem_autor"). Obras de outros
        autores com o mesmo título (que ele só gravou) são descartadas.
        """
        iguais = [o for o in self.relatorio["obras"] if any(titulos_parecidos(n, titulo) for n in o["nomes"])]
        dele = [o for o in iguais if o["autores"].keys() & self.relatorio["ids"]]
        sem_autor = [o for o in iguais if not o["autores"]]
        escolhidas, origem = (dele, "catalogo") if dele else (sem_autor, "catalogo_sem_autor")
        return [(1, {"id": o["id"], "title": o["title"], "score": 100}, o["autores"]) for o in escolhidas], origem

    def _so_oficiais(self, gravacoes: list[dict]):
        """Troca a data de cada gravação pela do seu lançamento oficial mais antigo.

        Confere as gravações da mais antiga para a mais nova e para assim que
        nenhuma das restantes pode ser anterior à melhor data já confirmada.
        Devolve (gravações com a data ajustada, gravações ignoradas, se parou
        antes da hora por ter atingido MAX_VERIFICACOES).
        """
        datadas = [g for g in gravacoes if _chave_data(g.get("first-release-date"))]
        datadas.sort(key=lambda g: _chave_data(g["first-release-date"]))
        novas_datas, ao_vivo, sem_status, ignoradas, melhor, incompleto = {}, set(), set(), [], None, False
        for g in datadas:
            if g["id"] in novas_datas:
                continue  # mesma gravação vinculada a duas obras
            if melhor is not None and melhor < _chave_data(g["first-release-date"]):
                break
            if len(novas_datas) == MAX_VERIFICACOES:
                incompleto = True
                break
            oficial, data, so_ao_vivo, so_sem_status = self._data_oficial(g)
            novas_datas[g["id"]] = data
            if so_ao_vivo:
                ao_vivo.add(g["id"])
            if so_sem_status:
                sem_status.add(g["id"])
            if not oficial:
                ignoradas.append(g)
            elif _chave_data(data) and (melhor is None or _chave_data(data) < melhor):
                melhor = _chave_data(data)
        ajustadas = [
            {**g, "first-release-date": novas_datas[g["id"]], "so_ao_vivo": g["id"] in ao_vivo,
             "sem_status": g["id"] in sem_status}
            if g["id"] in novas_datas else g
            for g in gravacoes
        ]
        return ajustadas, ignoradas, incompleto

    def _data_oficial(self, gravacao: dict) -> tuple[bool, str, bool, bool]:
        """(tem lançamento válido?, data do mais antigo, todos são ao vivo?, nenhum tem status cadastrado?).

        Válido é o lançamento que não é demo nem está marcado como bootleg,
        promocional ou pseudo-lançamento. Status em branco conta como válido:
        muito disco antigo está no MusicBrainz sem esse campo preenchido.
        """
        inicio = time.monotonic()
        try:
            dados = self.cliente.mb(
                "release", recording=gravacao["id"], inc="release-groups", limit=LANCAMENTOS_POR_CONSULTA,
            )
        finally:
            self.consultas_oficiais += 1
            self.tempo_oficiais += time.monotonic() - inicio
        lancamentos = dados.get("releases", [])
        if dados.get("release-count", len(lancamentos)) > len(lancamentos):
            # Mais de 100 lançamentos: é gravação de catálogo. Não dá para ver
            # todos em uma consulta, então mantém a data que o MusicBrainz informa.
            return True, gravacao["first-release-date"], False, False

        def tipos(lancamento):
            return {t.lower() for t in (lancamento.get("release-group") or {}).get("secondary-types") or []}

        validos = [
            r for r in lancamentos
            if (r.get("status") or "").lower() not in STATUS_NAO_OFICIAIS and not tipos(r) & TIPOS_QUE_NAO_DATAM
        ]
        so_sem_status = bool(validos) and not any(r.get("status") for r in validos)
        so_ao_vivo = bool(validos) and all("live" in tipos(r) for r in validos)
        datas = [r["date"] for r in validos if _chave_data(r.get("date"))]
        # Um lançamento só com o ano ("2010") pode ser anterior a um "2010-11-29":
        # fica valendo a data menos precisa, e o empate é tratado adiante.
        data = min(datas, key=lambda d: tuple(int(p) for p in d.split("-") if p.isdigit())) if datas else ""
        return bool(validos), data, so_ao_vivo, so_sem_status

    def _obras_compativeis(self, titulo: str, nomes: list[str]) -> list[tuple]:
        """[(nº de compositores que batem, obra, autores)], da melhor para a pior."""
        t = _frase(titulo)
        filtro = " OR ".join(f'"{_frase(n)}"' for n in nomes)
        consultas = [
            # Com o compositor na consulta, títulos comuns ("Hello") acham a obra certa.
            (f'(work:"{t}" OR alias:"{t}") AND artist:({filtro})', 25),
            # Sem ele: cobre apelidos, que o filtro acima não reconhece.
            (f'work:"{t}" OR alias:"{t}"', 100),
        ]
        for consulta, limite in consultas:
            compativeis = []
            for obra in self.cliente.mb("work", query=consulta, limit=limite).get("works", []):
                if not self._titulo_da_obra_confere(obra, titulo):
                    continue
                autores = {
                    rel["artist"]["id"]: rel["artist"].get("name", "")
                    for rel in obra.get("relations", [])
                    if rel.get("type") in TIPOS_AUTORIA and "artist" in rel
                }
                acertos = sum(self._eh_autor(nome, autores) for nome in nomes)
                if acertos:
                    compativeis.append((acertos, obra, autores))
            if compativeis:
                compativeis.sort(key=lambda trio: (-trio[0], -trio[1].get("score", 0)))
                return compativeis
        return []

    @staticmethod
    def _titulo_da_obra_confere(obra: dict, titulo: str) -> bool:
        nomes = [obra.get("title")] + [a.get("name") for a in obra.get("aliases") or []]
        return any(titulos_parecidos(nome, titulo) for nome in nomes)

    def _eh_autor(self, nome: str, autores: dict) -> bool:
        if any(nomes_parecidos(nome, autor) for autor in autores.values()):
            return True
        return bool(self.ids_do_artista(nome) & autores.keys())

    def _gravacoes_da_obra(self, mbid: str):
        gravacoes, total = [], 0
        for pagina in range(self.max_paginas):
            # `inc=releases` não é aceito neste endpoint; a data vem em first-release-date.
            dados = self.cliente.mb("recording", work=mbid, inc="artist-credits", limit=100, offset=pagina * 100)
            lote = dados.get("recordings", [])
            gravacoes.extend(lote)
            total = dados.get("recording-count", len(gravacoes))
            if not lote or len(gravacoes) >= total:
                break
        return gravacoes, total

    def _resultado_da_obra(
        self, titulo, nomes, obra, autores, artistas, analisadas, total, n_compativeis, ignoradas=(), incompleto=False,
        origem="",
    ) -> Resultado:
        for a in artistas:
            grau = self._grau_de_compositor(a, nomes, autores)
            a["compositor"], a["compositor_inteiro"] = grau > 0, grau == 2
            a["cliente"] = self._e_cliente(a)
        # Ser o dono do relatório só serve como último critério de desempate.
        datados = sorted(
            (a for a in artistas if a["data"]), key=lambda a: (a["data"], -a["gravacoes"], not a["cliente"])
        )
        sem_data = sorted(
            (a for a in artistas if not a["data"]), key=lambda a: (not a["compositor"], -a["gravacoes"], not a["cliente"])
        )
        confianca = "alta" if n_compativeis == 1 else "media"
        obs = [f'obra "{obra.get("title", "")}" no MusicBrainz']
        sufixos = []  # compõem a coluna "regra"
        if origem == "iswc":
            obs[0] = f'obra "{obra.get("title", "")}" encontrada pelo ISWC no MusicBrainz'
        elif origem == "catalogo":
            sufixos.append(origem)
            obs[0] = f'obra "{obra.get("title", "")}" na lista de obras do compositor no MusicBrainz'
        elif origem == "catalogo_sem_autor":
            confianca = _limitar(confianca, "media")
            sufixos.append(origem)
            obs[0] = (
                f'obra "{obra.get("title", "")}" ligada ao compositor no MusicBrainz só por uma gravação dele '
                "(sem autor cadastrado)"
            )
        if n_compativeis > 1:
            sufixos.append("varias_obras")
            obs.append(f"{n_compativeis} obras compatíveis; gravações de {min(n_compativeis, MAX_OBRAS)} somadas")
        if total > analisadas:
            confianca = _limitar(confianca, "media")
            sufixos.append("truncada")
            obs.append(f"obra com {total} gravações; analisadas {analisadas}")
        if ignoradas:
            exemplo = ignoradas[0]
            sufixos.append("nao_oficial_ignorada")
            obs.append(
                f"ignorada(s) {len(ignoradas)} gravação(ões) mais antiga(s) sem lançamento oficial "
                f"(ex.: {_credito(exemplo)}, {exemplo['first-release-date']}); usada a próxima oficial"
            )
        if incompleto:
            confianca = _limitar(confianca, "media")
            sufixos.append("oficial_nao_verificado")
            obs.append(f"só as {MAX_VERIFICACOES} gravações mais antigas foram conferidas quanto a lançamento oficial")
        if not datados:
            obs.append("nenhuma gravação com data de lançamento oficial; não dá para saber qual é a original")
            forte = next(iter(self._na_discografia(titulo)), None)
            if forte:
                # Sem data nenhuma para comparar, o título na discografia dele é a melhor pista; continua baixa.
                obs.append(
                    f'sugerido "{forte["confirmado"]}" (nome artístico confirmado do compositor), '
                    f'que tem o título na discografia no {forte["fontes_txt"]}'
                )
                return Resultado(
                    forte["nome"], "baixa", [a["nome"] for a in sem_data[:2]], forte["link"], "; ".join(obs),
                    "+".join(["obra_discografia"] + sufixos), ano_gravacao=0 if forte["data"] == SEM_DATA else forte["data"][0],
                )
            return Resultado(
                sem_data[0]["nome"], "baixa", [a["nome"] for a in sem_data[1:3]], sem_data[0]["link"], "; ".join(obs),
                "+".join(["obra_sem_data"] + sufixos),
            )

        primeiro = datados[0]
        posicao_da_data = len(obs)
        obs.append(f"gravação mais antiga: {primeiro['data_txt']}")
        empatados = [a for a in datados[1:] if _empate(primeiro["data"], a["data"])]
        if empatados:
            confianca = _limitar(confianca, "media")
            preferido = next((a for a in [primeiro] + empatados if a["compositor"]), primeiro)
            if preferido is not primeiro:
                sufixos.append("empate_compositor")
                obs.append(f"data empatada com {primeiro['nome']}; preferido por ser também compositor")
            else:
                sufixos.append("empate")
                obs.append(f"data empatada com {empatados[0]['nome']}")
            primeiro = preferido
        outros = [a for a in datados + sem_data if a is not primeiro]
        if primeiro["ao_vivo"]:
            sufixos.append("ao_vivo")
            obs.append("a gravação mais antiga só existe em lançamento ao vivo oficial")
        if primeiro["sem_status"]:
            sufixos.append("status_nao_cadastrado")
            obs.append("os lançamentos da gravação mais antiga estão sem status no MusicBrainz; contados como oficiais")
        ano = primeiro["data"][0]

        # A mais antiga sozinha não basta: a original pode não estar vinculada à
        # obra, e aí a "mais antiga" é um cover. Exige um segundo sinal.
        mais_gravado = max(a["gravacoes"] for a in artistas)
        popular = None
        # Compositores costumam tocar a música ao vivo antes de alguém lançá-la:
        # numa gravação ao vivo, ser compositor não confirma que é a original.
        if primeiro["compositor_inteiro"] and not primeiro["ao_vivo"]:
            sinal, regra = "também é compositor", "obra_compositor"
        elif primeiro["gravacoes"] >= 2 and primeiro["gravacoes"] == mais_gravado:
            sinal, regra = "é quem mais gravou a obra", "obra_mais_gravado"
        else:
            sinal, regra = None, "obra_sem_confirmacao"
            popular = max(self._faixas_deezer(titulo), key=lambda f: f["rank"], default=None)
            if popular and nomes_parecidos(popular["artista"], primeiro["nome"]):
                sinal, regra = "é o mais popular no Deezer para esse título", "obra_popular_deezer"
        if not sinal and primeiro["compositor"] and not primeiro["compositor_inteiro"] and not primeiro["ao_vivo"]:
            # Dupla, grupo ou parceria com um compositor dentro: ele pode ter regravado a
            # música depois de outra pessoa lançá-la. Vale como sinal, mas não dá alta.
            sinal, regra = "um dos compositores participa da gravação (o crédito não é só dele)", "obra_compositor_parcial"
            confianca = _limitar(confianca, "media")
        if sinal:
            obs.append(f"confirmação: {sinal}")
            return Resultado(
                primeiro["nome"], confianca, [a["nome"] for a in outros[:2]], primeiro["link"], "; ".join(obs),
                "+".join([regra] + sufixos), ano_gravacao=ano,
            )

        if popular and self._e_compositor({"nome": popular["artista"], "ids": set()}, nomes, autores):
            # Dois sinais (compositor + mais popular) contra uma data sem confirmação.
            obs[posicao_da_data] = f"gravação mais antiga vinculada: {primeiro['nome']} ({primeiro['data_txt']}), sem confirmação"
            obs.append("sugerido o compositor, que é o mais popular no Deezer para esse título")
            alternativas = [primeiro["nome"]] + [a["nome"] for a in outros[:1]]
            return Resultado(
                popular["artista"], "media", alternativas, popular["link"], "; ".join(obs),
                "+".join(["obra_compositor_popular"] + sufixos), ano_gravacao=ano,
            )

        forte = next(
            (f for f in self._na_discografia(titulo)
             if f["data"] < primeiro["data"] and not nomes_parecidos(f["nome"], primeiro["nome"])),
            None,
        )
        if forte:
            # A gravação "mais antiga" vinculada à obra não é a original: o próprio
            # compositor lançou o título antes dela, com um nome artístico confirmado.
            obs[posicao_da_data] = f"gravação mais antiga vinculada: {primeiro['nome']} ({primeiro['data_txt']}), sem confirmação"
            obs.append(
                f'sugerido "{forte["confirmado"]}" (nome artístico confirmado do compositor): a discografia dele '
                f'no {forte["fontes_txt"]} tem o título em {forte["data_txt"]}, antes dessa gravação'
            )
            alternativas = [primeiro["nome"]] + [a["nome"] for a in outros[:1]]
            return Resultado(
                forte["nome"], _limitar(confianca, "media"), alternativas, forte["link"], "; ".join(obs),
                "+".join(["obra_discografia"] + sufixos), ano_gravacao=forte["data"][0],
            )

        obs.append(
            "mais antiga no MusicBrainz, sem confirmação (falta um segundo sinal: ser compositor, "
            "ser quem mais gravou ou ser o mais popular no Deezer)"
        )
        alternativas = [a["nome"] for a in outros]
        if popular and not any(nomes_parecidos(popular["artista"], a) for a in alternativas[:2]):
            alternativas.insert(0, popular["artista"])
        return Resultado(
            primeiro["nome"], _limitar(confianca, "media"), alternativas[:2], primeiro["link"], "; ".join(obs),
            "+".join([regra] + sufixos), ano_gravacao=ano,
        )

    # --- sem obra: candidatos pelo título, no MusicBrainz e no Deezer ------

    def _por_titulo(self, titulo: str, nomes: list[str], avisos: list[str]) -> Resultado:
        # As duas fontes são consultadas de forma independente e depois unidas
        # pelo nome do artista: o certo pode estar em uma e não na outra.
        dados = self.cliente.mb("recording", query=f'recording:"{_frase(titulo)}"', limit=100)
        candidatos = _agrupar_por_artista(
            [
                g
                for g in dados.get("recordings", [])
                if titulos_parecidos(g.get("title"), titulo)
                and not _versao_derivada(titulo, g.get("title"), _credito(g), g.get("disambiguation"))
            ]
        )
        for c in candidatos:
            c.update(posicao=None, fontes="MusicBrainz")

        faixas = self._faixas_deezer(titulo)
        for nome in nomes[:MAX_COMPOSITORES_DEEZER]:
            if self._nome_do_cliente(nome):
                continue  # no modo relatório isso só traria o dono do relatório para a frente
            # Busca dirigida "compositor + título": acha o compositor-intérprete
            # mesmo que ele não esteja entre os primeiros resultados do título.
            for faixa in self._faixas_deezer(titulo, artista=nome):
                if self._e_compositor({"nome": faixa["artista"], "ids": set()}, [nome], {}):
                    faixas.append({**faixa, "posicao": None})  # posição de outra consulta não é comparável
        for faixa in faixas:
            igual = next((c for c in candidatos if nomes_parecidos(c["nome"], faixa["artista"], parcial=False)), None)
            if igual is None:
                igual = {"nome": faixa["artista"], "gravacoes": 0, "ids": set(), "data": None,
                         "link": faixa["link"], "posicao": None, "fontes": "Deezer"}
                candidatos.append(igual)
            elif igual["fontes"] == "MusicBrainz":
                igual["fontes"] = "MusicBrainz e Deezer"
            if faixa["posicao"] is not None and (igual["posicao"] is None or faixa["posicao"] < igual["posicao"]):
                igual["posicao"] = faixa["posicao"]

        if not candidatos:
            return Resultado(
                observacao="; ".join(avisos + ["sem resultados no MusicBrainz nem no Deezer"]), regra="nao_encontrado"
            )
        self._pontuar(candidatos, nomes)
        candidatos.sort(key=lambda c: (-c["pontos"], not c["cliente"]))
        vencedor = candidatos[0]

        if len(candidatos) == 1:
            obs = avisos + ["único artista com esse título"]
        else:
            obs = avisos + [f"{len(candidatos)} artistas com esse título; escolhido pela maior pontuação"]
        if vencedor["compositor"]:
            # Sem obra que confirme, um intérprete que também é compositor da
            # linha é o melhor sinal disponível.
            confianca = "media"
            obs.append("também é compositor da música")
        elif not nomes and len(candidatos) == 1 and vencedor["fontes"] != "Deezer":
            confianca = "media"
        else:
            # Só título com vários artistas, só Deezer, ou compositor da linha não confirmado.
            confianca = "baixa"
        if nomes:
            regra = "sem_obra_compositor" if vencedor["compositor"] else "sem_obra_palpite"
        else:
            regra = "so_titulo_unico" if len(candidatos) == 1 else "so_titulo_varios"
        if self.relatorio and len(candidatos) > 1 and not vencedor["compositor"] and not vencedor["cliente"]:
            # Título comum, e a única evidência é a popularidade de alguém sem relação com o compositor.
            regra = "palpite_titulo"
            obs.append("provável homônimo, conferir")
        obs.append(f"fonte: {vencedor['fontes']}")
        return Resultado(
            vencedor["nome"], confianca, [c["nome"] for c in candidatos[1:3]], vencedor["link"], "; ".join(obs),
            f"{regra}+{SIGLA_FONTE[vencedor['fontes']]}",
        )

    def _pontuar(self, candidatos: list[dict], nomes: list[str]):
        """Soma os sinais de cada candidato usando os pesos PESO_* do topo do arquivo."""
        for c in candidatos:
            c["compositor"] = bool(nomes) and self._e_compositor(c, nomes, {})
            c["cliente"] = self._e_cliente(c)
            c["pontos"] = PESO_COMPOSITOR * c["compositor"] + PESO_MB * min(c["gravacoes"], GRAVACOES_TETO) / GRAVACOES_TETO
            if c["posicao"] is not None:
                c["pontos"] += PESO_DEEZER * (1 - c["posicao"] / MAX_FAIXAS_DEEZER)
        mais_antigos = sorted((c for c in candidatos if c["data"]), key=lambda c: c["data"])[:3]
        for lugar, c in enumerate(mais_antigos):
            c["pontos"] += PESO_DATA * (3 - lugar) / 3

    def _faixas_deezer(self, titulo: str, artista: str = "") -> list[dict]:
        """Até 25 faixas do Deezer com esse título, na ordem em que o Deezer devolve.

        Usa texto livre: a sintaxe avançada (track:"..." artist:"...") é aceita
        pela API mas não é interpretada, e devolve resultados sem relação.
        """
        consulta = f"{artista} {titulo}".strip()
        try:
            itens = self.cliente.deezer(q=consulta, limit=100).get("data", [])
        except ErroDeRede as e:
            log.error("Deezer falhou para %r: %s", consulta, e)
            if not self._falhas:
                self._falhas.append("Deezer indisponível nesta consulta (ver log); resultado sem os dados dele")
            return []
        faixas = []
        for item in itens:
            nome = (item.get("artist") or {}).get("name", "")
            if not normalizar(nome) or normalizar(nome) in ARTISTAS_IGNORADOS:
                continue
            if not any(titulos_parecidos(item.get(campo), titulo) for campo in ("title", "title_short")):
                continue
            if _versao_derivada(titulo, item.get("title"), nome):
                continue
            faixas.append(
                {"artista": nome, "posicao": len(faixas), "rank": item.get("rank") or 0, "link": item.get("link", "")}
            )
            if len(faixas) == MAX_FAIXAS_DEEZER:
                break
        if itens and not faixas:
            log.info("Deezer devolveu %d faixas para %r, nenhuma aproveitável com o título pedido", len(itens), consulta)
        return faixas
