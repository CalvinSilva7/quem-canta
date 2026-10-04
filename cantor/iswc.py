"""ISWC (código internacional da obra musical): normalização e dígito verificador."""

import re

_FORMATO = re.compile(r"^T(\d{9})(\d)$")


def compactar(texto) -> str:
    """"t-123.456.789-0" -> "T1234567890" (sem validar)."""
    if texto is None or texto != texto:  # None ou NaN
        return ""
    return re.sub(r"[\s.\-–—]", "", str(texto)).upper()


def digito_verificador(nove_digitos: str) -> int:
    """Dígito do ISWC pela ISO 15707: 1 (o "T") + soma de cada dígito vezes a posição, mod 10."""
    soma = 1 + sum(int(d) * peso for peso, d in enumerate(nove_digitos, start=1))
    return (10 - soma % 10) % 10


def normalizar_iswc(texto):
    """Devolve (iswc no formato T-123.456.789-0, motivo do erro).

    Aceita T-123.456.789-0, T-123456789-0, T1234567890 e variações de espaço.
    Vazio devolve ("", ""); inválido devolve ("", motivo).
    """
    compacto = compactar(texto)
    if not compacto:
        return "", ""
    casamento = _FORMATO.match(compacto)
    if not casamento:
        return "", f'formato inválido: "{str(texto).strip()}"'
    digitos, verificador = casamento.groups()
    if digito_verificador(digitos) != int(verificador):
        return "", f'dígito verificador não confere: "{str(texto).strip()}"'
    return f"T-{digitos[:3]}.{digitos[3:6]}.{digitos[6:]}-{verificador}", ""
