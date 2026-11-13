"""Os vídeos das rádios (radios.py) viram texto no próprio PC, para o radar saber o que foi dito no ar.

A vigilância põe na fila (tabela transcricoes) os vídeos novos das contas da lista radios_to, com o endereço da faixa
de áudio que a própria grade do perfil traz: nenhuma leitura a mais. Daí em diante, num processo à parte
(python -m radar transcrever, ou transcrever.ps1):
1. baixa só o áudio, direto da rede de distribuição, sem a conta do Instagram;
2. converte para 16 kHz mono com o ffmpeg do imageio-ffmpeg (até 15 minutos) e apaga o arquivo baixado;
3. transcreve com o faster-whisper na CPU (modelo small em int8, com detecção de voz);
4. guarda a transcrição completa fora do repositório (RADAR_MIDIA_DIR) e, no banco, só os trechos que falam de
   energia (com um vizinho de cada lado), com os telefones mascarados;
5. com energia na fala, a DeepSeek analisa o post (mencoes.py, com os trechos), a menção negativa vira alerta e a
   consolidação agrupa a mesma notícia (fatos.py).
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import capas, config, db, mencoes, perfis

DIAS_FILA = 7  # vídeo mais velho que isso não entra na fila
MAX_SEGUNDOS = 15 * 60  # do vídeo longo (uma live), só os primeiros 15 minutos
MAX_SEGUNDOS_AO_VIVO = 31 * 60  # o bloco do ao vivo (aovivo.py) inteiro
MAX_BYTES = 150_000_000
TENTATIVAS = 3
ESPERA = 300  # fila vazia: olha de novo depois disso
LOTE = 10
TAM_TRECHOS = 2000
TAXA = 16000
# Só nomes próprios: palavras de assunto no prompt levam o modelo a "ouvir" o assunto na música e no silêncio.
PROMPT = "Rádio do Tocantins. Energisa, Palmas, Araguaína, Gurupi, Porto Nacional, Paraíso do Tocantins."
# Frases que o Whisper inventa na música e no silêncio.
ALUCINACOES = ("legendas pela comunidade", "amara.org", "obrigado por assistir", "obrigada por assistir",
               "inscreva-se no canal", "legendado por", "legenda adriana zanotto")
RE_TELEFONE = re.compile(r"(?<!\d)(?:\+?55[\s.-]?)?(?:\(?\d{2}\)?[\s.-]?)?9?[\s.-]?\d{4}[\s.-]?\d{4}(?!\d)")
ESTADOS = ("pendente", "feita", "sem_fala", "expirado", "erro")


class Expirado(Exception):
    """O endereço assinado venceu, ou o post saiu do ar."""


@dataclass
class Trecho:
    inicio: float
    fim: float
    texto: str


# --- Fila ------------------------------------------------------------------------------------

def expira(url: str | None) -> str | None:
    """Quando o endereço assinado vence (o parâmetro `oe`, em hexadecimal), em ISO."""
    oe = urllib.parse.parse_qs(urllib.parse.urlsplit(url or "").query).get("oe", [None])[0]
    try:
        return datetime.fromtimestamp(int(oe, 16), timezone.utc).isoformat(timespec="seconds") if oe else None
    except ValueError:
        return None


def enfileirar(con, username: str, itens: list[dict], agora: datetime | None = None) -> int:
    """Os vídeos (de até DIAS_FILA dias) de uma rádio da lista entram na fila. O endereço de um vídeo que ainda
    espera é renovado a cada visita, porque vence em uns 2 dias. Devolve quantos entraram."""
    agora = agora or datetime.now(timezone.utc)
    novos = 0
    for p in itens:
        url = p.get("audioUrl") or p.get("videoUrl")
        publicado = perfis.ler_data(p.get("timestamp"))
        if p.get("type") != "Video" or not url or publicado is None or agora - publicado > timedelta(days=DIAS_FILA):
            continue
        post_id = str(p.get("id") or p.get("shortCode"))
        linha = con.execute("SELECT estado FROM transcricoes WHERE rede = 'instagram' AND post_id = ?",
                            (post_id,)).fetchone()
        if linha is None:
            con.execute(
                "INSERT INTO transcricoes (rede, post_id, username, estado, midia_url, midia_expira, duracao, criado_em) "
                "VALUES ('instagram', ?, ?, 'pendente', ?, ?, ?, ?)",
                (post_id, username, url, expira(url), p.get("videoDuration"), db.agora()))
            novos += 1
        elif linha["estado"] in ("pendente", "expirado"):
            con.execute("UPDATE transcricoes SET estado = 'pendente', midia_url = ?, midia_expira = ?, "
                        "duracao = COALESCE(?, duracao) WHERE rede = 'instagram' AND post_id = ?",
                        (url, expira(url), p.get("videoDuration"), post_id))
    return novos


def vencer(con, agora: datetime | None = None) -> int:
    """Os endereços vencidos saem da fila; nenhum endereço fica no banco depois de usado."""
    agora = agora or datetime.now(timezone.utc)
    n = con.execute("UPDATE transcricoes SET estado = 'expirado', midia_url = NULL WHERE estado = 'pendente' "
                    "AND midia_expira IS NOT NULL AND midia_expira < ?",
                    (agora.isoformat(timespec="seconds"),)).rowcount
    con.execute("UPDATE transcricoes SET midia_url = NULL WHERE estado != 'pendente' AND midia_url IS NOT NULL")
    con.commit()
    return n


def fila(con, limite: int = LOTE) -> list[dict]:
    """Os vídeos à espera, os mais novos primeiro (a notícia de hoje vale mais que a de ontem)."""
    return [dict(l) for l in con.execute(
        "SELECT t.*, p.publicado_em FROM transcricoes t "
        "LEFT JOIN posts p ON p.rede = t.rede AND p.post_id = t.post_id "
        "WHERE t.estado = 'pendente' AND t.midia_url IS NOT NULL ORDER BY p.publicado_em DESC LIMIT ?", (limite,))]


def minutos_no_dia(con, agora: datetime | None = None) -> float:
    agora = agora or datetime.now(timezone.utc)
    return con.execute("SELECT COALESCE(SUM(segundos), 0) / 60.0 FROM transcricoes WHERE feito_em >= ?",
                       ((agora - timedelta(days=1)).isoformat(timespec="seconds"),)).fetchone()[0]


def situacao(con) -> dict:
    estados = dict(con.execute("SELECT estado, COUNT(*) FROM transcricoes GROUP BY estado").fetchall())
    espera = con.execute("SELECT COALESCE(SUM(MIN(COALESCE(duracao, 60), ?)), 0) / 60.0 FROM transcricoes "
                         "WHERE estado = 'pendente'", (MAX_SEGUNDOS,)).fetchone()[0]
    energia = con.execute("SELECT COUNT(*) FROM transcricoes WHERE energia = 1").fetchone()[0]
    return {"estados": estados, "minutos_na_fila": espera, "minutos_no_dia": minutos_no_dia(con),
            "com_energia": energia}


# --- Áudio e transcrição -----------------------------------------------------------------------

def baixar(url: str, destino: Path, timeout: float = 60) -> int:
    """O arquivo do endereço, direto da rede de distribuição, sem cookies nem a conta do Instagram."""
    pedido = urllib.request.Request(url, headers={"User-Agent": capas.AGENTE, "Accept": "*/*"})
    total = 0
    try:
        with urllib.request.urlopen(pedido, timeout=timeout, context=capas._contexto_ssl()) as resposta, \
                open(destino, "wb") as arquivo:
            while bloco := resposta.read(1 << 20):
                total += len(bloco)
                if total > MAX_BYTES:
                    raise ValueError(f"arquivo com mais de {MAX_BYTES // 1_000_000} MB")
                arquivo.write(bloco)
    except urllib.error.HTTPError as erro:
        if erro.code in (403, 404, 410):
            raise Expirado(f"HTTP {erro.code}") from erro
        raise
    return total


def pcm(caminho: Path, max_segundos: int = MAX_SEGUNDOS):
    """O áudio em 16 kHz mono (float32), pelo ffmpeg do imageio-ffmpeg, até `max_segundos`."""
    import imageio_ffmpeg
    import numpy as np

    saida = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-i", str(caminho), "-t", str(max_segundos),
         "-vn", "-ac", "1", "-ar", str(TAXA), "-f", "s16le", "-"],
        capture_output=True, check=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    return np.frombuffer(saida, np.int16).astype(np.float32) / 32768.0


def _valido(segmento) -> bool:
    """Descarta o trecho que é provavelmente música ou silêncio, repetição em laço ou frase inventada."""
    texto = mencoes.normalizar(segmento.text)
    if not re.search(r"[a-z]", texto):
        return False
    if segmento.no_speech_prob > 0.6 and segmento.avg_logprob < -1.0:
        return False
    if segmento.compression_ratio > 2.4:
        return False
    return not any(frase in texto for frase in ALUCINACOES)


class Transcritor:
    """O faster-whisper na CPU, carregado na primeira vez. O modelo é baixado uma vez para RADAR_MIDIA_DIR/modelos
    (o small tem ~0,5 GB e transcreve ~8x mais rápido que o tempo real num Ryzen de 8 núcleos)."""

    def __init__(self, modelo: str = config.TRANSCRICAO_MODELO, threads: int = config.TRANSCRICAO_THREADS):
        self.nome, self.threads, self._modelo = modelo, threads, None

    def carregar(self) -> None:
        """Antes da fila: um erro aqui (sem internet no primeiro download) não pode marcar os vídeos como erro."""
        if self._modelo is None:
            os.environ.setdefault("HF_HOME", str(config.MIDIA_DIR / "hf"))
            os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
            from faster_whisper import WhisperModel

            self._modelo = WhisperModel(self.nome, device="cpu", compute_type="int8", cpu_threads=self.threads,
                                        download_root=str(config.MIDIA_DIR / "modelos"))

    def __call__(self, audio) -> list[Trecho]:
        self.carregar()
        segmentos, _ = self._modelo.transcribe(audio, language="pt", vad_filter=True, beam_size=1,
                                               condition_on_previous_text=False, initial_prompt=PROMPT)
        return [Trecho(s.start, s.end, " ".join(s.text.split())) for s in segmentos if _valido(s)]


def mascarar(texto: str) -> str:
    return RE_TELEFONE.sub("[telefone]", texto)


def _tempo(segundos: float) -> str:
    return f"{int(segundos) // 60:02d}:{int(segundos) % 60:02d}"


def trechos_de_energia(trechos: list[Trecho]) -> str:
    """Os trechos que falam de energia (mencoes.RE_ENERGIA), com um vizinho de cada lado para dar contexto, e os
    telefones mascarados. Vazio se a fala não toca no assunto."""
    marcados = set()
    for i, t in enumerate(trechos):
        if mencoes.menciona_energia(t.texto):
            marcados |= {i - 1, i, i + 1}
    blocos, anterior = [], None
    for i in sorted(j for j in marcados if 0 <= j < len(trechos)):
        linha = f"[{_tempo(trechos[i].inicio)}] {mascarar(trechos[i].texto)}"
        if anterior is not None and i == anterior + 1:
            blocos[-1] += " " + linha
        else:
            blocos.append(linha)
        anterior = i
    return " (…) ".join(blocos)[:TAM_TRECHOS]


def arquivo_completo(rede: str, post_id: str) -> Path:
    return config.MIDIA_DIR / "transcricoes" / f"{rede}_{post_id}.txt"


def processar(con, item: dict, transcritor, baixar=baixar, converter=pcm) -> tuple[str, bool]:
    """Um vídeo da fila, do download à gravação. Devolve o novo estado e se a fala tem energia."""
    pasta = config.MIDIA_DIR / "tmp"
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = urllib.parse.urlsplit(item["midia_url"]).path
    local = item["midia_url"].startswith("file:")  # bloco gravado do ao vivo (aovivo.py): já está no disco
    destino = (Path(urllib.request.url2pathname(caminho)) if local
               else pasta / f"{item['rede']}_{item['post_id']}{Path(caminho).suffix or '.mp4'}")
    chave = (item["rede"], item["post_id"])
    inicio = time.process_time()
    try:
        if not local:
            baixar(item["midia_url"], destino)
        audio = converter(destino, MAX_SEGUNDOS_AO_VIVO) if item["rede"] == "radio" else converter(destino)
        trechos = transcritor(audio) if len(audio) else []
    except Expirado as erro:
        con.execute("UPDATE transcricoes SET estado = 'expirado', midia_url = NULL, erro = ? "
                    "WHERE rede = ? AND post_id = ?", (str(erro)[:200], *chave))
        con.commit()
        destino.unlink(missing_ok=True)
        return "expirado", False
    except Exception as erro:  # rede, ffmpeg, modelo: tenta de novo na próxima passada, até TENTATIVAS
        tentativas = (item.get("tentativas") or 0) + 1
        estado = "erro" if tentativas >= TENTATIVAS else "pendente"
        con.execute("UPDATE transcricoes SET estado = ?, tentativas = ?, erro = ?, "
                    "midia_url = CASE WHEN ? = 'erro' THEN NULL ELSE midia_url END WHERE rede = ? AND post_id = ?",
                    (estado, tentativas, f"{type(erro).__name__}: {erro}"[:200], estado, *chave))
        con.commit()
        if estado == "erro" or not local:  # o bloco gravado fica para a nova tentativa
            destino.unlink(missing_ok=True)
        return estado, False
    destino.unlink(missing_ok=True)

    energia = trechos_de_energia(trechos)
    if trechos:
        completo = arquivo_completo(*chave)
        completo.parent.mkdir(parents=True, exist_ok=True)
        completo.write_text("\n".join(f"[{_tempo(t.inicio)}] {t.texto}" for t in trechos) + "\n", encoding="utf-8")
    estado = "feita" if trechos else "sem_fala"
    con.execute(
        "UPDATE transcricoes SET estado = ?, midia_url = NULL, segundos = ?, trechos = ?, energia = ?, modelo = ?, "
        "cpu_segundos = ?, erro = NULL, feito_em = ? WHERE rede = ? AND post_id = ?",
        (estado, round(len(audio) / TAXA, 1), energia or None, int(bool(energia)), getattr(transcritor, "nome", None),
         round(time.process_time() - inicio, 1), db.agora(), *chave))
    con.commit()
    return estado, bool(energia)


# --- Análise e laço ----------------------------------------------------------------------------

def analisar_e_alertar(con, cliente, post_ids: list[str], aviso=print, rede: str = "instagram") -> int:
    """A DeepSeek analisa os posts cuja fala tem energia (com os trechos); a menção negativa nova vira alerta e a
    consolidação agrupa a mesma notícia. Devolve quantos são sobre energia."""
    from . import aovivo, vigia

    posts = aovivo.por_id(con, post_ids) if rede == aovivo.REDE else mencoes.por_id(con, post_ids, rede)
    if not posts:
        return 0

    def veredito(post_id):
        return tuple(con.execute("SELECT sobre_energia, sentimento FROM mencoes WHERE rede = ? AND post_id = ?",
                                 (rede, post_id)).fetchone() or (None, None))

    antes = {p["post_id"]: veredito(p["post_id"]) for p in posts}
    mencoes.analisar(con, cliente, posts, aviso=lambda _: None)
    # Post que já tinha virado alerta pela legenda não vira de novo.
    novos = [p for p in posts if antes[p["post_id"]] != (1, "negativo")]
    vigia.alertar(con, novos, aviso)
    sobre = sum(1 for p in posts if veredito(p["post_id"])[0] == 1)
    if sobre:
        vigia.consolidar(con, cliente, "transcricao", datetime.now(timezone.utc), aviso)
    return sobre


def rodar(con, cliente, transcritor, limite: int = LOTE, aviso=print, agora: datetime | None = None,
          **es) -> Counter:
    """Uma passada pela fila: vence os endereços velhos, transcreve até `limite` vídeos (dentro do teto de minutos
    do dia) e analisa os que falam de energia. `es` troca o download e a conversão nos testes."""
    agora = agora or datetime.now(timezone.utc)
    resumo = Counter()
    resumo["expirados"] += vencer(con, agora)
    com_energia = []
    itens = fila(con, limite)
    if itens and hasattr(transcritor, "carregar"):
        transcritor.carregar()
    for item in itens:
        if minutos_no_dia(con) >= config.TRANSCRICAO_MINUTOS_DIA:
            resumo["teto"] += 1
            aviso(f"teto de {config.TRANSCRICAO_MINUTOS_DIA:.0f} minutos de áudio em 24 h; o resto fica na fila")
            break
        estado, energia = processar(con, item, transcritor, **es)
        resumo[estado] += 1
        if energia:
            com_energia.append((item["rede"], item["post_id"]))
            aviso(f"{item['username']}: fala sobre energia ({item['rede']} {item['post_id']})")
    resumo["com_energia"] += len(com_energia)
    if com_energia and cliente is not None:
        for rede in sorted({r for r, _ in com_energia}):
            resumo["mencoes"] += analisar_e_alertar(con, cliente, [p for r, p in com_energia if r == rede], aviso,
                                                    rede)
    return resumo


def prioridade_baixa() -> None:
    """No Windows, o processo cede a vez à vigilância e ao uso do PC."""
    if os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL


def vigiar(con, cliente, transcritor, ate: datetime, aviso=print, dormir=time.sleep,
           ao_vivo: bool = True) -> Counter:
    """Fica olhando a fila até `ate`; sem nada à espera, dorme ESPERA segundos. Com `ao_vivo`, as rádios da lista
    (aovivo.py) são gravadas numa thread à parte, para a transcrição não atrasar a gravação."""
    import threading

    from . import aovivo

    total = Counter()
    parar = threading.Event()
    gravador = None
    if ao_vivo and aovivo.emissoras():
        gravador = aovivo.gravar_em_segundo_plano(config.DB_PATH, aviso, parar)
        aviso(f"ao vivo: {len(aovivo.emissoras())} rádios, gravadas na janela de cada uma (radios_ao_vivo.csv)")
    try:
        while datetime.now(timezone.utc) < ate:
            resumo = rodar(con, cliente, transcritor, aviso=aviso)
            total.update(resumo)
            if any(resumo[e] for e in ESTADOS):
                aviso(f"{dict(+resumo)}")
            # Fila vazia, só falhas passageiras (ficaram pendentes) ou teto do dia: espera antes da próxima passada.
            if resumo["teto"]:
                dormir(1800)
            elif not any(resumo[e] for e in ESTADOS if e != "pendente"):
                dormir(ESPERA)
    finally:
        parar.set()
        if gravador:
            gravador.join(120)
    return total
