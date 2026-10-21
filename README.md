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
└── mosquitto
    └── config
        └── mosquitto.conf     # Configurações do Broker MQTT```

## :wrench: Pré requisitos

- Instância AWS EC2 com Docker e Docker Compose instalados.
- Instância AWS RDS (PostgreSQL) configurada e acessível a partir da EC2.
- Tabela de destino criada no RDS (exemplo básico):

```
CREATE TABLE leituras (
    id SERIAL PRIMARY KEY,
    sensor_id VARCHAR(50),
    valor FLOAT,
    topico VARCHAR(100),
    criado_em TIMESTAMP DEFAULT NOW()
);```
