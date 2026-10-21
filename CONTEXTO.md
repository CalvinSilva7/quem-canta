# Contexto do projeto

Estado em 16/10/2026, versão 0.5.52. Escrito para quem assume o projeto sem ter acompanhado a construção: o que
existe, o que foi validado e onde, o que deu errado e por quê, e o que está pendente.

Este arquivo é público. Não traz nome de cliente, de música nem de artista; os casos reais usados nos testes ficam
em anotações privadas do dono do projeto.

## 1. O que o app faz

Quatro abas, independentes:

1. **Buscar intérpretes.** Entra o relatório do ECAD (PDF, ou planilha com título e compositor). Sai uma planilha
   de duas colunas, obra e intérprete. Em preto, o intérprete de que o app tem certeza; em vermelho, o que ele só
   supõe. Obra sem intérprete achado sai com a segunda coluna em branco. Usa Deezer e depois Apple Music, sem
   navegador e sem print. A planilha vai para o compositor conferir.
2. **Coleta de prints.** Entram o relatório e a planilha de intérpretes **conferida pelo compositor** (obrigatória:
   sem ela o botão de coletar não aparece). Para cada par obra + intérprete, o app acha a gravação em cada
   plataforma marcada, lê o crédito de compositor, classifica e tira o print. Saem a planilha de obras (formato do
   escritório), a lista da petição (.docx) e as provas por plataforma, num zip. As provas vêm em PDF e também em
   Word (decisão do dono, 14/10/2026), com as mesmas páginas, para quem precisa editar. Desde 21/10/2026 (pedido do
   dono, para todas as plataformas) cada página tem **só o print**: sem número, música ou intérprete em cima. A
   ordem é a da lista da petição, que traz o número, a música, o intérprete e o link; a parte "A confirmar antes de
   usar" continua separada por uma página própria, e o motivo de cada uma fica na lista (C1, C2...) e na planilha.
3. **Réplica (ECAD)** e 4. **Réplica (ISWC)** (0.5.50). Depois que a outra parte responde à petição, o escritório
   manda a réplica com prints do Ecadnet e do ISWC Network das obras da petição. Entram o relatório analítico e as
   listas da petição (.docx); saem os prints e um PDF único por site. Ver a seção 4.1.

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
| `creditos/juntar.py` | Junta planilhas de obras de coletas feitas em separado, pela obra e pelo intérprete (nunca pela posição) |
| `creditos/captura.py`, `tela.py` | O print: faixa de carimbo, SHA-256, ficha `.json`; a foto do monitor inteiro |
| `creditos/navegador.py` | O Chromium do app (Playwright): ritmo, sinais de bloqueio, perfil com sessão guardada |
| `creditos/deezer.py`, `apple.py`, `ytmusic.py`, `spotify.py`, `tidal.py` (com `tidal_api.py`), `amazon.py`, `amazon_app.py`, `claro.py`, `palco.py`, `vagalume.py` | Um coletor por plataforma |
| `creditos/cdp.py` | Conversa direta com a porta de depuração do aplicativo da Amazon (o Playwright não conecta nele) |
| `creditos/ecad.py`, `ubc.py` | Leitura do relatório em PDF e em planilha |
| `creditos/filtro.py` | O filtro do que pode sair do computador |
| `creditos/andamento.py` | Barra de progresso e a tarefa em segundo plano (uma por vez) |
| `creditos/atualizacao.py`, `empacotar.py`, `versao-publicada.json` | A atualização dos apps instalados |
| `creditos/replica.py` | Réplica: a janela do navegador do app numa linha própria, o cruzamento lista da petição × relatório, o PDF dos prints |
| `creditos/ecadnet.py`, `iswc.py` | Réplica: endereço e conferência da página no Ecadnet; o Chrome do ISWC, o PDF da página e a junção dos PDFs |
| `cantor/`, `app.py` | Primeira versão do projeto (achar o cantor de uma planilha). Ainda é usada: `cantor.matching` |

Dados de quem usa: `Documentos\Quem Canta\casos\<compositor>\<plataforma>\` (leituras em `leituras\*.json`, prints
`.png` com `.html` e `.json` ao lado). As sessões da Claro e da Deezer ficam em `Documentos\Quem Canta\perfil-claro`
e `perfil-deezer`, fora dos casos; a do Ecadnet em `perfil-ecadnet` e o Chrome do ISWC em `perfil-iswc-chrome`.
Os prints da réplica ficam em `casos\<compositor>\ecadnet\` e `iswc\`, e os PDFs únicos na pasta do caso.
Variáveis: `QUEMCANTA_DADOS` (pasta de dados), `QUEMCANTA_DB` (banco do `cantor`, usado nos testes),
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
| Tidal | API oficial pelo ISRC da Deezer (0.5.51); sem as chaves, a busca do site (fraca) | Tocador com a aba "Créditos" (prévia de 30 s, sem login); clipes: lista para o print à mão (0.5.52) | Coleta real de um caso (Windows, 16/10/2026): 161 gravações, 154 álbuns, 10 de 10 prints, 16 min, sem bloqueio. Antes: 3 de 13 |
| Amazon (aplicativo) | Busca do site, depois o aplicativo de desktop | 2 por música: menu com "Créditos" e janela de créditos | Validado no Windows: 9 de 9 e 5 de 5. Na 0.5.51, 10 de 10 refeitas certas (ver seção 5: o menu do álbum) |
| Amazon (site) | As mesmas faixas do aplicativo, só as que vieram sem o titular nele | Menu da faixa, sem item de créditos | Funciona; a busca acha poucas faixas. Desde a 0.5.49 não roda sozinho (ver abaixo) |

**Amazon é uma escolha só** (decisão do dono em 15/10/2026, 0.5.49), como o escritório faz à mão: primeiro o
aplicativo de desktop em todas as músicas; depois, só para as que vieram sem o titular no aplicativo (sem créditos
ou crédito de outros), o print do menu da mesma faixa no site, que não tem tela de créditos. Saem duas pastas de
provas, "Amazon Music (aplicativo)" e "Amazon Music (site)". Sem o aplicativo (Mac, ou programa não instalado), o
site não fotografa nada: não dá para saber quais músicas estão sem o titular. `coletar_amazon_completa`.
| Claro Música | Lista de músicas da busca, logado; página do artista como último recurso | Álbum com o menu da faixa e o cartão "Informações e créditos" | Validado no Windows: 5 de 6 (a sexta é link errado no gabarito) |
| Palco MP3 | Caixa de busca do site | Página da música; 2º print (página de créditos) quando o site corta a linha | Validado no Windows com gabarito de 2026: 3 de 3 e 5 de 6. Com o gabarito de 2024 diverge, porque o site mudou |
| Vagalume | Site de letras | Fora da coleta de prints (decisão do dono) | Código mantido, não exposto na tela |

### 4.1 Réplica (0.5.50)

**Cruzamento.** A réplica só precisa das obras da petição. As listas da petição (.docx; pode ser
mais de uma, a mesma música entra uma vez) dão os títulos, e só a parte firme: "A confirmar antes de usar" fica de
fora. Dois formatos de lista (0.5.53): a que o app gera ("N-Música: TÍTULO") e a tabela feita à mão no Word
("Nº | obra, com o intérprete na linha de baixo da mesma célula | link"). Lista sem nenhuma obra reconhecível dá erro
na tela (antes ficava em silêncio, com 0 obras).

**Título escrito de outro jeito** (decisão do dono, 20/10/2026: "se está no relatório e na lista, entra"). O ECAD
cadastra com título alternativo entre parênteses, sem artigo, com "MTG" de montagem, com parêntese cortado ou com
letra trocada. `replica.cruzar` tenta, em ordem: título igual; uma das formas igual (`_formas_do_titulo`); uma
forma começando pela outra; formas quase iguais (`SEMELHANCA_MINIMA` 0,85). Vale a primeira regra que achar alguma
obra, e entram todas que ela achar. A tela lista os que entraram por aproximação, para conferir na hora do print.
Num caso real (82 títulos, relatório de 1.135 obras): antes 74 títulos, agora os 82 (8 por aproximação).
 O relatório dá o código da obra (endereço no Ecadnet) e o ISWC. O cruzamento é pelo título
(`_titulo_base`): título com mais de um cadastro no relatório (mesma música cadastrada por associações diferentes,
em geral marcada BL/DU) traz todos os cadastros. São páginas diferentes no Ecadnet (autores e percentuais mudam),
por isso entram todos; quem decide qual vai na réplica é o jurídico.

**Ecadnet** (aba "Réplica (ECAD)"). A pessoa abre a janela do navegador do app e faz o login nela (o Ecadnet
recusa navegador sem janela, "Access Denied"). O app abre a página de cada obra pelo código, sem busca. Botões
(decisão do dono, 16/10/2026): **Fotografar** não muda de obra (às vezes são dois ou três prints da mesma, rolando
a página); **Próxima obra →** passa para a seguinte e já abre a página dela. O print é sempre o do monitor inteiro,
com a data e a hora do relógio do Windows (obrigatório, decisão do dono): sem ele, o print é descartado e a tela
pede para tentar de novo. O print sai sem a faixa de carimbo (pedido do dono); os dados dela ficam no `.json`, e o
monitor é achado pela posição que a janela informa. "Juntar os prints em PDF": uma página por print (A4 deitado),
na ordem da fila, com os prints da última rodada de cada obra (até 3 minutos antes do mais recente; prints de
testes anteriores ficam de fora). O Ecadnet não mostra cadastros pendentes de identificação ou em conflito ("Não há
resultados nesta busca"): o app avisa na hora e deixa fotografar essa tela como registro. Testado no Windows
pelo dono, com login real em dois dias seguidos (a sessão fica guardada).

**Busca alternativa na Socinpro** (0.5.55; pedido do jurídico e decisão do dono, 20/10/2026). Quando a obra não
aparece no Ecadnet (raro), a Socinpro às vezes tem o cadastro, e o print dela vai como prova complementar. Não é para
coletar sempre: fica num expander da aba "Réplica (ECAD)", para a obra da vez. A consulta
(`associado.socinpro.org.br/portal-web/pages/public/consulta.xhtml`) é pública, mas pede o reCAPTCHA a cada
pesquisa e o resultado não tem endereço próprio. Por isso (`creditos/socinpro.py`), como no ISWC, o app usa o Google
Chrome da pessoa (perfil `perfil-socinpro-chrome`, porta 9341): preenche o título (sem o que vem entre parênteses,
busca "Começando com"), clica em Pesquisar, espera a pessoa resolver o "não sou um robô" e clicar em Validar (até 3
minutos), acha a linha pelo código ECAD OBRA (o mesmo do relatório) e abre os detalhes. "Fotografar na Socinpro"
confere que os detalhes abertos são do mesmo código, traz a janela para a frente e tira o print do monitor inteiro
(obrigatório, como no Ecadnet). O print é guardado na pasta do Ecadnet, com a etapa dele e a plataforma "Socinpro"
na ficha. Desde a 0.5.59 (pedido do dono) os prints da Socinpro têm lista própria (↑ ↓ ✕) e PDF próprio,
"Socinpro - <titular>.pdf": não se misturam com os do Ecadnet, que continuam em "Ecadnet - <titular>.pdf". Termos de uso da Socinpro: proíbem copiar, baixar e
distribuir sem consentimento; o jurídico já fazia esses prints à mão para o processo, e o aval sobre os termos é do
jurídico. Testado: o preenchimento e a verificação no site real (uma pesquisa); a tabela, a abertura dos detalhes e
o print numa página de imitação com a mesma estrutura, no Chrome real. Não testado: o caminho inteiro com o
reCAPTCHA resolvido (a tabela de resultados de verdade). 0.5.56: no primeiro teste do dono, com o reCAPTCHA resolvido,
o app achou a obra pelo código ECAD e abriu os detalhes sozinho; mas "Abrir" e "Fotografar" ficavam apagados (a tela
os desenhava antes de o Chrome abrir). Agora ficam sempre clicáveis e avisam se o Chrome não estiver aberto.
0.5.57 (pedido do dono: "muito botão, a pessoa se perde"): só dois botões. **Buscar na Socinpro** pesquisa, espera o
"não sou um robô" e abre a obra; se a pesquisa já está na tela (verificação ou resultados), não pesquisa de novo, só
espera e abre. **Fotografar na Socinpro** tira o print. Fechar o Chrome é no X da própria janela.
0.5.58: na segunda obra, os resultados da primeira continuavam na tela e o Buscar não pesquisava de novo (procurava o
código novo nos resultados velhos: "a obra não está nos resultados" para qualquer música). Agora só aproveita a
pesquisa da tela se o título no campo de busca for o desta obra (`pesquisa_desta_obra`). Também visto no teste do
dono: o reCAPTCHA travou uma vez (o Verificar não ia); recarregar a página resolveu. Causa não confirmada.
UBC: consulta web em ubc.org.br/ferramentas/consulta-web (título, titular, intérprete; reCAPTCHA na própria página,
antes do Pesquisar). Feita na 0.5.60 (pedido do dono), no mesmo modelo.

**Busca alternativa na UBC** (0.5.60, pedido do dono). As duas associações agora usam o mesmo motor
(`creditos/consulta_web.py`, classe `ConsultaWeb`); `socinpro.py` e `ubc_web.py` só têm o endereço, a porta do Chrome
(9341 e 9342), o rótulo do código ECAD nos detalhes e os três scripts da página (preencher, ler a tela, abrir a obra).
Na UBC o "não sou um robô" vem antes do Pesquisar: o app preenche o título (página em React: `value` nativo + evento
`input`, senão o site não vê o texto), espera o reCAPTCHA preencher `g-recaptcha-response` e clica em Pesquisar uma
vez só; acha a linha pela coluna ECAD (`cdObraEcad`) e clica no botão "Detalhes" do fim da linha; confere o "ECAD" da
janela "Detalhes da Obra". A estrutura da tabela e dos detalhes saiu do código da própria página (o resultado de
verdade só aparece com a verificação resolvida). Testado: no site real, o preenchimento e a verificação pendente;
numa imitação, a tabela, os detalhes e a sequência verificação → Pesquisar → resultados → detalhes. Não testado: o
resultado de verdade. Lista e PDF próprios: "UBC - <titular>.pdf". Enquanto a verificação está na tela, o app olha a
página a cada 2 s (antes 1 s, nas duas associações).

**Ordem e exclusão dos prints** (pedido do dono, 20/10/2026, Ecadnet e ISWC). A lista "Prints … já tirados" mostra
exatamente o que vai no PDF único, na ordem dele, com ↑ ↓ e ✕ em cada linha. A ordem escolhida fica em
`ordem.json` na pasta do site (`replica.em_ordem`/`mover`); print novo, ainda não mexido, entra depois, na ordem da
fila. ✕ (`remover_print`) não apaga: move a ficha e os arquivos para a subpasta `removidos` (são provas; engano se
desfaz trazendo de volta). Continuam valendo as regras de antes: no ISWC, o PDF mais recente de cada obra; no
Ecadnet, a última rodada de cada obra e só obras da fila.

**Zoom do navegador** (0.5.53). O jurídico diminui o zoom (Ctrl -) para caber mais na tela, e o print falhava
("janela coberta" / "não está à vista"). Duas causas: o zoom muda `innerWidth` e `devicePixelRatio`, mas não
`screenX` nem `outerWidth` (`captura.posicao_da_pagina` agora acha o nível de zoom e a escala do Windows); e, abaixo
de 100%, o print do Playwright sai maior que a janela, com a página num canto e o resto branco (sem carimbo, o print
vem do próprio Chrome por CDP, `_print_da_pagina`, certo em qualquer zoom). Testado no Windows (escala 100%) com
zoom de 50%, 67%, 80%, 100%, 125% e 150%, janela maximizada e solta. Falta testar com o Windows em 125% ou 150%.

0.5.61: no computador de um colega (monitor diferente), o print falhava com zoom de 67% ("algo por cima"); até 75%
funcionava. Aqui, com monitores iguais em escala 100%, 67% sempre funcionou: a conta que transforma a posição que o
navegador informa em pixels do monitor não vale com qualquer monitor (escala do Windows, monitores de escalas
diferentes). Agora (`captura.janela_da_pagina` + `tela.retangulo_da_janela`) o app pergunta ao próprio Windows onde a
janela está, em pixels de verdade; para não confundir com outra janela aberta na mesma página, a página ganha por um
instante um título único, que volta ao normal antes do print. A conta antiga fica de reserva, e por último o app
procura a página até 12 pixels em volta. Testado aqui forçando a conta a errar 150 px: o app acha a página pela janela
com 50% e 67%, maximizada e solta, nos dois monitores. Quando o print falha, o print da página e as medidas ficam em
`ecadnet/diagnostico/` (os últimos 10). Falta o teste no computador do colega.

**ISWC Network** (aba "Réplica (ISWC)"). O "não sou um robô" recusa o navegador do app (Playwright), e os termos
só permitem consulta manual. Por isso o app abre o **Google Chrome** da pessoa, com um perfil só dele
(`perfil-iswc-chrome`) e uma porta local de depuração (9339), maximizado no monitor em que o app não está. A
pessoa resolve o captcha; o app só abre o link de resultado (`/search?iswc=`), na mesma aba a cada obra, e salva a
página em PDF quando ela clica em **Salvar PDF**. O PDF tem de sair igual ao Ctrl+P feito à mão (decisão do dono):
A3, com data, título, endereço e "1/1" do próprio Chrome (`Page.printToPDF`). O site monta o resultado em linhas
("ISWC:", "Original Title:", nomes completos) só em janela de até 1024 px; maximizada, vira tabela e corta os
nomes. Na hora de salvar, o app faz a página se comportar como numa janela de 1000 px, também durante a impressão
(as regras de CSS por largura e o `matchMedia`), e desfaz depois. "Juntar os PDFs": uma página por obra, a mais
recente. A junção usa o `pypdfium2`, que vem com o pdfplumber: a atualização do app não instala biblioteca nova.
Testado no Windows com o captcha resolvido à mão e 5 obras reais; posições do texto iguais às do PDF feito à mão
(diferença menor que 1 pt).

## 5. Erros que custaram tempo, e a causa de cada um

Vale ler antes de mexer na plataforma correspondente.

**Print e captura**
- *Print tirado sem a tela certa aberta.* Na Deezer o menu abre com animação; o clique no item de créditos saía
  cedo demais, o painel não abria, a página rolava e o print era tirado assim mesmo. Regra: só fotografar depois
  de conferir que o painel ou a janela está de fato por cima (`document.elementFromPoint`), e tentar de novo.
- *Menus escondidos na página.* O aplicativo da Amazon guarda na página uma lista de playlists invisível, com
  itens parecidos com os do menu da faixa. O app leu essa lista e concluiu "sem créditos". Regra: ler só o que
  está à vista, e reconhecer o menu por itens que só ele tem.
- *O menu do álbum no lugar do menu da música* (Amazon, aplicativo; corrigido na 0.5.51). Quando o álbum não abria,
  a busca do aplicativo pegava o resultado da seção "Álbuns" com o mesmo nome. O menu do álbum não tem "Créditos" e
  virou "sem créditos" falso em 10 músicas de um caso; refeitas, 9 traziam o titular no crédito. Regras: menu com
  "Compartilhar álbum" nunca vale; na busca, só a seção "Músicas"; o álbum que não abre de primeira abre depois de
  voltar ao início e esperar (segunda tentativa). Leitura guardada com menu de álbum é refeita.
- *Aba de créditos do Tidal que demora.* O tocador às vezes leva até 12 s para mostrar os créditos, às vezes fica
  vazio até trocar de aba e voltar. Com 2 s de espera, 4 de 9 prints falhavam. Agora: espera até 12 s, troca de aba
  e volta, e recarrega a faixa uma vez; o print só sai com a aba carregada (ela sempre mostra "Lançamento" e
  "Gravadora") e com os mesmos autores da página do álbum. Na 0.5.51, a música sem créditos era fotografada antes de
  a aba abrir (nenhum autor esperado = nenhum autor na tela vazia): o print saía com a capa. Print guardado com a aba
  vazia é refeito.
- *Lista de clipes do Tidal incompleta* (0.5.52). A API entrega os clipes de um artista em páginas de 20; o endereço
  da página seguinte vem codificado ("page%5Bcursor%5D") e era reenviado codificado de novo, e a API devolvia sempre
  a primeira página. Cada artista mostrava só 20 clipes, repetidos. Corrigido (o cache antigo, `_clipes.json`, é
  ignorado; o novo é `_clipes_v2.json`).
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
- *N/A em música que a plataforma achou (21/10/2026).* Na planilha de um compositor, músicas apareciam com "N/A" no
  Spotify mesmo tendo sido achadas lá. Cada plataforma escreve o intérprete de um jeito ("Fulano e Beltrano" ×
  "Fulano & Beltrano", "Cantor Fulano" × "Fulano Cantor", três artistas em ordens diferentes), e a aba Obras fazia
  uma linha por grafia: a plataforma que achou ficava na linha vizinha e a outra linha mostrava N/A. Agora a linha
  é por artistas (`saida.chave_do_interprete`: separa por e/&/vírgula/feat, ignora a ordem e "cantor"/"oficial");
  aparece a grafia mais usada. Na planilha real: 143 linhas viraram 119.
- *Música da Deezer na lista da petição sem print (21/10/2026).* Algumas faixas não têm "Consulte créditos musicais"
  no menu "…" (o menu vai de "Ouvir em seguida" a "Reportar um problema"). O app esperava o item, dava erro, descartava
  o print do menu e a música (corretamente sem crédito) ia para a lista sem prova; o escritório tirou o print à mão.
  Agora `abrir_creditos_da_deezer` reconhece o menu aberto sem o item (`MenuSemCreditos`, com o menu ainda aberto) e a
  prova é esse print, um só (etapa `menu_sem_creditos`, também reaproveitado nas coletas seguintes). Testado com
  navegador falso; falta conferir no site numa coleta real.
- *Intérprete que nem foi aberto no Spotify (21/10/2026).* Numa obra com 12 intérpretes conferidos, o último saía
  "N/A" no Spotify, embora a música estivesse lá (e no Deezer, na Claro e na Amazon). O Spotify abria no máximo 8
  faixas por OBRA: as versões ao vivo de dois intérpretes gastaram as 8 vagas e os outros nem foram abertos, sem
  aviso. Agora são 4 por intérprete (os mesmos artistas escritos de jeitos diferentes contam juntos) e 40 por obra,
  e o que o limite cortar vira aviso no Resumo. YouTube, Amazon, Claro e Palco já limitavam por intérprete; nas três
  últimas o limite subiu de 2 para 3 faixas por intérprete (a 3ª versão, ao vivo ou acústica, ficava de fora sem
  aviso) e o corte também vira aviso.
- *Coluna colada de outra planilha (21/10/2026).* Uma planilha veio com os links do Palco MP3 (e do Deezer) nas obras
  erradas e com o fim da coluna vazio. Não era o app: a planilha tinha só YouTube e Spotify coletados (abas Gravações
  e Resumo) e foi salva de novo à mão depois; as colunas Deezer e Palco foram coladas de outra coleta. Cada coleta
  monta as linhas com o que achou (uma obra com 5 intérpretes numa e 1 na outra), então colar pela posição desloca
  tudo. Solução: "Juntar planilhas de coletas feitas em separado" (`creditos/juntar.py`), que remonta a aba Obras a
  partir da aba Gravações de cada planilha, pela obra e pelo intérprete. Para conferir uma planilha suspeita: as
  plataformas da aba Gravações e o rodapé da aba Obras dizem o que o app de fato coletou.

## 6. O que foi tentado e não funciona

- **Tidal sem login.** A página de busca abre em branco e a caixa de sugestões só devolve artista famoso. Buscar
  "título + intérprete" devolve resultados sem relação.
- **Tidal com login pelo navegador do app.** A Tidal detecta a automação e bloqueia a tela de login ("acesso
  temporariamente restrito"). Não insistir nem disfarçar o navegador: é contornar bloqueio.
- **Serviço público de links entre plataformas** (a partir do link da Deezer): foi desativado.
- **Tidal pela API oficial** (0.5.51, o caminho que resolveu): developer.tidal.com, chave de app (Client ID e
  Secret, sem login de usuário), consulta `/v2/tracks?filter[isrc]=` com `include=albums`, um ISRC por vez (com
  vários, ela devolve só uma faixa por ISRC). O ISRC vem das gravações que a Deezer leu na rodada e, para as obras
  que faltarem, da busca da Deezer por título + intérprete. As chaves ficam em `chaves-tidal.txt` na pasta de dados
  (ou nas variáveis TIDAL_CLIENT_ID e TIDAL_CLIENT_SECRET), nunca no repositório. Obra sem gravação na Deezer não é
  procurada no Tidal (o resumo lista quais).
- **Clipes do Tidal** (0.5.52). Decisão do dono: quando a música tem clipe, a prova é o clipe (o tocador do vídeo
  com a aba "Créditos"). O vídeo do Tidal tem proteção contra cópia (Widevine) e só toca no Google Chrome de verdade:
  no navegador do app (Chromium) e no Chrome aberto em modo automatizado fica preto, com o aviso "problema de rede
  (NPBIO)". Num Chrome de verdade com perfil próprio, tocou depois de o Chrome baixar o componente. Decisão do dono:
  o app não fotografa os clipes; acha-os pela API (os clipes de cada artista das faixas achadas e, para obra sem
  gravação na Deezer, do artista achado pelo intérprete conferido) e lista no fim das provas do Tidal, com o link,
  em duas partes: os do intérprete da planilha conferida e os de outro artista (conferir se é a mesma música). Clipe
  medley vem marcado "a confirmar". O clipe tem ISRC próprio (não o do áudio) e a busca por texto da API recusou
  todos os formatos tentados. Num caso real: 42 clipes de obras do relatório (28 do intérprete conferido).
- **Playwright no aplicativo da Amazon:** `connect_over_cdp` falha; por isso existe `creditos/cdp.py`.
- **Versão da Microsoft Store do Amazon Music:** não abre por linha de comando comum; abre com
  `Invoke-CommandInDesktopPackage` e a porta de depuração.
- **Amazon Music no Mac:** não instala. O aplicativo só é testado no Windows; no Mac o que se faz é ensaiar numa
  cópia falsa das telas (ver seção 8).

## 7. Pendências

Decisões que são do dono:
- Tidal: cada computador do escritório precisa do arquivo `chaves-tidal.txt` na pasta de dados.
- Tidal, clipes: se o escritório quiser o print automático, o caminho é o Chrome de verdade com perfil próprio (como o
  do ISWC), com o componente de vídeo baixado na primeira vez. A lista de clipes percorre todos os clipes de cada
  artista (num caso, 2.742 de 102 artistas, 5 min): dá para limitar aos intérpretes conferidos.
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

Réplica (0.5.50):
- Qual cadastro vai na réplica quando a mesma obra tem vários no relatório (hoje entram todos): decisão do jurídico.
- O cruzamento é pelo título; se a lista da petição passar a trazer o código da obra, dá para cruzar por ele.
- Gerar o documento da réplica em si (tabela com o trecho do relatório destacado): discutido, não feito.
- Nunca rodou uma réplica inteira (dezenas de obras) de ponta a ponta.

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
