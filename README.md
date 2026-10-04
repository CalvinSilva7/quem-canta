# Quem canta?

App web local (Streamlit) que preenche o **cantor/intérprete** das músicas de uma
planilha, consultando o [MusicBrainz](https://musicbrainz.org) e o Deezer.
Não usa IA: tudo o que aparece vem de um registro encontrado nessas bases, com link.

## Rodar

Precisa de Python 3.11 ou mais novo.

```powershell
cd C:\Users\Calvin\quem-canta
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m streamlit run app.py
```

Depois abra http://localhost:8501 no navegador. Com o ambiente já criado, basta
dar dois cliques em `rodar.cmd`, que sobe o app e abre o navegador sozinho.

## Usar

1. Envie um `.xlsx` ou `.csv`. Há um exemplo em `exemplos/amostra.csv`.
2. Confira em **Colunas da planilha** se título, compositor e créditos foram
   reconhecidos (cabeçalhos como "Música", "Nome", "Autor" são aceitos). Se o
   título não for reconhecido, o app pede para você escolher.
3. Clique em **Processar**. Cada música nova leva de 2 a 5 segundos, porque o
   MusicBrainz permite uma consulta por segundo. Mil músicas levam perto de uma hora
   na primeira vez; depois saem do cache na hora.
4. Na **Prévia**, linhas amarelas são de baixa confiança e vermelhas são as não
   encontradas. Dê dois cliques em `cantor_sugerido` para corrigir.
5. Baixe a planilha original com as colunas novas, em Excel ou CSV.

### Colunas novas

| Coluna | Conteúdo |
| --- | --- |
| `cantor_sugerido` | Intérprete sugerido (vazio se não houver evidência) |
| `confianca` | `alta`, `media`, `baixa` ou `nao_encontrado` |
| `alternativas` | Até 2 outros cantores, separados por `;` |
| `fonte_link` | Link da gravação no MusicBrainz ou no Deezer, ou `correção manual` |
| `observacao` | Por que essa resposta e esse nível de confiança |
| `regra` | Identificador da regra que gerou a sugestão (ver "Regras" abaixo) |

## Como a resposta é escolhida

**Linha com compositor** (ou com créditos, se o compositor estiver vazio)

1. Busca a obra pelo título já filtrando pelos compositores informados. Se não
   achar, busca só pelo título e resolve apelidos pelos aliases do MusicBrainz
   ("Tom Jobim" = "Antônio Carlos Jobim").
2. Lista as gravações da obra e sugere o artista da **gravação mais antiga**.
   Em empate de data, prefere quem também é compositor.
   Só vale como data o lançamento com status Official que não seja demo: se a
   mais antiga só existe em bootleg, promocional ou demo, o app usa a próxima
   oficial e avisa na observação. Ao vivo oficial e coletânea oficial contam.
   Isso custa uma consulta a mais por gravação conferida (até 8 por linha).
   Se a mais antiga só existe em lançamento ao vivo, ser compositor não basta
   como segundo sinal (compositores tocam a música ao vivo antes de alguém
   lançá-la): ela precisa ser de quem mais gravou ou do mais popular no Deezer.
3. Confiança `alta` exige uma única obra compatível **e um segundo sinal** de que
   a mais antiga é mesmo a original. O artista precisa ser pelo menos um destes:
   compositor da obra; quem mais gravou a obra (com 2 gravações ou mais); o mais
   popular no Deezer para o título. Sem isso fica `media`, com a observação
   "mais antiga no MusicBrainz, sem confirmação".
4. Também cai para `media` quando há mais de uma obra compatível (as gravações
   são somadas), quando a data empata ou quando a obra tem mais de 1.500 gravações.
   Cai para `baixa` se nenhuma gravação tiver data.
5. Se a mais antiga não tem segundo sinal e o artista mais popular no Deezer é um
   dos compositores, o compositor é sugerido no lugar, com `media`.
6. Se nenhuma obra confirmar o compositor, segue pela busca por título abaixo.

**Busca por título** (linha só com título, ou compositor não confirmado)

Os candidatos vêm do MusicBrainz e do Deezer, de forma independente, e são
unidos pelo nome do artista. Karaokês, remixes, tributos, instrumentais e covers
declarados são descartados. Cada candidato recebe pontos (pesos em
`cantor/busca.py`):

| Sinal | Pontos |
| --- | --- |
| Nome bate com um compositor da linha | 100 |
| Posição no Deezer (1º resultado vale tudo, cai até o 25º) | até 40 |
| Nº de gravações no MusicBrainz (2,5 por gravação, até 10) | até 25 |
| Gravação datada mais antiga (2ª e 3ª valem menos) | até 15 |

- Vencedor é compositor da linha: `media`.
- Só título e um único artista, presente no MusicBrainz: `media`.
- Demais casos (vários artistas, candidato só do Deezer, compositor não
  confirmado): `baixa`.
- Nada em lugar nenhum: `nao_encontrado`, cantor vazio.

A observação diz de onde veio o candidato (`fonte: MusicBrainz`, `Deezer` ou os dois).

**Modo relatório** (a planilha é o catálogo de um compositor só)

O app liga esse modo sozinho quando o mesmo compositor aparece em pelo menos 90%
das linhas (planilhas com 5 linhas ou mais), avisa na tela e deixa desligar.
Parceiros na mesma célula ("Fulano / Parceiro") não atrapalham a detecção.

- Ser o dono do relatório **não é evidência** de quem gravou: não soma pontos,
  não vale como segundo sinal e não dá alta. Só serve de último desempate.
  Parceiros dele continuam valendo como compositores.
- Antes de processar, o app lista de uma vez todas as obras ligadas a ele no
  MusicBrainz (fica em cache) e procura cada título nessa lista. Só cai na busca
  geral se não achar. Isso evita músicas homônimas de outros autores.
- A lista traz dois tipos de obra: as que têm o compositor cadastrado como autor
  (sufixo `catalogo`) e as que estão ligadas a ele só porque ele as gravou, sem
  autor nenhum cadastrado (`catalogo_sem_autor`, confiança no máximo média).
  Obras de outros autores que ele só gravou são descartadas.
- No `avaliar.py`, `--sem-modo-relatorio` desliga a detecção.

**Linha com ISWC** (código da obra)

A coluna é reconhecida por nomes como "ISWC" ou "Código da obra". Os formatos
`T-123.456.789-0`, `T-123456789-0` e `T1234567890` são aceitos, e o dígito
verificador é conferido; código inválido é anotado na observação e ignorado.

1. O app procura o ISWC no MusicBrainz e no Credits.fm. Se o título da obra
   encontrada confere com o da linha, a obra está definida e substitui a busca
   por título e compositor (homônimos ficam de fora). Se o título não confere, o
   ISWC é ignorado (`iswc_titulo_diverge`) e vale a lógica de sempre.
2. Pela obra do MusicBrainz, o intérprete sai pelas regras de sempre (gravação
   oficial mais antiga, segundo sinal etc.).
3. Pelo Credits.fm vêm as gravações (ISRC) da obra. O app pega as de ISRC mais
   antigo (o ano está no próprio código) e confere o artista no Deezer, pelo ISRC.
4. Se as duas fontes apontam o mesmo intérprete, a linha sai **alta**
   (`fontes_concordam`). Se discordam, no máximo média, com o outro nas
   alternativas. Obra só no Credits.fm: no máximo média (`iswc_isrc_mais_antigo`).
5. Colunas novas: `iswc_normalizado` e `iswc_encontrado_em` (MB, Credits.fm,
   ambos ou nenhum). Sufixos: `iswc_mb`, `iswc_credits`, `iswc_ambos`,
   `iswc_invalido`, `iswc_nao_encontrado`, `iswc_titulo_diverge`.

Sem ISWC, ou com ISWC que não aparece em nenhuma fonte, nada muda.

**Atribuição:** os dados do Credits.fm (https://credits.fm) são publicados sob a
licença [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Quando a
planilha usa esses dados, o Excel exportado leva uma aba "fontes" com a
atribuição, e a observação das linhas afetadas diz "dados Credits.fm, CC BY 4.0".

**Regras** (coluna `regra`)

O identificador diz por qual caminho a sugestão saiu. Serve para medir o acerto
de cada regra com o `avaliar.py` antes de mexer em qualquer nível de confiança.

| Regra | Significado |
| --- | --- |
| `correcao_manual` | Correção salva por você |
| `obra_compositor` | Obra confirmada; a gravação mais antiga é de um compositor |
| `obra_mais_gravado` | ... é de quem mais gravou a obra |
| `obra_popular_deezer` | ... é do artista mais popular no Deezer para o título |
| `obra_sem_confirmacao` | ... sem nenhum segundo sinal |
| `obra_compositor_popular` | Mais antiga sem confirmação; sugerido o compositor, o mais popular no Deezer |
| `obra_sem_data` | Obra confirmada, mas nenhuma gravação tem data |
| `sem_obra_compositor` | Compositor não confirmado por obra; o candidato também é compositor da linha |
| `sem_obra_palpite` | Compositor não confirmado por obra; melhor candidato pelo título |
| `so_titulo_unico` | Linha só com título; um único artista |
| `so_titulo_varios` | Linha só com título; vários artistas |
| `nao_encontrado`, `sem_titulo`, `erro_de_rede` | Linha sem sugestão |

Sufixos, separados por `+`: `varias_obras`, `truncada` (obra com mais gravações do
que as analisadas), `relatorio`, `catalogo`, `catalogo_sem_autor` (ver modo
relatório), `ao_vivo` (a mais antiga só existe em lançamento ao vivo oficial),
`nao_oficial_ignorada` (havia gravação mais antiga só em bootleg, promocional
ou demo), `oficial_nao_verificado` (o limite de 8
verificações acabou antes), `empate`, `empate_compositor` (empate resolvido a favor do
compositor), `mb` / `deezer` / `mb_deezer` (de onde veio o candidato na busca por
título), os de ISWC acima, `fontes_concordam` / `fontes_discordam` e
`deezer_falhou` (Deezer ou Credits.fm não responderam). Exemplo: `obra_compositor+varias_obras`.

**Erros de API**

- MusicBrainz fora do ar depois de 4 tentativas (espera de 2, 4 e 8 segundos):
  linha vazia com a observação `erro de rede`.
- Deezer fora do ar: a linha é resolvida só com o MusicBrainz e a observação
  avisa `Deezer indisponível`.

Nos dois casos o resultado **não** entra no cache (processe de novo para
completar) e o erro fica registrado em `dados/quem_canta.log`.

## Dados salvos

Tudo fica em `dados/quem_canta.db` (SQLite):

- **Cache** por (título normalizado, compositor normalizado). Use *Ignorar cache*
  ou *Limpar cache* na barra lateral para consultar de novo.
- **Correções manuais**: têm prioridade sobre as APIs nas próximas planilhas e
  entram com confiança `alta`. Apagar o cantor de uma linha corrigida remove a correção.

Para usar outro arquivo de banco, defina a variável `QUEMCANTA_DB`.

## Contato no User-Agent

O MusicBrainz pede que cada aplicativo se identifique com um contato. Preencha o
campo **Contato para o MusicBrainz** na barra lateral ou defina `QUEMCANTA_CONTATO`
(e-mail ou site) antes de abrir o app. Sem isso o app funciona, mas se identifica
só como `QuemCanta/0.2`, e o MusicBrainz pode limitar o acesso.

## Medir a taxa de acerto

Com uma planilha em que o cantor já foi preenchido e conferido à mão:

```powershell
.venv\Scripts\python avaliar.py exemplos\amostra.csv
.venv\Scripts\python avaliar.py minha_planilha.xlsx --coluna-cantor "Intérprete" --erros erros.csv
.venv\Scripts\python avaliar.py musicas.xlsx --gabarito gabarito.xlsx
```

Mostra linhas, acertos e taxa por nível de confiança (`taxa_top3` conta também as
alternativas), os acertos por regra e a lista de erros com confiança alta. As correções manuais são
ignoradas na avaliação. `--sem-cache` refaz as consultas e `--limite N` avalia só
as N primeiras linhas.

Com `--gabarito`, a resposta certa vem de outra planilha, casada pela coluna `id`
(ou pela ordem das linhas). Uma coluna `tambem_aceito` pode listar outras
respostas válidas, separadas por `;`.

## Estrutura

```
app.py              tela do Streamlit
avaliar.py          script de avaliação
cantor/busca.py     MusicBrainz, Deezer, retry e a lógica de escolha
cantor/matching.py  normalização e comparação aproximada (rapidfuzz)
cantor/planilha.py  leitura, detecção de colunas e exportação
cantor/banco.py     SQLite: cache e correções
tests/              testes sem rede: .venv\Scripts\python -m pytest
```

## Limites conhecidos

- "Gravação mais antiga" depende da data de lançamento cadastrada no MusicBrainz.
  Se a gravação original não estiver lá, a sugestão é a mais antiga que existe
  na base. O segundo sinal reduz esse erro em "alta", mas ele continua
  aparecendo em "média".
- A busca avançada do Deezer (`track:"..." artist:"..."`) não funciona hoje: a
  API aceita a consulta e devolve resultados sem relação. O app usa texto livre.
- A célula editável da prévia não recebe a cor de destaque (limitação do
  Streamlit); o resto da linha recebe.
- Lê apenas a primeira aba do Excel.
