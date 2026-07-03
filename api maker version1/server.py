from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import re
from pathlib import Path
from urllib import request, parse
from urllib.parse import urlparse
from auth import load_users, save_users, set_session, clear_session, get_user_from_cookie, update_plan

ROOT = Path(__file__).parent


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/':
            self.serve_html('index.html')
            return
        if path == '/plans':
            self.serve_html('plans.html')
            return
        if path == '/dashboard':
            self.serve_html('dashboard.html')
            return
        if path == '/api/me':
            user = get_user_from_cookie(self.headers)
            if not user:
                self.send_json(401, {'message': 'Unauthorized'})
                return
            self.send_json(200, {'user': {'email': user['email'], 'name': user.get('name', ''), 'plan': user.get('plan', 'free')}})
            return
        if path.startswith('/api/'):
            user = get_user_from_cookie(self.headers)
            if not user:
                self.send_json(401, {'message': 'Unauthorized'})
                return
            prompt = self.headers.get('X-Api-Prompt', '')
            payload = self.build_payload(path, prompt)
            self.send_json(200, payload)
            return
        self.send_json(404, {'error': 'Not found'})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/auth':
            length = int(self.headers.get('Content-Length', '0'))
            body = json.loads(self.rfile.read(length).decode())
            users = load_users()
            email = body.get('email', '').strip().lower()
            password = body.get('password', '').strip()
            if not email or not password:
                self.send_json(400, {'message': 'Email and password are required'})
                return
            if body.get('mode') == 'signup':
                name = body.get('name', '').strip()
                if email in users:
                    self.send_json(400, {'message': 'User already exists'})
                    return
                users[email] = {'email': email, 'password': password, 'name': name, 'plan': 'free'}
                save_users(users)
                session_id = set_session({'email': email, 'password': password, 'name': name, 'plan': 'free'})
                self.send_json(200, {'message': 'Account created successfully'}, {'Set-Cookie': f'session_id={session_id}; HttpOnly; Path=/'})
                return
            user = users.get(email)
            if not user or user.get('password') != password:
                self.send_json(401, {'message': 'Invalid credentials'})
                return
            session_id = set_session(user)
            self.send_json(200, {'message': 'Login successful'}, {'Set-Cookie': f'session_id={session_id}; HttpOnly; Path=/'})
            return

        if path == '/api/plan':
            user = get_user_from_cookie(self.headers)
            if not user:
                self.send_json(401, {'message': 'Unauthorized'})
                return
            length = int(self.headers.get('Content-Length', '0'))
            body = json.loads(self.rfile.read(length).decode())
            plan = body.get('plan', 'free')
            payment_phone = body.get('paymentPhone')
            payment_id = body.get('paymentId')
            if plan in ['normal', 'premium'] and not payment_phone and not payment_id:
                self.send_json(400, {'message': 'Please provide bKash payment details'})
                return
            update_plan(user['email'], plan, payment_phone, payment_id)
            self.send_json(200, {'message': f'{plan.title()} plan activated successfully'})
            return

        if path == '/api/build':
            user = get_user_from_cookie(self.headers)
            if not user:
                self.send_json(401, {'message': 'Unauthorized'})
                return
            length = int(self.headers.get('Content-Length', '0'))
            body = json.loads(self.rfile.read(length).decode())
            prompt = body.get('prompt', '').strip()
            if not prompt:
                self.send_json(400, {'message': 'Prompt is required'})
                return
            api_id = f"{user['email'].split('@')[0]}-{len(load_users())}"
            payload = self.build_payload(f"/api/{api_id}", prompt)
            self.send_json(200, {'message': 'API generated', 'id': api_id, 'prompt': prompt, 'response': payload})
            return

        if path == '/api/logout':
            user = get_user_from_cookie(self.headers)
            if user:
                clear_session(user['email'])
            self.send_json(200, {'message': 'Logged out'})
            return

        self.send_json(404, {'error': 'Not found'})

    def build_payload(self, path, prompt):
        text = (prompt or '').strip().lower()
        llm_result = None
        api_key = os.getenv('OPENAI_API_KEY')
        if api_key:
            try:
                llm_result = self.call_openai(prompt)
            except Exception:
                llm_result = None
        if llm_result:
            return {
                'message': 'API ready',
                'endpoint': path,
                'prompt': prompt,
                'llm': 'openai',
                'response': llm_result
            }

        if 'weather' in text or 'temp' in text:
            return {
                'message': 'API ready',
                'endpoint': path,
                'prompt': prompt,
                'llm': 'local',
                'response': {
                    'city': 'Dhaka',
                    'temperature': '32°C',
                    'condition': 'Sunny',
                    'summary': 'Warm and bright weather today.'
                }
            }
        if 'quote' in text or 'inspiration' in text:
            return {
                'message': 'API ready',
                'endpoint': path,
                'prompt': prompt,
                'llm': 'local',
                'response': {
                    'quote': 'Keep going, your next build can be the one that matters.',
                    'author': 'API Maker Pro'
                }
            }
        if 'user' in text or 'profile' in text:
            return {
                'message': 'API ready',
                'endpoint': path,
                'prompt': prompt,
                'llm': 'local',
                'response': {
                    'name': 'Muhtasim',
                    'role': 'Builder',
                    'status': 'Active'
                }
            }
        return {
            'message': 'API ready',
            'endpoint': path,
            'prompt': prompt,
            'llm': 'local',
            'response': {
                'summary': f"Here is a generated response for: {prompt or 'your request'}",
                'status': 'success'
            }
        }

    def call_openai(self, prompt):
        payload = {
            'model': 'gpt-4o-mini',
            'messages': [
                {'role': 'system', 'content': 'You are an API builder assistant. Return concise JSON only.'},
                {'role': 'user', 'content': prompt}
            ],
            'temperature': 0.4,
        }
        data = json.dumps(payload).encode('utf-8')
        req = request.Request(
            'https://api.openai.com/v1/chat/completions',
            data=data,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f"Bearer {os.getenv('OPENAI_API_KEY')}"
            },
            method='POST'
        )
        with request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode('utf-8'))
            content = result['choices'][0]['message']['content']
            try:
                return json.loads(content)
            except Exception:
                return {'summary': content}

    def serve_html(self, filename):
        data = (ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, payload, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', 8000), Handler)
    print('API Maker Pro running at http://localhost:8000')
    server.serve_forever()
