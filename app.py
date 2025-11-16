import os
import json
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
import openai
from dotenv import load_dotenv

print("VERSÃO DO OPENAI INSTALADA:", openai.__version__)

load_dotenv()
def get_client():
    return OpenAI(api_key=os.getenv("CHAVE_API"))



# ---- App e configuração via environment variables ----
app = Flask(__name__)

# SECRET_KEY
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey_local')

db_url = os.environ.get('DATABASE_URL') or os.environ.get('MYSQL_URL')

if not db_url:
    raise ValueError(
        "ERRO: Nenhuma variável de ambiente DATABASE_URL ou MYSQL_URL foi definida."
    )

# Ajuste para SQLAlchemy
if db_url.startswith("mysql://"):
    db_url = db_url.replace("mysql://", "mysql+pymysql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inicializa DB
db = SQLAlchemy(app)



# (o resto do arquivo segue — suas classes User, Flashcard, Estatistica, rotas, etc.)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Desempenho(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    acertos = db.Column(db.Integer, default=0)
    erros = db.Column(db.Integer, default=0)
    semana = db.Column(db.Integer, nullable=False)

    usuario = db.relationship('User', backref=db.backref('desempenhos', lazy=True))

class Flashcard(db.Model):
    __tablename__ = 'flashcard'
    id = db.Column(db.Integer, primary_key=True)
    pergunta = db.Column(db.String(255), nullable=False)
    resposta = db.Column(db.String(255), nullable=False)
    alt_a = db.Column(db.String(255))
    alt_b = db.Column(db.String(255))
    alt_c = db.Column(db.String(255))
    alt_d = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    respondido = db.Column(db.Boolean, default=False)
    estatisticas = db.relationship('Estatistica', backref='flashcard', lazy=True)
    


class Estatistica(db.Model):
    __tablename__ = 'estatistica'
    id = db.Column(db.Integer, primary_key=True)
    resultado = db.Column(db.String(10), nullable=False)  # "acerto" ou "erro"
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    flashcard_id = db.Column(db.Integer, db.ForeignKey('flashcard.id'), nullable=False)
    data_resposta = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    try:
        db.create_all()
        print("Tabelas criadas/verificadas no Railway.")
    except Exception as e:
        print("Erro ao criar tabelas:", e)


# Rota inicial

@app.route('/home')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('home.html')

@app.route('/flashcards')
def flashcards():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # Só mostra flashcards que ainda não foram respondidos
    user_flashcards = Flashcard.query.filter_by(
        user_id=session['user_id'],
        respondido=False
    ).all()

    return render_template('flashcards.html', flashcards=user_flashcards)




from sqlalchemy import func, case

@app.route('/estatisticas')
def estatisticas():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    uid = session['user_id']

    # totais simples
    acertos_total = Estatistica.query.filter_by(user_id=uid, resultado='acerto').count()
    erros_total = Estatistica.query.filter_by(user_id=uid, resultado='erro').count()

    # evolução semanal (usando data_resposta)
    semanal = db.session.query(
        func.week(Estatistica.data_resposta).label('semana'),
        func.sum(case((Estatistica.resultado == 'acerto', 1), else_=0)).label('acertos'),
        func.sum(case((Estatistica.resultado == 'erro', 1), else_=0)).label('erros')
    ).filter(Estatistica.user_id == uid).group_by('semana').order_by('semana').all()

    # prepara dados pro gráfico
    semanas = [f"Semana {row.semana}" for row in semanal]
    acertos = [int(row.acertos) for row in semanal]
    erros = [int(row.erros) for row in semanal]

    return render_template(
        'estatisticas.html',
        semanas=semanas,
        acertos=acertos,
        erros=erros,
        acertos_total=acertos_total,
        erros_total=erros_total
    )


@app.route('/enviar_texto', methods=['POST'])
def enviar_texto():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    titulo = request.form['titulo']

    
    exemplos = [
        {"pergunta": f"O que significa '{titulo}'?", "resposta": "Exemplo de resposta gerada"},
        {"pergunta": f"Qual a ideia principal de '{titulo}'?", "resposta": "Resumo em poucas palavras"}
    ]

    for ex in exemplos:
        novo_flashcard = Flashcard(pergunta=ex['pergunta'], resposta=ex['resposta'], user_id=session['user_id'])
        db.session.add(novo_flashcard)

    db.session.commit()
    flash(f"Flashcards gerados a partir de '{titulo}'!", "success")
    return redirect(url_for('flashcards'))

@app.route('/responder/<int:flashcard_id>/<string:resultado>', methods=['POST'])
def responder(flashcard_id, resultado):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if resultado not in ('acerto', 'erro'):
        flash('Resultado inválido.', 'danger')
        return redirect(url_for('flashcards'))

    # Pega o flashcard pelo ID
    flashcard = Flashcard.query.get(flashcard_id)
    if not flashcard:
        flash('Flashcard não encontrado.', 'danger')
        return redirect(url_for('flashcards'))

    # Registra estatística
    nova = Estatistica(
        user_id=session['user_id'],
        flashcard_id=flashcard_id,
        resultado=resultado,
        data_resposta=datetime.utcnow()
    )
    db.session.add(nova)

    # Marca o flashcard como respondido
    flashcard.respondido = True

    db.session.commit()

    flash('Resposta registrada e flashcard ocultado.', 'success')
    return redirect(url_for('flashcards'))


@app.route('/ranking')
def ranking():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    ranking = db.session.query(
        User.username,
        func.coalesce(func.sum(case((Estatistica.resultado == 'acerto', 1), else_=0)), 0).label('acertos'),
        func.coalesce(func.sum(case((Estatistica.resultado == 'erro', 1), else_=0)), 0).label('erros')
    ).join(Estatistica, Estatistica.user_id == User.id, isouter=True) \
     .group_by(User.id) \
     .order_by(func.sum(case((Estatistica.resultado == 'acerto', 1), else_=0)).desc()) \
     .all()
    
    ranking_com_taxa = []
    for user in ranking:
        total = user.acertos + user.erros
        taxa = round((user.acertos / total * 100), 2) if total > 0 else 0
        ranking_com_taxa.append({
            'username': user.username,
            'acertos': user.acertos,
            'erros': user.erros,
            'taxa': taxa
        })

    return render_template('ranking.html', ranking=ranking_com_taxa)

@app.route('/gerar_flashcards', methods=['POST'])
def gerar_flashcards():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    prompt = request.form['prompt']
    print(f"🎯 Tema recebido: {prompt}")

    # ======== PROMPT DO SISTEMA ========
    sys = """
    Você é um gerador de flashcards educacionais.
    Gere SEMPRE um JSON válido no formato abaixo (sem texto adicional, sem explicações):
    {
      "flashcards": [
        {
          "question": "string",
          "answer": "string",
          "choices": ["alternativa A", "alternativa B", "alternativa C", "alternativa D"]
        }
      ]
    }

    Regras:
    - A resposta correta deve estar INCLUÍDA em "choices".
    - As demais alternativas devem ser plausíveis, mas erradas.
    - As posições das alternativas devem ser aleatórias (não coloque sempre a correta primeiro).
    - EMBARALHE SEMPRE A RESPOSTA CORRETA DENTRO DO ARRAY DE ESCOLHAS.
    - O conteúdo deve ser direto e educativo, com linguagem natural.
    - Tente não ser muito vago e dê os devidos detalhes, caso preciso.
    - A pergunta precisa ser completa e, quando necessário, haver contexto.
    """

    usermsg = f"Crie 5 flashcards sobre o tema: {prompt}. Cada flashcard deve seguir o formato descrito acima."

    # ======== CHAMADA À API OPENAI ========
    try:
        client = get_client()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": usermsg}
            ],
            max_tokens=1000
        )
    except Exception as e:
        print("ERRO NA API:", e)
        flash(f"Erro na API: {e}", "danger")
        return redirect(url_for('flashcards'))

    # ======== TRATAR A RESPOSTA ========
    text = resp.choices[0].message.content.strip()
    print("🧠 Resposta da OpenAI (bruta):")
    print(text)

    try:
        data = json.loads(text)
        print("✅ JSON carregado com sucesso.")
    except Exception as e:
        print("⚠️ Erro ao converter JSON da IA:", e)
        flash("A resposta da IA não veio em JSON válido. Tente novamente.", "warning")
        return redirect(url_for('flashcards'))

    # ======== SALVAR NO BANCO ========
    created = 0
    for fc in data.get('flashcards', []):
        print("📘 Flashcard recebido:", fc)
        q = fc.get('question')
        a = fc.get('answer')
        choices = fc.get('choices', [])
        if not q or not a or len(choices) < 4:
            print("⚠️ Flashcard inválido — ignorado:", fc)
            continue

        novo = Flashcard(
            pergunta=q,
            resposta=a,
            alt_a=choices[0],
            alt_b=choices[1],
            alt_c=choices[2],
            alt_d=choices[3],
            user_id=session['user_id']
        )
        db.session.add(novo)
        created += 1

    db.session.commit()
    print(f"💾 {created} flashcards salvos no banco com sucesso!")

    flash(f"{created} flashcards gerados com sucesso!", "success")
    return redirect(url_for('flashcards'))





@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['username'] = user.username
            flash("Login realizado com sucesso!", "success")
            return redirect(url_for('home'))
        else:
            flash("Usuário ou senha incorretos", "danger")
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, password=hashed_password)
        try:
            db.session.add(new_user)
            db.session.commit()
            flash("Usuário registrado com sucesso!", "success")
            return redirect(url_for('login'))
        except:
            flash("Erro: usuário já existe.", "danger")
    return render_template('register.html')


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    flash("Logout realizado com sucesso!", "info")
    return redirect(url_for('login'))





if __name__ == '__main__':
    with app.app_context():
        try:
            db.create_all()
            print("Banco verificado com sucesso!")
        except Exception as e:
            print(f"Erro ao criar tabelas: {e}")
    app.run(debug=True)
