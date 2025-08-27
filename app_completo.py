# -*- coding: utf-8 -*-
import os
import requests
import json
import re
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")

app = Flask(__name__)
CORS(app)

db_participantes_sorteio = {}
db_reclamacoes = []
reclamacao_id_counter = 1

def extrair_nome(texto):
    match = re.search(r"(?:meu nome é|chamo-me|sou o|sou a)\s+([A-Za-zÀ-ú\s]+)", texto, re.IGNORECASE)
    if match:
        return match.group(1).strip().title()
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

@app.route('/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    if request.method == 'GET':
        if request.args.get('hub.verify_token') == META_VERIFY_TOKEN:
            return request.args.get('hub.challenge')
        return "Token de verificação inválido", 403
    if request.method == 'POST':
        global reclamacao_id_counter
        data = request.json
        try:
            if 'entry' in data and data['entry'][0]['changes'][0]['value'].get('messages'):
                message_data = data['entry'][0]['changes'][0]['value']['messages'][0]
                remetente = message_data['from']
                mensagem = message_data['text']['body']
                print(f"Mensagem recebida de {remetente}: '{mensagem}'")
                nome_extraido = extrair_nome(mensagem)
                if "reclamação" in mensagem.lower() or "problema" in mensagem.lower() or "denúncia" in mensagem.lower():
                    nova_reclamacao = {"id": reclamacao_id_counter, "nome": nome_extraido or f"Pessoa ({remetente[-4:]})", "telefone": remetente, "texto": mensagem, "status": "Registrada"}
                    db_reclamacoes.append(nova_reclamacao)
                    reclamacao_id_counter += 1
                    adicionar_ao_sorteio(remetente, nome_extraido)
                    enviar_resposta_whatsapp(remetente, "Sua reclamação foi registrada com sucesso e você já está participando do nosso sorteio semanal! Obrigado por nos ajudar a melhorar nossa comunidade.")
                else:
                    if adicionar_ao_sorteio(remetente, nome_extraido):
                        enviar_resposta_whatsapp(remetente, "Obrigado por participar! Seu nome já está no sorteio. Boa sorte! 🤞")
                    else:
                        enviar_resposta_whatsapp(remetente, "Vimos que você já está participando do sorteio. 👍 Boa sorte!")
        except (KeyError, IndexError) as e:
            print(f"Formato de notificação não esperado: {e}")
            pass
        return "OK", 200

@app.route('/participantes', methods=['GET'])
def get_participantes(): return jsonify(list(db_participantes_sorteio.values()))

@app.route('/reclamacoes', methods=['GET'])
def get_reclamacoes(): return jsonify(db_reclamacoes)

@app.route('/reclamacoes/<int:id>/status', methods=['POST'])
def update_reclamacao_status(id):
    data = request.json
    novo_status = data.get('status')
    for reclamacao in db_reclamacoes:
        if reclamacao['id'] == id:
            reclamacao['status'] = novo_status
            return jsonify(reclamacao)
    return jsonify({'status': 'error', 'message': 'Reclamação não encontrada'}), 404

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Painel de Controle - Sorteios e Reclamações</title><script src="https://cdn.tailwindcss.com"></script><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&display=swap" rel="stylesheet"><style>body { font-family: 'Inter', sans-serif; }.winner-card { animation: fadeIn 0.5s ease-in-out, scaleUp 0.3s ease-out; }@keyframes fadeIn { from { opacity: 0; transform: translateY(-20px); } to { opacity: 1; transform: translateY(0); } }@keyframes scaleUp { from { transform: scale(0.9); } to { transform: scale(1); } }.roulette-item { transition: all 0.1s ease-in-out; }.highlight { background-color: #fde047; transform: scale(1.05); }</style></head><body class="bg-slate-100 text-slate-800"><div class="container mx-auto p-4 md:p-8"><header class="text-center mb-8"><h1 class="text-4xl font-bold text-slate-900">Painel de Controle Ao Vivo</h1><p class="text-slate-600 mt-2">Gerenciamento de Sorteios e Reclamações via WhatsApp</p></header><div class="grid grid-cols-1 md:grid-cols-2 gap-8"><div class="bg-white p-6 rounded-xl shadow-lg"><h2 class="text-2xl font-bold text-center mb-4 border-b pb-3 text-indigo-600">Direto no Sorteio</h2><div id="sorteio-container" class="text-center p-4 border-2 border-dashed rounded-lg min-h-[150px] flex items-center justify-center"><div id="winner-display" class="hidden"></div><p id="sorteio-placeholder" class="text-slate-500">Aguardando participantes...</p></div><button id="draw-button" class="w-full bg-indigo-600 text-white font-bold py-3 px-4 rounded-lg hover:bg-indigo-700 transition-all duration-300 mt-4 text-lg shadow-md" disabled>SORTEAR AGORA!</button><div class="mt-6"><h3 class="font-bold text-lg mb-2">Participantes da Semana (<span id="participant-count">0</span>)</h3><div class="bg-slate-50 p-3 rounded-lg max-h-60 overflow-y-auto border"><ul id="participants-list" class="space-y-2 text-sm"></ul></div></div></div><div class="bg-white p-6 rounded-xl shadow-lg"><h2 class="text-2xl font-bold text-center mb-4 border-b pb-3 text-red-600">Fala que Eu Registro</h2><div class="bg-slate-50 border border-slate-200 rounded-lg p-4 mb-6"><h3 class="font-bold text-lg text-center mb-3">Placar da Comunidade</h3><div class="flex justify-around text-center"><div><p class="text-3xl font-bold text-slate-800" id="registered-count">0</p><p class="text-sm text-slate-500">Registradas</p></div><div><p class="text-3xl font-bold text-green-600" id="solved-count">0</p><p class="text-sm text-slate-500">Solucionadas</p></div></div></div><div id="complaints-list" class="space-y-3 max-h-96 overflow-y-auto pr-2"></div></div></div></div><script>document.addEventListener('DOMContentLoaded', () => { const API_URL = window.location.origin; let participantesSorteio = []; let reclamacoes = []; const drawButton = document.getElementById('draw-button'); const participantsList = document.getElementById('participants-list'); const participantCount = document.getElementById('participant-count'); const winnerDisplay = document.getElementById('winner-display'); const sorteioPlaceholder = document.getElementById('sorteio-placeholder'); const complaintsList = document.getElementById('complaints-list'); const registeredCountEl = document.getElementById('registered-count'); const solvedCountEl = document.getElementById('solved-count'); async function fetchData() { try { const [participantesRes, reclamacoesRes] = await Promise.all([ fetch(`${API_URL}/participantes`), fetch(`${API_URL}/reclamacoes`) ]); participantesSorteio = await participantesRes.json(); reclamacoes = await reclamacoesRes.json(); renderizarTudo(); } catch (error) { console.error("Erro ao buscar dados do servidor:", error); sorteioPlaceholder.textContent = "Erro de conexão com o servidor."; } } function renderizarTudo() { renderizarParticipantes(); renderizarReclamacoes(); atualizarPlacar(); } function renderizarParticipantes() { participantsList.innerHTML = ''; participantCount.textContent = participantesSorteio.length; if (participantesSorteio.length === 0) { participantsList.innerHTML = '<li class="text-slate-400 text-center">Nenhum participante ainda.</li>'; drawButton.disabled = true; sorteioPlaceholder.textContent = 'Aguardando participantes...'; } else { participantesSorteio.forEach(p => { const li = document.createElement('li'); li.className = 'bg-white p-2 rounded border border-slate-200 roulette-item'; li.textContent = `${p.nome} - ${p.telefone}`; participantsList.appendChild(li); }); drawButton.disabled = false; sorteioPlaceholder.textContent = 'Clique no botão para iniciar o sorteio!'; } } function renderizarReclamacoes() { complaintsList.innerHTML = ''; if (reclamacoes.length === 0) { complaintsList.innerHTML = '<p class="text-slate-400 text-center">Nenhuma reclamação registrada.</p>'; return; } reclamacoes.sort((a, b) => b.id - a.id); reclamacoes.forEach(r => { const statusColors = { 'Registrada': 'bg-yellow-100 text-yellow-800 border-yellow-300', 'Em Análise': 'bg-blue-100 text-blue-800 border-blue-300', 'Solucionada': 'bg-green-100 text-green-800 border-green-300', 'Sem Solução': 'bg-red-100 text-red-800 border-red-300' }; const card = document.createElement('div'); card.className = `p-4 rounded-lg border ${statusColors[r.status]}`; card.innerHTML = `<div class="flex justify-between items-start"><div><p class="font-bold">${r.nome}</p><p class="text-xs text-slate-600">${r.telefone}</p></div><select data-id="${r.id}" class="status-select text-sm rounded border-slate-300 p-1"><option value="Registrada" ${r.status === 'Registrada' ? 'selected' : ''}>Registrada</option><option value="Em Análise" ${r.status === 'Em Análise' ? 'selected' : ''}>Em Análise</option><option value="Solucionada" ${r.status === 'Solucionada' ? 'selected' : ''}>Solucionada</option><option value="Sem Solução" ${r.status === 'Sem Solução' ? 'selected' : ''}>Sem Solução</option></select></div><p class="mt-2 text-sm">${r.texto}</p>`; complaintsList.appendChild(card); }); addStatusChangeListeners(); } function atualizarPlacar() { registeredCountEl.textContent = reclamacoes.length; solvedCountEl.textContent = reclamacoes.filter(r => r.status === 'Solucionada').length; } function realizarSorteio() { if (participantesSorteio.length === 0) return; drawButton.disabled = true; drawButton.textContent = 'SORTEANDO...'; winnerDisplay.classList.add('hidden'); sorteioPlaceholder.classList.remove('hidden'); sorteioPlaceholder.textContent = '...'; const participantElements = Array.from(participantsList.children); let rouletteInterval = setInterval(() => { participantElements.forEach(el => el.classList.remove('highlight')); const randomIndex = Math.floor(Math.random() * participantElements.length); participantElements[randomIndex].classList.add('highlight'); }, 100); setTimeout(() => { clearInterval(rouletteInterval); const winnerIndex = Math.floor(Math.random() * participantesSorteio.length); const winner = participantesSorteio[winnerIndex]; participantElements.forEach(el => el.classList.remove('highlight')); participantElements[winnerIndex].classList.add('highlight'); winnerDisplay.innerHTML = `<div class="winner-card text-center"><p class="text-sm text-slate-500">O VENCEDOR É...</p><p class="text-3xl font-bold text-indigo-700 my-2">${winner.nome}</p><p class="text-lg text-slate-600">${winner.telefone}</p></div>`; sorteioPlaceholder.classList.add('hidden'); winnerDisplay.classList.remove('hidden'); drawButton.disabled = false; drawButton.textContent = 'SORTEAR NOVAMENTE'; }, 4000); } async function updateStatus(id, newStatus) { try { await fetch(`${API_URL}/reclamacoes/${id}/status`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: newStatus }) }); fetchData(); } catch (error) { console.error("Erro ao atualizar status:", error); } } function addStatusChangeListeners() { document.querySelectorAll('.status-select').forEach(select => { select.addEventListener('change', (event) => { updateStatus(parseInt(event.target.dataset.id), event.target.value); }); }); } fetchData(); setInterval(fetchData, 5000); drawButton.addEventListener('click', realizarSorteio); });</script></body></html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    print("===================================================")
    print("🚀 Servidor do Painel (Modo Meta API) iniciado!")
    print("Acesse o painel em: http://127.0.0.1:5000")
    print("Use o ngrok ou Render para criar um link público para o webhook da Meta.")
    print("===================================================")
    app.run(host='0.0.0.0', port=5000, debug=False)