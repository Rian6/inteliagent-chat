"""Repository boundary: bounded, parameterized queries and pooled connections."""
import os
import threading
from contextlib import contextmanager
from mysql.connector.pooling import MySQLConnectionPool

class ChatRepository:
    def __init__(self):
        self._pool = None
        self._lock = threading.Lock()

    @contextmanager
    def connection(self):
        with self._lock:
            if self._pool is None:
                self._pool = MySQLConnectionPool(pool_name='chat', pool_size=5,
                    host=os.environ.get('DB_HOST', 'localhost'), port=int(os.environ.get('DB_PORT', '3307')),
                    database=os.environ.get('DB_NAME', 'inteliagent'), user=os.environ.get('DB_USER', 'inteliagent'),
                    password=os.environ['DB_PASSWORD'], connection_timeout=5, time_zone='+00:00')
        conn = self._pool.get_connection()
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True)
            yield conn, cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if cursor is not None: cursor.close()
            conn.close()

    def active(self, identity):
        with self.connection() as (_, cur):
            cur.execute('SELECT id FROM usuario WHERE id=%s AND id_cede=%s AND token_version=%s AND situacao=1 AND situacao_aprovacao=1',
                        (identity['uid'], identity['cede'], identity['ver']))
            return cur.fetchone() is not None

    def users(self, cede, user=None):
        with self.connection() as (_, cur):
            if user is not None:
                cur.execute('''SELECT u.id,u.nome,u.id_imagem_perfil_usuario AS photoId,m.text AS lastMessage,m.timestamp AS lastMessageAt,a.mime AS lastAttachmentMime
                    FROM usuario u
                    LEFT JOIN messages m ON m.id=GREATEST(
                      COALESCE((SELECT MAX(id) FROM messages WHERE sender=%s AND recipient=u.id),0),
                      COALESCE((SELECT MAX(id) FROM messages WHERE sender=u.id AND recipient=%s),0))
                    LEFT JOIN chat_attachments a ON a.id=m.attachment_id
                    WHERE u.id_cede=%s AND u.situacao=1 AND u.situacao_aprovacao=1
                    ORDER BY m.timestamp DESC,u.nome LIMIT 500''',(user,user,cede))
                rows=cur.fetchall()
                from datetime import timezone
                for row in rows:
                    if row['lastMessageAt']:row['lastMessageAt']=row['lastMessageAt'].replace(tzinfo=timezone.utc).isoformat()
                    photo_id=row.pop('photoId')
                    row['photoUrl']=f"/users/{row['id']}/photo?v={photo_id}" if photo_id else None
                return rows
            cur.execute('SELECT id, nome FROM usuario WHERE id_cede=%s AND situacao=1 AND situacao_aprovacao=1 ORDER BY nome LIMIT 500', (cede,))
            return cur.fetchall()

    def can_contact(self, cede, recipient):
        with self.connection() as (_, cur):
            cur.execute('SELECT id FROM usuario WHERE id=%s AND id_cede=%s AND situacao=1 AND situacao_aprovacao=1', (recipient, cede))
            return cur.fetchone() is not None

    def profile_photo(self, cede, user):
        with self.connection() as (_, cur):
            cur.execute('''SELECT i.image_data,i.image_type FROM usuario u
                JOIN imagem_perfil_usuario i ON i.id=u.id_imagem_perfil_usuario
                WHERE u.id=%s AND u.id_cede=%s AND u.situacao=1 AND u.situacao_aprovacao=1''', (user, cede))
            return cur.fetchone()

    def save(self, sender, recipient, text):
        with self.connection() as (_, cur):
            cur.execute('INSERT INTO messages (sender,recipient,text,timestamp) VALUES (%s,%s,%s,UTC_TIMESTAMP(6))', (sender,recipient,text))
            return cur.lastrowid

    def history(self, user, recipient, before):
        with self.connection() as (_, cur):
            cur.execute('SELECT id,sender,recipient,text,timestamp,attachment_id FROM messages WHERE id < %s AND ((sender=%s AND recipient=%s) OR (sender=%s AND recipient=%s)) ORDER BY id DESC LIMIT 100',
                        (before,user,recipient,recipient,user))
            rows=list(reversed(cur.fetchall()))
            ids=[row['attachment_id'] for row in rows if row.get('attachment_id')]
            attachments={}
            if ids:
                cur.execute('SELECT * FROM chat_attachments WHERE id IN ('+','.join(['%s']*len(ids))+')',tuple(ids))
                attachments={row['id']:row for row in cur.fetchall()}
            from chat.media import public_attachment
            for row in rows:
                row['attachment']=public_attachment(attachments.get(row.pop('attachment_id',None)))
            return rows

    def create_attachment(self, values):
        with self.connection() as (_, cur):
            cur.execute('INSERT INTO chat_attachments(id,sender,recipient,id_cede,mime,size_bytes,original_name,duration_ms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)', values)

    def attachment(self, attachment_id, uid, cede):
        with self.connection() as (_, cur):
            cur.execute('SELECT * FROM chat_attachments WHERE id=%s AND id_cede=%s AND (sender=%s OR recipient=%s)',(attachment_id,cede,uid,uid))
            return cur.fetchone()

    def save_message(self, identity, recipient, text, attachment_id, client_id):
        with self.connection() as (_, cur):
            if client_id:
                cur.execute('SELECT id,recipient,text,attachment_id FROM messages WHERE sender=%s AND client_id=%s',(identity['uid'],client_id))
                previous=cur.fetchone()
                if previous:
                    if (previous['recipient'],previous['text'],previous['attachment_id']) != (recipient,text,attachment_id):
                        raise ValueError('Identificador de mensagem reutilizado')
                    return previous['id']
            if attachment_id:
                cur.execute('SELECT id FROM chat_attachments WHERE id=%s AND sender=%s AND recipient=%s AND id_cede=%s',(attachment_id,identity['uid'],recipient,identity['cede']))
                if not cur.fetchone(): raise ValueError('Anexo inacessível')
            cur.execute('INSERT INTO messages(sender,recipient,text,attachment_id,client_id,timestamp) VALUES (%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))',(identity['uid'],recipient,text,attachment_id,client_id))
            return cur.lastrowid
