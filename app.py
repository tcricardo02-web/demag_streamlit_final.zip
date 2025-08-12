
import streamlit as st
import pandas as pd
import numpy as np

from calculations import calcular_performance
from utils import converter_potencia, converter_vazao

st.set_page_config(page_title="Demag Compressor Performance", layout="wide")

st.title("📊 Análise de Performance - Compressor Demag")

aba = st.tabs(["Entrada de Dados", "Resultados", "Gráficos", "Exportar"])

with aba[0]:
    st.header("Entrada de Dados")

    # Seleção de unidades
    sistema_unidades = st.radio("Sistema de Unidades", ["SI", "Oil & Gas"], horizontal=True)

    # Dados do gás
    st.subheader("Composição do Gás Natural (%)")
    col1, col2, col3, col4, col5 = st.columns(5)
    ch4 = col1.number_input("CH₄", 0.0, 100.0, 90.0)
    c2h6 = col2.number_input("C₂H₆", 0.0, 100.0, 5.0)
    c3h8 = col3.number_input("C₃H₈", 0.0, 100.0, 2.0)
    co2 = col4.number_input("CO₂", 0.0, 100.0, 1.0)
    n2 = col5.number_input("N₂", 0.0, 100.0, 2.0)

    # Dados do compressor
    st.subheader("Dados do Compressor")
    modelo = st.text_input("Modelo")
    estagios = st.number_input("Número de Estágios", 1, 6, 2)
    diametro = st.number_input("Diâmetro do Pistão", 10.0, 1000.0, 500.0, help="mm")
    curso = st.number_input("Curso do Pistão", 10.0, 1000.0, 300.0, help="mm")
    rpm = st.number_input("Rotação", 100, 3000, 1000, help="RPM")
    p_suc = st.number_input("Pressão de Sucção", 0.5, 200.0, 5.0, help="bar")
    p_desc = st.number_input("Pressão de Descarga", 0.5, 500.0, 50.0, help="bar")
    t_suc = st.number_input("Temperatura de Sucção", -50.0, 150.0, 25.0, help="°C")
    t_amb = st.number_input("Temperatura Ambiente", -50.0, 150.0, 25.0, help="°C")

    # Tipo de acionador
    st.subheader("Dados do Acionador")
    tipo_acionador = st.selectbox("Tipo de Acionador", ["Motor a combustão interna a gás natural", "Motor elétrico"])

    pot_nom = st.number_input("Potência Nominal", 0.0, 10000.0, 500.0, help="kW")
    if tipo_acionador == "Motor a combustão interna a gás natural":
        eficiencia = st.number_input("Eficiência Mecânica", 0.0, 100.0, 90.0, help="%")
        consumo_comb = st.number_input("Consumo de Combustível", 0.0, 5000.0, 200.0, help="Nm³/h")
        eficiencia_termica = st.number_input("Eficiência Térmica", 0.0, 100.0, 35.0, help="%")
    else:
        eficiencia = st.number_input("Eficiência Elétrica", 0.0, 100.0, 95.0, help="%")
        fator_potencia = st.number_input("Fator de Potência", 0.0, 1.0, 0.9)

    torque = st.number_input("Torque Nominal", 0.0, 50000.0, 1000.0, help="N·m")

    if st.button("Calcular Performance"):
        resultados = calcular_performance(
            diametro, curso, rpm, p_suc, p_desc, t_suc, ch4, c2h6, c3h8, co2, n2, estagios
        )
        st.session_state["resultados"] = resultados

with aba[1]:
    st.header("Resultados")
    if "resultados" in st.session_state:
        st.dataframe(st.session_state["resultados"])
    else:
        st.info("Insira os dados e clique em Calcular Performance.")

with aba[2]:
    st.header("Gráficos")
    st.info("Em desenvolvimento: curvas de Potência x Vazão e Temperatura x Estágio.")

with aba[3]:
    st.header("Exportar Resultados")
    st.info("Exportação em CSV e PDF em desenvolvimento.")
