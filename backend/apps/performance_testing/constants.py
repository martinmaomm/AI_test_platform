PROTOCOL_VERSION = 1
AGENT_VERSION = '0.1.0'
ENGINE_VERSION = '2.43.3'

HEARTBEAT_INTERVAL_SECONDS = 5
NODE_OFFLINE_AFTER_SECONDS = 30
ENROLLMENT_TTL_SECONDS = 15 * 60

MAX_USERS = 100
MAX_DURATION_SECONDS = 600
MAX_SPAWN_RATE = 100
MAX_STEPS = 20
MAX_REQUEST_BYTES = 64 * 1024

ALLOWED_HTTP_METHODS = (
    'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS',
)


def platform_config():
    return {
        'phase': 'node_access',
        'execution_enabled': False,
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
