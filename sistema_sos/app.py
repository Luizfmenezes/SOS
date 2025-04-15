import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from twilio.rest import Client
from dotenv import load_dotenv

# Carrega variáveis de ambiente do arquivo .env
load_dotenv()

app = Flask(__name__)
app.secret_key = '5191'
print("Chave secreta carregada:", app.secret_key)

# Configuração do Twilio (WhatsApp)
account_sid = 'AC2c5603db10f9f5e37354308120757027'
auth_token = '5111f33e9c0af6717af1c3b4696ed349'
twilio_client = Client(account_sid, auth_token)
whatsapp_from = 'whatsapp:+14155238886'  # Número Sandbox Twilio
destinatarios = [
    'whatsapp:+5511983878120',
    'whatsapp:+5511961753973',
    'whatsapp:+5511992320726',
]

# Configuração do Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Tornar datetime disponível nos templates
app.jinja_env.globals.update(datetime=datetime)

# Modelo de Usuário
class User(UserMixin):
    def __init__(self, id):
        self.id = id

# Usuários (em produção, use um banco de dados)
users = {
    os.getenv('ADMIN_USER'): {'password': os.getenv('ADMIN_PASSWORD')},
    'operador1': {'password': 'senhaoperador1'}
}

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

@app.route('/')
@login_required
def dashboard():
    """Painel principal com lista de SOS ativos"""
    conn = get_db_connection()
    try:
        # Buscar SOS pendentes
        pendentes = conn.execute(
            "SELECT id, numero_sos, linha, problema, motorista_nome, "
            "motorista_id, telefone, os, responsavel, data_hora, status "
            "FROM registros WHERE status='Pendente' ORDER BY data_hora DESC"
        ).fetchall()

        # Verificar alertas (>25 minutos)
        alertas = []
        for registro in pendentes:
            try:
                data_hora = datetime.strptime(registro[9], '%Y-%m-%d %H:%M:%S')
                if datetime.now() - data_hora > timedelta(minutes=25):
                    alertas.append(registro)
            except ValueError as e:
                app.logger.error(f"Erro ao processar registro {registro[0]}: {e}")

        # Estatísticas
        total_pendentes = conn.execute(
            "SELECT COUNT(*) FROM registros WHERE status='Pendente'"
        ).fetchone()[0]
        
        total_resolvidos = conn.execute(
            "SELECT COUNT(*) FROM registros WHERE status='Resolvido'"
        ).fetchone()[0]

        return render_template(
            'dashboard.html',
            pendentes=pendentes,
            alertas=alertas,
            total_pendentes=total_pendentes,
            total_resolvidos=total_resolvidos
        )
    finally:
        conn.close()

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Página de login"""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if username in users and users[username]['password'] == password:
            user = User(username)
            login_user(user)
            flash('Login realizado com sucesso!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Usuário ou senha incorretos', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Encerra a sessão do usuário"""
    logout_user()
    flash('Você foi desconectado', 'info')
    return redirect(url_for('login'))

@app.route('/registrar', methods=['GET', 'POST'])
@login_required
def registrar():
    """Página para registrar novos SOS"""
    if request.method == 'POST':
        dados = {
            'numero_sos': request.form['numero_sos'],
            'linha': request.form['linha'],
            'problema': request.form['problema'],
            'motorista_nome': request.form['motorista_nome'],
            'motorista_id': request.form['motorista_id'],
            'telefone': request.form['telefone'],
            'os': request.form['os'],
            'responsavel': current_user.id,
            'data_hora': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'status': 'Pendente'
        }

        # Validação dos campos
        if not all(dados.values()):
            flash('Todos os campos são obrigatórios!', 'danger')
            return redirect(url_for('registrar'))

        # Salvar no banco de dados
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO registros "
                "(numero_sos, linha, problema, motorista_nome, motorista_id, "
                "telefone, os, responsavel, data_hora, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(dados.values())
            )
            conn.commit()
            flash('SOS registrado com sucesso!', 'success')
        except Exception as e:
            conn.rollback()
            app.logger.error(f"Erro ao salvar no banco: {e}")
            flash('Erro ao registrar SOS', 'danger')
        finally:
            conn.close()

        # Enviar para WhatsApp
        enviar_whatsapp(
            f"🚨 *NOVO SOS* 🚨\n"
            f"{dados['numero_sos']} | {dados['linha']}\n"
            f"*Problema*: {dados['problema']}\n"
            f"*Motorista*: {dados['motorista_nome']} ({dados['motorista_id']})\n"
            f"📞 {dados['telefone']} | OS {dados['os']}\n"
            f"⌚ {dados['data_hora']}\n"
            f"Registrado por: {dados['responsavel']}"
        )

        return redirect(url_for('dashboard'))
    
    return render_template('registrar.html')

@app.route('/resolver/<int:sos_id>')
@login_required
def resolver(sos_id):
    """Marca um SOS como resolvido"""
    conn = get_db_connection()
    try:
        # Buscar dados antes de atualizar
        registro = conn.execute(
            "SELECT * FROM registros WHERE id = ?", (sos_id,)
        ).fetchone()

        if registro:
            # Atualizar status
            conn.execute(
                "UPDATE registros SET status='Resolvido' WHERE id=?", (sos_id,)
            )
            conn.commit()

            # Calcular tempo de resolução
            inicio = datetime.strptime(registro[9], '%Y-%m-%d %H:%M:%S')
            duracao = datetime.now() - inicio

            # Enviar confirmação
            enviar_whatsapp(
                f"✅ *SOS RESOLVIDO* ✅\n"
                f"{registro[1]} | {registro[2]}\n"
                f"Tempo de atendimento: {str(duracao).split('.')[0]}\n"
                f"Resolvido por: {current_user.id}"
            )

            flash('SOS marcado como resolvido!', 'success')
        else:
            flash('SOS não encontrado', 'danger')
    except Exception as e:
        conn.rollback()
        app.logger.error(f"Erro ao resolver SOS: {e}")
        flash('Erro ao atualizar status', 'danger')
    finally:
        conn.close()

    return redirect(url_for('dashboard'))

def enviar_whatsapp(mensagem):
    """Envia mensagem para todos os destinatários do WhatsApp"""
    for numero in destinatarios:
        try:
            twilio_client.messages.create(
                body=mensagem,
                from_=whatsapp_from,
                to=numero
            )
            print(f"Mensagem enviada para {numero}")
        except Exception as e:
            app.logger.error(f"Erro ao enviar WhatsApp para {numero}: {e}")
            flash(f'Mensagem não enviada para {numero}', 'warning')


def get_db_connection():
    """Cria e retorna uma conexão com o banco de dados"""
    conn = sqlite3.connect('sos.db')
    conn.row_factory = sqlite3.Row  # Permite acesso por nome de coluna
    return conn

def init_db():
    """Inicializa o banco de dados se não existir"""
    if not os.path.exists('sos.db'):
        conn = get_db_connection()
        try:
            conn.execute(
                "CREATE TABLE registros ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "numero_sos TEXT NOT NULL, "
                "linha TEXT NOT NULL, "
                "problema TEXT NOT NULL, "
                "motorista_nome TEXT NOT NULL, "
                "motorista_id TEXT NOT NULL, "
                "telefone TEXT NOT NULL, "
                "os TEXT NOT NULL, "
                "responsavel TEXT NOT NULL, "
                "data_hora TEXT NOT NULL, "
                "status TEXT NOT NULL)"
            )
            conn.commit()
        finally:
            conn.close()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
