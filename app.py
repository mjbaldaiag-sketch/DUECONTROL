from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
import json
import sqlite3
import re
import unicodedata
from pathlib import Path
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timedelta
import io
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
DB = BASE / "due.db"
app = Flask(__name__)
app.secret_key = "troque-esta-chave-em-producao"

SALDO_TOLERANCE = Decimal("0.005")
STATUS_PENDENTE = "PENDENTE"
STATUS_CONCLUIDO = "CONCLUÍDO"
NDF_STATUS_ATIVA = "ATIVA"
NDF_STATUS_LIQUIDADA = "LIQUIDADA"
NDF_STATUS_CANCELADA = "CANCELADA"
NDF_STATUS_VENCIDA = "VENCIDA"
NDF_STATUSES = (NDF_STATUS_ATIVA, NDF_STATUS_LIQUIDADA, NDF_STATUS_CANCELADA)
NDF_TIPOS = ("BANCO", "TRADING")
NDF_POSICOES = ("COMPRA", "VENDA")
PTAX_MOEDAS = ("USD", "EUR")
PTAX_API_URL = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoMoedaPeriodo"
PTAX_API_TIMEOUT = 20
CLIENTE_PAISES = (
    ("AD", "Andorra"), ("AE", "Emirados Árabes Unidos"), ("AF", "Afeganistão"),
    ("AG", "Antígua e Barbuda"), ("AI", "Anguilla"), ("AL", "Albânia"),
    ("AM", "Armênia"), ("AO", "Angola"), ("AQ", "Antártida"), ("AR", "Argentina"),
    ("AS", "Samoa Americana"), ("AT", "Áustria"), ("AU", "Austrália"), ("AW", "Aruba"),
    ("AX", "Ilhas Åland"), ("AZ", "Azerbaijão"), ("BA", "Bósnia e Herzegovina"),
    ("BB", "Barbados"), ("BD", "Bangladesh"), ("BE", "Bélgica"), ("BF", "Burkina Faso"),
    ("BG", "Bulgária"), ("BH", "Bahrein"), ("BI", "Burundi"), ("BJ", "Benim"),
    ("BL", "São Bartolomeu"), ("BM", "Bermudas"), ("BN", "Brunei"), ("BO", "Bolívia"),
    ("BQ", "Bonaire, Santo Eustáquio e Saba"), ("BR", "Brasil"), ("BS", "Bahamas"),
    ("BT", "Butão"), ("BV", "Ilha Bouvet"), ("BW", "Botsuana"), ("BY", "Belarus"),
    ("BZ", "Belize"), ("CA", "Canadá"), ("CC", "Ilhas Cocos"),
    ("CD", "República Democrática do Congo"), ("CF", "República Centro-Africana"),
    ("CG", "República do Congo"), ("CH", "Suíça"), ("CI", "Costa do Marfim"),
    ("CK", "Ilhas Cook"), ("CL", "Chile"), ("CM", "Camarões"), ("CN", "China"),
    ("CO", "Colômbia"), ("CR", "Costa Rica"), ("CU", "Cuba"), ("CV", "Cabo Verde"),
    ("CW", "Curaçau"), ("CX", "Ilha Christmas"), ("CY", "Chipre"), ("CZ", "Tchéquia"),
    ("DE", "Alemanha"), ("DJ", "Djibuti"), ("DK", "Dinamarca"), ("DM", "Dominica"),
    ("DO", "República Dominicana"), ("DZ", "Argélia"), ("EC", "Equador"), ("EE", "Estônia"),
    ("EG", "Egito"), ("EH", "Saara Ocidental"), ("ER", "Eritreia"), ("ES", "Espanha"),
    ("ET", "Etiópia"), ("FI", "Finlândia"), ("FJ", "Fiji"), ("FK", "Ilhas Malvinas"),
    ("FM", "Micronésia"), ("FO", "Ilhas Faroé"), ("FR", "França"), ("GA", "Gabão"),
    ("GB", "Reino Unido"), ("GD", "Granada"), ("GE", "Geórgia"), ("GF", "Guiana Francesa"),
    ("GG", "Guernsey"), ("GH", "Gana"), ("GI", "Gibraltar"), ("GL", "Groenlândia"),
    ("GM", "Gâmbia"), ("GN", "Guiné"), ("GP", "Guadalupe"), ("GQ", "Guiné Equatorial"),
    ("GR", "Grécia"), ("GS", "Geórgia do Sul e Ilhas Sandwich do Sul"), ("GT", "Guatemala"),
    ("GU", "Guam"), ("GW", "Guiné-Bissau"), ("GY", "Guiana"), ("HK", "Hong Kong"),
    ("HM", "Ilhas Heard e McDonald"), ("HN", "Honduras"), ("HR", "Croácia"), ("HT", "Haiti"),
    ("HU", "Hungria"), ("ID", "Indonésia"), ("IE", "Irlanda"), ("IL", "Israel"),
    ("IM", "Ilha de Man"), ("IN", "Índia"), ("IO", "Território Britânico do Oceano Índico"),
    ("IQ", "Iraque"), ("IR", "Irã"), ("IS", "Islândia"), ("IT", "Itália"),
    ("JE", "Jersey"), ("JM", "Jamaica"), ("JO", "Jordânia"), ("JP", "Japão"),
    ("KE", "Quênia"), ("KG", "Quirguistão"), ("KH", "Camboja"), ("KI", "Kiribati"),
    ("KM", "Comores"), ("KN", "São Cristóvão e Névis"), ("KP", "Coreia do Norte"),
    ("KR", "Coreia do Sul"), ("KW", "Kuwait"), ("KY", "Ilhas Cayman"), ("KZ", "Cazaquistão"),
    ("LA", "Laos"), ("LB", "Líbano"), ("LC", "Santa Lúcia"), ("LI", "Liechtenstein"),
    ("LK", "Sri Lanka"), ("LR", "Libéria"), ("LS", "Lesoto"), ("LT", "Lituânia"),
    ("LU", "Luxemburgo"), ("LV", "Letônia"), ("LY", "Líbia"), ("MA", "Marrocos"),
    ("MC", "Mônaco"), ("MD", "Moldávia"), ("ME", "Montenegro"), ("MF", "São Martinho"),
    ("MG", "Madagascar"), ("MH", "Ilhas Marshall"), ("MK", "Macedônia do Norte"),
    ("ML", "Mali"), ("MM", "Mianmar"), ("MN", "Mongólia"), ("MO", "Macau"),
    ("MP", "Ilhas Marianas do Norte"), ("MQ", "Martinica"), ("MR", "Mauritânia"),
    ("MS", "Montserrat"), ("MT", "Malta"), ("MU", "Maurício"), ("MV", "Maldivas"),
    ("MW", "Malaui"), ("MX", "México"), ("MY", "Malásia"), ("MZ", "Moçambique"),
    ("NA", "Namíbia"), ("NC", "Nova Caledônia"), ("NE", "Níger"), ("NF", "Ilha Norfolk"),
    ("NG", "Nigéria"), ("NI", "Nicarágua"), ("NL", "Países Baixos"), ("NO", "Noruega"),
    ("NP", "Nepal"), ("NR", "Nauru"), ("NU", "Niue"), ("NZ", "Nova Zelândia"),
    ("OM", "Omã"), ("PA", "Panamá"), ("PE", "Peru"), ("PF", "Polinésia Francesa"),
    ("PG", "Papua-Nova Guiné"), ("PH", "Filipinas"), ("PK", "Paquistão"), ("PL", "Polônia"),
    ("PM", "São Pedro e Miquelão"), ("PN", "Pitcairn"), ("PR", "Porto Rico"),
    ("PS", "Palestina"), ("PT", "Portugal"), ("PW", "Palau"), ("PY", "Paraguai"),
    ("QA", "Catar"), ("RE", "Reunião"), ("RO", "Romênia"), ("RS", "Sérvia"),
    ("RU", "Rússia"), ("RW", "Ruanda"), ("SA", "Arábia Saudita"), ("SB", "Ilhas Salomão"),
    ("SC", "Seicheles"), ("SD", "Sudão"), ("SE", "Suécia"), ("SG", "Singapura"),
    ("SH", "Santa Helena, Ascensão e Tristão da Cunha"), ("SI", "Eslovênia"),
    ("SJ", "Svalbard e Jan Mayen"), ("SK", "Eslováquia"), ("SL", "Serra Leoa"),
    ("SM", "San Marino"), ("SN", "Senegal"), ("SO", "Somália"), ("SR", "Suriname"),
    ("SS", "Sudão do Sul"), ("ST", "São Tomé e Príncipe"), ("SV", "El Salvador"),
    ("SX", "Sint Maarten"), ("SY", "Síria"), ("SZ", "Essuatíni"), ("TC", "Ilhas Turks e Caicos"),
    ("TD", "Chade"), ("TF", "Terras Austrais e Antárticas Francesas"), ("TG", "Togo"),
    ("TH", "Tailândia"), ("TJ", "Tajiquistão"), ("TK", "Tokelau"), ("TL", "Timor-Leste"),
    ("TM", "Turcomenistão"), ("TN", "Tunísia"), ("TO", "Tonga"), ("TR", "Turquia"),
    ("TT", "Trinidad e Tobago"), ("TV", "Tuvalu"), ("TW", "Taiwan"), ("TZ", "Tanzânia"),
    ("UA", "Ucrânia"), ("UG", "Uganda"), ("UM", "Ilhas Menores Distantes dos Estados Unidos"),
    ("US", "Estados Unidos"), ("UY", "Uruguai"), ("UZ", "Uzbequistão"), ("VA", "Santa Sé"),
    ("VC", "São Vicente e Granadinas"), ("VE", "Venezuela"), ("VG", "Ilhas Virgens Britânicas"),
    ("VI", "Ilhas Virgens Americanas"), ("VN", "Vietnã"), ("VU", "Vanuatu"),
    ("WF", "Wallis e Futuna"), ("WS", "Samoa"), ("YE", "Iêmen"), ("YT", "Mayotte"),
    ("ZA", "África do Sul"), ("ZM", "Zâmbia"), ("ZW", "Zimbábue"),
)
CLIENTE_PAISES_MAP = dict(CLIENTE_PAISES)

def pais_sort_key(item):
    nome = unicodedata.normalize("NFKD", item[1])
    nome = "".join(caractere for caractere in nome if not unicodedata.combining(caractere))
    return nome.casefold()

CLIENTE_PAISES_ORDENADOS = tuple(sorted(CLIENTE_PAISES, key=pais_sort_key))

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

    CREATE TABLE IF NOT EXISTS competencias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empresa_id INTEGER NOT NULL,
        descricao TEXT NOT NULL,
        data_inicial TEXT NOT NULL,
        data_final TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ABERTA',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE,
        UNIQUE(empresa_id, descricao)
    );

    CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL COLLATE NOCASE,
        pais TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(nome, pais)
    );

    CREATE TABLE IF NOT EXISTS contrapartes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL COLLATE NOCASE UNIQUE,
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
        ,competencia_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS contratos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_contrato TEXT NOT NULL UNIQUE,
        banco TEXT,
        banco_credito TEXT,
        banco_liquidacao TEXT,
        data_contrato TEXT,
        cnpj TEXT,
        cliente TEXT,
        moeda TEXT NOT NULL DEFAULT 'USD',
        valor_moeda REAL NOT NULL DEFAULT 0,
        taxa_cambio REAL,
        valor_reais REAL,
        status TEXT NOT NULL DEFAULT 'PENDENTE',
        saldo_zerado_manual INTEGER NOT NULL DEFAULT 0,
        observacao TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        ,competencia_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS ndfs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_operacao TEXT NOT NULL UNIQUE,
        cnpj TEXT NOT NULL,
        contraparte TEXT NOT NULL,
        tipo TEXT NOT NULL,
        moeda TEXT NOT NULL DEFAULT 'USD',
        valor_contratado REAL NOT NULL DEFAULT 0,
        taxa_contratada REAL NOT NULL,
        data_contratacao TEXT NOT NULL,
        data_vencimento TEXT NOT NULL,
        posicao TEXT NOT NULL,
        finalidade TEXT,
        observacao TEXT,
        status TEXT NOT NULL DEFAULT 'ATIVA',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        ,competencia_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS ptax_cotacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_cotacao TEXT NOT NULL,
        moeda TEXT NOT NULL,
        ptax_compra REAL NOT NULL,
        ptax_venda REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(moeda, data_cotacao)
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
        if "banco_id" not in contrato_columns:
            conn.execute("ALTER TABLE contratos ADD COLUMN banco_id INTEGER")
        if "banco_credito" not in contrato_columns:
            conn.execute("ALTER TABLE contratos ADD COLUMN banco_credito TEXT")
        if "banco_liquidacao" not in contrato_columns:
            conn.execute("ALTER TABLE contratos ADD COLUMN banco_liquidacao TEXT")
        conn.execute("UPDATE contratos SET banco_credito=COALESCE(NULLIF(banco_credito,''),banco), banco_liquidacao=COALESCE(NULLIF(banco_liquidacao,''),banco_credito,banco)")
        if "cliente_id" not in contrato_columns:
            conn.execute("ALTER TABLE contratos ADD COLUMN cliente_id INTEGER")
        if "saldo_zerado_manual" not in contrato_columns:
            conn.execute("ALTER TABLE contratos ADD COLUMN saldo_zerado_manual INTEGER NOT NULL DEFAULT 0")
        if "competencia_id" not in contrato_columns:
            conn.execute("ALTER TABLE contratos ADD COLUMN competencia_id INTEGER")
        due_columns = {row[1] for row in conn.execute("PRAGMA table_info(dues)")}
        if "competencia_id" not in due_columns:
            conn.execute("ALTER TABLE dues ADD COLUMN competencia_id INTEGER")
        ndf_columns = {row[1] for row in conn.execute("PRAGMA table_info(ndfs)")}
        if "cliente_id" not in ndf_columns:
            conn.execute("ALTER TABLE ndfs ADD COLUMN cliente_id INTEGER")
        if "contraparte_id" not in ndf_columns:
            conn.execute("ALTER TABLE ndfs ADD COLUMN contraparte_id INTEGER")
        if "competencia_id" not in ndf_columns:
            conn.execute("ALTER TABLE ndfs ADD COLUMN competencia_id INTEGER")
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

COMPETENCIA_STATUS_ABERTA = "ABERTA"
COMPETENCIA_STATUS_ENCERRADA = "ENCERRADA"
COMPETENCIA_STATUSES = (COMPETENCIA_STATUS_ABERTA, COMPETENCIA_STATUS_ENCERRADA)

def competencia_data(form, conn, current=None):
    descricao = (form.get("descricao") or "").strip()
    if not descricao:
        raise ValueError("A descrição da competência é obrigatória.")
    try:
        empresa_id = int(form.get("empresa_id"))
    except (TypeError, ValueError):
        empresa_id = current["empresa_id"] if current else None
    if not empresa_id or not conn.execute("SELECT id FROM empresas WHERE id=?", (empresa_id,)).fetchone():
        raise ValueError("Selecione uma empresa cadastrada.")
    data_inicial = parse_date(form.get("data_inicial"))
    data_final = parse_date(form.get("data_final"))
    if not data_inicial or not data_final:
        raise ValueError("As datas inicial e final são obrigatórias.")
    if data_inicial > data_final:
        raise ValueError("A data inicial não pode ser posterior à data final.")
    status = (form.get("status") or COMPETENCIA_STATUS_ABERTA).strip().upper()
    if status not in COMPETENCIA_STATUSES:
        raise ValueError("Selecione um status válido para a competência.")
    return {"empresa_id": empresa_id, "descricao": descricao, "data_inicial": data_inicial, "data_final": data_final, "status": status}

def sugerir_competencia(conn, empresa_id, data_recebimento):
    """Busca uma competência aberta que contenha a data de recebimento.

    Contratos ainda não possuem data_recebimento nem competencia_id; a função
    fica pronta para a integração futura sem alterar a regra atual.
    """
    data = parse_date(data_recebimento)
    if not data or not empresa_id:
        return None
    return conn.execute("""SELECT id, empresa_id, descricao, data_inicial, data_final, status
        FROM competencias WHERE empresa_id=? AND status=? AND data_inicial <= ? AND data_final >= ?
        ORDER BY data_inicial DESC, id DESC LIMIT 1""",
        (empresa_id, COMPETENCIA_STATUS_ABERTA, data, data)).fetchone()

def empresa_id_por_cnpj(conn, cnpj):
    if not cnpj:
        return None
    row = conn.execute("SELECT id FROM empresas WHERE cnpj=?", (re.sub(r"\D", "", str(cnpj)),)).fetchone()
    return row["id"] if row else None

def competencia_da_operacao(conn, raw_id, empresa_id, data_referencia, current_id=None):
    """Valida a competência da operação e sugere uma aberta pela data."""
    competencia_id = form_record_id(raw_id, current_id)
    if not competencia_id:
        sugerida = sugerir_competencia(conn, empresa_id, data_referencia)
        competencia_id = sugerida["id"] if sugerida else None
    if not competencia_id:
        raise ValueError("Selecione uma competência para a operação.")
    competencia = conn.execute("SELECT id FROM competencias WHERE id=? AND empresa_id=?", (competencia_id, empresa_id)).fetchone()
    if not competencia:
        raise ValueError("A competência selecionada não pertence à empresa da operação.")
    return competencia_id

def competencias_for_empresa(conn, empresa_id):
    if empresa_id:
        return conn.execute("""SELECT id, empresa_id, descricao, data_inicial, data_final, status
            FROM competencias WHERE empresa_id=? ORDER BY data_inicial DESC, descricao""", (empresa_id,)).fetchall()
    return conn.execute("""SELECT id, empresa_id, descricao, data_inicial, data_final, status
        FROM competencias ORDER BY data_inicial DESC, descricao""").fetchall()

@app.template_filter("pais_nome")
def pais_nome(value):
    codigo = str(value or "").strip().upper()
    return CLIENTE_PAISES_MAP.get(codigo, value or "-")

def normalize_pais(value):
    pais = (value or "").strip().upper()
    if pais not in CLIENTE_PAISES_MAP:
        raise ValueError("Selecione um país válido.")
    return pais

def clientes_for_form(conn):
    return conn.execute("""
        SELECT id, nome, pais
        FROM clientes
        ORDER BY nome, pais
    """).fetchall()

def contrapartes_for_form(conn):
    return conn.execute("""
        SELECT id, nome
        FROM contrapartes
        ORDER BY nome
    """).fetchall()

def form_record_id(value, fallback=None):
    if value in (None, ""):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def cliente_da_selecao(conn, raw_id, current_id=None, required=False):
    if raw_id in (None, ""):
        if current_id:
            cliente = conn.execute("SELECT id, nome, pais FROM clientes WHERE id=?", (current_id,)).fetchone()
            if cliente:
                return cliente
        if required:
            raise ValueError("Selecione um cliente cadastrado em Configurações.")
        return None
    try:
        cliente_id = int(raw_id)
    except (TypeError, ValueError):
        raise ValueError("Selecione um cliente cadastrado.")
    cliente = conn.execute("SELECT id, nome, pais FROM clientes WHERE id=?", (cliente_id,)).fetchone()
    if not cliente:
        raise ValueError("O cliente selecionado não foi encontrado.")
    return cliente

def contraparte_da_selecao(conn, raw_id, current_id=None, required=False):
    if raw_id in (None, ""):
        if current_id:
            contraparte = conn.execute("SELECT id, nome FROM contrapartes WHERE id=?", (current_id,)).fetchone()
            if contraparte:
                return contraparte
        if required:
            raise ValueError("Selecione um Banco / Contraparte cadastrado em Configurações.")
        return None
    try:
        contraparte_id = int(raw_id)
    except (TypeError, ValueError):
        raise ValueError("Selecione um Banco / Contraparte cadastrado.")
    contraparte = conn.execute("SELECT id, nome FROM contrapartes WHERE id=?", (contraparte_id,)).fetchone()
    if not contraparte:
        raise ValueError("O Banco / Contraparte selecionado não foi encontrado.")
    return contraparte

def banco_do_contrato(conn, raw_id, current=None):
    current_id = current["banco_id"] if current and "banco_id" in current.keys() else None
    contraparte = contraparte_da_selecao(conn, raw_id, current_id=current_id)
    if contraparte:
        return contraparte["id"], contraparte["nome"]
    return current_id, (current["banco"] if current else None)

def banco_id_for_form(conn, contrato=None, selected_id=None):
    if selected_id not in (None, ""):
        return form_record_id(selected_id)
    if not contrato:
        return None
    if "banco_id" in contrato.keys() and contrato["banco_id"]:
        return contrato["banco_id"]
    if contrato["banco"]:
        registro = conn.execute(
            "SELECT id FROM contrapartes WHERE nome=? COLLATE NOCASE",
            (contrato["banco"],),
        ).fetchone()
        return registro["id"] if registro else None
    return None

def banco_ids_for_contrato_form(conn, contrato=None, credito_id=None, liquidacao_id=None):
    """Resolve os dois bancos para o formulário, mantendo contratos legados."""
    credito_id = banco_id_for_form(conn, contrato, credito_id)
    credito_nome = ((contrato["banco_credito"] if "banco_credito" in contrato.keys() else None) if contrato else None) or ((contrato["banco"] if contrato and "banco" in contrato.keys() else None) if contrato else None)
    liquidacao_nome = ((contrato["banco_liquidacao"] if "banco_liquidacao" in contrato.keys() else None) if contrato else None) or credito_nome
    if liquidacao_id in (None, "") and liquidacao_nome:
        row = conn.execute("SELECT id FROM contrapartes WHERE nome=? COLLATE NOCASE", (liquidacao_nome,)).fetchone()
        liquidacao_id = row["id"] if row else None
    return credito_id, liquidacao_id

def bancos_do_contrato(conn, form, current=None):
    """Obtém crédito/liquidação sem perder o banco legado nem uma escolha manual."""
    credito_raw = form.get("banco_credito_id")
    if credito_raw in (None, ""):
        credito_raw = form.get("banco_id")
    credito_id, credito = banco_do_contrato(conn, credito_raw, current=current)
    anterior_credito = ((current["banco_credito"] if "banco_credito" in current.keys() else None) or
                        (current["banco"] if current else None)) if current else None
    anterior_liquidacao = ((current["banco_liquidacao"] if "banco_liquidacao" in current.keys() else None) or
                           anterior_credito) if current else None
    liquidacao_raw = form.get("banco_liquidacao_id")
    if liquidacao_raw in (None, ""):
        liquidacao_id, liquidacao = credito_id, credito
    else:
        liquidacao_id, liquidacao = banco_do_contrato(conn, liquidacao_raw)
        if current and anterior_liquidacao == anterior_credito and liquidacao == anterior_credito and credito != anterior_credito:
            liquidacao_id, liquidacao = credito_id, credito
    return credito_id, credito, liquidacao_id, liquidacao

def cliente_do_contrato(conn, raw_id, current=None, legacy_name=None):
    current_id = current["cliente_id"] if current and "cliente_id" in current.keys() else None
    cliente = cliente_da_selecao(conn, raw_id, current_id=current_id)
    if cliente:
        return cliente["id"], cliente["nome"]
    if legacy_name is not None:
        return current_id, (legacy_name or "").strip() or None
    return current_id, (current["cliente"] if current else None)

def cliente_id_for_form(contrato=None, selected_id=None):
    if selected_id not in (None, ""):
        return form_record_id(selected_id)
    if contrato and "cliente_id" in contrato.keys() and contrato["cliente_id"]:
        return contrato["cliente_id"]
    return None

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
        SELECT c.valor_moeda, c.saldo_zerado_manual,
               COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END), 0) AS vinculado
        FROM contratos c
        LEFT JOIN due_movimentacoes m ON m.contrato_id=c.id
        WHERE c.id=?
        GROUP BY c.id
    """, (contrato_id,)).fetchone()
    if not row:
        return None
    saldo = Decimal("0") if row["saldo_zerado_manual"] else contract_balance(row["valor_moeda"], row["vinculado"])
    status = STATUS_CONCLUIDO if row["saldo_zerado_manual"] else status_from_balance(saldo)
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
    data["saldo_zerado_manual"] = bool(data.get("saldo_zerado_manual"))
    data["saldo"] = Decimal("0") if data["saldo_zerado_manual"] else contract_balance(data.get("valor_moeda"), data["vinculado"])
    data["status"] = STATUS_CONCLUIDO if data["saldo_zerado_manual"] else status_from_balance(data["saldo"])
    return data

@app.template_filter("ndf_status_class")
def ndf_status_class(status):
    return {
        NDF_STATUS_ATIVA: "status-ndf-ativa",
        NDF_STATUS_VENCIDA: "status-ndf-vencida",
        NDF_STATUS_LIQUIDADA: "status-ndf-liquidada",
        NDF_STATUS_CANCELADA: "status-ndf-cancelada",
    }.get(status, "status-ndf-cancelada")

def ndf_display_status(status, data_vencimento):
    if status == NDF_STATUS_ATIVA and data_vencimento and data_vencimento < date.today().isoformat():
        return NDF_STATUS_VENCIDA
    return status

def decorate_ndf(row):
    data = dict(row)
    data["valor_contratado"] = decimal_value(data.get("valor_contratado"))
    data["taxa_contratada"] = decimal_value(data.get("taxa_contratada"))
    data["status_exibicao"] = ndf_display_status(data.get("status"), data.get("data_vencimento"))
    return data

def parse_ndf_form(form, conn, current=None):
    numero_operacao = (form.get("numero_operacao") or "").strip()
    if not numero_operacao:
        raise ValueError("O número/ID da operação é obrigatório.")

    cnpj = cnpj_da_empresa(conn, form.get("empresa_id"), current["cnpj"] if current else None)
    empresa_id = form_record_id(form.get("empresa_id"), empresa_id_por_cnpj(conn, cnpj))
    current_cliente_id = current["cliente_id"] if current and "cliente_id" in current.keys() else None
    current_contraparte_id = current["contraparte_id"] if current and "contraparte_id" in current.keys() else None
    cliente = cliente_da_selecao(
        conn, form.get("cliente_id"), current_id=current_cliente_id, required=current is None
    )
    contraparte_registro = contraparte_da_selecao(
        conn, form.get("contraparte_id"), current_id=current_contraparte_id, required=current is None
    )
    contraparte = contraparte_registro["nome"] if contraparte_registro else (current["contraparte"] if current else "")
    if not contraparte:
        raise ValueError("A contraparte é obrigatória.")

    tipo = (form.get("tipo") or "").strip().upper()
    if tipo not in NDF_TIPOS:
        raise ValueError("Selecione um tipo válido: Banco ou Trading.")

    moeda = (form.get("moeda") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", moeda):
        raise ValueError("A moeda deve conter exatamente 3 letras.")

    valor_bruto = form.get("valor_contratado")
    if not str(valor_bruto or "").strip():
        raise ValueError("O valor contratado é obrigatório.")
    valor_contratado = parse_number(valor_bruto)
    ensure_non_negative_balance(valor_contratado, "O valor contratado não pode ser negativo.")

    taxa_bruta = form.get("taxa_contratada")
    if not str(taxa_bruta or "").strip():
        raise ValueError("A taxa contratada é obrigatória.")
    taxa_contratada = parse_number(taxa_bruta)
    ensure_non_negative_balance(taxa_contratada, "A taxa contratada não pode ser negativa.")

    data_contratacao = parse_date(form.get("data_contratacao"))
    data_vencimento = parse_date(form.get("data_vencimento"))
    if not data_contratacao or not data_vencimento:
        raise ValueError("As datas de contratação e vencimento são obrigatórias.")
    if data_vencimento < data_contratacao:
        raise ValueError("A data de vencimento não pode ser anterior à data de contratação.")
    competencia_id = competencia_da_operacao(
        conn, form.get("competencia_id"), empresa_id, data_contratacao,
        current["competencia_id"] if current and "competencia_id" in current.keys() else None,
    )

    posicao = (form.get("posicao") or "").strip().upper()
    if posicao not in NDF_POSICOES:
        raise ValueError("Selecione uma posição válida: Compra ou Venda.")

    status = (form.get("status") or "").strip().upper()
    if status not in NDF_STATUSES:
        raise ValueError("Selecione um status válido.")

    return {
        "numero_operacao": numero_operacao,
        "cnpj": cnpj,
        "competencia_id": competencia_id,
        "cliente_id": cliente["id"] if cliente else None,
        "contraparte_id": contraparte_registro["id"] if contraparte_registro else None,
        "contraparte": contraparte,
        "tipo": tipo,
        "moeda": moeda,
        "valor_contratado": valor_contratado,
        "taxa_contratada": taxa_contratada,
        "data_contratacao": data_contratacao,
        "data_vencimento": data_vencimento,
        "posicao": posicao,
        "finalidade": (form.get("finalidade") or "").strip() or None,
        "observacao": (form.get("observacao") or "").strip() or None,
        "status": status,
    }

def parse_ptax_period(form):
    moeda = (form.get("moeda") or "").strip().upper()
    if moeda not in PTAX_MOEDAS:
        raise ValueError("Selecione uma moeda válida: USD ou EUR.")

    data_inicial = parse_date(form.get("data_inicial"))
    data_final = parse_date(form.get("data_final"))
    if not data_inicial or not data_final:
        raise ValueError("As datas inicial e final são obrigatórias.")
    if data_inicial > data_final:
        raise ValueError("A data inicial não pode ser posterior à data final.")
    return moeda, data_inicial, data_final

def ptax_api_url(moeda, data_inicial, data_final):
    parametros = urlencode({
        "@moeda": f"'{moeda}'",
        "@dataInicial": f"'{datetime.strptime(data_inicial, '%Y-%m-%d').strftime('%m-%d-%Y')}'",
        "@dataFinalCotacao": f"'{datetime.strptime(data_final, '%Y-%m-%d').strftime('%m-%d-%Y')}'",
        "$format": "json",
    })
    return (
        f"{PTAX_API_URL}(moeda=@moeda,dataInicial=@dataInicial,"
        f"dataFinalCotacao=@dataFinalCotacao)?{parametros}"
    )

def ptax_rate_value(value):
    if value is None or str(value).strip() in {"", "nan", "NaT", "None"}:
        return None
    try:
        rate_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not rate_value.is_finite() or rate_value <= 0:
        return None
    return float(rate_value)

def parse_ptax_rate(value, label):
    if not str(value or "").strip():
        raise ValueError(f"A {label} é obrigatória.")
    rate_value = ptax_rate_value(parse_number(value))
    if rate_value is None:
        raise ValueError(f"A {label} retornada é inválida.")
    return rate_value

def consultar_ptax_api(moeda, data_inicial, data_final):
    url = ptax_api_url(moeda, data_inicial, data_final)
    requisicao = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "DUE-Control PTAX",
    })
    try:
        with urlopen(requisicao, timeout=PTAX_API_TIMEOUT) as resposta:
            payload = json.loads(resposta.read().decode("utf-8"))
    except HTTPError as exc:
        raise ValueError(f"O Banco Central respondeu com erro HTTP {exc.code}.")
    except (URLError, TimeoutError, OSError):
        raise ValueError("Não foi possível acessar a API PTAX do Banco Central.")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("A API PTAX retornou uma resposta inválida.")

    registros_api = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(registros_api, list):
        raise ValueError("A API PTAX retornou um formato de dados inválido.")

    fechamentos = {}
    for registro in registros_api:
        if not isinstance(registro, dict):
            continue
        tipo_boletim = str(registro.get("tipoBoletim") or "").strip().casefold()
        if tipo_boletim != "fechamento":
            continue
        data_hora = str(registro.get("dataHoraCotacao") or "").strip()
        if not data_hora:
            continue
        data_cotacao = data_hora[:10]
        try:
            data_cotacao = parse_date(data_cotacao)
        except ValueError:
            continue
        ptax_compra = ptax_rate_value(registro.get("cotacaoCompra"))
        ptax_venda = ptax_rate_value(registro.get("cotacaoVenda"))
        if ptax_compra is None or ptax_venda is None:
            continue
        candidato = {
            "data_cotacao": data_cotacao,
            "moeda": moeda,
            "ptax_compra": ptax_compra,
            "ptax_venda": ptax_venda,
            "_data_hora": data_hora,
        }
        anterior = fechamentos.get(data_cotacao)
        if anterior is None or candidato["_data_hora"] > anterior["_data_hora"]:
            fechamentos[data_cotacao] = candidato

    resultado = []
    for registro in sorted(fechamentos.values(), key=lambda item: item["data_cotacao"], reverse=True):
        registro.pop("_data_hora", None)
        resultado.append(registro)
    return resultado

def parse_ptax_importacao(form):
    datas = form.getlist("cotacao_data")
    moedas = form.getlist("cotacao_moeda")
    compras = form.getlist("cotacao_compra")
    vendas = form.getlist("cotacao_venda")
    tamanhos = {len(datas), len(moedas), len(compras), len(vendas)}
    if len(tamanhos) != 1 or not datas:
        raise ValueError("A prévia de PTAX está incompleta e não pode ser importada.")

    registros = []
    for data, moeda, compra, venda in zip(datas, moedas, compras, vendas):
        moeda = (moeda or "").strip().upper()
        if moeda not in PTAX_MOEDAS:
            raise ValueError("A prévia contém uma moeda inválida.")
        data = parse_date(data)
        if not data:
            raise ValueError("A prévia contém uma data inválida.")
        registros.append({
            "data_cotacao": data,
            "moeda": moeda,
            "ptax_compra": parse_ptax_rate(compra, "PTAX Compra"),
            "ptax_venda": parse_ptax_rate(venda, "PTAX Venda"),
        })
    return registros

def render_ptax_page(previsao=None, consulta=None):
    consulta = consulta or {}
    form_values = {
        "moeda": consulta.get("moeda") or "USD",
        "data_inicial": consulta.get("data_inicial") or "",
        "data_final": consulta.get("data_final") or "",
    }
    conn = db()
    historico = conn.execute("""
        SELECT data_cotacao, moeda, ptax_compra, ptax_venda
        FROM ptax_cotacoes
        ORDER BY data_cotacao DESC, moeda ASC
    """).fetchall()
    conn.close()
    return render_template("ptax.html", moedas=PTAX_MOEDAS, consulta=form_values,
                           previsao=previsao, historico=historico)

def contract_summary(conn, contrato_id):
    row = conn.execute("""
        SELECT c.id, c.numero_contrato, c.moeda, c.valor_moeda, c.status,
               c.saldo_zerado_manual,
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

def contratos_filtros(args):
    empresa_id = form_record_id(args.get("empresa_id"))
    competencia_id = form_record_id(args.get("competencia_id"))
    where, params = [], []
    if empresa_id:
        where.append("e.id=?"); params.append(empresa_id)
    if competencia_id:
        where.append("c.competencia_id=?"); params.append(competencia_id)
    clause = " WHERE " + " AND ".join(where) if where else ""
    return empresa_id, competencia_id, clause, params

def consulta_contratos(conn, args):
    empresa_id, competencia_id, clause, params = contratos_filtros(args)
    rows = conn.execute(f"""
        SELECT c.*, e.id AS empresa_id_filtro, e.razao_social AS empresa_razao_social,
               e.apelido AS empresa_apelido, comp.descricao AS competencia_descricao,
               COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END),0) AS vinculado
        FROM contratos c
        LEFT JOIN empresas e ON REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(c.cnpj,''),'.',''),'/',''),'-',''),' ','')=e.cnpj
        LEFT JOIN competencias comp ON comp.id=c.competencia_id
        LEFT JOIN due_movimentacoes m ON m.contrato_id=c.id
        {clause}
        GROUP BY c.id ORDER BY c.id DESC
    """, params).fetchall()
    return rows, empresa_id, competencia_id

@app.route("/contratos")
def lista_contratos():
    conn = db()
    rows, empresa_id, competencia_id = consulta_contratos(conn, request.args)
    contratos = [decorate_contract(row) for row in rows]
    empresas = conn.execute("SELECT id, razao_social, apelido, cnpj FROM empresas ORDER BY razao_social").fetchall()
    competencias = conn.execute("SELECT id, descricao, data_inicial, data_final FROM competencias ORDER BY data_inicial DESC, descricao").fetchall()
    conn.close()
    return render_template("contratos.html", contratos=contratos, empresas=empresas, competencias=competencias,
                           empresa_id=empresa_id, competencia_id=competencia_id)

@app.route("/contratos/exportar")
def exportar_contratos():
    import pandas as pd
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    conn = db()
    rows, _, _ = consulta_contratos(conn, request.args)
    contrato_ids = [row["id"] for row in rows]
    vinculos = []
    if contrato_ids:
        placeholders = ",".join("?" for _ in contrato_ids)
        vinculos = conn.execute(f"""
            SELECT v.id, v.due_id, v.contrato_id, v.valor_vinculado, v.observacao,
                   v.created_at, c.numero_contrato, c.moeda AS contrato_moeda,
                   d.numero_due, d.chave_acesso, d.created_at AS data_lancamento,
                   COALESCE(SUM(m.valor), v.valor_vinculado) AS valor_calculado
            FROM due_contratos v
            JOIN contratos c ON c.id=v.contrato_id
            JOIN dues d ON d.id=v.due_id
            LEFT JOIN due_movimentacoes m ON m.due_contrato_id=v.id AND m.tipo='VINCULACAO'
            WHERE v.contrato_id IN ({placeholders})
            GROUP BY v.id
            ORDER BY c.numero_contrato, d.numero_due, v.id
        """, contrato_ids).fetchall()
    conn.close()

    def excel_value(value, field=None):
        if value is None:
            return None
        if field in {"data_contrato", "created_at", "data_lancamento"}:
            return date_br(value)
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, bool):
            return "Sim" if value else "Não"
        return value

    labels = {
        "id": "ID", "numero_contrato": "Número do contrato", "banco": "Banco legado",
        "banco_credito": "Banco de Crédito", "banco_liquidacao": "Banco de Liquidação",
        "data_contrato": "Data do contrato", "cnpj": "CNPJ", "cliente": "Cliente",
        "moeda": "Moeda", "valor_moeda": "Valor na moeda", "taxa_cambio": "Taxa de câmbio",
        "valor_reais": "Valor em reais", "status": "Status", "saldo_zerado_manual": "Saldo zerado manualmente",
        "observacao": "Observação", "created_at": "Criado em", "competencia_id": "Competência ID",
        "empresa_id_filtro": "Empresa ID", "empresa_razao_social": "Empresa - Razão social",
        "empresa_apelido": "Empresa - Apelido", "competencia_descricao": "Competência",
        "vinculado": "Total vinculado", "saldo": "Saldo disponível",
    }
    contratos_data = []
    for row in rows:
        item = decorate_contract(row)
        contratos_data.append({labels.get(key, key): excel_value(value, key) for key, value in item.items()})
    vinculos_data = []
    vinculo_labels = {
        "id": "Vínculo ID", "due_id": "DU-E ID", "contrato_id": "Contrato ID",
        "numero_contrato": "Número do contrato", "numero_due": "Número da DU-E",
        "chave_acesso": "Chave de acesso", "data_lancamento": "Data de lançamento",
        "contrato_moeda": "Moeda do contrato", "valor_vinculado": "Valor vinculado registrado",
        "valor_calculado": "Valor vinculado calculado", "observacao": "Observação",
        "created_at": "Vínculo criado em",
    }
    for row in vinculos:
        vinculos_data.append({vinculo_labels.get(key, key): excel_value(value, key) for key, value in dict(row).items()})

    contrato_columns = list(labels.values())
    vinculo_columns = list(vinculo_labels.values())
    contratos_df = pd.DataFrame(contratos_data, columns=contrato_columns)
    vinculos_df = pd.DataFrame(vinculos_data, columns=vinculo_columns)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        contratos_df.to_excel(writer, index=False, sheet_name="Contratos")
        vinculos_df.to_excel(writer, index=False, sheet_name="Vínculos")
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for cell in worksheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1769AA")
                cell.alignment = Alignment(horizontal="center")
            for column in worksheet.columns:
                letter = get_column_letter(column[0].column)
                width = min(max(max(len(str(cell.value or "")) for cell in column) + 2, 12), 45)
                worksheet.column_dimensions[letter].width = width
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="contratos_exportacao.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def build_contract_report_chart(rows, moeda):
    """Prepara o gráfico diário com fechamento médio ponderado pelo volume."""
    daily = {}
    for row in rows:
        if row["moeda"] != moeda or not row["data_contrato"]:
            continue
        item = daily.setdefault(row["data_contrato"], {"date": row["data_contrato"], "ptax": row["ptax_venda"], "volume": Decimal("0"), "weighted_rate": Decimal("0")})
        if item["ptax"] is None and row["ptax_venda"] is not None:
            item["ptax"] = row["ptax_venda"]
        if row["taxa_cambio"] is not None and row["valor_moeda"] > 0:
            item["volume"] += row["valor_moeda"]
            item["weighted_rate"] += row["valor_moeda"] * row["taxa_cambio"]
    daily = sorted(daily.values(), key=lambda item: item["date"])
    for item in daily:
        item["fechamento"] = item["weighted_rate"] / item["volume"] if item["volume"] else None
    values = [value for item in daily for value in (item["ptax"], item["fechamento"]) if value is not None]
    if not values:
        return {"moeda": moeda, "points": []}
    minimum, maximum = min(values), max(values)
    padding = max((maximum - minimum) * Decimal("0.12"), Decimal("0.001"))
    minimum -= padding; maximum += padding
    count = max(len(daily) - 1, 1)
    points = []
    for index, item in enumerate(daily):
        point = {"date": item["date"], "x": round(40 + index * 720 / count, 2)}
        for key in ("ptax", "fechamento"):
            value = item[key]
            point[key] = round(float(160 - ((value - minimum) / (maximum - minimum) * 130)), 2) if value is not None else None
        points.append(point)
    segments = {"ptax": [], "fechamento": []}
    for key in segments:
        current = []
        for point in points:
            if point[key] is None:
                if current: segments[key].append(current); current = []
            else:
                current.append(f"{point['x']},{point[key]}")
        if current: segments[key].append(current)
    return {"moeda": moeda, "points": points, "segments": segments}

@app.route("/contratos/relatorios")
def relatorios_contratos():
    data_de = request.args.get("data_de", "").strip(); data_ate = request.args.get("data_ate", "").strip()
    numero_contrato = request.args.get("numero_contrato", "").strip(); moeda = request.args.get("moeda", "").strip().upper()
    where, params = [], []
    try:
        inicio = parse_date(data_de) if data_de else None; fim = parse_date(data_ate) if data_ate else None
        if inicio and fim and inicio > fim:
            raise ValueError("A data inicial não pode ser posterior à data final.")
        if inicio: where.append("date(c.data_contrato) >= ?"); params.append(inicio)
        if fim: where.append("date(c.data_contrato) <= ?"); params.append(fim)
    except ValueError as exc:
        flash(str(exc), "danger")
    if numero_contrato: where.append("c.numero_contrato LIKE ?"); params.append(f"%{numero_contrato}%")
    if moeda: where.append("c.moeda = ?"); params.append(moeda)
    clause = " WHERE " + " AND ".join(where) if where else ""
    conn = db()
    rows = conn.execute(f"""SELECT c.id, c.numero_contrato, c.data_contrato, c.moeda, c.valor_moeda, c.taxa_cambio, p.ptax_venda
        FROM contratos c LEFT JOIN ptax_cotacoes p ON p.moeda=c.moeda AND p.data_cotacao=date(c.data_contrato)
        {clause} ORDER BY c.data_contrato DESC, c.numero_contrato ASC""", params).fetchall()
    conn.close()
    contratos, monthly = [], {}
    for row in rows:
        item = dict(row); item["valor_moeda"] = decimal_value(item["valor_moeda"])
        item["taxa_cambio"] = decimal_value(item["taxa_cambio"]) if item["taxa_cambio"] is not None else None
        item["ptax_venda"] = decimal_value(item["ptax_venda"]) if item["ptax_venda"] is not None else None
        # Resultado em R$: valor na moeda x (taxa de fechamento - PTAX venda).
        item["resultado"] = item["valor_moeda"] * (item["taxa_cambio"] - item["ptax_venda"]) if item["taxa_cambio"] is not None and item["ptax_venda"] is not None else None
        contratos.append(item); mes = (item["data_contrato"] or "")[:7]
        if not mes: continue
        resumo = monthly.setdefault(mes, {"contratos": 0, "volume": Decimal("0"), "resultado": Decimal("0"), "resultado_count": 0, "taxa_valor": Decimal("0"), "taxa_volume": Decimal("0"), "ptax_soma": Decimal("0"), "ptax_count": 0})
        resumo["contratos"] += 1; resumo["volume"] += item["valor_moeda"]
        if item["resultado"] is not None: resumo["resultado"] += item["resultado"]; resumo["resultado_count"] += 1
        if item["taxa_cambio"] is not None: resumo["taxa_valor"] += item["taxa_cambio"] * item["valor_moeda"]; resumo["taxa_volume"] += item["valor_moeda"]
        if item["ptax_venda"] is not None: resumo["ptax_soma"] += item["ptax_venda"]; resumo["ptax_count"] += 1
    for resumo in monthly.values():
        resumo["taxa_ponderada"] = resumo["taxa_valor"] / resumo["taxa_volume"] if resumo["taxa_volume"] else None
        resumo["ptax_media"] = resumo["ptax_soma"] / resumo["ptax_count"] if resumo["ptax_count"] else None
        resumo["resultado"] = resumo["resultado"] if resumo["resultado_count"] else None
    daily = {}
    for item in contratos:
        mes = (item["data_contrato"] or "")[:10]
        if not mes: continue
        key = (mes, item["moeda"])
        resumo = daily.setdefault(key, {"contratos": 0, "volume": Decimal("0"), "resultado": Decimal("0"), "resultado_count": 0, "taxa_valor": Decimal("0"), "taxa_volume": Decimal("0"), "ptax_soma": Decimal("0"), "ptax_count": 0})
        resumo["contratos"] += 1; resumo["volume"] += item["valor_moeda"]
        if item["resultado"] is not None: resumo["resultado"] += item["resultado"]; resumo["resultado_count"] += 1
        if item["taxa_cambio"] is not None: resumo["taxa_valor"] += item["taxa_cambio"] * item["valor_moeda"]; resumo["taxa_volume"] += item["valor_moeda"]
        if item["ptax_venda"] is not None: resumo["ptax_soma"] += item["ptax_venda"]; resumo["ptax_count"] += 1
    def finish_summary(summary):
        summary["taxa_ponderada"] = summary["taxa_valor"] / summary["taxa_volume"] if summary["taxa_volume"] else None
        summary["ptax_media"] = summary["ptax_soma"] / summary["ptax_count"] if summary["ptax_count"] else None
        summary["resultado"] = summary["resultado"] if summary["resultado_count"] else None
        return summary
    for summary in monthly.values(): finish_summary(summary)
    for summary in daily.values(): finish_summary(summary)
    moedas = sorted({item["moeda"] for item in contratos if item["moeda"]})
    graficos = [build_contract_report_chart(contratos, item) for item in moedas]
    return render_template("contratos_relatorios.html", contratos=contratos, mensais=sorted(monthly.items(), reverse=True), diarios=sorted(daily.items(), reverse=True), graficos=graficos, moedas=moedas, numero_contrato=numero_contrato, moeda=moeda, data_de=data_de, data_ate=data_ate)

@app.route("/derivativos")
def dashboard_derivativos():
    hoje = date.today().isoformat()
    conn = db()
    resumo_row = conn.execute("""
        SELECT COUNT(*) AS ativas,
               COALESCE(SUM(CASE WHEN moeda='USD' THEN valor_contratado ELSE 0 END), 0) AS usd_contratado,
               COALESCE(SUM(CASE WHEN moeda='USD' AND data_vencimento >= ?
                                 THEN valor_contratado ELSE 0 END), 0) AS usd_a_vencer
        FROM ndfs
        WHERE status=?
    """, (hoje, NDF_STATUS_ATIVA)).fetchone()
    proximos = [decorate_ndf(row) for row in conn.execute("""
        SELECT *
        FROM ndfs
        WHERE status=?
        ORDER BY CASE WHEN data_vencimento < ? THEN 0 ELSE 1 END,
                 data_vencimento ASC, id DESC
        LIMIT 5
    """, (NDF_STATUS_ATIVA, hoje)).fetchall()]
    conn.close()
    resumo = {
        "ativas": resumo_row["ativas"],
        "usd_contratado": decimal_value(resumo_row["usd_contratado"]),
        "usd_a_vencer": decimal_value(resumo_row["usd_a_vencer"]),
    }
    return render_template("derivativos.html", resumo=resumo, proximos=proximos)

@app.route("/derivativos/ndfs")
def lista_ndfs():
    conn = db()
    ndfs = [decorate_ndf(row) for row in conn.execute(
        "SELECT * FROM ndfs ORDER BY id DESC"
    ).fetchall()]
    conn.close()
    return render_template("ndfs.html", ndfs=ndfs)

@app.route("/ndf/novo", methods=["GET", "POST"])
def novo_ndf():
    conn = db()
    if request.method == "POST":
        try:
            data = parse_ndf_form(request.form, conn)
            conn.execute("""
                INSERT INTO ndfs
                    (numero_operacao,cnpj,cliente_id,contraparte_id,contraparte,tipo,moeda,valor_contratado,
                     taxa_contratada,data_contratacao,data_vencimento,posicao,
                     finalidade,observacao,status,competencia_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data["numero_operacao"], data["cnpj"], data["cliente_id"], data["contraparte_id"],
                data["contraparte"], data["tipo"],
                data["moeda"], data["valor_contratado"], data["taxa_contratada"],
                data["data_contratacao"], data["data_vencimento"], data["posicao"],
                data["finalidade"], data["observacao"], data["status"], data["competencia_id"],
            ))
            ndf_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            conn.close()
            flash("NDF cadastrada com sucesso.", "success")
            return redirect(url_for("detalhe_ndf", ndf_id=ndf_id))
        except sqlite3.IntegrityError:
            conn.rollback()
            flash("Já existe uma NDF com esse número/ID de operação.", "danger")
        except ValueError as exc:
            conn.rollback()
            flash(str(exc), "danger")
    empresas, empresa_id = empresas_for_form(
        conn, selected_id=request.form.get("empresa_id") if request.method == "POST" else None
    )
    clientes = clientes_for_form(conn)
    contrapartes = contrapartes_for_form(conn)
    cliente_id = form_record_id(request.form.get("cliente_id")) if request.method == "POST" else None
    contraparte_id = form_record_id(request.form.get("contraparte_id")) if request.method == "POST" else None
    competencias = competencias_for_empresa(conn, empresa_id)
    competencia_id = form_record_id(request.form.get("competencia_id")) if request.method == "POST" else None
    if not competencia_id and empresa_id:
        sugerida = sugerir_competencia(conn, empresa_id, request.form.get("data_contratacao") or date.today().isoformat())
        competencia_id = sugerida["id"] if sugerida else None
    conn.close()
    return render_template("ndf_form.html", ndf=None, empresas=empresas, empresa_id=empresa_id,
                           clientes=clientes, cliente_id=cliente_id,
                           contrapartes=contrapartes, contraparte_id=contraparte_id,
                           competencias=competencias, competencia_id=competencia_id,
                           ndf_tipos=NDF_TIPOS, ndf_posicoes=NDF_POSICOES, ndf_statuses=NDF_STATUSES)

@app.route("/ndf/<int:ndf_id>")
def detalhe_ndf(ndf_id):
    conn = db()
    ndf = conn.execute("""
        SELECT n.*, c.nome AS cliente_nome, c.pais AS cliente_pais
        FROM ndfs n
        LEFT JOIN clientes c ON c.id=n.cliente_id
        WHERE n.id=?
    """, (ndf_id,)).fetchone()
    conn.close()
    if not ndf:
        return "NDF não encontrada", 404
    return render_template("ndf_detalhe.html", ndf=decorate_ndf(ndf))

@app.route("/ndf/<int:ndf_id>/editar", methods=["GET", "POST"])
def editar_ndf(ndf_id):
    conn = db()
    ndf = conn.execute("SELECT * FROM ndfs WHERE id=?", (ndf_id,)).fetchone()
    if not ndf:
        conn.close()
        return "NDF não encontrada", 404
    if request.method == "POST":
        try:
            data = parse_ndf_form(request.form, conn, current=ndf)
            conn.execute("""
                UPDATE ndfs SET numero_operacao=?,cnpj=?,cliente_id=?,contraparte_id=?,contraparte=?,tipo=?,moeda=?,
                    valor_contratado=?,taxa_contratada=?,data_contratacao=?,data_vencimento=?,
                    posicao=?,finalidade=?,observacao=?,status=?,competencia_id=?
                WHERE id=?
            """, (
                data["numero_operacao"], data["cnpj"], data["cliente_id"], data["contraparte_id"],
                data["contraparte"], data["tipo"],
                data["moeda"], data["valor_contratado"], data["taxa_contratada"],
                data["data_contratacao"], data["data_vencimento"], data["posicao"],
                data["finalidade"], data["observacao"], data["status"], data["competencia_id"], ndf_id,
            ))
            conn.commit()
            conn.close()
            flash("NDF atualizada com sucesso.", "success")
            return redirect(url_for("detalhe_ndf", ndf_id=ndf_id))
        except sqlite3.IntegrityError:
            conn.rollback()
            flash("Já existe uma NDF com esse número/ID de operação.", "danger")
        except ValueError as exc:
            conn.rollback()
            flash(str(exc), "danger")
    empresas, empresa_id = empresas_for_form(
        conn, ndf["cnpj"], request.form.get("empresa_id") if request.method == "POST" else None
    )
    clientes = clientes_for_form(conn)
    contrapartes = contrapartes_for_form(conn)
    empresa_id = empresa_id_por_cnpj(conn, ndf["cnpj"])
    competencias = competencias_for_empresa(conn, empresa_id)
    competencia_id = form_record_id(request.form.get("competencia_id"), ndf["competencia_id"] if "competencia_id" in ndf.keys() else None)
    cliente_id = form_record_id(
        request.form.get("cliente_id") if request.method == "POST" else ndf["cliente_id"]
    )
    contraparte_id = form_record_id(
        request.form.get("contraparte_id") if request.method == "POST" else ndf["contraparte_id"]
    )
    ndf_data = decorate_ndf(ndf)
    conn.close()
    return render_template("ndf_form.html", ndf=ndf_data, empresas=empresas, empresa_id=empresa_id,
                           clientes=clientes, cliente_id=cliente_id,
                           contrapartes=contrapartes, contraparte_id=contraparte_id,
                           competencias=competencias, competencia_id=competencia_id,
                           ndf_tipos=NDF_TIPOS, ndf_posicoes=NDF_POSICOES, ndf_statuses=NDF_STATUSES)

@app.route("/ndf/<int:ndf_id>/excluir", methods=["POST"])
def excluir_ndf(ndf_id):
    conn = db()
    ndf = conn.execute("SELECT numero_operacao FROM ndfs WHERE id=?", (ndf_id,)).fetchone()
    if not ndf:
        conn.close()
        return "NDF não encontrada", 404
    conn.execute("DELETE FROM ndfs WHERE id=?", (ndf_id,))
    conn.commit()
    conn.close()
    flash(f"NDF {ndf['numero_operacao']} excluída com sucesso.", "success")
    return redirect(url_for("lista_ndfs"))

@app.route("/derivativos/ptax")
def ptax():
    return render_ptax_page()

@app.route("/derivativos/ptax/consultar", methods=["POST"])
def consultar_ptax():
    consulta = {
        "moeda": request.form.get("moeda"),
        "data_inicial": request.form.get("data_inicial"),
        "data_final": request.form.get("data_final"),
    }
    try:
        moeda, data_inicial, data_final = parse_ptax_period(request.form)
        previsao = consultar_ptax_api(moeda, data_inicial, data_final)
        return render_ptax_page(previsao=previsao, consulta=consulta)
    except ValueError as exc:
        flash(str(exc), "danger")
        return render_ptax_page(consulta=consulta)

@app.route("/derivativos/ptax/importar", methods=["POST"])
def importar_ptax():
    conn = db()
    try:
        registros = parse_ptax_importacao(request.form)
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany("""
            INSERT INTO ptax_cotacoes (data_cotacao,moeda,ptax_compra,ptax_venda)
            VALUES (?,?,?,?)
            ON CONFLICT(moeda,data_cotacao) DO UPDATE SET
                ptax_compra=excluded.ptax_compra,
                ptax_venda=excluded.ptax_venda
        """, [(
            registro["data_cotacao"], registro["moeda"],
            registro["ptax_compra"], registro["ptax_venda"],
        ) for registro in registros])
        conn.commit()
        flash(f"{len(registros)} cotação(ões) PTAX importada(s)/atualizada(s) com sucesso.", "success")
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "danger")
    except sqlite3.Error:
        conn.rollback()
        flash("Não foi possível gravar as cotações PTAX.", "danger")
    finally:
        conn.close()
    return redirect(url_for("ptax"))

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

@app.route("/configuracoes/clientes", methods=["GET", "POST"])
def cadastro_clientes():
    conn = db()
    nome = (request.form.get("nome") or "").strip() if request.method == "POST" else ""
    pais_selecionado = request.form.get("pais") if request.method == "POST" else ""
    if request.method == "POST":
        try:
            if not nome:
                raise ValueError("O nome do cliente é obrigatório.")
            pais = normalize_pais(pais_selecionado)
            conn.execute(
                "INSERT INTO clientes (nome, pais) VALUES (?, ?)",
                (nome, pais),
            )
            conn.commit()
            conn.close()
            flash("Cliente cadastrado com sucesso.", "success")
            return redirect(url_for("cadastro_clientes"))
        except sqlite3.IntegrityError:
            conn.rollback()
            flash("Já existe um cliente cadastrado com este nome e país.", "danger")
        except ValueError as exc:
            conn.rollback()
            flash(str(exc), "danger")
    clientes = conn.execute("""
        SELECT id, nome, pais
        FROM clientes
        ORDER BY nome, pais
    """).fetchall()
    conn.close()
    return render_template(
        "clientes.html", clientes=clientes, paises=CLIENTE_PAISES_ORDENADOS,
        nome=nome, pais_selecionado=pais_selecionado,
    )

@app.route("/configuracoes/contrapartes", methods=["GET", "POST"])
def cadastro_contrapartes():
    conn = db()
    nome = (request.form.get("nome") or "").strip() if request.method == "POST" else ""
    if request.method == "POST":
        try:
            if not nome:
                raise ValueError("O nome do Banco / Contraparte é obrigatório.")
            conn.execute(
                "INSERT INTO contrapartes (nome) VALUES (?)",
                (nome,),
            )
            conn.commit()
            conn.close()
            flash("Banco / Contraparte cadastrado com sucesso.", "success")
            return redirect(url_for("cadastro_contrapartes"))
        except sqlite3.IntegrityError:
            conn.rollback()
            flash("Já existe um Banco / Contraparte cadastrado com este nome.", "danger")
        except ValueError as exc:
            conn.rollback()
            flash(str(exc), "danger")
    contrapartes = conn.execute("""
        SELECT id, nome
        FROM contrapartes
        ORDER BY nome
    """).fetchall()
    conn.close()
    return render_template("contrapartes.html", contrapartes=contrapartes, nome=nome)

@app.route("/configuracoes/competencias", methods=["GET", "POST"])
def cadastro_competencias():
    conn = db()
    competencia = None
    if request.method == "POST":
        try:
            data = competencia_data(request.form, conn)
            conn.execute("INSERT INTO competencias (empresa_id,descricao,data_inicial,data_final,status) VALUES (?,?,?,?,?)",
                         (data["empresa_id"], data["descricao"], data["data_inicial"], data["data_final"], data["status"]))
            conn.commit(); conn.close()
            flash("Competência cadastrada com sucesso.", "success")
            return redirect(url_for("cadastro_competencias"))
        except sqlite3.IntegrityError:
            conn.rollback(); flash("Já existe uma competência com essa descrição para a empresa selecionada.", "danger")
        except ValueError as exc:
            conn.rollback(); flash(str(exc), "danger")
        competencia = dict(request.form)
    competencias = conn.execute("""SELECT c.id, c.empresa_id, c.descricao, c.data_inicial, c.data_final, c.status,
        e.razao_social, e.apelido FROM competencias c JOIN empresas e ON e.id=c.empresa_id
        ORDER BY c.data_inicial DESC, c.descricao""").fetchall()
    empresas = conn.execute("SELECT id, razao_social, apelido, cnpj FROM empresas ORDER BY razao_social").fetchall()
    conn.close()
    return render_template("competencias.html", competencia=competencia, competencias=competencias, empresas=empresas, statuses=COMPETENCIA_STATUSES)

@app.route("/configuracoes/competencias/<int:competencia_id>/editar", methods=["GET", "POST"])
def editar_competencia(competencia_id):
    conn = db()
    competencia = conn.execute("SELECT * FROM competencias WHERE id=?", (competencia_id,)).fetchone()
    if not competencia:
        conn.close(); return "Competência não encontrada", 404
    if request.method == "POST":
        try:
            data = competencia_data(request.form, conn, current=competencia)
            conn.execute("UPDATE competencias SET empresa_id=?, descricao=?, data_inicial=?, data_final=?, status=? WHERE id=?",
                         (data["empresa_id"], data["descricao"], data["data_inicial"], data["data_final"], data["status"], competencia_id))
            conn.commit(); conn.close()
            flash("Competência atualizada com sucesso.", "success")
            return redirect(url_for("cadastro_competencias"))
        except sqlite3.IntegrityError:
            conn.rollback(); flash("Já existe uma competência com essa descrição para a empresa selecionada.", "danger")
        except ValueError as exc:
            conn.rollback(); flash(str(exc), "danger")
        competencia = dict(request.form); competencia["id"] = competencia_id
    empresas = conn.execute("SELECT id, razao_social, apelido, cnpj FROM empresas ORDER BY razao_social").fetchall()
    competencias = conn.execute("""SELECT c.id, c.empresa_id, c.descricao, c.data_inicial, c.data_final, c.status,
        e.razao_social, e.apelido FROM competencias c JOIN empresas e ON e.id=c.empresa_id
        ORDER BY c.data_inicial DESC, c.descricao""").fetchall()
    conn.close()
    return render_template("competencias.html", competencia=competencia, competencias=competencias, empresas=empresas, statuses=COMPETENCIA_STATUSES)

@app.route("/configuracoes/competencias/<int:competencia_id>/encerrar", methods=["POST"])
def encerrar_competencia(competencia_id):
    conn = db()
    if not conn.execute("SELECT id FROM competencias WHERE id=?", (competencia_id,)).fetchone():
        conn.close(); return "Competência não encontrada", 404
    conn.execute("UPDATE competencias SET status=? WHERE id=?", (COMPETENCIA_STATUS_ENCERRADA, competencia_id))
    conn.commit(); conn.close()
    flash("Competência encerrada com sucesso.", "success")
    return redirect(url_for("cadastro_competencias"))

@app.route("/configuracoes/competencias/<int:competencia_id>/excluir", methods=["POST"])
def excluir_competencia(competencia_id):
    conn = db()
    if not conn.execute("SELECT id FROM competencias WHERE id=?", (competencia_id,)).fetchone():
        conn.close(); return "Competência não encontrada", 404
    conn.execute("DELETE FROM competencias WHERE id=?", (competencia_id,))
    conn.commit(); conn.close()
    flash("Competência excluída com sucesso.", "success")
    return redirect(url_for("cadastro_competencias"))

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
            banco_id, banco, banco_liquidacao_id, banco_liquidacao = bancos_do_contrato(conn, f)
            cliente_id, cliente = cliente_do_contrato(
                conn, f.get("cliente_id"), legacy_name=f.get("cliente")
            )
            cnpj = cnpj_da_empresa(conn, f.get("empresa_id"))
            empresa_id = form_record_id(f.get("empresa_id"), empresa_id_por_cnpj(conn, cnpj))
            data_contrato = parse_date(f.get("data_contrato"))
            competencia_id = competencia_da_operacao(conn, f.get("competencia_id"), empresa_id, data_contrato or date.today().isoformat())
            conn.execute("""INSERT INTO contratos
                (numero_contrato,banco_id,banco,banco_credito,banco_liquidacao,data_contrato,cnpj,cliente_id,cliente,moeda,valor_moeda,taxa_cambio,valor_reais,status,observacao,competencia_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f["numero_contrato"].strip(), banco_id, banco, banco, banco_liquidacao,
                 data_contrato, cnpj, cliente_id, cliente,
                 f.get("moeda") or "USD", valor_moeda,
                 optional_number(f.get("taxa_cambio")), optional_number(f.get("valor_reais")),
                 status_from_balance(valor_moeda), f.get("observacao"), competencia_id))
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
    contrapartes = contrapartes_for_form(conn)
    banco_credito_id, banco_liquidacao_id = banco_ids_for_contrato_form(
        conn, credito_id=request.form.get("banco_credito_id") or request.form.get("banco_id"),
        liquidacao_id=request.form.get("banco_liquidacao_id"),
    )
    clientes = clientes_for_form(conn)
    cliente_id = cliente_id_for_form(selected_id=request.form.get("cliente_id"))
    competencias = competencias_for_empresa(conn, empresa_id)
    competencia_id = form_record_id(request.form.get("competencia_id"))
    if not competencia_id and empresa_id:
        sugerida = sugerir_competencia(conn, empresa_id, request.form.get("data_contrato") or date.today().isoformat())
        competencia_id = sugerida["id"] if sugerida else None
    conn.close()
    return render_template("contrato_form.html", contrato=None, empresas=empresas, empresa_id=empresa_id,
                           contrapartes=contrapartes, banco_id=banco_credito_id,
                           banco_credito_id=banco_credito_id, banco_liquidacao_id=banco_liquidacao_id,
                           clientes=clientes, cliente_id=cliente_id,
                           competencias=competencias, competencia_id=competencia_id)

def carregar_detalhe_contrato(conn, contrato_id):
    contrato = conn.execute("""
        SELECT c.*, cl.pais AS cliente_pais,
               e.razao_social AS empresa_razao_social,
               e.apelido AS empresa_apelido
        FROM contratos c
        LEFT JOIN clientes cl ON cl.id=c.cliente_id
        LEFT JOIN empresas e ON e.cnpj=c.cnpj
        WHERE c.id=?
    """, (contrato_id,)).fetchone()
    if not contrato:
        return None
    vinculos = conn.execute("""
        SELECT v.*, m.id AS movimentacao_id, m.valor AS valor_movimentacao,
               d.chave_acesso, d.numero_due, d.created_at AS data_lancamento,
               d.cliente AS cliente_due
        FROM due_contratos v JOIN dues d ON d.id=v.due_id
        LEFT JOIN due_movimentacoes m ON m.due_contrato_id=v.id AND m.tipo='VINCULACAO'
        WHERE v.contrato_id=? ORDER BY v.id DESC
    """, (contrato_id,)).fetchall()
    summary = contract_summary(conn, contrato_id)
    contrato = dict(contrato)
    contrato.update({"vinculado": summary["vinculado"], "saldo": summary["saldo"], "status": summary["status"]})
    return contrato, vinculos, summary

@app.route("/contrato/<int:contrato_id>")
def detalhe_contrato(contrato_id):
    conn = db()
    dados = carregar_detalhe_contrato(conn, contrato_id)
    conn.close()
    if not dados:
        return "Contrato não encontrado", 404
    contrato, vinculos, summary = dados
    return render_template("contrato_detalhe.html", contrato=contrato, vinculos=vinculos,
                           vinculado=summary["vinculado"], saldo=summary["saldo"])

@app.route("/contrato/<int:contrato_id>/relatorio")
def relatorio_contrato(contrato_id):
    conn = db()
    dados = carregar_detalhe_contrato(conn, contrato_id)
    conn.close()
    if not dados:
        return "Contrato não encontrado", 404
    contrato, vinculos, summary = dados
    return render_template("contrato_relatorio.html", contrato=contrato, vinculos=vinculos,
                           vinculado=summary["vinculado"], saldo=summary["saldo"])

@app.route("/contrato/<int:contrato_id>/editar", methods=["GET", "POST"])
def editar_contrato(contrato_id):
    conn = db()
    contrato = conn.execute("SELECT * FROM contratos WHERE id=?", (contrato_id,)).fetchone()
    if not contrato:
        conn.close(); return "Contrato não encontrado", 404
    resumo = contract_summary(conn, contrato_id)
    if request.method == "POST":
        f = request.form
        try:
            valor_moeda = parse_number(f.get("valor_moeda"))
            linked = resumo["vinculado"]
            ensure_non_negative_balance(
                decimal_value(valor_moeda) - linked,
                "O valor do contrato não pode ficar abaixo do total já vinculado."
            )
            banco_id, banco, banco_liquidacao_id, banco_liquidacao = bancos_do_contrato(conn, f, current=contrato)
            cliente_id, cliente = cliente_do_contrato(
                conn, f.get("cliente_id"), current=contrato, legacy_name=f.get("cliente")
            )
            cnpj = cnpj_da_empresa(conn, f.get("empresa_id"), contrato["cnpj"])
            empresa_id = form_record_id(f.get("empresa_id"), empresa_id_por_cnpj(conn, cnpj))
            data_contrato = parse_date(f.get("data_contrato"))
            competencia_id = competencia_da_operacao(conn, f.get("competencia_id"), empresa_id, data_contrato or date.today().isoformat(), contrato["competencia_id"] if "competencia_id" in contrato.keys() else None)
            conn.execute("""UPDATE contratos SET numero_contrato=?,banco_id=?,banco=?,banco_credito=?,banco_liquidacao=?,data_contrato=?,cnpj=?,cliente_id=?,cliente=?,moeda=?,
                            valor_moeda=?,taxa_cambio=?,valor_reais=?,status=?,observacao=?,competencia_id=? WHERE id=?""",
                (f["numero_contrato"].strip(), banco_id, banco, banco, banco_liquidacao, data_contrato, cnpj,
                 cliente_id, cliente, f.get("moeda") or "USD", valor_moeda,
                 optional_number(f.get("taxa_cambio")), optional_number(f.get("valor_reais")),
                 STATUS_CONCLUIDO if resumo["saldo_zerado_manual"] else status_from_balance(contract_balance(valor_moeda, linked)),
                 f.get("observacao"), competencia_id, contrato_id))
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
    contrapartes = contrapartes_for_form(conn)
    banco_credito_id, banco_liquidacao_id = banco_ids_for_contrato_form(
        conn, contrato,
        request.form.get("banco_credito_id") if request.method == "POST" else None,
        request.form.get("banco_liquidacao_id") if request.method == "POST" else None,
    )
    clientes = clientes_for_form(conn)
    cliente_id = cliente_id_for_form(
        contrato,
        request.form.get("cliente_id") if request.method == "POST" else None,
    )
    competencias = competencias_for_empresa(conn, empresa_id)
    competencia_id = form_record_id(request.form.get("competencia_id"), contrato["competencia_id"] if "competencia_id" in contrato.keys() else None)
    conn.close()
    return render_template("contrato_form.html", contrato=contrato, resumo=resumo, empresas=empresas, empresa_id=empresa_id,
                           contrapartes=contrapartes, banco_id=banco_credito_id,
                           banco_credito_id=banco_credito_id, banco_liquidacao_id=banco_liquidacao_id,
                           clientes=clientes, cliente_id=cliente_id,
                           competencias=competencias, competencia_id=competencia_id)

@app.route("/contrato/<int:contrato_id>/zerar-saldo", methods=["POST"])
def zerar_saldo(contrato_id):
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        contrato = contract_summary(conn, contrato_id)
        if not contrato:
            conn.rollback()
            return "Contrato não encontrado", 404
        if contrato["saldo_zerado_manual"]:
            raise ValueError("O saldo deste contrato já foi zerado manualmente.")
        if contrato["saldo"] <= 0:
            raise ValueError("Só é possível zerar manualmente contratos com saldo positivo.")
        conn.execute("UPDATE contratos SET saldo_zerado_manual=1,status=? WHERE id=?",
                     (STATUS_CONCLUIDO, contrato_id))
        conn.commit()
        flash("Saldo do contrato zerado manualmente e contrato marcado como concluído.", "success")
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "danger")
    finally:
        conn.close()
    return redirect(url_for("editar_contrato", contrato_id=contrato_id))

@app.route("/contrato/<int:contrato_id>/reverter-saldo", methods=["POST"])
def reverter_saldo(contrato_id):
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        contrato = conn.execute("SELECT saldo_zerado_manual FROM contratos WHERE id=?", (contrato_id,)).fetchone()
        if not contrato:
            conn.rollback()
            return "Contrato não encontrado", 404
        if not contrato["saldo_zerado_manual"]:
            raise ValueError("Este contrato não possui zeramento manual ativo.")
        conn.execute("UPDATE contratos SET saldo_zerado_manual=0 WHERE id=?", (contrato_id,))
        update_contract_status(conn, contrato_id)
        conn.commit()
        flash("Zeramento manual revertido; saldo recalculado com os vínculos atuais.", "success")
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "danger")
    finally:
        conn.close()
    return redirect(url_for("editar_contrato", contrato_id=contrato_id))

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
            empresa_id = form_record_id(f.get("empresa_id"), empresa_id_por_cnpj(conn, cnpj))
            data_due = parse_date(f.get("data_due")) or date.today().isoformat()
            competencia_id = competencia_da_operacao(conn, f.get("competencia_id"), empresa_id, data_due)
            if conn.execute("SELECT 1 FROM dues WHERE chave_acesso=?", (chave,)).fetchone():
                raise ValueError("A Chave de Acesso já está cadastrada.")
            conn.execute("""INSERT INTO dues
                (chave_acesso,numero_due,cnpj,cliente,moeda,valor_original,status,created_at,observacao,data_due,competencia_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (chave,f["numero_due"].strip(),cnpj,f.get("cliente"),
                 f.get("moeda") or "USD",valor_original, status_from_balance(valor_original),
                 launch_timestamp(),
                 f.get("observacao"), data_due, competencia_id))
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
    competencias = competencias_for_empresa(conn, empresa_id)
    competencia_id = form_record_id(request.form.get("competencia_id"))
    if not competencia_id and empresa_id:
        sugerida = sugerir_competencia(conn, empresa_id, request.form.get("data_due") or date.today().isoformat())
        competencia_id = sugerida["id"] if sugerida else None
    conn.close()
    return render_template("due_form.html", due=None, empresas=empresas, empresa_id=empresa_id, competencias=competencias, competencia_id=competencia_id)

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
            empresa_id = form_record_id(f.get("empresa_id"), empresa_id_por_cnpj(conn, cnpj))
            data_due = parse_date(f.get("data_due")) or due["data_due"] or date.today().isoformat()
            competencia_id = competencia_da_operacao(conn, f.get("competencia_id"), empresa_id, data_due, due["competencia_id"] if "competencia_id" in due.keys() else None)
            if conn.execute("SELECT 1 FROM dues WHERE chave_acesso=? AND id<>?", (chave, due_id)).fetchone():
                raise ValueError("A Chave de Acesso já está cadastrada.")
            conn.execute("""UPDATE dues SET chave_acesso=?,numero_due=?,cnpj=?,cliente=?,moeda=?,valor_original=?,observacao=?,data_due=?,competencia_id=?
                            WHERE id=?""",
                         (chave, f["numero_due"].strip(), cnpj,
                          f.get("cliente"), f.get("moeda") or "USD", valor_original,
                          f.get("observacao"), data_due, competencia_id, due_id))
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
    competencias = competencias_for_empresa(conn, empresa_id)
    competencia_id = form_record_id(request.form.get("competencia_id"), due["competencia_id"] if "competencia_id" in due.keys() else None)
    conn.close()
    return render_template("due_form.html", due=due, empresas=empresas, empresa_id=empresa_id, competencias=competencias, competencia_id=competencia_id)

def carregar_detalhe_due(conn, due_id):
    due_row=conn.execute("SELECT * FROM dues WHERE id=?", (due_id,)).fetchone()
    if not due_row:
        return None
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
                               HAVING c.saldo_zerado_manual=0
                                  AND c.valor_moeda-COALESCE(SUM(CASE WHEN m.tipo='VINCULACAO' THEN m.valor ELSE 0 END),0)>?
                               ORDER BY c.numero_contrato""", (float(SALDO_TOLERANCE),)).fetchall()]
    utilizado=due_effect(conn, due_id)
    due = decorate_due({**dict(due_row), "utilizado": utilizado})
    saldo=due["saldo"]
    return due, mov, vinc, contratos, utilizado, saldo

@app.route("/due/<int:due_id>")
def due_detalhe(due_id):
    conn=db()
    dados=carregar_detalhe_due(conn, due_id)
    conn.close()
    if not dados:
        return "DU-E não encontrada",404
    due, mov, vinc, contratos, utilizado, saldo = dados
    return render_template("due_detalhe.html", due=due, mov=mov, vinc=vinc, contratos=contratos,
                           utilizado=utilizado, saldo=saldo)

@app.route("/due/<int:due_id>/relatorio")
def relatorio_due(due_id):
    conn=db()
    dados=carregar_detalhe_due(conn, due_id)
    conn.close()
    if not dados:
        return "DU-E não encontrada",404
    due, mov, vinc, contratos, utilizado, saldo = dados
    historico=[]
    saldo_parcial=decimal_value(due.get("valor_original"))
    for item in reversed(mov):
        valor=decimal_value(item["valor"])
        if item["tipo"] in ("UTILIZACAO", "VINCULACAO"):
            saldo_parcial-=valor
        elif item["tipo"] == "DEVOLUCAO":
            saldo_parcial+=valor
        historico.append({"mov": item, "saldo": saldo_parcial})
    return render_template("due_relatorio.html", due=due, vinc=vinc, historico=historico,
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
        if not contrato:
            raise ValueError("Contrato não encontrado.")
        if contrato["saldo_zerado_manual"]:
            raise ValueError("O contrato foi zerado manualmente e não pode receber vínculos.")
        if contrato["status"] != STATUS_PENDENTE:
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
            (numero_contrato,banco,banco_credito,banco_liquidacao,data_contrato,cnpj,cliente,moeda,valor_moeda,taxa_cambio,valor_reais,status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(numero_contrato) DO UPDATE SET
            banco=excluded.banco,banco_credito=excluded.banco_credito,banco_liquidacao=excluded.banco_liquidacao,
            data_contrato=excluded.data_contrato,cnpj=excluded.cnpj,cliente=excluded.cliente,
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
                    banco_credito = val("banco_credito") or val("banco")
                    banco_liquidacao = val("banco_liquidacao") or banco_credito
                    banco_credito = str(banco_credito).strip() if banco_credito is not None else None
                    banco_liquidacao = str(banco_liquidacao).strip() if banco_liquidacao is not None else banco_credito
                    conn.execute(query, (numero, banco_credito, banco_credito, banco_liquidacao, parse_date(val("data_contrato")), val("cnpj"), val("cliente"),
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
    df=pd.DataFrame(columns=["numero_contrato","banco_credito","banco_liquidacao","data_contrato","cnpj","cliente","moeda","valor_moeda","taxa_cambio","valor_reais"])
    out=io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer,index=False,sheet_name="Contratos")
    out.seek(0)
    return send_file(out,as_attachment=True,download_name="modelo_contratos.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
