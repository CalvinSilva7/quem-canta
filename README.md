# Quem canta?

App web local (Streamlit) que preenche o **cantor/intérprete** das músicas de uma
planilha, consultando o [MusicBrainz](https://musicbrainz.org) e o Deezer.
Não usa IA: tudo o que aparece vem de um registro encontrado nessas bases, com link.

## Rodar

Precisa de Python 3.11 ou mais novo.

```powershell
cd quem-canta
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
   Só vale como data o lançamento que não seja demo nem esteja marcado como
   bootleg, promocional ou pseudo-lançamento: se a mais antiga só existe assim,
   o app usa a próxima oficial e avisa na observação. Lançamento sem status
   cadastrado no MusicBrainz conta como oficial (`status_nao_cadastrado`). Ao vivo oficial e coletânea oficial contam.
   Isso custa uma consulta a mais por gravação conferida (até 8 por linha).
   Se a mais antiga só existe em lançamento ao vivo, ser compositor não basta
   como segundo sinal (compositores tocam a música ao vivo antes de alguém
   lançá-la): ela precisa ser de quem mais gravou ou do mais popular no Deezer.
3. Confiança `alta` exige uma única obra compatível **e um segundo sinal** de que
   a mais antiga é mesmo a original. O artista precisa ser pelo menos um destes:
   compositor da obra (o crédito inteiro: uma dupla ou grupo que só tem um
   compositor dentro não dá alta, fica `media` com `obra_compositor_parcial`); quem mais gravou a obra (com 2 gravações ou mais); o mais
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

*Nomes artísticos do compositor.* O relatório costuma trazer o nome civil, e as
bases, o nome artístico, um projeto ou um grupo. No modo relatório há um campo
para informar esses nomes, separados por `;`.

- O app sugere nomes de dois jeitos: artistas cujo nome usa só palavras do nome
  civil (botão **Sugerir nomes artísticos**) e, depois de processar, artistas que
  apareceram em várias linhas. Sugestão nenhuma vale sozinha: só entra quando
  você marca e confirma.
- Um nome confirmado vale como o próprio compositor em todas as regras (ou seja,
  não é evidência de quem gravou, só desempate), e as obras dele no MusicBrainz
  entram na lista de obras do relatório.
- A discografia de cada nome confirmado é listada de uma vez no Deezer (álbuns e
  faixas) e no MusicBrainz, se o artista existir lá, e fica em cache. Quando a
  linha não tem obra confirmada, o título é procurado nessa discografia **antes**
  da busca por título, que fica de reserva (regra `discografia`):
  um nome confirmado com o título, em uma base só, é `media`; se Deezer e
  MusicBrainz listam os dois o título na discografia dele, `alta`; se o título
  está na discografia de mais de um nome confirmado, `baixa` (`varios_nomes`).
- Quando a obra existe mas a gravação mais antiga ligada a ela não tem segundo
  sinal, o app também olha a discografia: se um nome confirmado lançou o título
  **antes** dessa gravação, ela não é a original e o nome confirmado é sugerido,
  com `media` (regra `obra_discografia`). Se a obra não tem nenhuma gravação
  datada e o título está na discografia dele, ele é sugerido com `baixa`.
- Se a linha cai na busca por título, há vários artistas e o escolhido não tem
  relação com o compositor, a regra é `palpite_titulo` e a observação diz
  "provável homônimo, conferir".
- No `avaliar.py`: `--nomes-artisticos "Nome Um; Projeto Dois"`.

**Data de cadastro da obra** (coluna "Data de cadastro", como no relatório da UBC)

Se a gravação mais antiga encontrada é de mais de 3 anos depois do cadastro, a
original provavelmente não está na base: a confiança fica no máximo `media` e a
observação diz "possível original ausente na base" (sufixo `cadastro_anterior`).
A data é usada só dentro do app: não vai para nenhuma API nem para o cache, e
não é repetida na observação.

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
| `obra_compositor_parcial` | ... é de uma dupla, grupo ou parceria em que um compositor só participa |
| `obra_mais_gravado` | ... é de quem mais gravou a obra |
| `obra_popular_deezer` | ... é do artista mais popular no Deezer para o título |
| `obra_sem_confirmacao` | ... sem nenhum segundo sinal |
| `obra_compositor_popular` | Mais antiga sem confirmação; sugerido o compositor, o mais popular no Deezer |
| `obra_sem_data` | Obra confirmada, mas nenhuma gravação tem data |
| `sem_obra_compositor` | Compositor não confirmado por obra; o candidato também é compositor da linha |
| `sem_obra_palpite` | Compositor não confirmado por obra; melhor candidato pelo título |
| `so_titulo_unico` | Linha só com título; um único artista |
| `so_titulo_varios` | Linha só com título; vários artistas |
| `discografia` | Modo relatório: título na discografia de um nome artístico confirmado |
| `obra_discografia` | Modo relatório: obra sem segundo sinal; o nome artístico confirmado lançou o título antes |
| `palpite_titulo` | Modo relatório: vários artistas e o escolhido não tem relação com o compositor |
| `nao_encontrado`, `sem_titulo`, `erro_de_rede` | Linha sem sugestão |

Sufixos, separados por `+`: `varias_obras`, `truncada` (obra com mais gravações do
que as analisadas), `relatorio`, `catalogo`, `catalogo_sem_autor` (ver modo
relatório), `ao_vivo` (a mais antiga só existe em lançamento ao vivo oficial),
`nao_oficial_ignorada` (havia gravação mais antiga só em bootleg, promocional
ou demo), `varios_nomes` (título na discografia de mais de um nome artístico confirmado),
`status_nao_cadastrado` (os lançamentos da mais antiga estão sem status no MusicBrainz),
`cadastro_anterior` (obra cadastrada mais de 3 anos antes da gravação mais antiga encontrada), `oficial_nao_verificado` (o limite de 8
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

## Créditos nas plataformas (em construção, por etapas)

Fluxo novo, para o escritório: em vez de apontar o intérprete original, verificar
se cada gravação das obras de um titular está com o compositor creditado em cada
plataforma. Fica no pacote `creditos/`, separado do motor acima, que continua
valendo como módulo. Nada aqui usa IA: são regras fixas.

Etapa 1 (pronta): importar o relatório e classificar créditos.

```powershell
.venv\Scripts\python -m streamlit run creditos_app.py
.venv\Scripts\python -m creditos.ecad relatorio.pdf --output obras.json
```

- `creditos/ecad.py` lê o "Relatório analítico de titular autoral e suas obras"
  do ECAD (PDF): obra, ISWC, situação, data de inclusão e, por titular, nome,
  pseudônimo, CAE, associação, categoria e percentual. Confere a contagem com o
  total que o próprio relatório declara; o que não bate vira aviso na tela.
  `creditos/ubc.py` lê o relatório de obras em planilha. O arquivo é sempre
  enviado à mão: o app não acessa o ECADNET nem portal de associação.
- Os pseudônimos do titular e dos coautores entram como nomes artísticos, e a
  tela mostra a lista para confirmar ou remover. Títulos iguais ou parecidos e
  situações DU/HO são sinalizados como possível duplicidade.
- `creditos/classificador.py` compara o crédito exibido em uma gravação com
  todos os autores de todos os registros do título e devolve o status da
  metodologia do escritório (SEM CRÉDITOS, VIOLAÇÃO - crédito errado, OK...),
  o fundamento e se precisa de revisão. Erro técnico nunca vira SEM CRÉDITOS, e
  título só aproximado nunca sustenta um resultado negativo.
- `creditos/filtro.py` barra percentuais, CAE/IPI, códigos do cadastro, datas
  de contrato e de inclusão, editoras e CPF em qualquer texto que vá para fora.

### As duas etapas

A tela trabalha em duas etapas, como o escritório:

1. **Buscar intérpretes** (`creditos/interpretes.py`): Deezer e Apple Music, sem navegador. Devolve uma planilha só
   com obra e intérprete, para o compositor conferir e corrigir.
2. **Verificar créditos e tirar prints**: exige a planilha de intérpretes conferida. O app verifica só os pares
   (obra, intérprete) dela; não descobre nem presume intérprete.

Plataformas da etapa 2: YouTube Music, Spotify, Tidal, Deezer, Vagalume, Apple Music e Amazon Music. A Amazon tem
dois coletores: o site (`creditos/amazon.py`, sem login, um print do menu da faixa, que não tem item de créditos) e
o aplicativo de desktop (`creditos/amazon_app.py`, só Windows, com a conta já logada no aplicativo; dois prints:
o menu e a janela "Créditos"). O do aplicativo foi escrito sem um Windows para testar: `Testar Amazon.cmd` roda
um diagnóstico que grava o que o aplicativo deixou ver.

A barra de progresso e o botão de parar ficam em `creditos/andamento.py`; o print de tela inteira, em `creditos/tela.py`.

### Instalar no Windows e atualizar

`python empacotar.py` monta `quem-canta-<versão>-windows.zip`. Quem recebe o zip
extrai, dá dois cliques em `Instalar.cmd` uma vez e passa a abrir o app por
`Abrir Quem Canta.cmd`. Os casos e os prints ficam em `Documentos\Quem Canta`,
fora da pasta do programa.

O app instalado consulta `versao-publicada.json` na branch `main` deste
repositório, ao abrir e pelo botão **Procurar atualização**. Se a versão
publicada for maior que a instalada, aparece o botão para baixar e instalar: o
zip só é instalado se o SHA-256 conferir com o publicado.

Para publicar uma versão:

1. Aumente `VERSAO` em `creditos/versao.py`.
2. Rode os testes e `python empacotar.py . "o que mudou"`. Isso gera o zip e
   regrava `versao-publicada.json` com o hash dele.
3. Crie a release `v<versão>` no GitHub com o zip anexado.
4. Só depois suba o `versao-publicada.json` para a `main`. Nessa ordem, nenhum
   app instalado é avisado de uma versão cujo zip ainda não existe.

`QUEMCANTA_ATUALIZACAO=desligada` desliga a consulta; outro endereço pode ser
dado na mesma variável ou em `atualizacao.json`.

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
