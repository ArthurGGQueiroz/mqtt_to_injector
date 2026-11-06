import os
import json
import time
import logging
import paho.mqtt.client as mqtt
import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2.extras import Json

import sincronizar_operacao as sync

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

# O id da telemetria é gerado pelo banco (BIGSERIAL).
# data_hora recebe o mesmo instante usado para sincronizar a operação.
SQL_INSERT = """
    INSERT INTO telemetria (
        id_operacao, data_hora, paf, pdf, pav, pdv, vazao, horimetro,
        ce_1, ce_2, ce_3, ce_4, ce_5, ce_6, ce_7, ce_8, ce_9,
        ch_1, ch_2, ch_3, ch_4, ch_5, ch_6, ch_7, ch_8, ch_9,
        calibracao_sensor, leitura_campo
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s,
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

# Resolve a máquina, sincroniza a operação (troca de tipo/operador) e grava a leitura,
# tudo numa transação só. Retorna (id_maquina, origem, id_operacao); id_operacao é None
# se não há operação para a leitura (ela é descartada).
def gravar_telemetria(id_topico, dev_id, codigo_tipo, codigo_operador, instante, valores):
    global db_conn
    # Repete no máximo uma vez por motivo: conexão caída ou conflito no índice de operação aberta
    reconectou = False
    reabriu = False
    while True:
        if db_conn is None or db_conn.closed:
            db_conn = get_db_connection()
            if db_conn is None:
                raise RuntimeError("sem conexão com o RDS")

        cursor = db_conn.cursor()
        try:
            id_maquina, origem = sync.resolver_maquina(cursor, dev_id, id_topico)
            id_tipo_operacao = sync.resolver_tipo_operacao(cursor, codigo_tipo)
            id_operador = sync.resolver_operador(cursor, codigo_operador)
            descricao = f"(códigos: tipo {codigo_tipo}, operador {codigo_operador})"

            id_operacao = sync.sincronizar_operacao(
                cursor, id_maquina, id_tipo_operacao, id_operador, instante, descricao
            )
            if id_operacao is None:
                db_conn.rollback()
                return id_maquina, origem, None

            cursor.execute(SQL_INSERT, (id_operacao, instante) + valores)
            db_conn.commit()
            return id_maquina, origem, id_operacao
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            # Conexão perdida (RDS reiniciou, timeout de rede...): reconecta e tenta de novo
            reset_db_connection()
            if not reconectou:
                reconectou = True
                logging.warning(f"Conexão com o RDS perdida ({e}), reconectando...")
                continue
            raise
        except pg_errors.UniqueViolation as e:
            # Duas mensagens abriram operação ao mesmo tempo (uq_operacao_aberta_por_maquina):
            # desfaz e repete, já enxergando a operação que o outro lado abriu
            db_conn.rollback()
            if not reabriu:
                reabriu = True
                logging.warning(f"Conflito ao abrir operação ({e}), repetindo a leitura...")
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
        # ID da máquina (maquina.id) do tópico MQTT (ex: japungu/telemetria/3211).
        # É o plano B: a máquina vem de preferência do dev_id do payload.
        topico_partes = msg.topic.split('/')
        id_topico = int(topico_partes[-1])

        payload_str = msg.payload.decode('utf-8')
        logging.info(f"Mensagem recebida no tópico [{msg.topic}] (máquina do tópico: {id_topico}): {payload_str}")

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
        # relógio o ESP32 manda ~1970: nesse caso cai no horário do servidor (com WARNING).
        instante, _ = sync.resolver_instante(data.get("timestamp"))

        # Identificação da máquina e da operação. id_parada (improdutiva) tem prioridade sobre
        # id_operacao (produtiva); ambos e o operador são códigos do cadastro do servidor.
        # estado, evento, apontamento_manual etc. não são usados.
        dev_id = data.get("dev_id")
        codigo_tipo = sync.escolher_codigo_tipo(data.get("id_parada"), data.get("id_operacao"))
        codigo_operador = data.get("operador")

        valores = (
            paf, pdf, pav, pdv, vazao, horimetro,
            ce_1, ce_2, ce_3, ce_4, ce_5, ce_6, ce_7, ce_8, ce_9,
            ch_1, ch_2, ch_3, ch_4, ch_5, ch_6, ch_7, ch_8, ch_9,
            calibracao_sensor, leitura_campo
        )

        # Inserção no RDS (máquina e operação são resolvidas/sincronizadas pelo injetor)
        id_maquina, origem, id_operacao = gravar_telemetria(
            id_topico, dev_id, codigo_tipo, codigo_operador, instante, valores
        )
        if id_operacao is None:
            logging.warning(f"Nenhuma operação para a máquina {id_maquina} (via {origem}) em {instante.isoformat()}: leitura foi descartada por não haver operação.")
            return

        logging.info(f"Dados de telemetria inseridos no RDS com sucesso! (máquina: {id_maquina} via {origem}, operação: {id_operacao})")

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
