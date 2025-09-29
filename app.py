# app_sped_auditor_streamlit.py
# ... (restante do código que você já tem acima permanece igual ATÉ os imports) ...
import streamlit as st
import pandas as pd
import io
import re
import os
import sys
import unicodedata
from collections import defaultdict

# +++ NOVO: reportlab para gerar PDF +++
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph
from reportlab.lib.enums import TA_LEFT

st.set_page_config(page_title="Auditor SPED - Smart01", layout="wide", initial_sidebar_state="expanded")

# (todas as funções utilitárias que você já tem continuam iguais)

# +++ NOVO: heurística para detectar “assinatura digital” no .txt +++
def detectar_assinatura_digital(text: str):
    """
    Heurística: procura por pistas comuns em SPEDs/arquivos assinados.
    Ex.: 'Assinado digitalmente', 'Assinatura', 'BEGIN PKCS7', 'BEGIN CERTIFICATE'
    Retorna (bool, detalhe)
    """
    low = text.lower()
    pistas = [
        "assinado digitalmente",
        "assinatura digital",
        "assinatura:",
        "begin pkcs7",
        "begin certificate",
        "fim da assinatura",
        "pkcs7",
        "p7s",
    ]
    for p in pistas:
        if p in low:
            return True, f"Evidência: '{p}'"
    return False, "Nenhuma evidência textual encontrada"

# +++ NOVO: helper para quebrar texto em linhas que caibam na largura +++
def _wrap_text(c, text, max_width):
    """
    Quebra 'text' em múltiplas linhas que caibam na largura max_width (em pontos).
    Usa a métrica de stringWidth da fonte atual do canvas.
    """
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if c.stringWidth(test, "Helvetica", 10) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

# +++ NOVO: gerador de PDF consolidado +++
def build_pdf_consolidado(paginas_info):
    """
    paginas_info: lista de dicts, um por arquivo:
    {
      'arquivo', 'competencia', 'empresa', 'cnpj', 'uf', 'diagnostico',
      'ajustes': {'C195':{'count':X,'cods':set(...)}, ...},
      'difal': {'status': str, 'codigos': [..]},
      'assinatura': {'tem': bool, 'detalhe': str}
    }
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4  # 595 x 842 pt aprox

    margem = 2*cm
    content_width = width - 2*margem
    y0 = height - margem

    for info in paginas_info:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margem, y0, "Relatório Básico – Auditoria SPED ICMS/IPI")

        # Linha de cabeçalho
        c.setStrokeColor(colors.black)
        c.setLineWidth(1)
        c.line(margem, y0-6, margem+content_width, y0-6)

        # Bloco: dados do arquivo
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
            # quebra se necessário
            for ln in _wrap_text(c, line, content_width):
                c.drawString(margem, y, ln)
                y -= 14

        # Quadro: Resumo de Ajustes
        y -= 6
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margem, y, "Resumo dos Ajustes")
        y -= 8
        c.setLineWidth(0.8)
        c.rect(margem, y-150, content_width, 150)  # quadro de 150pt de altura
        inner_y = y - 16
        left_x = margem + 8
        right_x = margem + content_width/2 + 8
        c.setFont("Helvetica", 10)

        # Coluna esquerda: C195/C197
        aj = info.get("ajustes", {})
        def _fmt_cods(s):
            if not s:
                return "-"
            s2 = sorted(list(s))
            sample = ", ".join(s2[:12])
            return sample + (" ..." if len(s2) > 12 else "")

        linhas_esq = [
            f"C195: {aj.get('C195',{}).get('count',0)} (códigos: {_fmt_cods(aj.get('C195',{}).get('cods'))})",
            f"C197: {aj.get('C197',{}).get('count',0)} (códigos: {_fmt_cods(aj.get('C197',{}).get('cods'))})",
            f"E111: {aj.get('E111',{}).get('count',0)} (códigos: {_fmt_cods(aj.get('E111',{}).get('cods'))})",
        ]
        for line in linhas_esq:
            for ln in _wrap_text(c, line, (content_width/2)-20):
                c.drawString(left_x, inner_y, ln)
                inner_y -= 12

        # Coluna direita: E115/E116 + DIFAL
        inner_y2 = y - 16
        difal = info.get("difal", {})
        linhas_dir = [
            f"E115: {aj.get('E115',{}).get('count',0)} (códigos: {_fmt_cods(aj.get('E115',{}).get('cods'))})",
            f"E116: {aj.get('E116',{}).get('count',0)} (códigos: {_fmt_cods(aj.get('E116',{}).get('cods'))})",
            f"DIFAL 2551/2556: {difal.get('status','-')}",
            f"Códigos DIFAL: {_fmt_cods(difal.get('codigos', []))}",
        ]
        for line in linhas_dir:
            for ln in _wrap_text(c, line, (content_width/2)-20):
                c.drawString(right_x, inner_y2, ln)
                inner_y2 -= 12

        # Rodapé
        c.setFont("Helvetica-Oblique", 8)
        c.setFillColor(colors.grey)
        c.drawRightString(margem+content_width, 1.5*cm, "Gerado por Auditor SPED – Smart01 (Streamlit)")
        c.setFillColor(colors.black)

        c.showPage()

    c.save()
    return buf.getvalue()

# ==========================================
# UI
# ==========================================
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
        excel_tabs = {}
        agg_blank = []
        agg_resumo_icms = []
        agg_resumo_ipi = []
        agg_c195_all, agg_c197_all, agg_e111_all, agg_e115_all, agg_e116_all = [], [], [], [], []
        agg_difal_analise, agg_difal_sem, agg_difal_check, agg_difal_resumo = [], [], [], []
        agg_cte_detalhe, agg_cte_resumo = [], []

        # +++ NOVO: páginas para PDF consolidado +++
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

            # 2) Resumo CFOP – ICMS / IPI
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
                st.caption("Ajustes por documento")
                st.dataframe(df_c195 if not df_c195.empty else pd.DataFrame(columns=df_c195.columns), use_container_width=True)
                st.dataframe(df_c197 if not df_c197.empty else pd.DataFrame(columns=df_c197.columns), use_container_width=True)
            with c2:
                st.caption("Ajustes por período")
                st.dataframe(df_e111 if not df_e111.empty else pd.DataFrame(columns=df_e111.columns), use_container_width=True)
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
                df_cte_det_ins = df_cte_det.copy()
                df_cte_det_ins.insert(0, "Arquivo", up.name)
                st.dataframe(df_cte_det_ins, use_container_width=True)
                agg_cte_detalhe.append(df_cte_det_ins)
            else:
                st.info("Sem D100/D190 de CT-e neste arquivo.")
            if not df_cte_res.empty:
                df_cte_res_ins = df_cte_res.copy()
                df_cte_res_ins.insert(0, "Arquivo", up.name)
                st.dataframe(df_cte_res_ins, use_container_width=True)
                agg_cte_resumo.append(df_cte_res_ins)

            # +++ NOVO: montar dados para 1 página de PDF +++
            # contagens e códigos únicos por tipo de ajuste
            def _conta(df, cod_col):
                if df is None or df.empty:
                    return 0, set()
                col = cod_col if cod_col in df.columns else None
                if not col:
                    return len(df), set()
                values = df[col].astype(str).str.strip()
                return len(df), set(v for v in values if v)

            c195_count, c195_cods = _conta(df_c195, "COD_OBS")
            c197_count, c197_cods = _conta(df_c197, "COD_AJ")
            e111_count, e111_cods = _conta(df_e111, "COD_AJ_APUR")
            e115_count, e115_cods = _conta(df_e115, "COD_INF_ADIC")
            e116_count, e116_cods = _conta(df_e116, "COD_REC") if "COD_REC" in df_e116.columns else (len(df_e116), set())

            # status DIFAL e códigos
            difal_status = "-"
            difal_codigos = []
            if not dfR.empty:
                difal_status = dfR.iloc[0].get("Status", "-")
                difal_codigos = [x.strip() for x in str(dfR.iloc[0].get("Códigos Ajuste Encontrados", "")).split(",") if x.strip()]

            # assinatura
            tem_ass, detail_ass = detectar_assinatura_digital(text)

            pdf_paginas.append({
                "arquivo": up.name,
                "competencia": comp,
                "empresa": razao,
                "cnpj": cnpj,
                "uf": uf,
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

        # Excel consolidado (já existia)
        def concat_or_empty(dfs_list, cols=None):
            if not dfs_list:
                return pd.DataFrame(columns=cols) if cols else pd.DataFrame()
            return pd.concat(dfs_list, ignore_index=True)

        df_blank_all   = concat_or_empty(agg_blank)
        df_icms_all    = concat_or_empty(agg_resumo_icms, ["Arquivo","Competência","CNPJ","UF","CFOP","CST","BC_ICMS","ICMS"])
        df_ipi_all     = concat_or_empty(agg_resumo_ipi,  ["Arquivo","Competência","CNPJ","UF","CFOP","CST","VL_CONTABIL","BC_IPI","IPI"])
        df_c195_all    = concat_or_empty(agg_c195_all)
        df_c197_all    = concat_or_empty(agg_c197_all)
        df_e111_all    = concat_or_empty(agg_e111_all)
        df_e115_all    = concat_or_empty(agg_e115_all)
        df_e116_all    = concat_or_empty(agg_e116_all)
        df_difA_all    = concat_or_empty(agg_difal_analise)
        df_difS_all    = concat_or_empty(agg_difal_sem)
        df_difC_all    = concat_or_empty(agg_difal_check)
        df_difR_all    = concat_or_empty(agg_difal_resumo)
        df_cte_det_all = concat_or_empty(agg_cte_detalhe)
        df_cte_res_all = concat_or_empty(agg_cte_resumo)

        excel_tabs = {
            "01_SPED_em_branco": df_blank_all,
            "02_Resumo_CFOP_ICMS_C190": df_icms_all,
            "03_Resumo_CFOP_IPI_E510": df_ipi_all,
            "04_Ajustes_C195": df_c195_all,
            "05_Ajustes_C197": df_c197_all,
            "06_Ajustes_E111": df_e111_all,
            "07_Ajustes_E115": df_e115_all,
            "08_Ajustes_E116": df_e116_all,
            "09_DIFAL_Resumo": df_difR_all,
            "10_DIFAL_Checklist": df_difC_all,
            "11_DIFAL_Analise": df_difA_all,
            "12_DIFAL_SemEvid": df_difS_all,
            "13_CTe_Detalhe_D100_D190": df_cte_det_all,
            "14_CTe_Resumo_CFOP": df_cte_res_all,
        }
        xlsx_bytes = build_excel_icms_ipi(excel_tabs)
        st.download_button(
            "⬇️ Baixar Excel consolidado (ICMS/IPI)",
            data=xlsx_bytes,
            file_name="auditoria_icms_ipi_consolidado.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # +++ NOVO: Botão para gerar o PDF consolidado +++
        if pdf_paginas:
            pdf_bytes = build_pdf_consolidado(pdf_paginas)
            st.download_button(
                "🧾⬇️ Gerar PDF consolidado",
                data=pdf_bytes,
                file_name="auditoria_icms_ipi_resumo.pdf",
                mime="application/pdf"
            )

    else:
        st.info("Rota PIS/COFINS (básico) pronta para receber seus requisitos de saída (abas/colunas).")
