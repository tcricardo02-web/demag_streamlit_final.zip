import streamlit as st
import sqlite3
import json
import math
import pandas as pd
from datetime import datetime
from typing import List, Dict

# ---------------------------
# Configurações e utilidades
# ---------------------------

DB_PATH = "compressor.db"

UNIT_OPTIONS = ["SI", "Metric"]

# Unidades de exibição e conversões básicas
def clamp(n, a, b):
    return max(a, min(n, b))

def to_pa(p_bar_or_pa, unit_system):
    if unit_system == "SI":
        return float(p_bar_or_pa)  # assume Pa
    else:
        return float(p_bar_or_pa) * 1e5

def from_pa(pa_value, unit_system):
    if unit_system == "SI":
        return pa_value
    else:
        return pa_value / 1e5

def K_from_C(C):
    return C + 273.15

def C_from_K(K):
    return K - 273.15

# Converte stroke/bore entre unidades para SI (m) internamente
def stroke_to_si(value, unit_system):
    if unit_system == "SI":
        return float(value)
    else:
        return float(value) / 1000.0  # mm -> m

def bore_to_si(value, unit_system):
    if unit_system == "SI":
        return float(value)
    else:
        return float(value) / 1000.0

def mm_display_from_si(value, unit_system):
    if unit_system == "SI":
        return value * 1000.0  # m -> mm (display)
    else:
        return value

# ---------------------------
# Banco de dados (SQLite)
# ---------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS gas_component (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        molecular_weight REAL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS gas_mixture (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS gas_mixture_component (
        mixture_id INTEGER,
        component_id INTEGER,
        mole_fraction REAL,
        FOREIGN KEY(mixture_id) REFERENCES gas_mixture(id),
        FOREIGN KEY(component_id) REFERENCES gas_component(id),
        PRIMARY KEY (mixture_id, component_id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS frame (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rpm REAL,
        stroke_m REAL,
        n_throws INTEGER
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS throw (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        frame_id INTEGER,
        throw_number INTEGER,
        bore_m REAL,
        clearance_m REAL,
        VVCP REAL,
        SACE REAL,
        SAHE REAL,
        FOREIGN KEY(frame_id) REFERENCES frame(id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS stage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stage_number INTEGER
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS stage_throw (
        stage_id INTEGER,
        throw_id INTEGER,
        PRIMARY KEY (stage_id, throw_id),
        FOREIGN KEY(stage_id) REFERENCES stage(id),
        FOREIGN KEY(throw_id) REFERENCES throw(id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS actuator (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        power_available_kW REAL,
        derate_percent REAL,
        air_cooler_fraction REAL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS process_inputs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mixture_id INTEGER,
        mass_flow_kg_s REAL,
        inlet_pressure_bar REAL,
        inlet_temperature_C REAL,
        FOREIGN KEY(mixture_id) REFERENCES gas_mixture(id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS outputs_ariel7 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        frame_id INTEGER,
        outputs_json TEXT,
        FOREIGN KEY(frame_id) REFERENCES frame(id)
    );
    """)

    conn.commit()
    conn.close()

def db_connect():
    return sqlite3.connect(DB_PATH)

# Helpers to save/load mix, frame, throws, etc.
def save_gas_component(name: str, mw: float):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO gas_component (name, molecular_weight) VALUES (?, ?)", (name, mw))
    conn.commit()
    cur.execute("SELECT id FROM gas_component WHERE name = ?", (name,))
    cid = cur.fetchone()[0]
    conn.close()
    return cid

def get_gas_components():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT id, name, molecular_weight FROM gas_component")
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "molecular_weight": r[2]} for r in rows]

def save_gas_mixture(name: str, components: List[Dict]):
    """
    components: list of {"component_id": int, "mole_fraction": float}
    """
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("INSERT INTO gas_mixture (name) VALUES (?)", (name,))
    mixture_id = cur.lastrowid
    for c in components:
        cur.execute(
            "INSERT INTO gas_mixture_component (mixture_id, component_id, mole_fraction) VALUES (?, ?, ?)",
            (mixture_id, c["component_id"], c["mole_fraction"]),
        )
    conn.commit()
    conn.close()
    return mixture_id

def save_frame(rpm: float, stroke_m: float, n_throws: int):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("INSERT INTO frame (rpm, stroke_m, n_throws) VALUES (?, ?, ?)", (rpm, stroke_m, n_throws))
    frame_id = cur.lastrowid
    conn.commit()
    conn.close()
    return frame_id

def save_throws(frame_id: int, throws: List[Dict]):
    """
    throws: list of {"throw_number": int, "bore_m": float, "clearance_m": float, "VVCP": float, "SACE": float, "SAHE": float}
    """
    conn = db_connect()
    cur = conn.cursor()
    for t in throws:
        cur.execute("""
            INSERT INTO throw (frame_id, throw_number, bore_m, clearance_m, VVCP, SACE, SAHE)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (frame_id, t["throw_number"], t["bore_m"], t["clearance_m"], t["VVCP"], t["SACE"], t["SAHE"]))
    conn.commit()
    conn.close()

def save_stage_mapping(frame_id: int, stage_mapping: Dict[int, int]):
    """
    stage_mapping: dict {stage_number: throw_id}
    """
    conn = db_connect()
    cur = conn.cursor()
    # Make stages
    for st_num, throw_id in stage_mapping.items():
        cur.execute("INSERT OR IGNORE INTO stage (stage_number) VALUES (?)", (st_num,))
        cur.execute("SELECT id FROM stage WHERE stage_number = ?", (st_num,))
        stage_id = cur.fetchone()[0]
        if throw_id is not None:
            cur.execute("INSERT INTO stage_throw (stage_id, throw_id) VALUES (?, ?)", (stage_id, throw_id))
    conn.commit()
    conn.close()

def save_actuator(power_kW: float, derate_percent: float, air_cooler_fraction: float):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("INSERT INTO actuator (power_available_kW, derate_percent, air_cooler_fraction) VALUES (?, ?, ?)",
                (power_kW, derate_percent, air_cooler_fraction))
    conn.commit()
    conn.close()

def save_process_inputs(mixture_id: int, mass_flow_kg_s: float, inlet_pressure_bar: float, inlet_temperature_C: float):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("INSERT INTO process_inputs (mixture_id, mass_flow_kg_s, inlet_pressure_bar, inlet_temperature_C) VALUES (?, ?, ?, ?)",
                (mixture_id, mass_flow_kg_s, inlet_pressure_bar, inlet_temperature_C))
    conn.commit()
    conn.close()

def insert_example_data():
    """
    Insere dados de exemplo (componentes, mistura, frame, throws, etc.)
    """
    # Componentes de exemplo
    ch4_id = save_gas_component("CH4", 16.04)
    c2h6_id = save_gas_component("C2H6", 30.07)
    co2_id = save_gas_component("CO2", 44.01)
    n2_id = save_gas_component("N2", 28.02)

    # Mistura
    mixture_id = save_gas_mixture("Mistura_exemplo", [
        {"component_id": ch4_id, "mole_fraction": 0.90},
        {"component_id": c2h6_id, "mole_fraction": 0.05},
        {"component_id": co2_id, "mole_fraction": 0.03},
        {"component_id": n2_id, "mole_fraction": 0.02},
    ])

    # Frame e Throws (exemplo simples com 3 throws)
    frame_id = save_frame(rpm=900, stroke_m=0.12, n_throws=3)
    # Throws (bore, clearance, VVCP, SACE, SAHE)
    throws = [
        {"throw_number": 1, "bore_m": 0.08, "clearance_m": 0.002, "VVCP": 0.9, "SACE": 0.8, "SAHE": 0.6},
        {"throw_number": 2, "bore_m": 0.08, "clearance_m": 0.002, "VVCP": 0.92, "SACE": 0.85, "SAHE": 0.65},
        {"throw_number": 3, "bore_m": 0.08, "clearance_m": 0.002, "VVCP": 0.94, "SACE": 0.88, "SAHE": 0.68},
    ]
    save_throws(frame_id, throws)

    # Estágios e mapeamento estágios <- throws 1,2,3
    stage_mapping = {
        1: 1,
        2: 2,
        3: 3
    }
    save_stage_mapping(frame_id, stage_mapping)

    # Actuador
    save_actuator(power_kW=250.0, derate_percent=5.0, air_cooler_fraction=25.0)

    # Processo
    save_process_inputs(mixture_id, mass_flow_kg_s=12.0, inlet_pressure_bar=60.0, inlet_temperature_C=25.0)

    return

# ---------------------------
# Cálculo simplificado (Ariel7-ish) - versão próxima a Ariel7
# ---------------------------

def perform_performance_calculation(params: Dict) -> Dict:
    """
    Calcula outputs de forma simplificada para cada estágio, buscando aproximar o Ariel7.

    params contém:
      - unit_system: 'SI' ou 'Metric'
      - mass_flow_kg_s
      - inlet_pressure_pa (Pa)
      - inlet_temperature_K (K)
      - n_stages
      - PR_total (Razão de compressão total)
      - rpm
      - stroke_m (m)
      - throws: list com {'throw_id','bore_m','clearance_m','VVCP','SACE','SAHE'}
      - stage_mapping: dict stage_number -> throw_id
      - actuator: {'power_kW','derate','air_cooler'}
    """
    # Parâmetros básicos
    m_dot = float(params.get("mass_flow_kg_s", 1.0))

    unit_system = params.get("unit_system", "SI")

    # Inlet
    if unit_system == "SI":
        P_in_pa = float(params.get("inlet_pressure_pa", 2e5))  # Pa
        T_in_K = float(params.get("inlet_temperature_K", 298.15))
        stroke_m = float(params.get("stroke_m", 0.12))
        n = int(params.get("n_stages", 3))
    else:
        # Não esperado, mas manter para robustez
        P_in_bar = float(params.get("inlet_pressure_bar", 2.0))
        P_in_pa = P_in_bar * 1e5
        T_in_C = float(params.get("inlet_temperature_C", 25.0))
        T_in_K = T_in_C + 273.15
        stroke_mm = float(params.get("stroke_mm", 120.0))
        stroke_m = stroke_mm / 1000.0
        n = int(params.get("n_stages", 3))

    # PR total
    PR_total = float(params.get("PR_total", 2.5))
    if n <= 0:
        n = 1
    PR_base = PR_total ** (1.0 / n)

    # Constantes de gás (apenas para MVP)
    gamma = 1.30
    cp_kJ_per_kgK = 2.0  # ~2.0 kJ/kg-K para mistura de gás natural (MVP)
    stage_details = []
    total_W_kW = 0.0

    # Dados de throws por ID
    stage_mapping = params.get("stage_mapping", {})  # stage -> throw_id
    throws_by_id: Dict[int, Dict] = {}
    for t in params.get("throws", []):
        throws_by_id[t["throw_id"]] = t

    # Início de cada estágio
    P_in_bar = P_in_pa / 1e5  # para exibir (bar)
    for i in range(1, n + 1):
        # P_in_i e P_out_i por estágio
        P_in_bar_i = P_in_bar * (PR_base ** (i - 1))
        P_out_bar_i = P_in_bar_i * PR_base

        # Obter parâmetros do throw associado (se houver)
        throw_id = stage_mapping.get(i)
        if throw_id is not None and throw_id in throws_by_id:
            th = throws_by_id[throw_id]
            SACE = th.get("SACE", 0.0)
            VVCP = th.get("VVCP", 0.0)
            SAHE = th.get("SAHE", 0.0)
        else:
            SACE = 0.0
            VVCP = 0.0
            SAHE = 0.0

        # Eficiência (isentropica) por estágio (ajuste com SACE/VVCP/SAHE)
        eta_isent = 0.65 + 0.15 * (SACE / 100.0) - 0.05 * (VVCP / 100.0) + 0.10 * (SAHE / 100.0)
        eta_isent = clamp(eta_isent, 0.65, 0.92)

        # Eficiência polytrópica (para registro; pode não ser usada diretamente no W)
        eta_poly = 0.75 + (SACE / 100.0) * 0.08 - (VVCP / 100.0) * 0.02 + (SAHE / 100.0) * 0.03
        eta_poly = clamp(eta_poly, 0.60, 0.95)

        # Cálculos de temperatura
        T_in_stage_K = T_in_K
        T_out_isentropic_K = T_in_stage_K * (PR_base ** ((gamma - 1.0) / gamma))
        T_out_actual_K = T_in_stage_K + (T_out_isentropic_K - T_in_stage_K) / max(eta_isent, 1e-6)
        delta_T_K = T_out_actual_K - T_in_stage_K

        # Potência do estágio (W) -> kW
        W_stage_kW = m_dot * cp_kJ_per_kgK * delta_T_K / 1000.0
        total_W_kW += W_stage_kW

        stage_details.append({
            "stage": i,
            "P_in_bar": P_in_bar_i,
            "P_out_bar": P_out_bar_i,
            "PR": PR_base,
            "T_in_C": T_in_stage_K - 273.15,
            "T_out_C": T_out_actual_K - 273.15,
            "poly_eff": eta_poly,
            "isentropic_eff": eta_isent,
            "shaft_power_kW": W_stage_kW
        })

        # 1) Prepara para próximo estágio
        # Assume T_out_actual_K becomes T_in for next stage
        T_in_K = T_out_actual_K

    # Outputs no contexto Ariel7 (estrutura simplificada)
    outputs = {
        "frame_rpm": params.get("rpm", 0.0),
        "mass_flow_kg_s": m_dot,
        "inlet_pressure_bar": P_in_bar,
        "inlet_temperature_C": (T_in_K - 273.15) if unit_system == "SI" else (T_in_K - 273.15),
        "n_stages": n,
        "total_shaft_power_kW": total_W_kW,
        "stage_details": stage_details,
        "a Ariel7 compatible": {
            "frame_rpm": params.get("rpm", 0.0),
            "stages": [
                {
                    "stage_number": s["stage"],
                    "P_in_bar": s["P_in_bar"],
                    "P_out_bar": s["P_out_bar"],
                    "PR": s["PR"],
                    "T_in_C": s["T_in_C"],
                    "T_out_C": s["T_out_C"],
                    "polytropic_efficiency": s["poly_eff"],
                    "isentropic_efficiency": s["isentropic_eff"],
                    "shaft_power_kW": s["shaft_power_kW"],
                } for s in stage_details
            ],
            "total_shaft_power_kW": total_W_kW
        }
    }

    return outputs

# ---------------------------
# Diagrama SVG (compressor + atuador)
# ---------------------------

def generate_diagram_svg(frame_rpm, n_stages, stage_details, stroke_m, throws):
    """
    Gera um SVG simples com o diagrama do compressor e do acionador.
    stage_details já contém P_in_bar, P_out_bar, etc.
    """
    width = 900
    height = 320
    pad = 20
    bar_h = 180
    stage_w = 140
    svg = f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'

    # Estágio base (linha de fluxo)
    x0 = pad + 60
    y0 = height // 2
    svg += f'<line x1="{x0}" y1="{y0}" x2="{width - pad - 60}" y2="{y0}" stroke="gray" stroke-width="2" />'
    # Blocos de estágios
    for idx, st in enumerate(stage_details):
        x = x0 + (idx * (stage_w + 20))
        w = stage_w
        h = 90
        y = y0 - h // 2
        svg += f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#bde4f6" stroke="#2c6eaf" rx="6" />'
        svg += f'<text x="{x + w/2}" y="{y + h/2}" text-anchor="middle" alignment-baseline="middle" font-family="Arial" font-size="12" fill="#0b1f3a">St{st["stage"]}</text>'
        # P_in/P_out
        svg += f'<text x="{x + w/2}" y="{y - 6}" text-anchor="middle" font-family="Arial" font-size="11" fill="#333">'
        svg += f'P_in {st["P_in_bar"]:.2f} bar / P_out {st["P_out_bar"]:.2f} bar'
        svg += '</text>'

    # Atuador (lado direito)
    actuator_x = width - pad - 150
    actuator_y = height // 2 - 20
    svg += f'<rect x="{actuator_x}" y="{actuator_y}" width="120" height="60" fill="#ffcc99" stroke="#8b5e2b" rx="8" />'
    svg += f'<text x="{actuator_x + 60}" y="{actuator_y + 32}" text-anchor="middle" font-family="Arial" font-size="12" fill="#3a2a0b">Acionador</text>'

    # rpm
    svg += f'<text x="{actuator_x + 60}" y="{actuator_y + 52}" text-anchor="middle" font-family="Arial" font-size="11" fill="#3a2a0b">RPM {frame_rpm:.0f}</text>'

    svg += '</svg>'
    return svg

# ---------------------------
# UI principal (Streamlit)
# ---------------------------

def main():
    st.set_page_config(page_title="Compressor Performance Calculator (Ariel7-style)", layout="wide")
    st.title("Calculadora de performance de compressores alternativos (Ariel7-style MVP)")

    # Iniciar db
    init_db()

    if "unit_system" not in st.session_state:
        st.session_state["unit_system"] = "SI"

    # Unidade na UI (barra lateral)
    with st.sidebar:
        st.header("Unidades de medida")
        unit = st.selectbox("Sistema de unidades", options=UNIT_OPTIONS, index=0)
        st.session_state["unit_system"] = unit

        st.markdown("---")
        st.subheader("Ações")
        if st.button("Carregar dados de exemplo"):
            insert_example_data()
            st.success("Dados de exemplo inseridos no DB.")
        if st.button("Resetar DB"):
            import os
            if os.path.exists(DB_PATH):
                os.remove(DB_PATH)
            init_db()
            st.success("Banco de dados resetado.")

    # Dados de edição em memória (Processo)
    if "mixture_edit" not in st.session_state:
        st.session_state.mixture_edit = [
            {"component_name": "CH4", "component_id": None, "mole_fraction": 0.90, "molecular_weight": 16.04},
            {"component_name": "C2H6", "component_id": None, "mole_fraction": 0.05, "molecular_weight": 30.07},
            {"component_name": "CO2", "component_id": None, "mole_fraction": 0.03, "molecular_weight": 44.01},
        ]
    if "throws" not in st.session_state:
        st.session_state.throws = []  # cada item: {'throw_number','bore_m','clearance_m','VVCP','SACE','SAHE'}

    tabs = st.tabs(["Processo", "Configuração do equipamento"])

    # ----------------------------
    # Aba PROCESSO
    # ----------------------------
    with tabs[0]:
        st.header("Processo")
        st.subheader("Composição do gás")
        with st.expander("Gerenciar componentes (em memória para MVP)"):
            for idx, comp in enumerate(st.session_state.mixture_edit):
                col1, col2, col3 = st.columns([1, 1, 2])
                comp_name = col1.text_input(f"Componente #{idx+1}", value=comp["component_name"], key=f"proc_name_{idx}")
                mole_frac = col2.number_input(f"Fração molar ({comp['component_name']})", min_value=0.0, max_value=1.0, value=comp["mole_fraction"], step=0.01, key=f"proc_frac_{idx}")
                mw = col3.number_input("Massa molar (g/mol)", value=comp.get("molecular_weight", 0.0), step=0.01, key=f"proc_mw_{idx}")
                st.session_state.mixture_edit[idx]["component_name"] = comp_name
                st.session_state.mixture_edit[idx]["mole_fraction"] = mole_frac
                st.session_state.mixture_edit[idx]["molecular_weight"] = mw

            if st.button("Adicionar componente"):
                st.session_state.mixture_edit.append({"component_name": "Novo", "component_id": None, "mole_fraction": 0.0, "molecular_weight": 0.0})

        st.markdown("---")
        st.subheader("Parâmetros do processo")
        if st.session_state["unit_system"] == "SI":
            inlet_pressure_pa = st.number_input("Pressão de sucção (Pa)", value=200000.0, step=1000.0, key="proc_inlet_pa")
            inlet_temp_K = st.number_input("Temperatura de entrada (K)", value=298.15, step=1.0, key="proc_inlet_k")
        else:
            inlet_pressure_bar = st.number_input("Pressão de sucção (bar)", value=2.0, step=0.01, key="proc_inlet_bar")
            inlet_temp_C = st.number_input("Temperatura de entrada (°C)", value=25.0, step=1.0, key="proc_inlet_c")

        mass_flow = st.number_input("Massa de gás (kg/s)", value=12.0, min_value=0.1, step=0.1)

        if st.session_state["unit_system"] == "SI":
            pressure_pa = inlet_pressure_pa
            T_in_K = inlet_temp_K
        else:
            pressure_pa = inlet_pressure_bar * 1e5
            T_in_K = inlet_temp_C + 273.15

        n_stages = st.number_input("Número de estágios", min_value=1, max_value=12, value=3, step=1, key="proc_n_stages")
        PR_total = st.number_input("Razão de compressão total (PR)", min_value=1.0, max_value=100.0, value=2.5, step=0.1, key="proc_total_pr")

        st.subheader("Resumo e outputs")
        if st.button("Calcular outputs (Processo)"):
            calc_params = {
                "unit_system": st.session_state["unit_system"],
                "mass_flow_kg_s": mass_flow,
                # Entrada em Pa/K se SI, ou Bar/°C se Metric (convertidos para SI internamente)
                "inlet_pressure_pa": pressure_pa,
                "inlet_temperature_K": T_in_K,
                "n_stages": int(n_stages),
                "PR_total": float(PR_total),
                "rpm": 0.0,
                "stroke_m": 0.0,
                "throws": [],  # preenchido na aba Equipamento
                "stage_mapping": {},  # preenchido na aba Equipamento
            }
            st.info("Para cálculo completo, vá até a aba Configuração do equipamento e forneça throws (bore/clearance/SACE/SACE/SAHE) e mapeie estágios.")
            st.json({"status": "need equipment data"})

        st.markdown("---")
        st.subheader("Ajuda / Notas rápidas")
        st.write("Unidades: escolha SI para Pa/K/m/s (internamente convertidas para Pa e K para cálculos). Escolha Metric para bar/°C/mm (conversões internas para Pa e K).")
        st.write("A aba Processo lida com composição do gás e condições de processo. A aba Configuração do equipamento define o frame (rpm, stroke, throws) e mapeia throws para estágios.")

    # ----------------------------
    # Aba CONFIGURAÇÃO DO EQUIPAMENTO
    # ----------------------------
    with tabs[1]:
        st.header("Configuração do equipamento")
        st.subheader("Diagrama do compressor e do acionador")
        frame_rpm = st.number_input("RPM do frame", min_value=100, max_value=3000, value=900, step=10, key="cfg_rpm")
        if st.session_state["unit_system"] == "SI":
            stroke_input = st.number_input("Stroke do frame (m)", min_value=0.01, max_value=1.0, value=0.12, step=0.01, key="cfg_stroke_m")
        else:
            stroke_input = st.number_input("Stroke do frame (mm)", min_value=10, max_value=1000, value=120, step=1, key="cfg_stroke_mm")

        n_throws = st.number_input("Numero de throws", min_value=1, max_value=20, value=3, step=1, key="cfg_nthrows")

        # Normalize stroke_m
        if st.session_state["unit_system"] == "SI":
            stroke_m_internal = float(stroke_input)
        else:
            stroke_m_internal = float(stroke_input) / 1000.0

        st.subheader("Throws (parâmetros por throw)")
        throws_local = []
        for i in range(1, int(n_throws) + 1):
            col1, col2, col3 = st.columns(3)
            if st.session_state["unit_system"] == "SI":
                bore = col1.number_input(f"Throw #{i} - Bore (m)", value=0.08, min_value=0.01, max_value=0.2, step=0.001, key=f"throw_{i}_bore_m")
                clearance = col2.number_input(f"Throw #{i} - Clearance (m)", value=0.002, min_value=0.0005, max_value=0.01, step=0.0005, key=f"throw_{i}_clear_m")
            else:
                bore = col1.number_input(f"Throw #{i} - Bore (mm)", value=80, min_value=10, max_value=400, step=1, key=f"throw_{i}_bore_mm")
                clearance = col2.number_input(f"Throw #{i} - Clearance (mm)", value=2, min_value=0, max_value=20, step=1, key=f"throw_{i}_clear_mm")
            VVCP = col3.number_input(f"Throw #{i} - VVCP (%)", value=90, min_value=0, max_value=100, step=1, key=f"throw_{i}_VVCP")
            SACE = st.number_input(f"Throw #{i} - SACE (%)", value=80, min_value=0, max_value=100, step=1, key=f"throw_{i}_SACE")
            SAHE = st.number_input(f"Throw #{i} - SAHE (%)", value=60, min_value=0, max_value=100, step=1, key=f"throw_{i}_SAHE")

            # Converter para SI (interno) se necessário
            if st.session_state["unit_system"] == "Metric":
                bore_m = bore / 1000.0
                clearance_m = clearance / 1000.0
            else:
                bore_m = bore
                clearance_m = clearance

            throws_local.append({
                "throw_number": i,
                "bore_m": float(bore_m) if bore_m is not None else 0.08,
                "clearance_m": float(clearance_m) if clearance_m is not None else 0.002,
                "VVCP": float(VVCP),
                "SACE": float(SACE),
                "SAHE": float(SAHE),
                "throw_id": i
            })

        st.subheader("Mapeamento de estágios e throws")
        stage_to_throw = {}
        for s in range(1, int(n_stages) + 1):
            options = [""]
            options += [f"Throw #{t['throw_number']}" for t in throws_local]
            choice = st.selectbox(f"Stage {s} recebe:", options=options, key=f"stage_{s}_assign")
            if choice:
                th_id = int(choice.split("#")[1].split(")")[0]) if "Throw" in choice else None
                stage_to_throw[s] = th_id
            else:
                stage_to_throw[s] = None

        st.subheader("Aquecimento: diagrama simples")
        st.markdown("Diagrama gerado com base na configuração do frame, stages e throws (SVG simples).")

        # Diagrama SVG (dados de placeholder para stages)
        stage_details_placeholder = [{"stage": s, "P_in_bar": 0, "P_out_bar": 0} for s in range(1, int(n_stages) + 1)]
        svg = generate_diagram_svg(frame_rpm, int(n_stages), stage_details_placeholder, stroke_m_internal, throws_local)
        st.markdown(svg, unsafe_allow_html=True)

        st.subheader("Actuator e derate")
        power_kW = st.number_input("Potência disponível do acionador (kW)", value=250.0, min_value=0.0, step=1.0, key="cfg_act_power")
        derate_pct = st.number_input("Derate do acionador (%)", value=5.0, min_value=0.0, max_value=100.0, step=0.5, key="cfg_act_derate")
        air_cooler_frac = st.number_input("Air cooler disponível (% da potência)", value=25.0, min_value=0.0, max_value=100.0, step=0.5, key="cfg_act_cooler")

        # Armazenar dados para DB (opcional)
        if st.button("Salvar configuração (Frame/Throws/Stage/Actuator)"):
            # Salvar frame
            frame_id = save_frame(rpm=frame_rpm, stroke_m=stroke_m_internal, n_throws=int(n_throws))
            # Salvar throws
            throws_payload = []
            for t in throws_local:
                throws_payload.append({
                    "throw_id": t["throw_id"],
                    "throw_number": t["throw_number"],
                    "bore_m": t["bore_m"],
                    "clearance_m": t["clearance_m"],
                    "VVCP": t["VVCP"],
                    "SACE": t["SACE"],
                    "SAHE": t["SAHE"],
                })
            save_throws(frame_id, throws_payload)
            # Mapear estágios
            stage_map = {s: stage_to_throw[s] for s in stage_to_throw}
            save_stage_mapping(frame_id, stage_map)

            # Actuator
            save_actuator(power_kW=power_kW, derate_percent=derate_pct, air_cooler_fraction=air_cooler_frac)

            st.success("Configuração salva no banco de dados.")

        if st.button("Calcular outputs com dados salvos (Ariel7 compatible)"):
            # Buscar dados salvos para cálculo
            conn = db_connect()
            cur = conn.cursor()
            cur.execute("SELECT id, rpm, stroke_m, n_throws FROM frame ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                frame_id, rpm, stroke_m, n_throws_db = row
                # obter throws
                cur.execute("SELECT id, throw_number, bore_m, clearance_m, VVCP, SACE, SAHE FROM throw WHERE frame_id=?", (frame_id,))
                throws_db = cur.fetchall()
                # stage mapping
                cur.execute("SELECT stage_id, throw_id FROM stage_throw")
                mappings = cur.fetchall()
                stage_mapping = {}
                for sid, tid in mappings:
                    cur.execute("SELECT stage_number FROM stage WHERE id=?", (sid,))
                    sn = cur.fetchone()[0]
                    stage_mapping[sn] = tid
                # actuator
                cur.execute("SELECT power_available_kW, derate_percent, air_cooler_fraction FROM actuator ORDER BY id DESC LIMIT 1")
                act = cur.fetchone()
                if act:
                    power_kW, derate_percent, air_cooler = act
                else:
                    power_kW, derate_percent, air_cooler = 200, 0, 0

                # Throws list
                throws_list = []
                for t in throws_db:
                    th_id, tnum, bore_m, clear_m, VVCP, SACE, SAHE = t
                    throws_list.append({"throw_id": th_id, "throw_number": tnum, "bore_m": bore_m, "clearance_m": clear_m, "VVCP": VVCP, "SACE": SACE, "SAHE": SAHE})

                # Constantes de processo fictício (para demonstração)
                mass_flow = 12.0
                inlet_pressure_bar = 60.0
                inlet_temperature_C = 25.0

                # Construir input para cálculo
                calc_params = {
                    "unit_system": st.session_state["unit_system"],
                    "mass_flow_kg_s": mass_flow,
                    "inlet_pressure_pa": inlet_pressure_bar * 1e5,
                    "inlet_temperature_K": inlet_temperature_C + 273.15,
                    "n_stages": int(n_stages),
                    "rpm": rpm,
                    "stroke_m": stroke_m,
                    "throws": throws_list,
                    "stage_mapping": stage_mapping,
                    "PR_total": 2.5,
                    "actuator": {"power_kW": power_kW, "derate": derate_percent, "air_cooler": air_cooler}
                }

                # Cálculo (mestre)
                outputs = perform_performance_calculation(calc_params)

                st.subheader("Outputs (Ariel7-compatible)")
                st.write("Frame RPM:", rpm)
                st.write("Total shaft power (kW):", f"{outputs['total_shaft_power_kW']:.2f}")

                # Tabela de estágios
                df_stage = []
                for s in outputs["stage_details"]:
                    df_stage.append({
                        "Stage": s["stage"],
                        "P_in_bar": s["P_in_bar"],
                        "P_out_bar": s["P_out_bar"],
                        "PR": s["PR"],
                        "T_in_C": s["T_in_C"],
                        "T_out_C": s["T_out_C"],
                        "polytropic_efficiency": s["poly_eff"],
                        "isentropic_efficiency": s["isentropic_eff"],
                        "W_kW": s["shaft_power_kW"],
                    })
                st.dataframe(pd.DataFrame(df_stage))

                # Exibir JSON de outputs Ariel7
                st.json(outputs["a Ariel7 compatible"])

                # Diagrama SVG com novos dados
                svg2 = generate_diagram_svg(rpm, int(n_stages), outputs["stage_details"], stroke_m, [{"throw_number": t["throw_id"], "bore_m": t["bore_m"]} for t in throws_list])
                st.markdown(svg2, unsafe_allow_html=True)
            else:
                st.warning("Nenhum frame encontrado no DB para calcular. Salve uma configuração primeiro.")

# ---------------------------
# Execução
# ---------------------------

if __name__ == "__main__":
    main()
