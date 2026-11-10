"""Texto de imagens pelo OCR do próprio Windows (Windows.Media.Ocr), em português.

Não tem custo nem programa a instalar: usa o reconhecimento de texto que vem com o Windows (o idioma pt-BR precisa
estar instalado, o que é o padrão num Windows em português). Serve para os stories, que quase nunca têm legenda.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

IDIOMA = "pt-BR"


def disponivel() -> bool:
    try:
        from winrt.windows.media.ocr import OcrEngine
    except ImportError:
        return False
    return any(l.language_tag == IDIOMA for l in OcrEngine.available_recognizer_languages)


def ler_texto(caminho: Path) -> str:
    """Texto da imagem, linha a linha numa só string. Vazio se não houver texto.

    Roda numa thread própria: a coleta acontece dentro do laço de eventos do Playwright, onde `asyncio.run` falha."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, _ler(Path(caminho).resolve())).result()


async def _ler(caminho: Path) -> str:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile

    arquivo = await StorageFile.get_file_from_path_async(str(caminho))
    fluxo = await arquivo.open_async(FileAccessMode.READ)
    decodificador = await BitmapDecoder.create_async(fluxo)
    bitmap = await decodificador.get_software_bitmap_async()
    motor = OcrEngine.try_create_from_language(Language(IDIOMA))
    resultado = await motor.recognize_async(bitmap)
    return " ".join(linha.text for linha in resultado.lines)
