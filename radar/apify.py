"""Execução de atores da Apify com teto de gasto e cópia bruta de cada resultado em data/raw/."""
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from apify_client import ApifyClient

from . import config


class Apify:
    def __init__(self, token: str | None = None, raw_dir: Path = config.RAW_DIR):
        token = token or config.APIFY_TOKEN
        if not token:
            raise RuntimeError("APIFY_TOKEN não configurado. Copie .env.example para .env e preencha.")
        self.cliente = ApifyClient(token)
        self.raw_dir = raw_dir
        self.gasto_usd = 0.0  # soma do custo das execuções feitas por esta instância

    def rodar(self, ator: str, entrada: dict, rotulo: str,
              max_usd: float = config.MAX_USD_POR_RUN) -> tuple[str, list[dict]]:
        """Roda o ator até terminar e devolve (id da execução, itens do dataset)."""
        # logger=None: sem repassar o log do ator, que é longo demais para o terminal.
        run = self.cliente.actor(ator).call(run_input=entrada, max_total_charge_usd=Decimal(str(max_usd)),
                                            logger=None)
        if run is None:
            raise RuntimeError(f"A execução de {ator} não retornou dados.")
        status = getattr(run.status, "value", run.status)
        if status != "SUCCEEDED":
            raise RuntimeError(f"{ator} terminou com status {status}: {run.status_message}")

        itens = list(self.cliente.dataset(run.default_dataset_id).iterate_items())
        self.gasto_usd += float(run.usage_total_usd or 0)
        self._guardar_bruto(ator, entrada, rotulo, run.id, run.usage_total_usd, itens)
        return run.id, itens

    def _guardar_bruto(self, ator, entrada, rotulo, run_id, usd, itens) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
        caminho = self.raw_dir / f"{carimbo}_{rotulo}_{run_id}.json"
        conteudo = {"ator": ator, "rotulo": rotulo, "run_id": run_id, "usd": usd,
                    "entrada": entrada, "itens": itens}
        caminho.write_text(json.dumps(conteudo, ensure_ascii=False, default=str), encoding="utf-8")


def tamanho_lote(coletor, padrao: int) -> int:
    """Itens por chamada a `rodar`. O navegador usa lotes pequenos: se o Instagram interromper, perde-se pouco."""
    return min(padrao, getattr(coletor, "lote_maximo", padrao))


def ler_brutos(raw_dir: Path = config.RAW_DIR):
    """Percorre os brutos já salvos, do mais antigo para o mais novo."""
    for caminho in sorted(raw_dir.glob("*.json")):
        yield json.loads(caminho.read_text(encoding="utf-8"))
