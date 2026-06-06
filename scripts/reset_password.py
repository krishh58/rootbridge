import sys, os
sys.path.insert(0, '/app')
os.chdir('/app')
import bcrypt
from app import create_app
from app.db import db
from app.models import User

app = create_app()
with app.app_context():
    user = User.query.filter_by(email='krishndrsn@gmail.com').first()
    if user:
        user.password_hash = bcrypt.hashpw(b'RootBridge2026!', bcrypt.gensalt()).decode()
        db.session.commit()
        print('Password reset to: RootBridge2026!')
    else:
        print('User not found')
