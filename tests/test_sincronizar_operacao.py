import os
import sys
import logging
import traceback
from datetime import datetime, timedelta, timezone

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import sincronizar_operacao as sync  # noqa: E402

# Testes da lógica de máquina/tipo/operador/operação com um cursor falso em memória.
# Rodam com pytest ou direto: python tests/test_sincronizar_operacao.py

T0 = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)


def em(minutos):
    return T0 + timedelta(minutes=minutos)


# Simula as tabelas usadas pelas consultas de sincronizar_operacao.py
class CursorFalso:
    def __init__(self, maquinas=None, tipos=None, operadores=None, operacoes=None):
        self.maquinas = maquinas or {}      # dev_id -> id da máquina
        self.tipos = tipos or {}            # codigo_cadastro_servidor -> id
        self.operadores = operadores or {}  # codigo_cadastro_servidor -> id
        # cada operação: dict(id, id_maquina, tipo, operador, inicio, fim)
        self.operacoes = operacoes or []
        self.consultas = []
        self._resultado = []
        self._proximo_id = max([o["id"] for o in self.operacoes], default=100) + 1

    def execute(self, sql, params=()):
        self.consultas.append((sql, params))
        if sql == sync.SQL_MAQUINA_POR_DEV_ID:
            id_ = self.maquinas.get(params[0])
            self._resultado = [(id_,)] if id_ is not None else []
        elif sql == sync.SQL_TIPO_OPERACAO:
            id_ = self.tipos.get(params[0])
            self._resultado = [(id_,)] if id_ is not None else []
        elif sql == sync.SQL_OPERADOR:
            id_ = self.operadores.get(params[0])
            self._resultado = [(id_,)] if id_ is not None else []
        elif sql == sync.SQL_OPERACAO_ABERTA:
            self._resultado = [
                (o["id"], o["tipo"], o["operador"], o["inicio"])
                for o in self.operacoes if o["id_maquina"] == params[0] and o["fim"] is None
            ]
        elif sql == sync.SQL_ULTIMO_FIM:
            fins = [o["fim"] for o in self.operacoes if o["id_maquina"] == params[0] and o["fim"] is not None]
            self._resultado = [(max(fins) if fins else None,)]
        elif sql == sync.SQL_FECHAR_OPERACAO:
            fim, id_ = params
            for o in self.operacoes:
                if o["id"] == id_:
                    o["fim"] = fim
            self._resultado = []
        elif sql == sync.SQL_ABRIR_OPERACAO:
            id_maquina, tipo, operador, inicio = params
            if any(o["id_maquina"] == id_maquina and o["fim"] is None for o in self.operacoes):
                raise psycopg2.errors.UniqueViolation("uq_operacao_aberta_por_maquina")
            nova = dict(id=self._proximo_id, id_maquina=id_maquina, tipo=tipo, operador=operador, inicio=inicio, fim=None)
            self._proximo_id += 1
            self.operacoes.append(nova)
            self._resultado = [(nova["id"],)]
        elif sql == sync.SQL_OPERACAO_VIGENTE:
            id_maquina, ts, _ = params
            candidatas = [
                o for o in self.operacoes
                if o["id_maquina"] == id_maquina and o["inicio"] <= ts and (o["fim"] is None or o["fim"] >= ts)
            ]
            candidatas.sort(key=lambda o: o["inicio"], reverse=True)
            self._resultado = [(candidatas[0]["id"],)] if candidatas else []
        else:
            raise AssertionError(f"consulta inesperada: {sql}")

    def fetchone(self):
        return self._resultado[0] if self._resultado else None


def operacao(id_, tipo, operador, inicio, fim=None, id_maquina=10):
    return dict(id=id_, id_maquina=id_maquina, tipo=tipo, operador=operador, inicio=inicio, fim=fim)


class CapturaLogs(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.mensagens = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


def capturar_logs():
    handler = CapturaLogs()
    logging.getLogger().addHandler(handler)
    return handler


def soltar_logs(handler):
    logging.getLogger().removeHandler(handler)


# ---------------------------------------------------------------- máquina

def test_maquina_por_dev_id():
    cur = CursorFalso(maquinas={215876883829000: 10})
    assert sync.resolver_maquina(cur, 215876883829000, 2) == (10, "dev_id")


def test_maquina_dev_id_desconhecido_cai_no_topico_com_warning():
    cur = CursorFalso(maquinas={215876883829000: 10})
    logs = capturar_logs()
    try:
        assert sync.resolver_maquina(cur, 999, 2) == (2, "tópico")
    finally:
        soltar_logs(logs)
    assert any("999" in m for m in logs.mensagens)


def test_maquina_sem_dev_id_usa_topico_sem_consultar_nem_avisar():
    cur = CursorFalso(maquinas={1: 10})
    logs = capturar_logs()
    try:
        assert sync.resolver_maquina(cur, None, 2) == (2, "tópico")
    finally:
        soltar_logs(logs)
    assert cur.consultas == [] and logs.mensagens == []


# ---------------------------------------------------------------- tipo e operador

def test_maquina_dev_id_float_inteiro_e_string_numerica_sao_aceitos():
    cur = CursorFalso(maquinas={215876883829000: 10})
    assert sync.resolver_maquina(cur, 215876883829000.0, 2) == (10, "dev_id")
    assert sync.resolver_maquina(cur, " 215876883829000 ", 2) == (10, "dev_id")
    assert sync.resolver_maquina(cur, "215876883829000", 2) == (10, "dev_id")


def test_maquina_dev_id_invalido_usa_topico_com_warning_sem_consultar():
    cur = CursorFalso(maquinas={1: 10, 0: 11, -5: 12})
    for invalido in (True, -5, 0, sync.BIGINT_MAXIMO + 1, "abc", 1.5, [1]):
        logs = capturar_logs()
        try:
            assert sync.resolver_maquina(cur, invalido, 2) == (2, "tópico")
        finally:
            soltar_logs(logs)
        assert logs.mensagens, invalido
    assert cur.consultas == []


def test_tipo_prefere_id_parada_sobre_id_operacao():
    assert sync.escolher_codigo_tipo(7, 285) == 7
    assert sync.escolher_codigo_tipo(None, 285) == 285
    assert sync.escolher_codigo_tipo(None, None) is None


def test_tipo_resolvido_por_codigo_cadastro_servidor():
    cur = CursorFalso(tipos={285: 4})
    cache = sync.CacheCadastro()
    assert sync.resolver_tipo_operacao(cur, 285, cache) == 4
    assert cur.consultas[0][0] == sync.SQL_TIPO_OPERACAO
    assert "codigo_cadastro_servidor" in sync.SQL_TIPO_OPERACAO


def test_operador_1001_desconhecido_vira_none_e_nunca_usa_codigo():
    cur = CursorFalso(operadores={})
    logs = capturar_logs()
    try:
        assert sync.resolver_operador(cur, 1001, sync.CacheCadastro()) is None
    finally:
        soltar_logs(logs)
    assert any("1001" in m for m in logs.mensagens)
    # a consulta olha só codigo_cadastro_servidor (a coluna "codigo" não aparece)
    sql = sync.SQL_OPERADOR.replace("codigo_cadastro_servidor", "")
    assert "codigo" not in sql
    assert len(cur.consultas) == 1


def test_codigo_ausente_vira_none_sem_consulta_nem_warning():
    cur = CursorFalso()
    logs = capturar_logs()
    try:
        assert sync.resolver_tipo_operacao(cur, None, sync.CacheCadastro()) is None
        assert sync.resolver_operador(cur, None, sync.CacheCadastro()) is None
    finally:
        soltar_logs(logs)
    assert cur.consultas == [] and logs.mensagens == []


def test_cache_evita_nova_consulta_inclusive_para_desconhecido_e_expira():
    agora = [0.0]
    cache = sync.CacheCadastro(ttl=300, relogio=lambda: agora[0])
    cur = CursorFalso(operadores={5: 50})
    logs = capturar_logs()
    try:
        assert sync.resolver_operador(cur, 5, cache) == 50
        assert sync.resolver_operador(cur, 5, cache) == 50
        assert sync.resolver_operador(cur, 1001, cache) is None
        assert sync.resolver_operador(cur, 1001, cache) is None
    finally:
        soltar_logs(logs)
    assert len(cur.consultas) == 2
    agora[0] = 301.0
    assert sync.resolver_operador(cur, 5, cache) == 50
    assert len(cur.consultas) == 3


# ---------------------------------------------------------------- instante

def test_instante_do_payload_e_fallback_para_servidor_com_warning():
    ts, do_payload = sync.resolver_instante(1793938758000, datetime.fromtimestamp(1793938758 + 86400, tz=timezone.utc))
    assert do_payload and ts == datetime.fromtimestamp(1793938758, tz=timezone.utc)
    agora = em(0)
    logs = capturar_logs()
    try:
        ts, do_payload = sync.resolver_instante(1000, agora)  # ~1970
    finally:
        soltar_logs(logs)
    assert not do_payload and ts == agora and logs.mensagens
    assert sync.resolver_instante(None, agora)[0] == agora


def test_instante_no_futuro_usa_servidor_com_warning():
    agora = em(0)
    futuro_ms = int(agora.timestamp() * 1000) + 30 * 86400 * 1000
    logs = capturar_logs()
    try:
        assert sync.resolver_instante(futuro_ms, agora) == (agora, False)
    finally:
        soltar_logs(logs)
    assert any("futuro" in m for m in logs.mensagens)


def test_instante_infinito_e_nan_usam_servidor():
    agora = em(0)
    logs = capturar_logs()
    try:
        assert sync.resolver_instante(float("inf"), agora) == (agora, False)
        assert sync.resolver_instante(float("nan"), agora) == (agora, False)
    finally:
        soltar_logs(logs)


def test_instante_levemente_adiantado_usa_servidor_sem_warning():
    agora = em(0)
    adiantado_ms = int(agora.timestamp() * 1000) + 30 * 1000
    logs = capturar_logs()
    try:
        assert sync.resolver_instante(adiantado_ms, agora) == (agora, False)
    finally:
        soltar_logs(logs)
    assert logs.mensagens == []


# ---------------------------------------------------------------- sincronização

def test_sem_aberta_abre_nova():
    cur = CursorFalso()
    id_ = sync.sincronizar_operacao(cur, 10, 4, 50, em(0))
    assert len(cur.operacoes) == 1
    o = cur.operacoes[0]
    assert id_ == o["id"] and (o["tipo"], o["operador"], o["inicio"], o["fim"]) == (4, 50, em(0), None)


def test_mesmo_tipo_e_operador_mantem():
    cur = CursorFalso(operacoes=[operacao(1, 4, 50, em(0))])
    assert sync.sincronizar_operacao(cur, 10, 4, 50, em(5)) == 1
    assert len(cur.operacoes) == 1 and cur.operacoes[0]["fim"] is None


def test_troca_de_tipo_fecha_com_fim_ts_e_abre():
    cur = CursorFalso(operacoes=[operacao(1, 4, 50, em(0))])
    id_ = sync.sincronizar_operacao(cur, 10, 9, 50, em(5))
    assert id_ != 1 and len(cur.operacoes) == 2
    assert cur.operacoes[0]["fim"] == em(5)
    nova = cur.operacoes[1]
    assert (nova["id"], nova["tipo"], nova["operador"], nova["inicio"], nova["fim"]) == (id_, 9, 50, em(5), None)


def test_troca_de_operador_fecha_e_abre():
    cur = CursorFalso(operacoes=[operacao(1, 4, 50, em(0))])
    id_ = sync.sincronizar_operacao(cur, 10, 4, None, em(5))
    assert id_ != 1 and cur.operacoes[0]["fim"] == em(5) and cur.operacoes[1]["operador"] is None


def test_leitura_atrasada_nao_troca_e_usa_operacao_vigente():
    cur = CursorFalso(operacoes=[
        operacao(1, 4, 50, em(-60), em(0)),
        operacao(2, 9, 50, em(0)),  # aberta
    ])
    # ts dentro da operação 1 e com (tipo, operador) diferente de ambas
    id_ = sync.sincronizar_operacao(cur, 10, 7, 60, em(-10))
    assert id_ == 1
    assert len(cur.operacoes) == 2 and cur.operacoes[1]["fim"] is None


def test_leitura_atrasada_sem_operacao_vigente_descarta():
    cur = CursorFalso(operacoes=[operacao(2, 9, 50, em(0))])
    assert sync.sincronizar_operacao(cur, 10, 7, 60, em(-10)) is None
    assert len(cur.operacoes) == 1 and cur.operacoes[0]["fim"] is None


def test_sem_aberta_e_ts_anterior_ao_ultimo_fim_nao_abre():
    cur = CursorFalso(operacoes=[operacao(1, 4, 50, em(-60), em(0))])
    id_ = sync.sincronizar_operacao(cur, 10, 9, 50, em(-30))
    assert id_ == 1 and len(cur.operacoes) == 1  # vigente em ts, nada aberto
    # fora de qualquer operação (antes de tudo): descarta
    assert sync.sincronizar_operacao(cur, 10, 9, 50, em(-120)) is None
    assert len(cur.operacoes) == 1


def test_sem_aberta_e_ts_igual_ou_posterior_ao_ultimo_fim_abre():
    cur = CursorFalso(operacoes=[operacao(1, 4, 50, em(-60), em(0))])
    id_ = sync.sincronizar_operacao(cur, 10, 9, 50, em(0))
    assert id_ == 2 and cur.operacoes[1]["inicio"] == em(0)


def test_outra_maquina_nao_interfere():
    cur = CursorFalso(operacoes=[operacao(1, 4, 50, em(0), id_maquina=11)])
    id_ = sync.sincronizar_operacao(cur, 10, 4, 50, em(5))
    assert id_ != 1 and cur.operacoes[0]["fim"] is None


def test_conflito_de_abertura_propaga_unique_violation():
    # o main.py faz rollback e repete uma vez quando isso acontece
    cur = CursorFalso()
    cur.execute(sync.SQL_ABRIR_OPERACAO, (10, 4, 50, em(0)))
    try:
        cur.execute(sync.SQL_ABRIR_OPERACAO, (10, 4, 50, em(1)))
    except psycopg2.errors.UniqueViolation:
        return
    raise AssertionError("era esperado UniqueViolation")


def test_fluxo_completo_payload_real():
    # payload real: dev_id da Gotejo_69, atividade 285, operador 1001 desconhecido
    cur = CursorFalso(maquinas={215876883829000: 10}, tipos={285: 4}, operadores={})
    cache = sync.CacheCadastro()
    id_maquina, origem = sync.resolver_maquina(cur, 215876883829000, 2)
    tipo = sync.resolver_tipo_operacao(cur, sync.escolher_codigo_tipo(None, 285), cache)
    operador = sync.resolver_operador(cur, 1001, cache)
    assert (id_maquina, origem, tipo, operador) == (10, "dev_id", 4, None)
    id_ = sync.sincronizar_operacao(cur, id_maquina, tipo, operador, em(0))
    assert cur.operacoes[0]["id"] == id_ and cur.operacoes[0]["operador"] is None


if __name__ == "__main__":
    testes = [(nome, f) for nome, f in sorted(globals().items()) if nome.startswith("test_") and callable(f)]
    falhas = 0
    for nome, funcao in testes:
        try:
            funcao()
            print(f"OK     {nome}")
        except Exception:
            falhas += 1
            print(f"FALHA  {nome}")
            traceback.print_exc()
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(1 if falhas else 0)
