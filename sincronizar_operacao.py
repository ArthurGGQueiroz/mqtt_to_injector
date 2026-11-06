import time
import logging
from datetime import datetime, timezone

# Lógica de resolução da máquina, do tipo de operação, do operador e da operação
# vigente. Fica separada do main.py (sem paho) para ser testada com um cursor falso.
# Todas as funções recebem o cursor da transação aberta pelo main.py.

# Máquina vinculada ao chip (maquina.dev_id, migration 007 do RigTech_APIs)
SQL_MAQUINA_POR_DEV_ID = "SELECT id FROM maquina WHERE dev_id = %s"

# Os códigos do payload são os do cadastro do servidor (codigo_cadastro_servidor),
# NUNCA tipo_operacao.codigo / operador.codigo (outra numeração, outras pessoas)
SQL_TIPO_OPERACAO = "SELECT id FROM tipo_operacao WHERE codigo_cadastro_servidor = %s"
SQL_OPERADOR = "SELECT id FROM operador WHERE codigo_cadastro_servidor = %s"

# Operação aberta da máquina (no máximo uma: índice uq_operacao_aberta_por_maquina).
# FOR UPDATE serializa duas mensagens simultâneas da mesma máquina.
SQL_OPERACAO_ABERTA = """
    SELECT id, id_tipo_operacao, id_operador, inicio FROM operacao
    WHERE id_maquina = %s AND fim IS NULL
    FOR UPDATE
"""

# Fim da operação mais recente já fechada (None se a máquina não tem nenhuma)
SQL_ULTIMO_FIM = "SELECT MAX(fim) FROM operacao WHERE id_maquina = %s"

SQL_FECHAR_OPERACAO = "UPDATE operacao SET fim = %s WHERE id = %s"

SQL_ABRIR_OPERACAO = """
    INSERT INTO operacao (id_maquina, id_tipo_operacao, id_operador, inicio)
    VALUES (%s, %s, %s, %s)
    RETURNING id
"""

# Regra antiga: operação da máquina vigente no instante da leitura (usada para
# leituras atrasadas/reenviadas do SD, que não podem trocar a operação atual)
SQL_OPERACAO_VIGENTE = """
    SELECT id FROM operacao
    WHERE id_maquina = %s
      AND inicio <= %s
      AND (fim IS NULL OR fim >= %s)
    ORDER BY inicio DESC
    LIMIT 1
"""

# Antes de 2020 o relógio do ESP32 ainda não sincronizou (manda ~1970)
TIMESTAMP_MINIMO_MS = 1577836800000  # 2020-01-01

# Quanto tempo o cadastro (tipo/operador) fica em memória antes de consultar o banco de novo
CADASTRO_TTL_SEGUNDOS = 300

_SEM_VALOR = object()


# Cache curto em memória dos códigos do cadastro. Guarda também os desconhecidos (None).
class CacheCadastro:
    def __init__(self, ttl=CADASTRO_TTL_SEGUNDOS, relogio=time.monotonic):
        self.ttl = ttl
        self.relogio = relogio
        self.itens = {}

    def obter(self, chave):
        item = self.itens.get(chave)
        if item is None:
            return _SEM_VALOR
        valor, expira_em = item
        if self.relogio() >= expira_em:
            del self.itens[chave]
            return _SEM_VALOR
        return valor

    def guardar(self, chave, valor):
        self.itens[chave] = (valor, self.relogio() + self.ttl)


cache_cadastro = CacheCadastro()


# Resolve a máquina da mensagem. Retorna (id_maquina, origem) com origem "dev_id" ou "tópico".
# dev_id não cadastrado (ou ausente) cai no id do tópico, como era antes.
def resolver_maquina(cursor, dev_id, id_topico):
    if isinstance(dev_id, int) and not isinstance(dev_id, bool):
        cursor.execute(SQL_MAQUINA_POR_DEV_ID, (dev_id,))
        row = cursor.fetchone()
        if row is not None:
            return row[0], "dev_id"
        logging.warning(f"dev_id {dev_id} não está cadastrado em maquina.dev_id: usando a máquina do tópico ({id_topico}).")
    return id_topico, "tópico"


# Instante da leitura como datetime UTC. Retorna (instante, veio_do_payload).
# Sem timestamp válido usa o horário do servidor.
def resolver_instante(timestamp_ms, agora=None):
    if isinstance(timestamp_ms, (int, float)) and not isinstance(timestamp_ms, bool) \
            and timestamp_ms >= TIMESTAMP_MINIMO_MS:
        return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc), True
    logging.warning(f"timestamp ausente ou inválido no payload ({timestamp_ms!r}): usando o horário do servidor.")
    return (agora or datetime.now(timezone.utc)), False


# Código do tipo: a parada (improdutiva) tem prioridade sobre a atividade (produtiva)
def escolher_codigo_tipo(id_parada, id_operacao):
    return id_parada if id_parada is not None else id_operacao


# Traduz um código do cadastro do servidor para o id da tabela. None se ausente ou desconhecido.
def _resolver_cadastro(cursor, sql, rotulo, codigo, cache):
    if codigo is None:
        return None
    if isinstance(codigo, float) and codigo.is_integer():
        codigo = int(codigo)
    if not isinstance(codigo, int) or isinstance(codigo, bool):
        logging.warning(f"{rotulo}: código inválido no payload ({codigo!r}), ignorado.")
        return None

    chave = (rotulo, codigo)
    guardado = cache.obter(chave)
    if guardado is not _SEM_VALOR:
        return guardado

    cursor.execute(sql, (codigo,))
    row = cursor.fetchone()
    id_encontrado = row[0] if row is not None else None
    if id_encontrado is None:
        logging.warning(f"{rotulo}: código {codigo} não existe em codigo_cadastro_servidor, gravando sem {rotulo}.")
    cache.guardar(chave, id_encontrado)
    return id_encontrado


def resolver_tipo_operacao(cursor, codigo, cache=None):
    return _resolver_cadastro(cursor, SQL_TIPO_OPERACAO, "tipo_operacao", codigo, cache or cache_cadastro)


def resolver_operador(cursor, codigo, cache=None):
    return _resolver_cadastro(cursor, SQL_OPERADOR, "operador", codigo, cache or cache_cadastro)


# Regra antiga: operação da máquina vigente em ts (None se não houver)
def buscar_operacao_vigente(cursor, id_maquina, ts):
    cursor.execute(SQL_OPERACAO_VIGENTE, (id_maquina, ts, ts))
    row = cursor.fetchone()
    return row[0] if row is not None else None


# Garante que a operação aberta da máquina corresponde ao (tipo, operador) da leitura.
# Retorna o id da operação a usar na telemetria, ou None se a leitura deve ser descartada.
# Roda dentro da transação do main.py (que faz o commit/rollback).
def sincronizar_operacao(cursor, id_maquina, id_tipo_operacao, id_operador, ts, descricao=""):
    cursor.execute(SQL_OPERACAO_ABERTA, (id_maquina,))
    aberta = cursor.fetchone()

    if aberta is not None:
        id_aberta, tipo_aberto, operador_aberto, inicio_aberta = aberta
        if ts < inicio_aberta:
            # Leitura atrasada/reenviada: nunca troca a operação atual
            return buscar_operacao_vigente(cursor, id_maquina, ts)
        if (tipo_aberto, operador_aberto) == (id_tipo_operacao, id_operador):
            return id_aberta
        cursor.execute(SQL_FECHAR_OPERACAO, (ts, id_aberta))
        id_nova = _abrir_operacao(cursor, id_maquina, id_tipo_operacao, id_operador, ts)
        logging.info(
            f"Troca de operação na máquina {id_maquina}: operação {id_aberta} "
            f"(tipo {tipo_aberto}, operador {operador_aberto}) -> operação {id_nova} "
            f"(tipo {id_tipo_operacao}, operador {id_operador}) em {ts.isoformat()} {descricao}".rstrip()
        )
        return id_nova

    cursor.execute(SQL_ULTIMO_FIM, (id_maquina,))
    ultimo_fim = cursor.fetchone()[0]
    if ultimo_fim is None or ts >= ultimo_fim:
        id_nova = _abrir_operacao(cursor, id_maquina, id_tipo_operacao, id_operador, ts)
        logging.info(
            f"Operação {id_nova} aberta na máquina {id_maquina} "
            f"(tipo {id_tipo_operacao}, operador {id_operador}) em {ts.isoformat()} {descricao}".rstrip()
        )
        return id_nova

    # Leitura antiga, de antes do fim da última operação: não abre operação nova
    return buscar_operacao_vigente(cursor, id_maquina, ts)


def _abrir_operacao(cursor, id_maquina, id_tipo_operacao, id_operador, ts):
    cursor.execute(SQL_ABRIR_OPERACAO, (id_maquina, id_tipo_operacao, id_operador, ts))
    return cursor.fetchone()[0]
