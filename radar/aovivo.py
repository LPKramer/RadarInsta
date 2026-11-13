"""As rádios ao vivo: o radar grava o sinal pela internet no horário dos jornais da manhã (radar/dados/radios_ao_vivo.csv,
das 6h às 9h de Tocantins), em blocos de 30 minutos, e cada bloco entra na fila da transcrição (transcricao.py), como
os vídeos. A fala que toca em energia vai para a análise, com a rádio, a cidade e o horário; a menção negativa vira
alerta e a consolidação (fatos.py) junta a mesma notícia de rádio, site e Instagram.

Cada bloco vira um "post" da rede 'radio' (a rádio é a "conta"). O áudio é gravado em Opus a 24 kbps (uns 5 MB por
bloco), em RADAR_MIDIA_DIR/aovivo, e é apagado depois de transcrito. O gravador roda dentro do `transcrever`.
"""
from __future__ import annotations

import csv
import subprocess
from collections import Counter
from datetime import datetime, time as hora, timedelta, timezone
from pathlib import Path

from . import config, db

REDE = "radio"
TIPO = "AoVivo"
ARQUIVO = Path(__file__).resolve().parent / "dados" / "radios_ao_vivo.csv"
FUSO = timezone(timedelta(hours=-3))  # o Tocantins não tem horário de verão
BLOCO = timedelta(minutes=30)
MINIMO = timedelta(minutes=1)  # bloco mais curto que isso (o sinal caiu) não vale a transcrição
ESPERA_FALHA = timedelta(minutes=5)  # sinal fora do ar: tenta de novo depois disso
MAX_SEGUNDOS = int(BLOCO.total_seconds()) + 60

SQL_POSTS = """
SELECT p.*, 'emissora de rádio' AS tipo_conta, c.nome AS emissora, c.seguidores, c.engajamento_medio,
       NULL AS municipios_conta, NULL AS triagem_energia,
       (SELECT tr.trechos FROM transcricoes tr WHERE tr.rede = p.rede AND tr.post_id = p.post_id AND tr.energia = 1)
         AS transcricao
FROM posts p JOIN contas c ON c.rede = p.rede AND c.username = p.username
WHERE p.rede = 'radio'
"""


def emissoras(arquivo: Path = ARQUIVO) -> list[dict]:
    with open(arquivo, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["ativo"].strip().lower() == "sim" and r["stream"].strip()]


def _hora(texto: str) -> hora:
    h, m = texto.strip().split(":")
    return hora(int(h), int(m))


def fim_da_janela(emissora: dict, agora: datetime) -> datetime | None:
    """O fim da janela de gravação em andamento (em UTC), ou None fora dela."""
    local = agora.astimezone(FUSO)
    inicio = datetime.combine(local.date(), _hora(emissora["inicio"]), FUSO)
    fim = datetime.combine(local.date(), _hora(emissora["fim"]), FUSO)
    return fim.astimezone(timezone.utc) if inicio <= local < fim else None


def comando(stream: str, segundos: int, destino: Path) -> list[str]:
    import imageio_ffmpeg

    return [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "error", "-y",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "30", "-rw_timeout", "30000000",
            "-i", stream, "-t", str(segundos), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k",
            str(destino)]


def registrar(con, emissora: dict, caminho: Path, inicio: datetime, fim: datetime) -> str:
    """O bloco gravado vira um post da rádio e entra na fila da transcrição."""
    local_ini, local_fim = inicio.astimezone(FUSO), fim.astimezone(FUSO)
    post_id = f"{emissora['slug']}_{local_ini:%Y%m%d_%H%M}"
    db.upsert_conta(con, {"username": emissora["slug"], "nome": emissora["nome"], "completo": 1,
                          "url_externa": emissora.get("site") or None, "origem": "ao_vivo"}, rede=REDE)
    con.execute(
        "INSERT INTO posts (rede, post_id, username, url, tipo, publicado_em, legenda, medido_em) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (rede, post_id) DO NOTHING",
        (REDE, post_id, emissora["slug"], emissora.get("site") or emissora["stream"], TIPO,
         inicio.isoformat(timespec="seconds"),
         f"{emissora['nome']} ({emissora['cidade']}), ao vivo das {local_ini:%H:%M} às {local_fim:%H:%M} "
         f"de {local_ini:%d/%m/%Y}", db.agora()))
    con.execute(
        "INSERT INTO transcricoes (rede, post_id, username, estado, midia_url, duracao, criado_em) "
        "VALUES (?, ?, ?, 'pendente', ?, ?, ?) ON CONFLICT (rede, post_id) DO NOTHING",
        (REDE, post_id, emissora["slug"], caminho.resolve().as_uri(), round((fim - inicio).total_seconds()),
         db.agora()))
    con.commit()
    return post_id


class Gravador:
    """Um ffmpeg por rádio dentro da janela, em blocos de até 30 minutos. `passo` fecha os blocos prontos (que vão
    para a fila) e abre os que faltam; chamado a cada minuto pelo laço da transcrição."""

    def __init__(self, con, aviso=print, abrir=subprocess.Popen, lista: list[dict] | None = None,
                 pasta: Path | None = None):
        self.con, self.aviso, self.abrir = con, aviso, abrir
        self.lista = emissoras() if lista is None else lista
        self.pasta = pasta or config.MIDIA_DIR / "aovivo"
        self.gravando: dict[str, tuple] = {}  # slug -> (processo, caminho, início, fim previsto)
        self.pausa: dict[str, datetime] = {}  # slug -> quando tentar de novo (sinal fora do ar)

    @property
    def ativo(self) -> bool:
        return bool(self.lista)

    def _fechar(self, slug: str, agora: datetime, resumo: Counter) -> None:
        processo, caminho, inicio, previsto = self.gravando.pop(slug)
        fim = min(agora, previsto)
        emissora = next(e for e in self.lista if e["slug"] == slug)
        if fim - inicio >= MINIMO and caminho.exists() and caminho.stat().st_size > 10_000:
            registrar(self.con, emissora, caminho, inicio, fim)
            resumo["blocos"] += 1
        else:
            caminho.unlink(missing_ok=True)
            resumo["sem_sinal"] += 1
            self.pausa[slug] = agora + ESPERA_FALHA
            self.aviso(f"{emissora['nome']}: sem sinal (ffmpeg saiu com {processo.returncode}); tento de novo em 5 min")

    def passo(self, agora: datetime | None = None) -> Counter:
        agora = agora or datetime.now(timezone.utc)
        resumo = Counter()
        for slug, (processo, _, _, previsto) in list(self.gravando.items()):
            if processo.poll() is not None or agora >= previsto + timedelta(minutes=2):
                if processo.poll() is None:
                    processo.terminate()
                    processo.wait(30)
                self._fechar(slug, agora, resumo)
        for e in self.lista:
            fim = fim_da_janela(e, agora)
            if not fim or e["slug"] in self.gravando or self.pausa.get(e["slug"], agora) > agora:
                continue
            fim_bloco = min(agora + BLOCO, fim)
            if fim_bloco - agora < MINIMO:
                continue
            self.pasta.mkdir(parents=True, exist_ok=True)
            caminho = self.pasta / f"{e['slug']}_{agora.astimezone(FUSO):%Y%m%d_%H%M}.ogg"
            processo = self.abrir(comando(e["stream"], int((fim_bloco - agora).total_seconds()), caminho),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.gravando[e["slug"]] = (processo, caminho, agora, fim_bloco)
            self.pausa.pop(e["slug"], None)
            resumo["gravando"] += 1
        return resumo

    def parar(self) -> Counter:
        """Fecha as gravações em andamento (o que já foi gravado vai para a fila)."""
        resumo = Counter()
        agora = datetime.now(timezone.utc)
        for slug, (processo, *_rest) in list(self.gravando.items()):
            if processo.poll() is None:
                processo.terminate()
                processo.wait(30)
            self._fechar(slug, agora, resumo)
        return resumo


def gravar_em_segundo_plano(caminho_banco, aviso, parar, intervalo: float = 30):
    """Uma thread que chama `Gravador.passo` a cada `intervalo` segundos até `parar` (threading.Event), com a sua
    própria conexão ao banco. No fim, fecha as gravações em andamento."""
    import threading

    def laco():
        con = db.conectar(caminho_banco)
        gravador = Gravador(con, aviso)
        try:
            while True:
                try:
                    gravador.passo()
                except Exception as erro:  # um bloco que falhou não pode parar a gravação das outras rádios
                    aviso(f"ao vivo: {type(erro).__name__}: {erro}"[:200])
                if parar.wait(intervalo):
                    break
        finally:
            gravador.parar()
            con.close()

    linha = threading.Thread(target=laco, name="ao-vivo", daemon=True)
    linha.start()
    return linha


def por_id(con, post_ids: list[str]) -> list[dict]:
    """Os blocos dados, com os campos da análise."""
    if not post_ids:
        return []
    return [dict(l) for l in con.execute(SQL_POSTS + f" AND p.post_id IN ({', '.join('?' * len(post_ids))})",
                                          post_ids)]


def testar(segundos: int = 15, aviso=print, lista: list[dict] | None = None) -> dict[str, bool]:
    """Grava alguns segundos de cada rádio da lista, para conferir os endereços; não grava nada no banco."""
    import tempfile

    resultado = {}
    for e in emissoras() if lista is None else lista:
        destino = Path(tempfile.gettempdir()) / f"radar_teste_{e['slug']}.ogg"
        try:
            subprocess.run(comando(e["stream"], segundos, destino), capture_output=True, timeout=segundos + 60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            resultado[e["slug"]] = destino.exists() and destino.stat().st_size > 2_000
        except (subprocess.TimeoutExpired, OSError):
            resultado[e["slug"]] = False
        finally:
            destino.unlink(missing_ok=True)
        aviso(f"{e['nome']:28} {'ok' if resultado[e['slug']] else 'SEM SINAL'}  {e['stream']}")
    return resultado
