"""Private chat attachments. Files are addressed by generated IDs, never by user paths."""
from pathlib import Path
import os

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_AUDIO_BYTES = 12 * 1024 * 1024

def media_directory():
    path = Path(os.environ.get('CHAT_UPLOAD_DIR', str(Path(__file__).resolve().parents[2] / 'data' / 'chat-media')))
    path.mkdir(parents=True, exist_ok=True)
    return path

def detect_mime(data):
    if data.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP': return 'image/webp'
    if data[:4] == b'RIFF' and data[8:12] == b'WAVE': return 'audio/wav'
    if data.startswith(b'OggS'): return 'audio/ogg'
    if data.startswith(b'ID3') or len(data)>1 and data[0] == 255 and data[1] & 224 == 224: return 'audio/mpeg'
    if len(data)>12 and data[4:8] == b'ftyp' and data[8:12] in (b'M4A ', b'isom', b'mp42', b'iso2'): return 'audio/mp4'
    if data.startswith(b'\x1aE\xdf\xa3') and b'webm' in data[:256]: return 'audio/webm'
    raise ValueError('Selecione uma imagem JPG, PNG ou WebP, ou uma gravação de áudio compatível.')

def public_attachment(row):
    if not row: return None
    return {'id':row['id'], 'kind':'image' if row['mime'].startswith('image/') else 'audio',
            'mime':row['mime'], 'size':row['size_bytes'], 'name':row['original_name'],
            'durationMillis':row.get('duration_ms'), 'url':'/attachments/'+row['id']}
