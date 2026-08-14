from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
import sqlite3
import re
from pathlib import Path
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta
import io
import time

BASE = Path(__file__).resolve().parent
DB = BASE / "due.db"
app = Flask(__name__)
app.secret_key = "troque-esta-chave-em-producao"

SALDO_TOLERANCE = Decimal("0.005")
STATUS_PENDENTE = "PENDENTE"
STATUS_CONCLUIDO = "CONCLUÍDO"

def db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn

def init_db():
    conn = db()
    try:
        conn.executescript("""
    CREATE TABLE IF NOT EXISTS empresas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        razao_social TEXT NOT NULL,
        cnpj TEXT NOT NULL UNIQUE,
        apelido TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS dues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_due TEXT NOT NULL UNIQUE,
        chave_acesso TEXT,
        data_due TEXT,
        cnpj TEXT,
        cliente TEXT,
        moeda TEXT NOT NULL DEFAULT 'USD',
        valor_original REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'PENDENTE',
        observacao TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS contratos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_contrato TEXT NOT NULL UNIQUE,
        banco TEXT,
        data_contrato TEXT,
        cnpj TEXT,
        cliente TEXT,
        moeda TEXT NOT NULL DEFAULT 'USD',
        valor_moeda REAL NOT NULL DEFAULT 0,
        taxa_cambio REAL,
        valor_reais REAL,
        status TEXT NOT NULL DEFAULT 'PENDENTE',
        observacao TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS due_movimentacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        due_id INTEGER NOT NULL,
        contrato_id INTEGER,
        due_contrato_id INTEGER,
        data_movimentacao TEXT NOT NULL,
        tipo TEXT NOT NULL DEFAULT 'UTILIZACAO',
        documento TEXT,
        valor REAL NOT NULL DEFAULT 0,
        observacao TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (due_id) REFERENCES dues(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS due_contratos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        due_id INTEGER NOT NULL,
        contrato_id INTEGER NOT NULL,
        valor_vinculado REAL NOT NULL DEFAULT 0,
        observacao TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(due_id, contrato_id),
        FOREIGN KEY (due_id) REFERENCES dues(id) ON DELETE CASCADE,
        FOREIGN KEY (contrato_id) REFERENCES contratos(id) ON DELETE CASCADE
    );
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(dues)")}
        if "chave_acesso" not in columns:
            conn.execute("ALTER TABLE dues ADD COLUMN chave_acesso TEXT")
        if "created_at" not in columns:
            conn.execute("ALTER TABLE dues ADD COLUMN created_at TEXT")
            conn.execute("UPDATE dues SET created_at=COALESCE(data_due, CURRENT_TIMESTAMP) WHERE created_at IS NULL")
        contrato_columns = {row[1] for row in conn.execute("PRAGMA table_info(contratos)")}
        if "banco" not in contrato_columns:
            conn.execute("ALTER TABLE contratos ADD COLUMN banco TEXT")
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_dues_chave_acesso
                       ON dues(chave_acesso)
                       WHERE chave_acesso IS NOT NULL AND chave_acesso <> ''""")
        movimentacao_columns = {row[1] for row in conn.execute("PRAGMA table_info(due_movimentacoes)")}
        if "contrato_id" not in movimentacao_columns:
            conn.execute("ALTER TABLE due_movimentacoes ADD COLUMN contrato_id INTEGER")
        if "due_contrato_id" not in movimentacao_columns:
            conn.execute("ALTER TABLE due_movimentacoes ADD COLUMN due_contrato_id INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mov_due_tipo ON due_movimentacoes(due_id, tipo)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mov_contrato_tipo ON due_movimentacoes(contrato_id, tipo)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mov_due_contrato ON due_movimentacoes(due_contrato_id)")
        for link in conn.execute("""
            SELECT v.id, v.due_id, v.contrato_id, v.valor_vinculado
            FROM due_contratos v
            LEFT JOIN due_movimentacoes m
              ON m.due_contrato_id=v.id
              OR (m.due_id=v.due_id AND m.contrato_id=v.contrato_id AND m.tipo='VINCULACAO')
            WHERE m.id IS NULL
        """).fetchall():
            conn.execute("""INSERT INTO due_movimentacoes
                (due_id,contrato_id,due_contrato_id,data_movimentacao,tipo,documento,valor,observacao)
                VALUES (?,?,?,?,?,?,?,?)""",
                (link["due_id"], link["contrato_id"], link["id"], date.today().isoformat(),
                 "VINCULACAO", f"VINCULO:{link['id']}", link["valor_vinculado"],
                 "Movimentação criada na migração do vínculo existente."))
        recalculate_statuses(conn)
        conn.commit()
    finally:
        conn.close()

@app.template_filter("money")
def money(v):
    try:
        return f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "0,00"

@app.template_filter("rate")
def rate(v):
    try:
        return f"{float(v):,.4f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "0,0000"

@app.template_filter("date_br")
def date_br(v):
    normalized = normalize_date(v)
    if not normalized:
        return ""
    return normalized[8:10] + "/" + normalized[5:7] + "/" + normalized[0:4]

def normalize_date(value):
    if value is None or str(value).strip() in {"", "NaT", "nan"}:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return (datetime(1899, 12, 30) + timedelta(days=float(value))).strftime("%Y-%m-%d")
        except (OverflowError, ValueError):
            return str(value)
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    try:
        return (datetime(1899, 12, 30) + timedelta(days=float(text))).strftime("%Y-%m-%d")
    except (OverflowError, ValueError):
        return text

def parse_date(value):
    normalized = normalize_date(value)
    if not normalized:
        return None
    try:
        datetime.strptime(normalized, "%Y-%m-%d")
        return normalized
    except ValueError:
        raise ValueError("Data inválida. Use o formato dd/mm/aaaa.")

def launch_timestamp():
    """Gera no backend a data e hora do lançamento de uma nova DU-E."""
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")

def parse_number(value):
    if value is None or str(value).strip() == "":
        return 0.0
    text = str(value).strip().replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        raise ValueError("Valor inválido. Use o formato 1.234,56.")

def parse_chave_acesso(value):
    chave = (value or "").strip().upper()
    if not chave:
        raise ValueError("A Chave de Acesso é obrigatória.")
    if not re.fullmatch(r"[A-Z0-9]{14}", chave):
        raise ValueError("A Chave de Acesso deve conter exatamente 14 caracteres alfanuméricos.")
    return chave

def normalize_cnpj(value):
    """Valida e armazena o CNPJ somente com seus 14 dígitos."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 14 or len(set(digits)) == 1:
        raise ValueError("Informe um CNPJ válido no formato 00.000.000/0000-00.")
    first_weights = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    second_weights = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    first = sum(int(digit) * weight for digit, weight in zip(digits[:12], first_weights))
    first_check = 0 if first % 11 < 2 else 11 - first % 11
    second = sum(int(digit) * weight for digit, weight in zip(digits[:12] + str(first_check), second_weights))
    second_check = 0 if second % 11 < 2 else 11 - second % 11
    if digits[-2:] != f"{first_check}{second_check}":
        raise ValueError("Informe um CNPJ válido no formato 00.000.000/0000-00.")
    return digits

def format_cnpj(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 14:
        return str(value or "")
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"

@app.template_filter("cnpj")
def cnpj_filter(value):
    return format_cnpj(value)

def empresas_for_form(conn, current_cnpj=None, selected_id=None):
    empresas = conn.execute("""
        SELECT id, razao_social, cnpj, apelido
        FROM empresas
        ORDER BY CASE WHEN TRIM(COALESCE(apelido, '')) <> '' THEN 0 ELSE 1 END,
                 apelido, razao_social
    """).fetchall()
    selected = None
    if selected_id not in (None, ""):
        try:
            selected = int(selected_id)
        except (TypeError, ValueError):
            selected = None
    if selected is None and current_cnpj:
        current_digits = re.sub(r"\D", "", str(current_cnpj))
        selected = next((empresa["id"] for empresa in empresas
                         if empresa["cnpj"] == current_digits), None)
    return empresas, selected

def cnpj_da_empresa(conn, empresa_id, current_cnpj=None):
    if empresa_id not in (None, ""):
        try:
            empresa_id = int(empresa_id)
        except (TypeError, ValueError):
            raise ValueError("Selecione uma empresa cadastrada.")
        empresa = conn.execute("SELECT cnpj FROM empresas WHERE id=?", (empresa_id,)).fetchone()
        if not empresa:
            raise ValueError("A empresa selecionada não foi encontrada.")
        return normalize_cnpj(empresa["cnpj"])
    if current_cnpj:
        return normalize_cnpj(current_cnpj)
    raise ValueError("Selecione uma empresa em Configurações > Cadastrar minhas empresas.")

def decimal_value(value):
    """Converte valores monetários para Decimal sem perder a regra de tolerância."""
    if value is None or str(value).strip() in {"", "nan", "NaT"}:
        return Decimal("0")
    return Decimal(str(value))

def normalize_balance(balance):
    balance = decimal_value(balance)
    return Decimal("0") if abs(balance) <= SALDO_TOLERANCE else balance

def status_from_balance(balance):
    """Única regra de status: saldo positivo relevante fica pendente."""
    return STATUS_PENDENTE if normalize_balance(balance) > 0 else STATUS_CONCLUIDO

@app.template_filter("status_class")
def status_class(status):
    return "status-pendente" if status == STATUS_PENDENTE else "status-concluido"

def movement_effect_sql(alias="m"):
    return f"CASE WHEN {alias}.tipo IN ('UTILIZACAO','VINCULACAO') THEN {alias}.valor ELSE -{alias}.valor END"

def due_effect(conn, due_id):
    return conn.execute(f"""SELECT COALESCE(SUM({movement_effect_sql()}),0)
                            FROM due_movimentacoes m WHERE m.due_id=?""", (due_id,)).fetchone()[0]

def due_balance(valor_original, utilizado):
    return normalize_balance(decimal_value(valor_original) - decimal_value(utilizado))

def contract_balance(valor_moeda, vinculado):
    return normalize_balance(decimal_value(valor_moeda) - decimal_value(vinculado))

def ensure_non_negative_balance(balance, message):
    if decimal_value(balance) < -SALDO_TOLERANCE:
        raise ValueError(message)

def update_due_status(conn, due_id):
    row = conn.execute(f"""
        SELECT d.valor_original,
               COALESCE(SUM({movement_effect_sql()}), 0) AS utilizado
        FROM dues d
        LEFT JOIN due_movimentacoes m ON m.due_id=d.id
        WHERE d.id=?
        GROUP BY d.id
    """, (due_id,)).fetchone()
    if not row:
        return None
    saldo = due_balance(row["valor_original"], row["utilizado"])
    status = status_from_balance(saldo)
    conn.execute("UPDATE dues SET status=? WHERE id=?", (status, due_id))
    return status

def update_contract_status(conn, contrato_id):
    row = conn.execute("""
        SELECT c.valor_moeda,
               COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END), 0) AS vinculado
        FROM contratos c
        LEFT JOIN due_movimentacoes m ON m.contrato_id=c.id
        WHERE c.id=?
        GROUP BY c.id
    """, (contrato_id,)).fetchone()
    if not row:
        return None
    saldo = contract_balance(row["valor_moeda"], row["vinculado"])
    status = status_from_balance(saldo)
    conn.execute("UPDATE contratos SET status=? WHERE id=?", (status, contrato_id))
    return status

def recalculate_statuses(conn, due_ids=None, contrato_ids=None):
    if due_ids is None:
        due_ids = [row[0] for row in conn.execute("SELECT id FROM dues")]
    if contrato_ids is None:
        contrato_ids = [row[0] for row in conn.execute("SELECT id FROM contratos")]
    for due_id in set(due_ids):
        update_due_status(conn, due_id)
    for contrato_id in set(contrato_ids):
        update_contract_status(conn, contrato_id)

def decorate_due(row):
    data = dict(row)
    data["valor_original"] = decimal_value(data.get("valor_original"))
    data["utilizado"] = decimal_value(data.get("utilizado"))
    data["saldo"] = due_balance(data.get("valor_original"), data["utilizado"])
    data["status"] = status_from_balance(data["saldo"])
    return data

def decorate_contract(row):
    data = dict(row)
    data["valor_moeda"] = decimal_value(data.get("valor_moeda"))
    data["vinculado"] = decimal_value(data.get("vinculado"))
    data["saldo"] = contract_balance(data.get("valor_moeda"), data["vinculado"])
    data["status"] = status_from_balance(data["saldo"])
    return data

def contract_summary(conn, contrato_id):
    row = conn.execute("""
        SELECT c.id, c.numero_contrato, c.moeda, c.valor_moeda, c.status,
               COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END),0) AS vinculado
        FROM contratos c
        LEFT JOIN due_movimentacoes m ON m.contrato_id=c.id
        WHERE c.id=?
        GROUP BY c.id
    """, (contrato_id,)).fetchone()
    if not row:
        return None
    data = decorate_contract(row)
    return data

init_db()

@app.route("/")
def index():
    conn = db()
    dues = [decorate_due(row) for row in conn.execute("""
        SELECT d.*, COALESCE(SUM(CASE WHEN m.tipo IN ('UTILIZACAO','VINCULACAO') THEN m.valor ELSE -m.valor END),0) AS utilizado
        FROM dues d
        LEFT JOIN due_movimentacoes m ON m.due_id=d.id
        GROUP BY d.id ORDER BY d.id DESC
    """).fetchall()]
    contratos = [decorate_contract(row) for row in conn.execute("""
        SELECT c.*, COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END),0) AS vinculado
        FROM contratos c
        LEFT JOIN due_movimentacoes m ON m.contrato_id=c.id
        GROUP BY c.id ORDER BY c.id DESC
    """).fetchall()]
    resumo = {
        "dues": conn.execute("SELECT COUNT(*) FROM dues").fetchone()[0],
        "contratos": conn.execute("SELECT COUNT(*) FROM contratos").fetchone()[0],
        "dues_sem_vinculo": conn.execute("""
            SELECT COUNT(*) FROM dues d
            WHERE NOT EXISTS (SELECT 1 FROM due_contratos v WHERE v.due_id=d.id)
        """).fetchone()[0],
        "contratos_sem_vinculo": conn.execute("""
            SELECT COUNT(*) FROM contratos c
            WHERE NOT EXISTS (SELECT 1 FROM due_contratos v WHERE v.contrato_id=c.id)
        """).fetchone()[0],
    }
    conn.close()
    return render_template("index.html", dues=dues, contratos=contratos, resumo=resumo)

@app.route("/contratos")
def lista_contratos():
    conn = db()
    contratos = [decorate_contract(row) for row in conn.execute("""
        SELECT c.*, COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END),0) AS vinculado
        FROM contratos c LEFT JOIN due_movimentacoes m ON m.contrato_id=c.id
        GROUP BY c.id ORDER BY c.id DESC
    """).fetchall()]
    conn.close()
    return render_template("contratos.html", contratos=contratos)

@app.route("/configuracoes")
def configuracoes():
    return render_template("configuracoes.html")

@app.route("/configuracoes/empresas", methods=["GET", "POST"])
def cadastro_empresas():
    conn = db()
    if request.method == "POST":
        try:
            razao_social = (request.form.get("razao_social") or "").strip()
            if not razao_social:
                raise ValueError("A Razão Social é obrigatória.")
            cnpj = normalize_cnpj(request.form.get("cnpj"))
            apelido = (request.form.get("apelido") or "").strip() or None
            conn.execute(
                "INSERT INTO empresas (razao_social, cnpj, apelido) VALUES (?,?,?)",
                (razao_social, cnpj, apelido),
            )
            conn.commit()
            conn.close()
            flash("Empresa cadastrada com sucesso.", "success")
            return redirect(url_for("cadastro_empresas"))
        except sqlite3.IntegrityError:
            conn.rollback()
            flash("Já existe uma empresa cadastrada com este CNPJ.", "danger")
        except ValueError as exc:
            conn.rollback()
            flash(str(exc), "danger")
    empresas = conn.execute("""
        SELECT id, razao_social, cnpj, apelido
        FROM empresas
        ORDER BY CASE WHEN TRIM(COALESCE(apelido, '')) <> '' THEN 0 ELSE 1 END,
                 apelido, razao_social
    """).fetchall()
    conn.close()
    return render_template("empresas.html", empresas=empresas)

def optional_number(value):
    return parse_number(value) if value and str(value).strip() else None

@app.route("/contrato/novo", methods=["GET", "POST"])
def novo_contrato():
    if request.method == "POST":
        f = request.form
        conn = db()
        try:
            valor_moeda = parse_number(f.get("valor_moeda"))
            ensure_non_negative_balance(valor_moeda, "O valor do contrato não pode ser negativo.")
            cnpj = cnpj_da_empresa(conn, f.get("empresa_id"))
            conn.execute("""INSERT INTO contratos
                (numero_contrato,banco,data_contrato,cnpj,cliente,moeda,valor_moeda,taxa_cambio,valor_reais,status,observacao)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (f["numero_contrato"].strip(), (f.get("banco") or "").strip() or None,
                 parse_date(f.get("data_contrato")), cnpj,
                 f.get("cliente"), f.get("moeda") or "USD", valor_moeda,
                 optional_number(f.get("taxa_cambio")), optional_number(f.get("valor_reais")),
                 status_from_balance(valor_moeda), f.get("observacao")))
            conn.commit(); conn.close()
            flash("Contrato cadastrado com sucesso.", "success")
            return redirect(url_for("lista_contratos"))
        except sqlite3.IntegrityError:
            conn.close()
            flash("Já existe um contrato com esse número.", "danger")
        except ValueError as exc:
            conn.close()
            flash(str(exc), "danger")
    conn = db()
    empresas, empresa_id = empresas_for_form(conn, selected_id=request.form.get("empresa_id"))
    conn.close()
    return render_template("contrato_form.html", contrato=None, empresas=empresas, empresa_id=empresa_id)

@app.route("/contrato/<int:contrato_id>")
def detalhe_contrato(contrato_id):
    conn = db()
    contrato = conn.execute("SELECT * FROM contratos WHERE id=?", (contrato_id,)).fetchone()
    if not contrato:
        conn.close(); return "Contrato não encontrado", 404
    vinculos = conn.execute("""
        SELECT v.*, m.id AS movimentacao_id, m.valor AS valor_movimentacao,
               d.chave_acesso, d.numero_due, d.created_at AS data_lancamento,
               d.cliente AS cliente_due
        FROM due_contratos v JOIN dues d ON d.id=v.due_id
        LEFT JOIN due_movimentacoes m ON m.due_contrato_id=v.id AND m.tipo='VINCULACAO'
        WHERE v.contrato_id=? ORDER BY v.id DESC
    """, (contrato_id,)).fetchall()
    summary = contract_summary(conn, contrato_id)
    conn.close()
    contrato = dict(contrato)
    contrato.update({"vinculado": summary["vinculado"], "saldo": summary["saldo"], "status": summary["status"]})
    return render_template("contrato_detalhe.html", contrato=contrato, vinculos=vinculos,
                           vinculado=summary["vinculado"], saldo=summary["saldo"])

@app.route("/contrato/<int:contrato_id>/editar", methods=["GET", "POST"])
def editar_contrato(contrato_id):
    conn = db()
    contrato = conn.execute("SELECT * FROM contratos WHERE id=?", (contrato_id,)).fetchone()
    if not contrato:
        conn.close(); return "Contrato não encontrado", 404
    if request.method == "POST":
        f = request.form
        try:
            valor_moeda = parse_number(f.get("valor_moeda"))
            linked = contract_summary(conn, contrato_id)["vinculado"]
            ensure_non_negative_balance(
                decimal_value(valor_moeda) - linked,
                "O valor do contrato não pode ficar abaixo do total já vinculado."
            )
            cnpj = cnpj_da_empresa(conn, f.get("empresa_id"), contrato["cnpj"])
            conn.execute("""UPDATE contratos SET numero_contrato=?,data_contrato=?,cnpj=?,cliente=?,moeda=?,
                            valor_moeda=?,taxa_cambio=?,valor_reais=?,status=?,observacao=?,banco=? WHERE id=?""",
                (f["numero_contrato"].strip(), parse_date(f.get("data_contrato")), cnpj,
                 f.get("cliente"), f.get("moeda") or "USD", valor_moeda,
                 optional_number(f.get("taxa_cambio")), optional_number(f.get("valor_reais")),
                 status_from_balance(contract_balance(valor_moeda, linked)),
                 f.get("observacao"), (f.get("banco") or "").strip() or None, contrato_id))
            conn.commit(); conn.close()
            flash("Contrato atualizado com sucesso.", "success")
            return redirect(url_for("detalhe_contrato", contrato_id=contrato_id))
        except sqlite3.IntegrityError:
            flash("Já existe um contrato com esse número.", "danger")
        except ValueError as exc:
            flash(str(exc), "danger")
    empresas, empresa_id = empresas_for_form(
        conn, contrato["cnpj"], request.form.get("empresa_id") if request.method == "POST" else None
    )
    conn.close()
    return render_template("contrato_form.html", contrato=contrato, empresas=empresas, empresa_id=empresa_id)

@app.route("/contrato/<int:contrato_id>/excluir", methods=["POST"])
def excluir_contrato(contrato_id):
    conn = db()
    contrato = conn.execute("SELECT numero_contrato FROM contratos WHERE id=?", (contrato_id,)).fetchone()
    if not contrato:
        conn.close(); return "Contrato não encontrado", 404
    due_ids = [row[0] for row in conn.execute(
        "SELECT DISTINCT due_id FROM due_movimentacoes WHERE contrato_id=?", (contrato_id,)
    )]
    conn.execute("DELETE FROM due_movimentacoes WHERE contrato_id=?", (contrato_id,))
    conn.execute("DELETE FROM contratos WHERE id=?", (contrato_id,))
    recalculate_statuses(conn, due_ids=due_ids, contrato_ids=[])
    conn.commit(); conn.close()
    flash(f"Contrato {contrato['numero_contrato']} excluído com sucesso.", "success")
    return redirect(url_for("lista_contratos"))

@app.route("/contratos/<int:contrato_id>/saldo")
def saldo_contrato(contrato_id):
    conn = db()
    contrato = contract_summary(conn, contrato_id)
    conn.close()
    if not contrato:
        return jsonify({"error": "Contrato não encontrado."}), 404
    total = float(contrato["valor_moeda"] or 0)
    vinculado = float(contrato["vinculado"] or 0)
    return jsonify({"id": contrato["id"], "numero_contrato": contrato["numero_contrato"],
                    "moeda": contrato["moeda"], "valor_total": total,
                    "total_vinculado": vinculado, "saldo_disponivel": float(contrato["saldo"]),
                    "status": contrato["status"]})

@app.route("/dues")
def consulta_dues():
    filters = {key: value for key, value in request.args.items()
               if key not in {"sort", "direction", "page"} and value}
    where, params = [], []
    if request.args.get("numero_due"):
        where.append("d.numero_due LIKE ?"); params.append(f"%{request.args['numero_due'].strip()}%")
    if request.args.get("chave_acesso"):
        where.append("d.chave_acesso LIKE ?"); params.append(f"%{request.args['chave_acesso'].strip().upper()}%")
    if request.args.get("cliente"):
        where.append("d.cliente LIKE ?"); params.append(f"%{request.args['cliente'].strip()}%")
    if request.args.get("cnpj"):
        where.append("d.cnpj LIKE ?"); params.append(f"%{request.args['cnpj'].strip()}%")
    if request.args.get("moeda"):
        where.append("d.moeda = ?"); params.append(request.args["moeda"].strip().upper())
    if request.args.get("status"):
        where.append("d.status = ?"); params.append(request.args["status"].strip().upper())
    for key, operator in (("data_de", ">="), ("data_ate", "<=")):
        if request.args.get(key):
            try:
                where.append(f"date(d.created_at) {operator} ?"); params.append(parse_date(request.args[key]))
            except ValueError as exc:
                flash(str(exc), "danger")
    for key, operator in (("valor_min", ">="), ("valor_max", "<=")):
        if request.args.get(key):
            try:
                where.append(f"d.valor_original {operator} ?"); params.append(parse_number(request.args[key]))
            except ValueError as exc:
                flash(str(exc), "danger")

    sort_fields = {"chave_acesso": "d.chave_acesso", "numero_due": "d.numero_due", "data_due": "d.created_at",
                   "cliente": "d.cliente", "moeda": "d.moeda",
                   "valor_original": "d.valor_original", "status": "d.status"}
    sort = request.args.get("sort", "data_due")
    sort = sort if sort in sort_fields else "data_due"
    direction = "ASC" if request.args.get("direction", "desc").lower() == "asc" else "DESC"
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    per_page = 20
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    conn = db()
    total = conn.execute(f"SELECT COUNT(*) FROM dues d{clause}", params).fetchone()[0]
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    dues = [decorate_due(row) for row in conn.execute(f"""
        SELECT d.*, COALESCE(SUM(CASE WHEN m.tipo IN ('UTILIZACAO','VINCULACAO') THEN m.valor ELSE -m.valor END),0) AS utilizado
        FROM dues d LEFT JOIN due_movimentacoes m ON m.due_id=d.id
        {clause} GROUP BY d.id ORDER BY {sort_fields[sort]} {direction}, d.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, (page - 1) * per_page]).fetchall()]
    moedas = [row[0] for row in conn.execute("SELECT DISTINCT moeda FROM dues WHERE moeda IS NOT NULL ORDER BY moeda")]
    conn.close()
    sort_links = {}
    for key in sort_fields:
        next_direction = "asc" if sort == key and direction == "DESC" else "desc"
        sort_links[key] = {**filters, "sort": key, "direction": next_direction}
    previous_args = {**filters, "sort": sort, "direction": direction, "page": page - 1}
    next_args = {**filters, "sort": sort, "direction": direction, "page": page + 1}
    return render_template("due_consulta.html", dues=dues, total=total, page=page, pages=pages,
                           filters=filters, moedas=moedas, sort=sort, direction=direction,
                           sort_links=sort_links, previous_args=previous_args, next_args=next_args)

@app.route("/dues/modelo")
def modelo_dues():
    import pandas as pd
    df = pd.DataFrame(columns=["numero_due", "chave_acesso", "cnpj", "cliente", "moeda", "valor_original"])
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="DU-Es")
    out.seek(0)
    return send_file(out, as_attachment=True, download_name="modelo_dues.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/dues/importar", methods=["POST"])
def importar_dues():
    import pandas as pd
    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename:
        flash("Selecione um arquivo Excel de DU-Es.", "danger")
        return redirect(url_for("consulta_dues"))

    try:
        df = pd.read_excel(arquivo)
        df.columns = [str(c).strip().lower() for c in df.columns]
        obrigatorias = {"chave_acesso", "numero_due", "valor_original"}
        faltantes = sorted(obrigatorias - set(df.columns))
        if faltantes:
            flash("O Excel precisa conter as colunas obrigatórias: " + ", ".join(sorted(obrigatorias)) + ".", "danger")
            return redirect(url_for("consulta_dues"))
    except Exception as exc:
        flash(f"Não foi possível ler o arquivo Excel: {exc}", "danger")
        return redirect(url_for("consulta_dues"))

    conn = db()
    existentes = {str(row[0]).strip() for row in conn.execute("SELECT numero_due FROM dues")}
    chaves_existentes = {str(row[0]).strip().upper() for row in conn.execute("SELECT chave_acesso FROM dues WHERE chave_acesso IS NOT NULL AND chave_acesso <> ''")}
    conn.close()
    registros, rejeitados, vistos_numeros, vistos_chaves = [], [], set(), set()

    def valor_linha(row, coluna, default=None):
        value = row[coluna] if coluna in df.columns else default
        return None if pd.isna(value) else value

    for indice, row in df.iterrows():
        linha = indice + 2
        numero = str(valor_linha(row, "numero_due", "") or "").strip()
        chave = str(valor_linha(row, "chave_acesso", "") or "").strip()
        try:
            chave = parse_chave_acesso(chave)
            if not numero:
                raise ValueError("numero_due é obrigatório")
            if numero in existentes:
                raise ValueError("a DU-E já está cadastrada no banco")
            if numero in vistos_numeros:
                raise ValueError("número de DU-E repetido no arquivo")
            if chave in chaves_existentes:
                raise ValueError("a Chave de Acesso já está cadastrada no banco")
            if chave in vistos_chaves:
                raise ValueError("Chave de Acesso repetida no arquivo")
            valor_bruto = valor_linha(row, "valor_original")
            if valor_bruto is None or str(valor_bruto).strip() == "":
                raise ValueError("valor_original é obrigatório")
            valor_original = parse_number(valor_bruto)
            ensure_non_negative_balance(valor_original, "valor_original não pode ser negativo")
            moeda = str(valor_linha(row, "moeda", "USD") or "USD").strip().upper()
            if not moeda:
                moeda = "USD"
            registros.append((chave, numero, valor_linha(row, "cnpj"), valor_linha(row, "cliente"),
                              moeda, valor_original, status_from_balance(valor_original), launch_timestamp()))
            vistos_numeros.add(numero)
            vistos_chaves.add(chave)
        except (TypeError, ValueError, InvalidOperation) as exc:
            rejeitados.append({"linha": linha, "chave": chave or "-", "numero": numero or "-", "motivo": str(exc)})

    conn = None
    try:
        if registros:
            conn = db()
            for tentativa in range(6):
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.executemany("""INSERT INTO dues
                        (chave_acesso,numero_due,cnpj,cliente,moeda,valor_original,status,created_at)
                        VALUES (?,?,?,?,?,?,?,?)""", registros)
                    conn.commit()
                    break
                except sqlite3.OperationalError as exc:
                    conn.rollback()
                    if "locked" not in str(exc).lower() or tentativa == 5:
                        raise
                    time.sleep(0.5)
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        rejeitados.append({"linha": "-", "chave": "-", "numero": "-", "motivo": f"Falha ao gravar os registros válidos: {exc}"})
        registros = []
    finally:
        if conn is not None:
            conn.close()

    resumo = {"encontrados": len(df), "importados": len(registros), "rejeitados": len(rejeitados),
              "erros": rejeitados}
    return render_template("due_import_resultado.html", resumo=resumo)

@app.route("/due/nova", methods=["GET","POST"])
def nova_due():
    if request.method == "POST":
        f=request.form
        conn = None
        try:
            chave = parse_chave_acesso(f.get("chave_acesso"))
            valor_original = parse_number(f.get("valor_original"))
            ensure_non_negative_balance(valor_original, "O valor original da DU-E não pode ser negativo.")
            conn = db()
            cnpj = cnpj_da_empresa(conn, f.get("empresa_id"))
            if conn.execute("SELECT 1 FROM dues WHERE chave_acesso=?", (chave,)).fetchone():
                raise ValueError("A Chave de Acesso já está cadastrada.")
            conn.execute("""INSERT INTO dues
                (chave_acesso,numero_due,cnpj,cliente,moeda,valor_original,status,created_at,observacao)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (chave,f["numero_due"].strip(),cnpj,f.get("cliente"),
                 f.get("moeda") or "USD",valor_original, status_from_balance(valor_original),
                 launch_timestamp(),
                 f.get("observacao")))
            conn.commit()
            flash("DU-E cadastrada com sucesso.", "success")
            return redirect(url_for("index"))
        except sqlite3.IntegrityError:
            flash("Já existe uma DU-E com esse número ou Chave de Acesso.", "danger")
        except ValueError as exc:
            flash(str(exc), "danger")
        finally:
            if conn is not None:
                conn.close()
    conn = db()
    empresas, empresa_id = empresas_for_form(conn, selected_id=request.form.get("empresa_id"))
    conn.close()
    return render_template("due_form.html", due=None, empresas=empresas, empresa_id=empresa_id)

@app.route("/due/<int:due_id>/editar", methods=["GET", "POST"])
def editar_due(due_id):
    conn = db()
    due = conn.execute("SELECT * FROM dues WHERE id=?", (due_id,)).fetchone()
    if not due:
        conn.close(); return "DU-E não encontrada", 404
    if request.method == "POST":
        f = request.form
        try:
            chave = parse_chave_acesso(f.get("chave_acesso"))
            valor_original = parse_number(f.get("valor_original"))
            ensure_non_negative_balance(valor_original, "O valor original da DU-E não pode ser negativo.")
            utilizado_atual = due_effect(conn, due_id)
            ensure_non_negative_balance(
                decimal_value(valor_original) - decimal_value(utilizado_atual),
                "O valor original da DU-E não pode ficar abaixo do total utilizado."
            )
            cnpj = cnpj_da_empresa(conn, f.get("empresa_id"), due["cnpj"])
            if conn.execute("SELECT 1 FROM dues WHERE chave_acesso=? AND id<>?", (chave, due_id)).fetchone():
                raise ValueError("A Chave de Acesso já está cadastrada.")
            conn.execute("""UPDATE dues SET chave_acesso=?,numero_due=?,cnpj=?,cliente=?,moeda=?,valor_original=?,observacao=?
                            WHERE id=?""",
                         (chave, f["numero_due"].strip(), cnpj,
                          f.get("cliente"), f.get("moeda") or "USD", valor_original,
                          f.get("observacao"), due_id))
            update_due_status(conn, due_id)
            conn.commit(); conn.close()
            flash("DU-E atualizada com sucesso.", "success")
            return redirect(url_for("due_detalhe", due_id=due_id))
        except sqlite3.IntegrityError:
            flash("Já existe uma DU-E com esse número ou Chave de Acesso.", "danger")
        except ValueError as exc:
            flash(str(exc), "danger")
    empresas, empresa_id = empresas_for_form(
        conn, due["cnpj"], request.form.get("empresa_id") if request.method == "POST" else None
    )
    conn.close()
    return render_template("due_form.html", due=due, empresas=empresas, empresa_id=empresa_id)

@app.route("/due/<int:due_id>")
def due_detalhe(due_id):
    conn=db()
    due_row=conn.execute("SELECT * FROM dues WHERE id=?", (due_id,)).fetchone()
    if not due_row:
        conn.close(); return "DU-E não encontrada",404
    mov=conn.execute("""SELECT m.*,c.numero_contrato,c.moeda AS contrato_moeda
                       FROM due_movimentacoes m
                       LEFT JOIN contratos c ON c.id=m.contrato_id
                       WHERE m.due_id=? ORDER BY m.data_movimentacao DESC,m.id DESC""",(due_id,)).fetchall()
    vinc=conn.execute("""SELECT v.*,c.numero_contrato,c.moeda,c.valor_moeda,
                         m.id AS movimentacao_id,COALESCE(m.valor,v.valor_vinculado) AS valor_calculado
                         FROM due_contratos v JOIN contratos c ON c.id=v.contrato_id
                         LEFT JOIN due_movimentacoes m ON m.due_contrato_id=v.id AND m.tipo='VINCULACAO'
                         WHERE v.due_id=? ORDER BY v.id DESC""",(due_id,)).fetchall()
    contratos=[decorate_contract(row) for row in conn.execute("""SELECT c.*,COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END),0) AS vinculado
                              FROM contratos c LEFT JOIN due_movimentacoes m ON m.contrato_id=c.id
                              GROUP BY c.id
                              HAVING c.valor_moeda-COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END),0)>?
                              ORDER BY c.numero_contrato""", (float(SALDO_TOLERANCE),)).fetchall()]
    utilizado=due_effect(conn, due_id)
    conn.close()
    due = decorate_due({**dict(due_row), "utilizado": utilizado})
    saldo=due["saldo"]
    return render_template("due_detalhe.html", due=due, mov=mov, vinc=vinc, contratos=contratos,
                           utilizado=utilizado, saldo=saldo)

@app.route("/due/<int:due_id>/movimentacao", methods=["POST"])
def movimentacao(due_id):
    f=request.form
    conn = None
    try:
        conn=db()
        due = conn.execute("SELECT id,valor_original FROM dues WHERE id=?", (due_id,)).fetchone()
        if not due:
            raise ValueError("DU-E não encontrada.")
        tipo = f.get("tipo", "UTILIZACAO").upper()
        if tipo not in {"UTILIZACAO", "DEVOLUCAO"}:
            raise ValueError("Tipo de movimentação inválido.")
        valor = parse_number(f.get("valor"))
        if valor <= 0:
            raise ValueError("O valor da movimentação deve ser maior que zero.")
        utilizado_atual = decimal_value(due_effect(conn, due_id))
        if tipo == "UTILIZACAO":
            ensure_non_negative_balance(
                decimal_value(due["valor_original"]) - utilizado_atual - decimal_value(valor),
                "A utilização não pode ultrapassar o saldo disponível da DU-E."
            )
        else:
            ensure_non_negative_balance(
                utilizado_atual - decimal_value(valor),
                "A devolução não pode ultrapassar o total utilizado da DU-E."
            )
        conn.execute("""INSERT INTO due_movimentacoes
            (due_id,data_movimentacao,tipo,documento,valor,observacao)
            VALUES (?,?,?,?,?,?)""",
            (due_id,parse_date(f["data_movimentacao"]),tipo,f.get("documento"),valor,f.get("observacao")))
        update_due_status(conn, due_id)
        conn.commit(); conn.close()
        flash("Movimentação registrada com sucesso.", "success")
    except ValueError as exc:
        if conn is not None:
            conn.close()
        flash(str(exc), "danger")
    return redirect(url_for("due_detalhe", due_id=due_id))

@app.route("/due/<int:due_id>/vincular", methods=["POST"])
def vincular(due_id):
    f=request.form
    conn=None
    conn=db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        due=conn.execute("SELECT valor_original FROM dues WHERE id=?",(due_id,)).fetchone()
        if not due:
            raise ValueError("DU-E não encontrada.")
        contrato_id=int(f["contrato_id"])
        contrato=contract_summary(conn, contrato_id)
        if not contrato or contrato["status"] != STATUS_PENDENTE:
            raise ValueError("O contrato selecionado não possui saldo disponível.")
        valor=Decimal(str(parse_number(f.get("valor_vinculado"))))
        if valor<=0:
            raise ValueError("O valor do vínculo deve ser maior que zero.")
        saldo_due=due_balance(due["valor_original"], due_effect(conn, due_id))
        saldo_contrato=contract_balance(contrato["valor_moeda"], contrato["vinculado"])
        if saldo_due <= 0:
            raise ValueError("A DU-E não possui saldo disponível.")
        if saldo_contrato <= 0:
            raise ValueError("O contrato não possui saldo disponível.")
        if valor>saldo_due:
            raise ValueError(f"Valor maior que o saldo disponível da DU-E ({money(saldo_due)}).")
        if valor>saldo_contrato:
            raise ValueError(f"Valor maior que o saldo disponível do contrato ({money(saldo_contrato)}).")
        link=conn.execute("SELECT id,valor_vinculado FROM due_contratos WHERE due_id=? AND contrato_id=?",
                          (due_id,contrato_id)).fetchone()
        if link:
            due_contrato_id=link["id"]
            conn.execute("UPDATE due_contratos SET valor_vinculado=valor_vinculado+?,observacao=? WHERE id=?",
                         (float(valor),f.get("observacao"),due_contrato_id))
        else:
            conn.execute("""INSERT INTO due_contratos(due_id,contrato_id,valor_vinculado,observacao)
                            VALUES (?,?,?,?)""",(due_id,contrato_id,float(valor),f.get("observacao")))
            due_contrato_id=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("""INSERT INTO due_movimentacoes
            (due_id,contrato_id,due_contrato_id,data_movimentacao,tipo,documento,valor,observacao)
            VALUES (?,?,?,?,?,?,?,?)""",(due_id,contrato_id,due_contrato_id,date.today().isoformat(),
                                          "VINCULACAO",f"CONTRATO:{contrato['numero_contrato']}",float(valor),f.get("observacao")))
        recalculate_statuses(conn, due_ids=[due_id], contrato_ids=[contrato_id])
        conn.commit(); flash("Contrato vinculado e movimentação registrada com sucesso.", "success")
    except sqlite3.IntegrityError:
        if conn: conn.rollback()
        flash("Esse contrato já está vinculado a esta DU-E.", "danger")
    except ValueError as exc:
        if conn: conn.rollback()
        flash(str(exc), "danger")
    finally:
        if conn: conn.close()
    return redirect(url_for("due_detalhe", due_id=due_id))

@app.route("/due/<int:due_id>/movimentacao/<int:mov_id>/excluir", methods=["POST"])
def excluir_movimentacao(due_id, mov_id):
    conn=db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        mov=conn.execute("SELECT * FROM due_movimentacoes WHERE id=? AND due_id=?",(mov_id,due_id)).fetchone()
        if not mov:
            raise ValueError("Movimentação não encontrada.")
        contrato_id = None
        if mov["tipo"]=="VINCULACAO":
            contrato_id = mov["contrato_id"]
            if mov["due_contrato_id"]:
                link=conn.execute("SELECT contrato_id,valor_vinculado FROM due_contratos WHERE id=?",(mov["due_contrato_id"],)).fetchone()
                if link:
                    contrato_id = contrato_id or link["contrato_id"]
                    saldo_link=decimal_value(link["valor_vinculado"])-decimal_value(mov["valor"])
                    if saldo_link <= SALDO_TOLERANCE:
                        conn.execute("DELETE FROM due_contratos WHERE id=?",(mov["due_contrato_id"],))
                    else:
                        conn.execute("UPDATE due_contratos SET valor_vinculado=? WHERE id=?",
                                     (float(saldo_link),mov["due_contrato_id"]))
            elif mov["contrato_id"]:
                link=conn.execute("SELECT id,valor_vinculado FROM due_contratos WHERE due_id=? AND contrato_id=?",
                                  (due_id,mov["contrato_id"])).fetchone()
                if link:
                    saldo_link=decimal_value(link["valor_vinculado"])-decimal_value(mov["valor"])
                    if saldo_link <= SALDO_TOLERANCE:
                        conn.execute("DELETE FROM due_contratos WHERE id=?",(link["id"],))
                    else:
                        conn.execute("UPDATE due_contratos SET valor_vinculado=? WHERE id=?",(float(saldo_link),link["id"]))
        conn.execute("DELETE FROM due_movimentacoes WHERE id=?",(mov_id,))
        recalculate_statuses(conn, due_ids=[due_id], contrato_ids=[contrato_id] if mov["tipo"] == "VINCULACAO" and contrato_id else [])
        conn.commit(); flash("Movimentação excluída e saldo revertido com sucesso.", "success")
    except ValueError as exc:
        conn.rollback(); flash(str(exc), "danger")
    finally:
        conn.close()
    return redirect(url_for("due_detalhe", due_id=due_id))

@app.route("/contratos/importar", methods=["POST"])
def importar_contratos():
    # Importação real via Excel usa pandas/openpyxl. Mantém atualização por número.
    conn = None
    try:
        import pandas as pd
        arquivo=request.files.get("arquivo")
        if not arquivo or not arquivo.filename:
            flash("Selecione um arquivo Excel.", "danger")
            return redirect(url_for("index"))
        df=pd.read_excel(arquivo)
        df.columns=[str(c).strip().lower() for c in df.columns]
        obrigatorias={"numero_contrato"}
        if not obrigatorias.issubset(df.columns):
            flash("O Excel precisa conter a coluna numero_contrato.", "danger")
            return redirect(url_for("index"))
        conn = db()
        query = """INSERT INTO contratos
            (numero_contrato,banco,data_contrato,cnpj,cliente,moeda,valor_moeda,taxa_cambio,valor_reais,status)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(numero_contrato) DO UPDATE SET
            banco=excluded.banco,data_contrato=excluded.data_contrato,cnpj=excluded.cnpj,cliente=excluded.cliente,
            moeda=excluded.moeda,valor_moeda=excluded.valor_moeda,taxa_cambio=excluded.taxa_cambio,
            valor_reais=excluded.valor_reais"""
        contrato_ids = []
        for attempt in range(6):
            try:
                conn.execute("BEGIN IMMEDIATE")
                for _, r in df.iterrows():
                    def val(col, default=None):
                        x = r[col] if col in df.columns else default
                        return None if pd.isna(x) else x
                    numero = str(val("numero_contrato", "")).strip()
                    if not numero:
                        continue
                    valor_moeda = parse_number(val("valor_moeda", 0))
                    ensure_non_negative_balance(valor_moeda, "valor_moeda não pode ser negativo")
                    existing = conn.execute(
                        "SELECT id FROM contratos WHERE numero_contrato=?", (numero,)
                    ).fetchone()
                    if existing:
                        linked = contract_summary(conn, existing["id"])["vinculado"]
                        ensure_non_negative_balance(
                            decimal_value(valor_moeda) - linked,
                            "valor_moeda não pode ficar abaixo do total já vinculado"
                        )
                    conn.execute(query, (numero, val("banco"), parse_date(val("data_contrato")), val("cnpj"), val("cliente"),
                                         str(val("moeda", "USD") or "USD").strip().upper(), float(valor_moeda),
                                         optional_number(val("taxa_cambio")),
                                         optional_number(val("valor_reais")),
                                         status_from_balance(valor_moeda)))
                    contrato_id = conn.execute(
                        "SELECT id FROM contratos WHERE numero_contrato=?", (numero,)
                    ).fetchone()[0]
                    contrato_ids.append(contrato_id)
                recalculate_statuses(conn, due_ids=[], contrato_ids=contrato_ids)
                conn.commit()
                break
            except sqlite3.OperationalError as exc:
                conn.rollback()
                if "locked" not in str(exc).lower() or attempt == 5:
                    raise
                time.sleep(0.5)
        flash(f"Importação concluída: {len(df)} linhas processadas.", "success")
    except Exception as e:
        flash(f"Erro na importação: {e}", "danger")
    finally:
        if conn is not None:
            conn.close()
    return redirect(url_for("index"))

@app.route("/contratos/modelo")
def modelo_contratos():
    import pandas as pd
    df=pd.DataFrame(columns=["numero_contrato","banco","data_contrato","cnpj","cliente","moeda","valor_moeda","taxa_cambio","valor_reais"])
    out=io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer,index=False,sheet_name="Contratos")
    out.seek(0)
    return send_file(out,as_attachment=True,download_name="modelo_contratos.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
