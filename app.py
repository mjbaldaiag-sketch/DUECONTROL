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
    CREATE TABLE IF NOT EXISTS dues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_due TEXT NOT NULL UNIQUE,
        chave_acesso TEXT,
        data_due TEXT,
        cnpj TEXT,
        cliente TEXT,
        moeda TEXT NOT NULL DEFAULT 'USD',
        valor_original REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'Ativa',
        observacao TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS contratos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_contrato TEXT NOT NULL UNIQUE,
        data_contrato TEXT,
        cnpj TEXT,
        cliente TEXT,
        moeda TEXT NOT NULL DEFAULT 'USD',
        valor_moeda REAL NOT NULL DEFAULT 0,
        taxa_cambio REAL,
        valor_reais REAL,
        status TEXT NOT NULL DEFAULT 'Ativo',
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
        conn.commit()
    finally:
        conn.close()

init_db()

@app.template_filter("money")
def money(v):
    try:
        return f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "0,00"

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

def movement_effect_sql(alias="m"):
    return f"CASE WHEN {alias}.tipo IN ('UTILIZACAO','VINCULACAO') THEN {alias}.valor ELSE -{alias}.valor END"

def due_effect(conn, due_id):
    return conn.execute(f"""SELECT COALESCE(SUM({movement_effect_sql()}),0)
                            FROM due_movimentacoes m WHERE m.due_id=?""", (due_id,)).fetchone()[0]

def contract_summary(conn, contrato_id):
    return conn.execute("""
        SELECT c.id, c.numero_contrato, c.moeda, c.valor_moeda, c.status,
               COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END),0) AS vinculado
        FROM contratos c
        LEFT JOIN due_movimentacoes m ON m.contrato_id=c.id
        WHERE c.id=?
        GROUP BY c.id
    """, (contrato_id,)).fetchone()

@app.route("/")
def index():
    conn = db()
    dues = conn.execute("""
        SELECT d.*, COALESCE(SUM(CASE WHEN m.tipo IN ('UTILIZACAO','VINCULACAO') THEN m.valor ELSE -m.valor END),0) AS utilizado
        FROM dues d
        LEFT JOIN due_movimentacoes m ON m.due_id=d.id
        GROUP BY d.id ORDER BY d.id DESC
    """).fetchall()
    contratos = conn.execute("""
        SELECT c.*, COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END),0) AS vinculado
        FROM contratos c
        LEFT JOIN due_movimentacoes m ON m.contrato_id=c.id
        GROUP BY c.id ORDER BY c.id DESC
    """).fetchall()
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
    contratos = conn.execute("""
        SELECT c.*, COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END),0) AS vinculado
        FROM contratos c LEFT JOIN due_movimentacoes m ON m.contrato_id=c.id
        GROUP BY c.id ORDER BY c.id DESC
    """).fetchall()
    conn.close()
    return render_template("contratos.html", contratos=contratos)

def optional_number(value):
    return parse_number(value) if value and str(value).strip() else None

@app.route("/contrato/novo", methods=["GET", "POST"])
def novo_contrato():
    if request.method == "POST":
        f = request.form
        conn = db()
        try:
            conn.execute("""INSERT INTO contratos
                (numero_contrato,data_contrato,cnpj,cliente,moeda,valor_moeda,taxa_cambio,valor_reais,status,observacao)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (f["numero_contrato"].strip(), parse_date(f.get("data_contrato")), f.get("cnpj"),
                 f.get("cliente"), f.get("moeda") or "USD", parse_number(f.get("valor_moeda")),
                 optional_number(f.get("taxa_cambio")), optional_number(f.get("valor_reais")),
                 f.get("status") or "Ativo", f.get("observacao")))
            conn.commit(); conn.close()
            flash("Contrato cadastrado com sucesso.", "success")
            return redirect(url_for("lista_contratos"))
        except sqlite3.IntegrityError:
            conn.close()
            flash("Já existe um contrato com esse número.", "danger")
        except ValueError as exc:
            conn.close()
            flash(str(exc), "danger")
    return render_template("contrato_form.html", contrato=None)

@app.route("/contrato/<int:contrato_id>")
def detalhe_contrato(contrato_id):
    conn = db()
    contrato = conn.execute("SELECT * FROM contratos WHERE id=?", (contrato_id,)).fetchone()
    if not contrato:
        conn.close(); return "Contrato não encontrado", 404
    vinculos = conn.execute("""
        SELECT v.*, m.id AS movimentacao_id, m.valor AS valor_movimentacao,
               d.chave_acesso, d.numero_due, d.data_due, d.cliente AS cliente_due
        FROM due_contratos v JOIN dues d ON d.id=v.due_id
        LEFT JOIN due_movimentacoes m ON m.due_contrato_id=v.id AND m.tipo='VINCULACAO'
        WHERE v.contrato_id=? ORDER BY v.id DESC
    """, (contrato_id,)).fetchall()
    conn.close()
    vinculado = sum(v["valor_movimentacao"] or 0 for v in vinculos)
    return render_template("contrato_detalhe.html", contrato=contrato, vinculos=vinculos,
                           vinculado=vinculado, saldo=contrato["valor_moeda"] - vinculado)

@app.route("/contrato/<int:contrato_id>/editar", methods=["GET", "POST"])
def editar_contrato(contrato_id):
    conn = db()
    contrato = conn.execute("SELECT * FROM contratos WHERE id=?", (contrato_id,)).fetchone()
    if not contrato:
        conn.close(); return "Contrato não encontrado", 404
    if request.method == "POST":
        f = request.form
        try:
            conn.execute("""UPDATE contratos SET numero_contrato=?,data_contrato=?,cnpj=?,cliente=?,moeda=?,
                            valor_moeda=?,taxa_cambio=?,valor_reais=?,status=?,observacao=? WHERE id=?""",
                (f["numero_contrato"].strip(), parse_date(f.get("data_contrato")), f.get("cnpj"),
                 f.get("cliente"), f.get("moeda") or "USD", parse_number(f.get("valor_moeda")),
                 optional_number(f.get("taxa_cambio")), optional_number(f.get("valor_reais")),
                 f.get("status") or "Ativo", f.get("observacao"), contrato_id))
            conn.commit(); conn.close()
            flash("Contrato atualizado com sucesso.", "success")
            return redirect(url_for("detalhe_contrato", contrato_id=contrato_id))
        except sqlite3.IntegrityError:
            flash("Já existe um contrato com esse número.", "danger")
        except ValueError as exc:
            flash(str(exc), "danger")
    conn.close()
    return render_template("contrato_form.html", contrato=contrato)

@app.route("/contrato/<int:contrato_id>/excluir", methods=["POST"])
def excluir_contrato(contrato_id):
    conn = db()
    contrato = conn.execute("SELECT numero_contrato FROM contratos WHERE id=?", (contrato_id,)).fetchone()
    if not contrato:
        conn.close(); return "Contrato não encontrado", 404
    conn.execute("DELETE FROM contratos WHERE id=?", (contrato_id,))
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
                    "total_vinculado": vinculado, "saldo_disponivel": total - vinculado})

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
        where.append("d.status = ?"); params.append(request.args["status"].strip())
    for key, operator in (("data_de", ">="), ("data_ate", "<=")):
        if request.args.get(key):
            try:
                where.append(f"d.data_due {operator} ?"); params.append(parse_date(request.args[key]))
            except ValueError as exc:
                flash(str(exc), "danger")
    for key, operator in (("valor_min", ">="), ("valor_max", "<=")):
        if request.args.get(key):
            try:
                where.append(f"d.valor_original {operator} ?"); params.append(parse_number(request.args[key]))
            except ValueError as exc:
                flash(str(exc), "danger")

    sort_fields = {"chave_acesso": "d.chave_acesso", "numero_due": "d.numero_due", "data_due": "d.data_due",
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
    dues = conn.execute(f"""
        SELECT d.*, COALESCE(SUM(CASE WHEN m.tipo IN ('UTILIZACAO','VINCULACAO') THEN m.valor ELSE -m.valor END),0) AS utilizado
        FROM dues d LEFT JOIN due_movimentacoes m ON m.due_id=d.id
        {clause} GROUP BY d.id ORDER BY {sort_fields[sort]} {direction}, d.id DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, (page - 1) * per_page]).fetchall()
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
    df = pd.DataFrame(columns=["chave_acesso", "numero_due", "data_due", "cnpj", "cliente", "moeda", "valor_original", "observacao"])
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
            data_due = parse_date(valor_linha(row, "data_due"))
            valor_original = parse_number(valor_bruto)
            moeda = str(valor_linha(row, "moeda", "USD") or "USD").strip().upper()
            if not moeda:
                moeda = "USD"
            registros.append((chave, numero, data_due, valor_linha(row, "cnpj"), valor_linha(row, "cliente"),
                              moeda, valor_original, valor_linha(row, "observacao")))
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
                        (chave_acesso,numero_due,data_due,cnpj,cliente,moeda,valor_original,observacao)
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
            conn = db()
            if conn.execute("SELECT 1 FROM dues WHERE chave_acesso=?", (chave,)).fetchone():
                raise ValueError("A Chave de Acesso já está cadastrada.")
            conn.execute("""INSERT INTO dues
                (chave_acesso,numero_due,data_due,cnpj,cliente,moeda,valor_original,observacao)
                VALUES (?,?,?,?,?,?,?,?)""",
                (chave,f["numero_due"].strip(),parse_date(f.get("data_due")),f.get("cnpj"),
                 f.get("cliente"),f.get("moeda") or "USD",parse_number(f.get("valor_original")),
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
    return render_template("due_form.html", due=None)

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
            if conn.execute("SELECT 1 FROM dues WHERE chave_acesso=? AND id<>?", (chave, due_id)).fetchone():
                raise ValueError("A Chave de Acesso já está cadastrada.")
            conn.execute("""UPDATE dues SET chave_acesso=?,numero_due=?,data_due=?,cnpj=?,cliente=?,moeda=?,valor_original=?,observacao=?
                            WHERE id=?""",
                         (chave, f["numero_due"].strip(), parse_date(f.get("data_due")), f.get("cnpj"),
                          f.get("cliente"), f.get("moeda") or "USD", parse_number(f.get("valor_original")),
                          f.get("observacao"), due_id))
            conn.commit(); conn.close()
            flash("DU-E atualizada com sucesso.", "success")
            return redirect(url_for("due_detalhe", due_id=due_id))
        except sqlite3.IntegrityError:
            flash("Já existe uma DU-E com esse número ou Chave de Acesso.", "danger")
        except ValueError as exc:
            flash(str(exc), "danger")
    conn.close()
    return render_template("due_form.html", due=due)

@app.route("/due/<int:due_id>")
def due_detalhe(due_id):
    conn=db()
    due=conn.execute("SELECT * FROM dues WHERE id=?", (due_id,)).fetchone()
    if not due:
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
    contratos=conn.execute("""SELECT c.*,COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END),0) AS vinculado
                              FROM contratos c LEFT JOIN due_movimentacoes m ON m.contrato_id=c.id
                              WHERE UPPER(COALESCE(c.status,''))='ATIVO'
                              GROUP BY c.id
                              HAVING c.valor_moeda-COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END),0)>0
                              ORDER BY c.numero_contrato""").fetchall()
    utilizado=due_effect(conn, due_id)
    conn.close()
    saldo=due["valor_original"]-utilizado
    return render_template("due_detalhe.html", due=due, mov=mov, vinc=vinc, contratos=contratos,
                           utilizado=utilizado, saldo=saldo)

@app.route("/due/<int:due_id>/movimentacao", methods=["POST"])
def movimentacao(due_id):
    f=request.form
    try:
        conn=db()
        conn.execute("""INSERT INTO due_movimentacoes
            (due_id,data_movimentacao,tipo,documento,valor,observacao)
            VALUES (?,?,?,?,?,?)""",
            (due_id,parse_date(f["data_movimentacao"]),f.get("tipo","UTILIZACAO"),f.get("documento"),
             parse_number(f.get("valor")),f.get("observacao")))
        conn.commit(); conn.close()
        flash("Movimentação registrada com sucesso.", "success")
    except ValueError as exc:
        if 'conn' in locals(): conn.close()
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
        if not contrato or str(contrato["status"] or "").upper()!="ATIVO":
            raise ValueError("O contrato selecionado não está ativo.")
        valor=Decimal(str(parse_number(f.get("valor_vinculado"))))
        if valor<=0:
            raise ValueError("O valor do vínculo deve ser maior que zero.")
        saldo_due=Decimal(str(due["valor_original"] or 0))-Decimal(str(due_effect(conn,due_id) or 0))
        saldo_contrato=Decimal(str(contrato["valor_moeda"] or 0))-Decimal(str(contrato["vinculado"] or 0))
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
        if mov["tipo"]=="VINCULACAO":
            if mov["due_contrato_id"]:
                link=conn.execute("SELECT valor_vinculado FROM due_contratos WHERE id=?",(mov["due_contrato_id"],)).fetchone()
                if link:
                    saldo_link=Decimal(str(link["valor_vinculado"] or 0))-Decimal(str(mov["valor"] or 0))
                    if saldo_link<=Decimal("0.005"):
                        conn.execute("DELETE FROM due_contratos WHERE id=?",(mov["due_contrato_id"],))
                    else:
                        conn.execute("UPDATE due_contratos SET valor_vinculado=? WHERE id=?",
                                     (float(saldo_link),mov["due_contrato_id"]))
            elif mov["contrato_id"]:
                link=conn.execute("SELECT id,valor_vinculado FROM due_contratos WHERE due_id=? AND contrato_id=?",
                                  (due_id,mov["contrato_id"])).fetchone()
                if link:
                    saldo_link=Decimal(str(link["valor_vinculado"] or 0))-Decimal(str(mov["valor"] or 0))
                    if saldo_link<=Decimal("0.005"):
                        conn.execute("DELETE FROM due_contratos WHERE id=?",(link["id"],))
                    else:
                        conn.execute("UPDATE due_contratos SET valor_vinculado=? WHERE id=?",(float(saldo_link),link["id"]))
        conn.execute("DELETE FROM due_movimentacoes WHERE id=?",(mov_id,))
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
            (numero_contrato,data_contrato,cnpj,cliente,moeda,valor_moeda,taxa_cambio,valor_reais,status,observacao)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(numero_contrato) DO UPDATE SET
            data_contrato=excluded.data_contrato,cnpj=excluded.cnpj,cliente=excluded.cliente,
            moeda=excluded.moeda,valor_moeda=excluded.valor_moeda,taxa_cambio=excluded.taxa_cambio,
            valor_reais=excluded.valor_reais,status=excluded.status,observacao=excluded.observacao"""
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
                    conn.execute(query, (numero, parse_date(val("data_contrato")), val("cnpj"), val("cliente"),
                                         val("moeda", "USD"), float(val("valor_moeda", 0) or 0),
                                         float(val("taxa_cambio", 0) or 0) or None,
                                         float(val("valor_reais", 0) or 0) or None,
                                         val("status", "Ativo"), val("observacao")))
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
    df=pd.DataFrame(columns=["numero_contrato","data_contrato","cnpj","cliente","moeda","valor_moeda","taxa_cambio","valor_reais","status","observacao"])
    out=io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer,index=False,sheet_name="Contratos")
    out.seek(0)
    return send_file(out,as_attachment=True,download_name="modelo_contratos.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
