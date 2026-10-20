"""Réplica: busca alternativa na Consulta Web pública da UBC (ver `consulta_web`). (`ubc.py` é outra coisa: lê a
planilha de obras que a UBC manda.)

Na UBC, o "não sou um robô" fica na própria página, antes do Pesquisar: o app preenche o título; a pessoa marca a
verificação; o app percebe a verificação resolvida (o reCAPTCHA preenche `g-recaptcha-response`), clica em Pesquisar,
acha a obra pelo código ECAD na tabela de resultados e abre os "Detalhes da Obra".

A página é feita em React: o título entra pelo `value` nativo do campo mais o evento `input`, senão o site não vê o
que foi escrito (testado no site real: com isso ele pede a verificação em vez de reclamar de campo vazio).
"""

from .consulta_web import VISIVEL, ConsultaWeb

_ROTULO_DO_TITULO = "Título ou título alternativo"

# Preenche só o título (titular e intérprete em branco). Não clica em Pesquisar: a verificação vem antes
_PREENCHER = """(titulo) => {
  """ + VISIVEL + """
  const definir = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  const rotulos = [...document.querySelectorAll('label')].filter(l => visivel(l) && l.htmlFor && document.getElementById(l.htmlFor));
  const rotulo = rotulos.find(l => l.innerText.trim() === '""" + _ROTULO_DO_TITULO + """');
  if (!rotulo) return 'não achei o campo do título';
  for (const l of rotulos) {
    const campo = document.getElementById(l.htmlFor);
    if (campo.type !== 'text') continue;
    definir.call(campo, l === rotulo ? titulo : '');
    campo.dispatchEvent(new Event('input', {bubbles: true}));
  }
  return '';
}"""

# O que está na tela: verificação pendente? pronta para pesquisar? resultados (código ECAD de cada linha)? detalhes abertos?
_ESTADO = """() => {
  """ + VISIVEL + """
  const resposta = document.getElementById('g-recaptcha-response');
  const resolvida = !!(resposta && resposta.value);
  const folhas = [...document.querySelectorAll('body *')].filter(e => e.children.length === 0 && visivel(e));
  const titulo = folhas.find(e => (e.innerText || '').trim() === 'Detalhes da Obra');
  const caixa = titulo && titulo.parentElement;
  const campos = {};
  if (caixa) {
    for (const e of caixa.querySelectorAll('*')) {
      const nome = e.children.length === 0 ? (e.innerText || '').trim() : '';
      if (['Título da Obra', 'ECAD', 'ISWC'].includes(nome) && e.nextElementSibling) campos[nome] = (e.nextElementSibling.innerText || '').trim();
    }
  }
  const linhas = [];
  for (const tabela of document.querySelectorAll('table')) {
    const cabecalhos = [...tabela.querySelectorAll('thead th')].map(th => th.innerText.trim().toUpperCase());
    const coluna = cabecalhos.findIndex(c => c.includes('ECAD'));
    if (coluna < 0 || !cabecalhos.some(c => c.includes('ISWC')) || !visivel(tabela)) continue;
    tabela.querySelectorAll('tbody tr').forEach((tr, i) => {
      const celulas = [...tr.querySelectorAll('td')];
      if (celulas.length > coluna) linhas.push({i, codigo: celulas[coluna].innerText.trim(), texto: tr.innerText.replace(/\\s+/g, ' ').trim()});
    });
  }
  const rotulo = [...document.querySelectorAll('label')].find(l => l.innerText.trim() === '""" + _ROTULO_DO_TITULO + """');
  const campo = rotulo && document.getElementById(rotulo.htmlFor);
  const carregando = folhas.some(e => /^Carregando/.test((e.innerText || '').trim()));
  return {verificacao: !resolvida && !linhas.length && !caixa, pode_pesquisar: resolvida && !linhas.length && !caixa && !carregando,
          carregando, detalhes: !!caixa, campos, titulo_pesquisado: campo ? campo.value.trim() : '',
          texto_dos_detalhes: caixa ? caixa.innerText : '', linhas, url: location.href};
}"""

_CLICAR_PESQUISAR = """() => {
  """ + VISIVEL + """
  const botao = [...document.querySelectorAll('button')].find(b => visivel(b) && b.innerText.trim() === 'Pesquisar');
  if (!botao) return 'não achei o botão Pesquisar da UBC';
  botao.click();
  return '';
}"""

# Abre os detalhes da linha cujo código ECAD é este (o botão "Detalhes" do fim da linha)
_ABRIR_DETALHES = """(codigo) => {
  for (const tabela of document.querySelectorAll('table')) {
    const cabecalhos = [...tabela.querySelectorAll('thead th')].map(th => th.innerText.trim().toUpperCase());
    const coluna = cabecalhos.findIndex(c => c.includes('ECAD'));
    if (coluna < 0 || !cabecalhos.some(c => c.includes('ISWC'))) continue;
    for (const tr of tabela.querySelectorAll('tbody tr')) {
      const celulas = [...tr.querySelectorAll('td')];
      if (celulas.length > coluna && celulas[coluna].innerText.trim() === codigo) {
        const botoes = tr.querySelectorAll('button, a');
        if (!botoes.length) return 'a linha da obra não tem o botão de detalhes';
        botoes[botoes.length - 1].click();
        return '';
      }
    }
  }
  return 'a obra não está nos resultados';
}"""

CONSULTA = ConsultaWeb(
    plataforma="UBC", site="https://www.ubc.org.br/ferramentas/consulta-web", host="ubc.org.br", porta=9342,
    pasta_do_perfil="perfil-ubc-chrome", campo_do_codigo="ECAD",
    js_preencher=_PREENCHER, js_estado=_ESTADO, js_abrir=_ABRIR_DETALHES, js_clicar_pesquisar=_CLICAR_PESQUISAR,
    como_resolver="marque o **não sou um robô** (o app clica em Pesquisar sozinho)",
)
PLATAFORMA = CONSULTA.plataforma

perfil_do_chrome = CONSULTA.perfil_do_chrome
chrome_aberto = CONSULTA.chrome_aberto
fechar_chrome = CONSULTA.fechar_chrome
estado = CONSULTA.estado
pesquisa_desta_obra = CONSULTA.pesquisa_desta_obra
pesquisar = CONSULTA.pesquisar
esperar_e_abrir = CONSULTA.esperar_e_abrir
conferir_detalhes = CONSULTA.conferir_detalhes
fotografar = CONSULTA.fotografar
