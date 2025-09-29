# app_sped_auditor_streamlit.py
# Auditor SPED – Smart01 (ICMS/IPI & PIS/COFINS – Básico)
# Funciona 100% em nuvem (GitHub + Streamlit Cloud)

import streamlit as st
import pandas as pd
import io
import re
import unicodedata
from collections import defaultdict

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib import colors

st.set_page_config(page_title="Auditor SPED - Smart01", layout="wide", initial_sidebar_state="expanded")

# =========================
# Estilo
# =========================
st.markdown("""
<style>
    .stApp { background-color: #0F1115; color: #EDEFF2; }
    p, .stMarkdown, .css-10trblm, .css-1offfwp { color: #EDEFF2 !important; }
    h1, h2, h3, h4 { color: #F5C542; }
    .block-container { padding-top: 1.5rem; }
</style>
""", unsafe_allow_html=True)

# =========================
# Utilidades de leitura
# =========================
def detectar_encoding_bytes(raw: bytes) -> str:
    """Detecta encoding de forma robusta. Cai para latin-1 se incerto/ausente."""
    try:
        import chardet
        det = chardet.detect(raw[:200000])  # ~200KB
        enc = det.get("encoding") or "latin-1"
        conf = det.get("confidence", 0.0) or 0.0
        if conf < 0.7 or enc.lower() in ("ascii",):
            return "latin-1"
        return enc
    except Exception:
        return "latin-1"

def load_text_from_upload(uploaded_file) -> str:
    """Lê o arquivo do uploader e devolve string do conteúdo."""
    raw = uploaded_file.read()
    enc = detectar_encoding_bytes(raw)
    try:
        return raw.decode(enc, errors="ignore")
    except Exception:
        return raw.decode("latin-1", errors="ignore")

def stream_text_to_lines(text: str):
    for line in text.splitlines():
        yield line

# =========================
# Helpers gerais
# =========================
def parse_float_br(x: str) -> float:
    if x is None:
        return 0.0
    x = str(x).strip()
    if x == "":
        return 0.0
    try:
        return float(x.replace(" ", ""))
    except:
        try:
            return float(x.replace(".", "").replace(",", "."))
        except:
            return 0.0

def norm_txt(s: str) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("utf-8")
    s = s.lower()
    return re.sub(r"[\s\.\-_/\\]+", " ", s).strip()

def extrair_competencia_0000(lines_iterable):
    """Retorna (MM/AAAA, Razão, CNPJ, UF) a partir do |0000| nas primeiras linhas."""
    comp, razao, cnpj, uf = "", "", "", ""
    for i, line in enumerate(lines_iterable):
        if i > 80:
            break
        parts = [p.strip() for p in line.strip().split("|")]
        if len(parts) > 1 and parts[1] == "0000":
            try:
                dt_ini = parts[4] if len(parts) > 4 else ""
                if dt_ini and len(dt_ini) == 8:
                    mes = dt_ini[2:4]; ano = dt_ini[4:8]
                    comp = f"{mes}/{ano}"
                razao = (parts[6] if len(parts) > 6 else "").strip()
                cnpj  = (parts[7] if len(parts) > 7 else "").strip()
                uf    = (parts[9] if len(parts) > 9 else "").strip().upper()
            except:
                pass
            break
    return comp, razao, cnpj, uf

# =========================
# 1) SPED "em branco"
# =========================
def check_sped_blank(text: str):
    has_c100 = False
    has_c190_val = False
    has_blocks = {"E200": False, "E300": False, "E500": False, "G110": False}
    for line in stream_text_to_lines(text):
        if "|C100|" in line:
            has_c100 = True
        if line.startswith("|C190|"):
            parts = line.split("|")
            if len(parts) > 7:
                bc = parse_float_br(parts[6]); ic = parse_float_br(parts[7])
                if (bc > 0) or (ic > 0):
                    has_c190_val = True
        if line.startswith("|E2"):
            has_blocks["E200"] = True
        if line.startswith("|E3"):
            has_blocks["E300"] = True
        if line.startswith("|E500|"):
            has_blocks["E500"] = True
        if line.startswith("|G110|"):
            has_blocks["G110"] = True

    if not has_c100:
        return "Provável sem documentos (sem C100)"
    if not has_c190_val and not any(has_blocks.values()):
        return "Provável zerado (sem BC/ICMS em C190 e sem E200/E300/E500/G110)"
    return "Com movimento"

# =========================
# 2) Resumo CFOP – ICMS / IPI
# =========================
def resumo_cfop_icms(text: str):
    rows = []
    for line in stream_text_to_lines(text):
        if line.startswith("|C190|"):
            parts = [p.strip() for p in line.split("|")]
            try:
                cst  = parts[2] if len(parts) > 2 else ""
                cfop = parts[3] if len(parts) > 3 else ""
                bc   = parse_float_br(parts[6] if len(parts) > 6 else "0")
                ic   = parse_float_br(parts[7] if len(parts) > 7 else "0")
                rows.append({"CFOP": cfop, "CST": cst, "BC_ICMS": bc, "ICMS": ic})
            except:
                pass
    if not rows:
        return pd.DataFrame(columns=["CFOP", "CST", "BC_ICMS", "ICMS"])
    df = pd.DataFrame(rows).groupby(["CFOP","CST"], as_index=False).agg({"BC_ICMS":"sum","ICMS":"sum"})
    return df

def resumo_cfop_ipi(text: str):
    rows = []
    for line in stream_text_to_lines(text):
        if line.startswith("|E510|"):
            parts = [p.strip() for p in line.split("|")]
            try:
                cfop = parts[2] if len(parts) > 2 else ""
                cst  = parts[3] if len(parts) > 3 else ""
                vcont = parse_float_br(parts[4] if len(parts) > 4 else "0")
                bc    = parse_float_br(parts[5] if len(parts) > 5 else "0")
                ipi   = parse_float_br(parts[6] if len(parts) > 6 else "0")
                rows.append({"CFOP": cfop, "CST": cst, "VL_CONTABIL": vcont, "BC_IPI": bc, "IPI": ipi})
            except:
                pass
    if not rows:
        return pd.DataFrame(columns=["CFOP","CST","VL_CONTABIL","BC_IPI","IPI"])
    df = pd.DataFrame(rows).groupby(["CFOP","CST"], as_index=False).agg({"VL_CONTABIL":"sum","BC_IPI":"sum","IPI":"sum"})
    return df

# =========================
# 3) Ajustes
# =========================
def coletar_ajustes(text: str):
    c195, c197, e111, e115, e116 = [], [], [], [], []
    current_doc = {"serie":"","numero":"","chave":""}

    for line in stream_text_to_lines(text):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        rec = parts[1]

        if rec == "C100":
            current_doc = {
                "serie": parts[7] if len(parts) > 7 else "",
                "numero": parts[8] if len(parts) > 8 else "",
                "chave":  parts[9] if len(parts) > 9 else ""
            }

        elif rec == "C195":
            cod_obs = parts[2] if len(parts) > 2 else ""
            txt     = parts[3] if len(parts) > 3 else ""
            c195.append({"Série": current_doc["serie"], "Número": current_doc["numero"], "Chave": current_doc["chave"],
                         "COD_OBS": cod_obs, "TXT_COMPL": txt})

        elif rec == "C197":
            cod = parts[2] if len(parts) > 2 else ""
            descr = parts[3] if len(parts) > 3 else ""
            vals = []
            for p in parts[2:]:
                v = parse_float_br(p)
                if v > 0: vals.append(v)
            vl = vals[-1] if vals else 0.0
            c197.append({"Série": current_doc["serie"], "Número": current_doc["numero"], "Chave": current_doc["chave"],
                        "COD_AJ": cod, "DESCR": descr, "VALOR": vl})

        elif rec == "E111":
            cod = parts[2] if len(parts) > 2 else ""
            descr = parts[3] if len(parts) > 3 else ""
            vl = parse_float_br(parts[4] if len(parts) > 4 else "0")
            e111.append({"COD_AJ_APUR": cod, "DESCR": descr, "VALOR": vl})

        elif rec == "E115":
            cod = parts[2] if len(parts) > 2 else ""
            vl = parse_float_br(parts[3] if len(parts) > 3 else "0")
            descr = parts[4] if len(parts) > 4 else ""
            e115.append({"COD_INF_ADIC": cod, "VALOR": vl, "DESCR": descr})

        elif rec == "E116":
            cod_or = parts[2] if len(parts) > 2 else ""
            vl = parse_float_br(parts[3] if len(parts) > 3 else "0")
            dt_v = parts[4] if len(parts) > 4 else ""
            cod_rec = parts[5] if len(parts) > 5 else ""
            num_proc = parts[6] if len(parts) > 6 else ""
            ind_proc = parts[7] if len(parts) > 7 else ""
            proc = parts[8] if len(parts) > 8 else ""
            txt = parts[9] if len(parts) > 9 else ""
            e116.append({"COD_OR": cod_or, "VALOR": vl, "DT_VCTO": dt_v, "COD_REC": cod_rec,
                         "NUM_PROC": num_proc, "IND_PROC": ind_proc, "PROC": proc, "TXT_COMPL": txt})

    df_c195 = pd.DataFrame(c195) if c195 else pd.DataFrame(columns=["Série","Número","Chave","COD_OBS","TXT_COMPL"])
    df_c197 = pd.DataFrame(c197) if c197 else pd.DataFrame(columns=["Série","Número","Chave","COD_AJ","DESCR","VALOR"])
    df_e111 = pd.DataFrame(e111) if e111 else pd.DataFrame(columns=["COD_AJ_APUR","DESCR","VALOR"])
    df_e115 = pd.DataFrame(e115) if e115 else pd.DataFrame(columns=["COD_INF_ADIC","VALOR","DESCR"])
    df_e116 = pd.DataFrame(e116) if e116 else pd.DataFrame(columns=["COD_OR","VALOR","DT_VCTO","COD_REC","NUM_PROC","IND_PROC","PROC","TXT_COMPL"])
    return df_c195, df_c197, df_e111, df_e115, df_e116

# =========================
# 4) Auditoria DIFAL 2551/2556
# =========================
CFOPS_ALVO = {"2551", "2556", "2.551", "2.556"}
EXCLUDE_CODES = {"SP000299"}
KEYWORDS_DIFAL = {
    "difal","dif al","d i f a l","dif-aliq",
    "dif.al","dif.aliq","dif. aliq","dif aliq","dif aliquota","dif.aliquota",
    "dif de aliq","dif de aliquota","dif. de aliquota",
    "diferencial","diferenca de aliquota","diferenca aliquota","diferenc. aliquota",
    "uso e consumo","uso/consumo","uso consumo","material de uso","uso-consumo",
    "imobilizado","ativo permanente","ativo imobilizado",
    "fecp","f e c p","fundo combate pobreza","fundo estadual de combate a pobreza",
}
DIFAL_CODES_UF = {
    "PR": {("ANY", "PR000081")},
    "SP": {("E111", "SP000207"), ("C197", "SP40090207"), ("C197", "SP10090718")},
    "SC": {("C197", "SC40000002"), ("C197", "SC40000003"), ("ANY", "SC000007")},
    "MS": {("C197", "MS70000001")},
    "MG": {("C197", "MG70000001"), ("C197", "MG70010001")},
    "GO": {("C197", "GO40000029"), ("C197", "GO020081"), ("C197", "GO020082"),
           ("C197", "GO050010"), ("C197", "GO050011")},
    "MT": {("C197", "MT70000001"), ("C197", "MT70000002")},
    "RJ": {("C197", "RJ70000001"), ("C197", "RJ70000002"), ("C197", "RJ70000003"),
           ("C197", "RJ70000006")},
}

def eh_cfop_alvo(cfop: str) -> bool:
    if not cfop:
        return False
    cf = cfop.strip()
    return (cf in CFOPS_ALVO) or (cf.replace(".", "") in {"2551","2556"})

def eh_codigo_difal_por_whitelist(uf: str, registro: str, cod: str) -> bool:
    cod = (cod or "").strip().upper()
    if not cod or cod in EXCLUDE_CODES:
        return False
    keyset = DIFAL_CODES_UF.get((uf or "").upper(), set())
    return ("ANY", cod) in keyset or (registro.upper(), cod) in keyset

def eh_desc_difal(descr: str) -> bool:
    s = norm_txt(descr)
    if not s:
        return False
    return any(kw in s for kw in KEYWORDS_DIFAL)

def eh_ajuste_difal(uf: str, registro: str, cod: str, descr: str) -> bool:
    if (cod or "").strip().upper() in EXCLUDE_CODES:
        return False
    if eh_codigo_difal_por_whitelist(uf, registro, cod):
        return True
    return eh_desc_difal(descr)

def auditoria_difal_text(text: str):
    comp, razao, cnpj, uf = extrair_competencia_0000(text.splitlines())

    docs = []
    current_doc = None
    obs_por_doc = defaultdict(list)
    ajustes_c197_all = []
    ajustes_e111_all, ajustes_e115_all, ajustes_e116_all = [], [], []

    def doc_id(d): return f"{d.get('serie','')}|{d.get('numero','')}|{d.get('chave','')}"

    for raw in stream_text_to_lines(text):
        line = raw.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        rec = parts[1] if len(parts) > 1 else ""

        if rec == "C100":
            if current_doc: docs.append(current_doc)
            current_doc = {"serie": parts[7] if len(parts) > 7 else "",
                           "numero": parts[8] if len(parts) > 8 else "",
                           "chave":  parts[9] if len(parts) > 9 else "",
                           "itens": []}

        elif rec == "C170" and current_doc is not None:
            desc = parts[4] if len(parts) > 4 else ""
            cfop = ""
            for idx in (11,10,12,13,9):
                if len(parts) > idx and parts[idx].strip():
                    cand = parts[idx].strip()
                    if re.fullmatch(r"\d{4}|\d\.\d{3}", cand):
                        cfop = cand; break
            vl_item = 0.0
            for idx in (7,20,12,24,25,5,6):
                if len(parts) > idx and parts[idx].strip():
                    v = parse_float_br(parts[idx])
                    if v > 0: vl_item = v; break
            current_doc["itens"].append({"desc": desc, "cfop": cfop, "vl_item": vl_item})

        elif rec == "C195":
            txt = parts[3] if len(parts) > 3 else ""
            if current_doc and txt:
                obs_por_doc[doc_id(current_doc)].append(txt)

        elif rec == "C197":
            cod = (parts[2] if len(parts) > 2 else "").strip().upper()
            descr = (parts[3] if len(parts) > 3 else "").strip()
            nums = []
            for p in parts[2:]:
                v = parse_float_br(p)
                if v > 0: nums.append(v)
            vl = nums[-1] if nums else 0.0
            did = doc_id(current_doc) if current_doc else ""
            ajustes_c197_all.append((cod, descr, vl, did))

        elif rec == "E111":
            cod = (parts[2] if len(parts) > 2 else "").strip().upper()
            descr = (parts[3] if len(parts) > 3 else "").strip()
            vl = parse_float_br(parts[4] if len(parts) > 4 else "0")
            ajustes_e111_all.append((cod, descr, vl))

        elif rec == "E115":
            cod = (parts[2] if len(parts) > 2 else "").strip().upper()
            vl = parse_float_br(parts[3] if len(parts) > 3 else "0")
            descr = (parts[4] if len(parts) > 4 else "").strip()
            ajustes_e115_all.append((cod, descr, vl))

        elif rec == "E116":
            cod_or = (parts[2] if len(parts) > 2 else "").strip().upper()
            vl = parse_float_br(parts[3] if len(parts) > 3 else "0")
            cod_rec = (parts[5] if len(parts) > 5 else "").strip().upper()
            txt = (parts[9] if len(parts) > 9 else "").strip()
            descr = f"{cod_or} {cod_rec} {txt}".strip()
            ajustes_e116_all.append((cod_or or cod_rec, descr, vl, cod_rec))

    if current_doc: docs.append(current_doc)
    for d in docs:
        d["tem_cfop_alvo"] = any(eh_cfop_alvo(it.get("cfop","")) for it in d.get("itens", []))

    ajustes_e111_validos = [(c,d,v) for (c,d,v) in ajustes_e111_all if eh_ajuste_difal(uf, "E111", c, d)]
    ajustes_e115_validos = [(c,d,v) for (c,d,v) in ajustes_e115_all if eh_ajuste_difal(uf, "E115", c, d)]

    ajustes_e116_validos = []
    for (c, d, v, cod_rec) in ajustes_e116_all:
        if eh_ajuste_difal(uf, "E116", cod_rec or c, d):
            ajustes_e116_validos.append((cod_rec or c, d, v))

    ajustes_c197_validos = [(c,d,v,did) for (c,d,v,did) in ajustes_c197_all if eh_ajuste_difal(uf, "C197", c, d)]

    ajustes_doc_validos = defaultdict(list)
    for (cod, descr, vl, did) in ajustes_c197_validos:
        if did: ajustes_doc_validos[did].append(("C197", cod, descr, vl))
    # C195 textual
    for did, oblist in obs_por_doc.items():
        if any(eh_desc_difal(txt) for txt in oblist):
            ajustes_doc_validos[did].append(("C195", "", "Obs C195 com menção a DIFAL/FECP", 0.0))

    docs_alvo = [d for d in docs if d.get("tem_cfop_alvo")]
    numeros_nfs_alvo = sorted({d.get("numero","") for d in docs_alvo if d.get("numero","")})
    analise_rows, sem_rows, check_rows, resumo_rows = [], [], [], []

    evid_periodo = []
    for cod, descr, vl in ajustes_e111_validos: evid_periodo.append(("E111",cod,descr,vl))
    for cod, descr, vl in ajustes_e115_validos: evid_periodo.append(("E115",cod,descr,vl))
    for cod, descr, vl in ajustes_e116_validos: evid_periodo.append(("E116",cod,descr,vl))

    codigos_c197 = sorted({c for (c,_,_,_) in ajustes_c197_validos})
    codigos_e111 = sorted({c for (c,_,_) in ajustes_e111_validos})
    codigos_e115 = sorted({c for (c,_,_) in ajustes_e115_validos})
    codigos_e116 = sorted({c for (c,_,_) in ajustes_e116_validos})
    codigos_usados = sorted(set(codigos_c197 + codigos_e111 + codigos_e115 + codigos_e116))

    conformidade_whitelist = False
    if codigos_usados:
        c197_ok = all(eh_codigo_difal_por_whitelist(uf, "C197", c) for c in codigos_c197) if codigos_c197 else True
        e111_ok = all(eh_codigo_difal_por_whitelist(uf, "E111", c) for c in codigos_e111) if codigos_e111 else True
        e115_ok = all(eh_codigo_difal_por_whitelist(uf, "E115", c) for c in codigos_e115) if codigos_e115 else True
        e116_ok = all(eh_codigo_difal_por_whitelist(uf, "E116", c) for c in codigos_e116) if codigos_e116 else True
        conformidade_whitelist = c197_ok and e111_ok and e115_ok and e116_ok

    for d in docs_alvo:
        did = f"{d.get('serie','')}|{d.get('numero','')}|{d.get('chave','')}"
        for it in d.get("itens", []):
            if not eh_cfop_alvo(it.get("cfop","")): continue
            linhas_ajuste = []
            if ajustes_doc_validos.get(did):
                for (reg, cod, descr, vl) in ajustes_doc_validos[did]:
                    linhas_ajuste.append((reg, cod, descr, vl))
            elif evid_periodo:
                for (reg, cod, descr, vl) in evid_periodo:
                    linhas_ajuste.append((reg, cod, descr, vl))
            else:
                linhas_ajuste.append(("", "", "", 0.0))
            for (reg, cod, descr, vl) in linhas_ajuste:
                analise_rows.append({
                    "Série": d.get("serie",""), "Número NF": d.get("numero",""), "Chave NF-e": d.get("chave",""),
                    "CFOP": it.get("cfop",""), "Descrição do Item": it.get("desc",""), "Valor Total Item": it.get("vl_item",0.0),
                    "Registro Ajuste": reg, "Cód. Ajuste DIFAL": cod, "Valor Ajuste": vl, "Descrição Ajuste": descr
                })

    for d in docs_alvo:
        did = f"{d.get('serie','')}|{d.get('numero','')}|{d.get('chave','')}"
        doc_tem_evidencia = bool(ajustes_doc_validos.get(did) or evid_periodo)
        if not doc_tem_evidencia:
            for it in d.get("itens", []):
                if not eh_cfop_alvo(it.get("cfop","")): continue
                sem_rows.append({
                    "Série": d.get("serie",""), "Número NF": d.get("numero",""), "Chave NF-e": d.get("chave",""),
                    "CFOP": it.get("cfop",""), "Descrição do Item": it.get("desc",""),
                    "Valor Total Item": it.get("vl_item",0.0),
                    "Observação": "Item com CFOP 2551/2556 sem evidência de DIFAL (C197/C195/E111/E115/E116)"
                })

    possui_notas = "Sim" if numeros_nfs_alvo else "Não"
    nfs_list = ", ".join(numeros_nfs_alvo) if numeros_nfs_alvo else ""
    tem_ajuste_difal = bool(ajustes_c197_validos or evid_periodo)

    codigos_descricoes = []
    for cod, descr, vl, _ in ajustes_c197_validos:
        codigos_descricoes.append(f"{cod} (C197) - VL {vl:.2f} - {descr or ''}".strip())
    for cod, descr, vl in ajustes_e111_validos:
        codigos_descricoes.append(f"{cod} (E111) - VL {vl:.2f} - {descr or ''}".strip())
    for cod, descr, vl in ajustes_e115_validos:
        codigos_descricoes.append(f"{cod} (E115) - VL {vl:.2f} - {descr or ''}".strip())
    for cod, descr, vl in ajustes_e116_validos:
        codigos_descricoes.append(f"{cod} (E116) - VL {vl:.2f} - {descr or ''}".strip())

    if tem_ajuste_difal and conformidade_whitelist:
        conforme_leg = "Sim (whitelist UF)"
    elif tem_ajuste_difal and not conformidade_whitelist and codigos_usados:
        conforme_leg = "Parcial/Não (códigos fora da whitelist)"
    else:
        conforme_leg = "Não se aplica / Ausente"

    check_rows.append({
        "Possui notas 2551/2556?": possui_notas, "Notas (nº)": nfs_list,
        "Tem código/ajuste DIFAL?": "Sim" if tem_ajuste_difal else "Não",
        "Códigos/Descrições identificados": "; ".join(codigos_descricoes),
        "Está conforme legislação?": conforme_leg,
    })

    if not numeros_nfs_alvo and not tem_ajuste_difal:
        status = "Não se aplica DIFAL"
    elif numeros_nfs_alvo and not tem_ajuste_difal:
        status = "DIFAL não apurado (há 2551/2556 sem evidência)"
    elif numeros_nfs_alvo and tem_ajuste_difal:
        status = "DIFAL OK"
    else:
        status = "Ajuste presente sem NF 2551/2556 (verificar coerência)"

    resumo_rows.append({
        "Status": status,
        "Notas 2551/2556 (nº)": nfs_list,
        "Códigos Ajuste Encontrados": ", ".join(codigos_usados)
    })

    df_analise = pd.DataFrame(analise_rows)
    df_sem = pd.DataFrame(sem_rows)
    df_check = pd.DataFrame(check_rows)
    df_resumo = pd.DataFrame(resumo_rows)
    return df_analise, df_sem, df_check, df_resumo

# =========================
# 5) CT-e
# =========================
def formatar_data_ddmmyyyy(data_8):
    if isinstance(data_8, str) and len(data_8) == 8 and data_8.isdigit():
        return f"{data_8[0:2]}/{data_8[2:4]}/{data_8[4:8]}"
    return data_8

def cte_parse(text: str):
    linhas = text.splitlines()
    comp, _, _, _ = extrair_competencia_0000(linhas[:20])
    todos, d100, warnings = [], {}, []

    for idx, linha in enumerate(linhas, 1):
        campos = linha.strip().split("|")
        if not campos or len(campos) < 2: continue
        if linha.strip().endswith("|"):
            campos.append("")
        reg = campos[1]

        if reg == "D100":
            d100 = {}
            try:
                d100['Competência'] = comp or ""
                d100['Chave CT-e'] = campos[10] if len(campos) > 10 and campos[10].strip() else 'N/A'
                d100['Série CT-e'] = campos[7] if len(campos) > 7 and campos[7].strip() else 'N/A'
                d100['Número CT-e'] = campos[9] if len(campos) > 9 and campos[9].strip() else 'N/A'
                d100['Data Emissão'] = formatar_data_ddmmyyyy(campos[11]) if len(campos) > 11 and campos[11].strip() else 'N/A'
                d100['Valor Total CT-e (D100)'] = parse_float_br(campos[15]) if len(campos) > 15 and campos[15].strip() else 0.0
                d100['BC ICMS CT-e (D100)'] = parse_float_br(campos[18]) if len(campos) > 18 and campos[18].strip() else 0.0
                d100['Valor ICMS CT-e (D100)'] = parse_float_br(campos[20]) if len(campos) > 20 and campos[20].strip() else 0.0
            except Exception as e:
                warnings.append(f"D100 malformado (linha {idx}): {e}")
                d100 = {}

        elif reg == "D190" and d100:
            try:
                row = d100.copy()
                row['CST CT-e'] = campos[2] if len(campos) > 2 and campos[2].strip() else 'N/A'
                row['CFOP (D190)'] = campos[3] if len(campos) > 3 and campos[3].strip() else 'N/A'
                aliq = parse_float_br(campos[4]) if len(campos) > 4 and campos[4].strip() else 0.0
                row['Alíquota ICMS (D190)'] = aliq
                vl_opr = parse_float_br(campos[5]) if len(campos) > 5 and campos[5].strip() else 0.0
                row['Valor Operação (D190)'] = vl_opr
                bc = parse_float_br(campos[6]) if len(campos) > 6 and campos[6].strip() else 0.0
                row['BC ICMS (D190)'] = bc
                ic = parse_float_br(campos[7]) if len(campos) > 7 and campos[7].strip() else 0.0
                row['Valor ICMS (D190)'] = ic
                row['Alíquota Efetiva (%)'] = (ic / vl_opr * 100) if vl_opr != 0 else 0.0
                # padding
                row['Num. Item'] = 'Não se Aplica'
                row['Cód. Item'] = 'Não se Aplica'
                row['Descrição do Produto'] = 'Serviço de Transporte'
                row['Alíquota IPI Item (%)'] = 'Não se Aplica'
                row['Valor IPI Item'] = 'Não se Aplica'
                row['NCM Item'] = 'Não se Aplica'
                row['Valor IPI Nota'] = 'Não se Aplica'
                todos.append(row)
            except Exception as e:
                warnings.append(f"D190 malformado (linha {idx}): {e}")

    if not todos:
        return (pd.DataFrame(columns=[
            'Competência','Chave CT-e','Série CT-e','Número CT-e','Data Emissão','CST CT-e','CFOP (D190)',
            'Valor Operação (D190)','Alíquota ICMS (D190)','BC ICMS (D190)','Valor ICMS (D190)','Alíquota Efetiva (%)',
            'Valor Total CT-e (D100)','BC ICMS CT-e (D100)','Valor ICMS CT-e (D100)',
            'Num. Item','Cód. Item','Descrição do Produto','Alíquota IPI Item (%)','Valor IPI Item','NCM Item','Valor IPI Nota'
        ]), pd.DataFrame(columns=['CFOP (D190)','CST CT-e','Valor Contábil (Resumo)','BC ICMS (Resumo)','Valor ICMS (Resumo)']))

    df = pd.DataFrame(todos)
    cols_order = [
        'Competência','Chave CT-e','Série CT-e','Número CT-e','Data Emissão','CST CT-e','CFOP (D190)',
        'Valor Operação (D190)','Alíquota ICMS (D190)','BC ICMS (D190)','Valor ICMS (D190)','Alíquota Efetiva (%)',
        'Valor Total CT-e (D100)','BC ICMS CT-e (D100)','Valor ICMS CT-e (D100)',
        'Num. Item','Cód. Item','Descrição do Produto','Alíquota IPI Item (%)','Valor IPI Item','NCM Item','Valor IPI Nota'
    ]
    df = df[[c for c in cols_order if c in df.columns]]
    df_resumo = df.groupby(['CFOP (D190)','CST CT-e'], as_index=False).agg({
        'Valor Operação (D190)':'sum',
        'BC ICMS (D190)':'sum',
        'Valor ICMS (D190)':'sum'
    }).rename(columns={
        'Valor Operação (D190)':'Valor Contábil (Resumo)',
        'BC ICMS (D190)':'BC ICMS (Resumo)',
        'Valor ICMS (D190)':'Valor ICMS (Resumo)'
    })
    return df, df_resumo

# =========================
# Excel helper
# =========================
def autosize_sheet(ws, df):
    try:
        ws.set_zoom(90)
        ws.freeze_panes(1, 0)
        for i, col in enumerate(df.columns):
            max_len = min(max(12, max((len(str(x)) for x in df[col].astype(str).values), default=0) + 2), 60)
            ws.set_column(i, i, max_len)
    except:
        pass

def build_excel_icms_ipi(exhibitions: dict) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        for sheet_name, df in exhibitions.items():
            name = sheet_name[:31]
            if df is None or df.empty:
                pd.DataFrame({"INFO":["Sem dados"]}).to_excel(writer, sheet_name=name, index=False)
                ws = writer.sheets[name]
                autosize_sheet(ws, pd.DataFrame({"INFO":["Sem dados"]}))
            else:
                df.to_excel(writer, sheet_name=name, index=False)
                ws = writer.sheets[name]
                autosize_sheet(ws, df)
    return output.getvalue()

# =========================
# PDF
# =========================
def detectar_assinatura_digital(text: str):
    low = text.lower()
    pistas = [
        "assinado digitalmente","assinatura digital","assinatura:",
        "begin pkcs7","begin certificate","fim da assinatura","pkcs7","p7s"
    ]
    for p in pistas:
        if p in low:
            return True, f"Evidência: '{p}'"
    return False, "Nenhuma evidência textual encontrada"

def _wrap_text(c, text, max_width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if c.stringWidth(test, "Helvetica", 10) <= max_width:
            cur = test
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines

def build_pdf_consolidado(paginas_info):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    margem = 2*cm
    content_width = width - 2*margem
    y0 = height - margem

    for info in paginas_info:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margem, y0, "Relatório Básico – Auditoria SPED ICMS/IPI")
        c.setStrokeColor(colors.black)
        c.setLineWidth(1)
        c.line(margem, y0-6, margem+content_width, y0-6)

        y = y0 - 25
        c.setFont("Helvetica", 10)
        header_lines = [
            f"Arquivo: {info.get('arquivo','-')}",
            f"Competência: {info.get('competencia','-')}",
            f"Empresa: {info.get('empresa','-')}",
            f"CNPJ: {info.get('cnpj','-')}    UF: {info.get('uf','-')}",
            f"Diagnóstico: {info.get('diagnostico','-')}",
            f"Assinatura digital: {'COM evidência' if info.get('assinatura',{}).get('tem') else 'SEM evidência'}"
            + (f" ({info.get('assinatura',{}).get('detalhe','')})" if info.get('assinatura',{}).get('detalhe') else "")
        ]
        for line in header_lines:
            for ln in _wrap_text(c, line, content_width):
                c.drawString(margem, y, ln); y -= 14

        y -= 6
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margem, y, "Resumo dos Ajustes")
        y -= 8
        c.setLineWidth(0.8)
        c.rect(margem, y-150, content_width, 150)
        inner_y = y - 16
        left_x = margem + 8
        right_x = margem + content_width/2 + 8
        c.setFont("Helvetica", 10)

        aj = info.get("ajustes", {})
        def _fmt_cods(s):
            if not s: return "-"
            s2 = sorted(list(s))
            sample = ", ".join(s2[:12])
            return sample + (" ..." if len(s2) > 12 else "")

        # esquerda
        for line in [
            f"C195: {aj.get('C195',{}).get('count',0)} (códigos: {_fmt_cods(aj.get('C195',{}).get('cods'))})",
            f"C197: {aj.get('C197',{}).get('count',0)} (códigos: {_fmt_cods(aj.get('C197',{}).get('cods'))})",
            f"E111: {aj.get('E111',{}).get('count',0)} (códigos: {_fmt_cods(aj.get('E111',{}).get('cods'))})",
        ]:
            for ln in _wrap_text(c, line, (content_width/2)-20):
                c.drawString(left_x, inner_y, ln); inner_y -= 12

        # direita
        inner_y2 = y - 16
        difal = info.get("difal", {})
        for line in [
            f"E115: {aj.get('E115',{}).get('count',0)} (códigos: {_fmt_cods(aj.get('E115',{}).get('cods'))})",
            f"E116: {aj.get('E116',{}).get('count',0)} (códigos: {_fmt_cods(aj.get('E116',{}).get('cods'))})",
            f"DIFAL 2551/2556: {difal.get('status','-')}",
            f"Códigos DIFAL: {_fmt_cods(difal.get('codigos', []))}",
        ]:
            for ln in _wrap_text(c, line, (content_width/2)-20):
                c.drawString(right_x, inner_y2, ln); inner_y2 -= 12

        c.setFont("Helvetica-Oblique", 8); c.setFillColor(colors.grey)
        c.drawRightString(margem+content_width, 1.5*cm, "Gerado por Auditor SPED – Smart01 (Streamlit)")
        c.setFillColor(colors.black)
        c.showPage()

    c.save()
    return buf.getvalue()

# =========================
# UI
# =========================
st.title("Auditor SPED – Smart01 (ICMS/IPI & PIS/COFINS – Básico)")
st.caption("Upload de SPED (.txt) • Análises básicas • Resumos e auditorias • Download Excel/PDF")

tipo = st.radio("Selecione o tipo de arquivo para análise:", ["SPED ICMS/IPI", "SPED PIS/COFINS"])
uploads = st.file_uploader("Envie um ou mais arquivos .txt", type=["txt"], accept_multiple_files=True)
btn = st.button("Executar análises")

if btn:
    if not uploads:
        st.warning("Envie pelo menos um arquivo .txt.")
        st.stop()

    if tipo == "SPED ICMS/IPI":
        agg_blank = []; agg_resumo_icms = []; agg_resumo_ipi = []
        agg_c195_all, agg_c197_all, agg_e111_all, agg_e115_all, agg_e116_all = [], [], [], [], []
        agg_difal_analise, agg_difal_sem, agg_difal_check, agg_difal_resumo = [], [], [], []
        agg_cte_detalhe, agg_cte_resumo = [], []
        pdf_paginas = []

        for up in uploads:
            text = load_text_from_upload(up)
            comp, razao, cnpj, uf = extrair_competencia_0000(text.splitlines())

            st.subheader(f"📄 Arquivo: {up.name}")
            colA, colB, colC = st.columns(3)
            with colA: st.write(f"**Competência:** {comp or '-'}")
            with colB: st.write(f"**Empresa:** {razao or '-'}")
            with colC: st.write(f"**CNPJ/UF:** {(cnpj or '-')}/{(uf or '-')}")

            # 1) SPED em branco?
            status_blank = check_sped_blank(text)
            df_blank = pd.DataFrame([{
                "Arquivo": up.name, "Competência": comp, "Empresa": razao, "CNPJ": cnpj, "UF": uf,
                "Diagnóstico": status_blank
            }])
            st.write("### 1) SPED em branco?")
            st.dataframe(df_blank, use_container_width=True)
            agg_blank.append(df_blank)

            # 2) Resumos
            st.write("### 2) Resumo por CFOP – ICMS (C190)")
            df_icms = resumo_cfop_icms(text)
            if not df_icms.empty:
                df_icms_ins = df_icms.copy()
                df_icms_ins.insert(0, "Arquivo", up.name)
                df_icms_ins.insert(1, "Competência", comp)
                df_icms_ins.insert(2, "CNPJ", cnpj)
                df_icms_ins.insert(3, "UF", uf)
                st.dataframe(df_icms_ins, use_container_width=True)
                agg_resumo_icms.append(df_icms_ins)
            else:
                st.info("Sem dados de C190.")

            st.write("### 2b) Resumo por CFOP – IPI (E510)")
            df_ipi = resumo_cfop_ipi(text)
            if not df_ipi.empty:
                df_ipi_ins = df_ipi.copy()
                df_ipi_ins.insert(0, "Arquivo", up.name)
                df_ipi_ins.insert(1, "Competência", comp)
                df_ipi_ins.insert(2, "CNPJ", cnpj)
                df_ipi_ins.insert(3, "UF", uf)
                st.dataframe(df_ipi_ins, use_container_width=True)
                agg_resumo_ipi.append(df_ipi_ins)
            else:
                st.info("Sem dados de E510.")

            # 3) Ajustes
            st.write("### 3) Ajustes C195/C197/E111/E115/E116")
            df_c195, df_c197, df_e111, df_e115, df_e116 = coletar_ajustes(text)
            for df_adj in (df_c195, df_c197, df_e111, df_e115, df_e116):
                if df_adj is not None and not df_adj.empty:
                    df_adj.insert(0, "Arquivo", up.name)
                    df_adj.insert(1, "Competência", comp)
                    df_adj.insert(2, "CNPJ", cnpj)
                    df_adj.insert(3, "UF", uf)
            c1, c2 = st.columns(2)
            with c1:
                st.caption("Ajustes por documento"); st.dataframe(df_c195 if not df_c195.empty else pd.DataFrame(columns=df_c195.columns), use_container_width=True)
                st.dataframe(df_c197 if not df_c197.empty else pd.DataFrame(columns=df_c197.columns), use_container_width=True)
            with c2:
                st.caption("Ajustes por período"); st.dataframe(df_e111 if not df_e111.empty else pd.DataFrame(columns=df_e111.columns), use_container_width=True)
                st.dataframe(df_e115 if not df_e115.empty else pd.DataFrame(columns=df_e115.columns), use_container_width=True)
                st.dataframe(df_e116 if not df_e116.empty else pd.DataFrame(columns=df_e116.columns), use_container_width=True)
            if not df_c195.empty: agg_c195_all.append(df_c195)
            if not df_c197.empty: agg_c197_all.append(df_c197)
            if not df_e111.empty: agg_e111_all.append(df_e111)
            if not df_e115.empty: agg_e115_all.append(df_e115)
            if not df_e116.empty: agg_e116_all.append(df_e116)

            # 4) DIFAL
            st.write("### 4) Auditoria DIFAL – Entradas 2551/2556")
            dfA, dfS, dfC, dfR = auditoria_difal_text(text)
            for dfX in (dfA, dfS, dfC, dfR):
                if not dfX.empty:
                    dfX.insert(0, "Arquivo", up.name)
                    dfX.insert(1, "Competência", comp)
                    dfX.insert(2, "CNPJ", cnpj)
                    dfX.insert(3, "UF", uf)
            st.dataframe(dfR, use_container_width=True)
            with st.expander("Ver detalhes da auditoria DIFAL"):
                st.dataframe(dfC, use_container_width=True)
                st.dataframe(dfA, use_container_width=True)
                st.dataframe(dfS, use_container_width=True)
            if not dfA.empty: agg_difal_analise.append(dfA)
            if not dfS.empty: agg_difal_sem.append(dfS)
            if not dfC.empty: agg_difal_check.append(dfC)
            if not dfR.empty: agg_difal_resumo.append(dfR)

            # 5) CT-e
            st.write("### 5) CT-e (D100/D190)")
            df_cte_det, df_cte_res = cte_parse(text)
            if not df_cte_det.empty:
                df_cte_det_ins = df_cte_det.copy(); df_cte_det_ins.insert(0, "Arquivo", up.name)
                st.dataframe(df_cte_det_ins, use_container_width=True)
                agg_cte_detalhe.append(df_cte_det_ins)
            else:
                st.info("Sem D100/D190 de CT-e neste arquivo.")
            if not df_cte_res.empty:
                df_cte_res_ins = df_cte_res.copy(); df_cte_res_ins.insert(0, "Arquivo", up.name)
                st.dataframe(df_cte_res_ins, use_container_width=True)
                agg_cte_resumo.append(df_cte_res_ins)

            # Página PDF
            def _conta(df, cod_col):
                if df is None or df.empty: return 0, set()
                if cod_col in df.columns:
                    vals = df[cod_col].astype(str).str.strip()
                    return len(df), set(v for v in vals if v)
                return len(df), set()

            c195_count, c195_cods = _conta(df_c195, "COD_OBS")
            c197_count, c197_cods = _conta(df_c197, "COD_AJ")
            e111_count, e111_cods = _conta(df_e111, "COD_AJ_APUR")
            e115_count, e115_cods = _conta(df_e115, "COD_INF_ADIC")
            e116_count, e116_cods = _conta(df_e116, "COD_REC") if "COD_REC" in df_e116.columns else (len(df_e116), set())

            difal_status = "-"
            difal_codigos = []
            if not dfR.empty:
                difal_status = dfR.iloc[0].get("Status", "-")
                difal_codigos = [x.strip() for x in str(dfR.iloc[0].get("Códigos Ajuste Encontrados", "")).split(",") if x.strip()]

            tem_ass, detail_ass = detectar_assinatura_digital(text)

            pdf_paginas.append({
                "arquivo": up.name, "competencia": comp, "empresa": razao, "cnpj": cnpj, "uf": uf,
                "diagnostico": status_blank,
                "assinatura": {"tem": tem_ass, "detalhe": detail_ass},
                "ajustes": {
                    "C195": {"count": c195_count, "cods": c195_cods},
                    "C197": {"count": c197_count, "cods": c197_cods},
                    "E111": {"count": e111_count, "cods": e111_cods},
                    "E115": {"count": e115_count, "cods": e115_cods},
                    "E116": {"count": e116_count, "cods": e116_cods},
                },
                "difal": {"status": difal_status, "codigos": difal_codigos},
            })

        # ========== Excel consolidado ==========
        def concat_or_empty(dfs_list, cols=None):
            if not dfs_list:
                return pd.DataFrame(columns=cols) if cols else pd.DataFrame()
            return pd.concat(dfs_list, ignore_index=True)

        excel_tabs = {
            "01_SPED_em_branco": concat_or_empty(agg_blank),
            "02_Resumo_CFOP_ICMS_C190": concat_or_empty(agg_resumo_icms, ["Arquivo","Competência","CNPJ","UF","CFOP","CST","BC_ICMS","ICMS"]),
            "03_Resumo_CFOP_IPI_E510": concat_or_empty(agg_resumo_ipi,  ["Arquivo","Competência","CNPJ","UF","CFOP","CST","VL_CONTABIL","BC_IPI","IPI"]),
            "04_Ajustes_C195": concat_or_empty(agg_c195_all),
            "05_Ajustes_C197": concat_or_empty(agg_c197_all),
            "06_Ajustes_E111": concat_or_empty(agg_e111_all),
            "07_Ajustes_E115": concat_or_empty(agg_e115_all),
            "08_Ajustes_E116": concat_or_empty(agg_e116_all),
            "09_DIFAL_Resumo": concat_or_empty(agg_difal_resumo),
            "10_DIFAL_Checklist": concat_or_empty(agg_difal_check),
            "11_DIFAL_Analise": concat_or_empty(agg_difal_analise),
            "12_DIFAL_SemEvid": concat_or_empty(agg_difal_sem),
            "13_CTe_Detalhe_D100_D190": concat_or_empty(agg_cte_detalhe),
            "14_CTe_Resumo_CFOP": concat_or_empty(agg_cte_resumo),
        }
        xlsx_bytes = build_excel_icms_ipi(excel_tabs)
        st.download_button(
            "⬇️ Baixar Excel consolidado (ICMS/IPI)",
            data=xlsx_bytes,
            file_name="auditoria_icms_ipi_consolidado.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # ========== PDF ==========
        if pdf_paginas:
            pdf_bytes = build_pdf_consolidado(pdf_paginas)
            st.download_button(
                "🧾⬇️ Gerar PDF consolidado",
                data=pdf_bytes,
                file_name="auditoria_icms_ipi_resumo.pdf",
                mime="application/pdf"
            )

    else:
        st.info("Rota PIS/COFINS (básico) pronta para receber os requisitos das abas/colunas.")
