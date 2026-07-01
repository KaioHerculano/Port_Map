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

1. **Thin Views / Views Enxutas** — Lógica de negócio, banco de dados, cálculos e Celery encapsulados puramente em uma camada dedicada de Serviços (`services.py`).
2. **Reorganização de Templates (Namespacing)** — Todos os templates modularizados dentro de suas respectivas apps (`accounts/` e `monitor/`) utilizando a estrutura oficial de namespacing do Django.
3. **Prevenção de N+1** — Uso de `.select_related()` e `.prefetch_related()` estratégicos.
4. **Logging** — Utilização de `logging` nativo do Python em toda a aplicação.
5. **Type hints** — Tipagem estrita de parâmetros e retornos em todo o código.

---

## Stack Tecnológica

| Camada | Tecnologia |
|--------|-----------|
| Linguagem | Python >= 3.12 |
| Framework Web | Django >= 4.2 |
| Banco de Dados | PostgreSQL (Docker/Produção) / SQLite (Opcional local) |
| Gerenciador de dependências | Poetry |
| Fila de tarefas | Celery |
| Message Broker | Redis |
| SNMP | pysnmp (hlapi.asyncio) |
| RouterOS | routeros-api |
| Frontend | HTML5, Vanilla CSS, JS Puro, Chart.js, Bootstrap Icons |

---

## Automação e Qualidade de Código (`Makefile`)

O projeto inclui um `Makefile` configurado para automatizar tarefas comuns de desenvolvimento e garantir a qualidade do código:

| Comando | Descrição |
|---------|-----------|
| `make install` | Instala as dependências via Poetry e configura os hooks de `pre-commit` |
| `make format` | Formata o código automaticamente usando `black` e `isort` |
| `make lint` | Executa validações de estilo PEP 8 com `flake8` |
| `make test` | Roda a suíte de testes usando as configurações rápidas de teste (`app.test`) |
| `make test-coverage` | Executa os testes e gera relatórios de cobertura do código |
| `make pre-commit` | Roda os hooks do pre-commit manualmente em todos os arquivos |

---

## Qualidade e CI/CD

### Git Hooks (`pre-commit`)
Configurado para rodar automaticamente antes de cada commit. Executa verificações de segurança, sintaxe, formatação e ordenação de imports (AST, Black, Flake8 e Isort).

### Análise de Segurança & Código Morto
- **Bandit**: Executa varreduras de segurança estática no código Python.
- **Vulture**: Detecta código morto, funções e variáveis declaradas mas não utilizadas.

### Pipeline de CI (GitHub Actions)
O pipeline está configurado no arquivo `.github/workflows/ci.yml`. A cada Pull Request para as branches `main` e `master`, o GitHub Actions executa:
1. Validações de pre-commit.
2. Varreduras com Bandit e Vulture.
3. Inicialização dos containers PostgreSQL e Redis.
4. Execução dos testes e validação da cobertura mínima com `make test-coverage`.

---

## Testes Automatizados

O projeto possui **44 testes automatizados** rápidos e dinâmicos, que testam o comportamento real do sistema integrados ao banco de dados:

- **Configurações Rápidas (`app/test.py`)**:
  - Uso do `MD5PasswordHasher` para acelerar a criação de usuários nos testes.
  - Inativação de migrações (`DisableMigrations`) para criar o esquema em memória instantaneamente.
  - Silenciamento global de notificações reais de Telegram durante as execuções.
  - Redirecionamento dinâmico do PostgreSQL: conecta-se ao container `db` ou recai para `localhost` no host de desenvolvimento.
- **Faker integration**: Substituição de strings chumbadas e IPs estáticos por dados dinâmicos e realistas gerados por `Faker()`.
- **Desempenho**: Suíte completa executada localmente em apenas **~2.8 segundos** (contra mais de 11 segundos no modelo convencional).

Para rodar os testes:
```bash
# Executar testes
make test

# Executar testes com relatório de cobertura
make test-coverage
```

---

## Instalação Local

### Pré-requisitos

1. **Python** >= 3.12
2. **Poetry**
3. **PostgreSQL** e **Redis Server** iniciados (pode subir apenas os serviços de dados usando docker-compose: `docker compose up -d db redis`)

### Configuração Passo a Passo

```bash
# 1. Instalar dependências e hooks do git
make install

# 2. Aplicar migrações
poetry run python manage.py migrate

# 3. Criar superusuário
poetry run python manage.py createsuperuser

# 4. Rodar o servidor de desenvolvimento
poetry run python manage.py runserver
```

---

## Executando com Docker Compose

O projeto está preparado para rodar em containers com Django, Celery Worker, Celery Beat, Redis e PostgreSQL.

### Subir os containers

```bash
docker compose up --build -d
```

Acesse: **http://localhost:8003/**
