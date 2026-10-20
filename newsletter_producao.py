import os
import smtplib
from datetime import datetime, timezone, timedelta
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

DIAS_SEMANA = {
    0: "segunda-feira",
    1: "terça-feira",
    2: "quarta-feira",
    3: "quinta-feira",
    4: "sexta-feira",
    5: "sábado",
    6: "domingo",
}

def obter_titulo_newsletter():
    """Gera o assunto dinâmico com dia da semana e data no horário de Brasília."""
    fuso_brasilia = timezone(timedelta(hours=-3))
    agora = datetime.now(fuso_brasilia)
    nome_dia = DIAS_SEMANA[agora.weekday()]
    data_formatada = agora.strftime("%d/%m")
    return f"Giro Jamildo: Destaques desta {nome_dia} ({data_formatada})"

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

        print(f"Base de leitores: {len(inscritos_unicos)} inscrito(s) ativo(s).")
        return inscritos_unicos
    except Exception as e:
        print(f"Erro ao ler inscritos no Google Sheets: {e}")
        return [EMAIL_REMETENTE]

def extrair_metadados_materia(url, limite_dias=1):
    """
    Acessa a matéria e verifica se foi publicada no prazo permitido.
    limite_dias=1 garante que só entra matéria de hoje ou ontem (máx 24-36h).
    """
    registro = {
        "url": url,
        "titulo": "",
        "linha_fina": "",
        "imagem": "",
        "data": None,
        "valida": False
    }

    try:
        resp = requests.get(url, headers=HEADERS_REQUISICAO, timeout=12)
        if resp.status_code != 200:
            return registro

        sopa = BeautifulSoup(resp.text, "html.parser")

        # Título
        tag_og_title = sopa.find("meta", property="og:title")
        if tag_og_title and tag_og_title.get("content"):
            titulo_bruto = tag_og_title["content"]
        else:
            tag_h1 = sopa.find("h1")
            titulo_bruto = tag_h1.text.strip() if tag_h1 else ""

        titulo_limpo = (
            titulo_bruto.replace("Jamildo · ", "")
            .replace(" - Jamildo", "")
            .replace(" | Jamildo.com", "")
            .strip()
        )

        if not titulo_limpo or len(titulo_limpo) < 15:
            return registro
        if titulo_limpo.lower().startswith("busca:") or "pesquisa" in titulo_limpo.lower():
            return registro

        registro["titulo"] = titulo_limpo

        # Data de publicação
        tag_data = (
            sopa.find("meta", property="article:published_time")
            or sopa.find("meta", attrs={"name": "pubdate"})
            or sopa.find("meta", property="og:published_time")
        )

        fuso_brasilia = timezone(timedelta(hours=-3))
        hoje = datetime.now(fuso_brasilia).date()

        if tag_data and tag_data.get("content"):
            texto_data = tag_data["content"][:10]
            try:
                data_materia = datetime.strptime(texto_data, "%Y-%m-%d").date()
                registro["data"] = data_materia
                diferenca = (hoje - data_materia).days
                if diferenca > limite_dias:
                    return registro
            except Exception:
                pass

        # Linha fina
        tag_og_desc = sopa.find("meta", property="og:description") or sopa.find("meta", attrs={"name": "description"})
        if tag_og_desc and tag_og_desc.get("content") and not tag_og_desc["content"].startswith("Listagem de"):
            registro["linha_fina"] = tag_og_desc["content"].strip()
        else:
            seletor_sub = sopa.select_one(".col-content p.lead, .subtitulo, .linha-fina")
            if seletor_sub:
                registro["linha_fina"] = seletor_sub.text.strip()

        # Imagem
        tag_og_img = sopa.find("meta", property="og:image")
        if tag_og_img and tag_og_img.get("content"):
            registro["imagem"] = tag_og_img["content"].strip()
        else:
            seletor_img = sopa.select_one(".col-content img, article img")
            if seletor_img and seletor_img.get("src"):
                registro["imagem"] = seletor_img["src"].strip()

        if registro["imagem"] and not registro["imagem"].startswith("http"):
            registro["imagem"] = f"https://jamildo.com{registro['imagem']}"

        registro["valida"] = True

    except Exception as erro:
        print(f"Erro em {url}: {erro}")

    return registro

def obter_ultimas_noticias_portal():
    """Busca as notícias recém-publicadas diretamente na capa e no canal de últimas."""
    urls = []
    fontes = [
        "https://jamildo.com/canal/ultimas-noticias",
        "https://jamildo.com/"
    ]

    for fonte in fontes:
        try:
            resp = requests.get(fonte, headers=HEADERS_REQUISICAO, timeout=12)
            if resp.status_code == 200:
                sopa = BeautifulSoup(resp.text, "html.parser")
                for link in sopa.find_all("a", href=True):
                    href = link["href"].strip()
                    if href.endswith(".html") and not any(p in href for p in ["/canal/", "/categoria/", "/tags/", "/tag/"]):
                        url_comp = href if href.startswith("http") else f"https://jamildo.com{href}"
                        if url_comp not in urls:
                            urls.append(url_comp)
        except Exception as e:
            print(f"Erro ao ler {fonte}: {e}")

    return urls

def consultar_mais_lidas_ga4():
    """Consulta os acessos de hoje no GA4."""
    try:
        cliente = BetaAnalyticsDataClient()
        solicitacao = RunReportRequest(
            property=f"properties/{GA4_PROPERTY_ID}",
            dimensions=[Dimension(name="pagePath")],
            metrics=[Metric(name="screenPageViews")],
            date_ranges=[DateRange(start_date="today", end_date="today")],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"), desc=True)],
            limit=50,
        )
        resposta = cliente.run_report(solicitacao)
    except Exception as e:
        print(f"Aviso no GA4: {e}. Passando para o feed.")
        return []

    candidatas = []
    bloqueios = ["/busca", "/search", "/tags/", "/tag/", "/categoria/", "/canal/", "gsc.tab", "?q="]

    for linha in resposta.rows:
        caminho = linha.dimension_values[0].value.strip()
        caminho_base = caminho.split("?")[0].split("#")[0].rstrip("/")

        if len(caminho_base) <= 3 or any(b in caminho.lower() for b in bloqueios):
            continue

        caminho_limpo = caminho_base.replace("/amp/", "/")
        url = f"https://jamildo.com{caminho_limpo}"
        if url not in candidatas:
            candidatas.append(url)

    return candidatas

def construir_estrutura_html(noticias, origem_texto):
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
                    <span style="font-size: 11px; font-weight: bold; color: #f83d03; text-transform: uppercase;">Top {idx} &bull; {item['origem_label']}</span>
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
                {origem_texto.upper()}
            </td>
        </tr>
        <tr>
            <td style="padding: 24px;">
                <div style="border: 2px solid #024796; border-radius: 6px; padding: 16px; margin-bottom: 26px; background-color: #ffffff;">
                    {foto_capa}
                    <span style="background-color: #f83d03; color: #ffffff; padding: 3px 8px; font-size: 11px; font-weight: bold; border-radius: 3px; text-transform: uppercase;">
                        #1 Destaque &bull; {destaque['origem_label']}
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

    noticias_finais = []
    urls_usadas = set()

    if modo == "manual" and links_informados:
        print("Modo Manual: processando links fornecidos via disparador.")
        urls_alvo = [u.strip() for u in links_informados.split(",") if u.strip()][:5]
        for url in urls_alvo:
            materia = extrair_metadados_materia(url, limite_dias=30)
            if materia["titulo"]:
                materia["origem_label"] = "Seleção Manual"
                noticias_finais.append(materia)
                urls_usadas.add(materia["url"])
        faixa_email = "Giro Diário · Seleção da Redação"
    else:
        print("Modo Automático: buscando do GA4 com trava de frescor...")
        candidatas_ga4 = consultar_mais_lidas_ga4()

        for url in candidatas_ga4:
            materia = extrair_metadados_materia(url, limite_dias=1)
            if materia["valida"] and url not in urls_usadas:
                materia["origem_label"] = "Mais Lida"
                noticias_finais.append(materia)
                urls_usadas.add(url)
                print(f"GA4 -> {materia['titulo']}")
            if len(noticias_finais) == 5:
                break

        total_ga4 = len(noticias_finais)

        if len(noticias_finais) < 5:
            faltam = 5 - len(noticias_finais)
            print(f"GA4 forneceu {total_ga4} matérias de hoje. Completando {faltam} do Feed do portal...")
            urls_feed = obter_ultimas_noticias_portal()

            for url in urls_feed:
                if url in urls_usadas:
                    continue
                materia = extrair_metadados_materia(url, limite_dias=1)
                if materia["valida"]:
                    materia["origem_label"] = "Últimas Notícias"
                    noticias_finais.append(materia)
                    urls_usadas.add(url)
                    print(f"Feed -> {materia['titulo']}")
                if len(noticias_finais) == 5:
                    break

        if total_ga4 >= 5:
            faixa_email = "Giro Diário · As 5 Notícias Mais Lidas"
        elif total_ga4 > 0:
            faixa_email = "Giro Diário · Mais Lidas e Destaques de Hoje"
        else:
            faixa_email = "Giro Diário · As Principais Notícias de Hoje"

    if len(noticias_finais) < 5:
        print(f"Quantidade insuficiente ({len(noticias_finais)} notícias válidas). Abortando.")
        return

    html_email = construir_estrutura_html(noticias_finais, faixa_email)
    assunto_email = obter_titulo_newsletter()

    lista_inscritos = carregar_inscritos_google_sheets()

    if not lista_inscritos:
        print("Nenhum inscrito na planilha. Abortando.")
        return

    total_inscritos = len(lista_inscritos)
    print(f"Disparando newsletter para {total_inscritos} assinante(s)...")
    try:
        with smtplib.SMTP("smtp-relay.brevo.com", 587, timeout=25) as servidor:
            servidor.ehlo()
            servidor.starttls()
            servidor.ehlo()
            servidor.login(BREVO_SMTP_LOGIN, BREVO_SMTP_KEY)

            for idx, email_leitor in enumerate(lista_inscritos, start=1):
                mensagem = MIMEMultipart("alternative")
                mensagem["Subject"] = assunto_email
                mensagem["From"] = f"Jamildo.com <{EMAIL_REMETENTE}>"
                mensagem["To"] = email_leitor
                mensagem.attach(MIMEText(html_email, "html"))

                servidor.sendmail(EMAIL_REMETENTE, email_leitor, mensagem.as_string())
                print(f"[{idx}/{total_inscritos}] Mensagem enviada com sucesso.")

        print("Ciclo finalizado com sucesso via Brevo.")
    except Exception as e:
        print(f"Erro no envio SMTP: {e}")

if __name__ == "__main__":
    processar_envio()
