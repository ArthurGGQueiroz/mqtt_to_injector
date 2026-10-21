# IoT MQTT to AWS RDS Injector

Sistema leve em containers Docker projetado para receber telemetria de dispositivos IoT via protocolo MQTT, processar as mensagens em tempo real e persistir os dados de forma centralizada em um banco de dados gerenciado na nuvem (**AWS RDS**).

---

## :office: Arquitetura do Sistema

O fluxo de dados ocorre da seguinte forma:
1. **Sensores IoT (ESP32):** Publicam dados em formato JSON em tópicos MQTT específicos.
2. **Broker MQTT (Eclipse Mosquitto):** Roda em um container na AWS EC2, recebendo o tráfego dos sensores na porta `1883`.
3. **Injetor (Python / Docker):** Consome o broker local de forma contínua, valida as mensagens e realiza as inserções diretamente no AWS RDS.
4. **Armazenamento (AWS RDS PostgreSQL):** Mantém o histórico seguro e estruturado para consultas e dashboards.

---

## :open_file_folder: Estrutura de Arquivos do Projeto

```
.
├── docker-compose.yml         # Orquestração dos containers (Mosquitto e Injetor)
├── Dockerfile                 # Configuração da imagem Python do injetor
├── main.py                    # Script Python (Consome MQTT e grava no RDS)
├── .env.example               # Modelo das variáveis de ambiente (copie para .env)
└── mosquitto
    └── config
        └── mosquitto.conf     # Configurações do Broker MQTT
```

## :wrench: Pré requisitos

- Instância AWS EC2 (Amazon Linux 2023 ou Ubuntu), na mesma VPC do RDS.
- Instância AWS RDS (PostgreSQL) configurada e acessível a partir da EC2.
- Tabela de destino criada no RDS (exemplo básico):

```
CREATE TABLE leituras (
    id SERIAL PRIMARY KEY,
    sensor_id VARCHAR(50),
    valor FLOAT,
    topico VARCHAR(100),
    criado_em TIMESTAMP DEFAULT NOW()
);
```

## :rocket: Passo a passo para rodar na EC2

### 1. Configure os Security Groups

| Security Group | Regra de entrada | Origem |
|---|---|---|
| EC2 | SSH (`22`) | Seu IP |
| EC2 | TCP `1883` (MQTT) | IPs dos sensores |
| RDS | PostgreSQL (`5432`) | Security Group da EC2 |

> :warning: A porta `1883` trafega sem criptografia. Evite liberá-la para `0.0.0.0/0`.

### 2. Acesse a EC2 e instale Docker, Docker Compose e Git

```
ssh -i sua-chave.pem ec2-user@IP_PUBLICO_DA_EC2
```

**Amazon Linux 2023:**

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

**Ubuntu** (usuário `ubuntu`):

```
curl -fsSL https://get.docker.com | sudo sh
sudo apt-get install -y git
sudo usermod -aG docker ubuntu
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
- `DB_HOST`: o endpoint do RDS (ex.: `meu-banco.xxxxxx.us-east-1.rds.amazonaws.com`).
- `DB_USER`, `DB_PASSWORD`, `DB_NAME`: as credenciais do banco.

### 6. Crie a tabela no RDS

Se a tabela `leituras` ainda não existir, conecte ao RDS com um container temporário do `psql`:

```
docker run --rm -it postgres:16 psql -h ENDPOINT_DO_RDS -U SEU_USUARIO -d SEU_BANCO
```

e execute o `CREATE TABLE` da seção [Pré requisitos](#wrench-pré-requisitos).

### 7. Suba os containers

```
docker compose up -d --build
docker compose ps
```

Os dois containers (`mqtt_broker` e `mqtt_injector`) devem aparecer como `running`. Com `restart: unless-stopped`, eles voltam sozinhos se a EC2 reiniciar.

### 8. Teste o fluxo

Publique uma mensagem de teste pelo próprio broker:

```
docker exec mqtt_broker mosquitto_pub -h localhost -u seu_usuario -P sua_senha \
  -t sensores/teste -m '{"sensor_id": "esp32-01", "valor": 23.5}'
```

E acompanhe os logs do injetor:

```
docker compose logs -f injector
```

Deve aparecer `Mensagem recebida no tópico [sensores/teste]` seguido de `Dado inserido no RDS com sucesso!`.

### Comandos úteis

| Ação | Comando |
|---|---|
| Ver logs | `docker compose logs -f` |
| Parar | `docker compose down` |
| Atualizar após `git pull` | `docker compose up -d --build` |
| Adicionar outro usuário MQTT | `docker run --rm -it -v $(pwd)/mosquitto/config:/mosquitto/config eclipse-mosquitto:2 mosquitto_passwd /mosquitto/config/passwd outro_usuario` e depois `docker compose restart mosquitto` |

### Problemas comuns

- **`Falha na conexão MQTT: Not authorized`**: o `MQTT_USER`/`MQTT_PASSWORD` do `.env` não bate com o `passwd`.
- **`Erro ao conectar no RDS: ... timeout`**: o Security Group do RDS não libera a porta `5432` para a EC2, ou o `DB_HOST` está errado.
- **`relation "leituras" does not exist`**: falta criar a tabela (passo 6).
- **`mqtt_broker` reiniciando sem parar**: falta o arquivo `mosquitto/config/passwd` (passo 4). Veja com `docker compose logs mosquitto`.
