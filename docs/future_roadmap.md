# Port_Map - Roadmap e Plano de Evolucao

Este documento detalha as proximas fases de desenvolvimento para o Port_Map, com base na arquitetura de multi-tenancy ja implementada. O objetivo e transformar o sistema em uma plataforma SaaS completa e permitir o monitoramento de redes locais (intranets) atraves de agentes instalaveis.

## Fase 1: Gestao de Usuarios e Empresa

Como a estrutura de multi-tenancy (Company vinculada a User, Target e Monitor) ja foi concluida e os filtros de seguranca ja estao operacionais, os proximos passos focam na usabilidade e colaboracao dentro das empresas.

*   **Fluxo de Registro e Onboarding:**
    *   Automatizar a criacao de uma "Company" quando um novo usuario se cadastrar no sistema.
    *   Vincular esse usuario inicial como o "Dono" (Owner) da organizacao recem-criada.

*   **Sistema de Convites:**
    *   Criar uma interface para que o Dono ou Administradores da empresa possam convidar outros usuarios para a mesma organizacao.
    *   O convite deve enviar um link de acesso ou associar a conta automaticamente.

*   **Role-Based Access Control (RBAC):**
    *   Implementar niveis de permissao dentro da Company (ex: Owner, Admin, Member, Viewer).
    *   Garantir que usuarios com nivel de visualizacao nao possam alterar configuracoes ou excluir monitores.

## Fase 2: Arquitetura do Agente Local (Sonda)

Devido as limitacoes de um servidor hospedado na nuvem, o Port_Map nao consegue monitorar equipamentos em redes locais atras de firewalls e NAT. Para resolver isso, sera implementada uma arquitetura de Agente (ou Sonda).

### Como funcionara o Agente

O Agente sera um programa leve executado na rede local do cliente. Ele se comunicara ativamente com o servidor web (Outbound) para buscar tarefas e retornar resultados.

1.  **Tokens de Autenticacao:**
    *   O painel web permitira a geracao de Tokens de API (API Keys) vinculados a uma especifica Company e, potencialmente, a um agente especifico.
2.  **Comunicacao Outbound:**
    *   O agente local fara requisicoes HTTPS (ou via WebSockets) para a API do Port_Map na nuvem, eliminando a necessidade do cliente abrir portas de entrada em seu firewall.
3.  **Gestao de Tarefas:**
    *   O agente consultara a API (pull) buscando por rotinas de monitoramento atribuidas a ele (ex: "Testar porta 80 do IP 192.168.0.10").
4.  **Execucao e Feedback:**
    *   O agente executara os testes na rede local.
    *   Em seguida, enviara os resultados (status, latencia, logs) de volta para a API do Port_Map.

### Modificacoes Necessarias na Web

Para suportar os agentes locais, o backend web do Port_Map precisara das seguintes atualizacoes:

*   **Tabela de Agentes/Tokens:** Criar modelos no banco de dados para registrar Agentes, vincula-los a uma Company e armazenar seus Tokens de acesso seguro.
*   **Flag de Origem do Monitor:** Adicionar uma propriedade aos modelos `Monitor` ou `Target` indicando se a execucao deve ocorrer a partir da nuvem ou a partir de um Agente especifico.
*   **API para Agentes:** Desenvolver endpoints RESTful seguros (usando o Token gerado) para que os agentes possam consumir a lista de tarefas pendentes e enviar os resultados das verificacoes.

## Consideracoes Futuras (SaaS Publico)

Quando a plataforma estiver madura o suficiente e houver o desejo de abrir para o publico geral (comercializacao):

*   **Planos e Limites (Quotas):** Definicao de tiers de assinatura, limitando a quantidade de Targets, Monitors ou Agentes com base no plano.
*   **Integracao de Pagamento:** Conexao com gateways (como Stripe) para faturamento automatizado.
*   **Logs de Auditoria:** Rastreamento de acoes criticas para organizacoes maiores.
