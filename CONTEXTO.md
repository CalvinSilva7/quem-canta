# Contexto do projeto

Estado em 15/10/2026, versão 0.5.49. Escrito para quem assume o projeto sem ter acompanhado a construção: o que
existe, o que foi validado e onde, o que deu errado e por quê, e o que está pendente.

Este arquivo é público. Não traz nome de cliente, de música nem de artista; os casos reais usados nos testes ficam
em anotações privadas do dono do projeto.

## 1. O que o app faz

Duas abas, independentes:

1. **Buscar intérpretes.** Entra o relatório do ECAD (PDF, ou planilha com título e compositor). Sai uma planilha
   de duas colunas, obra e intérprete. Em preto, o intérprete de que o app tem certeza; em vermelho, o que ele só
   supõe. Obra sem intérprete achado sai com a segunda coluna em branco. Usa Deezer e depois Apple Music, sem
   navegador e sem print. A planilha vai para o compositor conferir.
2. **Coleta de prints.** Entram o relatório e a planilha de intérpretes **conferida pelo compositor** (obrigatória:
   sem ela o botão de coletar não aparece). Para cada par obra + intérprete, o app acha a gravação em cada
   plataforma marcada, lê o crédito de compositor, classifica e tira o print. Saem a planilha de obras (formato do
   escritório), a lista da petição (.docx) e as provas por plataforma, num zip. As provas vêm em PDF e também em
   Word (decisão do dono, 14/10/2026), com as mesmas páginas, para quem precisa editar.

O app roda no computador de quem usa (Windows, no escritório). Não há servidor. A instalação é por máquina
(`Instalar.cmd`) e a atualização é pelo botão "Procurar atualização".

## 2. Onde está cada coisa

| Arquivo | Papel |
|---|---|
| `creditos_app.py` | A tela (Streamlit): as duas abas, o andamento, o botão de atualização, "Recomeçar do zero" |
| `creditos/pipeline.py` | O motor: `executar()` roda as plataformas pedidas; um `coletar_<plataforma>` para cada |
| `creditos/classificador.py` | Decide o status de cada gravação a partir do crédito exibido |
| `creditos/interpretes.py` | A busca de intérpretes, a planilha em preto e vermelho, a leitura da planilha conferida |
| `creditos/saida.py`, `provas.py` | Planilha de obras, lista da petição e provas (PDF e Word) |
| `creditos/captura.py`, `tela.py` | O print: faixa de carimbo, SHA-256, ficha `.json`; a foto do monitor inteiro |
| `creditos/navegador.py` | O Chromium do app (Playwright): ritmo, sinais de bloqueio, perfil com sessão guardada |
| `creditos/deezer.py`, `apple.py`, `ytmusic.py`, `spotify.py`, `tidal.py`, `amazon.py`, `amazon_app.py`, `claro.py`, `palco.py`, `vagalume.py` | Um coletor por plataforma |
| `creditos/cdp.py` | Conversa direta com a porta de depuração do aplicativo da Amazon (o Playwright não conecta nele) |
| `creditos/ecad.py`, `ubc.py` | Leitura do relatório em PDF e em planilha |
| `creditos/filtro.py` | O filtro do que pode sair do computador |
| `creditos/andamento.py` | Barra de progresso e a tarefa em segundo plano (uma por vez) |
| `creditos/atualizacao.py`, `empacotar.py`, `versao-publicada.json` | A atualização dos apps instalados |
| `cantor/`, `app.py` | Primeira versão do projeto (achar o cantor de uma planilha). Ainda é usada: `cantor.matching` |

Dados de quem usa: `Documentos\Quem Canta\casos\<compositor>\<plataforma>\` (leituras em `leituras\*.json`, prints
`.png` com `.html` e `.json` ao lado). As sessões da Claro e da Deezer ficam em `Documentos\Quem Canta\perfil-claro`
e `perfil-deezer`, fora dos casos. Variáveis: `QUEMCANTA_DADOS` (pasta de dados), `QUEMCANTA_DB` (banco do `cantor`, usado nos testes),
`QUEMCANTA_DEV` (mostra o campo "quantas obras", só para teste).

## 3. Como classificar (resumo)

- **Firme** entra na contagem da petição e no PDF. **A confirmar** (`revisar=True`) fica fora dos dois e aparece
  em amarelo, com o motivo.
- Homônimo provável, medley e título aproximado nunca são firmes.
- Aviso de bloqueio procurado no texto da página ignora os títulos e intérpretes do caso (0.5.45): uma obra cujo
  título era igual a um dos avisos parava a coleta do Spotify sempre na mesma faixa, e parecia bloqueio por volume.
  Antes de concluir "a plataforma bloqueou", ver o motivo gravado na aba Resumo e em que faixa parou.
- Crédito que traz só o primeiro nome do nome artístico do compositor conta como creditado (decisão do dono em
  13/10/2026, 0.5.40): não é violação nem vai para "a confirmar". Não vale para o sobrenome sozinho, para o primeiro
  nome do nome civil, nem para nome de menos de quatro letras.
- Título com grafia diferente da do cadastro (uma letra que seja) fica "a confirmar", com print. Decisão do dono em
  13/10/2026: não tolerar letra no título, porque o funcionário tem de conferir sempre. A tolerância de uma letra vale
  só para o nome do intérprete conferido. Desde a 0.5.39, título com grafia diferente e medley, quando têm print,
  entram na parte "A confirmar antes de usar" da lista e do PDF; a homônima provável (sem print) fica só na planilha.
- **Intérprete conferido pelo compositor** (veio da planilha): crédito só com outros nomes é violação firme.
  Decisão do dono: o compositor já disse que aquele intérprete gravou a obra dele.
- Nome artístico de uma palavra seguido de sobrenome que o relatório não traz é "variante a confirmar", e não
  crédito errado. O dono pode cadastrar o nome em "Outros nomes artísticos do titular".
- **Karaokê fica fora da coleta do YouTube Music** (decisão do dono em 15/10/2026, 0.5.48): vídeo com "karaokê" no
  título não é aberto, não tem print e não entra na lista nem no PDF; o resumo diz quantos foram deixados de fora.
  Se a própria obra tem "karaokê" no título, a palavra não conta. Cover, playback e parecidos continuam entrando
  (só vão para o fim da fila de cada intérprete).
- O que a plataforma escreve no lugar dos nomes ("Créditos indisponíveis", "Desconhecido") não é nome de autor:
  a faixa fica sem créditos.

## 4. Estado de cada plataforma

"Validado" quer dizer: comparado com o gabarito do escritório (planilha preenchida à mão e documento com os prints).

| Plataforma | Como acha a gravação | Print | Situação |
|---|---|---|---|
| Deezer | API pública, sem navegador | 2 por música, logado na conta (decisão do dono, 14/10/2026): o menu da faixa com "Consulte créditos musicais" destacado, um clique antes, e o painel de créditos | Leitura validada (sem login). Print corrigido na 0.5.18 e visto em 1 faixa, no Mac. **Print logado ainda não testado com conta real**: a sessão é reconhecida pelo cookie `arl`; visitante não o tem (conferido) |
| Apple Music | API do iTunes e página do álbum | Fora da coleta de prints (decisão do dono) | Só na busca de intérpretes. Recusa buscas (403/429) em relatório grande |
| YouTube Music | Busca do site | Menu da faixa | Validado com três gabaritos (no terceiro, Mac, 0.5.38: 29 de 31 links; os 2 restantes saem "a confirmar" por grafia do título). Intérprete conferido vale com uma letra de diferença e, se a busca mostra os músicos no lugar da banda, o app consulta o canal do vídeo (oEmbed público, só o código do vídeo sai); tela inteira validada no Windows |
| Spotify | Busca do site (cada consulta também na forma enxuta) | Janela "Créditos" | Validado no Mac e no Windows (0.5.36): 10 de 10 e 4 de 4. As leituras anteriores à 0.5.35 podiam trazer violação falsa |
| Tidal | Página do artista (fraco) | Tocador com a aba "Créditos" (prévia de 30 s, sem login) | **3 de 13 no gabarito.** Ver seção 6 |
| Amazon (aplicativo) | Busca do site, depois o aplicativo de desktop | 2 por música: menu com "Créditos" e janela de créditos | Validado no Windows: 9 de 9 e 5 de 5 |
| Amazon (site) | As mesmas faixas do aplicativo, só as que vieram sem o titular nele | Menu da faixa, sem item de créditos | Funciona; a busca acha poucas faixas. Desde a 0.5.49 não roda sozinho (ver abaixo) |

**Amazon é uma escolha só** (decisão do dono em 15/10/2026, 0.5.49), como o escritório faz à mão: primeiro o
aplicativo de desktop em todas as músicas; depois, só para as que vieram sem o titular no aplicativo (sem créditos
ou crédito de outros), o print do menu da mesma faixa no site, que não tem tela de créditos. Saem duas pastas de
provas, "Amazon Music (aplicativo)" e "Amazon Music (site)". Sem o aplicativo (Mac, ou programa não instalado), o
site não fotografa nada: não dá para saber quais músicas estão sem o titular. `coletar_amazon_completa`.
| Claro Música | Lista de músicas da busca, logado; página do artista como último recurso | Álbum com o menu da faixa e o cartão "Informações e créditos" | Validado no Windows: 5 de 6 (a sexta é link errado no gabarito) |
| Palco MP3 | Caixa de busca do site | Página da música; 2º print (página de créditos) quando o site corta a linha | Validado no Windows com gabarito de 2026: 3 de 3 e 5 de 6. Com o gabarito de 2024 diverge, porque o site mudou |
| Vagalume | Site de letras | Fora da coleta de prints (decisão do dono) | Código mantido, não exposto na tela |

## 5. Erros que custaram tempo, e a causa de cada um

Vale ler antes de mexer na plataforma correspondente.

**Print e captura**
- *Print tirado sem a tela certa aberta.* Na Deezer o menu abre com animação; o clique no item de créditos saía
  cedo demais, o painel não abria, a página rolava e o print era tirado assim mesmo. Regra: só fotografar depois
  de conferir que o painel ou a janela está de fato por cima (`document.elementFromPoint`), e tentar de novo.
- *Menus escondidos na página.* O aplicativo da Amazon guarda na página uma lista de playlists invisível, com
  itens parecidos com os do menu da faixa. O app leu essa lista e concluiu "sem créditos". Regra: ler só o que
  está à vista, e reconhecer o menu por itens que só ele tem.
- *Navegador antigo.* O aplicativo da Amazon embute um Chromium velho: `?.` e `??` derrubam o script inteiro. Há
  um teste que proíbe essa escrita nos scripts usados ali.
- *Carimbo escurecido.* Janela modal (créditos do Spotify) fica numa camada acima de tudo e escurece a faixa de
  carimbo. O print de tela inteira acha a janela pela linha vermelha do carimbo e desistia. O carimbo agora entra
  na mesma camada, como popover.
- *Carimbo cobrindo o que importa.* Na Tidal ele cobria a aba "Créditos"; o tocador é empurrado para baixo durante
  a captura.
- *Menu que não cabe na tela.* Na Claro o menu abre para baixo; na última faixa do álbum o item de informações
  ficava fora. Esticar a página deixava o print ilegível no PDF. Solução: levar a faixa para o alto da tela, com um
  espaço em branco depois da lista, e **não rolar de novo logo antes do clique** (fecha o menu).
- *Página que troca de música.* No Palco MP3 alguns endereços levam para outra música em menos de um segundo.
  Bloquear o autoplay não segura. Fica como "indisponível", com o motivo.
- *Print reaproveitado.* Com a tela inteira marcada, o app reaproveitava print guardado sem a foto do monitor.
  Agora só serve o print feito nesse modo; o outro é refeito.
- *PDF com um print por música.* No aplicativo da Amazon, no Palco e na Deezer a prova são dois; ver `TODOS_OS_PRINTS`.
  Na Deezer, print antigo só do painel não é reaproveitado: a música é fotografada de novo, com o menu.

- *Papéis lidos como blocos.* No Spotify, "Autores • Letrista" embaixo de um nome são dois papéis da mesma pessoa.
  O app adivinhava os blocos pela ordem dos textos, tomava "Letrista" por título e cortava a lista de autores: deu
  **violação falsa com o compositor na tela**. A página agora informa o que é título, nome e papel. Lição: não
  inferir a estrutura pela ordem do texto quando a página tem marcação; e conferir o print contra a classificação.
- *Página que troca de música, de novo.* No Palco MP3 a página existe, mas o tocador pula e ela troca junto. O que
  funciona: desligar a execução de scripts pelo navegador (`Emulation.setScriptExecutionDisabled`), na mesma
  janela, só naquela leitura. O que não funciona: segunda janela (cobre o print de tela inteira) e recusar os
  arquivos de script (o site os serve de um cache próprio; passou no Mac e falhou no Windows). Depois disso a
  busca seguinte precisa de uma página nova.
- *Página que confere aos poucos.* A página de créditos do Palco mostra "16 de 20 etapas" e pode ficar parada aí.
  Esperar enquanto avança; parada por uns 20 segundos, fotografar e anotar a etapa.

**Achar a gravação**
- *Palavras que quebram a busca.* No Spotify, "feat" e o "e" das duplas na consulta escondem a faixa; sem eles
  ela vem em primeiro lugar (`_consulta_enxuta`). Só foi aplicado ao Spotify; pode valer para outras.
- *Comparação de nomes por plataforma.* O Spotify usava uma comparação mais antiga e descartava duplas. Todas
  devem usar a mesma: `mesmo_artista`, `_nome_dentro`, `_mesma_grafia` e, para o intérprete conferido,
  `_palavra_do_nome`.
- *Grafia do intérprete.* "e" e "&", letra dobrada ("Beltrano"/"Beltranno") já são tolerados (`_mesma_grafia`).
  Letra a mais no meio do nome ("Tiago"/"Thiago") ainda não: é a principal causa de música não achada no
  Palco MP3.
- *Título escrito por quem publicou.* No Palco e no YouTube o título traz número de faixa, nome do artista, ano,
  "®", "- Compositor…". `_titulo_do_video` limpa parte disso.
- *Limite de nomes.* A Tidal procurava só os 8 primeiros nomes, começando pelos do próprio compositor.
- *Título comum demais.* A busca devolve 200 músicas e a do intérprete não está entre elas (Claro). Último recurso:
  a página do artista.
- *Endereço de álbum.* O álbum achado no site da Amazon nem sempre abre no aplicativo; aí o app usa a busca do
  próprio aplicativo. Os endereços de álbum do site deixam a tela do aplicativo vazia: não usar.

**Classificação e saída**
- *Relatório em planilha sem código.* Todas as obras ficavam com o código em branco e eram tratadas como uma só.
  Cada linha ganha um identificador local.
- *"A confirmar" sumia do PDF.* Hoje o PDF tem uma segunda parte, "A confirmar antes de usar", com o print e o
  motivo de cada uma (C1, C2...). A contagem da petição continua só com as firmes.
- *Nome junto ou separado.* "Di Tal" e "DiTal" são o mesmo nome artístico, e não grafia errada.
- *Texto do site no lugar do nome.* "Adicionar composição" (Palco) e "Créditos indisponíveis" (Amazon) não são autores.
- *Gabarito também erra.* Um link do gabarito apontava para outra música. Conferir o link antes de concluir que o
  app errou.
- *Plataforma muda.* O Palco MP3 passou a preencher compositores a partir de bases de direitos autorais; músicas
  que eram violação no gabarito de 2024 hoje trazem o compositor, às vezes no meio de dezenas de nomes de obras
  homônimas.

**Operação**
- *Pasta temporária.* O sistema limpou o ambiente virtual no meio de uma busca de mais de 4 horas. Tudo que dura
  fica em pasta fixa.
- *Substituição de trecho por marcador.* Um corte de arquivo entre dois marcadores pegou o marcador errado e
  duplicou 230 linhas da tela. Conferir o `git diff --stat` depois de edição grande.
- *Perfil em uso.* A janela de login da Claro aberta impede a coleta de abrir o mesmo perfil. Vira aviso
  (`PerfilEmUso`) e só a Claro para. Vale igual para a Deezer: só os prints dela param.
- *Coleta "que não rodou".* Várias vezes o zip recebido era o da rodada anterior: o app não tinha sido fechado e
  aberto depois da atualização, ou reaproveitou leituras. Conferir a hora dos arquivos dentro do zip.
- *Aviso de atualização.* O `versao-publicada.json` passa por um cache de cerca de 5 minutos no GitHub.

## 6. O que foi tentado e não funciona

- **Tidal sem login.** A página de busca abre em branco e a caixa de sugestões só devolve artista famoso. Buscar
  "título + intérprete" devolve resultados sem relação.
- **Tidal com login pelo navegador do app.** A Tidal detecta a automação e bloqueia a tela de login ("acesso
  temporariamente restrito"). Não insistir nem disfarçar o navegador: é contornar bloqueio.
- **Serviço público de links entre plataformas** (a partir do link da Deezer): foi desativado.
- **Caminho em aberto para a Tidal:** a API oficial (developer.tidal.com), buscando pelo ISRC que a Deezer já dá.
  Depende de o dono criar as chaves. Não foi testado.
- **Playwright no aplicativo da Amazon:** `connect_over_cdp` falha; por isso existe `creditos/cdp.py`.
- **Versão da Microsoft Store do Amazon Music:** não abre por linha de comando comum; abre com
  `Invoke-CommandInDesktopPackage` e a porta de depuração.
- **Amazon Music no Mac:** não instala. O aplicativo só é testado no Windows; no Mac o que se faz é ensaiar numa
  cópia falsa das telas (ver seção 8).

## 7. Pendências

Decisões que são do dono:
- Chave da API da Tidal, ou aceitar a Tidal limitada.
- Nome artístico com sobrenome a mais conta como o compositor?
- No Palco MP3, compositor no meio de dezenas de nomes de obras homônimas vale como "tem crédito"?
- Duas gravações da mesma música pelo mesmo intérprete: a petição conta por música ou por link?
- Na Amazon pelo aplicativo, serve o print do menu tirado na tela de busca (quando o álbum não abre)?
- No Palco MP3, manter o segundo print (página de créditos) quando a linha vem cortada? Ele mostra também os
  autores que o próprio site achou nas bases de direitos autorais.

Trabalho proposto e não feito:
- Apple Music na busca de intérpretes: detectar o bloqueio (recusas seguidas), pausar, desistir depois de algumas
  pausas, e **avisar na tela** quantas obras ficaram sem a consulta. Hoje a aba termina como se tudo tivesse dado
  certo; num relatório de 640 obras, 483 buscas ficaram sem resposta.
- Palco MP3: limpar "®" e sobras do título (a letra de diferença no nome do intérprete conferido entrou na 0.5.38)
  ("Open Bar"). Refazer o teste com o gabarito de 2024: as páginas "fora do ar" devem ser lidas agora.
- Decidir se a forma enxuta da consulta (sem "feat" e sem o "e") vale também para as outras plataformas.
- Resumo no fim de cada coleta dizendo se foi completa.
- Material de apoio para o pessoal do escritório (um Claude Project): descreve a versão 0.4.2.
- Carimbo do tempo (RFC 3161) nos prints e uma tela "Conferir um print": discutidos, não aprovados.

Deezer com login (primeira versão, só no navegador dos prints):
- Sem a sessão, os prints da Deezer param com o aviso "Entrar na Deezer"; a leitura dos créditos segue sem login.
  Se nenhuma gravação da Deezer precisa de print, o navegador nem abre e o login não é conferido.
- Falta testar no Windows, com a conta do escritório: o login na janela do app (a Deezer pode recusar navegador
  automatizado, como a Tidal; se recusar, não insistir), o painel de créditos logado e o print de tela inteira.
- Decidir depois se a leitura dos créditos também passa a usar a conta.

Nunca testado em tamanho real:
- Coleta de prints com centenas de pares (o maior teste teve 14 obras).
- A tela do app com relatório grande (a busca de 640 obras rodou pelo motor, não pela tela).
- Várias plataformas na mesma coleta.
- Tela inteira em Tidal, Deezer, Amazon pelo site e Amazon pelo aplicativo (no Palco MP3 e na Claro já rodou).
- Instalação do zero por alguém do escritório.

## 8. Como testar uma plataforma

O método que funcionou, e que o dono espera:

1. Ele manda o relatório do compositor, a planilha do escritório preenchida (o gabarito, com uma coluna por
   plataforma: link onde há violação, "tem créditos" onde há) e um documento com os prints feitos à mão.
2. Olhar os prints para saber **o formato** que o escritório usa naquela plataforma. O print do app tem de ser o
   mesmo.
3. Montar duas planilhas de teste: o relatório só com as obras do teste (título, compositor, pseudônimo e
   coautores; nada de percentual, código ou editora) e a planilha de intérpretes daquelas obras. Incluir as obras
   com violação e algumas com crédito.
4. Rodar aqui primeiro. Só depois pedir o teste no Windows.
5. Comparar música por música e separar: bateu; não achou (por quê); divergiu porque a plataforma mudou; gabarito
   errado.

Sem o aplicativo ou a conta à mão, dá para ensaiar com uma cópia falsa das telas: um HTML que imita o site ou o
aplicativo, servido localmente, com um Chromium aberto com porta de depuração. Foi assim que o coletor do
aplicativo da Amazon foi desenvolvido. O ensaio prova o mecanismo, não a plataforma: sempre dizer isso ao relatar.

## 9. Como publicar

1. `creditos/versao.py`: subir o número.
2. Testes passando.
3. Commit com corpo explicativo, sem coautoria, e `git push`.
4. `python empacotar.py` gera `quem-canta-<versão>-windows.zip` e atualiza `versao-publicada.json`.
5. `gh release create v<versão> quem-canta-<versão>-windows.zip --title "Quem Canta <versão>" --notes "..."`.
6. Commit e push do `versao-publicada.json` ("Publica a versão <versão> como atualização").

A ordem importa: o json só pode apontar para um pacote que já está na release. Quem instala a atualização precisa
fechar a janela preta do programa e abrir o app de novo.

## 10. Rodar no computador de desenvolvimento

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
set QUEMCANTA_DB=%CD%\dados\teste.db
.venv\Scripts\python -m pytest -q
set QUEMCANTA_DEV=1
.venv\Scripts\python -m streamlit run creditos_app.py --server.port 8502
```

No Mac ou Linux, troque `.venv\Scripts\python` por `.venv/bin/python` e `set` por `export`.
