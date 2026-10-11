#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""docx -> pdf（走 Word COM）。用法：python docx2pdf.py 文件名.docx"""
import sys
from pathlib import Path

import win32com.client

WD_FORMAT_PDF = 17


def convert(src: Path) -> Path:
    src = src.resolve()
    dst = src.with_suffix(".pdf")
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    try:
        doc = word.Documents.Open(str(src), ReadOnly=True)
        doc.SaveAs(str(dst), FileFormat=WD_FORMAT_PDF)
        doc.Close(False)
    finally:
        word.Quit()
    return dst


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.exists():
            p = Path(__file__).parent / arg
        out = convert(p)
        print(f"OK -> {out}  ({out.stat().st_size} bytes)")
