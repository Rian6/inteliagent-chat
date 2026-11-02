"""Authenticated chat transport. Identity is derived only from a verified JWT."""
import os
import time
import threading
import uuid
import io
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone
import jwt
from flask import Flask, jsonify, request, g, send_file
from chat.media import media_directory, detect_mime, public_attachment, MAX_IMAGE_BYTES, MAX_AUDIO_BYTES
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, disconnect
from chat.repository import ChatRepository
from chat.security import decode_token

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_AUDIO_BYTES + 1024 * 1024
origins = os.environ.get('CORS_ORIGINS', 'http://localhost:5173').split(',')
CORS(app, origins=origins, allow_headers=['Authorization', 'Content-Type', 'Range'], expose_headers=['Content-Range','Accept-Ranges'])
socketio = SocketIO(app, cors_allowed_origins=origins, async_mode='threading', max_http_buffer_size=32768, allow_upgrades=False)

@socketio.on_error_default
def socket_error(error):
    app.logger.exception('Chat event failed')
    return {'success': False, 'error': 'Serviço temporariamente indisponível'}
repository = ChatRepository()
_rates = {}
_rate_lock = threading.Lock()


def active(claims):
    return repository.active(claims)

def authenticated(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            g.identity = decode_token(request.headers.get('Authorization', ''))
            if not active(g.identity):
                return jsonify(error='Sessão inválida'), 401
        except (jwt.InvalidTokenError, ValueError, KeyError):
            return jsonify(error='Autenticação necessária'), 401
        return fn(*args, **kwargs)
    return wrapped

@app.get('/push/config')
@authenticated
def push_config():
    return jsonify(webPublicKey=os.environ.get('VAPID_PUBLIC_KEY'),webEnabled=bool(os.environ.get('VAPID_PRIVATE_KEY') and os.environ.get('VAPID_SUBJECT')))

@app.put('/push/devices/<device_id>')
@authenticated
def register_push(device_id):
    from chat.push import PushStore
    try:
        uuid.UUID(device_id)
        data=request.get_json()
        if not isinstance(data,dict) or data.get('userId')!=g.identity['uid']: raise ValueError('Conta inválida')
        PushStore(repository).register(g.identity,device_id,data)
    except (ValueError,TypeError,AttributeError): return jsonify(error='Dispositivo de notificações inválido'),400
    return jsonify(success=True),200

@app.delete('/push/devices/<device_id>')
@authenticated
def remove_push(device_id):
    from chat.push import PushStore
    try: uuid.UUID(device_id)
    except ValueError: return jsonify(error='Dispositivo inválido'),400
    PushStore(repository).remove(g.identity,device_id)
    return jsonify(success=True)

@app.errorhandler(Exception)
def unexpected(error):
    from werkzeug.exceptions import HTTPException
    if isinstance(error, HTTPException):
        return jsonify(error=error.name), error.code
    app.logger.exception('Chat request failed')
    return jsonify(error='Serviço temporariamente indisponível'), 503

@app.get('/users')
@authenticated
def users():
    return jsonify(repository.users(g.identity['cede'],g.identity['uid']))

@app.get('/users/<int:user_id>/photo')
@authenticated
def contact_photo(user_id):
    row=repository.profile_photo(g.identity['cede'],user_id)
    if not row or row['image_type'] not in ('image/jpeg','image/png'):
        return jsonify(error='Foto não encontrada'),404
    response=send_file(io.BytesIO(row['image_data']),mimetype=row['image_type'])
    response.headers['Cache-Control']='private, no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return response

@app.get('/health')
def health():
    return jsonify(status='UP')

@app.post('/attachments')
@authenticated
def upload_attachment():
    try:
        recipient=int(request.form['recipient'])
        if not allowed(g.identity,recipient): return jsonify(error='Contato inacessível'),403
        if not consume_rate(g.identity['uid']): return jsonify(error='Muitos envios. Aguarde um momento.'),429
        upload=request.files['file']
        data=upload.stream.read(MAX_AUDIO_BYTES+1)
        mime=detect_mime(data)
        limit=MAX_IMAGE_BYTES if mime.startswith('image/') else MAX_AUDIO_BYTES
        if not data or len(data)>limit: return jsonify(error='Arquivo excede o limite: imagens até 8 MB e áudio até 12 MB.'),413
        duration=request.form.get('durationMillis')
        duration=int(duration) if duration else None
        if duration is not None and not 0<duration<=180000: raise ValueError('O áudio deve ter até 3 minutos.')
        attachment_id=str(uuid.uuid4())
        name=Path((upload.filename or 'Anexo').replace('\\','/')).name[:160]
    except (KeyError,ValueError,TypeError) as error:
        return jsonify(error=str(error) if isinstance(error,ValueError) else 'Anexo inválido'),400
    path=media_directory()/attachment_id
    try:
        with path.open('xb') as file: file.write(data)
        repository.create_attachment((attachment_id,g.identity['uid'],recipient,g.identity['cede'],mime,len(data),name,duration))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return jsonify(public_attachment({'id':attachment_id,'mime':mime,'size_bytes':len(data),'original_name':name,'duration_ms':duration})),201

@app.get('/attachments/<attachment_id>')
@authenticated
def download_attachment(attachment_id):
    try: uuid.UUID(attachment_id)
    except ValueError: return jsonify(error='Anexo não encontrado'),404
    row=repository.attachment(attachment_id,g.identity['uid'],g.identity['cede'])
    if not row or not (media_directory()/attachment_id).is_file(): return jsonify(error='Anexo não encontrado'),404
    response=send_file(media_directory()/attachment_id,mimetype=row['mime'],download_name=row['original_name'],conditional=True)
    response.headers['Cache-Control']='private, max-age=60'
    response.headers['X-Content-Type-Options']='nosniff'
    return response

identities = {}
@socketio.on('connect')
def connect(auth):
    try:
        identity = decode_token((auth or {}).get('token', ''))
        if not active(identity):
            return False
    except (jwt.InvalidTokenError, ValueError, KeyError):
        return False
    if sum(1 for item in identities.values() if item['uid'] == identity['uid']) >= 20:
        return False
    identities[request.sid] = identity
    join_room(f"user:{identity['uid']}")

@socketio.on('disconnect')
def disconnected(reason=None):
    identities.pop(request.sid, None)

def socket_identity():
    identity = identities.get(request.sid)
    if not identity or identity['exp'] <= time.time() or not active(identity):
        disconnect()
        return None
    return identity

def allowed(identity, recipient):
    return repository.can_contact(identity['cede'], recipient)

def consume_rate(uid):
    now = time.monotonic()
    with _rate_lock:
        for key in list(_rates):
            if _rates[key][0] + 60 < now:
                del _rates[key]
        start, count = _rates.get(uid, (now, 0))
        _rates[uid] = (start, count + 1)
        return count < 60

@socketio.on('send_message')
def send_message(data):
    identity = socket_identity()
    if not identity:
        return {'success': False, 'error': 'Sessão expirada'}
    if not consume_rate(identity['uid']):
        return {'success': False, 'error': 'Muitas mensagens'}
    try:
        recipient = int(data['recipient'])
        text = data.get('text','').strip()
        attachment_id=data.get('attachmentId') or None
        client_id=data.get('clientId') or None
        if attachment_id: uuid.UUID(attachment_id)
        if client_id: uuid.UUID(client_id)
        if not isinstance(text, str) or len(text)>4000 or not (text or attachment_id) or not allowed(identity, recipient):
            raise ValueError()
    except (TypeError, KeyError, ValueError, AttributeError):
        return {'success': False, 'error': 'Mensagem inválida'}
    try:
        message_id = repository.save_message(identity,recipient,text,attachment_id,client_id) if attachment_id or client_id else repository.save(identity['uid'], recipient, text)
    except ValueError as error: return {'success':False,'error':str(error)}
    message = {'id': message_id, 'sender': identity['uid'], 'recipient': recipient, 'text': text,
               'timestamp': datetime.now(timezone.utc).isoformat(),
               'attachment': public_attachment(repository.attachment(attachment_id,identity['uid'],identity['cede'])) if attachment_id else None}
    participants = {identity['uid'], recipient}
    # Revalidate listeners before delivery, including idle sockets whose token was revoked.
    for sid, listener in list(identities.items()):
        if listener['uid'] in participants and (listener['exp'] <= time.time() or not active(listener)):
            socketio.server.disconnect(sid)
    for uid in participants:
        emit('receive_message', message, to=f'user:{uid}')
    return {'success': True, 'id': message_id}

@socketio.on('load_messages')
def load_messages(data):
    identity = socket_identity()
    if not identity:
        return {'success': False}
    try:
        recipient = int(data['recipient'])
        before = int(data.get('before', 9223372036854775807))
        if not allowed(identity, recipient):
            raise ValueError()
    except (TypeError, KeyError, ValueError):
        return {'success': False, 'error': 'Conversa inválida'}
    messages = repository.history(identity['uid'], recipient, before)
    for message in messages:
        message['timestamp'] = message['timestamp'].replace(tzinfo=timezone.utc).isoformat()
    emit('all_messages', messages)
    return {'success': True, 'hasMore': len(messages) == 100}

if __name__ == '__main__':
    from waitress import serve
    from chat.push import start_worker
    start_worker(repository,app.logger)
    serve(app, host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '5000')), threads=32)
