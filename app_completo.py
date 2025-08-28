# -*- coding: utf-8 -*-

# =============================================================================
# APLICAÇÃO COMPLETA v7: INTEGRAÇÃO COM BANCO DE DADOS POSTGRESQL
# =============================================================================

import os
import requests
import json
import re
from flask import Flask, request, jsonify, render_template_string, Response
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

# Carrega as variáveis de ambiente do arquivo .env para testes locais
load_dotenv()

# --- Configuração do Banco de Dados ---
app = Flask(__name__)
CORS(app)
# Pega a URL do banco de dados da variável de ambiente que vamos configurar no Render
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Modelo da Tabela do Banco de Dados ---
# Define a estrutura da nossa tabela de cadastros
class Cadastro(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    telefone = db.Column(db.String(30), unique=True, nullable=False)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Cadastro {self.telefone}>'

# --- Busca as credenciais do ambiente do servidor (Render) ou do arquivo .env (local) ---
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")

# --- Banco de Dados em Memória (para sorteio e reclamações, que são temporários) ---
db_participantes_sorteio = {}
db_reclamacoes = []
db_mensagens_recebidas = []
reclamacao_id_counter = 1
mensagem_id_counter = 1

# --- Lógica Principal ---

def salvar_numero_no_banco(telefone):
    """
    Salva um número de telefone no banco de dados PostgreSQL.
    Verifica se o número já existe antes de inserir.
    """
    with app.app_context():
        try:
            existente = Cadastro.query.filter_by(telefone=telefone).first()
            if not existente:
                novo_numero = Cadastro(telefone=telefone)
                db.session.add(novo_numero)
                db.session.commit()
                print(f"✅ Novo número '{telefone}' salvo no banco de dados.")
            else:
                print(f"🔄 Número '{telefone}' já consta no banco de dados.")
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
        print(f"AVISO: Credenciais da Meta não configuradas. Simulando envio para {destinatario}: {mensagem}")
        return
    url = f"https://graph.facebook.com/v18.0/{META_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": destinatario, "text": {"body": mensagem}}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        print(f"Mensagem de confirmação enviada para {destinatario}. Status: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"ERRO ao enviar mensagem via Meta API: {e}")
        print("Response Body:", e.response.text if e.response else "No response")

# --- Endpoints da API ---

@app.route('/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    if request.method == 'GET':
        if request.args.get('hub.verify_token') == META_VERIFY_TOKEN:
            return request.args.get('hub.challenge')
        return "Token de verificação inválido", 403

    if request.method == 'POST':
        global mensagem_id_counter
        data = request.json
        try:
            if 'entry' in data and data['entry'][0]['changes'][0]['value'].get('messages'):
                message_data = data['entry'][0]['changes'][0]['value']['messages'][0]
                remetente = message_data['from']
                
                # --- NOVO: Chama a função para salvar o número no banco de dados ---
                salvar_numero_no_banco(remetente)
                # -----------------------------------------------------------------

                message_type = message_data.get('type')
                
                mensagem_para_painel = ""
                nome_extraido = None
                media_id = None

                if message_type == 'text':
                    mensagem_para_painel = message_data['text']['body']
                    nome_extraido = extrair_nome(mensagem_para_painel)
                
                elif message_type in ['image', 'video', 'document']:
                    media_id = message_data[message_type]['id']
                    legenda = message_data[message_type].get('caption')
                    if legenda:
                        mensagem_para_painel = legenda
                        nome_extraido = extrair_nome(legenda)
                    else:
                        mensagem_para_painel = f"[{message_type.upper()} RECEBIDA]"

                elif message_type == 'audio':
                    media_id = message_data[message_type]['id']
                    mensagem_para_painel = "[ÁUDIO RECEBIDO]"

                else:
                    mensagem_para_painel = f"[MÍDIA DO TIPO '{message_type}' RECEBIDA]"

                print(f"Conteúdo recebido de {remetente}: '{mensagem_para_painel}'")

                nova_mensagem = {
                    "id": mensagem_id_counter, "nome": nome_extraido or f"Pessoa ({remetente[-4:]})",
                    "telefone": remetente, "texto": mensagem_para_painel,
                    "media_id": media_id, "media_type": message_type
                }
                db_mensagens_recebidas.append(nova_mensagem)
                mensagem_id_counter += 1

                if adicionar_ao_sorteio(remetente, nome_extraido):
                    enviar_resposta_whatsapp(remetente, "Obrigado por sua mensagem! Você já está participando do nosso sorteio semanal. Boa sorte! 🤞")
                
        except (KeyError, IndexError) as e:
            print(f"Formato de notificação não esperado ou erro de processamento: {e}")
            pass
        return "OK", 200

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

@app.route('/mensagens', methods=['GET'])
def get_mensagens(): return jsonify(db_mensagens_recebidas)

@app.route('/promover_reclamacao', methods=['POST'])
def promover_reclamacao():
    global reclamacao_id_counter
    data = request.json
    mensagem_id = data.get('id')
    mensagem_a_promover = next((msg for msg in db_mensagens_recebidas if msg['id'] == mensagem_id), None)
    
    if mensagem_a_promover:
        nova_reclamacao = {
            "id": reclamacao_id_counter, "nome": mensagem_a_promover['nome'],
            "telefone": mensagem_a_promover['telefone'], "texto": mensagem_a_promover['texto'],
            "status": "Registrada", "media_id": mensagem_a_promover.get('media_id'),
            "media_type": mensagem_a_promover.get('media_type'),
            "timestamp": datetime.now().isoformat() # Adiciona a data e hora
        }
        db_reclamacoes.append(nova_reclamacao)
        reclamacao_id_counter += 1
        db_mensagens_recebidas.remove(mensagem_a_promover)
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Mensagem não encontrada"}), 404

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
    return jsonify({'status': 'error', 'message': 'Reclamação não encontrada'}), 404

# --- Interface Visual (Painel HTML) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Painel de Controle - Sorteios e Reclamações</title><script src="https://cdn.tailwindcss.com"></script><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&display=swap" rel="stylesheet"><style>body { font-family: 'Inter', sans-serif; }.winner-card { animation: fadeIn 0.5s ease-in-out, scaleUp 0.3s ease-out; }@keyframes fadeIn { from { opacity: 0; transform: translateY(-20px); } to { opacity: 1; transform: translateY(0); } }@keyframes scaleUp { from { transform: scale(0.9); } to { transform: scale(1); } }.roulette-item { transition: all 0.1s ease-in-out; }.highlight { background-color: #fde047; transform: scale(1.05); } .media-text { font-style: italic; color: #475569; }</style></head><body class="bg-slate-100 text-slate-800">
<div class="container mx-auto p-4 md:p-8"><header class="text-center mb-8"><h1 class="text-4xl font-bold text-slate-900">Painel de Controle Ao Vivo</h1><p class="text-slate-600 mt-2">Gerenciamento de Sorteios e Reclamações via WhatsApp</p></header>
<div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
    <div class="bg-white p-6 rounded-xl shadow-lg lg:col-span-1"><h2 class="text-2xl font-bold text-center mb-4 border-b pb-3 text-cyan-600">Caixa de Entrada</h2><div id="messages-list" class="space-y-3 max-h-[600px] overflow-y-auto pr-2"></div></div>
    <div class="bg-white p-6 rounded-xl shadow-lg lg:col-span-1"><h2 class="text-2xl font-bold text-center mb-4 border-b pb-3 text-indigo-600">Direto no Sorteio</h2><div id="sorteio-container" class="text-center p-4 border-2 border-dashed rounded-lg min-h-[150px] flex items-center justify-center"><div id="winner-display" class="hidden"></div><p id="sorteio-placeholder" class="text-slate-500">Aguardando participantes...</p></div><button id="draw-button" class="w-full bg-indigo-600 text-white font-bold py-3 px-4 rounded-lg hover:bg-indigo-700 transition-all duration-300 mt-4 text-lg shadow-md" disabled>SORTEAR AGORA!</button><div class="mt-6"><h3 class="font-bold text-lg mb-2">Participantes da Semana (<span id="participant-count">0</span>)</h3><div class="bg-slate-50 p-3 rounded-lg max-h-60 overflow-y-auto border"><ul id="participants-list" class="space-y-2 text-sm"></ul></div></div></div>
    <div class="bg-white p-6 rounded-xl shadow-lg lg:col-span-1"><h2 class="text-2xl font-bold text-center mb-4 border-b pb-3 text-red-600">Fala que Eu Registro</h2>
    <!-- SEÇÃO DE FILTROS E IMPRESSÃO -->
    <div class="bg-slate-100 p-3 rounded-lg border mb-4"><h3 class="font-semibold text-sm mb-2 text-center">Gerar Relatório</h3><div class="grid grid-cols-2 gap-2 text-sm"><div><label for="filter-date" class="block font-medium">Data:</label><input type="date" id="filter-date" class="w-full p-1 border rounded"></div><div><label for="filter-status" class="block font-medium">Status:</label><select id="filter-status" class="w-full p-1 border rounded"><option value="todos">Todos</option><option value="Registrada">Registrada</option><option value="Em Análise">Em Análise</option><option value="Solucionada">Solucionada</option><option value="Sem Solução">Sem Solução</option></select></div></div><button id="print-button" class="w-full bg-gray-600 text-white font-bold py-2 px-4 rounded-lg hover:bg-gray-700 transition mt-3 text-sm">Imprimir Relatório</button></div>
    <!-- PLACAR -->
    <div class="bg-slate-50 border border-slate-200 rounded-lg p-4 mb-6"><h3 class="font-bold text-lg text-center mb-3">Placar da Comunidade</h3><div class="flex justify-around text-center"><div><p class="text-3xl font-bold text-slate-800" id="registered-count">0</p><p class="text-sm text-slate-500">Registradas</p></div><div><p class="text-3xl font-bold text-green-600" id="solved-count">0</p><p class="text-sm text-slate-500">Solucionadas</p></div></div></div><div id="complaints-list" class="space-y-3 max-h-96 overflow-y-auto pr-2"></div></div>
</div></div>
<script>
document.addEventListener('DOMContentLoaded', () => {
    const API_URL = window.location.origin;
    let participantesSorteio = [], reclamacoes = [], mensagensRecebidas = [];
    // Elementos do DOM
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

    async function fetchData() {
        try {
            const [pRes, rRes, mRes] = await Promise.all([
                fetch(`${API_URL}/participantes`), fetch(`${API_URL}/reclamacoes`), fetch(`${API_URL}/mensagens`)
            ]);
            participantesSorteio = await pRes.json();
            reclamacoes = await rRes.json();
            mensagensRecebidas = await mRes.json();
            renderizarTudo();
        } catch (error) { console.error("Erro ao buscar dados:", error); }
    }

    function renderizarTudo() {
        renderizarMensagens();
        renderizarParticipantes();
        renderizarReclamacoes();
        atualizarPlacar();
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

    function renderizarMensagens() {
        messagesList.innerHTML = '';
        if (mensagensRecebidas.length === 0) {
            messagesList.innerHTML = '<p class="text-slate-400 text-center">Nenhuma nova mensagem.</p>'; return;
        }
        mensagensRecebidas.sort((a, b) => b.id - a.id);
        mensagensRecebidas.forEach(msg => {
            const card = document.createElement('div');
            card.className = 'p-3 rounded-lg border bg-slate-50';
            card.innerHTML = `<div><p class="font-bold text-sm">${msg.nome}</p><p class="text-xs text-slate-500">${msg.telefone}</p></div> ${createMediaElement(msg)} <button data-id="${msg.id}" class="promote-btn w-full text-xs bg-cyan-500 text-white font-semibold py-1 px-2 rounded hover:bg-cyan-600 transition mt-2">Promover para Reclamação</button>`;
            messagesList.appendChild(card);
        });
        addPromoteListeners();
    }
    
    function renderizarParticipantes() {
        participantsList.innerHTML = '';
        participantCount.textContent = participantesSorteio.length;
        if (participantesSorteio.length === 0) {
            participantsList.innerHTML = '<li class="text-slate-400 text-center">Nenhum participante.</li>';
            drawButton.disabled = true; sorteioPlaceholder.textContent = 'Aguardando...';
        } else {
            participantesSorteio.forEach(p => {
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
        return reclamacoes.filter(r => {
            const matchStatus = (status === 'todos') || (r.status === status);
            const matchDate = !date || (r.timestamp && r.timestamp.startsWith(date));
            return matchStatus && matchDate;
        });
    }

    function renderizarReclamacoes() {
        const filteredReclamacoes = getFilteredReclamacoes();
        complaintsList.innerHTML = '';
        if (filteredReclamacoes.length === 0) {
            complaintsList.innerHTML = '<p class="text-slate-400 text-center">Nenhuma reclamação encontrada com os filtros atuais.</p>'; return;
        }
        filteredReclamacoes.sort((a, b) => b.id - a.id);
        filteredReclamacoes.forEach(r => {
            const statusColors = { 'Registrada': 'bg-yellow-100 text-yellow-800 border-yellow-300', 'Em Análise': 'bg-blue-100 text-blue-800 border-blue-300', 'Solucionada': 'bg-green-100 text-green-800 border-green-300', 'Sem Solução': 'bg-red-100 text-red-800 border-red-300' };
            const card = document.createElement('div');
            card.className = `p-4 rounded-lg border ${statusColors[r.status]}`;
            card.innerHTML = `<div class="flex justify-between items-start"><div><p class="font-bold">${r.nome}</p><p class="text-xs text-slate-600">${r.telefone}</p></div><select data-id="${r.id}" class="status-select text-sm rounded border-slate-300 p-1"><option value="Registrada" ${r.status === 'Registrada' ? 'selected' : ''}>Registrada</option><option value="Em Análise" ${r.status === 'Em Análise' ? 'selected' : ''}>Em Análise</option><option value="Solucionada" ${r.status === 'Solucionada' ? 'selected' : ''}>Solucionada</option><option value="Sem Solução" ${r.status === 'Sem Solução' ? 'selected' : ''}>Sem Solução</option></select></div>${createMediaElement(r)}`;
            complaintsList.appendChild(card);
        });
        addStatusChangeListeners();
    }

    function atualizarPlacar() {
        registeredCountEl.textContent = reclamacoes.length;
        solvedCountEl.textContent = reclamacoes.filter(r => r.status === 'Solucionada').length;
    }

    function imprimirRelatorio() {
        const filteredData = getFilteredReclamacoes();
        const dateFilter = filterDate.value ? new Date(filterDate.value + 'T00:00:00').toLocaleDateString('pt-BR') : 'Todas';
        const statusFilter = filterStatus.options[filterStatus.selectedIndex].text;

        let reportHtml = `
            <html><head><title>Relatório de Reclamações</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; }
                h1, h2 { color: #333; }
                table { width: 100%; border-collapse: collapse; margin-top: 20px; }
                th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
                th { background-color: #f2f2f2; }
                .no-print { display: none; }
                .media-placeholder { font-style: italic; color: #555; }
            </style>
            </head><body>
            <h1>Relatório de Reclamações</h1>
            <h2>Filtros Aplicados - Data: ${dateFilter} | Status: ${statusFilter}</h2>
            <table><thead><tr><th>Data</th><th>Nome</th><th>Telefone</th><th>Status</th><th>Descrição</th></tr></thead><tbody>
        `;
        if (filteredData.length === 0) {
            reportHtml += '<tr><td colspan="5" style="text-align:center;">Nenhum registro encontrado.</td></tr>';
        } else {
            filteredData.forEach(r => {
                const dataFormatada = r.timestamp ? new Date(r.timestamp).toLocaleString('pt-BR') : 'N/A';
                const textoDescricao = r.media_id ? `(${r.media_type}) ${r.texto}` : r.texto;
                reportHtml += `<tr><td>${dataFormatada}</td><td>${r.nome}</td><td>${r.telefone}</td><td>${r.status}</td><td>${textoDescricao}</td></tr>`;
            });
        }
        reportHtml += '</tbody></table></body></html>';
        
        const reportWindow = window.open('', '_blank');
        reportWindow.document.write(reportHtml);
        reportWindow.document.close();
        reportWindow.print();
    }

    async function promoverMensagem(id) {
        try {
            await fetch(`${API_URL}/promover_reclamacao`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: id }) });
            fetchData();
        } catch (error) { console.error("Erro ao promover mensagem:", error); }
    }

    function addPromoteListeners() {
        document.querySelectorAll('.promote-btn').forEach(btn => {
            btn.addEventListener('click', (event) => { promoverMensagem(parseInt(event.target.dataset.id)); });
        });
    }

    function realizarSorteio() {
        if (participantesSorteio.length === 0) return;
        drawButton.disabled = true; drawButton.textContent = 'SORTEANDO...';
        winnerDisplay.classList.add('hidden'); sorteioPlaceholder.classList.remove('hidden'); sorteioPlaceholder.textContent = '...';
        const pElems = Array.from(participantsList.children);
        let rouletteInterval = setInterval(() => {
            pElems.forEach(el => el.classList.remove('highlight'));
            const randIdx = Math.floor(Math.random() * pElems.length);
            pElems[randIdx].classList.add('highlight');
        }, 100);
        setTimeout(() => {
            clearInterval(rouletteInterval);
            const winnerIndex = Math.floor(Math.random() * participantesSorteio.length);
            const winner = participantesSorteio[winnerIndex];
            pElems.forEach(el => el.classList.remove('highlight'));
            pElems[winnerIndex].classList.add('highlight');
            winnerDisplay.innerHTML = `<div class="winner-card text-center"><p class="text-sm text-slate-500">O VENCEDOR É...</p><p class="text-3xl font-bold text-indigo-700 my-2">${winner.nome}</p><p class="text-lg text-slate-600">${winner.telefone}</p></div>`;
            sorteioPlaceholder.classList.add('hidden'); winnerDisplay.classList.remove('hidden');
            drawButton.disabled = false; drawButton.textContent = 'SORTEAR NOVAMENTE';
        }, 4000);
    }

    async function updateStatus(id, newStatus) {
        try {
            await fetch(`${API_URL}/reclamacoes/${id}/status`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: newStatus }) });
            fetchData();
        } catch (error) { console.error("Erro ao atualizar status:", error); }
    }

    function addStatusChangeListeners() {
        document.querySelectorAll('.status-select').forEach(select => {
            select.addEventListener('change', (event) => {
                updateStatus(parseInt(event.target.dataset.id), event.target.value);
            });
        });
    }

    fetchData();
    setInterval(fetchData, 5000);
    drawButton.addEventListener('click', realizarSorteio);
    filterDate.addEventListener('change', renderizarReclamacoes);
    filterStatus.addEventListener('change', renderizarReclamacoes);
    printButton.addEventListener('click', imprimirRelatorio);
});
</script></body></html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    # Cria a tabela no banco de dados se ela não existir
    with app.app_context():
        db.create_all()
    print("===================================================")
    print("🚀 Servidor do Painel v7 (Modo Meta API + DB) iniciado!")
    print("Acesse o painel em: http://127.0.0.1:5000")
    print("===================================================")
    app.run(host='0.0.0.0', port=5000, debug=False)
