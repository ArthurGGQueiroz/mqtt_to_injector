-- =============================================================================
-- Role de privilégio mínimo para o serviço mqtt_to_injector
-- Banco: irrigacao_db (AWS RDS PostgreSQL 17)
--
-- O injector só precisa:
--   * ler operacao/maquina (resolver a operação aberta do equipamento);
--   * inserir linhas em telemetria.
--
-- Script idempotente: pode ser executado várias vezes sem erro.
-- A senha NÃO fica neste arquivo; é passada como variável do psql:
--
--   psql "host=<host> port=5432 dbname=irrigacao_db user=postgres sslmode=require" \
--        -v senha='<senha-forte>' -f sql/criar_role_mqtt_injector.sql
--
-- Gere uma senha forte, por exemplo:
--   python -c "import secrets; print(secrets.token_urlsafe(32))"
-- =============================================================================

\set ON_ERROR_STOP on

-- 1) Cria a role com LOGIN caso ainda não exista
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mqtt_injector') THEN
        CREATE ROLE mqtt_injector LOGIN;
    END IF;
END
$$;

-- 2) Define/atualiza a senha (variável psql :senha, escapada como literal)
ALTER ROLE mqtt_injector WITH LOGIN PASSWORD :'senha';

-- 3) Acesso ao banco e ao schema
GRANT CONNECT ON DATABASE irrigacao_db TO mqtt_injector;
GRANT USAGE ON SCHEMA public TO mqtt_injector;

-- 4) Leitura apenas das tabelas necessárias para resolver a operação
GRANT SELECT ON TABLE public.operacao, public.maquina TO mqtt_injector;

-- 5) Inserção de telemetria (sem UPDATE/DELETE/SELECT)
GRANT INSERT ON TABLE public.telemetria TO mqtt_injector;

-- 6) Sequência da coluna telemetria.id (bigint DEFAULT nextval)
--    Nome confirmado via pg_get_serial_sequence('telemetria','id')
GRANT USAGE ON SEQUENCE public.telemetria_id_seq TO mqtt_injector;

-- Observação sobre o trigger:
--   telemetria possui o trigger AFTER INSERT OR UPDATE trg_telemetria_notificar,
--   que executa notificar_mudanca_maquina(). A função é SECURITY INVOKER, ou
--   seja, roda com os privilégios de mqtt_injector: ela faz
--   SELECT id_maquina FROM operacao (coberto pelo GRANT SELECT acima) e
--   pg_notify('irrigacao_maquina', ...), que não exige privilégio extra.
--   A verificação da FK telemetria.id_operacao -> operacao(id) é feita pelo
--   dono da tabela, sem exigir grants adicionais.
