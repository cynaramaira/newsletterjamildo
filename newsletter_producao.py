import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import gspread
import requests
from bs4 import BeautifulSoup
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    OrderBy,
    RunReportRequest,
)

# 1. Configurações de ambiente e credenciais
conteudo_credenciais = os.getenv("GA4_CREDENTIALS_JSON")
if conteudo_credenciais:
    with open("credenciais.json", "w", encoding="utf-8") as f:
        f.write(conteudo_credenciais.strip())

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "credenciais.json"
GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "443865423")

BREVO_SMTP_LOGIN = os.getenv("BREVO_SMTP_LOGIN", "")
BREVO_SMTP_KEY = os.getenv("BREVO_SMTP_KEY", "")
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE", "jamildo.com@gmail.com")

HEADERS_REQUISICAO = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

def carregar_inscritos_google_sheets(nome_planilha="Inscritos Newsletter Jamildo"):
    """Lê a lista de e-mails da planilha privada compartilhada com a Conta de Serviço."""
    try:
        gc = gspread.service_account(filename="credenciais.json")
        planilha = gc.open(nome_planilha).sheet1

        valores_coluna_a = planilha.col_values(1)
        if len(valores_coluna_a) > 1:
            emails_brutos = valores_coluna_a[1:]
        else:
            emails_brutos = []

        inscritos = [e.strip() for e in emails_brutos if "@" in e and "." in e]
        inscritos_unicos = list(dict.fromkeys(inscritos))

        print(f"Base de leitores carregada: {len(inscritos_unicos)} inscrito(s) ativo(s).")
        return inscritos_unicos
    except Exception as e:
        print(f"Erro ao ler inscritos no Google Sheets: {e}")
        return [EMAIL_REMETENTE]

def consultar_mais_lidas_ga4():
    """Recupera os 5 artigos com maior volume de acessos hoje no GA4."""
    cliente = BetaAnalyticsDataClient()
    solicitacao = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        dimensions=[
            Dimension(name="pageTitle"),
            Dimension(name="pagePath"),
        ],
        metrics=[Metric(name="screenPageViews")],
        date_ranges=[DateRange(start_date="today", end_date="today")],
        order_bys=[
            OrderBy(
                metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"),
                desc=True,
            )
        ],
        limit=30,
    )

    resposta = cliente.run_report(solicitacao)
    urls_identificadas = []
    caminhos_unicos = set()

    rotas_excluidas = ["/", "/busca", "/quem-somos", "/contato", "/politica-de-privacidade"]

    for linha in resposta.rows:
        caminho = linha.dimension_values[1].value
        if caminho in rotas_excluidas or len(caminho) <= 3:
            continue
        if caminho.startswith("/canal/") or "/categoria/" in caminho or caminho.startswith("/tag/"):
            continue

        caminho_higienizado = caminho.replace("/amp/", "/")
        if caminho_higienizado in caminhos_unicos:
            continue
        caminhos_unicos.add(caminho_higienizado)

        urls_identificadas.append(f"https://jamildo.com{caminho_higienizado}")
        if len(urls_identificadas) == 5:
            break

    return urls_identificadas

def extrair_metadados_materia(url):
    """Acessa a página da notícia e recupera foto de capa, resumo e título."""
    registro = {
        "url": url,
        "titulo": "",
        "linha_fina": "",
        "imagem": ""
    }

    try:
        resposta = requests.get(url, headers=HEADERS_REQUISICAO, timeout=12)
        if resposta.status_code == 200:
            sopa = BeautifulSoup(resposta.text, "html.parser")

            tag_og_title = sopa.find("meta", property="og:title")
            if tag_og_title and tag_og_title.get("content"):
                titulo_bruto = tag_og_title["content"]
            else:
                tag_h1 = sopa.find("h1")
                titulo_bruto = tag_h1.text.strip() if tag_h1 else ""

            registro["titulo"] = (
                titulo_bruto.replace("Jamildo · ", "")
                .replace(" - Jamildo", "")
                .replace(" | Jamildo.com", "")
                .strip()
            )

            tag_og_desc = sopa.find("meta", property="og:description") or sopa.find("meta", attrs={"name": "description"})
            if tag_og_desc and tag_og_desc.get("content") and not tag_og_desc["content"].startswith("Listagem de"):
                registro["linha_fina"] = tag_og_desc["content"].strip()
            else:
                seletor_sub =
