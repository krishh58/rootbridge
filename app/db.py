from flask_sqlalchemy import SQLAlchemy
import redis as redis_lib
from flask import current_app

db = SQLAlchemy()

def get_redis():
    return redis_lib.from_url(current_app.config['REDIS_URL'], decode_responses=True)
