# -*- coding: utf-8 -*-

# =============================================================================
# APLICAÇÃO COMPLETA v7: MÉTRICAS DO BANCO DE DADOS E BUSCA AVANÇADA
# =============================================================================

import os
import requests
import json
import re
import time
import random
import threading
from flask import Flask, request, jsonify, render_template_string, Response
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text, inspect

# Carrega as variáveis de ambiente do arquivo .env para testes locais
load_dotenv()

# --- Configuração do Banco de Dados ---
app = Flask(__name__)
CORS(app)
db_url = os.getenv('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Modelos das Tabelas do Banco de Dados ---
class Cadastro(db.Model):
    __tablename__ = 'cadastros'
    id = db.Column(db.Integer, primary_key=True)
    telefone = db.Column(db.String(30), unique=True, nullable=False)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

class Mensagem(db.Model):
    __tablename__ = 'mensagens'
    id = db.Column(db.Integer, primary_key=True)
    telefone = db.Column(db.String(30), nullable=False)
    nome = db.Column(db.String(100), nullable=True)
    texto = db.Column(db.Text, nullable=False)
    media_id = db.Column(db.String(255), nullable=True)
    media_type = db.Column(db.String(50), nullable=True)
    data_recebimento = db.Column(db.DateTime, default=datetime.utcnow)

# --- Cria as tabelas se não existirem ---
with app.app_context():
    db.create_all()

# --- Credenciais e Variáveis Globais ---
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")

db_participantes_sorteio = {}
db_reclamacoes = []
reclamacao_id_counter = 1
disparo_status = {"ativo": False, "progresso": 0, "total": 0, "log": []}

# --- Lógica Principal ---

def salvar_no_banco(telefone, nome, texto_mensagem, media_id, media_type):
    with app.app_context():
        try:
            if not Cadastro.query.filter_by(telefone=telefone).first():
                db.session.add(Cadastro(telefone=telefone))
            
            nova_mensagem_db = Mensagem(
                telefone=telefone, nome=nome, texto=texto_mensagem,
                media_id=media_id, media_type=media_type
            )
            db.session.add(nova_mensagem_db)
            db.session.commit()
            print(f"✅ Dados de '{telefone}' salvos no banco de dados.")
        except Exception as e:
            print(f"❌ ERRO ao salvar no banco de dados: {e}")
            db.session.rollback()

def extrair_nome(texto):
    if not texto or not isinstance(texto, str): return None
    match = re.search(r"(?:meu nome é|chamo-me|sou o|sou a)\s+([A-Za-zÀ-ú\s]+)", texto, re.IGNORECASE)
    if match: return match.group(1).strip().title()
    partes = texto.split()
    if len(partes) >= 2 and partes[0].isalpha() and len(partes[0]) > 2:
        return f"{partes[0].title()} {partes[1].title() if partes[1].isalpha() else ''}".strip()
    return None

def adicionar_ao_sorteio(telefone, nome_extraido):
    if telefone not in db_participantes_sorteio:
        nome_final = nome_extraido or f"Participante ({telefone[-4:]})"
        db_participantes_sorteio[telefone] = {"nome": nome_final, "telefone": telefone}
        return True
    return False

def enviar_resposta_whatsapp(destinatario, mensagem):
    if not all([META_ACCESS_TOKEN, META_PHONE_NUMBER_ID]):
        disparo_status["log"].append(f"AVISO: Credenciais não configuradas. Simulando envio para {destinatario}")
        return False
    url = f"https://graph.facebook.com/v18.0/{META_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": destinatario, "text": {"body": mensagem}}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        print(f"Mensagem enviada para {destinatario}. Status: {response.status_code}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"ERRO ao enviar mensagem para {destinatario}: {e}")
        disparo_status["log"].append(f"ERRO ao enviar para {destinatario}: {e.response.text if e.response else 'Sem resposta'}")
        return False

def tarefa_disparo_massa(mensagens):
    global disparo_status
    with app.app_context():
        cadastros = Cadastro.query.all()
        numeros = [c.telefone for c in cadastros]
        random.shuffle(numeros)
        
        disparo_status["total"] = len(numeros)
        disparo_status["progresso"] = 0
        disparo_status["log"] = [f"Iniciando disparos para {len(numeros)} contatos..."]

        for i in range(0, len(numeros), 5):
            if not disparo_status["ativo"]:
                disparo_status["log"].append("Campanha interrompida pelo usuário.")
                break
            
            lote = numeros[i:i+5]
            for numero in lote:
                mensagem_aleatoria = random.choice(mensagens)
                if enviar_resposta_whatsapp(numero, mensagem_aleatoria):
                    disparo_status["log"].append(f"Sucesso no envio para ...{numero[-4:]}")
                else:
                    disparo_status["log"].append(f"Falha no envio para ...{numero[-4:]}")
                disparo_status["progresso"] += 1
                time.sleep(random.randint(2, 5))
            
            if i + 5 < len(numeros):
                intervalo = random.randint(180, 600)
                disparo_status["log"].append(f"Pausa de {intervalo//60} minutos antes do próximo lote.")
                time.sleep(intervalo)
    
    disparo_status["log"].append("Campanha finalizada.")
    disparo_status["ativo"] = False

def tarefa_limpeza_banco():
    with app.app_context():
        try:
            query = text("SELECT pg_database_size(current_database())")
            tamanho_bytes = db.session.execute(query).scalar()
            tamanho_mb = tamanho_bytes / (1024 * 1024)
            print(f"Tamanho atual do banco de dados: {tamanho_mb:.2f} MB")

            if tamanho_mb > 500:
                print("Iniciando rotina de limpeza de mensagens antigas...")
                mensagens_para_apagar = Mensagem.query.order_by(Mensagem.data_recebimento.asc()).limit(500).all()
                if mensagens_para_apagar:
                    for msg in mensagens_para_apagar:
                        db.session.delete(msg)
                    db.session.commit()
                    print("✅ 500 mensagens mais antigas foram apagadas.")
        except Exception as e:
            print(f"❌ ERRO durante a rotina de limpeza: {e}")

# --- Endpoints da API ---

@app.route('/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    if request.method == 'GET':
        if request.args.get('hub.verify_token') == META_VERIFY_TOKEN:
            return request.args.get('hub.challenge')
        return "Token de verificação inválido", 403

    if request.method == 'POST':
        data = request.json
        try:
            if 'entry' in data and data['entry'][0]['changes'][0]['value'].get('messages'):
                message_data = data['entry'][0]['changes'][0]['value']['messages'][0]
                remetente = message_data['from']
                message_type = message_data.get('type')
                
                mensagem_para_painel, nome_extraido, media_id = "", None, None

                if message_type == 'text':
                    mensagem_para_painel = message_data['text']['body']
                    nome_extraido = extrair_nome(mensagem_para_painel)
                elif message_type in ['image', 'video', 'document', 'audio']:
                    media_id = message_data[message_type]['id']
                    legenda = message_data[message_type].get('caption')
                    if legenda:
                        mensagem_para_painel = legenda
                        nome_extraido = extrair_nome(legenda)
                    else:
                        mensagem_para_painel = f"[{message_type.upper()} RECEBIDA]"
                
                nome_final = nome_extraido or f"Pessoa ({remetente[-4:]})"
                salvar_no_banco(remetente, nome_final, mensagem_para_painel, media_id, message_type)
                
                if adicionar_ao_sorteio(remetente, nome_extraido):
                    enviar_resposta_whatsapp(remetente, "Obrigado por sua mensagem! Você já está participando do nosso sorteio semanal. Boa sorte! 🤞")
                
                threading.Thread(target=tarefa_limpeza_banco).start()
        except (KeyError, IndexError) as e:
            print(f"Formato de notificação não esperado: {e}")
        return "OK", 200

@app.route('/iniciar_disparo', methods=['POST'])
def iniciar_disparo():
    global disparo_status
    if disparo_status["ativo"]:
        return jsonify({"status": "error", "message": "Uma campanha já está em andamento."}), 400
    data = request.json
    mensagens = [msg for msg in [data.get('msg1'), data.get('msg2'), data.get('msg3')] if msg and msg.strip()]
    if not mensagens:
        return jsonify({"status": "error", "message": "Forneça pelo menos uma mensagem."}), 400
    disparo_status["ativo"] = True
    threading.Thread(target=tarefa_disparo_massa, args=(mensagens,)).start()
    return jsonify({"status": "success", "message": "Campanha de disparo iniciada."})

@app.route('/status_disparo', methods=['GET'])
def get_status_disparo(): return jsonify(disparo_status)

@app.route('/parar_disparo', methods=['POST'])
def parar_disparo():
    global disparo_status
    if disparo_status["ativo"]:
        disparo_status["ativo"] = False
        return jsonify({"status": "success", "message": "Campanha será interrompida."})
    return jsonify({"status": "error", "message": "Nenhuma campanha ativa para parar."})

@app.route('/mensagens', methods=['GET'])
def get_mensagens():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    query = Mensagem.query
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(Mensagem.data_recebimento.between(start_date, end_date))
        except ValueError:
            pass # Ignora datas inválidas e usa o padrão
    else:
        three_days_ago = datetime.utcnow() - timedelta(days=3)
        query = query.filter(Mensagem.data_recebimento >= three_days_ago)

    mensagens_db = query.order_by(Mensagem.data_recebimento.desc()).limit(200).all()
    return jsonify([{
        "id": msg.id, "nome": msg.nome, "telefone": msg.telefone, "texto": msg.texto,
        "media_id": msg.media_id, "media_type": msg.media_type,
        "timestamp": msg.data_recebimento.isoformat()
    } for msg in mensagens_db])

@app.route('/stats', methods=['GET'])
def get_stats():
    with app.app_context():
        try:
            total_cadastros = db.session.query(Cadastro).count()
            query = text("SELECT pg_database_size(current_database())")
            tamanho_bytes = db.session.execute(query).scalar() or 0
            tamanho_mb = f"{tamanho_bytes / (1024 * 1024):.2f} MB"
            return jsonify({"total_cadastros": total_cadastros, "db_size": tamanho_mb})
        except Exception as e:
            print(f"Erro ao buscar stats: {e}")
            return jsonify({"total_cadastros": "N/A", "db_size": "N/A"})

@app.route('/promover_reclamacao', methods=['POST'])
def promover_reclamacao():
    global reclamacao_id_counter
    data = request.json
    mensagem_id = data.get('id')
    
    mensagem_a_promover = Mensagem.query.get(mensagem_id)
    if mensagem_a_promover:
        nova_reclamacao = {
            "id": reclamacao_id_counter, "nome": mensagem_a_promover.nome,
            "telefone": mensagem_a_promover.telefone, "texto": mensagem_a_promover.texto,
            "status": "Registrada", "media_id": mensagem_a_promover.media_id,
            "media_type": mensagem_a_promover.media_type,
            "timestamp": datetime.now().isoformat()
        }
        db_reclamacoes.append(nova_reclamacao)
        reclamacao_id_counter += 1
        
        db.session.delete(mensagem_a_promover)
        db.session.commit()
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Mensagem não encontrada no banco de dados"}), 404

@app.route('/media/<media_id>')
def get_media(media_id):
    if not META_ACCESS_TOKEN: return "Token de acesso não configurado", 500
    url_info = f"https://graph.facebook.com/v18.0/{media_id}/"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}"}
    try:
        info_response = requests.get(url_info, headers=headers)
        info_response.raise_for_status()
        media_url = info_response.json()['url']
        media_response = requests.get(media_url, headers=headers)
        media_response.raise_for_status()
        return Response(media_response.content, content_type=media_response.headers['Content-Type'])
    except requests.exceptions.RequestException as e:
        print(f"Erro ao buscar mídia {media_id}: {e}")
        return "Erro ao buscar mídia", 500

@app.route('/participantes', methods=['GET'])
def get_participantes(): return jsonify(list(db_participantes_sorteio.values()))

@app.route('/reclamacoes', methods=['GET'])
def get_reclamacoes(): return jsonify(db_reclamacoes)

@app.route('/reclamacoes/<int:id>/status', methods=['POST'])
def update_reclamacao_status(id):
    reclamacao = next((r for r in db_reclamacoes if r['id'] == id), None)
    if reclamacao:
        reclamacao['status'] = request.json.get('status')
        return jsonify(reclamacao)
    return jsonify({'status': "error", 'message': 'Reclamação não encontrada'}), 404

# --- Interface Visual (Painel HTML) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Painel de Controle v7</title><script src="https://cdn.tailwindcss.com"></script><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&display=swap" rel="stylesheet"><style>body { font-family: 'Inter', sans-serif; } .log-box { background-color: #1e293b; color: #e2e8f0; font-family: monospace; font-size: 0.8rem; padding: 10px; border-radius: 5px; height: 150px; overflow-y: auto; } .log-box p { margin: 0; padding: 2px 0; border-bottom: 1px solid #334155; } </style></head><body class="bg-slate-100 text-slate-800">
<div class="container mx-auto p-4 md:p-8"><header class="text-center mb-8"><h1 class="text-4xl font-bold text-slate-900">Painel de Controle Ao Vivo</h1><p class="text-slate-600 mt-2">Gerenciamento de Sorteios, Reclamações e Disparos via WhatsApp</p></header>
<div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
    <!-- COLUNA 1 -->
    <div class="space-y-8">
        <div class="bg-white p-6 rounded-xl shadow-lg"><h2 class="text-2xl font-bold text-center mb-4 border-b pb-3 text-green-600">Disparo em Massa</h2><div class="space-y-2 text-sm"><div><label for="msg1" class="font-medium">Mensagem 1:</label><textarea id="msg1" rows="3" class="w-full p-1 border rounded"></textarea></div><div><label for="msg2" class="font-medium">Mensagem 2:</label><textarea id="msg2" rows="3" class="w-full p-1 border rounded"></textarea></div><div><label for="msg3" class="font-medium">Mensagem 3:</label><textarea id="msg3" rows="3" class="w-full p-1 border rounded"></textarea></div></div><button id="start-disparo-btn" class="w-full bg-green-600 text-white font-bold py-2 px-4 rounded-lg hover:bg-green-700 transition mt-3 text-sm">Iniciar Disparos</button><button id="stop-disparo-btn" class="w-full bg-red-600 text-white font-bold py-2 px-4 rounded-lg hover:bg-red-700 transition mt-2 text-sm" style="display: none;">Parar Disparos</button><div class="mt-4"><p class="text-center font-semibold">Status: <span id="disparo-progresso">0/0</span></p><div class="log-box" id="disparo-log"><p>Aguardando...</p></div></div></div>
        <div class="bg-white p-6 rounded-xl shadow-lg"><h2 class="text-2xl font-bold text-center mb-4 border-b pb-3 text-purple-600">Métricas</h2><div class="space-y-4 text-center"><div><p class="text-4xl font-bold text-purple-800" id="stats-total-cadastros">0</p><p class="text-sm text-slate-500">Contatos Cadastrados</p></div><div><p class="text-4xl font-bold text-purple-800" id="stats-db-size">0 MB</p><p class="text-sm text-slate-500">Tamanho do Banco</p></div></div></div>
    </div>
    <!-- COLUNA 2 -->
    <div class="bg-white p-6 rounded-xl shadow-lg"><h2 class="text-2xl font-bold text-center mb-4 border-b pb-3 text-cyan-600">Caixa de Entrada</h2>
        <div class="bg-slate-100 p-3 rounded-lg border mb-4"><h3 class="font-semibold text-sm mb-2 text-center">Buscar Mensagens</h3><div class="grid grid-cols-2 gap-2 text-sm"><div><label for="filter-start-date">De:</label><input type="date" id="filter-start-date" class="w-full p-1 border rounded"></div><div><label for="filter-end-date">Até:</label><input type="date" id="filter-end-date" class="w-full p-1 border rounded"></div></div><button id="search-messages-btn" class="w-full bg-blue-600 text-white font-bold py-1 px-2 rounded-lg hover:bg-blue-700 transition mt-2 text-xs">Buscar por Período</button><button id="reset-messages-btn" class="w-full bg-gray-500 text-white font-bold py-1 px-2 rounded-lg hover:bg-gray-600 transition mt-1 text-xs">Ver Últimos 3 Dias</button></div>
        <div id="messages-list" class="space-y-3 max-h-[600px] overflow-y-auto pr-2"></div>
    </div>
    <!-- COLUNA 3 -->
    <div class="space-y-8">
        <div class="bg-white p-6 rounded-xl shadow-lg"><h2 class="text-2xl font-bold text-center mb-4 border-b pb-3 text-indigo-600">Direto no Sorteio</h2><div id="sorteio-container" class="text-center p-4 border-2 border-dashed rounded-lg min-h-[150px] flex items-center justify-center"><div id="winner-display" class="hidden"></div><p id="sorteio-placeholder" class="text-slate-500">Aguardando...</p></div><button id="draw-button" class="w-full bg-indigo-600 text-white font-bold py-3 px-4 rounded-lg hover:bg-indigo-700 mt-4 text-lg shadow-md" disabled>SORTEAR AGORA!</button><div class="mt-6"><h3 class="font-bold text-lg mb-2">Participantes (<span id="participant-count">0</span>)</h3><div class="bg-slate-50 p-3 rounded-lg max-h-60 overflow-y-auto border"><ul id="participants-list" class="space-y-2 text-sm"></ul></div></div></div>
        <div class="bg-white p-6 rounded-xl shadow-lg"><h2 class="text-2xl font-bold text-center mb-4 border-b pb-3 text-red-600">Fala que Eu Registro</h2><div class="bg-slate-100 p-3 rounded-lg border mb-4"><h3 class="font-semibold text-sm mb-2 text-center">Gerar Relatório</h3><div class="grid grid-cols-2 gap-2 text-sm"><div><label for="filter-date" class="block font-medium">Data:</label><input type="date" id="filter-date" class="w-full p-1 border rounded"></div><div><label for="filter-status" class="block font-medium">Status:</label><select id="filter-status" class="w-full p-1 border rounded"><option value="todos">Todos</option><option value="Registrada">Registrada</option><option value="Em Análise">Em Análise</option><option value="Solucionada">Solucionada</option><option value="Sem Solução">Sem Solução</option></select></div></div><button id="print-button" class="w-full bg-gray-600 text-white font-bold py-2 px-4 rounded-lg hover:bg-gray-700 transition mt-3 text-sm">Imprimir Relatório</button></div><div class="bg-slate-50 border rounded-lg p-4 mb-6"><h3 class="font-bold text-lg text-center mb-3">Placar</h3><div class="flex justify-around text-center"><div><p class="text-3xl font-bold" id="registered-count">0</p><p class="text-sm text-slate-500">Registradas</p></div><div><p class="text-3xl font-bold text-green-600" id="solved-count">0</p><p class="text-sm text-slate-500">Solucionadas</p></div></div></div><div id="complaints-list" class="space-y-3 max-h-96 overflow-y-auto pr-2"></div></div>
    </div>
</div></div>
<script>
document.addEventListener('DOMContentLoaded', () => {
    const API_URL = window.location.origin;
    // ... (restante do JS)
    const messagesList = document.getElementById('messages-list');
    const drawButton = document.getElementById('draw-button');
    const participantsList = document.getElementById('participants-list');
    const participantCount = document.getElementById('participant-count');
    const winnerDisplay = document.getElementById('winner-display');
    const sorteioPlaceholder = document.getElementById('sorteio-placeholder');
    const complaintsList = document.getElementById('complaints-list');
    const registeredCountEl = document.getElementById('registered-count');
    const solvedCountEl = document.getElementById('solved-count');
    const filterDate = document.getElementById('filter-date');
    const filterStatus = document.getElementById('filter-status');
    const printButton = document.getElementById('print-button');
    const startDisparoBtn = document.getElementById('start-disparo-btn');
    const stopDisparoBtn = document.getElementById('stop-disparo-btn');
    const disparoProgresso = document.getElementById('disparo-progresso');
    const disparoLog = document.getElementById('disparo-log');
    const msg1 = document.getElementById('msg1');
    const msg2 = document.getElementById('msg2');
    const msg3 = document.getElementById('msg3');
    const statsTotalCadastros = document.getElementById('stats-total-cadastros');
    const statsDbSize = document.getElementById('stats-db-size');
    const searchMessagesBtn = document.getElementById('search-messages-btn');
    const resetMessagesBtn = document.getElementById('reset-messages-btn');
    const filterStartDate = document.getElementById('filter-start-date');
    const filterEndDate = document.getElementById('filter-end-date');

    let reclamacoesCache = [];
    let participantesCache = [];

    async function fetchMainData() {
        try {
            const [pRes, rRes] = await Promise.all([
                fetch(`${API_URL}/participantes`), fetch(`${API_URL}/reclamacoes`)
            ]);
            participantesCache = await pRes.json();
            reclamacoesCache = await rRes.json();
            renderizarParticipantes(participantesCache);
            renderizarReclamacoes();
            atualizarPlacar(reclamacoesCache);
        } catch (error) { console.error("Erro ao buscar dados principais:", error); }
    }
    
    async function fetchMessages(startDate = null, endDate = null) {
        let url = `${API_URL}/mensagens`;
        if (startDate && endDate) {
            url += `?start_date=${startDate}&end_date=${endDate}`;
        }
        try {
            const mRes = await fetch(url);
            renderizarMensagens(await mRes.json());
        } catch (error) { console.error("Erro ao buscar mensagens:", error); }
    }

    async function fetchStats() {
        try {
            const response = await fetch(`${API_URL}/stats`);
            const stats = await response.json();
            statsTotalCadastros.textContent = stats.total_cadastros;
            statsDbSize.textContent = stats.db_size;
        } catch (error) { console.error("Erro ao buscar stats:", error); }
    }
    
    async function fetchDisparoStatus() {
        try {
            const response = await fetch(`${API_URL}/status_disparo`);
            const status = await response.json();
            disparoProgresso.textContent = `${status.progresso}/${status.total}`;
            disparoLog.innerHTML = status.log.map(l => `<p>${l}</p>`).join('');
            disparoLog.scrollTop = disparoLog.scrollHeight;
            startDisparoBtn.style.display = status.ativo ? 'none' : 'block';
            stopDisparoBtn.style.display = status.ativo ? 'block' : 'none';
        } catch (error) { console.error("Erro ao buscar status do disparo:", error); }
    }

    function createMediaElement(msg) {
        let contentHtml = `<p class="mt-2 text-sm text-slate-700">${msg.texto}</p>`;
        if (msg.media_id) {
            if (msg.media_type === 'image') {
                contentHtml = `<img src="/media/${msg.media_id}" class="w-full h-auto rounded mt-2 cursor-pointer" onclick="window.open('/media/${msg.media_id}', '_blank')">`;
            } else {
                contentHtml = `<div class="mt-2"><a href="/media/${msg.media_id}" target="_blank" class="text-sm text-blue-600 hover:underline">Ver ${msg.media_type}</a></div>`;
            }
            if (msg.texto && !msg.texto.startsWith('[')) {
                contentHtml += `<p class="mt-1 text-sm text-slate-600">${msg.texto}</p>`;
            }
        }
        return contentHtml;
    }

    function renderizarMensagens(data) {
        messagesList.innerHTML = '';
        if (data.length === 0) {
            messagesList.innerHTML = '<p class="text-slate-400 text-center">Nenhuma mensagem encontrada.</p>'; return;
        }
        data.forEach(msg => {
            const dataFormatada = new Date(msg.timestamp).toLocaleString('pt-BR');
            const card = document.createElement('div');
            card.className = 'p-3 rounded-lg border bg-slate-50';
            card.innerHTML = `<div><p class="font-bold text-sm">${msg.nome}</p><p class="text-xs text-slate-500">${msg.telefone} - ${dataFormatada}</p></div> ${createMediaElement(msg)} <button data-id="${msg.id}" class="promote-btn w-full text-xs bg-cyan-500 text-white font-semibold py-1 px-2 rounded hover:bg-cyan-600 transition mt-2">Promover para Reclamação</button>`;
            messagesList.appendChild(card);
        });
        addPromoteListeners();
    }
    
    function renderizarParticipantes(data) {
        participantsList.innerHTML = '';
        participantCount.textContent = data.length;
        if (data.length === 0) {
            participantsList.innerHTML = '<li class="text-slate-400 text-center">Nenhum participante.</li>';
            drawButton.disabled = true; sorteioPlaceholder.textContent = 'Aguardando...';
        } else {
            data.forEach(p => {
                const li = document.createElement('li');
                li.className = 'bg-white p-2 rounded border border-slate-200 roulette-item';
                li.textContent = `${p.nome} - ${p.telefone}`;
                participantsList.appendChild(li);
            });
            drawButton.disabled = false; sorteioPlaceholder.textContent = 'Clique para sortear!';
        }
    }

    function getFilteredReclamacoes() {
        const date = filterDate.value;
        const status = filterStatus.value;
        return reclamacoesCache.filter(r => {
            const matchStatus = (status === 'todos') || (r.status === status);
            const matchDate = !date || (r.timestamp && r.timestamp.startsWith(date));
            return matchStatus && matchDate;
        });
    }

    function renderizarReclamacoes() {
        const filteredReclamacoes = getFilteredReclamacoes();
        complaintsList.innerHTML = '';
        if (filteredReclamacoes.length === 0) {
            complaintsList.innerHTML = '<p class="text-slate-400 text-center">Nenhuma reclamação com os filtros.</p>'; return;
        }
        filteredReclamacoes.sort((a, b) => b.id - a.id);
        filteredReclamacoes.forEach(r => {
            const statusColors = { 'Registrada': 'bg-yellow-100', 'Em Análise': 'bg-blue-100', 'Solucionada': 'bg-green-100', 'Sem Solução': 'bg-red-100' };
            const card = document.createElement('div');
            card.className = `p-4 rounded-lg border ${statusColors[r.status]}`;
            card.innerHTML = `<div class="flex justify-between items-start"><div><p class="font-bold">${r.nome}</p><p class="text-xs text-slate-600">${r.telefone}</p></div><select data-id="${r.id}" class="status-select text-sm rounded border-slate-300 p-1"><option value="Registrada" ${r.status === 'Registrada' ? 'selected' : ''}>Registrada</option><option value="Em Análise" ${r.status === 'Em Análise' ? 'selected' : ''}>Em Análise</option><option value="Solucionada" ${r.status === 'Solucionada' ? 'selected' : ''}>Solucionada</option><option value="Sem Solução" ${r.status === 'Sem Solução' ? 'selected' : ''}>Sem Solução</option></select></div>${createMediaElement(r)}`;
            complaintsList.appendChild(card);
        });
        addStatusChangeListeners();
    }

    function atualizarPlacar(reclamacoes) {
        registeredCountEl.textContent = reclamacoes.length;
        solvedCountEl.textContent = reclamacoes.filter(r => r.status === 'Solucionada').length;
    }

    function imprimirRelatorio() {
        // ... (código inalterado)
    }

    async function promoverMensagem(id) {
        try {
            await fetch(`${API_URL}/promover_reclamacao`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: id }) });
            fetchMainData();
            fetchMessages();
        } catch (error) { console.error("Erro ao promover mensagem:", error); }
    }

    function addPromoteListeners() {
        document.querySelectorAll('.promote-btn').forEach(btn => {
            btn.addEventListener('click', (event) => { promoverMensagem(parseInt(event.target.dataset.id)); });
        });
    }

    function realizarSorteio() { /* ... (código inalterado) ... */ }
    async function updateStatus(id, newStatus) { /* ... (código inalterado) ... */ }
    function addStatusChangeListeners() { /* ... (código inalterado) ... */ }

    // Event Listeners
    startDisparoBtn.addEventListener('click', async () => {
        const payload = { msg1: msg1.value, msg2: msg2.value, msg3: msg3.value };
        if (!payload.msg1 && !payload.msg2 && !payload.msg3) { alert('Escreva pelo menos uma mensagem.'); return; }
        try {
            await fetch(`${API_URL}/iniciar_disparo`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            fetchDisparoStatus();
        } catch (error) { console.error('Erro ao iniciar disparo:', error); }
    });
    stopDisparoBtn.addEventListener('click', async () => {
        try { await fetch(`${API_URL}/parar_disparo`, { method: 'POST' }); }
        catch (error) { console.error('Erro ao parar disparo:', error); }
    });
    filterDate.addEventListener('change', renderizarReclamacoes);
    filterStatus.addEventListener('change', renderizarReclamacoes);
    printButton.addEventListener('click', imprimirRelatorio);
    drawButton.addEventListener('click', realizarSorteio);
    searchMessagesBtn.addEventListener('click', () => {
        const start = filterStartDate.value;
        const end = filterEndDate.value;
        if (start && end) { fetchMessages(start, end); }
        else { alert('Por favor, selecione as duas datas.'); }
    });
    resetMessagesBtn.addEventListener('click', () => {
        filterStartDate.value = '';
        filterEndDate.value = '';
        fetchMessages();
    });

    // Inicialização
    fetchMainData();
    fetchMessages();
    fetchStats();
    setInterval(fetchMainData, 20000);
    setInterval(fetchMessages, 20000);
    setInterval(fetchStats, 60000); // 1 minuto
    setInterval(fetchDisparoStatus, 5000);
});
</script></body></html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    print("===================================================")
    print("🚀 Servidor do Painel v7 (Modo Meta API + DB) iniciado!")
    print("Acesse o painel em: http://127.0.0.1:5000")
    print("===================================================")
    app.run(host='0.0.0.0', port=5000, debug=False)
