"""Durable private-message push delivery. Provider calls never block message sends."""
import base64
import hashlib
import json
import os
import re
import threading
import urllib.request
from urllib.parse import urlparse

EXPO_SEND='https://exp.host/--/api/v2/push/send'
EXPO_RECEIPTS='https://exp.host/--/api/v2/push/getReceipts'

def validate_subscription(data):
    kind=data.get('kind')
    if kind=='expo':
        value=data.get('token','')
        if not isinstance(value,str) or not re.fullmatch(r'(?:ExponentPushToken|ExpoPushToken)\[[\w-]{10,200}\]',value):
            raise ValueError('Dispositivo inválido')
    elif kind=='web':
        value=data.get('subscription')
        if not isinstance(value,dict): raise ValueError('Assinatura inválida')
        endpoint=urlparse(value.get('endpoint',''))
        host=endpoint.hostname or ''
        allowed=host in ('fcm.googleapis.com','updates.push.services.mozilla.com','web.push.apple.com') or host.endswith('.notify.windows.com')
        if endpoint.scheme!='https' or not allowed or endpoint.port not in (None,443) or endpoint.username:
            raise ValueError('Provedor de notificações inválido')
        for key,size in [('p256dh',65),('auth',16)]:
            raw=value.get('keys',{}).get(key,'')
            if not isinstance(raw,str) or len(raw)>200: raise ValueError('Chave inválida')
            try: decoded=base64.urlsafe_b64decode(raw+'='*(-len(raw)%4))
            except Exception: raise ValueError('Chave inválida')
            if len(decoded)!=size: raise ValueError('Chave inválida')
        value={'endpoint':value['endpoint'],'keys':{k:value['keys'][k] for k in ('p256dh','auth')}}
    else: raise ValueError('Tipo de dispositivo inválido')
    serialized=json.dumps(value,sort_keys=True,separators=(',',':'))
    if len(serialized)>8192: raise ValueError('Assinatura muito grande')
    # A provider endpoint/token belongs to one logged-in account at a time.
    address=value if kind=='expo' else value['endpoint']
    return kind,serialized,hashlib.sha256(address.encode()).hexdigest()

def enqueue(cursor,message_id,recipient):
    cursor.execute("""INSERT IGNORE INTO chat_push_outbox(message_id,device_id)
        SELECT %s,d.id FROM chat_push_devices d JOIN usuario u ON u.id=d.user_id
        WHERE d.user_id=%s AND d.active=1 AND d.token_version=u.token_version
        AND d.id_cede=u.id_cede AND u.situacao=1 AND u.situacao_aprovacao=1""",(message_id,recipient))

class PushStore:
    def __init__(self,repository): self.repository=repository
    def register(self,identity,device_id,data):
        kind,serialized,digest=validate_subscription(data)
        with self.repository.connection() as (_,cur):
            # Retire the same token on any previous installation/account.
            cur.execute('DELETE FROM chat_push_devices WHERE token_hash=%s AND id<>%s',(digest,device_id))
            cur.execute("""INSERT INTO chat_push_devices(id,user_id,id_cede,token_version,kind,token_hash,subscription,active)
                VALUES (%s,%s,%s,%s,%s,%s,%s,1) ON DUPLICATE KEY UPDATE
                user_id=VALUES(user_id),id_cede=VALUES(id_cede),token_version=VALUES(token_version),
                kind=VALUES(kind),token_hash=VALUES(token_hash),subscription=VALUES(subscription),active=1""",
                (device_id,identity['uid'],identity['cede'],identity['ver'],kind,digest,serialized))
    def remove(self,identity,device_id):
        with self.repository.connection() as (_,cur):
            cur.execute('UPDATE chat_push_devices SET active=0 WHERE id=%s AND user_id=%s',(device_id,identity['uid']))
    def claim(self):
        with self.repository.connection() as (_,cur):
            cur.execute("""SELECT * FROM chat_push_outbox
                WHERE created_at>DATE_SUB(NOW(6),INTERVAL 24 HOUR) AND attempts<6
                AND ((state IN ('PENDING','RECEIPT') AND next_attempt_at<=NOW(6))
                OR (state='SENDING' AND lease_until<NOW(6)))
                ORDER BY next_attempt_at LIMIT 1 FOR UPDATE SKIP LOCKED""")
            row=cur.fetchone()
            if row:
                cur.execute("UPDATE chat_push_outbox SET state='SENDING',lease_until=DATE_ADD(NOW(6),INTERVAL 60 SECOND),attempts=attempts+1 WHERE message_id=%s AND device_id=%s",(row['message_id'],row['device_id']))
                cur.execute("""SELECT d.kind,d.subscription,d.token_hash,d.user_id,d.token_version,d.active,
                    u.token_version AS current_version,u.situacao,u.situacao_aprovacao,
                    m.sender,m.recipient,s.nome AS sender_name
                    FROM chat_push_devices d JOIN usuario u ON u.id=d.user_id
                    JOIN messages m ON m.id=%s JOIN usuario s ON s.id=m.sender WHERE d.id=%s""",(row['message_id'],row['device_id']))
                row.update(cur.fetchone())
            return row
    def finish(self,row,state,receipt=None,disable=False):
        with self.repository.connection() as (_,cur):
            cur.execute("""UPDATE chat_push_outbox SET state=%s,receipt_id=%s,lease_until=NULL,
                next_attempt_at=DATE_ADD(NOW(6),INTERVAL %s SECOND) WHERE message_id=%s AND device_id=%s""",
                (state,receipt,900 if state=='RECEIPT' else min(900,15*2**row['attempts']),row['message_id'],row['device_id']))
            if disable: cur.execute('UPDATE chat_push_devices SET active=0 WHERE id=%s AND token_hash=%s',(row['device_id'],row['token_hash']))

def expo_request(url,payload):
    headers={'Content-Type':'application/json','Accept':'application/json'}
    if os.getenv('EXPO_ACCESS_TOKEN'): headers['Authorization']='Bearer '+os.environ['EXPO_ACCESS_TOKEN']
    request=urllib.request.Request(url,json.dumps(payload).encode(),headers,method='POST')
    with urllib.request.urlopen(request,timeout=10) as response: return json.load(response)

def deliver(row):
    value=json.loads(row['subscription'])
    data={'senderId':row['sender'],'recipientId':row['recipient'],'messageId':row['message_id'],'senderName':row['sender_name']}
    # Keep message text and attachment URLs off the lock screen/provider.
    payload={'title':'Inteliagent','body':'Você recebeu uma nova mensagem.','data':data}
    if row['kind']=='expo':
        if row['receipt_id']:
            result=expo_request(EXPO_RECEIPTS,{'ids':[row['receipt_id']]}).get('data',{}).get(row['receipt_id'])
            if not result: return 'RECEIPT',row['receipt_id'],False
        else:
            result=expo_request(EXPO_SEND,{'to':value,**payload,'sound':'default','channelId':'messages','priority':'high','ttl':86400}).get('data',{})
        if result.get('status')=='ok':
            return ('SENT',None,False) if row['receipt_id'] else ('RECEIPT',result['id'],False)
        error=result.get('details',{}).get('error')
        if error=='DeviceNotRegistered': return 'FAILED',None,True
        if error in ('InvalidCredentials','MessageTooBig','MismatchSenderId'): return 'FAILED',None,False
        return 'PENDING',None,False
    from pywebpush import webpush,WebPushException
    try:
        webpush(subscription_info=value,data=json.dumps(payload),vapid_private_key=os.environ['VAPID_PRIVATE_KEY'],
                vapid_claims={'sub':os.environ['VAPID_SUBJECT']},ttl=86400,timeout=10)
    except WebPushException as error:
        if error.response is not None and error.response.status_code in (404,410): return 'FAILED',None,True
        raise
    return 'SENT',None,False

def process_one(store):
    row=store.claim()
    if not row: return False
    if not row['active'] or row['user_id']!=row['recipient'] or row['token_version']!=row['current_version'] or row['situacao']!=1 or row['situacao_aprovacao']!=1:
        store.finish(row,'SKIPPED');return True
    try: state,receipt,disable=deliver(row)
    except Exception:
        store.finish(row,'FAILED' if row['attempts']>=5 else ('RECEIPT' if row['receipt_id'] else 'PENDING'),row['receipt_id'])
    else:
        if state in ('PENDING','RECEIPT') and row['attempts']>=5: state='FAILED'
        store.finish(row,state,receipt,disable)
    return True

def start_worker(repository,logger):
    def run():
        store=PushStore(repository)
        while True:
            try: worked=process_one(store)
            except Exception:
                logger.exception('Push worker temporarily unavailable');worked=False
            if not worked: threading.Event().wait(5)
    threading.Thread(target=run,name='chat-push',daemon=True).start()
