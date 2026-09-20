PROTOCOL_VERSION = 2
AGENT_VERSION = '0.3.1'
# Old agents may keep heartbeating so the management UI can show an upgrade
# prompt. Execution compatibility requires the matching fixed runtime version.
SUPPORTED_AGENT_VERSIONS = ('0.2.0', '0.2.1', '0.3.0', AGENT_VERSION)
ENGINE_VERSION = '2.43.3'

HEARTBEAT_INTERVAL_SECONDS = 5
NODE_OFFLINE_AFTER_SECONDS = 30
ENROLLMENT_TTL_SECONDS = 15 * 60
CONTROLLER_FRESH_SECONDS = 15
COMMAND_LEASE_SECONDS = 15
MAX_METRICS_SAMPLES = 400

MAX_USERS = 100
MAX_DURATION_SECONDS = 600
MAX_SPAWN_RATE = 100
MAX_STEPS = 20
MAX_REQUEST_BYTES = 64 * 1024
MAX_FAILURE_SAMPLES = 20
MAX_FAILURE_VALUE_BYTES = 2048

ALLOWED_HTTP_METHODS = (
    'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS',
)


def platform_config(*, controller_online=False, execution_available=False, unavailable_reason=''):
    execution_enabled = bool(execution_available and controller_online)
    if execution_enabled:
        reason = ''
    elif not execution_available:
        reason = unavailable_reason or '性能执行配置不可用。'
    else:
        reason = '性能控制器未在线。'
    return {
        'phase': 'execution',
        'controller_online': bool(controller_online),
        'execution_enabled': execution_enabled,
        'execution_unavailable_reason': reason,
        'max_nodes_per_run': 1,
        'protocol_version': PROTOCOL_VERSION,
        'engine_version': ENGINE_VERSION,
        'agent_version': AGENT_VERSION,
        'heartbeat_interval_seconds': HEARTBEAT_INTERVAL_SECONDS,
        'node_offline_after_seconds': NODE_OFFLINE_AFTER_SECONDS,
        'limits': {
            'max_users': MAX_USERS,
            'max_duration_seconds': MAX_DURATION_SECONDS,
            'max_spawn_rate': MAX_SPAWN_RATE,
            'max_steps': MAX_STEPS,
        },
    }
