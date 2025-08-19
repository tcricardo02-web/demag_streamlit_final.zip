import streamlit as st
import logging
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import pint

from sqlalchemy import create_engine, Column, Integer, Float, String, ForeignKey, Table
from sqlalchemy.orm import sessionmaker, relationship, declarative_base

# ------------------------------------------------------------------------------
# Configuração de logger e unidades (Pint)
# ------------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ureg = pint.UnitRegistry()
Q_ = ureg.Quantity

# ------------------------------------------------------------------------------
# Configuração do banco de dados com SQLAlchemy
# ------------------------------------------------------------------------------
DB_PATH = "sqlite:///compressor.db"
Base = declarative_base()
engine = create_engine(DB_PATH, echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

# Tabelas associativas (para mistura de gás) podem ser definidas caso necessário

# Exemplos de modelos ORM para algumas entidades (simplificados)
class GasComponent(Base):
    __tablename__ = "gas_component"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    molecular_weight = Column(Float)


class GasMixture(Base):
    __tablename__ = "gas_mixture"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)


class FrameModel(Base):
    __tablename__ = "frame"
    id = Column(Integer, primary_key=True, index=True)
    rpm = Column(Float)
    stroke_m = Column(Float)
    n_throws = Column(Integer)
    throws = relationship("ThrowModel", back_populates="frame")


class ThrowModel(Base):
    __tablename__ = "throw"
    id = Column(Integer, primary_key=True, index=True)
    frame_id = Column(Integer, ForeignKey("frame.id"))
    throw_number = Column(Integer)
    bore_m = Column(Float)
    clearance_m = Column(Float)
    VVCP = Column(Float)
    SACE = Column(Float)
    SAHE = Column(Float)
    
    frame = relationship("FrameModel", back_populates="throws")


class ActuatorModel(Base):
    __tablename__ = "actuator"
    id = Column(Integer, primary_key=True, index=True)
    power_available_kW = Column(Float)
    derate_percent = Column(Float)
    air_cooler_fraction = Column(Float)


def init_db():
    Base.metadata.create_all(bind=engine)
    logger.info("Banco de dados inicializado.")


# ------------------------------------------------------------------------------
# Domínio: Uso de dataclasses para entidades
# ------------------------------------------------------------------------------
@dataclass
class Frame:
    rpm: float
    stroke: float  # em metros (interno SI)
    n_throws: int


@dataclass
class Throw:
    throw_number: int
    bore: float       # metros
    clearance: float  # metros
    VVCP: float       # em porcentagem
    SACE: float       # em porcentagem
    SAHE: float       # em porcentagem
    throw_id: Optional[int] = None


@dataclass
class Actuator:
    power_kW: float
    derate_percent: float
    air_cooler_fraction: float


# ------------------------------------------------------------------------------
# Cálculos de performance (simplificado, inspirado no Ariel7)
# ------------------------------------------------------------------------------
def clamp(n, a, b):
    return max(a, min(n, b))


def perform_performance_calculation(
    mass_flow: float,
    inlet_pressure: Q_,
    inlet_temperature: Q_,
    n_stages: int,
    PR_total: float,
    throws: List[Throw],
    stage_mapping: Dict[int, int],
    actuator: Actuator,
) -> Dict:
    """
    Calcula outputs de performance para cada estágio.
    Todas as entradas já são convertidas para unidades SI (Pa, K, m).
    """
    # Parâmetros básicos
    m_dot = mass_flow  # kg/s
    
    # Unidades de entrada
    P_in = inlet_pressure.to(ureg.Pa).magnitude
    T_in = inlet_temperature.to(ureg.K).magnitude

    # Divisão da razão total de compressão por estágios
    n = max(n_stages, 1)
    PR_base = PR_total ** (1.0 / n)
    
    gamma = 1.30
    cp = 2.0  # kJ/(kg*K)
    
    stage_details = []
    total_W_kW = 0.0
    
    # Mapeia throws por id para acesso
    throws_by_id = {t.throw_number: t for t in throws}
    
    for stage in range(1, n + 1):
        # Pressões de entrada e saída por estágio
        P_in_stage = P_in * (PR_base ** (stage - 1))
        P_out_stage = P_in_stage * PR_base
        
        # Obter dados do throw associado ao estágio
        throw_number = stage_mapping.get(stage)
        if throw_number is not None and throw_number in throws_by_id:
            throw_obj = throws_by_id[throw_number]
            SACE = throw_obj.SACE
            VVCP = throw_obj.VVCP
            SAHE = throw_obj.SAHE
        else:
            SACE = VVCP = SAHE = 0.0
        
        # Eficiência isentrópica influenciada por parâmetros do throw
        eta_isent = 0.65 + 0.15 * (SACE / 100.0) - 0.05 * (VVCP / 100.0) + 0.10 * (SAHE / 100.0)
        eta_isent = clamp(eta_isent, 0.65, 0.92)
        
        # Temperaturas de saída considerando transformação isentrópica
        T_out_isent = T_in * (PR_base ** ((gamma - 1.0) / gamma))
        T_out_actual = T_in + (T_out_isent - T_in) / max(eta_isent, 1e-6)        
        delta_T = T_out_actual - T_in
        
        # Potência requerida para o estágio (kW)
        W_stage = m_dot * cp * delta_T / 1000.0
        total_W_kW += W_stage
        
        stage_details.append({
            "stage": stage,
            "P_in_bar": P_in_stage / 1e5,
            "P_out_bar": P_out_stage / 1e5,
            "PR": PR_base,
            "T_in_C": T_in - 273.15,
            "T_out_C": T_out_actual - 273.15,
            "isentropic_efficiency": eta_isent,
            "shaft_power_kW": W_stage,
        })
        
        # Para o próximo estágio, a saída atual vira a entrada
        T_in = T_out_actual
    
    outputs = {
        "frame_rpm": None,  # a ser preenchido na UI conforme o frame
        "mass_flow_kg_s": m_dot,
        "inlet_pressure_bar": P_in / 1e5,
        "inlet_temperature_C": inlet_temperature.to(ureg.degC).magnitude,
        "n_stages": n_stages,
        "total_shaft_power_kW": total_W_kW,
        "stage_details": stage_details,
        "a_Ariel7_compatible": {
            "stages": stage_details,
            "total_shaft_power_kW": total_W_kW
        }
    }
    return outputs


# ------------------------------------------------------------------------------
# Diagrama usando Plotly: Acionador, Frame e Throws
# ------------------------------------------------------------------------------
def generate_diagram(frame: Frame, throws: List[Throw], actuator: Actuator) -> go.Figure:
    """
    Cria um diagrama interativo representando:
      - O frame (como retângulo central)
      - Cada throw, conforme o número de throws (posicionados abaixo do frame)
      - O acionador (representado à direita)
    """
    fig = go.Figure()
    
    diagram_width = 800
    diagram_height = 300
    
    # Posicionamento do frame
    frame_x = 50
    frame_y = 100
    frame_width = 200
    frame_height = 50
    
    # Desenha o frame
    fig.add_shape(type="rect",
                  x0=frame_x, y0=frame_y,
                  x1=frame_x+frame_width, y1=frame_y+frame_height,
                  line=dict(color="RoyalBlue"),
                  fillcolor="LightSkyBlue")
    fig.add_annotation(x=frame_x+frame_width/2, y=frame_y+frame_height/2,
                       text=f"Frame\n(RPM: {frame.rpm:.0f})", showarrow=False)
    
    # Distribuir os throws abaixo do frame
    n = frame.n_throws
    throw_spacing = frame_width / max(n, 1)
    throw_shapes = []
    for t in throws:
        idx = t.throw_number - 1
        throw_x = frame_x + idx * throw_spacing + throw_spacing/4
        throw_y = frame_y + frame_height + 20
        throw_width = throw_spacing/2
        throw_height = 30
        fig.add_shape(type="rect",
                      x0=throw_x, y0=throw_y,
                      x1=throw_x+throw_width, y1=throw_y+throw_height,
                      line=dict(color="DarkOrange"),
                      fillcolor="Moccasin")
        fig.add_annotation(x=throw_x+throw_width/2, y=throw_y+throw_height/2,
                           text=f"Throw {t.throw_number}", showarrow=False, font_size=10)
    
    # Desenha o acionador à direita
    actuator_x = frame_x + frame_width + 150
    actuator_y = frame_y + frame_height/2 - 20
    fig.add_shape(type="rect",
                  x0=actuator_x, y0=actuator_y,
                  x1=actuator_x+120, y1=actuator_y+60,
                  line=dict(color="SaddleBrown"),
                  fillcolor="PeachPuff")
    fig.add_annotation(x=actuator_x+60, y=actuator_y+30,
                       text=f"Atuador\n{actuator.power_kW:.0f} kW", showarrow=False)
    
    fig.update_layout(
        width=diagram_width,
        height=diagram_height,
        margin=dict(l=20, r=20, t=20, b=20),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False)
    )
    
    return fig


# ------------------------------------------------------------------------------
# Interface do usuário com Streamlit
# ------------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Calculadora de Performance de Compressor - Ariel7-style", layout="wide")
    st.title("Calculadora de Performance de Compressor (Ariel7-style)")
    
    # Inicializa a base de dados
    init_db()
    
    # Seleção do sistema de unidades
    UNIT_OPTIONS = ["SI", "Metric"]
    if "unit_system" not in st.session_state:
        st.session_state["unit_system"] = "SI"
    
    with st.sidebar:
        st.header("Configurações Gerais")
        unit = st.selectbox("Sistema de unidades", options=UNIT_OPTIONS, index=0)
        st.session_state["unit_system"] = unit
        
        if st.button("Resetar DB"):
            import os
            if os.path.exists("compressor.db"):
                os.remove("compressor.db")
            init_db()
            st.success("Banco de dados reinicializado.")
    
    # Dados do Processo (Aba 1)
    tabs = st.tabs(["Processo", "Configuração do Equipamento"])
    
    # --- Aba PROCESSO ---
    with tabs[0]:
        st.header("Processo")
        st.subheader("Composição e Condições")
        
        # Aqui poderíamos incluir componentes do gás usando dataclasses, mas para simplificação manteremos inputs
        if st.session_state["unit_system"] == "SI":
            inlet_pressure = st.number_input("Pressão de sucção (Pa)", value=200000.0, step=1000.0)
            inlet_temperature = st.number_input("Temperatura de entrada (K)", value=298.15, step=1.0)
        else:
            inlet_pressure_bar = st.number_input("Pressão de sucção (bar)", value=2.0, step=0.01)
            inlet_temperature_C = st.number_input("Temperatura de entrada (°C)", value=25.0, step=1.0)
            inlet_pressure = inlet_pressure_bar * 1e5
            inlet_temperature = inlet_temperature_C + 273.15
        
        mass_flow = st.number_input("Massa de Gás (kg/s)", value=12.0, step=0.1)
        n_stages = st.number_input("Número de estágios", min_value=1, max_value=12, value=3, step=1)
        PR_total = st.number_input("Razão de compressão total (PR)", min_value=1.0, max_value=100.0, value=2.5, step=0.1)
        
        st.subheader("Calcular Performance")
        if st.button("Calcular outputs (Processo)"):
            # Para esse cálculo os throws e mapeamento serão preenchidos na aba de equipamento
            calc_outputs = perform_performance_calculation(
                mass_flow=mass_flow,
                inlet_pressure=Q_(inlet_pressure, ureg.Pa),
                inlet_temperature=Q_(inlet_temperature, ureg.K),
                n_stages=n_stages,
                PR_total=PR_total,
                throws=[],           # para cálculo completo, configure os throws na aba de equipamento
                stage_mapping={},
                actuator=Actuator(power_kW=0, derate_percent=0, air_cooler_fraction=0)
            )
            st.json(calc_outputs)
    
    # --- Aba CONFIGURAÇÃO DO EQUIPAMENTO ---
    with tabs[1]:
        st.header("Configuração do Equipamento")
        st.subheader("Frame e Throws")
        
        # Inputs do frame
        frame_rpm = st.number_input("RPM do Frame", min_value=100, max_value=3000, value=900, step=10)
        if st.session_state["unit_system"] == "SI":
            stroke_input = st.number_input("Stroke do Frame (m)", min_value=0.01, max_value=1.0, value=0.12, step=0.01)
        else:
            stroke_input = st.number_input("Stroke do Frame (mm)", min_value=10, max_value=1000, value=120, step=1)
        n_throws = st.number_input("Número de Throws", min_value=1, max_value=20, value=3, step=1)
        
        # Conversão para SI
        if st.session_state["unit_system"] == "Metric":
            stroke_si = stroke_input / 1000.0
        else:
            stroke_si = stroke_input
        
        # Criação do objeto Frame (domínio)
        current_frame = Frame(rpm=frame_rpm, stroke=stroke_si, n_throws=n_throws)
        
        # Inputs para cada throw
        st.subheader("Parâmetros dos Throws")
        throws_list = []
        for i in range(1, int(n_throws)+1):
            st.markdown(f"**Throw {i}**")
            if st.session_state["unit_system"] == "SI":
                bore = st.number_input(f"Throw {i} - Bore (m)", value=0.08, min_value=0.01, max_value=0.2, step=0.001, key=f"throw{i}_bore")
                clearance = st.number_input(f"Throw {i} - Clearance (m)", value=0.002, min_value=0.0005, max_value=0.01, step=0.0005, key=f"throw{i}_clearance")
            else:
                bore_mm = st.number_input(f"Throw {i} - Bore (mm)", value=80, min_value=10, max_value=400, step=1, key=f"throw{i}_bore")
                clearance_mm = st.number_input(f"Throw {i} - Clearance (mm)", value=2, min_value=0, max_value=20, step=1, key=f"throw{i}_clearance")
                bore = bore_mm / 1000.0
                clearance = clearance_mm / 1000.0
            VVCP = st.number_input(f"Throw {i} - VVCP (%)", value=90, min_value=0, max_value=100, step=1, key=f"throw{i}_VVCP")
            SACE = st.number_input(f"Throw {i} - SACE (%)", value=80, min_value=0, max_value=100, step=1, key=f"throw{i}_SACE")
            SAHE = st.number_input(f"Throw {i} - SAHE (%)", value=60, min_value=0, max_value=100, step=1, key=f"throw{i}_SAHE")
            throws_list.append(Throw(throw_number=i, bore=bore, clearance=clearance, VVCP=VVCP, SACE=SACE, SAHE=SAHE))
        
        # Mapeamento de estágios para throws (simples: estágio i usa throw i)
        stage_mapping = {i: i for i in range(1, int(n_stages)+1)}
        
        # Inputs do Atuador
        st.subheader("Parâmetros do Atuador")
        power_kW = st.number_input("Potência disponível (kW)", value=250.0, min_value=0.0, step=1.0)
        derate_pct = st.number_input("Derate (%)", value=5.0, min_value=0.0, max_value=100.0, step=0.5)
        air_cooler_frac = st.number_input("Air Cooler (%)", value=25.0, min_value=0.0, max_value=100.0, step=0.5)
        current_actuator = Actuator(power_kW=power_kW, derate_percent=derate_pct, air_cooler_fraction=air_cooler_frac)
        
        st.markdown("---")
        st.subheader("Diagrama do Equipamento")
        fig_diagram = generate_diagram(current_frame, throws_list, current_actuator)
        st.plotly_chart(fig_diagram, use_container_width=True)
        
        st.markdown("---")
        st.subheader("Salvar Configuração e Calcular Performance")
        if st.button("Salvar Configuração e Calcular Outputs"):
            # Aqui exemplificamos a persistência usando SQLAlchemy.
            db = SessionLocal()
            # Salvar frame
            frame_model = FrameModel(rpm=frame_rpm, stroke_m=stroke_si, n_throws=n_throws)
            db.add(frame_model)
            db.commit()
            db.refresh(frame_model)
            # Salvar throws
            for t in throws_list:
                throw_model = ThrowModel(
                    frame_id=frame_model.id,
                    throw_number=t.throw_number,
                    bore_m=t.bore,
                    clearance_m=t.clearance,
                    VVCP=t.VVCP,
                    SACE=t.SACE,
                    SAHE=t.SAHE
                )
                db.add(throw_model)
            # Salvar atuador
            actuator_model = ActuatorModel(
                power_available_kW=power_kW,
                derate_percent=derate_pct,
                air_cooler_fraction=air_cooler_frac
            )
            db.add(actuator_model)
            db.commit()
            db.close()
            
            # Para o cálculo, definindo também dados exemplares do processo:
            calc_outputs = perform_performance_calculation(
                mass_flow=12.0,
                inlet_pressure=Q_(6000000, ureg.Pa),  # ex: 60 bar
                inlet_temperature=Q_(298.15, ureg.K),
                n_stages=n_stages,
                PR_total=2.5,
                throws=throws_list,
                stage_mapping=stage_mapping,
                actuator=current_actuator
            )
            # Acrescenta o RPM do frame nos outputs
            calc_outputs["frame_rpm"] = frame_rpm
            st.success("Configuração salva e outputs calculados:")
            st.json(calc_outputs)


if __name__ == "__main__":
    main()
