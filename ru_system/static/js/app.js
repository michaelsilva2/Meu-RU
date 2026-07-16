/**
 * app.js — Funções utilitárias do sistema RU
 */

// ─── Toast flutuante (padrão de notificação — cartão no canto superior direito) ─

document.addEventListener('DOMContentLoaded', function() {
    const msgToast = document.getElementById('msg-toast');
    if (!msgToast) return;

    requestAnimationFrame(() => {
        const bar = document.getElementById('toast-bar');
        if (bar) bar.style.width = '0%';
    });

    if (!msgToast.dataset.persist) {
        setTimeout(() => {
            msgToast.style.transition = 'opacity .35s, transform .35s';
            msgToast.style.opacity = '0';
            msgToast.style.transform = 'translateX(24px)';
            setTimeout(() => msgToast.remove(), 380);
        }, 4000);
    }
});

function mostrarAlertaFlutuante(iconePath, titulo, subtitulo) {
    const id = 'msg-toast-flutuante-' + Date.now();
    const alerta = document.createElement('div');
    alerta.id = id;
    alerta.style.cssText = 'position:fixed;top:80px;right:24px;z-index:9999;width:310px;border-radius:18px;background:white;box-shadow:0 24px 60px rgba(0,0,0,0.13);overflow:hidden;animation:toastSlide .3s ease;';
    alerta.innerHTML = `
        <div style="display:flex;align-items:center;gap:14px;padding:16px 18px;">
            <div style="width:42px;height:42px;border-radius:13px;background:linear-gradient(135deg,#22C55E,#16A34A);display:flex;align-items:center;justify-content:center;flex-shrink:0;">
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="white" stroke-width="2.5">
                    <path stroke-linecap="round" stroke-linejoin="round" d="${iconePath}"/>
                </svg>
            </div>
            <div style="flex:1;min-width:0;">
                <p style="font-weight:800;font-size:14px;color:#111827;margin:0;letter-spacing:-0.01em;">${titulo}</p>
                <p style="font-size:12px;color:#9CA3AF;margin:3px 0 0;font-weight:500;">${subtitulo}</p>
            </div>
            <button onclick="this.closest('#${id}').remove()" style="background:none;border:none;cursor:pointer;color:#D1D5DB;font-size:20px;line-height:1;padding:0;flex-shrink:0;">&times;</button>
        </div>
    `;
    document.body.appendChild(alerta);
    setTimeout(() => alerta.remove(), 4000);
}


// ─── Custom select (troca a lista nativa do SO por um painel no estilo do site) ─

function _initCustomSelects() {
    document.querySelectorAll('select[data-custom]').forEach(sel => {
        if (sel.dataset.customReady) return;
        sel.dataset.customReady = '1';

        const wrap = document.createElement('div');
        wrap.className = 'custom-select-wrap';
        wrap.style.position = 'relative';
        if (sel.classList.contains('w-full')) {
            wrap.style.display = 'block';
            wrap.style.width = '100%';
        } else {
            wrap.style.display = 'inline-block';
        }

        sel.parentNode.insertBefore(wrap, sel);
        wrap.appendChild(sel);
        sel.classList.add('custom-select-native');

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = sel.className.replace('custom-select-native', '') + ' custom-select-btn';
        if (sel.classList.contains('w-full')) btn.style.width = '100%';
        wrap.appendChild(btn);

        const panel = document.createElement('div');
        panel.className = 'custom-select-panel hidden';
        wrap.appendChild(panel);

        function renderOptions() {
            panel.innerHTML = '';
            Array.from(sel.options).forEach(opt => {
                const item = document.createElement('div');
                item.className = 'custom-select-option' + (opt.value === sel.value ? ' selected' : '');
                item.textContent = opt.textContent;
                item.onclick = () => {
                    sel.value = opt.value;
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                    syncButton();
                    fecharPainel();
                };
                panel.appendChild(item);
            });
        }

        function syncButton() {
            const selecionada = sel.options[sel.selectedIndex];
            btn.innerHTML = '';
            const span = document.createElement('span');
            span.textContent = selecionada ? selecionada.textContent : '';
            btn.appendChild(span);
            btn.insertAdjacentHTML('beforeend', `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 custom-select-chevron" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>`);
            renderOptions();
        }

        function fecharPainel() {
            panel.classList.add('hidden');
            wrap.classList.remove('open');
        }

        btn.onclick = () => {
            const estavaAberto = !panel.classList.contains('hidden');
            document.querySelectorAll('.custom-select-panel').forEach(p => p.classList.add('hidden'));
            document.querySelectorAll('.custom-select-wrap').forEach(w => w.classList.remove('open'));
            if (!estavaAberto) {
                panel.classList.remove('hidden');
                wrap.classList.add('open');
            }
        };

        syncButton();
    });
}

document.addEventListener('DOMContentLoaded', _initCustomSelects);

document.addEventListener('click', (e) => {
    document.querySelectorAll('.custom-select-wrap').forEach(wrap => {
        if (!wrap.contains(e.target)) {
            wrap.classList.remove('open');
            const panel = wrap.querySelector('.custom-select-panel');
            if (panel) panel.classList.add('hidden');
        }
    });
});


// ─── Toasts ───────────────────────────────────────────────────────────────

function showToast(mensagem, tipo = 'info', duracao = 3500) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const cores = { sucesso: 'bg-green-600', erro: 'bg-red-600', aviso: 'bg-yellow-500', info: 'bg-blue-600' };
    const icones = { sucesso: '[OK]', erro: '[X]', aviso: '[!]', info: '[i]' };

    const toast = document.createElement('div');
    toast.className = `toast ${cores[tipo] || 'bg-gray-700'} text-white px-5 py-3 rounded-xl shadow-lg flex items-center gap-3 max-w-sm text-sm`;
    toast.innerHTML = `<span>${icones[tipo] || '[i]'}</span><span>${mensagem}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('saindo');
        setTimeout(() => toast.remove(), 300);
    }, duracao);
}


// ─── Loading em botões ────────────────────────────────────────────────────

/**
 * Mostra estado de loading no botão SEM desabilitá-lo antes do submit.
 * btn.disabled = true SÍNCRONO cancela o submit em alguns browsers.
 */
function setLoading(btn) {
    const textoEl = btn.querySelector('.btn-text');
    const loadingEl = btn.querySelector('.btn-loading');
    if (textoEl) textoEl.classList.add('hidden');
    if (loadingEl) loadingEl.classList.remove('hidden');

    // Desabilita APÓS o browser processar o submit (assíncrono)
    setTimeout(() => { btn.disabled = true; }, 100);
}


// ─── Toggle visibilidade de senha ─────────────────────────────────────────

function toggleSenha(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const isPassword = input.type === 'password';
    input.type = isPassword ? 'text' : 'password';

    const eyeOpen   = document.getElementById('eye-open-'   + inputId);
    const eyeClosed = document.getElementById('eye-closed-' + inputId);
    if (eyeOpen)   eyeOpen.classList.toggle('hidden', isPassword);
    if (eyeClosed) eyeClosed.classList.toggle('hidden', !isPassword);
}


// ─── Confirmação de ação (substitui o confirm() nativo do navegador) ──────

let _formPendenteConfirmacao = null;

function confirmarEnvio(form, mensagem) {
    _formPendenteConfirmacao = form;
    const texto = document.getElementById('modal-confirmacao-texto');
    if (texto) texto.textContent = mensagem;
    abrirModal('modal-confirmacao');
    return false;
}

function _confirmarConfirmacao() {
    fecharModal('modal-confirmacao');
    if (_formPendenteConfirmacao) {
        _formPendenteConfirmacao.submit();
        _formPendenteConfirmacao = null;
    }
}

function _fecharConfirmacao() {
    fecharModal('modal-confirmacao');
    _formPendenteConfirmacao = null;
}


// ─── Modais ───────────────────────────────────────────────────────────────

function abrirModal(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.remove('hidden');
        // Foca no primeiro input do modal
        setTimeout(() => {
            const input = modal.querySelector('input:not([type=hidden])');
            if (input) input.focus();
        }, 100);
    }
}

function fecharModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.add('hidden');
}


// ─── Validação customizada de inputs (substitui o balão nativo do browser) ─

function validarValorInput(input, mensagens = {}) {
    if (!input) return;
    const erroBox = document.getElementById(`erro-${input.id}`);
    const erroTexto = erroBox ? erroBox.querySelector('.erro-texto') : null;

    const marcarErro = (msg) => {
        if (erroTexto) erroTexto.textContent = msg;
        if (erroBox) erroBox.classList.remove('hidden');
        input.classList.add('border-red-300', 'ring-2', 'ring-red-100');
        input.classList.remove('border-gray-200');
    };

    const limparErro = () => {
        if (erroBox) erroBox.classList.add('hidden');
        input.classList.remove('border-red-300', 'ring-2', 'ring-red-100');
        input.classList.add('border-gray-200');
    };

    input.addEventListener('invalid', (e) => {
        e.preventDefault();
        let msg = mensagens.padrao || 'Valor inválido.';
        if (input.validity.valueMissing) msg = mensagens.obrigatorio || msg;
        else if (input.validity.rangeOverflow) msg = mensagens.maximo || msg;
        else if (input.validity.rangeUnderflow) msg = mensagens.minimo || msg;
        marcarErro(msg);
    });

    input.addEventListener('input', limparErro);
}

// Fecha modal ao clicar fora dele
document.addEventListener('click', function(e) {
    document.querySelectorAll('[id^="modal-"]').forEach(modal => {
        if (e.target === modal) modal.classList.add('hidden');
    });
});

// Fecha modal com Escape
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        document.querySelectorAll('[id^="modal-"]').forEach(m => m.classList.add('hidden'));
    }
});


// ─── Menu mobile (admin) ──────────────────────────────────────────────────

function toggleMenuMobile() {
    const menu = document.getElementById('menu-mobile');
    if (menu) menu.classList.toggle('hidden');
}


// ─── Dark Mode ────────────────────────────────────────────────────────────

function toggleDarkMode() {
    const html = document.documentElement;
    const isDark = html.classList.toggle('dark');
    localStorage.setItem('ru-theme', isDark ? 'dark' : 'light');
    document.querySelectorAll('.dm-icon').forEach(el => {
        el.innerHTML = isDark ? _iconSun() : _iconMoon();
    });
}

function _iconMoon() {
    return `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M21 12.79A9 9 0 1111.21 3a7 7 0 109.79 9.79z"/>
    </svg>`;
}

function _iconSun() {
    return `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364-6.364l-.707.707M6.343 17.657l-.707.707M17.657 17.657l-.707-.707M6.343 6.343l-.707-.707M12 8a4 4 0 100 8 4 4 0 000-8z"/>
    </svg>`;
}

// Sincroniza ícone ao carregar
document.addEventListener('DOMContentLoaded', function() {
    const isDark = document.documentElement.classList.contains('dark');
    document.querySelectorAll('.dm-icon').forEach(el => {
        el.innerHTML = isDark ? _iconSun() : _iconMoon();
    });
});


// ─── Validação customizada de campos obrigatórios ────────────────────────

function mostrarErroCampo(input, mensagem) {
    limparErroCampo(input);
    input.style.borderColor = '#F47920';
    input.style.boxShadow   = '0 0 0 3px #F4792022';

    const erro = document.createElement('p');
    erro.className = 'campo-erro flex items-center gap-1 mt-1.5 text-xs font-medium';
    erro.style.color = '#F47920';
    erro.innerHTML = `<svg class="w-3 h-3 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
        <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
    </svg>${mensagem}`;
    input.parentNode.appendChild(erro);

    input.addEventListener('input', () => limparErroCampo(input), { once: true });
}

function limparErroCampo(input) {
    input.style.borderColor = '';
    input.style.boxShadow   = '';
    input.parentNode.querySelectorAll('.campo-erro').forEach(e => e.remove());
}

function validarFormulario(form) {
    let valido = true;
    form.querySelectorAll('input[required], select[required], textarea[required]').forEach(input => {
        if (input.type === 'hidden') return;
        limparErroCampo(input);

        if (!input.value.trim()) {
            mostrarErroCampo(input, 'ESTE CAMPO É OBRIGATÓRIO');
            if (valido) input.focus();
            valido = false;
            return;
        }

        if (input.type === 'email') {
            const emailValido = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(input.value.trim());
            if (!emailValido) {
                mostrarErroCampo(input, 'INFORME UM E-MAIL VÁLIDO (EX: NOME@DOMINIO.COM)');
                if (valido) input.focus();
                valido = false;
            }
        }
    });
    return valido;
}

function _setupValidacao() {
    document.querySelectorAll('form[novalidate]').forEach(form => {
        if (form._validacaoSetup) return;
        form._validacaoSetup = true;
        form.addEventListener('submit', function(e) {
            if (!validarFormulario(form)) {
                e.preventDefault();
                e.stopImmediatePropagation();
            } else {
                const btn = form.querySelector('button[type="submit"]');
                if (btn) setLoading(btn);
            }
        });
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _setupValidacao);
} else {
    _setupValidacao();
}


// ─── Busca com debounce ───────────────────────────────────────────────────

let debounceTimer;
const campoBusca = document.getElementById('campo-busca');
if (campoBusca) {
    campoBusca.addEventListener('input', function() {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            // Submete o formulário pai após 300ms sem digitar
            const form = this.closest('form');
            if (form) form.submit();
        }, 500);
    });
}


// ─── Validação de formato de valor monetário ──────────────────────────────

document.querySelectorAll('input[name="valor"]').forEach(input => {
    input.addEventListener('input', function() {
        // Remove caracteres não numéricos exceto vírgula e ponto
        let val = this.value.replace(/[^0-9,\.]/g, '');
        this.value = val;
    });
});


// ─── Auto-remove mensagens de sucesso ─────────────────────────────────────

document.addEventListener('DOMContentLoaded', function() {
    // Remove mensagens de sucesso após 4 segundos
    document.querySelectorAll('[id^="msg-sucesso"]').forEach(el => {
        setTimeout(() => {
            el.style.transition = 'opacity 0.5s';
            el.style.opacity = '0';
            setTimeout(() => el.remove(), 500);
        }, 4000);
    });
});
