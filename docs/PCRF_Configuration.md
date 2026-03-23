# Configuração do PCRF no PyHSS

Este documento descreve como configurar o módulo PCRF (Policy and Charging Rules Function) no PyHSS. O PCRF é responsável por definir políticas de QoS (Qualidade de Serviço) e regras de tarifação via interface Gx.

## 1. Fluxo de Configuração e Pré-requisitos

Para que as configurações do PCRF funcionem, os seguintes módulos devem estar previamente configurados:

1.  **HSS (Base de Dados):** O banco de dados deve estar inicializado e o serviço `hssService.py` rodando.
2.  **Diameter (Gx):** A interface Gx deve estar operacional para que o PGW/PCEF possa solicitar políticas ao PCRF.
3.  **APN (Access Point Name):** Um APN deve estar definido, pois as regras de tarifação são associadas a ele.
4.  **Subscriber:** O assinante deve estar vinculado ao APN que possui as regras.

### Ordem de Execução Recomendada:
1.  **Definir TFTs (Traffic Flow Templates):** Filtros que identificam o tráfego.
2.  **Definir Charging Rules:** Regras que aplicam QoS e Rating Groups ao tráfego filtrado pelos TFTs.
3.  **Associar ao APN:** Vincular as regras criadas ao APN desejado.

---

## 2. Passo a Passo com Exemplos Práticos

Utilizaremos a API REST do PyHSS para as configurações.

### Passo 1: Definir TFTs (Traffic Flow Templates)
Os TFTs usam `IPFilterRules` (RFC 6733) para identificar pacotes. O PyHSS suporta o uso do placeholder `{{ UE_IP }}` que é substituído dinamicamente pelo IP real alocado ao usuário.

**Exemplo: Identificar tráfego para um servidor de Streaming (IP: 200.1.2.3, Porta: 443)**

```json
// PUT /tft/
{
    "tft_group_id": 10,
    "tft_string": "permit out 6 from 200.1.2.3 443 to {{ UE_IP }} 1-65535",
    "direction": 1 // Downlink
}
```

- **Placeholder `{{ UE_IP }}`:** Altamente recomendado para regras que precisam ser específicas ao IP do assinante.
- **Parâmetro `tft_group_id`:** Agrupa múltiplos filtros sob uma mesma regra. Escolhido `10` para organizar serviços de vídeo.
- **Parâmetro `direction`:** `1` (Downlink) pois o tráfego vem do servidor para o usuário. Outras opções: `2` (Uplink), `3` (Bidirecional).

### Passo 2: Definir Charging Rule (Regra de Tarifação)
Aqui vinculamos o TFT a uma política de QoS e Tarifação.

**Exemplo: Plano "Streaming Ilimitado" com Alta Prioridade**

```json
// PUT /charging_rule/
{
    "rule_name": "Streaming_Premium",
    "qci": 2, 
    "arp_priority": 3,
    "arp_preemption_capability": true,
    "arp_preemption_vulnerability": false,
    "mbr_dl": 50000000, // 50 Mbps
    "mbr_ul": 10000000, // 10 Mbps
    "gbr_dl": 10000000, // 10 Mbps (Garantido)
    "gbr_ul": 2000000,  // 2 Mbps (Garantido)
    "tft_group_id": 10,
    "precedence": 50,
    "rating_group": 5000
}
```

### Passo 3: Associar ao APN
Adicionamos o ID da regra criada à lista de regras do APN.

```json
// PUT /apn/
{
    "apn": "internet",
    "charging_rule_list": "1,10" // IDs das regras separados por vírgula. Ex: ID 10 é a nossa regra.
    // ... outros campos do APN
}
```

---

## 3. Endpoints da API para Gestão

O PyHSS utiliza os seguintes endpoints para gerenciar o PCRF:

- `GET /tft/`: Lista todos os TFTs definidos.
- `PUT /tft/`: Cria ou atualiza um TFT.
- `DELETE /tft/{tft_id}`: Remove um TFT.
- `GET /charging_rule/`: Lista todas as regras de tarifação.
- `PUT /charging_rule/`: Cria ou atualiza uma regra.
- `DELETE /charging_rule/{charging_rule_id}`: Remove uma regra.
- `GET /apn/`: Lista APNs.
- `PUT /apn/`: Cria ou atualiza APN (onde se associa as regras).

---

## 4. Explicação dos Parâmetros de QoS e Tarifação

| Parâmetro | Valor Escolhido | Por que este valor? | Outras Opções | Cenários de Uso |
| :--- | :--- | :--- | :--- | :--- |
| **QCI** (QoS Class Identifier) | `2` | Para tráfego de vídeo/voz em tempo real (GBR). | `9` (Default), `5` (IMS Signaling), `1` (Voz GBR). | `2` ajuda em streaming; `9` é para internet comum "best-effort". |
| **ARP Priority** | `3` | Alta prioridade para garantir que a sessão não seja derrubada em congestionamento. | `1` (Máxima) a `15` (Mínima). | Prioridade baixa (`12-15`) para usuários "Free"; alta para "Premium". |
| **MBR** (Max Bit Rate) | `50000000` | Limite máximo de 50Mbps para evitar que um usuário consuma toda a banda. | Depende do plano comercial. | Útil para "Traffic Shaping" em planos limitados. |
| **GBR** (Guaranteed Bit Rate) | `10000000` | Garante 10Mbps mínimos para evitar "buffering" no vídeo. | `0` para tráfego não-GBR. | Essencial para VoLTE ou Streaming Premium. |
| **Rating Group** | `5000` | Identificador para o OCS cobrar de forma diferenciada (ex: Zero Rating). | Qualquer inteiro. | `5000` pode ser "Free-Video" no sistema de cobrança. |
| **Precedence** | `50` | Define a ordem de aplicação. Menor valor = maior prioridade. | `0` a `255`. | Use `10` para regras específicas e `250` para regras genéricas. |

## 4. Cenários de Exemplo

### Cenário A: Zero Rating para Redes Sociais
- **Objetivo:** Permitir acesso ao Facebook sem descontar da franquia.
- **Configuração:** TFT com IPs do Facebook, `rating_group` configurado no OCS como "isento", `qci: 9` (Best effort).

### Cenário B: Serviço de Missão Crítica (MCPTT)
- **Objetivo:** Garantir comunicação imediata e ininterrupta.
- **Configuração:** `qci: 65` (ou `1`), `arp_priority: 1`, `arp_preemption_capability: true`. Isso derrubará outros usuários se necessário para dar lugar a este tráfego.

---
*Documento gerado para auxiliar na configuração do módulo PCRF do PyHSS.*
