from flask_sqlalchemy import SQLAlchemy
import redis as redis_lib
from flask import current_app, g

db = SQLAlchemy()

def get_redis():
    if 'redis_client' not in g:
        g.redis_client = redis_lib.from_url(
            current_app.config['REDIS_URL'],
            decode_responses=True
        )
    return g.redis_client
