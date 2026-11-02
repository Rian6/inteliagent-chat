import base64
import os
import jwt

def decode_token(token):
    secret = base64.b64decode(os.environ['JWT_SECRET'], validate=True)
    if len(secret) < 32:
        raise ValueError('JWT_SECRET requires at least 32 bytes')
    return jwt.decode(token.removeprefix('Bearer '), secret, algorithms=['HS256'], issuer='inteliagent-auth',
                      options={'require': ['exp', 'iat', 'iss', 'sub', 'uid', 'cede', 'ver']})
