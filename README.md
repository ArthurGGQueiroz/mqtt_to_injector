# IoT MQTT to AWS RDS Injector

Sistema leve em containers Docker projetado para receber telemetria de dispositivos IoT via protocolo MQTT, processar as mensagens em tempo real e persistir os dados de forma centralizada em um banco de dados gerenciado na nuvem (**AWS RDS**).

---

## :office: Arquitetura do Sistema

O fluxo de dados ocorre da seguinte forma:
1. **Sensores IoT (ESP32 / rex-tracker-sys):** Publicam a telemetria em JSON no tópico `<ecode>/telemetria/<maquina.id>`.
2. **Broker MQTT (Eclipse Mosquitto):** Roda em um container na AWS EC2, recebendo o tráfego dos sensores na porta `1883`.
3. **Injetor (Python / Docker):** Consome o broker local de forma contínua, valida as mensagens e realiza as inserções diretamente no AWS RDS.
4. **Armazenamento (AWS RDS PostgreSQL):** Grava cada leitura na tabela `telemetria` do banco `irrigacao_db`, vinculada à operação da máquina.

---

## :satellite: Fluxo da telemetria

**Tópico:** `<ecode>/telemetria/<maquina.id>` (o injetor assina `+/telemetria/+`). O último segmento é o `id` da tabela `maquina`; o `ecode` não é usado.

**Payload (JSON)**, veja [exemplo_payload.json](exemplo_payload.json):

| Campo | Tipo | Observação |
|---|---|---|
| `timestamp` | inteiro (ms desde epoch) | Hora da leitura. Ausente ou anterior a 2020 (relógio ainda não sincronizado) → usa `NOW()` |
| `paf`, `pdf`, `pav`, `pdv`, `vazao`, `horimetro` | número | Sensor ausente é gravado como `NULL` (nunca `0`) |
| `ce_1` … `ce_9` | booleano | Comandos elétricos |
| `ch_1` … `ch_9` | booleano | Comandos hidráulicos |
| `calibracao_sensor` | objeto/lista (opcional) | Gravado como `jsonb`; qualquer outro valor vira `NULL` |
| `leitura_campo` | booleano (opcional) | Ausente → `false` |

Um `id_operacao` enviado no payload é **ignorado**: a operação é resolvida pelo próprio injetor.

**Resolução da operação:** para cada mensagem o injetor busca na tabela `operacao` a operação da máquina que estava aberta no instante da leitura (`inicio <= data_hora` e `fim IS NULL` ou `fim >= data_hora`), escolhendo a de `inicio` mais recente. A leitura é inserida em `telemetria` com esse `id_operacao` (o `id` da telemetria é gerado pelo banco).

> :warning: Se a máquina **não tiver operação aberta** naquele instante, a leitura é **descartada** (não é gravada) e aparece um `WARNING` no log.

---

## :open_file_folder: Estrutura de Arquivos do Projeto

```
.
├── docker-compose.yml         # Orquestração dos containers (Mosquitto e Injetor)
├── Dockerfile                 # Configuração da imagem Python do injetor
├── requirements.txt           # Dependências Python do injetor
├── main.py                    # Script Python (Consome MQTT e grava no RDS)
├── exemplo_payload.json       # Payload de exemplo para testes
├── .env.example               # Modelo das variáveis de ambiente (copie para .env)
├── sql
│   └── criar_role_mqtt_injector.sql  # Cria a role do banco usada pelo injetor
└── mosquitto
    └── config
        └── mosquitto.conf     # Configurações do Broker MQTT
```

## :wrench: Pré requisitos

- Instância AWS EC2 **t4g.micro (arm64) com Amazon Linux 2023**, na mesma VPC do RDS, com um **Elastic IP** associado (os sensores apontam para um IP fixo).
  - Instância: `<INSTANCE_ID>` (t4g.micro, us-east-1b)
  - Elastic IP: `<ELASTIC_IP>`
- RDS PostgreSQL `irrigacao-db` (`<ENDPOINT_RDS>`), banco `irrigacao_db`.
- As tabelas `telemetria`, `operacao` e `maquina` **já existem** no `irrigacao_db`: não há `CREATE TABLE` a rodar.
- Role do banco `mqtt_injector`, criada pelo script [`sql/criar_role_mqtt_injector.sql`](sql/criar_role_mqtt_injector.sql) (executado uma vez por um administrador do banco).

## :rocket: Passo a passo para rodar na EC2

### 1. Configure os Security Groups

A EC2 usa o Security Group `mqtt-injector-sg` (`<SG_EC2>`), e o SG do RDS (`<SG_RDS>`) libera a porta `5432` a partir dele:

| Security Group | Regra de entrada | Origem |
|---|---|---|
| `mqtt-injector-sg` (EC2) | SSH (`22`) | Seu IP |
| `mqtt-injector-sg` (EC2) | TCP `1883` (MQTT) | IPs dos sensores |
| `<SG_RDS>` (RDS) | PostgreSQL (`5432`) | `mqtt-injector-sg` |

> :warning: A porta `1883` trafega sem criptografia. Evite liberá-la para `0.0.0.0/0`.

### 2. Acesse a EC2 e instale Docker, Docker Compose e Git

```
ssh -i <sua-chave>.pem ec2-user@<ELASTIC_IP>
```

**Amazon Linux 2023** (a instância é arm64; o `uname -m` abaixo já baixa o binário `aarch64`):

```
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user

# Plugin do Docker Compose (não vem no repositório do Amazon Linux)
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m) \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
```

Saia e entre novamente na sessão SSH para o grupo `docker` valer, e confira:

```
docker compose version
```

### 3. Clone o repositório

```
git clone https://github.com/ArthurGGQueiroz/mqtt_to_injector.git
cd mqtt_to_injector
```

### 4. Crie o usuário do broker MQTT

O arquivo `mosquitto/config/passwd` é obrigatório: sem ele o Mosquitto não inicia.

```
docker run --rm -it -v $(pwd)/mosquitto/config:/mosquitto/config eclipse-mosquitto:2 \
  mosquitto_passwd -c /mosquitto/config/passwd seu_usuario
```

O terminal pedirá a senha duas vezes. Use esse mesmo usuário e senha nos sensores e no `.env`. Mais detalhes em [mosquitto/config/README.md](mosquitto/config/README.md).

### 5. Configure as variáveis de ambiente

```
cp .env.example .env
nano .env
```

Preencha:

- `MQTT_USER` / `MQTT_PASSWORD`: o usuário criado no passo 4.
- `DB_PASSWORD`: a senha da role `mqtt_injector`.

`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_NAME` e `DB_SSLMODE` já vêm preenchidos para o `irrigacao_db` (a conexão usa SSL, `sslmode=require`).

### 6. Confira o acesso ao banco (opcional)

A tabela `telemetria` já existe, então não há nada a criar. Para testar a conexão e as permissões da role com um container temporário do `psql`:

```
docker run --rm -it postgres:16 psql \
  "host=<ENDPOINT_RDS> dbname=irrigacao_db user=mqtt_injector sslmode=require" \
  -c "SELECT id, id_maquina, inicio, fim FROM operacao WHERE fim IS NULL;"
```

A consulta também mostra quais máquinas têm operação aberta (só elas terão leituras gravadas).

### 7. Suba os containers

```
docker compose up -d --build
docker compose ps
```

Os dois containers (`mqtt_broker` e `mqtt_injector`) devem aparecer como `running`. Com `restart: unless-stopped`, eles voltam sozinhos se a EC2 reiniciar. Os logs de cada container são rotacionados (3 arquivos de 10 MB).

### 8. Teste o fluxo

Publique o payload de exemplo para a máquina `2` (troque pelo `id` de uma máquina com operação aberta), de qualquer computador com o `mosquitto_pub` instalado:

```
mosquitto_pub -h <host> -u <user> -P <senha> -t teste/telemetria/2 -f exemplo_payload.json
```

> O `exemplo_payload.json` traz um `timestamp` fixo, e a operação precisa estar aberta nesse instante. Para usar a hora de chegada, remova o campo `timestamp`.

E acompanhe os logs do injetor:

```
docker compose logs -f injector
```

Deve aparecer `Mensagem recebida no tópico [teste/telemetria/2] (máquina: 2)` seguido de `Dados de telemetria inseridos no RDS com sucesso! (máquina: 2, operação: <id>)`.

### Comandos úteis

| Ação | Comando |
|---|---|
| Ver logs | `docker compose logs -f` |
| Parar | `docker compose down` |
| Atualizar após `git pull` | `docker compose up -d --build` |
| Adicionar outro usuário MQTT | `docker run --rm -it -v $(pwd)/mosquitto/config:/mosquitto/config eclipse-mosquitto:2 mosquitto_passwd /mosquitto/config/passwd outro_usuario` e depois `docker compose restart mosquitto` |

### Problemas comuns

- **`Falha na conexão MQTT: Not authorized`**: o `MQTT_USER`/`MQTT_PASSWORD` do `.env` não bate com o `passwd`.
- **`Erro ao conectar no RDS: ... timeout`**: o SG do RDS (`<SG_RDS>`) não libera a porta `5432` para o `mqtt-injector-sg`, ou o `DB_HOST` está errado.
- **`Erro ao conectar no RDS: ... password authentication failed`**: o `DB_PASSWORD` não é a senha da role `mqtt_injector`.
- **`Nenhuma operação aberta para a máquina N: leitura foi descartada...`**: não existe em `operacao` uma linha com `id_maquina = N` aberta no instante da leitura (`fim` nulo ou posterior ao `timestamp`). Abra a operação no sistema e confira se o último segmento do tópico é o `maquina.id` correto e se o `timestamp` do payload está certo.
- **`permission denied for table ...`**: a role `mqtt_injector` está sem permissão; rode novamente `sql/criar_role_mqtt_injector.sql`.
- **`Erro ao extrair o ID do tópico MQTT`**: o último segmento do tópico não é um número (`maquina.id`).
- **`mqtt_broker` reiniciando sem parar**: falta o arquivo `mosquitto/config/passwd` (passo 4). Veja com `docker compose logs mosquitto`.
