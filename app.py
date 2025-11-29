import os
import datetime
import uuid
from functools import wraps

from flask import Flask, request, jsonify
from tinydb import TinyDB, Query
from werkzeug.security import generate_password_hash, check_password_hash
import jwt

# -----------------------------------------------------------
# CONFIGURAÇÕES GERAIS
# -----------------------------------------------------------

# Arquivo do banco TinyDB
DB_PATH = os.environ.get("TINYDB_PATH", "db.json")

# Segredo do JWT (normalmente seria no .env)
JWT_SECRET = os.environ.get("JWT_SECRET", "super-secret-change-me")
JWT_ALGO = "HS256"

# Token dura 60 min (padrão)
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", 60))

# Inicia Flask e banco
app = Flask(__name__)
db = TinyDB(DB_PATH)

# Tabelas do banco
users_table = db.table("users")
shipments_table = db.table("shipments")
events_table = db.table("events")
sns_events_table = db.table("sns_queue")   # Fila SNS

UserQ = Query()  # Query helper do TinyDB


# -----------------------------------------------------------
# Função pra gerar token JWT
# -----------------------------------------------------------
def create_token(user_id):
    payload = {
        "sub": user_id,  # ID do usuário
        "iat": int(datetime.datetime.utcnow().timestamp()),  # quando foi criado
        "exp": int((datetime.datetime.utcnow() + datetime.timedelta(
            minutes=JWT_EXPIRE_MINUTES)).timestamp())  # quando expira
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


# -----------------------------------------------------------
# Middleware simples pra checar token nas rotas protegidas
# -----------------------------------------------------------
def token_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):

        # Pego o header Authorization
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Missing token"}), 401

        # Extraio o token
        token = auth.split(" ", 1)[1]

        try:
            # Decodifica sem validar iat (pra evitar erro de relógio)
            payload = jwt.decode(
                token,
                JWT_SECRET,
                algorithms=[JWT_ALGO],
                options={"verify_iat": False}
            )
            user_id = payload.get("sub")
        except Exception as e:
            return jsonify({"error": "Invalid token", "msg": str(e)}), 401

        # Ver se o usuário existe
        user = users_table.get(UserQ.id == user_id)
        if not user:
            return jsonify({"error": "User not found"}), 401

        # Jogo o user na request
        request.user = user
        request.user_id = user_id
        return f(*args, **kwargs)

    return wrapper


# -----------------------------------------------------------
# ROTAS DE AUTENTICAÇÃO (simulam duas Lambdas)
# -----------------------------------------------------------

@app.route("/auth/register", methods=["POST"])
def register():
    # Pega os dados enviados
    data = request.get_json() or {}
    email = data.get("email")
    password = data.get("password")
    name = data.get("name", "")

    # Validações básicas
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400

    if users_table.search(UserQ.email == email):
        return jsonify({"error": "user already exists"}), 400

    # Cria ID e salva o user
    uid = str(uuid.uuid4())
    pw_hash = generate_password_hash(password)

    users_table.insert({
        "id": uid,
        "email": email,
        "password": pw_hash,
        "name": name,
        "created_at": str(datetime.datetime.utcnow())
    })

    # Já devolve token
    return jsonify({"id": uid, "email": email, "token": create_token(uid)}), 201


@app.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"error": "email and password required"}), 400

    users = users_table.search(UserQ.email == email)
    if not users:
        return jsonify({"error": "invalid credentials"}), 401

    user = users[0]

    if not check_password_hash(user["password"], password):
        return jsonify({"error": "invalid credentials"}), 401

    # Gera token e retorna
    return jsonify({"id": user["id"], "email": email, "token": create_token(user["id"])}), 200


# -----------------------------------------------------------
# ROTAS PRINCIPAIS DO SISTEMA (simulate lambdas)
# -----------------------------------------------------------

# Criar shipment
@app.route("/shipments", methods=["POST"])
@token_required
def create_shipment():
    data = request.get_json() or {}

    sid = str(uuid.uuid4())
    external_id = data.get("external_id") or sid

    shipment = {
        "id": sid,
        "external_id": external_id,
        "origin": data.get("origin", {}),
        "destination": data.get("destination", {}),
        "meta": data.get("meta", {}),
        "owner_id": request.user_id,
        "status": "created",
        "created_at": str(datetime.datetime.utcnow()),
    }

    shipments_table.insert(shipment)

    return jsonify({"id": sid, "external_id": external_id}), 201


@app.route("/shipments", methods=["GET"])
@token_required
def list_shipments():
    # Filtra apenas os do usuário logado
    results = [s for s in shipments_table.all() if s.get("owner_id") == request.user_id]
    return jsonify(results), 200


@app.route("/shipments/<shipment_id>", methods=["GET"])
@token_required
def get_shipment(shipment_id):
    # Busca envio
    s = shipments_table.get(Query().id == shipment_id)
    if not s:
        return jsonify({"error": "shipment not found"}), 404

    # Pega eventos do envio
    events = [e for e in events_table.all() if e.get("shipment_id") == shipment_id]

    resp = s.copy()
    resp["events"] = events

    return jsonify(resp), 200


# Adicionar evento manual
@app.route("/shipments/<shipment_id>/events", methods=["POST"])
@token_required
def add_event(shipment_id):
    data = request.get_json() or {}
    status = data.get("status")

    if not status:
        return jsonify({"error": "status required"}), 400

    # Verifica se o envio existe
    s = shipments_table.get(Query().id == shipment_id)
    if not s:
        return jsonify({"error": "shipment not found"}), 404

    # Monta evento
    ev = {
        "id": str(uuid.uuid4()),
        "shipment_id": shipment_id,
        "status": status,
        "location": data.get("location", {}),
        "note": data.get("note", ""),
        "timestamp": data.get("timestamp") or str(datetime.datetime.utcnow()),
        "created_by": request.user_id
    }

    events_table.insert(ev)

    # Atualiza status do envio
    shipments_table.update({"status": status}, Query().id == shipment_id)

    return jsonify({"event_id": ev["id"]}), 201


# -----------------------------------------------------------
# WEBHOOK (envia atualização externa para o SNS)
# -----------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook_receiver():
    payload = request.get_json() or {}
    tracking = payload.get("tracking_id")

    if not tracking:
        return jsonify({"error": "tracking_id required"}), 400

    # Aqui em vez de atualizar direto eu jogo na fila (SNS)
    sns_events_table.insert({
        "id": str(uuid.uuid4()),
        "tracking_id": tracking,
        "payload": payload,
        "received_at": str(datetime.datetime.utcnow())
    })

    return jsonify({"ok": True, "msg": "published to SNS"}), 200


# -----------------------------------------------------------
# PROCESSADOR DO SNS (simula uma Lambda que consome fila)
# -----------------------------------------------------------
@app.route("/sns/process", methods=["POST"])
def process_sns_messages():
    msgs = sns_events_table.all()
    if not msgs:
        return jsonify({"ok": True, "processed": 0})

    processed_count = 0

    for msg in msgs:
        tracking = msg["tracking_id"]

        # Acha o shipment usando external_id
        matches = shipments_table.search(Query().external_id == tracking)
        if not matches:
            continue

        shipment = matches[0]
        payload = msg["payload"]

        # Crio o evento real
        evt = {
            "id": str(uuid.uuid4()),
            "shipment_id": shipment["id"],
            "status": payload.get("status", "unknown"),
            "location": payload.get("location", {}),
            "note": payload.get("note", ""),
            "timestamp": payload.get("timestamp") or str(datetime.datetime.utcnow()),
            "created_by": None,
            "source": "sns-processor"
        }

        events_table.insert(evt)

        # Atualiza status do shipment
        shipments_table.update({"status": evt["status"]}, Query().id == shipment["id"])

        processed_count += 1
        sns_events_table.remove(doc_ids=[msg.doc_id])

    return jsonify({"ok": True, "processed": processed_count})


# -----------------------------------------------------------
# Healthcheck
# -----------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "now": str(datetime.datetime.utcnow())})

# -----------------------------------------------------------
# RUN
# -----------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
