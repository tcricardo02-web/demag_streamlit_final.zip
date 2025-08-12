
def converter_potencia(valor, de='kW', para='HP'):
    if de == 'kW' and para == 'HP':
        return valor * 1.34102
    elif de == 'HP' and para == 'kW':
        return valor / 1.34102
    else:
        return valor

def converter_vazao(valor, de='m3/h', para='MMSCFD'):
    if de == 'm3/h' and para == 'E3m3/d':
        return valor * 24 / 1000
    elif de == 'm3/h' and para == 'MMSCFD':
        return valor * 0.000588577
    else:
        return valor
