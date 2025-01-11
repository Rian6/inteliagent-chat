from flask import Flask, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import mysql.connector
from datetime import datetime
from mysql.connector import Error

# Iniciar o Flask
app = Flask(__name__)

# Ativar o CORS para permitir acesso do frontend local
CORS(app, origins=["http://localhost:5173"])  # Permitir acesso apenas da origem específica

# Configuração do SocketIO
socketio = SocketIO(app, cors_allowed_origins=["http://localhost:5173"])  # Corrigido para usar uma lista de origens

def get_db_connection():
    try:
        connection = mysql.connector.connect(
            host='localhost',
            port=3307,  # A porta correta conforme sua configuração
            database='inteliagent',
            user='root',
            password='root9844',
            ssl_disabled=True,  # Desabilitar SSL se não estiver usando SSL
            auth_plugin='mysql_native_password'  # Forçar o uso do plugin de autenticação mysql_native_password
        )
        if connection.is_connected():
            print('Conexão bem-sucedida ao banco de dados')
            return connection
    except Error as e:
        print(f"Erro ao conectar ao banco de dados: {e}")
        return None


# Rota para listar todos os usuários
@app.route('/users', methods=['GET'])
def list_users():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute("SELECT id, nome FROM usuario")  # Alterado para id ao invés de uuid
    users = cursor.fetchall()
    connection.close()
    return jsonify(users)

# Função para salvar a mensagem no banco
def save_message(sender, recipient, text):
    connection = get_db_connection()
    cursor = connection.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    query = """
        INSERT INTO messages (sender, recipient, text, timestamp)
        VALUES (%s, %s, %s, %s)
    """
    cursor.execute(query, (sender, recipient, text, timestamp))
    connection.commit()
    connection.close()
    print(f"Mensagem salva: sender={sender}, recipient={recipient}, text={text}")

# Função para converter o datetime em string para JSON
def serialize_datetime(dt):
    if isinstance(dt, datetime):
        return dt.isoformat()  # Converte para formato ISO 8601
    return dt

# Evento para enviar e receber mensagens
@socketio.on('send_message')
def handle_message(data):
    sender = data['sender']
    recipient = data['recipient']
    text = data['text']
    
    print(f"Recebendo mensagem: sender={sender}, recipient={recipient}, text={text}")

    # Salvar a mensagem no banco de dados
    save_message(sender, recipient, text)

    # Criar o timestamp e formatá-lo para ISO 8601
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Enviar a mensagem de volta para o remetente e destinatário
    emit('receive_message', {'sender': sender, 'recipient': recipient, 'text': text, 'timestamp': timestamp}, broadcast=True)

# Evento para carregar as mensagens de um usuário
@socketio.on('load_messages')
def load_messages(data):
    id = data['id']  # Usando 'id' no lugar de 'uuid'
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    query = """
        SELECT id, sender, recipient, text, timestamp
        FROM messages
        WHERE sender = %s OR recipient = %s
        ORDER BY id ASC
    """
    cursor.execute(query, (id, id))  # Alterado para 'id'
    messages = cursor.fetchall()
    connection.close()

    # Serializar os timestamps das mensagens
    for message in messages:
        message['timestamp'] = serialize_datetime(message['timestamp'])

    # Enviar mensagens de volta para o frontend
    emit('all_messages', messages)

# Rota principal para verificar se o servidor está funcionando
@app.route('/')
def index():
    return "Servidor WebSocket funcionando!"

# Iniciar o servidor
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
