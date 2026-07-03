import json
import os
from pathlib import Path
from http.cookies import SimpleCookie
from urllib.parse import urlparse

DATA_FILE = Path('users.json')


def load_users():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {}


def save_users(users):
    DATA_FILE.write_text(json.dumps(users, indent=2))


def get_user_from_cookie(headers):
    cookie_header = headers.get('Cookie', '')
    if not cookie_header:
        return None
    cookie = SimpleCookie(cookie_header)
    session_id = cookie.get('session_id')
    if not session_id:
        return None
    users = load_users()
    for user in users.values():
        if user.get('session_id') == session_id.value:
            return user
    return None


def set_session(user):
    import secrets
    session_id = secrets.token_hex(16)
    users = load_users()
    if user['email'] in users:
        users[user['email']]['session_id'] = session_id
        users[user['email']]['plan'] = users[user['email']].get('plan', 'free')
    else:
        users[user['email']] = {**user, 'session_id': session_id, 'plan': 'free'}
    save_users(users)
    return session_id


def clear_session(email):
    users = load_users()
    if email in users:
        users[email].pop('session_id', None)
        save_users(users)


def update_plan(email, plan, payment_phone=None, payment_id=None):
    users = load_users()
    if email not in users:
        return False
    users[email]['plan'] = plan
    users[email]['paymentPhone'] = payment_phone or ''
    users[email]['paymentId'] = payment_id or ''
    save_users(users)
    return True
