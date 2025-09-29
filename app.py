# app.py
import io
import re
import base64
from datetime import datetime
from typing import Dict, List, Tuple, Optional

import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib import colors
from reportlab.lib.units import mm

# ============== UTIL ==============
def try_read_bytes(uploaded) -> bytes:
    if uploaded is None:
        return b""
    # Streamlit UploadedFile é um buffer reutilizável: use getvalue()
    try:
        return uploaded.getvalue()
    except Exception:
        return uploaded.read()

def detect_encoding_and_text(b: bytes) -> Tuple[str, str]:
    """
    Tenta detectar encoding (com chardet, se disponível) e devolve (encoding, text).
    Sempre retorna texto (fallback latin-1).
    """
    enc = "latin-1"
    try:
        import chardet  # opcional
        det = chardet.detect(b[:200_000])
        if det and det.get("encoding"):
            enc = det["encoding"]
            # preferir latin-1 quando chardet indicar ascii/low-confidence
            if enc.lower() in ("ascii",) or (det.get("confidence", 0) < 0.6):
                enc = "latin-1"
    except Exception:
        enc = "latin-1"

    try:
        text = b.decode(enc, errors="ignore")
    except Exception:
        enc = "latin-1"
        text = b.decode(enc, errors="ignore")
    return enc, text

def extract_first(line: str, idx: int) -> str:
    parts = [p.strip() for p in line.strip().split("|")]
    return parts[idx] if len(parts) > idx else ""

def parse_header_0000(text: str) -> Dict[str, str]:
    """
    Retorna: {competencia, empresa, cnpj, uf}
    Layout 0000: |0000|COD_VER|COD_FIN|DT_INI|DT_FIN|NOME|CNPJ|UF?… (varia por versão)
    Pegamos NOME(6), CNPJ(7), DT_INI (4) e UF (9) se existir.
    """
    empresa = cnpj = uf = ""
    competencia = ""
    for line in text.splitlines()[:120]:
        if line.startswith("|0000|"):
            parts = [p.strip() for p in line.split("|")]
            # datas
            if len(parts) > 4 and len(parts[4]) == 8 and parts[4].isdigit():
                dt_ini = parts[4]  # DDMMAAAA
                mes = dt_ini[2:4]
                ano = dt_ini[4:8]
                competencia = f"{mes}/{ano}"
            # empresa/cnpj
            if len(parts) > 6:
                empresa = parts[6]
            if len(parts) > 7:
                cnpj = parts[7]
            # uf (nem sempre em 9, mas tentamos)
            if len(parts) > 9:
                uf = parts[9]
            break
    return {
        "competencia": competencia or "Não identificado",
        "empresa": empresa or "",
        "cnpj": cnpj or "",
        "uf": (uf or "").upper(),
    }

def has_movimento(text: str) -> bool:
    """
    Sinaliza 'com movimento' se encontrar quaisquer documentos/itens (C100/C170, D100/D190) ou apurações E110/E200/E300.
    """
    patterns = (r"\|C100\|", r"\|C170\|", r"\|D100\|", r"\|D190\|", r"\|E110\|", r"\|E200\|", r"\|E300\|")
    for p in patterns:
        if re.search(p, text):
            return True
    return False

def summarize_ajustes(text: str) -> Dict[str, Dict[str, float]]:
    """
    Resume C195/C197/E111/E115/E116:
    - retorna dict por registro com contagem e soma de valores (quando houver).
    """
    resumo = {
        "C195": {"qtd": 0, "valor": 0.0},
        "C197": {"qtd": 0, "valor": 0.0},
        "E111": {"qtd": 0, "valor": 0.0},
        "E115": {"qtd": 0, "valor": 0.0},
        "E116": {"qtd": 0, "valor": 0.0},
    }
    def parse_br_to_float(s: str) -> float:
        s = (s or "").strip()
        if not s:
            return 0.0
        # tenta EN
        try:
            return float(s.replace(" ", ""))
        except Exception:
            pass
        # tenta BR
        try:
            return float(s.replace(".", "").replace(",", "."))
        except Exception:
            return 0.0

    for line in text.splitlines():
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        rec = parts[1]
        if rec == "C195":
            resumo["C195"]["qtd"] += 1
        elif rec == "C197":
            resumo["C197"]["qtd"] += 1
            # valores podem estar no final (VL_ICMS ou VL_OUTROS). somamos tudo que for número > 0
            acc = 0.0
            for p in parts[2:]:
                v = parse_br_to_float(p)
                if v > 0:
                    acc += v
            resumo["C197"]["valor"] += acc
        elif rec == "E111":
            resumo["E111"]["qtd"] += 1
            if len(parts) > 4:
                resumo["E111"]["valor"] += parse_br_to_float(parts[4])
        elif rec == "E115":
            resumo["E115"]["qtd"] += 1
            if len(parts) > 3:
                resumo["E115"]["valor"] += parse_br_to_float(parts[3])
        elif rec == "E116":
            resumo["E116"]["qtd"] += 1
            if len(parts) > 3:
                resumo["E116"]["valor"] += parse_br_to_float(parts[3])
    return resumo

DIFAL_KWS = {
    "difal","dif al","d i f a l","dif-aliq","dif.aliq","dif aliquota","diferencial","diferenca de aliquota",
    "uso e consumo","uso/consumo","imobilizado","ativo permanente","ativo imobilizado","fecp","fundo combate pobreza"
}
DIFAL_WHITELIST_CODES = {
    # exemplos comuns por UF (não exaustivo, mas ajuda)
    "SP": {"SP000207","SP40090207","SP10090718"},
    "RJ": {"RJ70000001","RJ70000002","RJ70000003","RJ70000006"},
    "PR": {"PR000081"},
    "SC": {"SC40000002","SC40000003","SC000007"},
    "MS": {"MS70000001"},
    "MG": {"MG70000001","MG70010001"},
    "GO": {"GO40000029","GO020081","GO020082","GO050010","GO050011"},
    "MT": {"MT70000001","MT70000002"},
}

def norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = s.encode("ascii","ignore").decode("utf-8")
    s = s.lower()
    s = re.sub(r"[\s\.\-_/\\]+"," ", s).strip()
    return s

def difal_auditoria(text: str, uf: str) -> Dict[str, any]:
    """
    Analisa entradas CFOP 2551/2556 e tenta cruzar evidências de ajustes (C195/C197/E111/E115/E116).
    Retorna: contagem itens 2551/2556, NFs distintas, lista de códigos detectados, flag tem_evidencia.
    """
    # coleta CFOPs 2551/2556 (C170; índice do CFOP varia, então tentamos vários)
    nfs_255x = set()
    itens_255x = 0
    current_doc = {"serie":"", "numero":"", "chave":""}

    for line in text.splitlines():
        if not line or "|" not in line: 
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2: 
            continue
        rec = parts[1]

        if rec == "C100":
            # |C100|IND_OPER|...|SER|NUM_DOC|CHV_NFE|...
            current_doc = {
                "serie": parts[7] if len(parts) > 7 else "",
                "numero": parts[8] if len(parts) > 8 else "",
                "chave":  parts[9] if len(parts) > 9 else "",
            }
        elif rec == "C170":
            # tenta achar CFOP
            cfop = ""
            for idx in (11,10,12,13,9):
                if len(parts) > idx and parts[idx]:
                    cand = parts[idx]
                    if re.fullmatch(r"\d{4}|\d\.\d{3}", cand):
                        cfop = cand.replace(".","")
                        break
            if cfop in ("2551","2556"):
                itens_255x += 1
                nf_id = f"{current_doc.get('serie','')}|{current_doc.get('numero','')}"
                if nf_id.strip("|"):
                    nfs_255x.add(nf_id)

    # extrai códigos/descrições nos ajustes
    codigos = set()
    descrs = []
    def add_codigo(c: str):
        c = (c or "").strip().upper()
        if c:
            codigos.add(c)

    for line in text.splitlines():
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        rec = parts[1]
        if rec == "C197":
            add_codigo(parts[2] if len(parts) > 2 else "")
            if len(parts) > 3:
                descrs.append(parts[3])
        elif rec == "E111":
            add_codigo(parts[2] if len(parts) > 2 else "")
            if len(parts) > 3:
                descrs.append(parts[3])
        elif rec == "E115":
            add_codigo(parts[2] if len(parts) > 2 else "")
            if len(parts) > 4:
                descrs.append(parts[4])
        elif rec == "E116":
            # cod_orfica/cod_rec/descr complementares
            if len(parts) > 2: add_codigo(parts[2])
            if len(parts) > 5: add_codigo(parts[5])
            if len(parts) > 9: descrs.append(parts[9])

    # heurística de evidência: whitelist por UF OU palavras-chave na descrição
    uf = (uf or "").upper()
    wl = DIFAL_WHITELIST_CODES.get(uf, set())
    in_whitelist = any((c in wl) for c in codigos)
    has_kw = any(any(kw in norm(d) for kw in DIFAL_KWS) for d in descrs if d)

    tem_evidencia = in_whitelist or has_kw
    return {
        "itens_255x": itens_255x,
        "nfs_distintas": len(nfs_255x),
        "codigos_detectados": sorted(list(codigos)),
        "tem_evidencia": tem_evidencia
    }

def detect_assinatura(bytes_data: bytes, text: str) -> bool:
    """
    Detecta presença de assinatura digital embutida no arquivo SPED (rodapé binário com cadeia ICP-Brasil).
    Estratégias:
    - presença de muitos bytes não-ASCII no rodapé
    - presença de strings 'ICP-Brasil', 'AC ' conhecidas
    - comprimento após |9999| significativo com binário
    """
    # 1) Procura palavras-chave no texto (muitos arquivos “assinado” carregam strings legíveis)
    if re.search(r"ICP[- ]?Brasil", text, flags=re.IGNORECASE) or \
       re.search(r"AC\s+SOLUTI", text, flags=re.IGNORECASE) or \
       re.search(r"Certificado", text, flags=re.IGNORECASE):
        return True

    # 2) Checa “cauda binária” após |9999|
    tail = b""
    try:
        # pega últimos 50kB
        tail = bytes_data[-50_000:]
    except Exception:
        tail = bytes_data

    # proporção de bytes não-texto
    non_text = sum(1 for bt in tail if bt < 9 or bt == 11 or bt == 12 or bt > 126)
    ratio = non_text / max(1, len(tail))
    if ratio > 0.20 and len(tail) > 2000:  # 20% de bytes não-ASCII em cauda razoável
        return True

    return False

def load_logo_bytes(logo_upload, fallback_path: str = "Image_smart01.png") -> Optional[bytes]:
    # prioriza upload via sidebar
    if logo_upload is not None:
        b = try_read_bytes(logo_upload)
        if b:
            return b
    # tenta fallback do repo
    try:
        with open(fallback_path, "rb") as f:
            return f.read()
    except Exception:
        return None

# ============== UI ==============
st.set_page_config(page_title="Smart01 • Auditoria SPED", page_icon="🧾", layout="wide")

st.sidebar.header("⚙️ Opções")
logo_up = st.sidebar.file_uploader("Logo (PNG) opcional", type=["png"])
logo_bytes = load_logo_bytes(logo_up, fallback_path="Image_smart01.png")

uploaded = st.file_uploader("Envie o arquivo SPED (.txt) assinado ou não", type=["txt"])
if uploaded is None:
    st.info("Envie um arquivo SPED para iniciar a análise.")
    st.stop()

file_bytes = try_read_bytes(uploaded)
enc, text = detect_encoding_and_text(file_bytes)

# Cabeçalho visual com logo
cols = st.columns([1, 3])
with cols[0]:
    if logo_bytes:
        st.image(logo_bytes, caption="Smart01", use_container_width=True)
with cols[1]:
    st.title("Auditoria SPED • Smart01")
    st.caption("Resumo rápido de movimento, ajustes e DIFAL (entradas 2551/2556)")

# Extrações
hdr = parse_header_0000(text)
diagnostico = "Com movimento" if has_movimento(text) else "Sem movimento"
assinatura = "Assinado digitalmente (ICP-Brasil detectado)" if detect_assinatura(file_bytes, text) else "Arquivo sem assinatura"

# Resumos
res_aj = summarize_ajustes(text)
difal = difal_auditoria(text, uf=hdr["uf"])

# Painéis
st.subheader("📄 Identificação do Arquivo")
st.write(
    f"**Nome do arquivo:** {uploaded.name}  \n"
    f"**Competência:** {hdr['competencia']}  \n"
    f"**Empresa:** {hdr['empresa']}  \n"
    f"**CNPJ:** {hdr['cnpj']}  \n"
    f"**UF:** {hdr['uf']}  \n"
    f"**Diagnóstico:** {diagnostico}  \n"
    f"**Assinatura:** {assinatura}"
)

st.subheader("🧾 Resumo de Ajustes")
aj_cols = st.columns(5)
for i, reg in enumerate(["C195","C197","E111","E115","E116"]):
    with aj_cols[i]:
        st.metric(reg, f"Qtd: {res_aj[reg]['qtd']}", help=f"Soma (numérica) detectada: {res_aj[reg]['valor']:.2f}")

st.subheader("🏷️ Auditoria DIFAL – Entradas 2551/2556")
st.write(
    f"- **Itens 2551/2556 (C170):** {difal['itens_255x']}  \n"
    f"- **Notas distintas com 2551/2556:** {difal['nfs_distintas']}  \n"
    f"- **Códigos de ajuste detectados:** {', '.join(difal['codigos_detectados']) if difal['codigos_detectados'] else '—'}  \n"
    f"- **Evidência de DIFAL (whitelist/descrição):** {'Sim' if difal['tem_evidencia'] else 'Não'}"
)

# ============== PDF ==============
def generate_pdf(logo_png: Optional[bytes],
                 file_name: str,
                 hdr: Dict[str,str],
                 diagnostico: str,
                 assinatura: str,
                 res_aj: Dict[str, Dict[str, float]],
                 difal: Dict[str, any]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    margin = 18 * mm
    y = height - margin

    # Cabeçalho com logo
    if logo_png:
        try:
            img = ImageReader(io.BytesIO(logo_png))
            # altura fixa ~18mm, mantém proporção
            img_h = 18 * mm
            iw, ih = img.getSize()
            img_w = iw * (img_h / ih)
            c.drawImage(img, margin, y - img_h, width=img_w, height=img_h, preserveAspectRatio=True, mask='auto')
        except Exception:
            pass
    # Título
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin + 70*mm, y - 6*mm, "Auditoria SPED • Smart01")

    y -= 24 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, "Identificação do Arquivo")
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    c.line(margin, y - 2, width - margin, y - 2)

    y -= 7 * mm
    c.setFont("Helvetica", 10)
    lines = [
        f"Nome do arquivo: {file_name}",
        f"Competência: {hdr.get('competencia','')}",
        f"Empresa: {hdr.get('empresa','')}",
        f"CNPJ: {hdr.get('cnpj','')}",
        f"UF: {hdr.get('uf','')}",
        f"Diagnóstico: {diagnostico}",
        f"Assinatura: {assinatura}",
    ]
    for ln in lines:
        c.drawString(margin, y, ln)
        y -= 6 * mm

    # Resumo de ajustes
    y -= 2 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, "Resumo dos Ajustes (C195 / C197 / E111 / E115 / E116)")
    c.line(margin, y - 2, width - margin, y - 2)
    y -= 8 * mm

    c.setFont("Helvetica", 10)
    for reg in ["C195","C197","E111","E115","E116"]:
        txt = f"{reg}: Qtd = {res_aj[reg]['qtd']} • Soma = {res_aj[reg]['valor']:.2f}"
        c.drawString(margin, y, txt)
        y -= 6 * mm

    # DIFAL
    y -= 4 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, "Auditoria DIFAL – Entradas 2551/2556")
    c.line(margin, y - 2, width - margin, y - 2)
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    dif_lines = [
        f"Itens 2551/2556 (C170): {difal['itens_255x']}",
        f"Notas distintas com 2551/2556: {difal['nfs_distintas']}",
        f"Códigos de ajuste detectados: {', '.join(difal['codigos_detectados']) if difal['codigos_detectados'] else '—'}",
        f"Evidência de DIFAL (whitelist/descrição): {'Sim' if difal['tem_evidencia'] else 'Não'}",
    ]
    for ln in dif_lines:
        c.drawString(margin, y, ln)
        y -= 6 * mm

    # Rodapé
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(colors.gray)
    c.drawRightString(width - margin, 12 * mm, f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()

st.divider()
st.subheader("📤 Exportar")

pdf_bytes = generate_pdf(
    logo_png=logo_bytes,
    file_name=uploaded.name,
    hdr=hdr,
    diagnostico=diagnostico,
    assinatura=assinatura,
    res_aj=res_aj,
    difal=difal
)

st.download_button(
    "Gerar PDF",
    data=pdf_bytes,
    file_name=f"Auditoria_SPED_{hdr['cnpj'] or 'semCNPJ'}_{hdr['competencia'].replace('/','-')}.pdf",
    mime="application/pdf",
    type="primary"
)

st.caption("Obs.: se a logo não aparecer no PDF, verifique o upload da imagem ou mantenha o arquivo `Image_smart01.png` no repositório.")
