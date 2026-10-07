"""Relatório de obras do titular (ECAD ou UBC) em um formato só, e o que se deriva dele."""

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

from cantor.matching import normalizar, normalizar_titulo

# Categorias do ECAD que são autoria da obra. As demais (E, SE, AM...) são
# editoras e administradoras: dado contratual, que nunca sai do app.
CATEGORIAS_DE_AUTOR = {"CA", "C", "A", "V", "AD", "AR", "T"}
SITUACOES = {
    "LB": "liberado", "BL": "bloqueada", "DU": "duplicidade", "HO": "homônima",
    "DP": "domínio público", "CO": "composta", "EC": "em conflito",
}
SITUACOES_DE_DUPLICIDADE = {"DU", "HO"}
TITULO_PARECIDO = 0.88  # mesma medida e limiar da metodologia do escritório


def semelhanca(a, b) -> float:
    """Similaridade de 0 a 1 entre dois textos já sem acento, caixa e pontuação."""
    return SequenceMatcher(None, normalizar(a), normalizar(b)).ratio()


@dataclass
class Titular:
    codigo: str = ""
    nome: str = ""
    pseudonimo: str = ""
    cae: str = ""
    associacao: str = ""
    categoria: str = ""
    percentual: float | None = None
    contrato: str = ""
    link: str = ""

    @property
    def e_autor(self) -> bool:
        return self.categoria in CATEGORIAS_DE_AUTOR


@dataclass
class Obra:
    codigo: str = ""
    iswc: str = ""
    titulo: str = ""
    associacao: str = ""
    situacao: str = ""
    tipo: str = ""
    nacional: str = ""
    inclusao: str = ""  # data de inclusão no cadastro: só é usada dentro do app
    titulares: list[Titular] = field(default_factory=list)

    @property
    def autores(self) -> list[Titular]:
        return [t for t in self.titulares if t.e_autor]

    @property
    def situacoes(self) -> list[str]:
        """A situação pode vir composta ("BL/DU")."""
        return [s for s in self.situacao.split("/") if s]


@dataclass
class Relatorio:
    origem: str = ""  # "ECAD" ou "UBC"
    codigo_titular: str = ""
    nome_titular: str = ""
    pseudonimo_titular: str = ""
    emitido_em: str = ""
    total_declarado: int | None = None
    obras: list[Obra] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def para_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def de_dict(cls, dados: dict) -> "Relatorio":
        obras = [
            Obra(**{**o, "titulares": [Titular(**t) for t in o.get("titulares", [])]}) for o in dados.get("obras", [])
        ]
        return cls(**{**dados, "obras": obras})

    def e_o_titular(self, titular: Titular) -> bool:
        if self.codigo_titular and titular.codigo:
            return titular.codigo == self.codigo_titular
        return normalizar(titular.nome) == normalizar(self.nome_titular)

    def pseudonimos(self) -> dict[str, list[str]]:
        """{chave do autor: [pseudônimos]} juntando todas as obras: o mesmo autor aparece com nomes diferentes."""
        achados = {}
        if self.pseudonimo_titular:
            achados.setdefault(self.codigo_titular or normalizar(self.nome_titular), {})[
                normalizar(self.pseudonimo_titular)
            ] = self.pseudonimo_titular
        for obra in self.obras:
            for autor in obra.autores:
                if autor.pseudonimo and normalizar(autor.pseudonimo) != normalizar(autor.nome):
                    achados.setdefault(chave_do_autor(autor), {}).setdefault(normalizar(autor.pseudonimo), autor.pseudonimo)
        return {chave: list(nomes.values()) for chave, nomes in achados.items()}

    def nomes_artisticos(self) -> list[dict]:
        """Pseudônimos do titular e dos coautores, para o usuário confirmar ou remover.

        Cada item: {"nome", "de" (nome civil), "titular" (é o dono do relatório?), "obras" (em quantas aparece)}.
        O titular vem primeiro; depois, os coautores mais frequentes.
        """
        itens = {}
        for obra in self.obras:
            for autor in obra.autores:
                if not autor.pseudonimo or normalizar(autor.pseudonimo) == normalizar(autor.nome):
                    continue
                chave = (chave_do_autor(autor), normalizar(autor.pseudonimo))
                item = itens.setdefault(
                    chave, {"nome": autor.pseudonimo, "de": autor.nome, "titular": self.e_o_titular(autor), "obras": 0}
                )
                item["obras"] += 1
        principal = normalizar(self.pseudonimo_titular)
        if principal and not any(i["titular"] and normalizar(i["nome"]) == principal for i in itens.values()):
            itens[("", principal)] = {"nome": self.pseudonimo_titular, "de": self.nome_titular, "titular": True, "obras": 0}
        return sorted(itens.values(), key=lambda i: (not i["titular"], -i["obras"], i["nome"]))

    def duplicidades(self) -> list[dict]:
        """Obras que podem ser a mesma música cadastrada duas vezes.

        Devolve grupos {"motivo", "obras": [Obra]}: "titulo" quando os títulos são
        iguais ou parecidos; "situacao" quando o próprio cadastro marca DU ou HO
        e a obra não entrou em nenhum grupo de título.
        """
        grupo_de = list(range(len(self.obras)))

        def raiz(i):
            while grupo_de[i] != i:
                grupo_de[i] = grupo_de[grupo_de[i]]
                i = grupo_de[i]
            return i

        titulos = [normalizar_titulo(o.titulo) for o in self.obras]
        for i in range(len(self.obras)):
            for j in range(i + 1, len(self.obras)):
                if titulos[i] and titulos[j] and (
                    titulos[i] == titulos[j] or SequenceMatcher(None, titulos[i], titulos[j]).ratio() >= TITULO_PARECIDO
                ):
                    grupo_de[raiz(j)] = raiz(i)
        juntas = {}
        for i, obra in enumerate(self.obras):
            juntas.setdefault(raiz(i), []).append(obra)
        grupos = [{"motivo": "titulo", "obras": obras} for obras in juntas.values() if len(obras) > 1]
        agrupadas = {id(o) for g in grupos for o in g["obras"]}
        grupos += [
            {"motivo": "situacao", "obras": [o]}
            for o in self.obras
            if SITUACOES_DE_DUPLICIDADE.intersection(o.situacoes) and id(o) not in agrupadas
        ]
        return grupos

    def termos_sensiveis(self) -> set[str]:
        """Tudo o que não pode aparecer em requisição externa: CAE/IPI, códigos de titulares e de obras
        no cadastro, percentuais, datas de contrato e de inclusão, e nomes de editoras e administradoras."""
        termos = set()
        for obra in self.obras:
            termos.update(x for x in (obra.codigo, obra.inclusao) if x)
            for t in obra.titulares:
                termos.update(x for x in (t.codigo, t.cae, t.contrato) if x)
                if t.percentual is not None:
                    termos.add(f"{t.percentual:.2f}".replace(".", ","))
                if not t.e_autor and t.nome:
                    termos.add(t.nome)
        return termos


def chave_do_autor(titular: Titular) -> str:
    return titular.codigo or normalizar(titular.nome)
