# Inteliagent Chat

Chat HTTP/Socket.IO autenticado por JWT, com salas privadas por usuário e histórico limitado à conversa. `chat/security.py` valida identidades, `chat/repository.py` encapsula consultas e conexões, e `server.py` adapta o transporte.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
# Exporte as variáveis de .env.example no processo.
.venv\Scripts\python.exe server.py
```

O servidor local escuta em loopback na porta 5000. O runtime Windows usa Waitress e o fallback long polling de Socket.IO. Não execute múltiplas instâncias sem configurar um gerenciador compartilhado de salas.

Os testes integrados e o SMTP que não entrega emails externos ficam no workspace pai. Para eles, instale `requirements-dev.txt` e consulte `../README.md`.


## CI/CD e produção

O workflow `.github/workflows/ci.yml` valida pushes e pull requests. Consulte a [infraestrutura e operação](https://github.com/Rian6/inteliagent-infra). Configure variáveis a partir de `.env.example`; segredos ficam em arquivos privados no servidor e GitHub Secrets. Não publique tokens, senhas ou arquivos de assinatura.
