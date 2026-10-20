"""Réplica: busca alternativa na consulta web da Socinpro (ver `consulta_web`).

Na Socinpro, o "não sou um robô" aparece depois do Pesquisar: o app preenche o título e clica em Pesquisar; a pessoa
marca a verificação e clica em Validar; o app acha a obra pelo código "ECAD OBRA" e abre os detalhes.
"""

from .consulta_web import VISIVEL, ConsultaWeb, titulo_para_buscar  # noqa: F401 (titulo_para_buscar: usada pela tela e testes)

# Preenche só o título (os outros campos em branco, "Começando com...") e clica em Pesquisar
_PESQUISAR = """(titulo) => {
  """ + VISIVEL + """
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
  """ + VISIVEL + """
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

CONSULTA = ConsultaWeb(
    plataforma="Socinpro", site="https://associado.socinpro.org.br/portal-web/pages/public/consulta.xhtml",
    host="socinpro.org.br", porta=9341, pasta_do_perfil="perfil-socinpro-chrome", campo_do_codigo="ECAD OBRA",
    js_preencher=_PESQUISAR, js_estado=_ESTADO, js_abrir=_ABRIR_DETALHES,
    como_resolver="marque o **não sou um robô** e clique em **Validar**",
)
SITE, PLATAFORMA, PORTA, NOME = CONSULTA.site, CONSULTA.plataforma, CONSULTA.porta, CONSULTA.nome

perfil_do_chrome = CONSULTA.perfil_do_chrome
chrome_aberto = CONSULTA.chrome_aberto
fechar_chrome = CONSULTA.fechar_chrome
estado = CONSULTA.estado
pesquisa_desta_obra = CONSULTA.pesquisa_desta_obra
pesquisar = CONSULTA.pesquisar
esperar_e_abrir = CONSULTA.esperar_e_abrir
conferir_detalhes = CONSULTA.conferir_detalhes
fotografar = CONSULTA.fotografar
