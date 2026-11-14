# Radar de Reputação Energisa — Fase 1: banco de contas do Instagram (Tocantins)

Monta e mantém uma base de perfis do Instagram que publicam **notícias, fofocas e acontecimentos locais** nos
139 municípios do Tocantins. Na fase 2, essa base vai alimentar o monitoramento de menções à Energisa.

## Como funciona

| Etapa | Comando | O que faz | Custo |
|---|---|---|---|
| 1 | `municipios` | Baixa os 139 municípios e a população (Censo 2022) do IBGE e define o porte de cada um. | grátis |
| 2 | `descobrir` | Busca perfis no Instagram com termos por cidade ("gurupi noticias", "fofoca palmas"...). Cidades maiores recebem mais termos. | Apify |
| 3 | `enriquecer` | Coleta o perfil completo das contas que só têm o @ (vindas de relacionadas, hashtags, Google ou da equipe) e descarta privadas, pequenas, inativas e comerciais. | Apify |
| 4 | `classificar` | A IA (Claude ou DeepSeek) classifica cada perfil: tipo, se publica notícia local, municípios cobertos, abrangência e confiança. | Anthropic ou DeepSeek |
| 5 | `expandir` | Traz os "perfis relacionados" dos portais e páginas relevantes (bola de neve, até 2 passos; `--profundidade` aumenta). Depois rode 3 e 4 de novo. | — |
| 6 | `exportar` | Calcula o score (A/B/C) e gera o Excel de revisão, com uma aba de cobertura por município. | grátis |
| 7 | `importar-revisao` | Grava no banco as decisões da equipe (aprovada/rejeitada + nota). | grátis |

`pipeline` roda as etapas 2 a 6 em sequência.

## Instalação

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env   # e preencha APIFY_TOKEN e ANTHROPIC_API_KEY ou DEEPSEEK_API_KEY
.venv\Scripts\python -m pytest
```

**Provedor da IA:** com só a `DEEPSEEK_API_KEY` preenchida, a classificação usa a DeepSeek (`deepseek-flash`, sem
raciocínio, ~US$ 0,0005 por conta). Com a `ANTHROPIC_API_KEY`, usa o Claude. `RADAR_PROVEDOR_IA=deepseek` ou
`anthropic` força a escolha, e `RADAR_MODELO` troca o modelo. As opções `--direto` e `--nao-esperar` valem só para
o Claude.

## Piloto recomendado (Palmas, ~US$ 1 a 3)

Se o PowerShell bloquear o `Activate.ps1`, troque `python` por `.venv\Scripts\python` nos comandos.

```powershell
.venv\Scripts\Activate.ps1
python -m radar municipios
python -m radar descobrir --municipio Palmas --dry-run    # mostra os termos e o custo, sem gastar
python -m radar descobrir --municipio Palmas              # pede confirmação antes de gastar
python -m radar adicionar @conta1 @conta2 --municipio Palmas   # contas que a equipe já conhece (mede a cobertura)
python -m radar enriquecer
python -m radar classificar --direto                      # no piloto; na rodada completa use o batch (metade do preço)
python -m radar expandir
python -m radar enriquecer
python -m radar classificar --direto
python -m radar exportar                                  # data\export\contas_tocantins.xlsx
python -m radar status
```

**Como avaliar o piloto:**
- **Precisão:** entre as 50 primeiras contas de prioridade A, pelo menos 80% devem ser de fato notícia ou fofoca local.
- **Cobertura:** rode `adicionar` com 10 a 20 contas que a equipe já conhece **depois** do `descobrir`. O comando
  informa quantas delas a busca já tinha encontrado sozinha.

Se a precisão ou a cobertura ficarem baixas, ajuste os termos em [radar/termos.py](radar/termos.py) e as regras em
[radar/enriquecimento.py](radar/enriquecimento.py) antes da rodada completa.

## Rodada completa (139 municípios)

```powershell
python -m radar descobrir --dry-run                # ~540 termos, até ~US$ 37
python -m radar descobrir --hashtags --google      # fontes extras: +~US$ 8
python -m radar classificar                        # Message Batches; pode levar até 1 h
python -m radar classificar --nao-esperar          # alternativa: envia e sai; rode de novo depois para gravar
python -m radar expandir
python -m radar enriquecer --limite 1500
python -m radar classificar
python -m radar exportar
```

A aba **Cobertura por município** da planilha lista primeiro as cidades sem nenhuma conta A/B. São essas que
precisam de busca manual ou de `adicionar`.

**O que o piloto mostrou (nov/2026):** a bola de neve rende bem mais que a busca. Os 12 termos de Palmas custaram
US$ 0,31 e trouxeram 9 contas relevantes. Quatro passos de expansão a partir delas (`expandir --profundidade 4`)
custaram ~US$ 3 e chegaram a 257 contas A/B em 91 municípios. Para crescer a base, vale começar pela expansão e
usar a busca só nas cidades que ficarem descobertas. Por padrão, só portais, páginas, fofoca, jornalistas e plantão
policial servem de semente: os relacionados a políticos e prefeituras são quase sempre outros políticos e
prefeituras (`--todos-os-tipos` inclui todos).

**Busca por município no Google (a mais barata):** uma consulta `site:instagram.com "Nome oficial" (noticias OR
fofoca OR plantao OR news OR portal OR acontece)` por município custa US$ 0,0045 no plano FREE. O @ sai da URL do
perfil ou do título "Nome (@usuario)"; posts sem o autor no título são ignorados. Rodar de novo o mesmo comando
faz uma segunda consulta (`"Nome oficial" tocantins`) só nos municípios que ainda não rodaram as duas.

```powershell
python -m radar descobrir --google --sem-busca --dry-run                   # os 139 municípios, ~US$ 0,63
python -m radar descobrir --google --sem-busca --cobertura-max 0 --sim     # só os que ainda não têm conta A/B
python -m radar enriquecer
python -m radar classificar
```

Na rodada de nov/2026, as 139 consultas mais a segunda passada nos 31 descobertos custaram ~US$ 1,15 com a coleta
dos perfis e levaram a base de 91 para 120 municípios. Nas cidades bem pequenas, a segunda passada só acha
prefeitura, câmara e secretarias.

**Relacionados por sinais e Google estadual:** quando a bola de neve chega à profundidade máxima, ainda sobram
milhares de perfis relacionados guardados no banco (de graça, vêm junto com cada perfil coletado). `expandir
--por-sinais` escolhe entre eles os apontados por 2+ portais e páginas relevantes, e os que têm palavra de notícia no
@ ou no nome ("noticias", "portal", "plantao", "fofoca", "radio"...) e ainda estão ligados a um portal relevante ou
citam um município do TO. `--min-ligacoes 99` deixa só esses últimos, que rendem mais. `descobrir --google-estado`
roda 10 consultas estaduais e regionais (Bico do Papagaio, Jalapão, sudeste...) e uma por cidade T1, com várias
páginas de resultado.

```powershell
python -m radar expandir --por-sinais --dry-run                      # lista e estima, sem gastar
python -m radar expandir --por-sinais
python -m radar descobrir --google-estado --sem-busca --paginas 3 --dry-run
python -m radar descobrir --google-estado --sem-busca --paginas 3
python -m radar enriquecer
python -m radar classificar
```

Na rodada de 1º/nov à tarde, as duas fontes custaram US$ 0,53 na Apify e US$ 0,05 na DeepSeek. Trouxeram 39 contas
A/B, 32 delas portais, entre eles @afnoticias, @t1noticiasoficial, @redetv_to e @saomiguelonline_. As monitoradas
foram de 342 para 381 e os municípios cobertos, de 122 para 124. Os relacionados também trazem portais do Maranhão e
do Pará vizinhos ao Bico do Papagaio; a IA os deixa em C.

## Controle de custo

- Todo comando pago mostra a estimativa e pede confirmação (`--sim` pula a pergunta). `--dry-run` não gasta nada.
- Termos já executados ficam na tabela `buscas` e não são repetidos.
- Cada execução da Apify tem teto de gasto (`RADAR_MAX_USD_POR_RUN`, padrão US$ 10).
- O JSON bruto de cada execução fica em `data/raw/`. `importar-brutos` reconstrói o banco a partir deles sem pagar
  de novo.
- `status` soma o gasto registrado na Apify.
- A busca da Apify já devolve o perfil completo. Por isso o enriquecimento pago só roda para contas vindas de
  relacionadas, hashtags, Google ou da equipe.

## Revisão da equipe

1. Abra `data/export/contas_tocantins.xlsx` e preencha as colunas `status` (lista: pendente/aprovada/rejeitada) e
   `nota` na aba **Contas**.
2. Rode `python -m radar importar-revisao data\export\contas_tocantins.xlsx`.
3. Contas rejeitadas deixam de servir de semente para a expansão. Uma nova exportação mantém as decisões.

## Score e prioridade

O score vai de 0 a 100 e soma cinco componentes: alcance (escala log de seguidores, 35%), confiança da IA (20%),
engajamento por post (15%), atividade nos últimos 30 dias (15%) e foco no Tocantins (15%). Contas que não
publicam notícia local, ou que a IA não ligou a nenhum município do Tocantins (exceto as de cobertura estadual),
ficam com 20% do valor e prioridade C. A: ≥ 55, B: ≥ 40. Os pesos estão em
[radar/score.py](radar/score.py).

## Estrutura

```
radar/
  __main__.py        CLI
  config.py          .env e limites
  db.py              esquema SQLite (data/radar.db)
  municipios.py      IBGE, porte, casamento de nomes
  termos.py          termos de busca, hashtags e consultas Google
  apify.py           execução dos atores + cópia bruta
  perfis.py          conversão dos itens da Apify em contas
  descoberta.py      busca, hashtags, Google e bola de neve
  enriquecimento.py  pré-filtro e coleta de perfis
  classificacao.py   prompt, validação e Claude (saída estruturada + Message Batches)
  classificacao_deepseek.py   DeepSeek (modo JSON, chamadas em paralelo)
  score.py           score e prioridade
  exportar.py        Excel de revisão e reimportação
  monitoramento.py   fase 2: coleta de posts e comentários
  mencoes.py         fase 2: filtro de palavras-chave, tema/setor/sentimento/alcance e reação do público
  relatorio.py       fase 2: Excel das menções com engajamento e repercussão
  app_dados.py       JSON do app: menções, contas e o mapa projetado em SVG
  painel.py          painel no navegador (servidor e consultas); a página fica em radar/web/
  capas.py           capas dos posts e fotos dos perfis em data/capas/
  localizacao.py     município aproximado de cada post e conta, para o mapa do painel
  imagens.py         fotos de perfil e capas de menções que faltam, pela Apify (python -m radar imagens)
  estatico.py        o painel sem servidor, em arquivos, para o GitHub Pages (python -m radar site)
  dados/municipios_to.csv   reserva offline dos municípios
  dados/malha_to.geojson    malha municipal do IBGE (qualidade mínima)
tests/               pytest (Apify, Claude e DeepSeek simulados, sem custo)
mobile/              app Expo (React Native): src/app = telas, src/componentes, src/dados, src/tema.ts
```

O banco tem a coluna `rede` em todas as tabelas de contas, o que permite incluir X, Facebook e TikTok sem migração.

## Observação legal

A base guarda apenas dados públicos de perfis que publicam conteúdo. A coleta por scraper vai contra os termos de
uso do Instagram. Antes de usar em produção, convém ter o aval do jurídico ou do compliance da concessionária.

A coleta pelo navegador também contraria os termos e usa uma conta real: o Instagram pode pedir verificação ou
bloquear essa conta por algumas horas. O radar para no primeiro aviso, mas o risco fica com a conta usada.

## Fase 2: menções à energia e à Energisa

| Comando | O que faz | Custo |
|---|---|---|
| `monitorar` | Coleta os 12 posts mais recentes de cada conta monitorada (as A/B e as aprovadas, menos as rejeitadas) e atualiza o engajamento dos posts já vistos. | Apify, ~US$ 0,0026 por conta |
| `noticias` | Matérias de sites de notícia pelo Google Notícias (54 consultas: a marca, a marca com as cidades maiores e temas como "falta de energia" Tocantins). Ficam só as que citam o Tocantins ou um município do TO no título, sem outra distribuidora (Copel, Equatorial...). Entram na mesma base de menções, como rede `web` (o veículo é a "conta" e o título, a legenda), e a IA analisa as que passam no filtro. Matéria negativa vira alerta. | grátis + DeepSeek |
| `mencoes` | Filtra por palavras-chave (Energisa, falta de energia, apagão, conta de luz, poste, iluminação pública...) e a IA confirma se o post é sobre o serviço de energia. Classifica **tema**, **setor da Energisa** mais envolvido (operação, manutenção, atendimento, comercial, obras e expansão, segurança, RH, institucional, regulatório e jurídico, ou fora da Energisa), **sentimento** (efeito na imagem da Energisa), **alcance do impacto** (local, regional, estadual, nacional) e municípios afetados. | DeepSeek, ~US$ 0,0004 por post |
| `reacoes` | Lê pelo navegador até 150 comentários dos posts sobre energia e dos que citam a Energisa. A DeepSeek dá o **sentimento**, o **relato** e o **local** de cada comentário e resume a **reação do público** (positiva, negativa, mista, neutra). Grava só o texto, sem o autor. Ver "A voz da população", abaixo. | grátis (navegador) + DeepSeek; pela Apify, ~US$ 0,0026 por comentário |
| `sentimento` | A noção de sentimento nos comentários de um período: % de negativos e positivos, relatos, lugares, semana a semana. | grátis |
| `relatorio-mencoes` | Gera `data/export/mencoes_energisa.xlsx`. | grátis |
| `gabarito-rodar` | O teste com casos reais (enova IA): passa cada caso de `DemoDay/gabarito/Gabarito_Radar_v0.xlsx` pela análise das menções, num pedido isolado, e guarda as respostas num arquivo à parte, para o autor marcar a resposta certa sem vê-las. Não grava no banco. | DeepSeek, ~US$ 0,02 pelos 40 casos |
| `gabarito-conferir` | Compara a resposta marcada pelo autor com a da IA: acerto por dificuldade, tema e município, e a planilha `Gabarito_Radar_resultado.xlsx`, caso a caso. | grátis |
| `fatos` | A mesma notícia: junta as menções que tratam do mesmo fato (link em comum, semelhança do resumo e decisão da DeepSeek). A ficha do post e a aba "Mesma notícia" do relatório mostram o agrupamento. Ver "A mesma notícia", abaixo. | DeepSeek, centavos |
| `radios-propor` / `radios-aprovar` | As rádios do TO: levanta as contas de emissora, programa e radialista (base, lista curada e busca do Instagram), a DeepSeek classifica e a planilha `data/radios_propostas.xlsx` vai para aprovação. As aprovadas entram na lista `radios_to`, que a vigilância visita no mínimo a cada 6 h. Ver "As rádios", abaixo. | navegador (uma vez) + DeepSeek, ~US$ 0,04 |
| `transcrever` | Transcreve no PC os vídeos que as rádios aprovadas postam (faster-whisper, na CPU). A fala que toca em energia vai para a análise das menções e para os alertas. | grátis (CPU) + DeepSeek só nos vídeos com energia |

Rotina diária sugerida:

```powershell
python -m radar monitorar --sim
python -m radar mencoes
python -m radar reacoes --posts 10
python -m radar relatorio-mencoes --desde 2026-11-01
python -m radar exportar-app
```

**Colunas do relatório:**
- **Engajamento:**
  - `interacoes`: curtidas + comentários.
  - `engajamento_pct`: interações ÷ seguidores.
  - `indice_vs_media_da_conta`: acima de 1, o post rendeu mais que a média dos posts recentes da conta.
  - `horas_ate_medicao`: o engajamento ainda cresce nos primeiros dias.
- **Natureza:** `tema`, `setor` (a área da Energisa que teria de responder ou agir) e `cita_energisa`. Menções analisadas antes de existir o setor: `python -m radar mencoes --completar-setor`.
- **Tom:** `sentimento` do post e `reacao` nos comentários. Os dois podem divergir: notícia positiva sobre obra
  com comentários negativos sobre o preço da energia, por exemplo.
- **Impacto só local?**
  - `alcance`: o que a IA lê no fato.
  - `contas_que_repercutiram`: contas que trataram do mesmo tema no mesmo município em até 3 dias.
  - `teve_cobertura_estadual`: se algum portal estadual tratou do assunto.

As abas `Por tema`, `Por setor`, `Por município` e `Sentimento x alcance` resumem o período.

O profile scraper traz só os 12 últimos posts. Um portal grande passa disso num dia, então `monitorar` precisa rodar
pelo menos uma vez por dia (duas, para os maiores). A análise de menções por enquanto só roda com a DeepSeek.

**Filtro de palavras-chave** (`mencoes.EXPRESSOES_ENERGIA`, por assunto): a distribuidora (Energisa, Celtins, o nome
antigo), falta e queda (com plural e até duas palavras no meio: "quedas constantes de energia", "interrupção
programada no fornecimento"; "acabou a luz", "segue no escuro", "energia restabelecida"), a rede (poste, fio caído,
fiação, árvore nos fios, alta tensão, linha de transmissão), conta e tarifa (reajuste, bandeira, medidor, kWh),
fraude e ligação (gato, ligação clandestina, Luz para Todos) e segurança e danos (choque, curto-circuito, aparelho
queimado). Na dúvida, entra: a IA descarta o ruído. Nos 29 mil posts de 07/11/2026, o filtro ampliado pegou 268
contra 201 do antigo.

**Triagem dos stories pela IA** (`triagem.py`). O filtro só pega o que está escrito de um jeito previsto, e o OCR erra
("Queda de energnae•xa"). Os stories com texto que não passam no filtro vão à IA, 20 por pedido, com um prompt curto
que só devolve os números dos que falam de energia ou da Energisa. Os marcados seguem para a análise completa. Na
vigilância, a triagem roda a cada página visitada, antes de a imagem ser apagada: o story marcado fica com a foto em
`data/stories/`. Stories quase sem texto (foto sem escrita) ficam de fora, porque a IA de texto não tem o que ler.

```powershell
python -m radar triar-stories --dry-run   # quantos stories guardados esperam a triagem
python -m radar triar-stories             # os guardados antes da vigilância ter a triagem (sem a foto)
```

Piloto de 07/11/2026: 2.081 stories em 105 pedidos, ~108 tokens de entrada por story. O saldo da DeepSeek não chegou
a mudar (menos de US$ 0,01). A triagem marcou 5, e a análise completa confirmou 3 sobre energia, entre eles "Queda
de energia mata 400 mil peixes" (OCR com erro) e "Tocantins produz energia mas quem paga a conta é o povo".

Matérias de site (`noticias`) aparecem no relatório (coluna `rede` = web, `tipo_conta` = site_noticias) e no app,
com o nome do veículo no lugar do @. O painel por enquanto mostra só o Instagram.

### Coleta pelo navegador (sem Apify)

`enriquecer`, `monitorar` e `historico` aceitam `--via navegador`, e esse é o padrão quando o `.env` tem
`RADAR_COLETA=navegador`. O radar abre o Edge instalado (pelo Playwright), já logado no Instagram, visita a página de
cada perfil como uma pessoa faria e lê os dados que o próprio site recebe para montá-la. Uma visita traz os dados da
conta, os 12 posts mais recentes e as contas parecidas; cada rolagem da página traz mais 12 posts. Não há custo por
resultado; o preço é o tempo.

- **Login, uma vez:** `python -m radar navegador-login` abre o Edge comum, sem automação (o Instagram estranha login
  em navegador automatizado), já na página de login. Entre no Instagram e feche a janela; o comando confere a
  sessão. A senha fica só no navegador, e a sessão fica em `data/navegador/`.
- **Ritmo:** pausas de 4 a 8 s entre leituras, uma pausa de 2 a 4 min a cada 50, e no máximo 200 leituras por hora e
  2.000 por dia (`RADAR_NAV_MAX_HORA`, `RADAR_NAV_MAX_DIA`). A janela fica visível durante a coleta, porque o modo
  invisível é mais fácil de detectar; `RADAR_NAV_VISIVEL=0` a esconde. Fechar a janela interrompe a coleta.
- **Parada:** um 429, um checkpoint ou um pedido de login param a coleta na hora. O que já foi coletado fica gravado,
  e o mesmo comando continua de onde parou. Espere algumas horas antes de rodar de novo.
- **Monitoramento sem o limite de 12 posts:** pelo navegador, `monitorar` visita cada conta e rola a página até o
  último post já guardado, relê os dos últimos 3 dias (para atualizar curtidas e comentários) e relê o perfil uma vez
  por semana. Toda rodada visita todas as contas monitoradas; uma rodada interrompida retoma pelas que ficaram sem
  visita. No piloto de 4/nov, 5 portais renderam 44 posts novos (de 1º a 4/nov) em 6 visitas.
- **O que a página não traz:** as visualizações de vídeo (a grade do perfil não as mostra).
- **Comentários:** a página do post traz os ~15 primeiros no HTML, e o radar rola o painel de comentários para ler
  os seguintes, de 10 em 10 (cada leva é uma leitura). As respostas a comentários ficam de fora. Ver "A voz da
  população".
- **O que continua só na Apify:** a busca de perfis e o Google do `descobrir`.
- **Stories:** na mesma visita do dia, o perfil mostra se a conta tem story novo (das últimas 24 horas). Só então o
  radar abre a página de stories da conta e lê os dados embutidos no HTML, sem clicar em "Ver story"; no piloto,
  nenhum story foi marcado como visto. O texto de cada story junta a legenda automática do Instagram, os adesivos
  (link da matéria, menções com o nome, hashtags, local) e o OCR do Windows sobre a imagem (`radar/ocr.py`, em pt-BR;
  nos vídeos, sobre a capa). Ele vira a legenda do story na tabela de posts, com tipo `Story`, e passa pelo mesmo
  filtro e pela mesma IA das menções. A imagem e o vídeo dos que falam de energia ficam em `data/stories/`, porque o
  story some em 24 horas. `monitorar --sem-stories` pula essa parte. Como o story dura 24 horas, o monitoramento
  precisa rodar ao menos uma vez por dia; um dia sem rodar perde os stories daquele dia.
- **Por que não os endereços da API:** os endereços `/api/v1/...` que bibliotecas como o Instaloader usavam agora
  respondem 404 mesmo com a sessão aberta. Ler o que a página recebe (`/api/graphql`) é o que o site usa hoje.

```powershell
python -m radar navegador-login
python -m radar monitorar --dry-run          # 381 contas: cerca de 435 leituras, umas 2,2 h
python -m radar monitorar
python -m radar historico --dry-run          # 60 dias pelo navegador
```

### Várias contas em paralelo e o comando `stories`

Cada conta do Instagram tem o seu perfil do Edge (`data/navegador/perfil` para a principal, `perfil-<nome>` para as
outras), a sua sessão e o seu próprio ritmo e tetos de leitura. `monitorar --via navegador` e `stories` soltam um
trabalhador por conta logada, cada um num processo e numa janela próprios. Eles dividem o serviço por uma fila no banco
(tabela `tarefas`): cada um pega um lote de 10 contas monitoradas, coleta, marca como feito e pega o próximo. Se o
Instagram interromper uma conta, ela devolve o lote e para; as outras seguem. Rodar de novo no mesmo dia retoma o que
ficou pendente. Com 3 contas, a rodada diária de ~1.400 visitas cai de ~7 h para ~2,5 h.

```powershell
python -m radar navegador-login --conta conta2     # uma vez por conta; a senha fica só no navegador
python -m radar navegador-contas                   # quais contas estão logadas
python -m radar stories --dry-run                  # quantas contas e quanto tempo por conta do navegador
python -m radar stories                            # todas as contas logadas, em paralelo
python -m radar stories --contas principal,conta2
python -m radar monitorar --via navegador          # posts e stories, em paralelo
python -m radar mencoes                            # a IA classifica o que passou no filtro
```

`stories` abre a página de stories de cada conta monitorada (uma leitura por conta, tenha story ou não), enquanto o
`monitorar` só abre quando a visita ao perfil mostra story novo. Como o story dura 24 horas, vale rodar `stories` duas
vezes por dia (de manhã e à noite) e o `monitorar` uma vez.

As contas saem do mesmo computador e da mesma internet: se o Instagram limitar uma, pode olhar as outras com mais
atenção. O ritmo por conta continua o mesmo; o paralelo só divide o trabalho.

#### Gerenciar as contas

```powershell
python -m radar navegador-contas                     # logada, sem login, em uso agora ou DESATIVADA (com o motivo)
python -m radar navegador-login --conta conta7       # conta nova
python -m radar navegador-abrir --conta conta6       # Edge comum da conta: verificação, recurso de suspensão, sair
python -m radar navegador-desativar conta6 --motivo "em descanso"   # sai do trabalho, o login fica
python -m radar navegador-ativar conta6              # volta na próxima vigilância
python -m radar navegador-remover conta6             # apaga o login; o nome fica livre para outra conta
```

Uma conta que o Instagram levar para a página de suspensão (`/accounts/suspended`) fica desativada sozinha. Sem isso,
ela continuaria "logada" (o cookie fica) e voltaria a cada vigilância. Ficam de fora a vigilância, os comandos em
paralelo e quem tentar abri-la; uma conta desativada no meio da vigilância sai em até 1 minuto. Um pedido de verificação
(`/challenge`) não desativa, porque passa depois que você o resolve em `navegador-abrir`. O `navegador-remover` mantém o
registro de leituras da conta, para o teto continuar valendo se ela voltar com o mesmo nome.

### Vigilância contínua (`vigiar`)

Em vez de uma rodada por dia, o radar fica rodando e visita primeiro as páginas mais atrasadas, conforme a
prioridade de cada uma:

| Prioridade | Quem | Intervalo |
|---|---|---|
| Foco | os 100 influenciadores (lista `influenciadores_to`) e todas as páginas de fofoca do TO | 1 h |
| Alta | portais, páginas de cidade, plantão e jornalistas que já falaram de energia ou têm 30 mil+ seguidores | 2 h |
| Média | os demais desses tipos | 6 h |
| Baixa | órgãos públicos, políticos e o resto | 24 h |

Os intervalos ficam no `.env` (`RADAR_VIGIA_FOCO_H`, `RADAR_VIGIA_ALTA_H`, `RADAR_VIGIA_MEDIA_H`,
`RADAR_VIGIA_BAIXA_H`). Cada conta do Instagram logada vira um trabalhador; eles reservam a página no banco (tabela
`reservas`) para dois não visitarem a mesma. Cada visita traz os posts novos e, se houver, os stories. Um post que
passa no filtro de energia vai na hora para a IA, e as menções negativas entram em `data/alertas.txt`; sem DeepSeek,
fica para o `mencoes`.

```powershell
python -m radar influentes lista --foco influenciadores   # grava os 100 influenciadores (refaça depois do classificar)
python -m radar vigiar --dry-run       # prioridades, visitas por dia e quantas contas são necessárias
python -m radar vigiar --horas 24      # roda por 24 h com todas as contas logadas
python -m radar vigiar --parar         # pede para a vigilância em andamento parar
powershell -ExecutionPolicy Bypass -File .\vigiar.ps1 -Horas 24   # numa janela própria, com log em data\vigia.log
powershell -ExecutionPolicy Bypass -File .\vigiar.ps1 -Horas 24 -Contas conta2,conta3   # só essas contas
python -m radar testar-ia              # confere se a DeepSeek responde daqui (grátis)
```

Para rodar por muitas horas, use o `vigiar.ps1` numa janela do PowerShell aberta fora do VS Code: ela não depende
do editor nem do Claude, e o log fica em `data\vigia.log`. Se uma conta cair por erro inesperado (o Edge não abriu,
travou), ela tenta de novo em 5 minutos, até 3 vezes.

Para render mais com o teto de leituras:
- **stories primeiro:** toda página é visitada pelo menos a cada 20 h (`RADAR_VIGIA_STORIES_H`), antes de qualquer
  revisita, porque os stories somem em 24 h. Só a capacidade que sobra vai para as revisitas de 1 h e 2 h;
- **visitas vazias espaçam a página:** cada visita seguida sem post nem story novo dobra o intervalo dela (até 8 vezes,
  sempre dentro das 20 h); a primeira novidade volta ao intervalo normal;
- **rolar só por post novo:** a grade é rolada até o último post já gravado; curtidas e comentários dos posts dos
  últimos 3 dias se atualizam com a primeira página, que já vem com a visita;
- **perfil sem leitura extra:** os seguidores (atualizados uma vez por semana) saem da própria visita.
- **leituras espalhadas pelo dia:** a vigilância gasta o que resta do teto diário ao longo das 12 h seguintes (entre
  ~33 e 100 leituras por hora), em vez de gastar o teto da hora em minutos e ficar parada até a hora virar.

Proteções das contas:
- teto por conta de 800 leituras por dia e 100 por hora (`RADAR_NAV_MAX_DIA`, `RADAR_NAV_MAX_HORA`); no teto, a conta
  espera as leituras mais velhas saírem da janela de 24 h, em vez de parar;
- as contas entram com 3 minutos de diferença;
- uma conta barrada pelo Instagram (429, verificação, volta para o login) para sozinha, e as outras assumem as páginas;
- duas contas barradas em menos de 1 hora param todas (parada geral, registrada em `data/navegador/PARAR`): o
  problema provavelmente é a conexão, e é melhor esperar algumas horas;
- uma conta logada com a vigilância já rodando (`python -m radar navegador-login --conta conta2`) entra sozinha
  quando a janela de login fecha. No máximo 12 contas ao mesmo tempo (cada Edge usa ~0,5 GB de memória).

Com influenciadores e fofoca a cada 1 h são ~4.300 visitas e ~5.600 leituras por dia: com o teto de 800, pelo menos
8 contas. Com menos contas, a vigilância continua funcionando, só que os intervalos se esticam (ela sempre pega a
mais atrasada em relação ao intervalo, então os de 1 h vêm primeiro).

### Duas voltas por dia (`vigiar --volta` e `agendar.ps1`)

Em vez de ficar ligada o dia todo, a vigia pode dar **voltas**: cada volta passa uma vez por todas as páginas
monitoradas, com todas as contas logadas, e termina. Os stories duram 24 h, então duas voltas por dia, com 12 h
entre elas, não perdem nenhum. Com 12 h desde a volta anterior, são ~860 leituras por volta (~170 por conta com 5
contas): a visita de cada página, a página de stories em 30% a 55% delas e ~10% de comentários, buscas e
investigação. Fica bem abaixo do teto. Depois de dias sem vigia são mais, porque cada página tem mais posts e
comentários a recolher (2,4 leituras por página em 13/nov, depois de 2 dias).

- Entra na volta toda página sem visita nas 4 h antes do começo dela (`RADAR_VIGIA_VOLTA_H`). O que a volta já
  visitou não entra de novo, por mais que ela dure. Uma volta que recomeça (o PC reiniciou no meio) continua de onde
  parou.
- O ritmo é fixo: 60 leituras por hora por conta (`RADAR_VIGIA_VOLTA_POR_HORA`), o que dá ~3 h por volta com 5
  contas. Quase todo o tempo da volta é a pausa entre as leituras; o navegador gasta poucos segundos em cada uma. O
  ritmo da vigilância contínua espalha o teto do dia e desconta o que a conta já leu: com duas voltas por dia cairia
  para ~38 por hora, e a volta passaria de 4 h. Um ritmo maior encurta a volta (pela conta, 80 dá ~2h15 e 90 dá
  ~2 h), mas as contas 2 a 5 nunca leram a mais de ~60 por hora; o teto de 100 por hora vale em qualquer caso. Cada
  conta a mais também encurta a volta, na proporção.
- A ordem: primeiro as páginas com stories em risco (mais de 20 h sem visita); depois por prioridade (fofoca e
  influenciadores, portais, o resto), da visita mais velha para a mais nova.
- A busca de posts, os comentários das menções, a investigação de quem posta e os sites de notícia rodam dentro da
  volta, como na vigilância contínua.
- `--horas` vira o limite da volta. As contas logadas depois do início entram na volta seguinte.

```powershell
python -m radar vigiar --volta --dry-run    # quantas páginas e leituras a volta tem agora
powershell -ExecutionPolicy Bypass -File .\vigiar.ps1 -Volta -Horas 6   # uma volta à mão, numa janela própria
powershell -ExecutionPolicy Bypass -File .\agendar.ps1                  # agenda: voltas às 07:00 e às 19:00
powershell -ExecutionPolicy Bypass -File .\agendar.ps1 -Horarios 06:30,18:30   # outros horários
powershell -ExecutionPolicy Bypass -File .\agendar.ps1 -Estado          # última e próxima volta
powershell -ExecutionPolicy Bypass -File .\agendar.ps1 -Desligar        # desliga a agenda e para a volta em curso
powershell -ExecutionPolicy Bypass -File .\agendar.ps1 -Ligar           # religa
```

A tarefa "Radar - vigia" do Agendador de Tarefas roda com você logado no Windows, porque o Edge precisa da área de
trabalho. Se o PC estiver desligado ou sem ninguém logado na hora, a volta roda assim que possível. Se a volta
anterior ainda estiver rodando, a nova não abre; o `vigiar.ps1` também não sobe uma segunda vigia (contínua ou volta)
quando já há uma rodando, porque as duas disputariam os perfis do Edge. Cada início fica marcado no
`data\vigia.log` com uma linha `=== data hora`.

### As 1.000 páginas mais seguidas do TO

Completam a base monitorada as 1.000 páginas e perfis do Tocantins com mais seguidores: pessoas, criadores e páginas
(notícia, fofoca, entretenimento, cidade). Ficam de fora empresas e órgãos públicos. O critério é só a contagem de
seguidores; as páginas de notícia que já eram monitoradas continuam, estejam ou não entre as 1.000.

A descoberta roda pelo navegador, em rodadas com orçamento de leituras (`radar/influentes.py`):
1. busca do Instagram com termos ("influencer palmas", "humor palmas"...) e os nomes dos 139 municípios;
2. contas parecidas das maiores contas do TO já aceitas;
3. perfil dos candidatos mais promissores: sinal de TO no @ ou no nome, ligação com contas do TO, resultado de busca.

Uma triagem por regra tira privadas, empresas e órgãos públicos, perfis sem ligação com o TO (nome, bio, posts
marcados em cidades do TO ou contas do TO que o apontam) e os que ficam abaixo do corte (a 1.000ª conta; 10 mil
seguidores enquanto a lista não enche). Só os finalistas vão para a IA.

```powershell
python -m radar influentes descobrir --dry-run          # situação, sem leituras
python -m radar influentes descobrir --orcamento 800    # uma rodada; repita em várias noites
python -m radar classificar                             # finalistas, ~US$ 0,0005 cada na DeepSeek
python -m radar influentes lista                        # grava as 1.000; elas entram no monitoramento
```

### Os 100 influenciadores do TO

`--foco influenciadores` muda a descoberta para pessoas que criam conteúdo (influenciadores, criadores, artistas,
humoristas, atletas, apresentadores); ficam de fora portais, páginas, órgãos públicos, políticos e empresas. Conta como
influenciador quem a IA classificou assim ou, antes da IA, quem tem categoria de criador no perfil ("Digital creator",
"Blogger", "Artist", "Public figure"...) ou diz isso na bio, sem nada de loja no nome. As sementes são os influenciadores
do TO já aceitos, e a fila prioriza quem eles apontam como parecido (o Instagram sugere outros criadores da região).

```powershell
python -m radar influentes descobrir --foco influenciadores --tamanho 100 --orcamento 250   # pelo navegador
python -m radar influentes classificar      # a IA confirma os candidatos (pessoa? do TO?), ~US$ 0,0005 cada
python -m radar influentes ranking --foco influenciadores --tamanho 100   # data/export/...influenciadores_100.xlsx
```

A regra sozinha deixa passar ruído (clubes e marcas com nome de cidade do TO, empresas que se dizem "Digital
creator"); só depois do `influentes classificar` a lista fica limpa. Até lá, a planilha marca cada conta como
"a confirmar".

### Páginas de fofoca em todos os municípios

A busca usa a busca de posts por palavra-chave do Instagram (a mesma da seção seguinte). Cada termo mostra uns 24
posts, com o autor, as curtidas e a data. Os termos são o nome da cidade em todos os municípios, mais "fofoca {c}"
nos de 10 a 40 mil habitantes e também "noticias {c}" nos maiores: 172 termos.

O nome leva "to" em três casos:
- termina em "do Tocantins" ("colinas to");
- é homônimo de um lugar de fora (Lajeado, Taguatinga, Paranã);
- é uma palavra comum (Peixe, Almas, Novo Acordo, Talismã).

A 1ª versão usava a busca de contas. Ela mostra só 5 contas por termo e não traz os seguidores, e trazia sobretudo
clones e contas largadas:
- das 77 contas lidas com "fofoca", "fuxico", "sigilo" ou "crush" no @, nenhuma tinha 300 seguidores e posts recentes;
- com "gurupi mil grau" e "gurupi online", vinham contas de 1 a 300 seguidores.

Na busca de posts, só é lido o perfil do autor que passar em seis condições:
1. tem post dos últimos 180 dias na busca;
2. o post tem ao menos 30 curtidas, ou 1.000 se a conta não tem cara de página, para pegar só os influenciadores;
3. o @, o nome ou a legenda citam uma cidade do TO, pela regra dos homônimos, ou "Tocantins";
4. não é privada, não é loja nem órgão público, não é da Energisa e tem nome;
5. não é uma conta já classificada ou descartada;
6. aparece em algum termo da busca.

Os posts sobre energia que aparecem na busca já entram nas menções, com o mesmo filtro da busca de posts.

Os homônimos usam a lista de municípios do Brasil do IBGE (`radar/dados/municipios_brasil.csv`):
- **Nome de fora que contém o de uma cidade do TO:** não conta. "Valparaíso" não é Paraíso, "Ji-Paraná" não é
  Paranã, "Buritis" não é Buriti.
- **Cidade do TO com o mesmo nome de um lugar de fora, fora as maiores, ou com nome de palavra comum:** só conta com
  "TO", "Tocantins" ou "063" no @, no nome ou na legenda. Exemplos são Taguatinga (DF), Colinas (MA), Lajeado (RS) e
  "peixe".

Entram na fila também as contas que o radar já conhecia só de nome (contas parecidas, buscas antigas) com cara de
página local e nome de cidade do TO. Depois de lidas, as contas passam por três filtros:
1. o pré-filtro de sempre: privada, menos de 300 seguidores, sem post há 180 dias;
2. a regra dos homônimos de novo, agora com a bio como sinal de TO. Quem não passa fica `fora_do_to`, sem ir à IA;
3. a IA, a cada 25 contas.

Na 1ª rodada, 28 contas lidas de outros estados ficaram `fora_do_to`.

As que a IA classificar como fofoca do TO entram na vigilância a cada 1 hora. Os portais e as páginas de cidade com
nota A/B também entram.

```powershell
python -m radar buscar-fofoca --dry-run                               # situação, sem leituras
powershell -ExecutionPolicy Bypass -File .\buscar-fofoca.ps1 -Conta principal   # janela própria, log em data\fofoca.log
```

Use uma conta que não esteja na vigilância (o perfil do Edge não abre em duas janelas ao mesmo tempo). No teto
diário da conta, o comando espera e continua; parado no meio, ele retoma de onde parou (termos feitos em `buscas`,
fila em `tarefas`).

### Busca de posts por palavra-chave (qualquer conta)

A busca por palavra-chave do Instagram (`instagram.com/explore/search/keyword/?q=...`) mostra uns 24 posts por termo,
de qualquer conta, numa leitura. Ela acha o que as contas monitoradas não publicaram: a moradora que reclama, o
vereador, o portal pequeno. São 22 termos (`radar/busca_posts.py`):
- a marca nas cidades maiores: "energisa tocantins", "energisa palmas", "energisa araguaína"...;
- temas de energia: "falta de energia palmas", "apagão tocantins", "conta de energia tocantins"...;
- o jeito de falar das pessoas, sem cidade: "energisa", "acabou a energia", "sem energia de novo", "faltou energia",
  "energia caiu", "sem luz".

Cada post cai num de três destinos:

| Destino | Quando |
|---|---|
| fora | conta com "energisa" no @ (a própria Energisa ou associadas); mais de 30 dias; sem palavra de energia; outra distribuidora ou outra área da Energisa ("Energisa MT"...); ou conta que a investigação já situou fora do TO |
| do TO | o texto ou o local cita "Tocantins" ou cidade do TO (regra dos homônimos), ou a conta já é do TO (pela IA ou pela investigação) |
| a investigar | o resto: quem posta sem dizer de onde é ("Até quando vamos aceitar esse abuso vindo da ENERGISA?") |

Os posts do TO vão para a IA, que os analisa como as outras menções, com uma regra a mais: se o fato não acontece no
TO, não conta, porque a Energisa atende outros estados. Menção negativa vira alerta. O estado de cada post fica em
`posts_busca.estado`.

**Onde mora quem posta (`radar/investigacao.py`).** A DeepSeek determina a localização provável do autor de cada
post "a investigar", em duas etapas:
1. só com o que veio na busca (o @, o nome, os posts e os locais marcados), sem leitura. Com confiança de 0,75 ou
   mais, o veredito fica;
2. na dúvida, o radar lê o perfil (uma leitura: bio, link, posts recentes e locais marcados) e a DeepSeek responde
   de novo. No máximo 30 perfis por dia (`RADAR_INVESTIGAR_PERFIS_DIA`). No teto do dia, fica o da etapa 1, e só é do
   TO com confiança de 0,75 ou mais.

A conta é do TO com UF "TO", confiança de 0,75 ou mais e, quando a DeepSeek diz a cidade, um município do TO pela
lista do IBGE: "Vila Rica, TO" vira Vila Rica-MT. Nome de bairro sozinho não basta. Em 9/nov, com o corte em 0,5,
"Alvorada 1 e 2 e Nova Esperança" levaram a Araguaína (confiança 0,55) uma conta de Manaus. A DeepSeek conhece
distritos e bairros: Luzimangues vira Porto Nacional, Taquaralto vira Palmas. A localização fica guardada mesmo quando é de fora ("Ponta Porã-MS"), na
tabela `investigacoes`, e vale por 90 dias; o município do TO vai para `conta_municipio` com fonte `investigacao`.
A planilha mostra a localização provável e a confiança na coluna `local_provavel_da_conta`. Custa ~US$ 0,0002 por
conta.

No reprocessamento dos brutos guardados em 9/nov (188 buscas, sem leitura nova), entraram 36 posts novos. A
investigação situou @euamoluzimangues em Porto Nacional e @julio.borgess em "Vila Rica (TO)", que é de MT (corrigido
depois, com a conferência pelo IBGE); Ponta Porã, Colíder,
Itaporã e Juazeiro ficaram de fora. Saíram alertas de Sandolândia (três dias sem energia na zona rural) e de Araguaçu
(poste caído).

Na vigilância, cada termo roda a cada 6 horas (`RADAR_VIGIA_BUSCA_POSTS_H`; 0 desliga) e é reservado entre as
contas como as páginas. São ~64 leituras por dia, divididas entre as contas. Na 1ª passada, em 7/nov, foram 336 posts
vistos e 36 achados de 31 contas fora da base. Desses, 32 eram sobre energia no TO e 17 negativos (falta de luz no
Jalapão, em Porto Nacional, em Araguaína, conta de R$ 500...), por menos de US$ 0,02 na DeepSeek.

```powershell
python -m radar buscar-posts --dry-run           # situação
python -m radar buscar-posts --conta principal   # os 22 termos e a investigação agora, numa conta fora da vigilância
python -m radar buscar-posts --so-ia             # investiga sem ler perfis (a dúvida fica para a vigilância) e analisa
python -m radar buscar-posts --reprocessar       # passa os brutos dos últimos 30 dias pela triagem, sem leitura
```

### A voz da população: comentários e sentimento

A população fala pouco em post próprio que dê para achar; ela fala nos comentários das páginas locais. As 94 menções
sobre energia de 9/nov somavam 5.695 comentários, e só 76 tinham sido lidos.

**Quais posts:** os comentários são lidos nos posts sobre energia e também nos que citam a Energisa sem ser sobre
falta de luz (vagas, obras, eventos), para medir o sentimento sobre a empresa. As contas da própria Energisa ficam
de fora.

**Quando:** a vigilância lê os comentários de cada menção do último mês quando o post tem 6 h, e relê perto das 48 h
se os comentários cresceram 30% ou mais (`mencoes.comentarios_vencidos`, reservado entre as contas).

**Como:** pelo navegador, até 150 comentários por post (`RADAR_COMENTARIOS_POR_POST`). Quem comentou não é guardado:
só o texto, as curtidas e a data.

**O que a DeepSeek diz de cada comentário (`radar/comentarios.py`)**, em lotes de 40, com o resumo e o município do
post como contexto:
- **sentimento** sobre a Energisa e o serviço de energia: negativo, positivo ou neutro. Crítica a outro alvo
  (prefeitura, polícia, governo) é neutra;
- **relato:** falta de energia, oscilação, conta e tarifa, atendimento, elogio, outro problema da rede elétrica;
- **local:** o bairro, setor ou cidade citado ("Aureny 2", "Setor Sul"). Sem lugar citado, vale a cidade do post.

Depois, a reação de cada post é resumida, como antes (`reacao`, `reacao_resumo`).

**Alerta de relatos:** três ou mais comentários das últimas 12 h relatando falta de energia no mesmo lugar viram um
alerta em `data/alertas.txt` ("5 comentários relatam falta de energia em Setor Sul (Palmas)"), um por lugar por dia.
É a população avisando antes das páginas.

**Onde aparece:**
- `python -m radar sentimento --dias 30`: % de negativos, positivos e neutros, os relatos, os lugares com mais relatos,
  a semana a semana e os posts com mais comentários negativos;
- a planilha (`relatorio-mencoes`): aba "Comentários", um por linha, e nas menções as colunas
  `pct_comentarios_negativos`, `relatos_de_falta` e `locais_dos_relatos`;
- o painel: na seção "Reação" da ficha, a linha "x% negativos · n relatos de falta de luz: lugares", e os comentários
  negativos e positivos marcados com a cor do sentimento.

No piloto de 9/nov (262 comentários de 14 posts, US$ 0,01), a classificação separou "Aqui no Aureny 2 estamos sem
energia novamente" (negativo, falta de energia, Aureny 2) das queixas de iluminação pública dirigidas à prefeitura
(neutras).

```powershell
python -m radar reacoes --dry-run              # posts e leituras
python -m radar reacoes --posts 100            # lê os comentários dos posts ainda não lidos e analisa
python -m radar reacoes --posts 0              # só analisa os comentários já guardados
python -m radar sentimento --dias 30
```

### A mesma notícia (`radar/fatos.py`)

O mesmo fato sai em vários perfis, stories e sites. O choque no IFTO de Dianópolis (9/11) saiu em 24 posts de 17
perfis. O radar junta essas menções num agrupamento (tabelas `fatos` e `fato_mencao`). Os posts continuam como são. A
ficha do post ganha a seção "A mesma notícia em N perfis", com:
- as interações somadas;
- as visualizações;
- a primeira aparição;
- os cartões dos outros posts, cada um com o seu link.

O relatório ganha a aba "Mesma notícia" e as colunas `fato_id`, `fato_posts` e `fato_interacoes`. Os alertas
continuam por post.

Como uma menção entra num agrupamento:
1. **Link em comum.** O mesmo endereço de matéria (o "Link:" de um story ou um link na legenda) junta direto, sem IA.
   Endereço de seção, como `/ultimas-noticias/`, não conta.
2. **Candidatos, sem IA.** Valem os agrupamentos da janela: última aparição até 3 dias antes e primeira até 10 dias
   antes. A nota de cada um é a semelhança do resumo (rapidfuzz), mais bônus de município (+15), de mesmo veículo (+10)
   e de mesmo tema (+5). Ficam até 4 candidatos com nota 55 ou mais.
3. **Sem candidato:** cria um agrupamento novo.
4. **Com candidatos:** a DeepSeek decide, em lotes, se é o mesmo fato.
   - Desdobramentos do mesmo caso contam: vítima identificada, nota da Energisa.
   - Opiniões sobre um assunto geral não contam: dois advogados falando de ICMS são fatos separados.

O site e o perfil do mesmo portal contam como um perfil só: `afnoticias.com.br` e @afnoticias. Isso vale quando o link
da bio é a raiz do site ou o @ é o nome do domínio.

A vigilância agrupa as menções novas a cada 10 minutos, uma consolidação por vez entre os trabalhadores e sem gastar
leitura. Os comandos `mencoes`, `noticias`, `buscar-posts` e `triar-stories` também agrupam no fim.

Na primeira passada (11/nov), as 196 menções viraram 94 agrupamentos, e 26 deles saíram em mais de um post. Os casos
de referência:

| Fato | Agrupamento |
|---|---|
| IFTO de Dianópolis (9/11) | 24 posts de 17 perfis |
| Operação do Enem (6/11) | 11 posts de 8 perfis |
| Tarifa Social (28/10) | 10 posts de 10 perfis |
| Advogados falando de ICMS | 4 agrupamentos separados |

A passada custou cerca de US$ 0,02.

```powershell
python -m radar fatos --dry-run                 # quantas menções faltam e o custo
python -m radar fatos                           # agrupa as que faltam e mostra os maiores
python -m radar fatos --refazer                 # refaz tudo (as correções à mão ficam)
python -m radar fatos-mover --post <post_id> --para 89    # corrige à mão (ou --para novo)
```

### As rádios (`radar/radios.py` e `radar/transcricao.py`)

O radar ouve o que as rádios do TO postam em vídeo no Instagram: programas, entrevistas e trechos do ar. Ele não grava
o sinal ao vivo.

**A lista.** As rádios só entram na vigilância depois de aprovadas.
1. `radios-propor` reúne as candidatas:
   - as contas da base com cara de rádio (nome, @ ou bio com "rádio", "FM", "radialista"...) ou do tipo
     `jornalista_radialista`;
   - com `--descobrir`, as que a busca do Instagram acha para dois tipos de termo:
     - a lista curada (`radar/dados/radios_to.csv`): as emissoras, as redes (Som Alvorada, Som Araguaia, Som
       Tocantins, Folha Popular...) e as comunitárias de nome próprio (Radicalmas FM, Ribeira FM, Kennedy FM...);
     - "rádio <cidade>": nas 15 cidades maiores e em todo município com emissora outorgada, os mais populosos
       primeiro. Os municípios vêm de `radar/dados/radios_mcom_to.csv`, extraído dos dados abertos do Ministério
       das Comunicações: 171 outorgas no TO (50 FM, 18 OM e 103 comunitárias) em 100 municípios.

     Antes de ler um perfil, a conta achada precisa casar com o nome da emissora ou, na busca por cidade, ter cara de
     rádio e citar a cidade. Os termos buscados ficam na tabela `buscas` (fonte `radios_insta`), e a descoberta
     seguinte continua de onde parou (`--orcamento` limita as leituras de cada vez).
2. A DeepSeek classifica cada uma:
   - emissora, programa, radialista ou não;
   - a cidade e a UF, conferidas no IBGE;
   - se publica vídeos.
3. A planilha `data/radios_propostas.xlsx` traz as do TO primeiro, com a coluna "aprovar".
4. `radios-aprovar --planilha` grava a lista `radios_to`, que a vigilância visita no mínimo a cada 6 h.

**Os vídeos, sem leitura a mais.** A grade do perfil já traz, em cada vídeo, um manifesto DASH com uma faixa só de áudio
(uns 55 kbps, menos de 1 MB por minuto). A vigilância põe na fila (tabela `transcricoes`) os vídeos de até 7 dias das
rádios aprovadas. O endereço vence em uns 2 dias e é renovado a cada visita.

**A transcrição** (`transcrever`, numa janela própria e em prioridade baixa):
1. baixa só o áudio, direto da rede de distribuição, sem a conta do Instagram;
2. converte para 16 kHz mono com o ffmpeg (até 15 minutos por vídeo) e apaga o arquivo;
3. transcreve com o faster-whisper `small` em int8, na CPU, com detecção de voz. Trechos de música, repetições e
   frases inventadas são descartados.
4. Com energia na fala:
   - o banco guarda só os trechos sobre energia, com um vizinho de cada lado e os telefones mascarados;
   - a DeepSeek analisa o post com esses trechos;
   - a menção negativa vira alerta;
   - a consolidação junta a mesma notícia.

A transcrição completa e o modelo (~0,5 GB) ficam em `RADAR_MIDIA_DIR` (no `.env`; nesta máquina, `E:\RadarDados`),
fora do repositório. O teto é de 240 minutos de áudio por dia (`RADAR_TRANSCRICAO_MINUTOS_DIA`).

O piloto de 11/nov, com o `small`, transcreveu 128 s de fala em 15 s num Ryzen 7 5700X. A ficha do post mostra a seção
"Fala no vídeo", e o relatório, a coluna `fala_no_video`.

```powershell
python -m radar radios-propor --descobrir        # levanta, classifica e gera a planilha para aprovar
powershell -ExecutionPolicy Bypass -File .\descobrir-radios.ps1 -Orcamento 200   # o mesmo, numa janela própria
python -m radar radios-aprovar --planilha        # grava as marcadas com "sim"
python -m radar transcrever --dry-run            # a fila e o uso do dia
powershell -ExecutionPolicy Bypass -File .\transcrever.ps1 -Horas 24    # janela própria, ao lado da vigilância
python -m radar transcrever --uma-vez --limite 10 --modelo medium        # comparar com o modelo maior
```

### Rádios ao vivo (`radar/aovivo.py`)

O radar ouve os jornais da manhã das rádios do TO, das 6h às 9h, pelo sinal na internet. A lista fica em
`radar/dados/radios_ao_vivo.csv`, com os endereços vindos do catálogo aberto Radio Browser e do site de cada rádio:
- Unitins FM 96.1, Capital FM 87.9, Conexão FM 98.1, Meio Norte 91.1 e Jovem FM, de Palmas;
- Rádio Tocantins, de Porto Nacional.

Para tirar uma rádio, marque `ativo = não`. Para mudar o horário, edite `inicio` e `fim`.

O gravador roda dentro do `transcrever`, numa thread à parte:
1. grava um ffmpeg por rádio, em blocos de 30 minutos, em Opus 24 kbps (uns 5 MB por bloco, em
   `RADAR_MIDIA_DIR/aovivo`);
2. cada bloco vira um "post" da rede `radio` e entra na fila da transcrição, como os vídeos;
3. a fala que toca em energia vai para a análise, com a rádio e o horário, e o aviso de que a rádio também dá notícia
   de fora;
4. a menção negativa vira alerta ("rádio ao vivo unitins_fm"), e o áudio é apagado depois de transcrito.

Se o sinal cair, o bloco gravado até ali vale, e a rádio é tentada de novo em 5 minutos.

As 6 rádios por 3 h dão 18 h de áudio por dia, uns 2 a 3 h de CPU com o `small`. O teto diário é de 1.500 min
(`RADAR_TRANSCRICAO_MINUTOS_DIA`). Os blocos mais novos são transcritos primeiro.

```powershell
python -m radar ao-vivo-testar                  # grava 15 s de cada rádio para conferir os endereços
powershell -ExecutionPolicy Bypass -File .\transcrever.ps1 -Horas 168   # vídeos + ao vivo, numa janela própria
python -m radar transcrever --sem-ao-vivo       # só os vídeos
```

### Sites de notícia pelo feed (`radar/sites.py`)

O Google Notícias não indexa boa parte dos sites pequenos do TO. Por isso o radar também lê direto o feed de uma base
de sites (tabela `sites`), que junta três fontes:
- a curadoria, em `radar/dados/sites_to.csv`:
  - os jornais do TO (Conexão Tocantins, Cleber Toledo, Gazeta do Cerrado, g1 Tocantins...);
  - o jornalismo investigativo do TO (Jornal Opção TO, NC News / Norte Investigação);
  - os investigativos nacionais (Agência Pública, Repórter Brasil, Intercept, InfoAmazonia, Amazônia Real, ((o))eco);
- os veículos que o Google Notícias já trouxe;
- os sites das contas de notícia, rádio e jornalista do TO no Instagram (o link da bio). Entram aí as rádios que
  publicam as suas matérias no site.

`sites-descobrir` acha o feed de cada site, nesta ordem:
1. o RSS anunciado na página;
2. os endereços comuns do WordPress e do Blogger;
3. a API do WordPress.

Agregadores de links, redes sociais e o site da Energisa ficam de fora.

A vigilância faz uma rodada por hora, sem gastar leitura do Instagram:
- lê os feeds;
- grava só as matérias dos últimos 14 dias que passam no filtro de energia. Os sites de escopo nacional, como os
  veículos do Google Notícias e os investigativos nacionais, só entram com matéria que cita o Tocantins;
- a cada 6 h, roda também o Google Notícias;
- a DeepSeek analisa com o título e o começo do texto, a matéria negativa vira alerta e a consolidação junta a mesma
  notícia vinda de site e de Instagram.

```powershell
python -m radar sites-descobrir          # monta a base e acha os feeds (refaz a cada 30 dias, ou --reverificar)
python -m radar sites                    # lê agora, analisa e alerta (a vigilância faz isso a cada hora)
python -m radar sites --dry-run          # a base: quantos sites, quantos com feed, por tipo
```

### Threads e X (`radar/redes.py`)

O ponto de partida é a menção, como na busca de posts do Instagram:
1. a busca por palavra-chave acha os posts sobre energia;
2. a investigação situa quem postou;
3. os posts do TO vão para a análise e para os alertas.

Cada rede tem a sua conta no navegador: a `conta7` (uma conta do Instagram criada só para o Threads) e a `x1`. O
arquivo `data/navegador/redes-<conta>.txt` diz em que rede cada conta trabalha. Sem ele, a conta é só do Instagram.
Assim, a `conta7` não entra nos comandos do Instagram, mesmo que o login do Threads deixe o cookie de lá.

| Item | Threads | X |
|---|---|---|
| Termos (`busca_posts.TERMOS_POR_REDE`) | 12 curtos, a cada 6 h | 5 consultas combinadas, sem retuítes, a cada 3 h |
| Investigação | o veredito do Instagram vale para o mesmo @, sem leitura nem IA | a bio e o local do perfil já entram na etapa 1 |
| Formato | post_id com o prefixo `th_` | post_id com o prefixo `x_` |

A análise recebe a localização provável da conta e a regra "fato fora do TO não conta". O alerta diz a rede ("X @fulano").

Situação em 11/nov:
- prontos: a busca e a investigação por rede, o login por rede e o piloto;
- faltam: os leitores das respostas de cada rede e a entrada na vigilância. Eles saem do piloto, sobre as respostas reais.

```powershell
python -m radar navegador-login --conta conta7 --rede threads   # entre no Threads na janela e feche
python -m radar navegador-login --conta x1 --rede x             # entre no X na janela e feche
python -m radar navegador-contas                                # cada conta com a sua rede
python -m radar piloto-rede --rede threads --conta conta7 --termo energisa --perfil @<um_perfil_do_TO>
python -m radar piloto-rede --rede x --conta x1 --termo "energisa palmas"
```

### Histórico (olhar para trás)

`historico` usa o `apify/instagram-post-scraper` para trazer todos os posts dos últimos N dias de cada conta
monitorada. Ele cobra por post, e a estimativa usa o ritmo de cada conta (medido pelos 12 posts mais recentes). As
contas cujo histórico já cobre o período são puladas, então dá para rodar em partes; `--max-usd` para de abrir lotes
antes de passar do teto.

```powershell
python -m radar historico --dry-run                 # 60 dias, 381 contas: ~20 mil posts, até ~US$ 54
python -m radar historico --limite 3 --sim          # piloto: 3 contas, ~US$ 0,09; confira o preço real por post
python -m radar historico --max-usd 30 --sim        # o resto, em partes, com teto
python -m radar mencoes
python -m radar exportar-app
```

Metade dos posts vem de 25 contas (o @jornaloguaira publica ~24 por dia). O preço usado na estimativa
(`RADAR_PRECO_POST_1K`, US$ 2,70 por mil) é conservador; ajuste-o no `.env` depois do piloto.

## App de visualização (pasta `mobile/`)

App em React Native (Expo SDK 58, Expo Router) com duas visões:
- **Menções:** mapa do Tocantins pintado pelo número de menções, números do recorte, colunas por semana (as 9
  últimas), barras por tema e por setor da Energisa divididas por sentimento e a lista de posts. O detalhe de cada post traz engajamento, repercussão, reação nos comentários,
  legenda e link para o Instagram.
- **Contas:** o mesmo mapa com as contas monitoradas por município, os municípios sem conta e a lista de perfis.

Tocar num município (no mapa ou na lista ao lado), num tema, num setor ou num sentimento da legenda filtra o resto da tela.

O visual usa uma cor de destaque só: o dourado do capim do Jalapão, que pinta o mapa e marca a opção escolhida.
Sentimento é vermelho (negativo), cinza (neutro) e azul (positivo), sempre com a palavra ao lado. A fonte é a Barlow,
inspirada na sinalização de rodovias. O ícone e a tela de abertura são a silhueta do Tocantins com as divisas dos
municípios, desenhada a partir da malha do IBGE. As cores do mapa e dos gráficos passaram no validador de contraste
e de daltonismo da skill de visualização.

O app lê `mobile/src/dados/radar.json`, gerado por `python -m radar exportar-app` (grátis). O arquivo não vai para o
git. A malha dos municípios vem da API de malhas do IBGE e fica em `radar/dados/malha_to.geojson`.

```powershell
python -m radar exportar-app      # atualiza os dados do app
cd mobile
npm install                       # só na primeira vez (precisa do Node.js LTS)
npx expo start                    # QR code: abra no celular com o app Expo Go, na mesma rede Wi-Fi
npx expo start --web              # ou no navegador
```

Com o `expo start` rodando, um novo `exportar-app` recarrega o app sozinho. Um APK instalável (`npx eas-cli build`)
leva os dados do momento do build. Para atualizar sem novo build, o JSON teria de ser publicado num servidor.

## Painel no navegador (estilo streaming)

`python -m radar painel` abre http://127.0.0.1:8787, lido direto do banco e só para leitura: pode ficar aberto junto
com a vigilância, e as menções aparecem à medida que a vigilância e a IA as encontram.

**O painel só mostra o que tem a ver com a Energisa, direta ou indiretamente:** posts e stories do Instagram e
matérias de sites de notícia (rede `web`, de `noticias.py`) que a IA ligou à empresa ou ao serviço de energia
(`mencoes.sobre_energia`), citando a Energisa (`cita_energisa`) ou não (falta de luz, poste caído, conta de energia,
obra na rede), e as contas e sites que os publicaram. O resto do que a vigilância coleta
(sorteio, política, fofoca) fica no banco e não aparece em nenhuma tela, nem na busca, nem aberto por link direto. A
exceção é a página Contas, que lista todas as contas monitoradas. Em 2026-11-09 o usuário confirmou a regra: a aba
Recentes chegou a mostrar todos os posts das contas, e ele pediu de volta "somente conteúdos que direta ou
indiretamente tenham relação com a concessionária".

- **Início:** destaque com as menções dos últimos 14 dias (negativas primeiro), Top 10 da semana por interações (do
  mês, se a semana tiver menos de dez), todas as menções, negativas, as que citam a Energisa, as que tratam de energia
  sem citá-la, stories, reels, uma fileira por tipo de conta (portais, páginas de cidade, fofoca, influenciadores,
  plantão, jornalistas), os municípios com mais menções e as contas que mais falam da Energisa.
- **Recentes:** as menções à Energisa (posts, reels, stories e matérias) das últimas 24 horas, 3 dias ou 7 dias, em
  cartões com a imagem, das mais novas ou das com mais interações (`PERIODOS_RECENTES` em `painel.py`). Não vira
  fileira da tela inicial, onde repetiria "Menções à Energisa".
- **Cartões:** passar o mouse abre a prévia; clicar abre a ficha, com a análise da IA, os números, a repercussão, os
  comentários lidos, a localização aproximada num mapinha e as outras menções da conta. "Ver tudo" abre a fileira
  inteira, com filtros de período, sentimento e ordem.
- **Contas:** todas as monitoradas (e as que falaram da Energisa fora do monitoramento), das mais seguidas para as
  menos ou das que mais falam da Energisa, com filtro por tipo. O cartão mostra o número de menções e de negativas de
  quem já falou da empresa e os seguidores de quem não falou. A página da conta traz só os stories e as menções dela.
  A busca de contas segue a mesma lista.
- **Setores:** as menções à Energisa separadas pelo setor da empresa que teria de responder ou agir (o `setor` que a
  IA escolhe: operação, manutenção, atendimento, comercial, obras e expansão, segurança, RH, institucional,
  regulatório e jurídico, ou fora da Energisa). No alto, uma barra por setor, do tamanho do número de menções e
  dividida em negativas, neutras e positivas; embaixo, uma fileira de cartões por setor, com "Ver tudo".
- **Mapa:** cada menção (ou conta que publicou uma) é um ponto no seu município, com zoom (roda do mouse, + e −) e
  arrasto. A cor do município diz quantos itens ele tem, e a lista ao lado traz a mesma contagem em texto. Clicar num
  ponto abre a ficha (ou a página da conta); clicar no município abre a página dele. A camada "Contas da base" mostra
  o alcance da base: as contas da página Contas e todas as que passaram no pré-filtro e que a IA ligou ao TO, menos as
  irrelevantes, com quantos municípios elas cobrem e a parte da população do estado que mora neles. Ficam de fora as
  que só a busca por município achou, porque ela traz contas de fora com nome parecido (Sampaio, Almas, Cariri).
- **Notícias:** as matérias de sites ligadas à Energisa, também numa fileira da tela inicial. O cartão traz a
  manchete e o nome do site, e a ficha abre o link da matéria. Elas entram nas menções, nos setores, no mapa e na
  busca, mas não no Top 10 (site não tem curtidas).
- **Menu:** Recentes, Menções, Setores, Notícias, Reels, Stories, Contas, Mapa e a busca, que ignora acentos e olha o
  texto do post e o resumo da IA.

A visualização também está em https://github.com/LPKramer/RadarRep (repositório público), com um banco de
demonstração das menções à Energisa e as imagens delas. A cópia local fica em `C:\Users\Luiz\Downloads\RadarRep`.

O painel completo está publicado pelo GitHub Pages em https://lpkramer.github.io/RadarRep/. Lá não há servidor:
`python -m radar site` grava em `site/` a resposta da API a cada pedido das telas (`radar/estatico.py`), com as capas,
as fotos e a mídia dos stories já baixadas, e `radar/web/estatico.js` lê esses arquivos no lugar do servidor. Tudo o
que o painel mostra fica público: as menções, as contas monitoradas, as bios e os comentários lidos.

Antes de gravar, o `site` consolida as notícias com a DeepSeek, por centavos, para cada notícia sair num cartão só.
As menções sem agrupamento ganham um, e são revisados todos os pares de agrupamentos parecidos, não só os dos últimos
3 dias, como faz a vigilância. Cada par é decidido uma vez só, e `--sem-ia` pula essa etapa. A vigia faz a mesma
consolidação quando termina, inclusive no fim de cada volta.

Para atualizar o site, use o `publicar-site.ps1`. Ele consolida, gera o site na pasta `site` do RadarRep, que é o ramo
`gh-pages`, e confere que o site saiu inteiro, com uma ficha por menção, a página de contas, a busca e as fileiras. Só
depois disso faz o commit e o push. A geração leva alguns minutos sem mostrar nada; espere a mensagem final.

```powershell
powershell -ExecutionPolicy Bypass -File .\publicar-site.ps1            # consolida, gera, confere e publica
powershell -ExecutionPolicy Bypass -File .\publicar-site.ps1 -SemPush   # só gera e confere
```

Os comandos soltos fazem o mesmo, mas não conferem nada. Um site interrompido no meio, commitado assim, publica um
painel sem dados, como aconteceu em 13/nov:

```powershell
python -m radar site --saida C:\Users\Luiz\Downloads\RadarRep\site
git -C C:\Users\Luiz\Downloads\RadarRep\site add -A
git -C C:\Users\Luiz\Downloads\RadarRep\site commit -m "Dados atualizados"
git -C C:\Users\Luiz\Downloads\RadarRep\site push https://github.com/LPKramer/RadarRep.git gh-pages
```
- **Ao vivo:** aparece quando alguma página foi visitada nos últimos 15 minutos; o botão laranja embaixo diz quantas
  menções novas a IA achou desde que a tela abriu.

**Localização aproximada** (`radar/localizacao.py`). O post fica no município que a IA apontou no fato (menções);
sem isso, no da marcação de local do post; depois, no primeiro município citado no texto, só com inicial maiúscula
("salva de palmas" não vira Palmas); por fim, na cidade da conta. A conta fica na cidade que a IA ligou a ela (com
mais de três municípios ela é estadual e fica fora do mapa); sem a IA, vale a da bio ou a da busca que a achou. A ficha
diz de onde veio a pista. Um erro na cidade da conta passa para o mapa.

**Capas e fotos.** O coletor guarda o endereço da imagem de cada post (`posts.capa`) e da foto de cada perfil
(`contas.foto`, renovada a cada visita da vigilância). Esses endereços vencem uns 4 dias depois da leitura, então o
painel baixa as imagens em segundo plano para `data/capas/`, direto da rede de distribuição do Instagram, sem a conta
logada. Só o que aparece na tela ganha imagem: a capa de cada menção e a foto de cada conta que publicou uma. Como a IA
analisa o post minutos depois da coleta, o endereço ainda vale quando ele vira menção. A capa de um story sai da
imagem que o coletor salvou para o OCR, sem leitura extra. As capas de posts que não são menção são apagadas. Post
sem capa vira um cartaz com o texto da legenda.

Conta sem foto e menção coletada antes de o radar guardar o endereço da capa precisam de leitura nova:
`python -m radar imagens` (`radar/imagens.py`) lê pela Apify só o que falta. O profile scraper traz a foto e os 12
posts recentes de cada conta, e o instagram-scraper traz, pelo link, a capa das menções mais antigas. As imagens são
baixadas na hora. Custa ~US$ 2,60 por mil perfis e para antes de começar se a estimativa passar de `--max-usd`.

**Visual.** Saiu da skill ui-ux-pro-max (`.claude/skills/ui-ux-pro-max`), para o tipo de produto "Video
Streaming/OTT": tema escuro, destaque dominante no topo, Barlow Condensed nos títulos e Barlow no texto. A identidade
vem da pasta `Assets`: o logo, vetorizado em `radar/web/marca/` (a chama também é o ícone da aba e o grafismo do
destaque), e a planilha de cores. O fundo é o Azul BI escurecido; o Azul do logo marca navegação, foco e botões; o
Laranja do logo marca o que é quente (ao vivo, novidades, Top 10, ranking). O sentimento usa o vermelho, o cinza e o
verde da planilha, sempre com a palavra. Ícone, só onde age: as setas das fileiras, o X que fecha a ficha e o zoom do
mapa.

```powershell
python -m radar painel              # abre o navegador
python -m radar painel --rede       # também no celular, na mesma rede Wi-Fi
python -m radar capas               # baixa agora as capas pendentes (o painel aberto já faz isso)
python -m radar imagens             # Apify: fotos de perfil e capas de menções que faltam (pago, pede confirmação)
```
