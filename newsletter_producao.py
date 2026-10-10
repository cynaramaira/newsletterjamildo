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

GMAIL_USER = os.getenv("GMAIL_USER", "jamildo.com@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

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
        return [GMAIL_USER]

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
                seletor_sub = sopa.select_one(".col-content p.lead, .subtitulo, .linha-fina")
                if seletor_sub:
                    registro["linha_fina"] = seletor_sub.text.strip()

            tag_og_img = sopa.find("meta", property="og:image")
            if tag_og_img and tag_og_img.get("content"):
                registro["imagem"] = tag_og_img["content"].strip()
            else:
                seletor_img = sopa.select_one(".col-content img, article img")
                if seletor_img and seletor_img.get("src"):
                    registro["imagem"] = seletor_img["src"].strip()

            if registro["imagem"] and not registro["imagem"].startswith("http"):
                registro["imagem"] = f"https://jamildo.com{registro['imagem']}"

    except Exception as erro:
        print(f"Erro ao extrair metadados de {url}: {erro}")

    return registro

def construir_estrutura_html(noticias):
    """Monta o e-mail responsivo nas cores Azul (#024796) e Laranja (#f83d03)."""
    destaque = noticias[0]
    secundarias = noticias[1:]

    blocos_secundarios = ""
    for idx, item in enumerate(secundarias, start=2):
        html_foto = ""
        if item["imagem"]:
            html_foto = f"""
            <td width="130" valign="top" style="padding-right: 15px;">
                <a href="{item['url']}" target="_blank">
                    <img src="{item['imagem']}" alt="{item['titulo']}" width="130" style="width: 100%; max-width: 130px; height: auto; border-radius: 4px; display: block;">
                </a>
            </td>
            """

        blocos_secundarios += f"""
        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-bottom: 20px; border-bottom: 1px solid #eeeeee; padding-bottom: 16px;">
            <tr>
                {html_foto}
                <td valign="top">
                    <span style="font-size: 11px; font-weight: bold; color: #f83d03; text-transform: uppercase;">Top {idx} do dia</span>
                    <h3 style="margin: 4px 0 6px 0; font-size: 16px; line-height: 1.35;">
                        <a href="{item['url']}" target="_blank" style="color: #024796; text-decoration: none; font-weight: bold;">
                            {item['titulo']}
                        </a>
                    </h3>
                    <p style="margin: 0; font-size: 13px; line-height: 1.4; color: #555555;">
                        {item['linha_fina']}
                    </p>
                </td>
            </tr>
        </table>
        """

    foto_capa = ""
    if destaque["imagem"]:
        foto_capa = f"""
        <div style="margin-bottom: 14px;">
            <a href="{destaque['url']}" target="_blank">
                <img src="{destaque['imagem']}" alt="{destaque['titulo']}" width="552" style="width: 100%; max-width: 552px; height: auto; border-radius: 6px; display: block;">
            </a>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="pt-br">
<head><meta charset="utf-8"></head>
<body style="margin: 0; padding: 25px 0; background-color: #f0f2f5; font-family: Arial, Helvetica, sans-serif;">
    <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #e0e0e0;">
        <tr>
            <td align="center" style="background-color: #024796; padding: 22px;">
                <img src="https://jamildo.com/media/uploads/brancologojamildo_6xe6mA0.png" alt="Jamildo.com" width="180" style="display: block; max-width: 180px;">
            </td>
        </tr>
        <tr>
            <td style="background-color: #f83d03; padding: 6px 20px; text-align: center; color: #ffffff; font-size: 12px; font-weight: bold; letter-spacing: 0.5px;">
                GIRO DIÁRIO &bull; AS 5 NOTÍCIAS MAIS LIDAS
            </td>
        </tr>
        <tr>
            <td style="padding: 24px;">
                <div style="border: 2px solid #024796; border-radius: 6px; padding: 16px; margin-bottom: 26px; background-color: #ffffff;">
                    {foto_capa}
                    <span style="background-color: #f83d03; color: #ffffff; padding: 3px 8px; font-size: 11px; font-weight: bold; border-radius: 3px; text-transform: uppercase;">
                        #1 Mais Lida do Dia
                    </span>
                    <h2 style="margin: 10px 0 8px 0; font-size: 19px; line-height: 1.3;">
                        <a href="{destaque['url']}" target="_blank" style="color: #024796; text-decoration: none; font-weight: bold;">
                            {destaque['titulo']}
                        </a>
                    </h2>
                    <p style="margin: 0 0 14px 0; font-size: 14px; line-height: 1.45; color: #444444;">
                        {destaque['linha_fina']}
                    </p>
                    <a href="{destaque['url']}" target="_blank" style="display: inline-block; background-color: #024796; color: #ffffff; padding: 9px 16px; border-radius: 4px; text-decoration: none; font-weight: bold; font-size: 13px;">
                        Acessar reportagem completa &rarr;
                    </a>
                </div>

                <h4 style="color: #024796; border-bottom: 2px solid #024796; padding-bottom: 6px; margin: 0 0 20px 0; font-size: 14px; text-transform: uppercase;">
                    Outros destaques editoriais
                </h4>

                {blocos_secundarios}
            </td>
        </tr>
        <tr>
            <td style="background-color: #f7f8fa; padding: 18px 24px; text-align: center; border-top: 1px solid #eeeeee;">
                <p style="font-size: 12px; color: #777777; margin: 0; line-height: 1.4;">
                    Curadoria de notícias produzida pela redação do <strong>Jamildo.com</strong>.
                </p>
            </td>
        </tr>
    </table>
</body>
</html>
"""

def processar_envio():
    modo = os.getenv("INPUT_MODO", "auto")
    links_informados = os.getenv("INPUT_LINKS", "")

    if modo == "manual" and links_informados:
        print("Acionamento em Modo Manual detectado.")
        urls_alvo = [u.strip() for u in links_informados.split(",") if u.strip()][:5]
    else:
        print("Acionamento em Modo Automático detectado (GA4).")
        urls_alvo = consultar_mais_lidas_ga4()

    if len(urls_alvo) < 5:
        print(f"Quantidade insuficiente de links ({len(urls_alvo)} encontrados). Abortando.")
        return

    print("Raspando metadados das matérias selecionadas...")
    noticias = [extrair_metadados_materia(u) for u in urls_alvo]

    html_email = construir_estrutura_html(noticias)

    lista_inscritos = carregar_inscritos_google_sheets()

    if not lista_inscritos:
        print("Nenhum inscrito encontrado na planilha. Abortando envio.")
        return

    print(f"Iniciando conexao SMTP e envio para {len(lista_inscritos)} leitor(es)...")
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=25) as servidor:
            servidor.ehlo()
            servidor.starttls()
            servidor.ehlo()
            servidor.login(GMAIL_USER, GMAIL_APP_PASSWORD)

            for email_leitor in lista_inscritos:
                mensagem = MIMEMultipart("alternative")
                mensagem["Subject"] = "Giro Jamildo: As 5 matérias mais lidas de hoje"
                mensagem["From"] = f"Jamildo.com <{GMAIL_USER}>"
                mensagem["To"] = email_leitor
                mensagem.attach(MIMEText(html_email, "html"))

                servidor.sendmail(GMAIL_USER, email_leitor, mensagem.as_string())
                print(f"Envio concluido para: {email_leitor}")

        print("Processo finalizado com sucesso.")
    except Exception as e:
        print(f"Erro na entrega de e-mails: {e}")

if __name__ == "__main__":
    processar_envio()
