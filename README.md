# Sistema DU-E + Contratos de Câmbio

V1 local em Python + Flask + SQLite.

## Instalação no Windows

1. Instale Python 3.11+.
2. Abra o terminal nesta pasta.
3. Crie ambiente virtual:
   `python -m venv .venv`
4. Ative:
   `.venv\Scripts\activate`
5. Instale:
   `pip install -r requirements.txt`
6. Execute:
   `python app.py`
7. Abra no navegador:
   `http://127.0.0.1:5000`

O banco `due.db` é criado automaticamente.

## Excel de contratos

Use "Baixar modelo Excel". A coluna obrigatória é `numero_contrato`.
Se o número já existir, a importação atualiza o cadastro em vez de duplicar.

Atenção: esta é uma V1 básica. Antes de uso operacional, defina política de backup e a chave secreta.
