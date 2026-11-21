# Projeto: Mouse Remoto via PWA e Python (Suporte a Múltiplos Monitores)

> **Status:** Fases 1 a 4 implementadas (nov/2026). Este documento descreve o sistema **como foi construído**; as decisões que mudaram em relação à especificação original estão marcadas com **(revisado)**.

## 1. Visão Geral e Objetivo
Sistema leve, de baixa latência e sem instalação de aplicativo nativo no smartphone, para controlar o ponteiro do mouse, cliques, rolagem, teclado e mídia de um PC Desktop conectado a uma TV (ou a múltiplos monitores).

* **Backend (Host no PC):** servidor Python que serve o PWA, recebe eventos em tempo real via WebSocket e injeta *input* no sistema operacional. Roda com terminal ou em segundo plano com ícone na bandeja.
* **Frontend (Cliente no Celular):** PWA que funciona como *touchpad* virtual com gestos multitoque, troca/trava de monitor, teclas de mídia e teclado.

---

## 2. O Impacto de Ter Duas Telas (Monitor + TV)

### A. Movimento Relativo ($\Delta x, \Delta y$) vs. Coordenadas Absolutas ($x, y$)
* **Problema:** mapear a tela do celular para a área de trabalho combinada (ex.: $3840 \times 1080$) distorce a proporção, e escalas de DPI diferentes (100% / 150%) causam saltos.
* **Solução:** o celular envia apenas o **deslocamento relativo**, e o cursor atravessa a borda entre as telas naturalmente.
* **(revisado) Frações de pixel:** o servidor acumula o resto fracionário entre mensagens (`dx: 0.3` dez vezes = 3 px). Sem isso, movimentos lentos — justamente os de precisão — ficavam travados. O mesmo vale para a rolagem.
* **(revisado) DPI-awareness:** o processo chama `SetProcessDpiAwareness(2)` (per-monitor) antes de qualquer outra coisa. Sem isso, o Windows "virtualiza" as coordenadas em monitores com escala ≠ 100%, e o teleporte/trava erram a posição. Confirmado no PC de testes (monitor em 150%).

### B. O Cursor "Perdido" na Outra Tela
* **Solução:** botões **"Tela 1" / "Tela 2"** no PWA que teleportam o cursor para o centro exato do monitor escolhido.
* **(revisado) Identificação por nome, não por índice:** cada monitor é identificado pelo nome do sistema (ex.: `\\.\DISPLAY2`). A ordem dos índices muda quando a TV é desligada/religada no HDMI; o nome não.
* **(revisado) Ordem dos botões:** da esquerda para a direita, conforme a posição física configurada no Windows.
* **(revisado) Nomes personalizados:** segurar um botão de tela permite renomeá-lo (ex.: "TV"). O nome fica salvo no celular.

### C. Trava de Limite de Tela (Monitor Lock)
* Botão **"🔓 Travar"**: prende o cursor nos limites $(X_{min}, Y_{min}, X_{max}, Y_{max})$ da tela selecionada. Se nenhuma tela foi selecionada, trava na tela onde o cursor está.
* **(revisado)** Teleportar para outra tela com a trava ligada leva a trava junto.
* **(revisado)** A lista de monitores é relida a cada 3 s. Se a tela travada desaparecer (TV desligada), a trava é solta e o celular vibra.
* **(revisado)** A trava desejada é reaplicada automaticamente após reconexão.
* A trava é **por conexão** e afeta só o controle pelo celular; o mouse físico continua livre.

---

## 3. Arquitetura do Sistema

```text
+-----------------------------------+          Wi-Fi (LAN)          +-----------------------------------+
|      SMARTPHONE (Cliente PWA)     | <===========================> |       PC DESKTOP (Host Python)    |
|                                   |  HTTP ou HTTPS (arquivos)     |                                   |
|  - Touch Events + gestos          |  WS ou WSS (eventos JSON)     |  - FastAPI / Uvicorn              |
|  - Aceleração e envio por quadro  |  Token na URL (QR Code)       |  - Monitores (screeninfo)         |
|  - Telas, trava, mídia, teclado   |                               |  - Input (pynput)                 |
|  - Service Worker + Wake Lock     |                               |  - CA local (cryptography)        |
+-----------------------------------+                               |  - Bandeja + autostart (pystray)  |
                                                                    +-----------------------------------+
```

---

## 4. Stack Tecnológica

### Backend (Python 3.10+; testado no 3.15, Windows 10)
| Pacote | Uso |
|---|---|
| `fastapi` + `uvicorn[standard]` | Arquivos do PWA e WebSocket na mesma porta |
| `pynput` | Mouse (movimento, cliques, rolagem fracionária) e teclado (texto, teclas, mídia) |
| `screeninfo` | Monitores: nome, posição, resolução |
| `qrcode` | QR Code no terminal e na janela da bandeja |
| `cryptography` | CA local e certificado do servidor (modo `--https`) |
| `pystray` + `pillow` | Ícone na bandeja, janela do QR Code (tkinter) |

Versões fixadas em `server/requirements.txt`.

### Frontend (Vanilla JS, sem build)
* HTML5 / CSS3 / JavaScript ES2020.
* `manifest.json` + `sw.js` (Service Worker "rede primeiro") para instalar como app *standalone*.
* Touch Events (gestos) e Pointer Events (botões).
* Screen Wake Lock API, Fullscreen API, `navigator.vibrate` (retorno tátil no Android).

---

## 5. Protocolo de Comunicação (WebSocket)

Conexão: `ws(s)://<ip>:<porta>/ws?token=<token>`. Mensagens em JSON compacto.

### Celular → PC
```json
// Movimento relativo — agrupado e enviado no máximo 1x por quadro (requestAnimationFrame)
{ "type": "move", "dx": 14.5, "dy": -6.0 }

// Cliques — "down"/"up" permitem arrastar (revisado)
{ "type": "click", "button": "left", "action": "tap" }    // button: left | right | middle
{ "type": "click", "button": "left", "action": "down" }   // action: tap | down | up
{ "type": "click", "button": "left", "action": "up" }

// Rolagem em entalhes de roda, aceita frações (rolagem suave). dy > 0 = para cima
{ "type": "scroll", "dx": 0, "dy": -0.35 }

// Monitores — identificados pelo nome do sistema (revisado)
{ "type": "get_monitors" }
{ "type": "focus_monitor", "monitor": "\\\\.\\DISPLAY2" }
{ "type": "lock_monitor", "enabled": true, "monitor": "\\\\.\\DISPLAY2" }  // monitor opcional
{ "type": "lock_monitor", "enabled": false }

// Mídia do sistema
{ "type": "media", "command": "play_pause" }  // next, prev, volume_up, volume_down, mute

// Teclas (novo) — space e f controlam players web (YouTube/Netflix)
{ "type": "key", "key": "space" }  // enter, backspace, esc, tab, left, right, up, down, f

// Texto digitado no teclado do celular (novo) — máx. 500 caracteres por mensagem
{ "type": "text", "value": "matrix" }

// Medição de latência (novo)
{ "type": "ping", "t": 12345.6 }

// Apps do PC (PWAs do Chrome/Edge) (novo)
{ "type": "get_apps" }
{ "type": "launch_app", "app": "agimnkijcaahngcdmfeangaknmldooml", "monitor": "\\\\.\\DISPLAY2" }  // monitor opcional
```

### PC → Celular (novo)
```json
{ "type": "hello" }
{ "type": "monitors", "list": [
    { "id": "\\\\.\\DISPLAY2", "x": -1600, "y": 174, "w": 1600, "h": 900, "primary": false },
    { "id": "\\\\.\\DISPLAY1", "x": 0, "y": 0, "w": 1920, "h": 1080, "primary": true }
] }
{ "type": "lock_state", "enabled": true, "monitor": "\\\\.\\DISPLAY1" }
{ "type": "pong", "t": 12345.6 }
{ "type": "apps", "list": [ { "id": "agimnkijcaahngcdmfeangaknmldooml", "name": "YouTube", "icon": "data:image/png;base64,…" } ] }
{ "type": "app_launched", "app": "agimnkijcaahngcdmfeangaknmldooml", "ok": true, "placed": true }
```
Ao conectar, o servidor envia `hello`, `monitors`, `lock_state` e `apps`. `monitors` é reenviado sempre que a configuração de telas muda.

### Códigos de fechamento
| Código | Significado | Comportamento do app |
|---|---|---|
| `4401` | Token inválido ou trocado | Para de reconectar, apaga o token salvo, pede novo QR/token |
| `1008` | `Origin` de outro site | Conexão recusada no handshake |
| outros | Queda de rede, PC desligado | Reconecta com espera crescente (0,5 s → 5 s) |

### Validação no servidor
* Mensagens que não são JSON ou objeto são ignoradas.
* Valores numéricos inválidos/infinitos viram 0 e são limitados (2000 px por `move`, 50 entalhes por `scroll`).
* Comandos de tecla/mídia desconhecidos são ignorados (lista fechada).
* **Ao desconectar, todos os botões pressionados são soltos** — evita botão "preso" se o Wi-Fi cair no meio de um arraste.

---

## 6. Estrutura de Diretórios

```text
mouse_remoto/
├── docs/
│   └── especifica_o_de_projeto_mouse_remoto_com_python_e_pwa.md
├── server/
│   ├── main.py              # Ponto de entrada: argumentos, FastAPI, WebSocket, token
│   ├── input_controller.py  # pynput: movimento/rolagem fracionários, cliques, teclado, mídia, DPI
│   ├── monitor_manager.py   # screeninfo: lista, centro, clamp da trava
│   ├── apps.py              # PWAs instalados: leitura de .lnk, ícones, abrir e maximizar na tela
│   ├── certs.py             # CA local + certificado do servidor (--https)
│   ├── tray.py              # Ícone na bandeja + janela do QR Code
│   ├── autostart.py         # Iniciar com o Windows (registro HKCU\...\Run)
│   ├── requirements.txt
│   ├── token.txt            # (gerado) token de acesso — não versionar
│   ├── certs/               # (gerado) ca.key, ca.crt, server.key, server.crt — não versionar
│   └── mouse_remoto.log     # (gerado) log do modo bandeja — não versionar
├── web/
│   ├── index.html
│   ├── styles.css
│   ├── app.js               # Gestos, WebSocket, reconexão, telas, teclado, SW
│   ├── manifest.json
│   ├── sw.js
│   ├── setup.html           # Página de instalação do certificado (servidor HTTP porta+1)
│   └── icons/               # icon-192, icon-512, icon-maskable-512, apple-touch-icon
├── tools/
│   └── make_icons.py        # Regenera os ícones (Pillow)
└── .gitignore
```

---

## 7. Interface e Gestos (UX no Celular)

### Touchpad (área central)
| Gesto | Ação |
|---|---|
| Deslizar 1 dedo | Move o ponteiro, com aceleração (lento = preciso, rápido = atravessa a TV) |
| Toque rápido 1 dedo | Clique esquerdo (**imediato**, sem esperar por toque duplo) |
| Dois toques rápidos | Duplo clique |
| Toque + tocar de novo e arrastar | Segura o clique esquerdo (arrastar barra de progresso) |
| Toque rápido 2 dedos | Clique direito |
| Deslizar 2 dedos | Rolagem vertical e horizontal ("natural", como no celular) |

**(revisado)** O clique por toque é enviado na hora. A alternativa (esperar ~250 ms para descobrir se vem um segundo toque) deixaria todos os cliques com atraso. O custo: ao arrastar, um clique simples acontece antes no mesmo ponto — inofensivo em barras de progresso.

Parâmetros (em `web/app.js`): toque ≤ 250 ms e ≤ 8 px; janela de arraste 300 ms; 30 px de dedo = 1 entalhe de rolagem; ganho = `sensibilidade × (0,6 + 1,2 × min(velocidade, 3))`.

### Barra superior
* Status (`Conectado` / `Reconectando…` / `Acesso negado`) e latência em ms.
* Sensibilidade (0,5 a 4; salva no celular).
* Tela cheia (Fullscreen API; oculto onde não há suporte, como no iPhone).

### Apps do PC (botão ▦ na barra superior) (novo)
* Painel em tela cheia com a grade dos PWAs instalados no PC (ícone + nome). Ao abrir, pede `get_apps` para atualizar a lista.
* **Toque:** `launch_app` para a tela selecionada na barra de telas; o status mostra "Abrindo <app>…" até chegar `app_launched`. **Segurar:** renomear (salvo no celular).
* **Descoberta** (`server/apps.py`): atalhos `.lnk` do menu Iniciar (usuário e todos os usuários) cujos argumentos contêm `--app-id=` (Chrome e Edge instalam PWAs assim, via `chrome_proxy.exe`/`msedge_proxy.exe`). O `.lnk` é lido em Python puro (formato MS-SHLLINK), sem `pywin32`. O ícone `.ico` vira PNG de 96 px (Pillow), com cache.
* **Posicionamento:** antes de abrir, o servidor guarda as janelas do Chrome/Edge visíveis; depois espera **até 20 s** por uma janela nova (ou por uma janela do navegador que passe a ficar em primeiro plano). Encontrada, ela é restaurada, movida para o monitor de destino e maximizada (`SetWindowPos` + `ShowWindow`), e o cursor vai para o centro desse monitor. Monitor de destino: o pedido → a tela travada → a tela onde está o cursor.
* **Medido:** o Chrome leva de ~5 s a ~9 s para mostrar a janela de um PWA neste PC. Uma versão anterior, com 8 s de limite e uma busca pelo título como último recurso, chegou a mover a janela errada; a busca pelo título foi removida: sem uma janela identificada com certeza, nada é movido (`placed: false`).
* Chamar o mesmo app de novo abre **outra** janela (comportamento do Chrome para esses PWAs).

### Barra de telas (só aparece com 2+ monitores)
* `[Tela 1] [Tela 2] … [🔓 Travar]` — ver seção 2.

### Barra inferior
| Linha | Botões |
|---|---|
| Teclas | `Esc` · `Espaço` · `F` · `⌨` (abre o teclado do celular) |
| Mídia | ⏮ ⏯ ⏭ 🔉 🔇 🔊 (segurar volume = repetir) |
| Mouse | `Esquerdo` · `Direito` (segurar = manter pressionado) |

**(revisado) Play/Pause:** a especificação original misturava a tecla de mídia com o `Espaço`. Agora são botões separados: **⏯** é a tecla de mídia do sistema (Spotify, players do Windows, funciona em segundo plano); **Espaço** e **F** são teclas comuns para players web, que precisam da janela em foco.

**Teclado:** o texto vai para um `<input>` invisível; a cada mudança o app compara com o valor anterior e envia só a diferença (backspaces + texto novo). Isso funciona com autocorretor e teclados que "compõem" palavras (Gboard), onde `keydown` chega sem a tecla real (`keyCode 229`). Enter é enviado como tecla.

---

## 8. Como Executar

```bash
pip install -r server/requirements.txt

# Com terminal (QR Code impresso no terminal)
python server/main.py                 # HTTP
python server/main.py --https         # HTTPS (instalar como app + tela sempre ligada)

# Em segundo plano, com ícone na bandeja
pythonw server/main.py --tray --https
```

| Opção | Efeito |
|---|---|
| `--https` | Serve via HTTPS com a CA local |
| `--tray` | Sem terminal; ícone na bandeja |
| `--port N` | Porta (padrão 8000) |
| `--host H` | Interface (padrão `0.0.0.0`) |
| `--new-token` | Gera um novo token na inicialização |

### Menu da bandeja
* **Mostrar QR Code** (também com clique no ícone) — atualiza sozinho se o IP ou o token mudarem.
* **Instalar certificado no celular** (só com `--https`) — mesma janela, com o QR da página de instalação; um botão alterna entre os dois QR Codes.
* **Copiar endereço**.
* **Iniciar com o Windows** — liga/desliga; grava os argumentos atuais. Usa `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (não exige administrador).
* **Gerar novo token…** — com confirmação; derruba os celulares conectados.
* **Sair**.

Se a porta já estiver em uso (ex.: instância iniciada pelo autostart), o programa avisa em vez de falhar.

### Primeira configuração do HTTPS (uma vez por celular)
**(revisado)** No modo `--https` o servidor também sobe um **servidor HTTP de configuração** na porta `porta+1` (padrão 8001), que serve apenas:
* `GET /` → `web/setup.html`: instruções em 3 passos (baixar, instalar, abrir o controle);
* `GET /ca.crt` → certificado público da CA.

Ele não serve o PWA, o WebSocket nem o token. Se a porta estiver ocupada, o servidor principal sobe mesmo assim, só sem a página de configuração.

**Motivo:** o Chrome no Android **bloqueia downloads em páginas com erro de certificado**, e a página HTTPS tem esse erro até a CA ser instalada (problema encontrado no teste real). Também não dá para linkar um download `http://` a partir da página `https://`, porque o Chrome bloqueia downloads inseguros vindos de página segura. Por isso a faixa amarela do app **navega** até a página HTTP, e o download acontece lá. No máximo, o Chrome mostra o aviso "download não seguro", com a opção **Manter**.

Fluxo:
1. Abrir `http://<ip>:8001`: pelo primeiro QR Code do terminal, pelo menu da bandeja **Instalar certificado no celular** (a janela do QR alterna entre os dois QR Codes) ou pela faixa amarela do app.
2. **Baixar certificado** e instalar:
   * **Android:** Configurações › Segurança › Mais configurações › Criptografia e credenciais › Instalar um certificado › Certificado de CA.
   * **iPhone:** Ajustes › Perfil Baixado › Instalar; depois Ajustes › Geral › Sobre › Ajustes de Confiança de Certificados › ativar "Mouse Remoto CA".
3. Reabrir o navegador, tocar em **Abrir o Mouse Remoto** (`https://<ip>:8000/`) e usar **Adicionar à tela inicial**. O token já está salvo no `localStorage` da origem HTTPS desde a primeira leitura do QR.

---

## 9. Roteiro de Desenvolvimento — Entregue

* **Fase 1 — MVP (Core)** ✅
  * Servidor FastAPI + WebSocket + `pynput`; touchpad com movimento, cliques, arraste e rolagem.
  * Incluídos já nesta fase **(revisado)**: token de acesso, verificação de `Origin`, DPI-awareness, acúmulo de frações, liberação de botões ao desconectar, envio por quadro, reconexão automática, indicador de latência, QR Code no terminal.
* **Fase 2 — Múltiplos Monitores** ✅
  * Lista de monitores ao conectar e quando muda; teleporte para o centro; trava com reaplicação após reconexão; nomes personalizados.
* **Fase 3 — PWA e Refinamento** ✅
  * `manifest.json`, ícones, `sw.js`; Wake Lock; CSS anti-gestos do navegador.
  * **(revisado)** HTTPS opcional com CA local (necessário para Service Worker e Wake Lock — ver seção 11).
  * Incluídos: teclas de mídia, teclas de player, teclado do celular, campo para colar o token.
* **Fase 4 — Segurança e Conveniência** ✅
  * Token via QR Code (antecipado para a Fase 1); troca de token pelo menu com desconexão dos clientes.
  * Ícone na bandeja, janela do QR Code, iniciar com o Windows, log em arquivo, proteção contra instância duplicada.

---

## 10. Segurança

* **Token de acesso:** 96 bits aleatórios (`secrets.token_urlsafe(12)`), salvo em `server/token.txt`, comparado em tempo constante. Vai no QR Code; no celular, fica no `localStorage` e é removido da barra de endereço.
* **Origin:** conexões WebSocket vindas de páginas de outros sites são recusadas (impede que um site aberto no celular controle o PC).
* **Abrir apps — lista fechada:** o celular envia só o `app-id`; o servidor abre apenas atalhos da lista que ele mesmo descobriu no menu Iniciar. Nenhum caminho, comando ou argumento vem do celular (testado: um caminho de executável no lugar do id é recusado com `ok: false`).
* **Troca de token:** pelo menu da bandeja ou `--new-token`; celulares com o token antigo são desconectados com `4401`.
* **CA local restrita (Name Constraints):** a CA só pode emitir certificados para `localhost`, o nome deste PC e IPs privados (`10/8`, `172.16/12`, `192.168/16`, `127/8`). Mesmo que `server/certs/ca.key` vaze, não serve para falsificar sites da internet no celular. Testado: um certificado para `www.google.com` assinado pela CA é rejeitado.
* **Certificado do servidor:** validade de 397 dias (limite do Safari/Chrome: 398); reemitido automaticamente quando o IP muda ou faltam 30 dias para vencer — sem reinstalar nada no celular.
* **Arquivos sensíveis** (`token.txt`, `certs/`, log) estão no `.gitignore`.
* **Limite conhecido:** em HTTP, o token trafega sem criptografia na rede local. Em redes não confiáveis, usar `--https`.

---

## 11. Pontos de Atenção Técnicos

* **HTTPS é obrigatório para Service Worker e Wake Lock (revisado).** Esses recursos só funcionam em contexto seguro; `http://192.168.x.x` não é. Sem `--https` o app funciona, mas não instala como app *standalone* e a tela do celular pode apagar (o botão de tela cheia continua disponível).
* **Download do certificado (revisado):** precisa ser feito pela página HTTP de configuração (`porta+1`), não pela página HTTPS. Ver seção 8.
* **Barra de telas oculta:** com só um monitor ativo (ex.: TV desligada), a barra de seleção de telas some de propósito e volta sozinha quando o segundo monitor reaparece (em até 3 s).
* **iPhone — armazenamento separado:** o app instalado na tela inicial não compartilha o `localStorage` com o Safari. Na primeira abertura do app instalado, colar o endereço/token no campo exibido.
* **Windows — Firewall:** liberar na **Rede Privada**. Atenção: `pythonw.exe` (modo bandeja) é um executável diferente de `python.exe` e pede permissão separada.
* **Windows — janelas elevadas (UIPI):** sem privilégio de administrador, não é possível controlar janelas de programas elevados (Gerenciador de Tarefas, instaladores), a tela de UAC nem a tela de bloqueio.
* **Linux (Wayland vs. X11):** `pynput` funciona em X11. Em Wayland a injeção de input é bloqueada; usar `ydotool` ou `evdev`/`uinput`. O autostart e a bandeja foram feitos e testados só para Windows.
* **macOS:** exige permissão de **Acessibilidade** para o Python. Não testado.
* **CSS `touch-action: none`:** obrigatório na área do touchpad e nos botões.

---

## 12. Pendências e Próximos Passos

### Testado em aparelho real (Android/Chrome)
* Touchpad, gestos e cliques; teclado do celular; TV desligada (barra de telas some e a trava é solta); início com o Windows.

### Ainda não testado em aparelho real
* Instalação do certificado pela página HTTP de configuração e instalação como app (a primeira tentativa, pela página HTTPS, foi bloqueada pelo Chrome e motivou a página HTTP).
* iPhone (Safari, certificado, teclado).

### Ideias futuras
* Clique do meio e rolagem horizontal dedicada.
* Atalhos configuráveis (ex.: `Alt+Tab`, `Win`, `Ctrl+W`).
* Toggle de rolagem natural/invertida e ajuste da velocidade de rolagem.
* Suporte a Linux/macOS no modo bandeja e autostart.
