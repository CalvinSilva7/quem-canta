"""Print de tela inteira: a foto do monitor onde está a janela do navegador do app.

A captura de página (captura.py) não mostra a barra de endereço nem o relógio do sistema. Aqui o app fotografa
o monitor inteiro, como num print feito à mão. Para isso a janela precisa estar visível: a pessoa a arrasta
para um monitor que vai ficar livre e trabalha em outro.

Nada é desenhado nem montado. O app só escolhe qual monitor fotografar e confere se a página estava mesmo à
vista: procura, em cada monitor, a linha vermelha da faixa de carimbo que a captura põe no topo da página e
compara o que está abaixo dela com a captura de página feita no mesmo instante. Janela coberta, minimizada ou
cortada pela borda do monitor não passa: o print de tela inteira não é salvo e o motivo fica registrado.
"""

import io
import sys

VERMELHO = (176, 0, 0)  # a borda inferior da faixa de carimbo (captura._CARIMBAR)
FOLGA_DE_COR = 60       # monitores com perfil de cor mudam um pouco o vermelho
LARGURA_MINIMA = 300    # em pixels: menos que isso não é a faixa, é outro elemento vermelho da tela
DIFERENCA_MAXIMA = 22   # diferença média de brilho (0 a 255) tolerada entre a página e o que o monitor mostra
A_VISTA_MINIMO = 0.9    # fração da página que precisa caber no monitor


class SemComponente(Exception):
    """As bibliotecas de captura de tela não estão instaladas (app atualizado sem rodar o Instalar.cmd)."""


def achar_faixa(matriz):
    """Onde está a linha vermelha da faixa de carimbo: (linha, coluna inicial, coluna final), ou None.

    `matriz` é a imagem em RGB (altura x largura x 3). Vale a sequência horizontal mais longa de vermelho.
    """
    import numpy as np

    r, g, b = (matriz[:, :, i].astype(np.int16) for i in range(3))
    vermelho = (abs(r - VERMELHO[0]) < FOLGA_DE_COR) & (g < FOLGA_DE_COR) & (b < FOLGA_DE_COR)
    melhor = None
    for linha in np.flatnonzero(vermelho.sum(axis=1) >= LARGURA_MINIMA):
        marcado = np.concatenate(([0], vermelho[linha].view(np.int8), [0]))
        mudancas = np.flatnonzero(np.diff(marcado))
        inicios, fins = mudancas[::2], mudancas[1::2]
        maior = int(np.argmax(fins - inicios))
        if fins[maior] - inicios[maior] >= LARGURA_MINIMA and (melhor is None or fins[maior] - inicios[maior] > melhor[2] - melhor[1]):
            melhor = (int(linha), int(inicios[maior]), int(fins[maior]))
    return melhor


def conferir(monitor, pagina) -> tuple[bool, str]:
    """A página (captura feita pelo navegador) está à vista, inteira, neste monitor? Devolve (sim ou não, motivo).

    As duas imagens são RGB. A faixa de carimbo dá a posição e a escala; o resto é comparado em miniatura,
    para tolerar o que se mexe na tela (barra de reprodução, cursor).
    """
    import numpy as np
    from PIL import Image

    na_tela, na_pagina = achar_faixa(monitor), achar_faixa(pagina)
    if na_pagina is None:
        return False, "a captura de página veio sem a faixa de carimbo"
    if na_tela is None:
        return False, "a janela do navegador não está à vista neste monitor"
    escala = (na_tela[2] - na_tela[1]) / (na_pagina[2] - na_pagina[1])
    alto, largo = pagina.shape[0] * escala, pagina.shape[1] * escala
    topo, esquerda = na_tela[0] - na_pagina[0] * escala, na_tela[1] - na_pagina[1] * escala
    y0, x0 = max(0, round(topo)), max(0, round(esquerda))
    y1, x1 = min(monitor.shape[0], round(topo + alto)), min(monitor.shape[1], round(esquerda + largo))
    if y1 <= y0 or x1 <= x0 or (y1 - y0) * (x1 - x0) < A_VISTA_MINIMO * alto * largo:
        return False, "a janela do navegador não cabe inteira no monitor"
    # O mesmo recorte nas duas imagens, reduzido a uma miniatura em tons de cinza.
    py0, px0 = round((y0 - topo) / escala), round((x0 - esquerda) / escala)
    py1, px1 = round((y1 - topo) / escala), round((x1 - esquerda) / escala)
    miniaturas = [
        np.asarray(Image.fromarray(np.ascontiguousarray(recorte)).convert("L").resize((96, 60), Image.BOX), dtype=np.int16)
        for recorte in (monitor[y0:y1, x0:x1], pagina[py0:py1, px0:px1])
    ]
    diferenca = float(abs(miniaturas[0] - miniaturas[1]).mean())
    if diferenca > DIFERENCA_MAXIMA:
        if na_tela[1] == 0 or na_tela[2] == monitor.shape[1]:
            # A faixa encosta na borda: o mais provável é a janela estar com um pedaço para fora do monitor.
            return False, "a janela do navegador não cabe inteira no monitor"
        return False, "a janela do navegador está coberta por outra janela"
    return True, ""


def _monitores():
    """Cada monitor ligado: (número, imagem RGB como matriz, PNG da imagem)."""
    try:
        import mss
        import mss.tools
        import numpy as np
    except ImportError as e:
        raise SemComponente('falta um componente de captura de tela: dê dois cliques em "Instalar.cmd" e abra o app de novo') from e
    with mss.mss() as telas:
        for numero, monitor in enumerate(telas.monitors[1:], start=1):
            foto = telas.grab(monitor)
            matriz = np.frombuffer(foto.rgb, dtype=np.uint8).reshape(foto.height, foto.width, 3)
            yield numero, matriz, mss.tools.to_png(foto.rgb, foto.size)


def fotografar(imagem_da_pagina: bytes, monitores=_monitores) -> tuple[bytes | None, dict]:
    """A foto do monitor em que a página está à vista. Devolve (PNG, {"monitor": n}) ou (None, {"erro": motivo}).

    `imagem_da_pagina` é a captura que o navegador acabou de fazer, com a faixa de carimbo ainda na tela.
    """
    import numpy as np
    from PIL import Image

    pagina = np.asarray(Image.open(io.BytesIO(imagem_da_pagina)).convert("RGB"))
    motivos = []
    for numero, matriz, png in monitores():
        certo, motivo = conferir(matriz, pagina)
        if certo:
            return png, {"monitor": numero}
        motivos.append(motivo)
    # Entre os motivos, o mais informativo é o do monitor em que a faixa chegou a ser achada.
    motivos.sort(key=lambda m: "não está à vista" in m)
    motivo = motivos[0] if motivos else "nenhum monitor encontrado"
    if "não está à vista" in motivo and sys.platform == "darwin":
        # No Mac, sem a permissão de Gravação de Tela, a foto vem só com o papel de parede.
        motivo += ' (no Mac, confira em Ajustes do Sistema > Privacidade e Segurança > Gravação de Tela se o app que abriu o Quem Canta está permitido)'
    return None, {"erro": motivo}
