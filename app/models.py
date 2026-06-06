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
    referral_code = db.Column(db.String(16), unique=True)
    referred_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    referral_reward_paid = db.Column(db.Boolean, default=False, nullable=False)
    discovery_enabled = db.Column(db.Boolean, default=True, nullable=False)
    two_factor_enabled = db.Column(db.Boolean, default=False, nullable=False)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    blocked_user_ids  = db.Column(db.JSON, nullable=False, default=list, server_default='[]')
    display_name      = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    trees = db.relationship('Tree', backref='owner', lazy=True, cascade='all, delete-orphan')

    TIER_TOKENS = {
        'free': 100,
        'us': 1500,
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

    def get_display_name(self):
        if self.display_name:
            return self.display_name
        prefix = self.email.split('@')[0]
        return prefix[:20]

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
    middle_name = db.Column(db.String(255))
    notes = db.Column(db.Text)
    confidence = db.Column(db.Integer, default=0)
    parent_ids = db.Column(db.JSON, default=list)
    spouse_ids = db.Column(db.JSON, default=list)
    research_findings = db.Column(db.JSON, default=list)
    soundex_key  = db.Column(db.String(4), nullable=True)
    birth_decade = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    search_results = db.relationship('SearchResult', backref='person', lazy=True, cascade='all, delete-orphan')
    gaps = db.relationship('Gap', backref='person', lazy=True, cascade='all, delete-orphan')
    alfred_messages = db.relationship('AlfredMessage', cascade='all, delete-orphan', backref='person', lazy=True)
    __table_args__ = (
        db.Index('ix_persons_soundex_decade', 'soundex_key', 'birth_decade'),
    )

class Document(db.Model):
    __tablename__ = 'documents'
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    tree_id = db.Column(db.Integer, db.ForeignKey('trees.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(100), nullable=False)
    file_data = db.Column(db.LargeBinary, nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    extracted_text = db.Column(db.Text)
    ai_summary = db.Column(db.Text)
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


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


class TreeInvite(db.Model):
    __tablename__ = 'tree_invites'
    id = db.Column(db.Integer, primary_key=True)
    tree_id = db.Column(db.Integer, db.ForeignKey('trees.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False)
    invite_token = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(255))
    claimed_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=False)


class TreeCollaborator(db.Model):
    __tablename__ = 'tree_collaborators'
    id = db.Column(db.Integer, primary_key=True)
    tree_id = db.Column(db.Integer, db.ForeignKey('trees.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint('tree_id', 'user_id', name='uq_tree_collaborator'),)


class PersonMatch(db.Model):
    __tablename__ = 'person_matches'
    id          = db.Column(db.Integer, primary_key=True)
    person_a_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    person_b_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    user_a_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user_b_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    score       = db.Column(db.Integer, nullable=False)
    notified_a  = db.Column(db.Boolean, default=False, nullable=False)
    notified_b  = db.Column(db.Boolean, default=False, nullable=False)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    messages    = db.relationship('ResearchMessage', backref='match', lazy=True,
                                  cascade='all, delete-orphan')
    __table_args__ = (
        db.UniqueConstraint('person_a_id', 'person_b_id', name='uq_person_match_pair'),
        db.CheckConstraint('person_a_id < person_b_id', name='ck_person_match_order'),
    )


class ResearchMessage(db.Model):
    __tablename__ = 'research_messages'
    id         = db.Column(db.Integer, primary_key=True)
    match_id   = db.Column(db.Integer, db.ForeignKey('person_matches.id'), nullable=False)
    sender_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    body       = db.Column(db.Text, nullable=False)
    read       = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_tokens'
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token      = db.Column(db.String(64), unique=True, nullable=False)
    used       = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=False)


class TwoFactorCode(db.Model):
    __tablename__ = 'two_factor_codes'
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    code       = db.Column(db.String(6), nullable=False)
    temp_token = db.Column(db.String(64), unique=True, nullable=False)
    used       = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=False)


class EmailVerification(db.Model):
    __tablename__ = 'email_verifications'
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token      = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=False)


class PersonEdge(db.Model):
    """Adjacency list replacing JSON parent_ids/spouse_ids for O(1) bidirectional traversal."""
    __tablename__ = 'person_edges'
    id         = db.Column(db.Integer, primary_key=True)
    person_id  = db.Column(db.Integer, db.ForeignKey('persons.id', ondelete='CASCADE'), nullable=False)
    related_id = db.Column(db.Integer, db.ForeignKey('persons.id', ondelete='CASCADE'), nullable=False)
    rel_type   = db.Column(db.String(10), nullable=False)  # 'parent', 'child', 'spouse'
    __table_args__ = (
        db.Index('ix_person_edges_person_id',  'person_id'),
        db.Index('ix_person_edges_related_id', 'related_id'),
        db.UniqueConstraint('person_id', 'related_id', 'rel_type', name='uq_person_edge'),
    )
