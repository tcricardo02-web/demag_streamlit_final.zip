
import pandas as pd

def calcular_performance(diametro, curso, rpm, p_suc, p_desc, t_suc, ch4, c2h6, c3h8, co2, n2, estagios):
    # Modelo simplificado
    pot_kw = (p_desc - p_suc) * diametro * curso * rpm * 0.0001
    pot_hp = pot_kw * 1.34102
    dados = {
        "Estágio": list(range(1, estagios+1)),
        "Potência (kW)": [pot_kw/estagios]*estagios,
        "Potência (HP)": [pot_hp/estagios]*estagios,
        "Temperatura Descarga (°C)": [t_suc + (i*10) for i in range(estagios)]
    }
    return pd.DataFrame(dados)
