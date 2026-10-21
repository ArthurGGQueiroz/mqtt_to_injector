import os
import json
import logging
import paho.mqtt.client as mqtt
import psycopg2

# Configuração de Logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Variáveis de ambiente injetadas pelo Docker Compose
MQTT_HOST = os.getenv("MQTT_BROKER_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "sensores/#")

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

# Função para conectar ao PostgreSQL (AWS RDS)
def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )
        return conn
    except Exception as e:
        logging.error(f"Erro ao conectar no RDS: {e}")
        return None

# Callback executado quando o MQTT conecta ao broker
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info("Conectado ao Broker MQTT com sucesso!")
        client.subscribe(MQTT_TOPIC)
    else:
        logging.error(f"Falha na conexão MQTT, código de retorno: {rc}")

# Callback executado quando uma nova mensagem chega de um sensor
def on_message(client, userdata, msg):
    try:
        payload_str = msg.payload.decode('utf-8')
        logging.info(f"Mensagem recebida no tópico [{msg.topic}]: {payload_str}")
        
        data = json.loads(payload_str)
        
        # Exemplo de extração dos dados (ajuste conforme o payload do seu sensor)
        sensor_id = data.get("sensor_id")
        valor = data.get("valor")
        timestamp = data.get("timestamp") # Ou use o horário atual do servidor

        # Inserção no RDS
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            # Certifique-se de que a tabela 'leituras' existe no seu RDS
            cursor.execute(
                "INSERT INTO leituras (sensor_id, valor, topico, criado_em) VALUES (%s, %s, %s, NOW())",
                (sensor_id, valor, msg.topic)
            )
            conn.commit()
            cursor.close()
            conn.close()
            logging.info("Dado inserido no RDS com sucesso!")
            
    except json.JSONDecodeError:
        logging.error("O payload recebido não é um JSON válido.")
    except Exception as e:
        logging.error(f"Erro ao processar/inserir a mensagem: {e}")

# Inicialização do Cliente MQTT
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

if __name__ == "__main__":
    logging.info("Iniciando o Injetor MQTT...")
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    # Loop contínuo para escutar as mensagens indefinidamente
    client.loop_forever()
