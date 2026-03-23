# Integração PyHSS (PCRF) e SigScale OCS

Este documento descreve como integrar o módulo PCRF do PyHSS com o SigScale OCS (Online Charging System). Essa integração permite o controle de políticas em tempo real aliado à tarifação dinâmica de dados e serviços.

## 1. Arquitetura da Integração

A integração entre o PyHSS e o SigScale OCS ocorre principalmente de duas formas:

1.  **Via Interface Diameter (Fluxo de Dados):**
    - O **PCRF (PyHSS)** define as regras de QoS e fornece o **Rating Group** para o PGW/PCEF via interface **Gx**.
    - O **PGW/PCEF** utiliza esse **Rating Group** para solicitar autorização de crédito ao **SigScale OCS** via interface **Gy (Ro)**.
2.  **Via REST Webhooks (Notificações de Sessão):**
    - O PyHSS envia notificações REST diretamente para o SigScale OCS quando uma sessão Gx é iniciada ou encerrada, permitindo que o OCS sincronize o estado da sessão.

---

## 2. Configuração no PyHSS

A configuração principal reside no arquivo `config.yaml`.

### Habilitando Notificações para o OCS
O PyHSS possui um módulo de notificações específico para o OCS que dispara webhooks em eventos de Credit Control.

```yaml
### Notificações para o OCS em Requests de Credit Control
ocs:
  enabled: True
  endpoints:
    - 'http://<ip-sigscale-ocs>:8080/ocs/v1/session_notification'
```

- **Parâmetro `enabled`:** Deve ser `True` para que o PyHSS envie notificações. Se `False`, o PCRF funcionará de forma isolada, sem informar o OCS sobre novas sessões.
- **Parâmetro `endpoints`:** Lista de URLs do SigScale OCS que receberão os eventos. 
    - **Cenário de Uso:** Use múltiplos endpoints para alta disponibilidade ou para alimentar sistemas de analytics em paralelo ao OCS.

### Configuração de Rating Groups no PCRF
Como vimos na configuração do PCRF, o `rating_group` é a "chave" que une os dois sistemas.

**Exemplo de Charging Rule no PyHSS:**
```json
{
    "rule_name": "Video_Premium",
    "rating_group": 100,
    "qci": 2
}
```

- **Por que o Rating Group 100?** Este ID deve coincidir exatamente com o ID do produto ou característica de tarifação configurada no SigScale OCS.
- **Outras Opções:** Você pode ter diferentes Rating Groups para diferentes serviços (ex: 10 para Voz, 20 para Redes Sociais, 100 para Internet Geral).

---

## 3. Configuração no SigScale OCS (Resumo)

No lado do SigScale OCS, é necessário que o assinante possua um "Product Offering" que corresponda ao Rating Group enviado pelo PCRF.

1.  **Product Offering:** Criar uma oferta com o ID ou característica que mapeie para o `rating_group: 100`.
2.  **Balance Management:** O assinante deve ter saldo (dinheiro ou bytes) associado a esse produto.
3.  **Diameter Client:** O SigScale deve estar configurado para aceitar conexões Gy/Ro do PGW que está sendo controlado pelo PyHSS.

---

## 4. Fluxo de Operação (Passo a Passo)

1.  **Anexação do UE:** O usuário se conecta à rede.
2.  **Sessão Gx (PCRF):** O PGW envia um CCR-I para o **PyHSS**.
3.  **Resposta do PCRF:** O PyHSS responde com as regras (ex: `Video_Premium`) e o `rating_group: 100`.
4.  **Notificação REST (Opcional):** O PyHSS envia um POST para o SigScale informando que o IMSI X iniciou uma sessão com o IP Y.
5.  **Sessão Gy (OCS):** O PGW envia um CCR-I (Gy) para o **SigScale OCS** solicitando quota para o `rating_group: 100`.
6.  **Autorização de Crédito:** O SigScale verifica o saldo e autoriza (CCA-I) o tráfego.

---

## 5. Explicação dos Parâmetros de Integração

| Parâmetro | Opção Escolhida | Por que? | Outras Opções | Quando usar? |
| :--- | :--- | :--- | :--- | :--- |
| **OCS Webhook** | `Enabled: True` | Garante que o OCS saiba o IP e a sessão do usuário em tempo real. | `False` | Use `False` se o OCS for puramente baseado em Diameter e não precisar de notificações REST. |
| **Rating Group** | `Inteiro (ex: 100)` | Padronização 3GPP para identificar o serviço no OCS. | Qualquer ID numérico. | Deve ser único por tipo de tarifação (ex: 0 para Free, >0 para Pago). |
| **Precedence** | `Alta (ex: 10)` | Garante que a regra com tarifação específica seja avaliada antes da regra default. | `0-255` | Use valores baixos para serviços específicos (Zero Rating) e valores altos para Internet Geral. |

---

## 6. Cenários de Erro e Soluções

- **Sintoma:** Usuário navega mas o saldo no SigScale não diminui.
    - **Causa:** O `rating_group` no PyHSS PCRF não coincide com o configurado no SigScale.
    - **Solução:** Verifique se o ID em `charging_rule` no PyHSS é o mesmo que o `Service Identifier` ou `Rating Group` no SigScale.
- **Sintoma:** PGW rejeita a sessão.
    - **Causa:** O SigScale OCS está fora do ar ou recusando a conexão Gy.
    - **Solução:** Verifique a conectividade Diameter entre o PGW e o SigScale.

---
*Documento gerado para auxiliar na integração entre PyHSS PCRF e SigScale OCS.*
