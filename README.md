# PortMap Monitor 🌐📡

O **PortMap Monitor** é um sistema profissional e moderno desenvolvido em Django para o monitoramento assíncrono e em tempo real de portas TCP ativas em múltiplos endereços IP ou hostnames. Ele foi projetado para substituir scripts manuais temporários por uma interface robusta, de alta performance e visualmente impressionante.

---

## 🚀 Funcionalidades Principais

*   **Autenticação Flexível (E-mail ou Usuário)**: A tela de login permite a autenticação de forma transparente tanto utilizando o nome de usuário (username) quanto o e-mail cadastrado.
*   **Cadastro Altamente Prático de Portas (Lote/Bulk)**:
    *   **Cadastro Individual**: Nome, IP/Hostname e porta única.
    *   **Cadastro em Lote (Bulk)**: Área de texto para colar múltiplos registros de uma vez.
    *   **Faixas de Portas (Ranges)**: Cadastre como `45.174.193.10:40001-40048` e o sistema criará e monitorará todos os 48 alvos sequencialmente em background.
    *   **Portas por Vírgula**: Suporte a listagens curtas como `192.168.1.1:80,443,8080`.
    *   **Identificadores (Labels)**: Defina apelidos amigáveis no fim de cada linha entre colchetes ou parênteses, ex: `10.0.0.5:22 [Servidor SSH]`.
*   **Varreduras Assíncronas com Celery & Redis**: Os testes de portas não travam o servidor web. Eles são distribuídos para Workers do Celery que testam as conexões de forma concorrente utilizando Sockets TCP puros de baixo nível com timeout controlado.
*   **Visualização de Dados (Dashboard Premium)**:
    *   Painel estatístico com contadores de alvos online (portas abertas), offline (portas fechadas), inativos e totais.
    *   Filtros dinâmicos rápidos por status e busca textual integrada.
    *   Gráficos dinâmicos interativos (Chart.js) exibindo o histórico de variação de latência (ms) nas últimas 50 conexões.
    *   Tabela com controle AJAX instantâneo para ativar/desativar monitoramentos individualmente e excluir alvos sem necessidade de dar refresh na página.

---

## 🛠️ Arquitetura de Software e Boas Práticas

O projeto foi escrito sob rígidos princípios de qualidade de código (**SOLID, DRY, KISS e Clean Code**):

1.  **Class-Based Views (CBVs) Exclusivas**: Nenhuma view funcional (FBV) foi utilizada. Todo o fluxo web herda das classes genéricas otimizadas do Django (ex: `generic.ListView`, `generic.TemplateView`, `generic.DetailView`, etc.).
2.  **Isolamento de Regras de Negócio (Services Layer)**: As views não possuem regras de banco ou parsing. Toda a lógica foi extraída para o arquivo `monitor/services.py`:
    *   `PortParserService`: Faz a higienização, validação de limites de portas (1-65535) e a expansão de lotes/ranges em transações atômicas seguras no banco de dados.
    *   `PortCheckerService`: Responsável por conduzir a conexão TCP via Socket, medir a latência com precisão de milissegundos e persistir o log final.
3.  **Prevenção de Consultas N+1**: Utilização estratégica de `.select_related()` e `.prefetch_related()` para garantir carregamento eficiente de tabelas e histórico de monitoramento em uma única consulta SQL.
4.  **Logging**: Todo o rastreamento operacional e tratamento de erros do sistema utiliza o módulo `logging` integrado ao invés de funções `print()`.
5.  **Tipagem Estrita**: Funções e métodos implementam `type hints` em Python para facilitar manutenção futura e autocompletes de IDEs.

---

## 📦 Stack Tecnológica

*   **Linguagem**: Python >= 3.12
*   **Framework Web**: Django >= 4.2 (Configurado em PT-BR e fuso horário de São Paulo)
*   **Banco de Dados**: SQLite3 (Pronto para migrar para PostgreSQL/Docker)
*   **Gerenciador de Dependências**: Poetry
*   **Fila de Tarefas / Asincronismo**: Celery
*   **Message Broker (Mensageria)**: Redis
*   **Gráficos / Frontend**: HTML5, Vanilla CSS (Glassmorphic Dark Theme), JS Puro, Chart.js

---

## ⚙️ Preparação do Ambiente e Instalação

### Pré-requisitos
1.  **Python** instalado (versão 3.12 ou superior).
2.  **Poetry** instalado globalmente na máquina (`pip install poetry`).
3.  **Redis Server** rodando localmente (porta padrão `6379`).

### Passo 1: Clonar o Repositório e Instalar Dependências
Navegue até a pasta do projeto e instale todas as dependências declaradas no Poetry Lockfile:
```bash
poetry install
```

### Passo 2: Executar as Migrações do Banco de Dados
Gere a estrutura inicial de tabelas do SQLite (incluindo as tabelas de controle de tarefas do Celery Beat):
```bash
poetry run python manage.py migrate
```

### Passo 3: Criar um Usuário Administrador
Para acessar o painel pela primeira vez, utilize o superusuário de testes já configurado:
*   **Usuário**: `admin` (ou e-mail `admin@example.com`)
*   **Senha**: `admin123`

Se preferir criar uma nova credencial personalizada, execute:
```bash
poetry run python manage.py createsuperuser
```

---

## ⚡ Como Iniciar o Sistema para Testes

O PortMap necessita de três processos principais rodando simultaneamente:

### 1. Iniciar o Servidor Web Django
Inicia a interface de gerenciamento:
```bash
poetry run python manage.py runserver
```
Acesse no seu navegador: **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**

### 2. Iniciar o Celery Worker (Conexões de Teste)
Processa a fila de varredura assíncrona das portas:
```bash
poetry run celery -A port_monitor worker --loglevel=info -P threads
```
*(Nota: A flag `-P threads` é altamente recomendada no ambiente Windows para garantir estabilidade multithread do Celery).*

### 3. Iniciar o Celery Beat (Varredura Automática Agendada)
Caso deseje disparar coletas automatizadas em períodos cronometrados:
```bash
poetry run celery -A port_monitor beat --loglevel=info
```

---

## 🐳 Executando com Docker e Docker Compose

O projeto está totalmente preparado para ser executado em containers isolados contendo Django, Celery Worker, Celery Beat, Redis e PostgreSQL.

### Passo 1: Construir e iniciar os containers
Na raiz do projeto (onde está o arquivo `docker-compose.yml`), execute o comando:
```bash
docker compose up --build -d
```

Este comando irá:
1. Compilar a imagem Docker do Django baseada em `python:3.12-slim` utilizando o Poetry.
2. Iniciar o banco de dados PostgreSQL e aguardar seu estado saudável (`service_healthy`).
3. Iniciar o Redis.
4. Aplicar as migrações automáticas do banco.
5. Iniciar os serviços Web, Celery Worker e Celery Beat conectados em rede interna.

O painel de controle estará disponível no navegador em **[http://localhost:8000/](http://localhost:8000/)**.

### Passo 2: Criar um Superuser dentro do container
Para criar uma conta administrativa no container em execução, execute:
```bash
docker exec -it port_map_web poetry run python manage.py createsuperuser
```

### Passo 3: Parar os serviços
Para derrubar e encerrar todos os containers mantendo os volumes de dados persistidos no PostgreSQL, execute:
```bash
docker compose down
```

---

## 🧪 Execução de Testes Automatizados

O sistema conta com 16 testes automatizados que cobrem fluxos cruciais como criação de usuários com restrições de e-mail único, login por e-mail insensível a maiúsculas, cálculo matemático correto da latência de logs em 24h e parser robusto de faixas sequenciais de portas.

Para rodar a suite de testes completa, execute:
```bash
poetry run python manage.py test
```
