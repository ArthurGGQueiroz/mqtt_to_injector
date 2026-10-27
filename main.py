import os
import json
import time
import logging
import paho.mqtt.client as mqtt
import psycopg2
from psycopg2.extras import Json

# Configuração de Logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Variáveis de ambiente injetadas pelo Docker Compose
MQTT_HOST = os.getenv("MQTT_BROKER_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "+/telemetria/+")
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")
DB_SSLMODE = os.getenv("DB_SSLMODE", "require")

# Operação aberta da máquina no instante da leitura (timestamp em ms ou NOW())
SQL_OPERACAO = """
    SELECT id FROM operacao
    WHERE id_maquina = %s
      AND inicio <= COALESCE(to_timestamp(%s / 1000.0), NOW())
      AND (fim IS NULL OR fim >= COALESCE(to_timestamp(%s / 1000.0), NOW()))
    ORDER BY inicio DESC
    LIMIT 1
"""

# O id da telemetria é gerado pelo banco (BIGSERIAL)
SQL_INSERT = """
    INSERT INTO telemetria (
        id_operacao, data_hora, paf, pdf, pav, pdv, vazao, horimetro,
        ce_1, ce_2, ce_3, ce_4, ce_5, ce_6, ce_7, ce_8, ce_9,
        ch_1, ch_2, ch_3, ch_4, ch_5, ch_6, ch_7, ch_8, ch_9,
        calibracao_sensor, leitura_campo
    ) VALUES (
        %s, COALESCE(to_timestamp(%s / 1000.0), NOW()), %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s
    )
"""

# Conexão reaproveitada entre as mensagens (reconecta quando cai)
db_conn = None

# Função para conectar ao PostgreSQL (AWS RDS)
def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            sslmode=DB_SSLMODE,
            connect_timeout=5
        )
        return conn
    except Exception as e:
        logging.error(f"Erro ao conectar no RDS: {e}")
        return None

# Fecha e descarta a conexão atual (a próxima mensagem abre outra)
def reset_db_connection():
    global db_conn
    if db_conn is not None:
        try:
            db_conn.close()
        except Exception:
            pass
    db_conn = None

# Resolve a operação e grava a leitura. Retorna o id_operacao usado ou None se não há operação.
def gravar_telemetria(id_maquina, timestamp, valores):
    global db_conn
    # Tenta no máximo duas vezes: a segunda só se a conexão tiver caído
    for tentativa in range(2):
        if db_conn is None or db_conn.closed:
            db_conn = get_db_connection()
            if db_conn is None:
                raise RuntimeError("sem conexão com o RDS")

        cursor = db_conn.cursor()
        try:
            cursor.execute(SQL_OPERACAO, (id_maquina, timestamp, timestamp))
            row = cursor.fetchone()
            if row is None:
                db_conn.rollback()
                return None

            id_operacao = row[0]
            cursor.execute(SQL_INSERT, (id_operacao, timestamp) + valores)
            db_conn.commit()
            return id_operacao
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            # Conexão perdida (RDS reiniciou, timeout de rede...): reconecta e tenta de novo
            reset_db_connection()
            if tentativa == 0:
                logging.warning(f"Conexão com o RDS perdida ({e}), reconectando...")
                continue
            raise
        except Exception:
            # Erro na query: desfaz a transação para a conexão continuar utilizável
            try:
                db_conn.rollback()
            except Exception:
                reset_db_connection()
            raise
        finally:
            try:
                cursor.close()
            except Exception:
                pass

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
        # Extração do ID da máquina (maquina.id) a partir do tópico MQTT (ex: japungu/telemetria/3211)
        topico_partes = msg.topic.split('/')
        id_maquina = int(topico_partes[-1])

        payload_str = msg.payload.decode('utf-8')
        logging.info(f"Mensagem recebida no tópico [{msg.topic}] (máquina: {id_maquina}): {payload_str}")

        data = json.loads(payload_str)

        # Extração dos dados do payload (sensores ausentes ficam NULL, nunca 0)
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

        # Ainda não enviados pelo firmware. calibracao_sensor é jsonb (NULL se ausente);
        # leitura_campo ausente = leitura normal da máquina (False)
        calibracao_sensor = data.get("calibracao_sensor")
        calibracao_sensor = Json(calibracao_sensor) if isinstance(calibracao_sensor, (dict, list)) else None
        leitura_campo = data.get("leitura_campo")
        if leitura_campo is None:
            leitura_campo = False

        # Hora da leitura (ms desde epoch). O firmware guarda as mensagens no SD quando
        # está sem rede, então NOW() daria a hora de chegada. Antes de sincronizar o
        # relógio o ESP32 manda ~1970: nesse caso cai no NOW().
        timestamp = data.get("timestamp")
        if not isinstance(timestamp, (int, float)) or timestamp < 1577836800000:  # 2020-01-01
            timestamp = None

        valores = (
            paf, pdf, pav, pdv, vazao, horimetro,
            ce_1, ce_2, ce_3, ce_4, ce_5, ce_6, ce_7, ce_8, ce_9,
            ch_1, ch_2, ch_3, ch_4, ch_5, ch_6, ch_7, ch_8, ch_9,
            calibracao_sensor, leitura_campo
        )

        # Inserção no RDS (a operação é resolvida pelo banco, não pelo payload)
        id_operacao = gravar_telemetria(id_maquina, timestamp, valores)
        if id_operacao is None:
            logging.warning(f"Nenhuma operação aberta para a máquina {id_maquina}: leitura foi descartada por não haver operação.")
            return

        id_operacao_payload = data.get("id_operacao")
        if id_operacao_payload is not None and id_operacao_payload != id_operacao:
            logging.debug(f"id_operacao do payload ({id_operacao_payload}) ignorado; usando {id_operacao}")

        logging.info(f"Dados de telemetria inseridos no RDS com sucesso! (máquina: {id_maquina}, operação: {id_operacao})")

    # JSONDecodeError é subclasse de ValueError: precisa vir antes
    except json.JSONDecodeError:
        logging.error("O payload recebido não é um JSON válido.")
    except (ValueError, IndexError):
        logging.error(f"Erro ao extrair o ID do tópico MQTT: {msg.topic}")
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
