from flask import Flask, render_template, request, redirect, url_for, flash, send_file
import sqlite3
from pathlib import Path
from decimal import Decimal
from datetime import datetime
import io

BASE = Path(__file__).resolve().parent
DB = BASE / "due.db"
app = Flask(__name__)
app.secret_key = "troque-esta-chave-em-producao"

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS dues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_due TEXT NOT NULL UNIQUE,
        data_due TEXT,
        cnpj TEXT,
        cliente TEXT,
        moeda TEXT NOT NULL DEFAULT 'BRL',
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
    conn.commit()
    conn.close()

@app.template_filter("money")
def money(v):
    try:
        return f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "0,00"

@app.route("/")
def index():
    conn = db()
    dues = conn.execute("""
        SELECT d.*, COALESCE(SUM(
            CASE WHEN m.tipo='UTILIZACAO' THEN m.valor ELSE -m.valor END
        ),0) AS utilizado
        FROM dues d
        LEFT JOIN due_movimentacoes m ON m.due_id=d.id
        GROUP BY d.id ORDER BY d.id DESC
    """).fetchall()
    contratos = conn.execute("""
        SELECT c.*, COALESCE(SUM(v.valor_vinculado),0) AS vinculado
        FROM contratos c
        LEFT JOIN due_contratos v ON v.contrato_id=c.id
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

@app.route("/due/nova", methods=["GET","POST"])
def nova_due():
    if request.method == "POST":
        f=request.form
        try:
            conn=db()
            conn.execute("""INSERT INTO dues
                (numero_due,data_due,cnpj,cliente,moeda,valor_original,observacao)
                VALUES (?,?,?,?,?,?,?)""",
                (f["numero_due"].strip(),f.get("data_due"),f.get("cnpj"),
                 f.get("cliente"),f.get("moeda","BRL"),float(f.get("valor_original") or 0),
                 f.get("observacao")))
            conn.commit(); conn.close()
            flash("DU-E cadastrada com sucesso.", "success")
            return redirect(url_for("index"))
        except sqlite3.IntegrityError:
            flash("Já existe uma DU-E com esse número.", "danger")
    return render_template("due_form.html")

@app.route("/due/<int:due_id>")
def due_detalhe(due_id):
    conn=db()
    due=conn.execute("SELECT * FROM dues WHERE id=?", (due_id,)).fetchone()
    if not due:
        conn.close(); return "DU-E não encontrada",404
    mov=conn.execute("SELECT * FROM due_movimentacoes WHERE due_id=? ORDER BY data_movimentacao DESC,id DESC",(due_id,)).fetchall()
    vinc=conn.execute("""SELECT v.*,c.numero_contrato,c.moeda,c.valor_moeda
                         FROM due_contratos v JOIN contratos c ON c.id=v.contrato_id
                         WHERE v.due_id=? ORDER BY v.id DESC""",(due_id,)).fetchall()
    contratos=conn.execute("SELECT * FROM contratos ORDER BY numero_contrato").fetchall()
    conn.close()
    utilizado=sum((m["valor"] if m["tipo"]=="UTILIZACAO" else -m["valor"]) for m in mov)
    saldo=due["valor_original"]-utilizado
    return render_template("due_detalhe.html", due=due, mov=mov, vinc=vinc, contratos=contratos,
                           utilizado=utilizado, saldo=saldo)

@app.route("/due/<int:due_id>/movimentacao", methods=["POST"])
def movimentacao(due_id):
    f=request.form
    conn=db()
    conn.execute("""INSERT INTO due_movimentacoes
        (due_id,data_movimentacao,tipo,documento,valor,observacao)
        VALUES (?,?,?,?,?,?)""",
        (due_id,f["data_movimentacao"],f.get("tipo","UTILIZACAO"),f.get("documento"),
         float(f.get("valor") or 0),f.get("observacao")))
    conn.commit(); conn.close()
    flash("Movimentação registrada com sucesso.", "success")
    return redirect(url_for("due_detalhe", due_id=due_id))

@app.route("/due/<int:due_id>/vincular", methods=["POST"])
def vincular(due_id):
    f=request.form
    conn=db()
    try:
        conn.execute("""INSERT INTO due_contratos(due_id,contrato_id,valor_vinculado,observacao)
                        VALUES (?,?,?,?)""",
                     (due_id,int(f["contrato_id"]),float(f.get("valor_vinculado") or 0),f.get("observacao")))
        conn.commit(); flash("Contrato vinculado com sucesso.", "success")
    except sqlite3.IntegrityError:
        flash("Esse contrato já está vinculado a esta DU-E.", "danger")
    finally:
        conn.close()
    return redirect(url_for("due_detalhe", due_id=due_id))

@app.route("/contratos/importar", methods=["POST"])
def importar_contratos():
    # Importação real via Excel usa pandas/openpyxl. Mantém atualização por número.
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
        conn=db()
        for _,r in df.iterrows():
            def val(col, default=None):
                x=r[col] if col in df.columns else default
                return None if pd.isna(x) else x
            numero=str(val("numero_contrato","")).strip()
            if not numero: continue
            conn.execute("""INSERT INTO contratos
                (numero_contrato,data_contrato,cnpj,cliente,moeda,valor_moeda,taxa_cambio,valor_reais,status,observacao)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(numero_contrato) DO UPDATE SET
                data_contrato=excluded.data_contrato,cnpj=excluded.cnpj,cliente=excluded.cliente,
                moeda=excluded.moeda,valor_moeda=excluded.valor_moeda,taxa_cambio=excluded.taxa_cambio,
                valor_reais=excluded.valor_reais,status=excluded.status,observacao=excluded.observacao""",
                (numero,val("data_contrato"),val("cnpj"),val("cliente"),val("moeda","USD"),
                 float(val("valor_moeda",0) or 0),float(val("taxa_cambio",0) or 0) or None,
                 float(val("valor_reais",0) or 0) or None,val("status","Ativo"),val("observacao")))
        conn.commit(); conn.close()
        flash(f"Importação concluída: {len(df)} linhas processadas.", "success")
    except Exception as e:
        flash(f"Erro na importação: {e}", "danger")
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
    init_db()
    app.run(debug=True, host="127.0.0.1", port=5000)
