from flask import Flask, render_template_string, jsonify, request
import requests
from datetime import datetime, timedelta
import threading
import time
import pytz

app = Flask(__name__)

# =============================================================================
# ENGINE - HUNTER V28 PRO (APENAS INTERVALO BRANCO)
# =============================================================================
API_HISTORY = 'https://blaze.bet.br/api/singleplayer-originals/originals/roulette_games/recent/history/1?page=1&limit=200'
TZ_BR = pytz.timezone('America/Sao_Paulo')

ESTRAT_KEYS = ["Intervalo Branco"]

ESTADO = {
    'rodando': False,
    'sinais': [],
    'grid_continuo': [],
    'ultima': {'num': '--', 'cor': 'preto'},
    'stats_geral': {'wins': 0, 'loss': 0},
    'perf_estrategias': {k: {
        'wins': 0, 'loss': 0, 
        'cur_win_streak': 0, 'max_win_streak': 0, 
        'cur_loss_streak': 0, 'max_loss_streak': 0,
        'assertividade': 0
    } for k in ESTRAT_KEYS},
    'tg_token': '',
    'tg_chat_id': ''
}

class EngineHunter:
    def enviar_tg(self, msg):
        if ESTADO['tg_token'] and ESTADO['tg_chat_id']:
            try:
                url = f"https://api.telegram.org/bot{ESTADO['tg_token']}/sendMessage"
                r = requests.post(url, json={'chat_id': ESTADO['tg_chat_id'], 'text': msg, 'parse_mode': 'Markdown'}, timeout=5).json()
                return r['result']['message_id'] if r.get('ok') else None
            except: pass
        return None

    def editar_tg(self, msg_id, msg):
        if ESTADO['tg_token'] and ESTADO['tg_chat_id'] and msg_id:
            try:
                url = f"https://api.telegram.org/bot{ESTADO['tg_token']}/editMessageText"
                requests.post(url, json={'chat_id': ESTADO['tg_chat_id'], 'message_id': msg_id, 'text': msg, 'parse_mode': 'Markdown'}, timeout=5)
            except: pass

    def atualizar_perf(self, tipo_str, resultado):
        if tipo_str in ESTADO['perf_estrategias']:
            p = ESTADO['perf_estrategias'][tipo_str]
            if resultado == 'win':
                p['wins'] += 1
                p['cur_win_streak'] += 1
                p['cur_loss_streak'] = 0
                if p['cur_win_streak'] > p['max_win_streak']: p['max_win_streak'] = p['cur_win_streak']
                ESTADO['stats_geral']['wins'] += 1
            else:
                p['loss'] += 1
                p['cur_loss_streak'] += 1
                p['cur_win_streak'] = 0
                if p['cur_loss_streak'] > p['max_loss_streak']: p['max_loss_streak'] = p['cur_loss_streak']
                ESTADO['stats_geral']['loss'] += 1
            
            total = p['wins'] + p['loss']
            p['assertividade'] = round((p['wins'] / total) * 100, 1) if total > 0 else 0

    def processar_dados(self):
        try:
            res = requests.get(API_HISTORY, timeout=10).json()
            recs = res.get('records', [])
            if not recs: return
            agora_br = datetime.now(TZ_BR)
            ESTADO['ultima'] = {'num': recs[0]['roll'], 'cor': 'branco' if recs[0]['color'] == 0 else 'vermelho' if recs[0]['color'] == 1 else 'preto'}
            
            # --- ESTRATÉGIA ÚNICA: INTERVALO BRANCO ---
            brancos_raw = [r for r in recs if r['color'] == 0]
            
            if len(brancos_raw) >= 3:
                b1_dt = datetime.strptime(brancos_raw[0]['created_at'], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=pytz.UTC).astimezone(TZ_BR)
                b2_dt = datetime.strptime(brancos_raw[1]['created_at'], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=pytz.UTC).astimezone(TZ_BR)
                b3_dt = datetime.strptime(brancos_raw[2]['created_at'], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=pytz.UTC).astimezone(TZ_BR)

                # Validação: Intervalo entre o penúltimo e o antepenúltimo >= 11 min
                diff_validacao = (b2_dt - b3_dt).total_seconds() / 60
                if diff_validacao >= 11:
                    # Intervalo do sinal: Entre o último e o penúltimo
                    diff_sinal = int((b1_dt - b2_dt).total_seconds() / 60)
                    
                    if diff_sinal > 0:
                        alvo1 = b1_dt.replace(second=0, microsecond=0) + timedelta(minutes=diff_sinal)
                        alvo2 = alvo1 + timedelta(minutes=diff_sinal)
                        
                        self.gerar_sinal(alvo1, agora_br, "Intervalo Branco")
                        self.gerar_sinal(alvo2, agora_br, "Intervalo Branco")

            ESTADO['grid_continuo'] = self.montar_grid(recs)
            self.verificar_vitoria_derrota(recs, agora_br)
        except Exception as e:
            print(f"Erro Engine: {e}")

    def gerar_sinal(self, alvo_dt, agora, tipo):
        if alvo_dt <= agora or (alvo_dt - agora).total_seconds() > 3600: return
        horario_str = alvo_dt.strftime("%H:%M")
        
        existente = next((s for s in ESTADO['sinais'] if s['horario'] == horario_str and s['tipo'] == tipo and not s['finalizado']), None)
        if not existente:
            cob = [(alvo_dt + timedelta(minutes=j)).strftime("%H:%M") for j in range(-1, 2)]
            ESTADO['sinais'].append({
                'horario': horario_str, 'alvo_dt': alvo_dt, 'fim_dt': alvo_dt + timedelta(minutes=2),
                'cobertura': cob, 'status': 'wait', 'finalizado': False,
                'alertado': False, 'msg_id': None, 'tipo': tipo
            })

    def verificar_vitoria_derrota(self, recs, agora):
        for s in ESTADO['sinais'][:]:
            if s['finalizado']: continue
            restante = (s['fim_dt'] - agora).total_seconds()
            
            if 0 < (s['alvo_dt'] - agora).total_seconds() <= 120 and not s['alertado']:
                s['msg_id'] = self.enviar_tg(f"🚨 *ENTRADA CONFIRMADA*\n🎯 Alvo: ⚪️*{s['horario']}*\n💡 Estratégia: {s['tipo']}\n🕒 Janela: {s['cobertura'][0]} a {s['cobertura'][2]}")
                s['alertado'] = True

            win = any(dt_r.strftime("%H:%M") in s['cobertura'] and int(r['roll']) == 0 
                     for r, dt_r in [(r, datetime.strptime(r['created_at'], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=pytz.UTC).astimezone(TZ_BR)) for r in recs])
            
            if win:
                s['status'] = 'win'; s['finalizado'] = True
                self.atualizar_perf(s['tipo'], 'win')
                if s['msg_id']: self.editar_tg(s['msg_id'], f"🚨 *FINALIZADO*\n🎯 Alvo: ⚪️*{s['horario']}*\n✅ *WIN!* ({s['tipo']})")
            elif restante < -60:
                s['status'] = 'loss'; s['finalizado'] = True
                self.atualizar_perf(s['tipo'], 'loss')
                if s['msg_id']: self.editar_tg(s['msg_id'], f"🚨 *FINALIZADO*\n🎯 Alvo: ⚪️*{s['horario']}*\n❌ *LOSS* ({s['tipo']})")

    def montar_grid(self, recs):
        blocos = {}
        for r in reversed(recs):
            dt_br = datetime.strptime(r['created_at'], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=pytz.UTC).astimezone(TZ_BR)
            chave = dt_br.strftime("%H:%M")[:-1] 
            if chave not in blocos: blocos[chave] = {i: [] for i in range(10)}
            col = dt_br.minute % 10
            if len(blocos[chave][col]) < 2: 
                blocos[chave][col].append({'n': r['roll'], 'c': 'branco' if r['color'] == 0 else 'vermelho' if r['color'] == 1 else 'preto', 'h': dt_br.strftime("%H:%M")})
        return [{'label': k, 'minutos': blocos[k]} for k in sorted(blocos.keys(), reverse=True)]

hunter = EngineHunter()

HTML_UI = '''
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hunter Pro v28 | Intervalo Branco</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;900&family=Plus+Jakarta+Sans:wght@400;800&display=swap');
        body { background: #010206; color: #f8fafc; font-family: 'Plus Jakarta Sans', sans-serif; overflow-x: hidden; }
        .container-90 { width: 90%; max-width: 1800px; margin: 0 auto; }
        .glass { background: rgba(255, 255, 255, 0.01); backdrop-filter: blur(15px); border: 1px solid rgba(255, 255, 255, 0.03); border-radius: 12px; }
        .font-cyber { font-family: 'Orbitron', sans-serif; }
        .grid-main { display: grid; grid-template-columns: repeat(10, minmax(90px, 1fr)); gap: 6px; min-width: 900px; }
        .cell-pair { background: rgba(255,255,255,0.01); padding: 5px; border-radius: 8px; display: grid; grid-template-columns: 1fr 1fr; gap: 4px; position: relative; padding-bottom: 20px; border: 1px solid rgba(255,255,255,0.01); }
        .sq { width: 100%; aspect-ratio: 1/1; border-radius: 4px; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 900; }
        .sq-v { background: linear-gradient(135deg, #ef4444, #7f1d1d); }
        .sq-p { background: linear-gradient(135deg, #334155, #0f172a); }
        .sq-b { background: #fff; color: #000; box-shadow: 0 0 10px #fff; }
        .time-label { font-size: 8px; color: #475569; text-align: center; margin-top: 2px; font-weight: 800; }
        .status-badge { position: absolute; bottom: 2px; width: 100%; text-align: center; font-size: 7px; font-weight: 900; }
        .is-signal { border: 1px solid #3b82f6 !important; background: rgba(59, 130, 246, 0.05) !important; }
        .custom-scroll::-webkit-scrollbar { width: 3px; }
        .custom-scroll::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 10px; }
    </style>
</head>
<body class="p-4 md:p-6">
    <div class="container-90 space-y-6">
        <header class="glass p-4 flex justify-between items-center">
            <div>
                <h1 class="font-cyber text-lg font-black tracking-tighter">HUNTER <span class="text-blue-500">PRO</span> <span class="text-slate-500">V28</span></h1>
            </div>
            <div class="flex gap-2">
                <button onclick="control('iniciar')" id="btnP" class="bg-blue-600 px-5 py-2 rounded-lg font-black text-[10px] uppercase">Iniciar Sistema</button>
                <button onclick="openModal('modalHist')" class="glass px-4 py-2 rounded-lg text-[10px] font-black text-slate-400">Histórico</button>
                <button onclick="openModal('modalTg')" class="glass p-2 px-3 rounded-lg text-blue-400"><i class="fab fa-telegram-plane"></i></button>
            </div>
        </header>

        <div id="estratGrid" class="flex justify-center">
            <!-- Renderizado dinamicamente via JS -->
        </div>

        <section class="glass p-5 overflow-x-auto">
            <div class="flex justify-between items-center mb-4">
                <h2 class="text-[9px] font-black uppercase text-slate-500 tracking-widest">Monitoramento Estrutural</h2>
                <div class="flex gap-4">
                    <div class="text-[10px] font-black"><span class="text-emerald-500">WINS:</span> <span id="st-w">0</span></div>
                    <div class="text-[10px] font-black"><span class="text-red-500">LOSS:</span> <span id="st-l">0</span></div>
                    <div id="relogio" class="text-[10px] font-cyber text-slate-500">00:00:00</div>
                </div>
            </div>
            <div class="grid-main" id="gridBody"></div>
        </section>
    </div>

    <div id="modalHist" class="hidden fixed inset-0 bg-black/90 flex items-center justify-center z-[200] p-4">
        <div class="glass p-6 w-full max-w-md max-h-[70vh] flex flex-col">
            <h3 class="font-cyber text-[10px] mb-6 text-blue-500 text-center uppercase tracking-widest">Últimas Entradas</h3>
            <div id="histLista" class="space-y-2 overflow-y-auto custom-scroll flex-1 pr-1"></div>
            <button onclick="closeModal('modalHist')" class="w-full mt-6 py-4 glass text-[9px] text-slate-400 font-black uppercase">Voltar</button>
        </div>
    </div>

    <div id="modalTg" class="hidden fixed inset-0 bg-black/95 flex items-center justify-center z-[200]">
        <div class="glass p-8 w-80 text-center">
            <h3 class="text-white text-[10px] font-black uppercase mb-4">Configurar Telegram</h3>
            <input id="tk" type="password" placeholder="Bot Token" class="w-full bg-white/5 border border-white/10 p-3 rounded-lg mb-2 text-xs text-white outline-none">
            <input id="cid" type="text" placeholder="Chat ID" class="w-full bg-white/5 border border-white/10 p-3 rounded-lg mb-4 text-xs text-white outline-none">
            <button onclick="saveTg()" class="w-full bg-blue-600 py-3 rounded-lg font-black text-[10px] uppercase">Salvar Config</button>
            <button onclick="closeModal('modalTg')" class="w-full mt-2 text-[8px] text-slate-500 uppercase font-black">Cancelar</button>
        </div>
    </div>

    <script>
        const openModal = id => document.getElementById(id).classList.remove('hidden');
        const closeModal = id => document.getElementById(id).classList.add('hidden');

        function update() {
            fetch('/dados').then(r => r.json()).then(d => {
                document.getElementById('st-w').innerText = d.stats_geral.wins;
                document.getElementById('st-l').innerText = d.stats_geral.loss;
                document.getElementById('relogio').innerText = new Date().toLocaleTimeString('pt-BR');

                let estratHtml = '';
                for (const [nome, p] of Object.entries(d.performance)) {
                    const sinaisEstrat = d.sinais
                        .filter(s => s.tipo === nome && !s.finalizado)
                        .sort((a, b) => a.horario.localeCompare(b.horario))
                        .slice(0, 2);

                    estratHtml += `
                    <div class="glass p-4 border border-white/5 flex flex-col space-y-4 w-full max-w-sm">
                        <div class="flex justify-between items-start">
                            <div>
                                <h4 class="text-[9px] font-black uppercase text-blue-500 leading-none">${nome}</h4>
                                <span class="text-xl font-cyber text-white">${p.assertividade}%</span>
                            </div>
                            <div class="text-right">
                                <span class="block text-[7px] text-slate-500 font-black uppercase">Seq. Atual</span>
                                <span class="text-[10px] font-cyber ${p.cur_win_streak > 0 ? 'text-emerald-500' : 'text-red-500'}">
                                    ${p.cur_win_streak > 0 ? 'W'+p.cur_win_streak : 'L'+p.cur_loss_streak}
                                </span>
                            </div>
                        </div>

                        <div class="space-y-2">
                            ${sinaisEstrat.map(s => `
                                <div class="bg-blue-600/10 border border-blue-600/20 p-2 rounded-lg relative overflow-hidden">
                                    <div class="flex justify-between items-center">
                                        <span class="text-[14px] font-cyber text-white">${s.horario}</span>
                                        <span class="text-[7px] font-black text-blue-400 uppercase">Confirmado</span>
                                    </div>
                                    <div class="text-[7px] text-slate-500 font-bold uppercase mt-1">Janela: ${s.cobertura[0]}-${s.cobertura[2]}</div>
                                </div>
                            `).join('') || '<div class="text-[8px] text-slate-700 font-black uppercase text-center py-4 italic border border-dashed border-white/5 rounded-lg">Buscando Padrão...</div>'}
                        </div>

                        <div class="grid grid-cols-2 gap-2 border-t border-white/5 pt-3">
                            <div class="text-center"><span class="block text-[7px] text-slate-500 uppercase">Wins</span><span class="text-[10px] font-bold text-emerald-500">${p.wins}</span></div>
                            <div class="text-center"><span class="block text-[7px] text-slate-500 uppercase">Loss</span><span class="text-[10px] font-bold text-red-500">${p.loss}</span></div>
                            <div class="text-center"><span class="block text-[7px] text-slate-500 uppercase">Max Win</span><span class="text-[10px] font-bold text-emerald-400">${p.max_win_streak}</span></div>
                            <div class="text-center"><span class="block text-[7px] text-slate-500 uppercase">Max Loss</span><span class="text-[10px] font-bold text-red-400">${p.max_loss_streak}</span></div>
                        </div>
                    </div>`;
                }
                document.getElementById('estratGrid').innerHTML = estratHtml;

                const finalizados = d.sinais.filter(s => s.finalizado).reverse().slice(0, 15);
                document.getElementById('histLista').innerHTML = finalizados.map(s => `
                    <div class="flex justify-between items-center p-3 bg-white/5 rounded-xl border-l-2 ${s.status === 'win' ? 'border-emerald-500' : 'border-red-500'}">
                        <div><div class="text-[7px] font-black text-slate-500 uppercase">${s.tipo}</div><div class="text-lg font-cyber">${s.horario}</div></div>
                        <div class="font-black text-[10px] ${s.status === 'win' ? 'text-emerald-500' : 'text-red-500'}">${s.status.toUpperCase()}</div>
                    </div>
                `).join('');

                renderGrid(d.grid, d.sinais);
            });
        }

        function renderGrid(grid, sinais) {
            let html = '';
            grid.forEach(bloco => {
                for (let i = 0; i < 10; i++) {
                    const recs = bloco.minutos[i] || [], h = bloco.label.split(':')[0], m = (parseInt(bloco.label.split(':')[1]) * 10) + i;
                    const tempo = `${h}:${m.toString().padStart(2, '0')}`;
                    const sinalAtivo = sinais.find(s => s.horario === tempo && !s.finalizado);
                    const sinalFinalizado = sinais.find(s => s.horario === tempo && s.finalizado);
                    html += `<div class="cell-pair ${sinalAtivo ? 'is-signal' : ''}">
                        ${renderItem(recs[0])}${renderItem(recs[1])}
                        ${sinalFinalizado ? `<div class="status-badge ${sinalFinalizado.status === 'win' ? 'text-emerald-500' : 'text-red-500'}">${sinalFinalizado.status.toUpperCase()}</div>` : ''}
                    </div>`;
                }
            });
            document.getElementById('gridBody').innerHTML = html;
        }

        function renderItem(r) {
            if(!r) return `<div><div class="sq bg-white/5"></div><div class="time-label">--:--</div></div>`;
            return `<div><div class="sq ${r.c === 'branco' ? 'sq-b' : r.c === 'vermelho' ? 'sq-v' : 'sq-p'}">${r.n}</div><div class="time-label">${r.h}</div></div>`;
        }

        function control(a) { fetch('/controle/'+a); document.getElementById('btnP').innerText = "BOT OPERANDO"; document.getElementById('btnP').classList.replace('bg-blue-600', 'bg-emerald-600'); }
        function saveTg() {
            fetch('/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({token: document.getElementById('tk').value, chat_id: document.getElementById('cid').value})});
            closeModal('modalTg');
        }
        setInterval(update, 3000);
    </script>
</body>
</html>
'''

@app.route('/')
def index(): return render_template_string(HTML_UI)

@app.route('/dados')
def dados():
    return jsonify({
        'sinais': ESTADO['sinais'], 'ultima': ESTADO['ultima'], 
        'stats_geral': ESTADO['stats_geral'], 'performance': ESTADO['perf_estrategias'],
        'grid': ESTADO['grid_continuo']
    })

@app.route('/config', methods=['POST'])
def config():
    d = request.json
    ESTADO['tg_token'], ESTADO['tg_chat_id'] = d['token'], d['chat_id']
    return jsonify({'ok': True})

@app.route('/controle/<acao>')
def controle(acao):
    if acao == 'iniciar' and not ESTADO['rodando']:
        ESTADO['rodando'] = True
        def loop():
            while ESTADO['rodando']:
                hunter.processar_dados(); time.sleep(10)
        threading.Thread(target=loop, daemon=True).start()
    return jsonify({'ok': True})

if __name__ == '__main__':
    app.run(debug=False, port=5000, host='0.0.0.0')
