"""Inspeção mínima de arquivos: hash SHA-256 e verificação da assinatura PDF.

O conteúdo do PDF é lido apenas para calcular o hash e conferir os 5 bytes
iniciais (``%PDF-``). Nada do conteúdo é extraído, interpretado ou registrado.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

PDF_MAGIC = b"%PDF-"
MAX_FILE_BYTES = 50 * 1024 * 1024  # limite defensivo por arquivo


@dataclass(frozen=True)
class FileFacts:
    path: Path
    name: str
    size: int
    sha256: str
    is_pdf: bool


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def inspect_file(path: Path) -> FileFacts:
    path = Path(path).resolve()
    size = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(len(PDF_MAGIC))
    return FileFacts(path=path, name=path.name, size=size, sha256=sha256_file(path), is_pdf=head == PDF_MAGIC)


def list_pdfs(directory: Path, recursive: bool = False) -> list[Path]:
    directory = Path(directory)
    pattern = "**/*" if recursive else "*"
    files = [p for p in directory.glob(pattern) if p.is_file() and p.suffix.lower() == ".pdf"]
    return sorted(files, key=lambda p: p.name.lower())
