import os

class Config:
    _raw_db_url = os.environ.get('DATABASE_URL', '')
    SECRET_KEY = os.environ.get('SECRET_KEY', '')
    SQLALCHEMY_DATABASE_URI = _raw_db_url.replace('postgres://', 'postgresql://', 1) or 'sqlite:///:memory:'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 5,
        'pool_timeout': 10,   # fail fast instead of hanging when pool is exhausted
        'pool_recycle': 300,
        'max_overflow': 2,
    }
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
    FAMILYSEARCH_CLIENT_ID = os.environ.get('FAMILYSEARCH_CLIENT_ID', '')
    FAMILYSEARCH_CLIENT_SECRET = os.environ.get('FAMILYSEARCH_CLIENT_SECRET', '')
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
    STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
    BASE_URL = os.environ.get('BASE_URL', 'https://rootbridge.app')
    ADMIN_SECRET = os.environ.get('ADMIN_SECRET', '')

class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SECRET_KEY = 'test-secret-key'
    REDIS_URL = 'redis://localhost:6379/1'
