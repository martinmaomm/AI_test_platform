"""
Django settings for the Automation Platform project.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from datetime import timedelta
# Load environment variables
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# 检查并创建 logs 目录，防止 Celery/Django 启动时找不到日志文件报错
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-change-me-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'True').lower() == 'true'

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,::1').split(',')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',  # 保留sessions用于admin
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third party apps
    'rest_framework',
    'rest_framework.authtoken',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',  # JWT黑名单功能
    'corsheaders',
    'django_filters',
    'channels',  # 添加Channels支持
    'django_celery_beat',  # Celery Beat支持
    
    # Local apps
    'users',
    'projects',
    'api_testing',
    'ai_core',
    'web_testing',
    'scheduled_tasks',
    'notifications',
    'project_knowledge',
    'performance_testing',
]

# 独立知识应用；关闭后禁止提交新任务，不影响原 API RAG 或 WebUI 脚本。
PROJECT_KNOWLEDGE_ENABLED = os.getenv('PROJECT_KNOWLEDGE_ENABLED', 'true').lower() == 'true'
try:
    PROJECT_KNOWLEDGE_TOTAL_TIMEOUT = min(1500, max(30, int(os.getenv('KNOWLEDGE_TOTAL_TIMEOUT_SECONDS', '1200'))))
except (TypeError, ValueError):
    PROJECT_KNOWLEDGE_TOTAL_TIMEOUT = 1200

# Browser-derived API discovery is opt-in. Its budgets are intentionally
# independent of WebUI generation and the global Celery time limit.
API_BROWSER_DISCOVERY_ENABLED = os.getenv('API_BROWSER_DISCOVERY_ENABLED', 'false').lower() == 'true'


def _browser_discovery_setting(name, default, minimum, maximum):
    try:
        return min(maximum, max(minimum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


API_BROWSER_DISCOVERY_TOTAL_TIMEOUT_SECONDS = _browser_discovery_setting(
    'API_BROWSER_DISCOVERY_TOTAL_TIMEOUT_SECONDS', 900, 60, 1800,
)
API_BROWSER_DISCOVERY_MAX_MODEL_STEPS = _browser_discovery_setting(
    'API_BROWSER_DISCOVERY_MAX_MODEL_STEPS', 100, 1, 100,
)
API_BROWSER_DISCOVERY_MAX_TOOL_CALLS = _browser_discovery_setting(
    'API_BROWSER_DISCOVERY_MAX_TOOL_CALLS', 100, 1, 100,
)
API_BROWSER_DISCOVERY_MAX_REQUESTS = _browser_discovery_setting(
    'API_BROWSER_DISCOVERY_MAX_REQUESTS', 500, 1, 500,
)
API_BROWSER_DISCOVERY_MAX_BODY_BYTES = _browser_discovery_setting(
    'API_BROWSER_DISCOVERY_MAX_BODY_BYTES', 256 * 1024, 1024, 256 * 1024,
)
API_BROWSER_DISCOVERY_MAX_TOTAL_BYTES = _browser_discovery_setting(
    'API_BROWSER_DISCOVERY_MAX_TOTAL_BYTES', 20 * 1024 * 1024, 1024, 20 * 1024 * 1024,
)
API_BROWSER_DISCOVERY_HEARTBEAT_LEASE_SECONDS = _browser_discovery_setting(
    'API_BROWSER_DISCOVERY_HEARTBEAT_LEASE_SECONDS', 45, 10, 300,
)

# Custom User Model
AUTH_USER_MODEL = 'users.User'

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',  # 保留SessionMiddleware用于admin
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST', '127.0.0.1'),
        'PORT': os.getenv('DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
        },
    }
}

# Redis Configuration
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Celery Configuration
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Shanghai'
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30分钟超时
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_WORKER_LOG_FILE = os.path.join(LOGS_DIR, 'celery.log')

# Celery Beat Configuration
CELERY_BEAT_SCHEDULER = 'scheduled_tasks.beat_health:BeatHealthDatabaseScheduler'
CELERY_BEAT_SCHEDULE = {
    # 可以在这里定义一些默认的定时任务
}


# Cache Configuration
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_URL,
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 50,
                'retry_on_timeout': True,
            },
            'SOCKET_CONNECT_TIMEOUT': 5,
            'SOCKET_TIMEOUT': 5,
        }
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Media files（用户上传与执行截图；报告数据使用认证接口）
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# 前端/报告站点地址，用于通知中的「查看报告」链接及 CORS/CSRF 放行
# TODO: 用户请在此处填入本机的真实局域网 IP，如 http://192.168.1.100:5173（或通过环境变量 FRONTEND_BASE_URL 配置）
FRONTEND_BASE_URL = os.getenv('FRONTEND_BASE_URL', 'http://127.0.0.1:5173').rstrip('/')
# 兼容旧环境变量 SITE_URL
SITE_URL = os.getenv('SITE_URL', FRONTEND_BASE_URL).rstrip('/')

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework settings
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'EXCEPTION_HANDLER': 'common.api.api_exception_handler.custom_exception_handler',
}

# CORS settings（放行前端/报告页来源，局域网访问时 FRONTEND_BASE_URL 会加入此列表）
_CORS_ORIGINS_DEFAULT = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
CORS_ALLOWED_ORIGINS = _CORS_ORIGINS_DEFAULT + (
    [FRONTEND_BASE_URL] if FRONTEND_BASE_URL not in _CORS_ORIGINS_DEFAULT else []
)

CORS_ALLOW_CREDENTIALS = True

# Additional CORS settings for development
CORS_ALLOW_ALL_ORIGINS = False  # Keep this False for security
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]

CORS_ALLOW_METHODS = [
    'DELETE',
    'GET',
    'OPTIONS',
    'PATCH',
    'POST',
    'PUT',
]

# CSRF settings（与 CORS 一致，局域网访问时 FRONTEND_BASE_URL 会加入此列表）
_CSRF_ORIGINS_DEFAULT = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
CSRF_TRUSTED_ORIGINS = _CSRF_ORIGINS_DEFAULT + (
    [FRONTEND_BASE_URL] if FRONTEND_BASE_URL not in _CSRF_ORIGINS_DEFAULT else []
)

# Additional CSRF settings for development
CSRF_COOKIE_SECURE = False  # Set to True in production with HTTPS
CSRF_COOKIE_HTTPONLY = False  # Allow JavaScript access to CSRF token
CSRF_COOKIE_SAMESITE = 'Lax'  # Allow cross-site requests
CSRF_USE_SESSIONS = False  # Use cookies instead of sessions for CSRF

# Session settings removed - using JWT only


# File upload settings
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': os.path.join(BASE_DIR, 'logs', 'django.log'),
            'formatter': 'verbose',
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
}

# Security settings（XFrameOptionsMiddleware 仍在 MIDDLEWARE 中启用）
X_FRAME_OPTIONS = 'SAMEORIGIN'
SECURE_CONTENT_TYPE_NOSNIFF = False  # Disable content type sniffing for development

# Channels配置
ASGI_APPLICATION = 'config.asgi.application'

# Channels层配置
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [REDIS_URL],
            "capacity": 100,  # 每个频道组的最大连接数
            "expiry": 60,     # 频道过期时间（秒）
            "group_expiry": 86400,  # 频道组过期时间（秒）
        },
    },
}

# JWT配置
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=7),       # AccessToken 有效期：7天
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),      # RefreshToken 有效期：7天
    "ROTATE_REFRESH_TOKENS": True,                  # 刷新 token 时生成新的 refresh
    "BLACKLIST_AFTER_ROTATION": True,               # 旧的 refresh 是否作废
    "AUTH_HEADER_TYPES": ("Bearer",),               # 请求头格式: Authorization: Bearer <token>
    
}
