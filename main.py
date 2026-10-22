import os
import json
import time
import logging
import paho.mqtt.client as mqtt
import psycopg2

# Configuração de Logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Variáveis de ambiente injetadas pelo Docker Compose
MQTT_HOST = os.getenv("MQTT_BROKER_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "sensores/#")
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

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
def on_connect(client, userdata, flags, reason_code, properties):
    if not reason_code.is_failure:
        logging.info("Conectado ao Broker MQTT com sucesso!")
        client.subscribe(MQTT_TOPIC)
    else:
        logging.error(f"Falha na conexão MQTT: {reason_code}")

# Callback executado quando uma nova mensagem chega de um sensor
def on_message(client, userdata, msg):
    try:
        # Extração do ID a partir do tópico MQTT (ex: japungu/telemetria/3211)
        topico_partes = msg.topic.split('/')
        id = int(topico_partes[-1])
        
        payload_str = msg.payload.decode('utf-8')
        logging.info(f"Mensagem recebida no tópico [{msg.topic}] (ID: {id_operacao}): {payload_str}")
        
        data = json.loads(payload_str)
        
        # Extração dos dados do payload
        id_operacao = data.get("id_operacao")
        paf = data.get("paf")
        pdf = data.get("pdf")
        pav = data.get("pav")
        pdv = data.get("pdv")
        vazao = data.get("vazao")
        horimetro = data.get("horimetro")
        
        # Comandos elétricos (ce_1 a ce_9)[cite: 1]
        ce_1 = data.get("ce_1")
        ce_2 = data.get("ce_2")
        ce_3 = data.get("ce_3")
        ce_4 = data.get("ce_4")
        ce_5 = data.get("ce_5")
        ce_6 = data.get("ce_6")
        ce_7 = data.get("ce_7")
        ce_8 = data.get("ce_8")
        ce_9 = data.get("ce_9")
        
        # Comandos hidráulicos (ch_1 a ch_9)[cite: 1]
        ch_1 = data.get("ch_1")
        ch_2 = data.get("ch_2")
        ch_3 = data.get("ch_3")
        ch_4 = data.get("ch_4")
        ch_5 = data.get("ch_5")
        ch_6 = data.get("ch_6")
        ch_7 = data.get("ch_7")
        ch_8 = data.get("ch_8")
        ch_9 = data.get("ch_9")
        
        # Inserção no RDS (utilizando NOW() para data_hora)
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            
            sql = """
                INSERT INTO telemetria (
                    id, id_operacao, data_hora, paf, pdf, pav, pdv, vazao, horimetro,
                    ce_1, ce_2, ce_3, ce_4, ce_5, ce_6, ce_7, ce_8, ce_9,
                    ch_1, ch_2, ch_3, ch_4, ch_5, ch_6, ch_7, ch_8, ch_9,
                    calibracao_sensor, leitura_campo
                ) VALUES (
                    %s, %s, NOW(), %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s
                )
            """
            
            cursor.execute(sql, (
                id, id_operacao, paf, pdf, pav, pdv, vazao, horimetro,
                ce_1, ce_2, ce_3, ce_4, ce_5, ce_6, ce_7, ce_8, ce_9,
                ch_1, ch_2, ch_3, ch_4, ch_5, ch_6, ch_7, ch_8, ch_9,
                calibracao_sensor, leitura_campo
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            logging.info("Dados de telemetria inseridos no RDS com sucesso!")
            
    except (ValueError, IndexError):
        logging.error(f"Erro ao extrair o ID do tópico MQTT: {msg.topic}")
    except json.JSONDecodeError:
        logging.error("O payload recebido não é um JSON válido.")
    except Exception as e:
        logging.error(f"Erro ao processar/inserir a mensagem: {e}")

# Inicialização do Cliente MQTT
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
client.on_connect = on_connect
client.on_message = on_message

if __name__ == "__main__":
    logging.info("Iniciando o Injetor MQTT...")
    # O broker pode ainda não estar pronto quando o container sobe
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            break
        except OSError as e:
            logging.warning(f"Broker MQTT indisponível ({e}), tentando novamente em 5s...")
            time.sleep(5)
    # Loop contínuo para escutar as mensagens indefinidamente
    client.loop_forever()
