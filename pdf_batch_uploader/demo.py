"""Gera PDFs fictícios mínimos para demonstração e testes (sem dados reais)."""

from __future__ import annotations

from pathlib import Path

SAMPLE_NAMES = [
    "folha_ativos_2026-09.pdf",
    "folha_aposentados_09-2026.pdf",
    "folha_ativos_13o_2025.pdf",
    "ferias_202607.pdf",
    "ferias_mensal_2026-08.pdf",  # ambíguo de propósito (Ferias x Mensal)
    "documento_sem_competencia.pdf",
]


def minimal_pdf(label: str) -> bytes:
    """PDF 1.4 válido de uma página com um texto curto (ASCII)."""
    safe = "".join(c for c in label if c.isalnum() or c in " -_.")[:60]
    stream = f"BT /F1 14 Tf 50 750 Td (DOCUMENTO FICTICIO - {safe}) Tj ET".encode("ascii")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def write_samples(target: Path, names: list[str] | None = None) -> list[Path]:
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names or SAMPLE_NAMES:
        p = target / name
        p.write_bytes(minimal_pdf(p.stem))
        paths.append(p)
    return paths
