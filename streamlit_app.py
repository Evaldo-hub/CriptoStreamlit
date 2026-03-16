import streamlit as st
import pandas as pd
import numpy as np
import ccxt
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import ta # Technical Analysis Library: pip install ta

# Configuração da Página
st.set_page_config(
    page_title="Crypto Quant Scanner v4.2",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilização CSS Moderna para Dashboard Institucional
st.markdown("""
    <style>
    .metric-box {
        background-color: #1E2329;
        padding: 20px;
        border-radius: 10px;
        border: 1px solid #2B3139;
        color: white;
    }
    .buy-color { color: #0ECB81; }
    .sell-color { color: #F6465D; }
    </style>
""", unsafe_allow_html=True)

st.title("🤖 Dashboard Analítico Quantitativo Institucional")
st.markdown("Sistema de varredura 4H - Identificação de Padrões e Acumulação Institucional")

# Sidebar
st.sidebar.header("⚙️ Parâmetros do Scanner")
tickers_input = st.sidebar.text_input("Ativos Binance (separados por vírgula)", "BTC/USDT, DEXE/USDT,ETH/USDT, SOL/USDT, AVAX/USDT, INJ/USDT")
timeframe = st.sidebar.selectbox("Timeframe", ["1h", "4h", "1d"], index=1, help="Timeframes da Binance (Recomendado 4h)")
ma_period = st.sidebar.number_input("Período EMA Principal", 10, 200, 56)

@st.cache_data(ttl=300)
def load_data(ticker, interval="4h", limit=200):
    try:
        exchange = ccxt.binance({'enableRateLimit': True})
        bars = exchange.fetch_ohlcv(ticker, timeframe=interval, limit=limit)
        if not bars:
            return None
        df = pd.DataFrame(bars, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
        # A Binance retona UTC. Convertendo para GMT-3 (Horário de Brasília)
        df['Timestamp'] = df['Timestamp'].dt.tz_localize('UTC').dt.tz_convert('America/Sao_Paulo')
        df.set_index('Timestamp', inplace=True)
        return df
    except Exception as e:
        return None

def apply_indicators(df):
    # RSI
    df['RSI'] = ta.momentum.RSIIndicator(df['Close'], window=14).rsi()
    
    # Stochastic RSI
    stoch_rsi = ta.momentum.StochRSIIndicator(df['Close'], window=14, smooth1=3, smooth2=3)
    df['Stoch_RSI_K'] = stoch_rsi.stochrsi_k() * 100
    df['Stoch_RSI_D'] = stoch_rsi.stochrsi_d() * 100
    
    # MACD
    macd = ta.trend.MACD(df['Close'], window_slow=26, window_fast=12, window_sign=9)
    df['MACD'] = macd.macd()
    df['MACD_Signal'] = macd.macd_signal()
    df['MACD_Hist'] = macd.macd_diff()
    
    # EMA 56
    df['EMA_56'] = ta.trend.EMAIndicator(df['Close'], window=56).ema_indicator()
    
    # Média de Volume (20)
    df['Vol_SMA_20'] = df['Volume'].rolling(window=20).mean()
    
    # ATR para Stop Loss e Take Profit
    atr = ta.volatility.AverageTrueRange(df['High'], df['Low'], df['Close'], window=14)
    df['ATR'] = atr.average_true_range()
    
    return df

def calcular_score_sinal(row, current_price):
    score = 0
    sinal = "AGUARDAR"
    motivo = []
    
    # Tendência (20 pts)
    if current_price > row['EMA_56']:
        score += 20
        motivo.append("Preço acima da EMA 56")
    
    # RSI (15 pts)
    if 50 < row['RSI'] < 70:
        score += 15
        motivo.append("RSI em zona de força compradora")
    elif row['RSI'] < 30:
        score += 10 # Sobrevendido
        motivo.append("RSI Sobrevendido")
        
    # MACD (15 pts)
    if row['MACD'] > row['MACD_Signal']:
        score += 15
        motivo.append("MACD Cruzamento Positivo")
        
    # Volume (20 pts)
    if row['Volume'] > row['Vol_SMA_20']:
        score += 20
        motivo.append("Volume acima da média")
        
    # Vol Spike
    if row['Volume'] > (row['Vol_SMA_20'] * 2):
        score += 10
        motivo.append("🔥 VOLUME SPIKE DETECTADO")
        
    if score >= 60:
        sinal = "COMPRA"
    elif score <= 30 and current_price < row['EMA_56']:
        sinal = "VENDA"
        
    return score, sinal, ", ".join(motivo)

def verificar_anomalia_volume(ticker, exchange):
    # Puxa apenas as últimas velas de 5 minutos para identificar despejos/compras agressivas em tempo real
    try:
        bars = exchange.fetch_ohlcv(ticker, timeframe="5m", limit=12) # Última 1 hora fracionada em 5m
        if not bars:
            return None
        df_5m = pd.DataFrame(bars, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
        # A média de volume das velas recentes (excluindo a última que está rolando)
        media_vol = df_5m['Volume'].iloc[:-1].mean()
        vela_atual = df_5m.iloc[-1]
        
        # Se o volume da vela de 5m atual for MAIOR que 4x a média recente, tem treta (institucional/baleia)
        if media_vol > 0 and vela_atual['Volume'] > (media_vol * 4):
            variacao = ((vela_atual['Close'] - vela_atual['Open']) / vela_atual['Open']) * 100
            tipo = "DUMP 🩸" if variacao < 0 else "PUMP 🚀"
            return {
                "tipo": tipo,
                "variacao": f"{variacao:.2f}%",
                "multiplicador_vol": round(vela_atual['Volume'] / media_vol, 1)
            }
        return None
    except:
        return None

def verificar_manipulacao_baleias(df):
    if len(df) < 5:
        return None
        
    df_recent = df.tail(3)
    c1, c2, c3 = df_recent.iloc[0], df_recent.iloc[1], df_recent.iloc[2]
    
    vol_medio = df['Volume'].tail(20).mean()
    atr = df['ATR'].iloc[-1]
    padroes = []
    
    tamanho_c2 = abs(c2['Close'] - c2['Open'])
    tamanho_c3 = abs(c3['Close'] - c3['Open'])
    if (tamanho_c2 > atr * 1.5) and (tamanho_c3 > atr * 1.5):
        if (c2['Close'] > c2['Open'] and c3['Close'] < c3['Open']) or (c2['Close'] < c2['Open'] and c3['Close'] > c3['Open']):
            if c2['Volume'] > vol_medio * 1.5 and c3['Volume'] > vol_medio * 1.5:
                padroes.append({"tipo": "Whipsaw", "classe": "Possível manipulação de liquidez"})
                
    if c2['High'] > c1['High'] + (atr * 0.5):
        pavio_superior_c2 = c2['High'] - max(c2['Open'], c2['Close'])
        corpo_c2 = abs(c2['Close'] - c2['Open'])
        if pavio_superior_c2 > corpo_c2 * 1.5 and c2['Volume'] > vol_medio * 1.2:
            padroes.append({"tipo": "Stop Hunt (High)", "classe": "Stop Liquidity Grab"})
        elif c3['Close'] < c2['Low']:
             padroes.append({"tipo": "Fake Breakout", "classe": "Armadilha de compra (Bull Trap)"})
             
    if c2['Low'] < c1['Low'] - (atr * 0.5):
        pavio_inferior_c2 = min(c2['Open'], c2['Close']) - c2['Low']
        corpo_c2 = abs(c2['Close'] - c2['Open'])
        if pavio_inferior_c2 > corpo_c2 * 1.5 and c2['Volume'] > vol_medio * 1.2:
            padroes.append({"tipo": "Stop Hunt (Low)", "classe": "Stop Liquidity Grab"})
        elif c3['Close'] > c2['High']:
             padroes.append({"tipo": "Fake Breakdown", "classe": "Armadilha de venda (Bear Trap)"})
             
    ultimos_5 = df.tail(5)
    volatilidade_baixa = ultimos_5['ATR'].iloc[-1] < df['ATR'].tail(20).mean()
    fundos_ascendentes = ultimos_5['Low'].is_monotonic_increasing
    rsi_subindo = ultimos_5['RSI'].iloc[-1] > ultimos_5['RSI'].iloc[0] and ultimos_5['RSI'].iloc[-1] < 60
    
    if volatilidade_baixa and fundos_ascendentes and rsi_subindo and sum([1 for v in ultimos_5['Volume'] if v > vol_medio]) >= 2:
        padroes.append({"tipo": "Acumulação Silenciosa", "classe": "Acumulação Institucional"})

    return padroes if len(padroes) > 0 else None

def verificar_smart_money(df):
    """
    Avalia a presença de dinheiro institucional (Smart Money) com base na estrutura, volume e ímpeto.
    Retorna o Status e a Confiabilidade.
    """
    if len(df) < 20:
        return "Nenhum", "Baixa"
        
    df_recent = df.tail(10)
    ultimas_3 = df.tail(3)
    c_atual = df.iloc[-1]
    
    vol_medio_20 = df['Volume'].tail(20).mean()
    
    # --- 1. SMART MONEY ENTRY ---
    # volume > 3x media, candles verdes consecutivos, correções pequenas, RSI subindo gradualmente
    vol_explosivo = c_atual['Volume'] > (vol_medio_20 * 3)
    candles_verdes = all(c['Close'] > c['Open'] for _, c in ultimas_3.iterrows())
    rsi_subindo = df['RSI'].iloc[-1] > df['RSI'].iloc[-3]
    fechamento_forte = c_atual['Close'] > c_atual['High'] - (c_atual['ATR'] * 0.2)
    
    if vol_explosivo and candles_verdes and rsi_subindo:
        confiabilidade = "Alta" if fechamento_forte else "Moderada"
        return "Entrada Institucional", confiabilidade
        
    # --- 2. INSTITUTIONAL ACCUMULATION ZONE ---
    # lateralização, volume crescente nos dias de alta, rejeições de queda (pavios inferiores), MACD virando
    lateral = abs(df['Close'].iloc[-10] - c_atual['Close']) / df['Close'].iloc[-10] < 0.05
    pavios_inferiores = sum([1 for _, c in df_recent.iterrows() if (min(c['Open'], c['Close']) - c['Low']) > (c['ATR'] * 0.5)]) >= 3
    macd_virando = df['MACD_Hist'].iloc[-1] > df['MACD_Hist'].iloc[-2] and df['MACD'].iloc[-1] < 0
    vol_crescente = df['Volume'].iloc[-1] > vol_medio_20
    
    if lateral and pavios_inferiores and macd_virando:
        confiabilidade = "Alta" if vol_crescente else "Moderada"
        return "Zona de Acumulação", confiabilidade
        
    # --- 3. DISTRIBUIÇÃO INSTITUCIONAL ---
    # topo, volume alto na venda, divergência de baixa do rsi (preço sobe/lateral, rsi cai), falha num rompimento
    topo_recente = df['High'].rolling(20).max().iloc[-1]
    perto_topo = c_atual['Close'] > topo_recente * 0.95
    candle_venda_forte = c_atual['Close'] < c_atual['Open'] and c_atual['Volume'] > vol_medio_20 * 1.5
    rsi_caindo = df['RSI'].iloc[-1] < df['RSI'].iloc[-5] and df['Close'].iloc[-1] >= df['Close'].iloc[-5]
    
    if perto_topo and candle_venda_forte and rsi_caindo:
        confiabilidade = "Alta" if c_atual['Volume'] > vol_medio_20 * 2.5 else "Moderada"
        return "Distribuição Institucional", confiabilidade
        
    return "Nenhum", "Baixa"

def verificar_altcoin_early_stage(df, ticker, sm_status):
    if len(df) < 50: return None
    
    ultimos_10 = df.tail(10)
    c_atual = df.iloc[-1]
    vol_medio_20 = df['Volume'].tail(20).mean()
    atr_atual = df['ATR'].iloc[-1]
    
    # 1. EARLY ACCUMULATION (Sinais)
    vol_crescente = ultimos_10['Volume'].mean() > df['Volume'].iloc[-30:-10].mean()
    preco_lateral = max(df['Close'].tail(20)) / min(df['Close'].tail(20)) < 1.15
    rsi_idx = df['RSI'].iloc[-1]
    rsi_valido = 45 < rsi_idx < 60 and rsi_idx > df['RSI'].iloc[-5]
    fundos_ascendentes = df['Low'].iloc[-1] > df['Low'].iloc[-5] and df['Low'].iloc[-5] > df['Low'].iloc[-10]
    compressao_volatilidade = atr_atual < df['ATR'].tail(20).mean()
    
    # Tamanho médio dos corpos
    velas_verdes = ultimos_10[ultimos_10['Close'] > ultimos_10['Open']]
    velas_vermelhas = ultimos_10[ultimos_10['Close'] < ultimos_10['Open']]
    corpo_verde = (velas_verdes['Close'] - velas_verdes['Open']).mean() if len(velas_verdes) > 0 else 0
    corpo_vermelho = (velas_vermelhas['Open'] - velas_vermelhas['Close']).mean() if len(velas_vermelhas) > 0 else 0
    candles_compra_maiores = corpo_verde > corpo_vermelho
    
    macd_aproximando = df['MACD_Hist'].iloc[-1] < 0 and df['MACD_Hist'].iloc[-1] > df['MACD_Hist'].iloc[-3]
    rejeicoes_queda = sum([1 for _, c in ultimos_10.iterrows() if (min(c['Open'], c['Close']) - c['Low']) > (c['ATR'] * 0.5)]) >= 2
    
    early_criteria = sum([vol_crescente, preco_lateral, rsi_valido, fundos_ascendentes, compressao_volatilidade, candles_compra_maiores, macd_aproximando, rejeicoes_queda])
    
    # Narrativas em alta e Setores
    narrativas = {
        "AI": ["FET", "AGIX", "RNDR", "TAO"],
        "RWA": ["ONDO", "POLYX", "MKR"],
        "LAYER2": ["ARB", "OP", "IMX", "MATIC"],
        "MODULAR": ["TIA", "SEI", "DYM"],
        "GAMING": ["IMX", "GALA", "PIXEL"],
        "LAYER1": ["SOL", "AVAX", "INJ"],
        "DEPIN": ["HNT", "RNDR", "AR"],
        "RESTAKING": ["ETHFI", "ALT"]
    }
    
    ativo_base = ticker.replace("/USDT", "")
    
    def detectar_narrativa(token):
        for setor, moedas in narrativas.items():
            if token in moedas:
                return setor
        return "OUTROS"
        
    def calcular_score_narrativa(token):
        nar = detectar_narrativa(token)
        if nar in ["AI", "RWA", "DEPIN"]: return 20
        if nar in ["LAYER2", "MODULAR", "RESTAKING"]: return 15
        if nar in ["GAMING", "LAYER1"]: return 10
        return 0
        
    setor_narrativa = detectar_narrativa(ativo_base)
    nar_score = calcular_score_narrativa(ativo_base)
    
    # Score Volume (20)
    vol_mult = c_atual['Volume'] / vol_medio_20
    vol_score = 20 if vol_mult > 3 else 15 if vol_mult > 1.5 else 10 if vol_mult > 1 else 5
    vol_class = "Explosivo" if vol_mult > 3 else "Alto" if vol_mult > 1.5 else "Médio" if vol_mult > 1 else "Baixo"
    
    # Score Estrutura (20)
    rompimento = c_atual['Close'] > df['High'].tail(20).max() * 0.99
    est_score = 20 if rompimento else 15 if compressao_volatilidade else 10 if preco_lateral else 5
    est_class = "Breakout" if rompimento else "Compressão" if compressao_volatilidade else "Lateral"
    
    # Score Institucional (20)
    inst_score = 20 if "Entrada" in sm_status else 15 if "Acumulação" in sm_status else 10 if sm_status == "Nenhum" else 0
    
    # Score Momentum (20)
    mom_score = 20 if c_atual['MACD'] > c_atual['MACD_Signal'] and rsi_idx > 50 else 10 if rsi_idx > 40 else 5
    
    total_score = vol_score + est_score + inst_score + mom_score + nar_score
    
    # Fase do Ciclo
    if total_score >= 80: fase_ciclo = "TENDÊNCIA FORTE / BREAKOUT"
    elif total_score >= 60: fase_ciclo = "PRÉ-BREAKOUT"
    elif total_score >= 40: fase_ciclo = "ACUMULAÇÃO AVANÇADA"
    else: fase_ciclo = "ACUMULAÇÃO"
    
    # Status Final
    if total_score >= 80: status_final = "Explosão Iminente"
    elif early_criteria >= 5 and total_score >= 60: status_final = "Early Gem (Pré-Pump)"
    elif early_criteria >= 5: status_final = "Early Gem"
    elif total_score >= 60: status_final = "Pré-Pump"
    elif total_score >= 40: status_final = "Acumulação"
    else: status_final = "Normal"
    
    # Potencial
    potencial = "300–1000%" if total_score >= 80 else "100–300%" if total_score >= 60 else "50–100%" if total_score >= 40 else "< 50%"
    
    # Observação
    motivos_obs = []
    if early_criteria >= 5: motivos_obs.append(f"Encontrou {early_criteria}/8 sinais Ocultos.")
    if setor_narrativa != "OUTROS": motivos_obs.append(f"Ativo forte da Narrativa {setor_narrativa}.")
    if rompimento: motivos_obs.append("Rompendo barreira crítica.")
    
    obs = " ".join(motivos_obs) if motivos_obs else "Ação de preço pacata."
    
    return {
        "Fase_Ciclo": fase_ciclo,
        "Status_Alvo": status_final,
        "Score_Explosivo": min(total_score, 100), # Cap in 100
        "Vol_Class": vol_class,
        "Estrutura_Class": est_class,
        "Setor": setor_narrativa,
        "Potencial": potencial,
        "Observacao_Gem": obs
    }

tickers = [t.strip() for t in tickers_input.split(',')]

col1, col2, col3 = st.columns(3)

import os

if 'analise_iniciada' not in st.session_state:
    
    # No Render inicia automaticamente
    if os.getenv("RENDER"):
        st.session_state.analise_iniciada = True
    else:
        st.session_state.analise_iniciada = False

if st.sidebar.button("🚀 Iniciar Análise Global"):
    st.session_state.analise_iniciada = True

if st.session_state.analise_iniciada:
    with st.spinner("Conectando aos provedores de liquidez e analisando dados..."):
        resultados = []
        gemas_detectadas = []
        
        for ticker in tickers:
            exchange = ccxt.binance({'enableRateLimit': True})
            df = load_data(ticker, interval=timeframe)
            if df is not None and len(df) > 60:
                df = apply_indicators(df)
                last_row = df.iloc[-1]
                current_price = last_row['Close']
                
                score, sinal, motivo = calcular_score_sinal(last_row, current_price)
                
                anomalia = verificar_anomalia_volume(ticker, exchange)
                baleias = verificar_manipulacao_baleias(df)
                sm_status, sm_confiabilidade = verificar_smart_money(df)
                early_gem_data = verificar_altcoin_early_stage(df, ticker, sm_status)
                
                resultados.append({
                    "Ativo": ticker.replace("/USDT", ""),
                    "Preço": current_price,
                    "Score": score,
                    "Sinal": sinal,
                    "Smart Money": sm_status,
                    "Confiabilidade": sm_confiabilidade,
                    "RSI": round(last_row['RSI'], 1),
                    "Status": "Normal" if score < 70 else "Breakout Provável",
                    "Observação": motivo,
                    "Anomalia_5m": anomalia,
                    "Manipulacao_Baleia": baleias
                })
                
                if early_gem_data and int(early_gem_data["Score_Explosivo"]) >= 60:
                    gemas_detectadas.append({
                        "Ativo": ticker.replace("/USDT", ""),
                        "Preço": current_price,
                        "Smart Money": sm_status,
                        "Early_Gem": early_gem_data
                    })
        
        if resultados:
            df_res = pd.DataFrame(resultados).sort_values(by="Score", ascending=False)
            
            # --- Alertas de Radar de Alta Frequência (Baleias/Dumps) ---
            alertas_baleia = [r for r in resultados if r["Anomalia_5m"] is not None]
            if alertas_baleia:
                st.markdown("<h3 style='color: #F6465D;'>🚨 RADAR DE ALTA FREQUÊNCIA: MOVIMENTO INSTITUCIONAL DETECTADO AGORA!</h3>", unsafe_allow_html=True)
                cols_alert = st.columns(len(alertas_baleia))
                for idx, alerta in enumerate(alertas_baleia):
                    dados_anomalia = alerta["Anomalia_5m"]
                    cor = "#F6465D" if "DUMP" in dados_anomalia['tipo'] else "#0ECB81"
                    cols_alert[idx].markdown(f"""
                    <div style="background-color: rgba(246, 70, 93, 0.1); border-left: 5px solid {cor}; padding: 15px; margin-bottom: 20px;">
                        <h4 style="margin:0; color:{cor};">{alerta['Ativo']} - {dados_anomalia['tipo']}</h4>
                        <p style="margin:5px 0 0 0;">Volume <b>{dados_anomalia['multiplicador_vol']}x maior</b> que a média nos últimos 5 minutos!</p>
                        <p style="margin:0;">Variação rápida: <b>{dados_anomalia['variacao']}</b></p>
                    </div>
                    """, unsafe_allow_html=True)
            
            # --- Alertas de Manipulação Restrita (Traps / Whipsaws / Silent Accumulation) ---
            alertas_manipulacao = [r for r in resultados if r.get("Manipulacao_Baleia") is not None]
            if alertas_manipulacao:
                st.markdown("<h3 style='color: #FFB020;'>🐋 DETECTOR DE MANIPULAÇÃO DE BALEIAS ATIVADO</h3>", unsafe_allow_html=True)
                cols_manip = st.columns(min(len(alertas_manipulacao), 3))
                for idx, alerta in enumerate(alertas_manipulacao):
                    col_target = cols_manip[idx % 3]
                    for manip in alerta["Manipulacao_Baleia"]:
                        cor = "#FFB020" if "Acum" not in manip['tipo'] else "#0ECB81"
                        icon = "🤫" if "Acum" in manip['tipo'] else "🪤" 
                        col_target.markdown(f"""
                        <div style="background-color: rgba(255, 176, 32, 0.1); border-left: 5px solid {cor}; padding: 15px; margin-bottom: 20px;">
                            <h4 style="margin:0; color:{cor};">{icon} {alerta['Ativo']} - {manip['tipo']}</h4>
                            <p style="margin:5px 0 0 0;">Classificação: <b>{manip['classe']}</b></p>
                            <p style="margin:0; font-size:12px; color:#888;">Padrão detectado no timeframe primário.</p>
                        </div>
                        """, unsafe_allow_html=True)
            
            # --- Alertas do Early Stage Altcoin Detector (GEMAS) ---
            if gemas_detectadas:
                st.markdown("<h3 style='color: #8A2BE2;'>💎 DETECTOR DE GEMAS: EARLY STAGE ALTCOINS</h3>", unsafe_allow_html=True)
                cols_gem = st.columns(min(len(gemas_detectadas), 3))
                for idx, gema in enumerate(gemas_detectadas):
                    dados_gem = gema["Early_Gem"]
                    cor_gem = "#FFD700" if dados_gem['Score_Explosivo'] >= 80 else "#8A2BE2"
                    pulse = "class='metric-box'" if dados_gem['Score_Explosivo'] >= 80 else ""
                    
                    cols_gem[idx % 3].markdown(f"""
                    <div style="background-color: rgba(138, 43, 226, 0.1); border: 1px solid {cor_gem}; padding: 20px; border-radius: 10px; margin-bottom: 20px;">
                        <h4 style="color: #bbb; margin:0; font-size: 12px;">NARRATIVA: {dados_gem.get('Setor', 'OUTROS')}</h4>
                        <h3 style="color: {cor_gem}; margin-top:5px;">{gema['Ativo']} - <span style="font-size: 16px;">{dados_gem['Status_Alvo']}</span></h3>
                        <p style="font-size: 20px; font-weight: bold; margin:0;">${gema['Preço']} | Score: {dados_gem['Score_Explosivo']}/100</p>
                        <hr style="border-color: #2B3139;">
                        <b>FASE DO CICLO:</b> {dados_gem['Fase_Ciclo']}<br>
                        <b>VOLUME:</b> {dados_gem['Vol_Class']} | <b>ESTRUTURA:</b> {dados_gem['Estrutura_Class']}<br>
                        <b>SMART MONEY:</b> {gema['Smart Money']}<br>
                        <h4 style="color:#0ECB81; margin: 10px 0 5px 0;">🎯 POTENCIAL ESTIMADO: {dados_gem['Potencial']}</h4>
                        <p style="color:#888; font-size:14px; margin:0;"><b>OBS:</b> {dados_gem['Observacao_Gem']}</p>
                    </div>
                    """, unsafe_allow_html=True)
            
            # Top Metrics
            top_coin = df_res.iloc[0]
            col1.markdown(f"""
            <div class="metric-box">
                <h3 style="margin:0; color:#888;">Top Oportunidade</h3>
                <h2 style="margin:0; color:#0ECB81;">{top_coin['Ativo']}</h2>
                <p style="margin:0;">Score: {top_coin['Score']}/100</p>
            </div>
            """, unsafe_allow_html=True)
            
            # Mostrar Tabela de Scanner
            st.subheader("📊 Resultados do Scanner Ativo")
            
            def map_color(val):
                if val == "COMPRA": return 'background-color: rgba(14, 203, 129, 0.2); color: #0ECB81;'
                if val == "VENDA": return 'background-color: rgba(246, 70, 93, 0.2); color: #F6465D;'
                return ''
            
            # Remover colunas internas da visualização e aplicar estilo dinâmico
            colunas_remover = ['Anomalia_5m', 'Manipulacao_Baleia', 'Early_Gem']
            df_display = df_res.drop(columns=[col for col in colunas_remover if col in df_res.columns])
            
            def map_color_smart_money(val):
                if "Entrada" in str(val) or "Acumulação" in str(val): return 'color: #0ECB81; font-weight: bold;'
                if "Distribuição" in str(val): return 'color: #F6465D; font-weight: bold;'
                return ''
                
            def map_color_confiabilidade(val):
                if val == "Alta": return 'color: #0ECB81;'
                if val == "Moderada": return 'color: #FFB020;'
                if val == "Baixa": return 'color: #888888;'
                return ''
                
            styled_df = df_display.style.map(map_color, subset=['Sinal'])\
                                        .map(map_color_smart_money, subset=['Smart Money'])\
                                        .map(map_color_confiabilidade, subset=['Confiabilidade'])
                                        
            st.dataframe(styled_df, use_container_width=True)
            
            # Gráfico de Análise Dinâmico
            st.subheader(f"📈 Gráfico Analítico Avançado: {top_coin['Ativo']}")
            
            df_top = load_data(top_coin['Ativo']+"/USDT", interval=timeframe)
            df_top = apply_indicators(df_top)
            df_plot = df_top.tail(100) # Últimas 100 velas
            
            fig = make_subplots(rows=3, cols=1, shared_xaxes=True, 
                                vertical_spacing=0.05, row_heights=[0.6, 0.2, 0.2])

            # Preço e EMA
            fig.add_trace(go.Candlestick(x=df_plot.index, open=df_plot['Open'], high=df_plot['High'],
                                         low=df_plot['Low'], close=df_plot['Close'], name='Preço'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['EMA_56'], line=dict(color='orange', width=2), name='EMA 56'), row=1, col=1)
            
            # --- Adicionar Stop Loss e Take Profit ---
            ultimo_fechamento = df_plot['Close'].iloc[-1]
            ultimo_atr = df_plot['ATR'].iloc[-1]
            
            if top_coin['Sinal'] == 'COMPRA':
                sl = ultimo_fechamento - (ultimo_atr * 1.5)
                tp1 = ultimo_fechamento + (ultimo_atr * 2.0)
                tp2 = ultimo_fechamento + (ultimo_atr * 4.0)
                fig.add_hline(y=sl, line_dash="dot", line_color="#F6465D", annotation_text="Stop Loss (1.5x ATR)", row=1, col=1)
                fig.add_hline(y=tp1, line_dash="dot", line_color="#0ECB81", annotation_text="Take Profit 1 (2x ATR)", row=1, col=1)
                fig.add_hline(y=tp2, line_dash="dot", line_color="#0ECB81", annotation_text="Take Profit 2 (4x ATR)", row=1, col=1)
            elif top_coin['Sinal'] == 'VENDA':
                sl = ultimo_fechamento + (ultimo_atr * 1.5)
                tp1 = ultimo_fechamento - (ultimo_atr * 2.0)
                tp2 = ultimo_fechamento - (ultimo_atr * 4.0)
                fig.add_hline(y=sl, line_dash="dot", line_color="#F6465D", annotation_text="Stop Loss (1.5x ATR)", row=1, col=1)
                fig.add_hline(y=tp1, line_dash="dot", line_color="#0ECB81", annotation_text="Take Profit 1 (2x ATR)", row=1, col=1)
                fig.add_hline(y=tp2, line_dash="dot", line_color="#0ECB81", annotation_text="Take Profit 2 (4x ATR)", row=1, col=1)

            # Volume
            colors = ['#0ECB81' if row['Close'] >= row['Open'] else '#F6465D' for index, row in df_plot.iterrows()]
            fig.add_trace(go.Bar(x=df_plot.index, y=df_plot['Volume'], marker_color=colors, name='Volume'), row=2, col=1)
            fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['Vol_SMA_20'], line=dict(color='yellow', width=1), name='Média Vol'), row=2, col=1)

            # MACD
            colors_macd = ['#0ECB81' if val >= 0 else '#F6465D' for val in df_plot['MACD_Hist']]
            fig.add_trace(go.Bar(x=df_plot.index, y=df_plot['MACD_Hist'], marker_color=colors_macd, name='MACD Hist'), row=3, col=1)
            fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MACD'], line=dict(color='blue', width=1), name='MACD'), row=3, col=1)
            fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['MACD_Signal'], line=dict(color='orange', width=1), name='Signal'), row=3, col=1)

            fig.update_layout(height=800, plot_bgcolor='#1E2329', paper_bgcolor='#1E2329', 
                              font=dict(color='white'), margin=dict(l=20, r=20, t=20, b=20),
                              xaxis_rangeslider_visible=False)
            fig.update_yaxes(gridcolor='#2B3139')
            fig.update_xaxes(gridcolor='#2B3139')
            
            st.plotly_chart(fig, use_container_width=True)
            
            # --- NOVO: Histórico de Sinais ---
            st.markdown("---")
            st.subheader("🕒 Histórico Analítico por Ativo")
            
            # Dropdown para selecionar a cripto desejada
            ativos_disponiveis = df_res['Ativo'].tolist()
            ativo_selecionado = st.selectbox("Selecione a Cripto para ver o Histórico:", ativos_disponiveis)
            
            # Carregar dados da cripto selecionada (se não for a top_coin que já está na RAM)
            if ativo_selecionado == top_coin['Ativo']:
                df_history_plot = df_plot # Já carregado para o gŕafico acima
            else:
                with st.spinner(f"Carregando histórico de {ativo_selecionado}..."):
                    df_history_raw = load_data(ativo_selecionado+"/USDT", interval=timeframe)
                    df_history_plot = apply_indicators(df_history_raw)
            
            history_data = []
            
            # Pegar as últimas 20 velas para o relatório de tempo/hora
            for timestamp, row in df_history_plot.tail(20).iterrows():
                h_score, h_sinal, h_motivo = calcular_score_sinal(row, row['Close'])
                history_data.append({
                    "Data/Hora": timestamp.strftime("%Y-%m-%d %H:%M"),
                    "Preço (USDT)": f"${row['Close']:.2f}",
                    "Sinal": h_sinal,
                    "Score": h_score,
                    "Critérios Alcançados": h_motivo
                })
                
            history_df = pd.DataFrame(history_data)
            # Inverter para mostrar a vela mais recente no topo
            history_df = history_df.iloc[::-1].reset_index(drop=True)
            
            st.dataframe(history_df.style.map(map_color, subset=['Sinal']), use_container_width=True)
            
import time

time.sleep(60)
st.rerun()

st.markdown("---")
st.markdown("🟢 **Status**: Conectado à API oficial da Binance via ccxt. Dados sendo extraídos em tempo real.")
