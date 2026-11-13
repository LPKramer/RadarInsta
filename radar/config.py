"""Configuração lida do .env, com valores padrão para o piloto."""
import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

DATA_DIR = RAIZ / "data"
DB_PATH = Path(os.getenv("RADAR_DB", DATA_DIR / "radar.db"))
RAW_DIR = DATA_DIR / "raw"
EXPORT_DIR = DATA_DIR / "export"
MUNICIPIOS_CSV = Path(__file__).resolve().parent / "dados" / "municipios_to.csv"

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# Provedor da classificação: "anthropic" (Claude, com Message Batches) ou "deepseek".
# Sem RADAR_PROVEDOR_IA, usa a DeepSeek quando só a chave dela está configurada.
PROVEDOR_IA = os.getenv("RADAR_PROVEDOR_IA") or (
    "deepseek" if DEEPSEEK_API_KEY and not os.getenv("ANTHROPIC_API_KEY") else "anthropic")
MODELO_PADRAO = {"anthropic": "claude-haiku-5-5", "deepseek": "deepseek-flash"}
MODELO = os.getenv("RADAR_MODELO") or MODELO_PADRAO[PROVEDOR_IA]

SEGUIDORES_MIN = int(os.getenv("RADAR_SEGUIDORES_MIN", "300"))
SEARCH_LIMIT = int(os.getenv("RADAR_SEARCH_LIMIT", "30"))
PROFUNDIDADE_MAX = int(os.getenv("RADAR_PROFUNDIDADE_MAX", "2"))
CONFIANCA_EXPANSAO = float(os.getenv("RADAR_CONFIANCA_EXPANSAO", "0.7"))
DIAS_INATIVA = 180

# Teto de gasto por execução de ator (a Apify interrompe a execução ao atingir o valor).
MAX_USD_POR_RUN = float(os.getenv("RADAR_MAX_USD_POR_RUN", "10"))

# Preços de referência (US$ por 1000 resultados), usados só na estimativa do --dry-run.
PRECO_BUSCA_1K = float(os.getenv("RADAR_PRECO_BUSCA_1K", "2.30"))
PRECO_PERFIL_1K = float(os.getenv("RADAR_PRECO_PERFIL_1K", "2.30"))
PRECO_HASHTAG_1K = float(os.getenv("RADAR_PRECO_HASHTAG_1K", "2.30"))
PRECO_GOOGLE_1K = float(os.getenv("RADAR_PRECO_GOOGLE_1K", "4.50"))
PRECO_COMENTARIO_1K = float(os.getenv("RADAR_PRECO_COMENTARIO_1K", "2.60"))
PRECO_POST_1K = float(os.getenv("RADAR_PRECO_POST_1K", "2.70"))

ATOR_BUSCA = "apify/instagram-search-scraper"
ATOR_PERFIL = "apify/instagram-profile-scraper"
ATOR_HASHTAG = "apify/instagram-hashtag-scraper"
ATOR_GOOGLE = "apify/google-search-scraper"
ATOR_COMENTARIOS = "apify/instagram-comment-scraper"
ATOR_POSTS = "apify/instagram-post-scraper"

# Coletor de perfis, posts e comentários: "apify" (pago por resultado) ou "navegador" (o Edge logado no
# Instagram, sem custo, mais lento). A busca e o Google do `descobrir` continuam só na Apify.
COLETA = os.getenv("RADAR_COLETA", "apify")
NAVEGADOR_DIR = DATA_DIR / "navegador"
# Janela visível por padrão: o modo invisível (headless) é mais fácil de o Instagram estranhar.
NAV_VISIVEL = os.getenv("RADAR_NAV_VISIVEL", "1") == "1"
# Ritmo das leituras pelo navegador: pausa aleatória entre leituras, pausa longa de tempos em tempos e tetos.
NAV_PAUSA = (float(os.getenv("RADAR_NAV_PAUSA_MIN", "4")), float(os.getenv("RADAR_NAV_PAUSA_MAX", "8")))
NAV_PAUSA_LONGA = (120.0, 240.0)
NAV_LONGA_A_CADA = int(os.getenv("RADAR_NAV_LONGA_A_CADA", "50"))
NAV_MAX_HORA = int(os.getenv("RADAR_NAV_MAX_HORA", "200"))
NAV_MAX_DIA = int(os.getenv("RADAR_NAV_MAX_DIA", "2000"))
# Vigilância contínua (`vigiar`): horas entre visitas por prioridade, e seguidores que põem um portal na alta.
VIGIA_HORAS = {"foco": float(os.getenv("RADAR_VIGIA_FOCO_H", "1")),  # influenciadores e páginas de fofoca
               "alta": float(os.getenv("RADAR_VIGIA_ALTA_H", "2")),
               "media": float(os.getenv("RADAR_VIGIA_MEDIA_H", "6")),
               "baixa": float(os.getenv("RADAR_VIGIA_BAIXA_H", "24"))}
VIGIA_SEGUIDORES_ALTA = int(os.getenv("RADAR_VIGIA_SEGUIDORES_ALTA", "30000"))
# Os stories somem em 24 h: toda página monitorada é visitada pelo menos a cada tantas horas, antes das revisitas.
VIGIA_PRAZO_STORIES_H = float(os.getenv("RADAR_VIGIA_STORIES_H", "20"))
# Uma volta (`vigiar --volta`, duas por dia pelo agendar.ps1) passa por toda página sem visita nas tantas horas antes
# do começo dela e termina. Assim, uma volta que recomeça (o PC reiniciou no meio) continua de onde parou.
VIGIA_VOLTA_H = float(os.getenv("RADAR_VIGIA_VOLTA_H", "4"))
# Ritmo da volta: tantas leituras por hora por conta, fixo, sempre dentro dos tetos NAV_MAX_HORA e NAV_MAX_DIA. O ritmo
# da vigilância contínua espalha o teto do dia e desconta o que a conta já leu: com duas voltas por dia cairia para
# ~38 por hora, e a volta levaria mais de 4 h.
VIGIA_VOLTA_POR_HORA = float(os.getenv("RADAR_VIGIA_VOLTA_POR_HORA", "60"))
# Cada termo da busca de posts por palavra-chave (busca_posts.py) roda a cada tantas horas; 0 desliga.
VIGIA_BUSCA_POSTS_H = float(os.getenv("RADAR_VIGIA_BUSCA_POSTS_H", "6"))
# O mesmo no Threads e no X (redes.py), com as contas próprias de cada rede.
VIGIA_BUSCA_THREADS_H = float(os.getenv("RADAR_VIGIA_BUSCA_THREADS_H", "6"))
VIGIA_BUSCA_X_H = float(os.getenv("RADAR_VIGIA_BUSCA_X_H", "3"))
# Comentários lidos por post pelo navegador (cada 10 a mais é uma leitura). As respostas ficam de fora.
COMENTARIOS_POR_POST = int(os.getenv("RADAR_COMENTARIOS_POR_POST", "150"))
# Perfis lidos por dia na investigação de quem posta (etapa 2, só quando a DeepSeek fica em dúvida).
INVESTIGAR_PERFIS_DIA = int(os.getenv("RADAR_INVESTIGAR_PERFIS_DIA", "30"))

# Transcrição dos vídeos das rádios (transcricao.py). O modelo, o áudio passageiro e as transcrições completas ficam
# fora do repositório, de preferência noutro disco (RADAR_MIDIA_DIR no .env).
MIDIA_DIR = Path(os.getenv("RADAR_MIDIA_DIR") or DATA_DIR / "midia")
TRANSCRICAO_MODELO = os.getenv("RADAR_TRANSCRICAO_MODELO", "small")
TRANSCRICAO_THREADS = int(os.getenv("RADAR_TRANSCRICAO_THREADS", "4"))
# Teto de áudio por dia: os vídeos das rádios e os jornais da manhã ao vivo (6 rádios x 3 h = 1.080 min).
TRANSCRICAO_MINUTOS_DIA = float(os.getenv("RADAR_TRANSCRICAO_MINUTOS_DIA", "1500"))

# Quantos termos ou usernames vão em cada execução de ator.
TERMOS_POR_RUN = 50
PERFIS_POR_RUN = 200
