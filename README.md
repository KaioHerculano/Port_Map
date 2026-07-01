# PortMap Monitor

O **PortMap Monitor** é um sistema profissional desenvolvido em Django para monitoramento assíncrono e em tempo real de equipamentos de rede. Ele oferece suporte a múltiplos protocolos de monitoramento e uma interface moderna baseada em glassmorphism dark theme.

---

## Funcionalidades Principais

### Monitoramento Multi-Protocolo

Cada equipamento pode ter **vários sensores independentes** configurados:

| Sensor | Protocolo | Descrição |
|--------|-----------|-----------|
| **Ping** | ICMP | Mede latência e disponibilidade via ICMP |
| **TCP** | TCP Socket | Verifica se uma porta está aberta e responsiva |
| **SNMP Numérico** | SNMP v2c | Lê OIDs numéricos (CPU, temperatura, uptime) |
| **SNMP Tráfego** | SNMP v2c | Mede taxa de tráfego (Mbps) em interfaces |
| **MikroTik API** | RouterOS API | Lê CPU, temperatura e uptime via API nativa |

### Equipamentos Suportados

- **MikroTik (API RouterOS)** — CPU, temperatura da board, uptime via API
- **MikroTik (SNMP)** — CPU (`hrProcessorLoad`), temperatura (`mtxrHlCpuTemperature`), uptime (`sysUpTime`)
- **OLT Parks GPON (SNMP)** — Ping + SNMP genérico
- **Genérico (SNMP)** — Qualquer OID customizado
- **Genérico (Apenas Ping)** — Monitoramento por latência ICMP

### Gráficos com Unidade Correta por Sensor

O gráfico histórico de cada sensor exibe automaticamente a unidade correta:
- Temperatura → **Histórico de Temperatura (°C)**
- CPU → **Histórico de Uso de CPU (%)**
- Uptime → **Histórico de Uptime (Dias)**
- Tráfego → **Histórico de Tráfego (Mbps)**
- Ping / TCP → **Histórico de Latência (ms)**

### Seleção de Sensores no Cadastro

Ao cadastrar um equipamento, o formulário exibe **checkboxes dinâmicos** com todos os sensores disponíveis para aquele tipo. Todos vêm marcados por padrão; o usuário pode desmarcar qualquer um para não criá-lo.

### Outras Funcionalidades

- **Autenticação por e-mail ou usuário**
- **Cadastro em lote (Bulk)** com suporte a faixas de portas (`10.0.0.1:40001-40048`) e múltiplas portas por vírgula
- **Dashboard estatístico** com filtros rápidos, busca textual e tabela com controle AJAX
- **Relatórios SLA** em PDF por grupo
- **Alertas Telegram** configuráveis por sensor (1ª falha, 2ª falha, 3ª falha consecutiva, ou desabilitado)
- **Grupos** para organizar equipamentos com gestão de alertas em lote
- **Auditoria** de ações dos usuários

---

## Arquitetura de Software

O projeto segue princípios **SOLID, DRY, KISS e Clean Code**:

1. **Class-Based Views (CBVs) exclusivas** — Nenhuma FBV. Todo fluxo web herda das classes genéricas do Django.
2. **Services Layer isolada** (`monitor/services.py`):
   - `PortParserService` — Higienização, validação e expansão de lotes/ranges
   - `PortCheckerService` — Execução dos checks por protocolo, parsing de métricas e geração de dados para gráfico
   - `SLAReportService` — Compilação de dados SLA e geração de PDF
3. **Prevenção de N+1** — Uso de `.select_related()` e `.prefetch_related()` estratégicos
4. **Logging** — `logging` em vez de `print()` em toda a aplicação
5. **Type hints** — Tipagem estrita em funções e métodos

---

## Stack Tecnológica

| Camada | Tecnologia |
|--------|-----------|
| Linguagem | Python >= 3.12 |
| Framework Web | Django >= 4.2 |
| Banco de Dados | PostgreSQL (Docker) / SQLite (dev local) |
| Gerenciador de dependências | Poetry |
| Fila de tarefas | Celery |
| Message Broker | Redis |
| SNMP | pysnmp (hlapi.asyncio) |
| RouterOS | routeros-api |
| Frontend | HTML5, Vanilla CSS, JS Puro, Chart.js, Bootstrap Icons |

---

## Instalação Local

### Pré-requisitos

1. **Python** >= 3.12
2. **Poetry** (`pip install poetry`)
3. **Redis Server** rodando na porta `6379`

### Passo 1 — Instalar Dependências

```bash
poetry install
```

### Passo 2 — Aplicar Migrações

```bash
poetry run python manage.py migrate
```

### Passo 3 — Criar Superusuário

```bash
poetry run python manage.py createsuperuser
```

### Passo 4 — Iniciar os Processos

**Terminal 1 — Servidor Web:**
```bash
poetry run python manage.py runserver
```
Acesse: **http://127.0.0.1:8000/**

**Terminal 2 — Celery Worker:**
```bash
poetry run celery -A app worker --loglevel=info -P threads
```
> A flag `-P threads` é recomendada no Windows para evitar problemas de `asyncio` no Celery.

**Terminal 3 — Celery Beat (agendador):**
```bash
poetry run celery -A app beat --loglevel=info
```

---

## Executando com Docker Compose

O projeto está preparado para rodar em containers com Django, Celery Worker, Celery Beat, Redis e PostgreSQL.

### Subir os containers

```bash
docker compose up --build -d
```

Isso irá:
1. Compilar a imagem Django baseada em `python:3.12-slim`
2. Iniciar PostgreSQL e aguardar `service_healthy`
3. Iniciar Redis
4. Aplicar as migrações automaticamente
5. Iniciar Web, Celery Worker e Celery Beat em rede interna

Acesse: **http://localhost:8003/**

### Criar superusuário no container

```bash
docker exec -it port_map_web poetry run python manage.py createsuperuser
```

### Parar os serviços

```bash
docker compose down
```

---

## Variáveis de Ambiente

Configure o arquivo `.env` (ou as variáveis do Docker Compose):

```env
# Banco de dados (Docker)
DB_NAME=portmap
DB_USER=portmap
DB_PASSWORD=portmap
DB_HOST=db
DB_PORT=5432

# Telegram (opcional — deixe vazio para desativar alertas)
TELEGRAM_BOT_TOKEN=seu_token_do_botfather
TELEGRAM_CHAT_ID=-1001234567890
```

> Se `TELEGRAM_BOT_TOKEN` ou `TELEGRAM_CHAT_ID` estiverem vazios, o sistema desativa os alertas silenciosamente.

---

## Integração com Telegram

### Configuração de Alertas por Sensor

Cada sensor pode ser configurado individualmente com a regra de disparo:

| Opção | Comportamento |
|-------|--------------|
| Imediatamente (1ª falha) | Alerta na 1ª varredura falha |
| Após 2 falhas consecutivas | Aguarda 2 varreduras falhas |
| Após 3 falhas consecutivas | Aguarda 3 varreduras falhas |
| Não notificar | Sem alertas para este sensor |

> O sistema garante envio único por queda — não há spam enquanto o dispositivo estiver offline. O alerta de recuperação é enviado assim que o sensor voltar ao estado Online.

### Configuração em Lote por Grupo

Na tela de edição de um grupo, é possível aplicar a mesma regra de alerta para todos os sensores do grupo de uma vez.

### Teste Manual

Em qualquer tela de detalhe de sensor, clique em **"Testar Telegram"** para validar a configuração do bot e do Chat ID.

---

## Observações sobre SNMP

- O sistema usa **pysnmp `hlapi.asyncio`** com `asyncio.new_event_loop()` em cada chamada para garantir compatibilidade com workers Celery (que podem ter loops fechados ou ausentes).
- OIDs de temperatura do MikroTik (`1.3.6.1.4.1.14988.1.1.3.10.0`) retornam o valor em **décimos de grau** — o sistema divide por 10 automaticamente para exibir em °C.
- OID de uptime (`1.3.6.1.2.1.1.3.0`) retorna em **centésimos de segundo (timeticks)** — o sistema converte para dias/horas/minutos.

---

## Testes Automatizados

O projeto conta com **40 testes automatizados** cobrindo:

- Autenticação por e-mail (case-insensitive) e por username
- Parser de lotes de portas com faixas e múltiplas entradas
- Cálculo de disponibilidade SLA
- Ciclo completo de alertas Telegram com mocks HTTP
- Batch update de configurações de grupo
- Parsing e armazenamento de `metric_value` por tipo de sensor

Para executar a suite completa:

```bash
# Localmente (SQLite)
$env:DB_ENGINE="django.db.backends.sqlite3"; poetry run python manage.py test

# Ou sem variável de ambiente se já configurado
poetry run python manage.py test
```

---

## Estrutura de Arquivos Relevantes

```
port_map/
├── monitor/
│   ├── models.py          # Device, MonitorTarget, MonitorLog, Group, AuditLog
│   ├── services.py        # PortCheckerService, PortParserService, SLAReportService
│   ├── views.py           # CBVs: Dashboard, AddDevice, TargetDetail, SLAReport...
│   ├── tasks.py           # Tarefas Celery: check_single_target, run_scheduled_checks
│   ├── forms.py           # DeviceForm, BulkAddForm, GroupForm
│   └── migrations/        # Histórico de migrações do banco
├── templates/
│   └── monitor/
│       ├── dashboard.html
│       ├── add_device.html  # Formulário com seleção de sensores por checkboxes
│       ├── target_detail.html # Gráfico com label/unidade dinâmica por sensor
│       └── ...
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```
