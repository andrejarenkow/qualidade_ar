import os
import time
import datetime
import requests
import numpy as np
import pandas as pd
import geopandas as gpd
import pygrib
import plotly.express as px
import plotly.graph_objs as go
from shapely.geometry import Polygon
from scipy.interpolate import griddata
import streamlit as st

# Configurar página do Streamlit
st.set_page_config(page_title="Qualidade do Ar RS", layout="wide")

st.title("Qualidade do Ar - RS (MP2.5)")

# Instruções iniciais
st.markdown("""
Este painel faz download automático das previsões do CAMS para MP2.5, gera mapas interpolados e categoriza municípios do RS.
""")

# Dados do usuário
with st.expander("Configuração da API Copernicus (CDSAPI)"):
    st.markdown('Você precisa de uma chave Copernicus válida. [Veja como obter](https://cds.climate.copernicus.eu/api-how-to)')
    cdsapi_key = 'be81bb34-c773-4711-8896-c15bccc82a33'#st.text_input("Chave da API Copernicus (formato: <uid>:<api_key>)", type='password')
    baixar = st.button("Baixar e processar dado mais recente")

# Caminhos
geojson_path = 'https://raw.githubusercontent.com/andrejarenkow/geodata/main/municipios_rs_CRS/RS_Municipios_2021.json'
url_geojson_crs = "https://raw.githubusercontent.com/andrejarenkow/geodata/main/RS_por_CRS/RS_por_CRS.json"
download_path = 'download.grib'

def salvar_cdsapirc(key):
    # Salva o arquivo .cdsapirc na home do usuário
    home = os.path.expanduser("~")
    cdsapirc_path = os.path.join(home, ".cdsapirc")
    content = f"url: https://ads.atmosphere.copernicus.eu/api\nkey: {key}\n"
    with open(cdsapirc_path, "w") as file:
        file.write(content)
    return cdsapirc_path

def baixar_dado_cds(date_str, key):
    import cdsapi
    salvar_cdsapirc(key)
    dataset = "cams-global-atmospheric-composition-forecasts"
    request = {
        'variable': ['particulate_matter_2.5um'],
        'date': [date_str],
        'time': ['00:00'],
        'leadtime_hour': ['0', '12', '24', '36', '48', '60', '72', '84', '96', '108', '120'],
        "type": ["forecast"],
        'area': [-27, -58, -34, -49],
        'data_format': 'grib',
    }
    client = cdsapi.Client()
    response = client.retrieve(dataset, request)
    response.download(download_path)
    time.sleep(3)
    return os.path.exists(download_path)

@st.cache_data(show_spinner="Carregando dados dos municípios...")
def carregar_municipios():
    return gpd.read_file(geojson_path)

@st.cache_data(show_spinner="Carregando CRS geodata...")
def carregar_geojson_crs():
    geojson_data_crs = requests.get(url_geojson_crs).json()
    return geojson_data_crs

def gerar_mapa(item, file_path_grib, gdf_municipios, geojson_data_crs):
    grbs = pygrib.open(file_path_grib)
    grb = grbs[item]
    horas_na_frente = int(grb.stepRange)
    dia_previsao_inicial = grb.dataDate
    dia_final = str(int(dia_previsao_inicial + horas_na_frente/24))
    ano = dia_final[:4]
    mes = dia_final[4:6]
    dia = dia_final[6:]
    data_formatada = f"{dia}/{mes}/{ano}"

    lat, lon = grb.latlons()
    values = grb.values * 1e+9

    data = {
        'lat': lat.flatten(),
        'lon': lon.flatten(),
        'value': values.flatten()
    }
    df = pd.DataFrame(data)

    # print o dataframe
    df

    # Interpolação
    grid_lat = np.linspace(df['lat'].min(), df['lat'].max(), 100)
    grid_lon = np.linspace(df['lon'].min(), df['lon'].max(), 100)
    grid_lon, grid_lat = np.meshgrid(grid_lon, grid_lat)
    grid_value = griddata((df['lat'], df['lon']), df['value'], (grid_lat, grid_lon), method='cubic')

    # Criar curvas de contorno e extrair polígonos
    import matplotlib.pyplot as plt

    # Criar curvas de contorno
    contours = plt.contour(grid_lon, grid_lat, grid_value, levels=200)

    # Extrair polígonos das curvas de contorno
    polygons = []
    values = []
    
    # Check if 'collections' attribute exists before accessing it
    if hasattr(contours, 'collections'):
        for i, collection in enumerate(contours.collections):
            for path in collection.get_paths():
                # Verifica se o número de vértices é suficiente para formar um polígono
                if len(path.vertices) >= 4:
                    polygons.append(Polygon(path.vertices))
                    values.append(contours.levels[i])  # Adiciona o valor correspondente ao nível
    elif hasattr(contours, 'allsegs'):
         for i, seg in enumerate(contours.allsegs):
            for path in seg:
                # Verifica se o número de vértices é suficiente para formar um polígono
                if len(path) >= 4:
                    polygons.append(Polygon(path))
                    values.append(contours.levels[i])  # Adiciona o valor correspondente ao nível
                
    gdf_contours = gpd.GeoDataFrame({'value': values, 'geometry': polygons})
    gdf_contours = gdf_contours.set_crs(gdf_municipios.crs)

    # Junção espacial
    gdf_joined = gpd.sjoin(gdf_municipios, gdf_contours, how='left', predicate='intersects')
    gdf_joined_limpo = gdf_joined.sort_values('value').dropna().drop_duplicates(subset='NM_MUN', keep='last')

    bins = [0, 15, 50, 75, 125, float('inf')]
    labels = ['Boa', 'Moderada', 'Ruim', 'Muito Ruim', 'Péssima']
    gdf_joined_limpo['Categoria'] = pd.cut(gdf_joined_limpo['value'], bins=bins, labels=labels, right=False)
    gdf_joined_limpo = gdf_joined_limpo.set_index('NM_MUN')
    gdf_joined_limpo.index.name = 'Município'
    gdf_joined_limpo['geometry'] = gdf_joined_limpo['geometry'].simplify(tolerance=0.001)

    # Definindo os limites e as cores
    limites = [0, 15, 50, 75, 125, 800]
    cores = ['#70E17B', '#FDD900', '#EE7D15', '#C90101', '#6C1775']

    map_fig = px.choropleth_mapbox(
        gdf_joined_limpo,
        geojson=gdf_joined_limpo.geometry,
        locations=gdf_joined_limpo.index,
        color='value',
        center={'lat': -30.45235, 'lon': -53.5532},
        zoom=5.5,
        mapbox_style="open-street-map",
        width=900,
        height=750,
        color_continuous_scale=cores,
        range_color=[0, 125],
        title=f'Concentração de Material Particulado 2.5µm em {data_formatada}, RS'
    )
    map_fig.update_layout(paper_bgcolor='rgba(0,0,0,0)', margin=go.layout.Margin(l=10, r=10, t=50, b=10))
    map_fig.update_traces(marker_line_width=0.2)
    map_fig.update_coloraxes(
        colorbar=dict(
            title='MP 2.5 (μg/m³)',
            tickvals=limites,
            ticktext=[f'{limite}' for limite in limites],
            orientation='h'
        ),
        colorbar_yanchor='bottom',
        colorbar_y=-0.13
    )
    # Camada adicional
    map_fig.update_layout(mapbox_layers=[
        dict(
            sourcetype = 'geojson',
            source = geojson_data_crs,
            type = 'line',
            color = 'black',
            line = dict(width=1.5)
        )
    ])
    return map_fig, gdf_joined_limpo

# Painel principal
gdf_municipios = carregar_municipios()
geojson_data_crs = carregar_geojson_crs()

# Data "hoje" padrão
hoje = datetime.date.today()
hoje_str = hoje.strftime('%Y-%m-%d')

if baixar and cdsapi_key:
    st.info("Baixando dados do CAMS. Isso pode levar alguns minutos...")
    ok = baixar_dado_cds(hoje_str, cdsapi_key)
    if ok:
        st.success("Arquivo baixado com sucesso!")
    else:
        st.error("Erro ao baixar arquivo. Verifique a chave da API.")
else:
    if not os.path.exists(download_path):
        st.warning("Faça o download do dado mais recente usando sua chave da API Copernicus.")

if os.path.exists(download_path):
    with st.expander("Seleção de previsão"):
        st.markdown("Selecione o horário da previsão (cada passo são 12h à frente da data base):")
        steps = list(range(1, 12))  # 11 passos = 0h a 120h
        step_idx = st.slider("Passo (leadtime)", min_value=1, max_value=11, value=1)
        st.caption(f"Passo {step_idx}: previsão para {step_idx*12}h à frente de {hoje_str}")

    map_fig, df_categorias = gerar_mapa(step_idx, download_path, gdf_municipios, geojson_data_crs)

    st.plotly_chart(map_fig, use_container_width=True)

    with st.expander("Tabela de municípios e categorias"):
        st.dataframe(df_categorias[['Categoria', 'value']], use_container_width=True)
else:
    st.info("Nenhum dado GRIB disponível. Faça o download usando sua chave Copernicus acima.")
