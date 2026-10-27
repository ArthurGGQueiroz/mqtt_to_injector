# Relatório de deploy — mqtt_to_injector na AWS

**Data:** 27/10/2026
**Commit em produção:** `9fdbfce` (branch `main`)
**Status:** em produção e testado de ponta a ponta (sensor → Mosquitto → injector → `irrigacao_db`)

> Identificadores da infraestrutura (`<ELASTIC_IP>`, `<INSTANCE_ID>`, `<SG_EC2>`, `<SG_RDS>`, `<ENDPOINT_RDS>`...) foram omitidos por o repositório ser público. Os valores reais estão no relatório interno e no Parameter Store `/mqtt-injector/env`.

---

## 1. Resumo

O `mqtt_to_injector` (broker Mosquitto + injetor Python) está rodando numa nova instância EC2 e grava a telemetria dos sensores na tabela `telemetria` do banco `irrigacao_db` (RDS `irrigacao-db`). O formato gravado é o que a `api_irrigacao` (RigTech_APIs) espera para os endpoints `/maquinas/status`, `/maquinas/{id}`, `/alertas` e o stream SSE `/maquinas/{id}/eventos`.

```
Sensor (ESP32) ──MQTT 1883──▶ Mosquitto (EC2) ──▶ injector (EC2) ──5432/SSL──▶ RDS irrigacao_db
                                                                                  │
                                                         api_irrigacao ◀──────────┘ (leitura + pg_notify)
```

---

## 2. O que foi feito

### 2.1 Correções no código (`main.py`)

| Problema encontrado | Correção |
|---|---|
| O id do equipamento (tópico) era gravado em `telemetria.id`, que é `BIGSERIAL`. A partir da 2ª leitura do mesmo equipamento o banco retornava *duplicate key* | O `id` passou a ser gerado pelo banco. O último trecho do tópico agora é o `maquina.id` |
| O `id_operacao` vinha do payload do firmware | O injector busca no banco a operação da máquina que estava aberta no instante da leitura (`inicio <= data_hora` e `fim IS NULL OR fim >= data_hora`). O `id_operacao` do payload é ignorado |
| Leitura sem operação causava erro de FK | A leitura é descartada e registrada no log como `WARNING` (mesmo comportamento do simulador da API) |
| Uma conexão nova por mensagem, sem fechar em caso de erro | Uma conexão reaproveitada entre as mensagens, que reconecta sozinha se cair (RDS reiniciou, rede) |
| `leitura_campo` ficava NULL | Passa a ser `false` por padrão (leitura automática). A API ignora `leitura_campo = true` |
| — | Conexão com o RDS via SSL (`DB_SSLMODE=require`) |

As correções já existentes foram mantidas: `timestamp` em ms do dispositivo com fallback para `NOW()`, e tratamento de JSON inválido.

### 2.2 Outros arquivos do repositório

- `requirements.txt`: novo. O `Dockerfile` passou a instalar as dependências por ele.
- `docker-compose.yml`: rotação de logs (10 MB × 3 arquivos por container).
- `.env.example`: aponta para o RDS `irrigacao-db` / `irrigacao_db` com o usuário `mqtt_injector`.
- `exemplo_payload.json`: inclui `timestamp`. O campo `id_operacao` foi removido.
- `sql/criar_role_mqtt_injector.sql`: novo. Script que cria a role do banco; pode ser executado mais de uma vez sem problema.
- `README.md`: reescrito com o fluxo real (tabela `telemetria`, tópicos, deploy na EC2, troubleshooting).

### 2.3 Banco de dados (`irrigacao_db`)

Foi criada a role **`mqtt_injector`**, com o mínimo de privilégio:

| Permissão | Objeto |
|---|---|
| `CONNECT` | database `irrigacao_db` |
| `USAGE` | schema `public` |
| `SELECT` | `operacao`, `maquina` |
| `INSERT` | `telemetria` |
| `USAGE` | sequence `telemetria_id_seq` |

Testes feitos com essa role: o INSERT funciona e o trigger `pg_notify` dispara. `DELETE`, `UPDATE`, `CREATE` e `SELECT` em `telemetria`/`projeto` são negados.
**Atenção:** como a role não lê `telemetria`, `INSERT ... RETURNING` não é permitido.

O schema existente não foi alterado.

### 2.4 Infraestrutura AWS (`us-east-1`)

| Recurso | Identificador | Detalhes |
|---|---|---|
| EC2 | `<INSTANCE_ID>` (`mqtt-injector`) | t4g.micro (arm64), Amazon Linux 2023, us-east-1b, disco gp3 20 GB criptografado, IMDSv2 obrigatório, perfil de instância com SSM (acesso via SSM) |
| Elastic IP | **`<ELASTIC_IP>`** | `<EIP_ALLOCATION>` |
| Security Group da EC2 | `<SG_EC2>` (`mqtt-injector-sg`) | Entrada: TCP 22 e TCP 1883 **somente de <IP_OPERADOR>/32** (provisório) |
| Regra no SG do RDS | `<RULE_ID>` em `<SG_RDS>` | TCP 5432 a partir de `mqtt-injector-sg` |
| Parameter Store | `/mqtt-injector/env` (SecureString) | Conteúdo do `.env` de produção (credenciais MQTT e banco) |

Software na EC2: Docker 25.0.14 e Docker Compose v2.29.7. Código em `/home/ec2-user/mqtt_to_injector`.

### 2.5 Deploy e testes

- Containers `mqtt_broker` (Mosquitto 2.1.2) e `mqtt_injector` estão no ar, com `restart: unless-stopped`.
- Usuário do broker: definido em `MQTT_USER`. A senha fica no Parameter Store e no `.env`.
- **Teste de ponta a ponta:** mensagens publicadas de fora da AWS no Elastic IP.
  - `teste/telemetria/2` → `Dados de telemetria inseridos no RDS com sucesso! (máquina: 2, operação: 2)`. Linha conferida no banco e depois removida.
  - `teste/telemetria/999` → `Nenhuma operação aberta para a máquina 999: leitura foi descartada`.

---

## 3. Configuração dos sensores

### 3.1 Conexão MQTT

| Parâmetro | Valor |
|---|---|
| Host | `<ELASTIC_IP>` |
| Porta | `1883` (sem TLS) |
| Usuário | `MQTT_USER` do `.env` (ou um usuário próprio por sensor, ver 4.2) |
| Senha | `MQTT_PASSWORD` no Parameter Store `/mqtt-injector/env` |
| Client ID | Único por sensor (ex.: `japungu-2`). Dois sensores com o mesmo ID se derrubam |
| QoS recomendado | 1 |
| Keep-alive | 60 s |

> Para obter a senha: `aws ssm get-parameter --name /mqtt-injector/env --with-decryption --query Parameter.Value --output text`
> (no Git Bash, prefixe com `MSYS_NO_PATHCONV=1`).

### 3.2 Tópico

```
<ecode>/telemetria/<maquina.id>
```

- `<ecode>`: código livre da fazenda/cliente (ex.: `japungu`). Não é gravado no banco.
- `<maquina.id>`: **inteiro**, igual ao `id` da tabela `maquina` no `irrigacao_db`. Exemplo: `japungu/telemetria/2`.

Antes de instalar um sensor, a máquina correspondente precisa estar cadastrada em `maquina`. Para conferir o id:

```sql
SELECT m.id, m.nome, m.nome_casa_bomba, p.nome AS projeto
FROM maquina m JOIN projeto p ON p.id = m.id_projeto;
```

### 3.3 Payload (JSON, UTF-8)

```json
{
  "timestamp": 1793112000000,
  "paf": 12.5, "pdf": 10.0, "pav": 11.2, "pdv": 9.8,
  "vazao": 45.2, "horimetro": 1250.5,
  "ce_1": true, "ce_2": false, "...": "...", "ce_9": false,
  "ch_1": true, "ch_2": true,  "...": "...", "ch_9": false
}
```

| Campo | Tipo | Unidade / significado |
|---|---|---|
| `timestamp` | inteiro | Hora da leitura em **ms desde epoch (UTC)**. Se ausente ou anterior a 2020 (relógio não sincronizado), usa a hora de chegada |
| `paf`, `pdf` | número | Pressão antes/depois do filtro (mca). Perda no filtro = `paf - pdf` |
| `pav`, `pdv` | número | Pressão antes/depois da válvula (mca) |
| `vazao` | número | Vazão (m³/h). **`0` = vazão zero real** (pode gerar alerta) |
| `horimetro` | número | Horímetro acumulado (h) |
| `ce_1`…`ce_9` | booleano | Comando elétrico do setor N (`bloco.numero = N`) |
| `ch_1`…`ch_9` | booleano | Comando hidráulico do setor N |
| `calibracao_sensor` | objeto (opcional) | Ganho/offset atuais, gravado como `jsonb` |
| `leitura_campo` | booleano (opcional) | `true` = leitura manual (a API ignora). Padrão `false` |

**Regras importantes:**
- Sensor sem leitura → **omitir o campo** ou mandar `null`. Nunca mandar `0` no lugar, porque o zero é tratado como valor real.
- Leituras guardadas no SD durante falta de rede devem ser reenviadas com o `timestamp` original. O injector associa cada uma à operação que estava aberta naquele instante.
- **A máquina precisa ter uma operação aberta** (`operacao.fim IS NULL`) no horário da leitura. Caso contrário, a leitura é descartada. A abertura/fechamento de operações é feita pela API/operador, não pelo sensor.
- Após cada leitura gravada, a API recebe um `pg_notify` e atualiza o painel em tempo real. Uma máquina fica `sem_sinal` se passar mais de 15 min sem leitura.

### 3.4 Teste rápido a partir de um computador liberado no SG

```bash
mosquitto_pub -h <ELASTIC_IP> -p 1883 -u <usuario> -P '<senha>' -q 1 \
  -t teste/telemetria/2 -f exemplo_payload.json
```

> O `exemplo_payload.json` tem `timestamp` fixo: retire o campo para usar a hora atual.

---

## 4. Próximos passos

### 4.1 Obrigatório antes de ligar os sensores
1. **Liberar os IPs dos sensores** na porta 1883 do `<SG_EC2>`:
   ```bash
   aws ec2 authorize-security-group-ingress --group-id <SG_EC2> \
     --ip-permissions 'IpProtocol=tcp,FromPort=1883,ToPort=1883,IpRanges=[{CidrIp=<IP_DO_SENSOR>/32,Description="sensor <nome>"}]'
   ```
   Se os sensores usam rede móvel com IP dinâmico, a liberação por IP não funciona. Nesse caso, ver item 4.2.
2. **Conferir o mapeamento** sensor → `maquina.id` e garantir que cada máquina tem operação aberta quando for irrigar.

### 4.2 Recomendado (segurança)
3. **TLS no MQTT (porta 8883)**: hoje usuário, senha e dados trafegam em texto claro. Configurar certificado no Mosquitto (ex.: Let's Encrypt com um domínio apontando para o Elastic IP), liberar 8883 e fechar 1883. Com TLS fica aceitável abrir a porta para `0.0.0.0/0`, o que resolve o caso de IP dinâmico.
4. **Usuário MQTT por sensor** (`mosquitto_passwd -b /mosquitto/config/passwd <usuario> <senha>`) + ACL restringindo cada um ao próprio tópico. Com isso, um sensor comprometido não publica por outra máquina.

> Pontos de segurança da infraestrutura compartilhada (RDS e SGs) estão no relatório interno, fora do repositório.

### 4.3 Operação
5. **Monitoramento**: alarme no CloudWatch para status check da EC2 e, se possível, para "nenhuma telemetria nova em X minutos".
6. **Leituras descartadas** ficam só no log (`docker compose logs injector | grep "Nenhuma operação"`). Se for preciso não perder nenhuma, considerar uma tabela de quarentena (exige alteração de schema e permissão).
7. **Fuso/relógio do ESP32**: garantir NTP no firmware para o `timestamp` ser confiável.

---

## 5. Operação do dia a dia

Acesso à EC2 (sem chave SSH, via SSM):
```bash
aws ssm start-session --target <INSTANCE_ID>
cd /home/ec2-user/mqtt_to_injector
```

| Ação | Comando (na EC2) |
|---|---|
| Ver status | `docker compose ps` |
| Logs do injector | `docker compose logs -f injector` |
| Atualizar código | `sudo -u ec2-user git pull && docker compose up -d --build` |
| Reiniciar | `docker compose restart` |
| Adicionar usuário MQTT | `docker compose exec mosquitto mosquitto_passwd -b /mosquitto/config/passwd <user> <senha>` e depois `docker compose restart mosquitto` |
| Atualizar `.env` | Editar o parâmetro `/mqtt-injector/env` e depois `aws ssm get-parameter --name /mqtt-injector/env --with-decryption --query Parameter.Value --output text > .env && docker compose up -d` |

Atualização remota sem abrir sessão (da máquina local):
```bash
aws ssm send-command --instance-ids <INSTANCE_ID> --document-name AWS-RunShellScript \
  --parameters 'commands=["cd /home/ec2-user/mqtt_to_injector && sudo -u ec2-user git pull --ff-only && docker compose up -d --build"]'
```

Custo estimado: t4g.micro (~US$ 6/mês) + EBS 20 GB (~US$ 1,60/mês) + IPv4 público/Elastic IP (~US$ 3,60/mês).
