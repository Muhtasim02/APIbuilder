import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, redirect, render_template_string, request, send_file, session, url_for

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / 'generated_apis.db'


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT,
            plan TEXT DEFAULT 'free',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS apis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            owner_email TEXT NOT NULL,
            api_name TEXT NOT NULL,
            description TEXT,
            endpoint TEXT NOT NULL,
            method TEXT NOT NULL,
            auth_type TEXT DEFAULT 'none',
            spec TEXT NOT NULL,
            schema_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            published INTEGER DEFAULT 0,
            version TEXT DEFAULT 'v1'
        )
    ''')
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


init_db()


@app.route('/')
def index():
    return render_template_string(OPENING_HTML)


@app.route('/plans')
def plans():
    if 'user' not in session:
        return redirect('/')
    return render_template_string(PLANS_HTML)


@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect('/')
    return render_template_string(DASHBOARD_HTML)


@app.route('/api/auth', methods=['POST'])
def auth():
    data = request.get_json(silent=True) or {}
    mode = data.get('mode', 'login')
    email = (data.get('email') or '').strip().lower()
    password = (data.get('password') or '').strip()
    if not email or not password:
        return jsonify({'message': 'Email and password are required'}), 400
    conn = get_db()
    cur = conn.cursor()
    if mode == 'signup':
        name = (data.get('name') or '').strip()
        cur.execute('SELECT 1 FROM users WHERE email=?', (email,))
        if cur.fetchone():
            conn.close()
            return jsonify({'message': 'User already exists'}), 400
        cur.execute('INSERT INTO users (email, password, name, plan) VALUES (?, ?, ?, ?)', (email, password, name, 'free'))
        conn.commit()
        conn.close()
        session['user'] = {'email': email, 'name': name, 'plan': 'free'}
        return jsonify({'message': 'Account created successfully'}), 200
    cur.execute('SELECT * FROM users WHERE email=? AND password=?', (email, password))
    user = cur.fetchone()
    conn.close()
    if not user:
        return jsonify({'message': 'Invalid credentials'}), 401
    session['user'] = {'email': user['email'], 'name': user['name'] or '', 'plan': user['plan']}
    return jsonify({'message': 'Login successful'}), 200


@app.route('/api/me')
def me():
    if 'user' not in session:
        return jsonify({'message': 'Unauthorized'}), 401
    return jsonify({'user': session['user']})


@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('user', None)
    return jsonify({'message': 'Logged out'})


@app.route('/api/plan', methods=['POST'])
def set_plan():
    if 'user' not in session:
        return jsonify({'message': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    plan = data.get('plan', 'free')
    conn = get_db()
    cur = conn.cursor()
    cur.execute('UPDATE users SET plan=? WHERE email=?', (plan, session['user']['email']))
    conn.commit()
    conn.close()
    session['user']['plan'] = plan
    return jsonify({'message': f'{plan.title()} plan activated successfully'})


@app.route('/api/build', methods=['POST'])
def build_api():
    if 'user' not in session:
        return jsonify({'message': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'message': 'Prompt is required'}), 400
    spec = generate_api_spec(prompt)
    slug = slugify(spec['endpoint'])
    endpoint = spec['endpoint']
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM apis WHERE slug=?', (slug,))
    if cur.fetchone():
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"
    api_data = json.dumps(spec)
    cur.execute('INSERT INTO apis (slug, owner_email, api_name, description, endpoint, method, auth_type, spec, schema_json, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (
        slug, session['user']['email'], spec['api_name'], spec['description'], endpoint, spec['http_method'], spec.get('authentication', {}).get('type', 'none'), json.dumps(spec), api_data, spec.get('version', 'v1')
    ))
    conn.commit()
    conn.close()
    register_route(spec)
    return jsonify({'message': 'API generated', 'id': slug, 'endpoint': endpoint, 'prompt': prompt, 'response': spec['success_response']})


@app.route('/api/list')
def list_apis():
    if 'user' not in session:
        return jsonify({'message': 'Unauthorized'}), 401
    conn = get_db()
    rows = conn.execute('SELECT * FROM apis WHERE owner_email=? ORDER BY id DESC', (session['user']['email'],)).fetchall()
    conn.close()
    return jsonify({'apis': [dict(r) for r in rows]})


@app.route('/api/docs/<slug>')
def docs(slug):
    conn = get_db()
    row = conn.execute('SELECT * FROM apis WHERE slug=?', (slug,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'message': 'Not found'}), 404
    spec = json.loads(row['spec'])
    return jsonify({'slug': row['slug'], 'spec': spec})


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


@app.route('/openapi.json')
def openapi():
    conn = get_db()
    rows = conn.execute('SELECT * FROM apis').fetchall()
    conn.close()
    openapi_spec = {
        'openapi': '3.0.3',
        'info': {'title': 'API Maker Pro', 'version': '1.0.0'},
        'paths': {}
    }
    for row in rows:
        spec = json.loads(row['spec'])
        path = spec['endpoint']
        openapi_spec['paths'][path] = {
            spec['http_method'].lower(): {
                'summary': spec['api_name'],
                'description': spec['description'],
                'responses': {'200': {'description': 'Success'}}
            }
        }
    return jsonify(openapi_spec)


def register_route(spec: Dict[str, Any]):
    endpoint = spec['endpoint']
    method = spec['http_method'].upper()
    slug = slugify(endpoint)

    def handler(**kwargs):
        payload = generate_response_from_spec(spec)
        return jsonify(payload)

    func_name = f"route_{slug.replace('-', '_')}"
    handler.__name__ = func_name
    route_rule = endpoint if endpoint.startswith('/') else f'/{endpoint}'
    app.add_url_rule(route_rule, view_func=handler, methods=[method])


def generate_response_from_spec(spec: Dict[str, Any]):
    payload = {'message': 'API ready', 'endpoint': spec['endpoint'], 'method': spec['http_method'].upper(), 'response': spec['success_response']}
    return payload


def generate_api_spec(prompt: str) -> Dict[str, Any]:
    prompt_lower = prompt.lower()
    endpoint = infer_endpoint(prompt_lower)
    method = infer_method(prompt_lower)
    api_name = infer_api_name(prompt_lower)
    category = classify_prompt(prompt_lower)

    ai_payload = None
    provider = os.environ.get('AI_PROVIDER', 'openai').strip().lower()
    api_key = os.environ.get('OPENAI_API_KEY') or os.environ.get('AI_API_KEY')
    base_url = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')

    if api_key and provider in {'openai', 'openai-compatible'}:
        try:
            ai_payload = call_ai_provider(prompt, api_key, base_url, provider)
        except Exception:
            ai_payload = None

    if ai_payload:
        return ai_payload

    spec = {
        'api_name': api_name,
        'description': f"Generated REST API for {api_name}.",
        'endpoint': endpoint,
        'http_method': method,
        'request_headers': [{'name': 'Content-Type', 'required': True, 'type': 'string', 'description': 'application/json'}],
        'authentication': {'type': 'none', 'required': False},
        'query_parameters': [],
        'request_body_schema': {'type': 'object', 'properties': {}},
        'response_schema': {'type': 'object', 'properties': {'message': {'type': 'string'}}},
        'success_response': {'message': 'success'},
        'error_responses': [{'status_code': 400, 'message': 'Bad request'}],
        'status_codes': [200, 400, 401, 404, 500],
        'example_curl': f"curl -X {method} http://localhost:5000{endpoint}",
        'javascript_example': f"fetch('http://localhost:5000{endpoint}', {{ method: '{method}' }})",
        'python_example': f"import requests\nrequests.{method.lower()}('http://localhost:5000{endpoint}')",
        'tags': [category],
        'version': 'v1',
        'external_api': infer_external_api(prompt_lower),
        'mock_mode': False,
    }
    if 'weather' in prompt_lower:
        spec.update({
            'description': 'Weather API that returns weather information for a city.',
            'query_parameters': [{'name': 'city', 'required': True, 'type': 'string', 'description': 'City name'}],
            'request_body_schema': {'type': 'object', 'properties': {}},
            'response_schema': {'type': 'object', 'properties': {'city': {'type': 'string'}, 'temperature': {'type': 'string'}, 'condition': {'type': 'string'}}},
            'success_response': {'city': 'Dhaka', 'temperature': '32°C', 'condition': 'Sunny'}
        })
    elif 'book' in prompt_lower:
        spec.update({
            'description': 'Book API that returns book details.',
            'query_parameters': [{'name': 'title', 'required': False, 'type': 'string', 'description': 'Book title'}],
            'response_schema': {'type': 'object', 'properties': {'title': {'type': 'string'}, 'author': {'type': 'string'}, 'year': {'type': 'integer'}}},
            'success_response': {'title': 'The Alchemist', 'author': 'Paulo Coelho', 'year': 1988}
        })
    elif 'student' in prompt_lower or 'crud' in prompt_lower:
        spec.update({
            'description': 'CRUD API for managing students.',
            'request_body_schema': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'email': {'type': 'string'}, 'age': {'type': 'integer'}}},
            'response_schema': {'type': 'object', 'properties': {'id': {'type': 'integer'}, 'name': {'type': 'string'}, 'email': {'type': 'string'}}},
            'success_response': {'id': 1, 'name': 'Aisha', 'email': 'aisha@example.com'}
        })
    elif 'crypto' in prompt_lower or 'currency' in prompt_lower:
        spec.update({
            'description': 'Real-time price API for cryptocurrencies or currencies.',
            'query_parameters': [{'name': 'symbol', 'required': True, 'type': 'string', 'description': 'Asset symbol'}],
            'response_schema': {'type': 'object', 'properties': {'symbol': {'type': 'string'}, 'price': {'type': 'number'}}},
            'success_response': {'symbol': 'BTC', 'price': 67234.12}
        })
    elif 'auth' in prompt_lower or 'jwt' in prompt_lower:
        spec.update({
            'description': 'Authentication API using JWT tokens.',
            'authentication': {'type': 'jwt', 'required': True},
            'request_body_schema': {'type': 'object', 'properties': {'email': {'type': 'string'}, 'password': {'type': 'string'}}},
            'response_schema': {'type': 'object', 'properties': {'token': {'type': 'string'}, 'message': {'type': 'string'}}},
            'success_response': {'token': 'jwt.token.value', 'message': 'Authentication successful'}
        })
    elif 'upload' in prompt_lower or 'file' in prompt_lower:
        spec.update({
            'description': 'File upload API.',
            'request_body_schema': {'type': 'object', 'properties': {'file_name': {'type': 'string'}, 'size': {'type': 'integer'}}},
            'response_schema': {'type': 'object', 'properties': {'file_name': {'type': 'string'}, 'uploaded': {'type': 'boolean'}}},
            'success_response': {'file_name': 'document.pdf', 'uploaded': True}
        })
    elif 'task' in prompt_lower or 'todo' in prompt_lower:
        spec.update({
            'description': 'Task manager API.',
            'request_body_schema': {'type': 'object', 'properties': {'title': {'type': 'string'}, 'completed': {'type': 'boolean'}}},
            'response_schema': {'type': 'object', 'properties': {'id': {'type': 'integer'}, 'title': {'type': 'string'}, 'completed': {'type': 'boolean'}}},
            'success_response': {'id': 1, 'title': 'Ship MVP', 'completed': False}
        })
    elif 'chat' in prompt_lower:
        spec.update({
            'description': 'AI chat API.',
            'request_body_schema': {'type': 'object', 'properties': {'message': {'type': 'string'}}},
            'response_schema': {'type': 'object', 'properties': {'reply': {'type': 'string'}}},
            'success_response': {'reply': 'Hello! I am your AI assistant.'}
        })
    return spec


def call_ai_provider(prompt: str, api_key: str, base_url: str, provider: str) -> Optional[Dict[str, Any]]:
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    payload = {
        'model': os.environ.get('AI_MODEL', 'gpt-4o-mini'),
        'messages': [
            {'role': 'system', 'content': 'You are an API design assistant. Return valid JSON only with keys: api_name, description, endpoint, http_method, authentication, query_parameters, request_body_schema, response_schema, success_response, error_responses, status_codes, example_curl, javascript_example, python_example, tags, version, external_api, mock_mode.'},
            {'role': 'user', 'content': f'Generate a REST API spec for this request: {prompt}'}
        ],
        'temperature': 0.2,
    }
    response = requests.post(f'{base_url}/chat/completions', headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    content = data['choices'][0]['message']['content']
    cleaned = content.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    parsed = json.loads(cleaned)
    parsed.setdefault('mock_mode', False)
    parsed.setdefault('version', 'v1')
    return parsed


def infer_endpoint(prompt: str) -> str:
    if 'weather' in prompt:
        return '/api/weather'
    if 'book' in prompt:
        return '/api/books'
    if 'student' in prompt or 'crud' in prompt:
        return '/api/students'
    if 'crypto' in prompt or 'currency' in prompt:
        return '/api/crypto'
    if 'auth' in prompt or 'jwt' in prompt:
        return '/api/auth/login'
    if 'upload' in prompt or 'file' in prompt:
        return '/api/upload'
    if 'task' in prompt or 'todo' in prompt:
        return '/api/todos'
    if 'chat' in prompt:
        return '/api/chat'
    return '/api/hello'


def infer_method(prompt: str) -> str:
    if 'create' in prompt or 'build' in prompt or 'make' in prompt:
        return 'POST'
    return 'GET'


def infer_api_name(prompt: str) -> str:
    if 'weather' in prompt:
        return 'Weather API'
    if 'book' in prompt:
        return 'Books API'
    if 'student' in prompt or 'crud' in prompt:
        return 'Students API'
    if 'crypto' in prompt or 'currency' in prompt:
        return 'Crypto Price API'
    if 'auth' in prompt or 'jwt' in prompt:
        return 'Authentication API'
    if 'upload' in prompt or 'file' in prompt:
        return 'File Upload API'
    if 'task' in prompt or 'todo' in prompt:
        return 'Task Manager API'
    if 'chat' in prompt:
        return 'AI Chat API'
    return 'Generated API'


def classify_prompt(prompt: str) -> str:
    if 'weather' in prompt:
        return 'weather'
    if 'book' in prompt:
        return 'books'
    if 'student' in prompt or 'crud' in prompt:
        return 'students'
    if 'crypto' in prompt or 'currency' in prompt:
        return 'crypto'
    if 'auth' in prompt or 'jwt' in prompt:
        return 'auth'
    if 'upload' in prompt or 'file' in prompt:
        return 'upload'
    if 'task' in prompt or 'todo' in prompt:
        return 'todo'
    if 'chat' in prompt:
        return 'chat'
    return 'general'


def infer_external_api(prompt: str) -> Optional[str]:
    if 'weather' in prompt:
        return 'OpenWeatherMap API'
    if 'crypto' in prompt or 'currency' in prompt:
        return 'CoinGecko API'
    if 'news' in prompt:
        return 'News API'
    return None


def slugify(endpoint: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', endpoint.lower()).strip('-')
    return slug or 'api'


OPENING_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>API Maker Pro</title>
  <style>
    body{font-family:Inter,Segoe UI,sans-serif;background:#07111f;color:#f8fafc;margin:0;padding:0;} .wrap{max-width:1100px;margin:0 auto;padding:24px;} .card{background:#101c30;padding:24px;border-radius:20px;box-shadow:0 16px 40px rgba(0,0,0,.25);} .btn{display:inline-block;background:linear-gradient(90deg,#5eead4,#7c3aed);color:white;padding:12px 16px;border:none;border-radius:999px;cursor:pointer;text-decoration:none;font-weight:700;} .muted{color:#9fb0c9;} input{width:100%;padding:12px 14px;border-radius:12px;border:1px solid #26344a;background:#16253e;color:white;margin-top:8px;} .msg{margin-top:10px;font-weight:600;} .msg.error{color:#ff6b6b;} .msg.success{color:#34d399;} .grid{display:grid;gap:16px;grid-template-columns:1fr 1fr;}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Build production-ready APIs with AI</h1>
      <p class="muted">Login, describe the API you want, and the platform will generate a real REST endpoint, schema, docs, and examples.</p>
      <form id="authForm">
        <input id="email" placeholder="Email" />
        <input id="password" type="password" placeholder="Password" />
        <input id="name" placeholder="Name (optional for login)" />
        <br /><br />
        <div style="display:flex;gap:12px;flex-wrap:wrap;">
          <button class="btn" type="submit" data-mode="login">Login</button>
          <button class="btn" type="submit" data-mode="signup">Sign up</button>
        </div>
        <div id="msg" class="msg"></div>
      </form>
    </div>
  </div>
  <script>
    const form = document.getElementById('authForm');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const button = e.submitter || document.querySelector('#authForm button[type="submit"]');
      const mode = button?.dataset.mode || 'login';
      const email = document.getElementById('email').value.trim();
      const password = document.getElementById('password').value.trim();
      const name = document.getElementById('name').value.trim();
      const msg = document.getElementById('msg');
      try {
        const res = await fetch('/api/auth', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({mode, email, password, name: name || email})});
        const data = await res.json();
        msg.textContent = data.message;
        if (res.ok) {
          window.location.assign('/plans');
        }
      } catch (error) {
        msg.textContent = 'Connection failed. Please refresh and try again.';
      }
    });
  </script>
</body>
</html>
"""

PLANS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" /><title>Plans</title><style>body{font-family:Inter,Segoe UI,sans-serif;background:#07111f;color:#f8fafc;margin:0;padding:0;} .wrap{max-width:1100px;margin:0 auto;padding:24px;} .card{background:#101c30;padding:24px;border-radius:20px;margin-bottom:16px;} .btn{background:linear-gradient(90deg,#5eead4,#7c3aed);color:white;padding:12px 16px;border:none;border-radius:999px;cursor:pointer;text-decoration:none;font-weight:700;} .muted{color:#9fb0c9;}</style></head>
<body>
  <div class="wrap">
    <div class="card"><h1>Choose a plan</h1><p class="muted">Free gives localhost-only endpoints. Premium unlocks public publishing, custom domains, analytics, and API keys.</p><button class="btn" onclick="activate('free')">Free</button> <button class="btn" onclick="activate('premium')">Premium</button></div>
  </div>
  <script>
    async function activate(plan){
      const res = await fetch('/api/plan', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({plan})});
      const data = await res.json();
      alert(data.message);
      if (res.ok) window.location.href='/dashboard';
    }
  </script>
</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" /><title>Dashboard</title><style>body{font-family:Inter,Segoe UI,sans-serif;background:#07111f;color:#f8fafc;margin:0;padding:0;} .wrap{max-width:1300px;margin:0 auto;padding:24px;} .card{background:#101c30;padding:20px;border-radius:20px;margin-bottom:16px;} .btn{background:linear-gradient(90deg,#5eead4,#7c3aed);color:white;padding:10px 14px;border:none;border-radius:999px;cursor:pointer;text-decoration:none;font-weight:700;} textarea{width:100%;min-height:100px;padding:12px;border-radius:12px;border:1px solid #26344a;background:#16253e;color:white;} .muted{color:#9fb0c9;} .code{background:#020617;padding:12px;border-radius:12px;color:#cbd5e1;white-space:pre-wrap;font-family:Consolas,monospace;}</style></head>
<body>
  <div class="wrap">
    <div class="card"><h1>AI API Builder Dashboard</h1><p class="muted">Describe the API you want and it will be generated with a real route and schema.</p><textarea id="prompt"></textarea><br /><br /><button class="btn" onclick="generate()">Generate API</button></div>
    <div class="card"><h3>Generated API</h3><div id="result" class="code">Waiting...</div></div>
    <div class="card"><h3>My APIs</h3><div id="list" class="code"></div></div>
  </div>
  <script>
    async function generate(){
      const prompt = document.getElementById('prompt').value.trim();
      const resultBox = document.getElementById('result');
      if (!prompt) {
        resultBox.textContent = 'Please enter a prompt first.';
        return;
      }
      resultBox.textContent = 'Generating your API...';
      try {
        const res = await fetch('/api/build', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({prompt})});
        const data = await res.json();
        resultBox.textContent = JSON.stringify(data, null, 2);
        loadList();
      } catch (error) {
        resultBox.textContent = 'Generation failed. Please refresh and try again.';
      }
    }
    async function loadList(){
      try {
        const res = await fetch('/api/list');
        const data = await res.json();
        document.getElementById('list').textContent = JSON.stringify(data, null, 2);
      } catch (error) {
        document.getElementById('list').textContent = 'Unable to load your APIs.';
      }
    }
    loadList();
  </script>
</body>
</html>
"""


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
