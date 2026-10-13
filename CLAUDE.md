# Quem Canta: instruções para o agente

App Streamlit, local, para um escritório de advocacia. A partir do relatório analítico do ECAD de um compositor, ele
(1) descobre quem gravou cada obra e (2) confere, em cada plataforma de música, se o compositor aparece nos créditos,
tira o print de prova e gera a planilha, a lista da petição e o PDF de provas.

Leia `CONTEXTO.md` antes de mexer em qualquer plataforma: ele traz o estado de cada uma, o que já foi tentado e não
funcionou, e as decisões do dono do projeto. Responda ao dono em português do Brasil, em linguagem simples (ele não é
programador).

## Regras que não mudam

- **Sem IA no app.** Nenhuma etapa usa LLM. Tudo é determinístico.
- **O que pode sair do computador:** título da obra, ISWC, pseudônimos e nomes de autores (são públicos). **Nunca
  sai:** percentuais, CAE/IPI, códigos ECAD/UBC de titulares, editoras, datas de contrato ou de cadastro, CPF, RG,
  endereço, a aba de duplicidades. O filtro de saída (`creditos/filtro.py`) bloqueia isso em toda requisição; não o
  contorne.
- **Nunca acessar ECADNET, UBC ou portais de associações de forma automatizada.** Esses dados entram só por
  importação manual do relatório.
- **Nunca afirmar sem ter visto.** "Tem créditos" e "sem créditos" só com a tela lida. Erro técnico, bloqueio ou
  dúvida viram "verificar manualmente", nunca "sem créditos".
- **Robôs:** nunca contornar captcha, bloqueio ou detecção de automação. No máximo uma página a cada 5 a 10
  segundos. Ao ser bloqueado, parar a fila daquela plataforma e guardar o que foi lido. Página traduzida pelo
  navegador é recusada.
- **Login:** a regra é não logar em plataforma. Há duas exceções decididas pelo dono, e em nenhuma o app digita ou
  guarda senha: o aplicativo de desktop da Amazon Music (usa a conta já logada nele) e a Claro Música (a pessoa loga
  uma vez numa janela do navegador do app; a sessão fica num perfil local).
- **Chaves de API** sempre em variável de ambiente ou arquivo local fora do repositório. O repositório é público.
- **Nada de nomes de músicas, artistas ou clientes no código, nos testes, nos commits ou nos documentos do
  repositório.** Os testes usam dados inventados ("Zeca Lima", "Banda do Baile", "COISA FEITA").

## Como trabalhar aqui

- Testes: `python -m pytest -q` com `QUEMCANTA_DB` apontando para um arquivo em `dados/` (ver `CONTEXTO.md`). Têm
  de passar antes de qualquer commit.
- Commits: só com a autoria do dono do repositório, **sem linha `Co-Authored-By`**. Todo commit tem corpo
  explicando, em português simples, o que mudou e por quê (o corpo vai para um canal do Slack).
- Publicar uma versão: a ordem está em `CONTEXTO.md` ("Como publicar"). Não pule passos: o aviso de atualização dos
  apps instalados depende dela.
- Coleta longa: rodar em segundo plano e esperar o aviso de término. Não ficar consultando em laço.
- Nunca guardar clone, ambiente virtual ou dados de coleta em pasta temporária do sistema: uma coleta de mais de 4
  horas já foi perdida assim.
- Mudança de comportamento que o dono não pediu: proponha e espere a resposta. Ele já reverteu mudanças feitas sem
  pedir.
- Antes de dizer que algo funciona, diga onde foi testado (cópia falsa da tela, Mac, Windows, quantas músicas). O
  que só passou em teste automático ou em ensaio não está validado.
