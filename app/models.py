from datetime import datetime, timezone
from .db import db

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    tier = db.Column(db.String(20), default='free', nullable=False)
    token_balance = db.Column(db.Integer, default=100, nullable=False)
    token_balance_topup = db.Column(db.Integer, default=0, nullable=False)
    token_reset_date = db.Column(db.DateTime)
    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    trees = db.relationship('Tree', backref='owner', lazy=True, cascade='all, delete-orphan')

    TIER_TOKENS = {
        'free': 100,
        'us': 500,
        'european': 1000,
        'aa': 1000,
        'asian': 1000,
        'all': 2500,
    }

    def total_tokens(self):
        return self.token_balance + self.token_balance_topup

    def deduct_tokens(self, amount):
        if self.total_tokens() < amount:
            return False
        if self.token_balance >= amount:
            self.token_balance -= amount
        else:
            remainder = amount - self.token_balance
            self.token_balance = 0
            self.token_balance_topup -= remainder
        return True

class Tree(db.Model):
    __tablename__ = 'trees'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    share_token = db.Column(db.String(64), unique=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    persons = db.relationship('Person', backref='tree', lazy=True, cascade='all, delete-orphan')

class Person(db.Model):
    __tablename__ = 'persons'
    id = db.Column(db.Integer, primary_key=True)
    tree_id = db.Column(db.Integer, db.ForeignKey('trees.id'), nullable=False)
    first_name = db.Column(db.String(255))
    last_name = db.Column(db.String(255))
    birth_year = db.Column(db.Integer)
    birth_state = db.Column(db.String(100))
    birth_country = db.Column(db.String(100))
    death_year = db.Column(db.Integer)
    death_place = db.Column(db.String(255))
    notes = db.Column(db.Text)
    confidence = db.Column(db.Integer, default=0)
    parent_ids = db.Column(db.JSON, default=list)
    spouse_ids = db.Column(db.JSON, default=list)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    search_results = db.relationship('SearchResult', backref='person', lazy=True, cascade='all, delete-orphan')
    gaps = db.relationship('Gap', backref='person', lazy=True, cascade='all, delete-orphan')

class SearchResult(db.Model):
    __tablename__ = 'search_results'
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    source = db.Column(db.String(50), nullable=False)
    record_type = db.Column(db.String(100))
    url = db.Column(db.Text)
    raw_data = db.Column(db.JSON)
    found_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Gap(db.Model):
    __tablename__ = 'gaps'
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    gap_type = db.Column(db.String(100), nullable=False)
    suggested_source = db.Column(db.String(255))
    suggested_query = db.Column(db.Text)
    resolved = db.Column(db.Boolean, default=False)

class AlfredMessage(db.Model):
    __tablename__ = 'alfred_messages'
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    tree_id = db.Column(db.Integer, db.ForeignKey('trees.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'user' or 'assistant'
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
