<p align="center">
  <img src="web/icons/icon-192.png" width="96" alt="Mouse Remoto">
</p>

<h1 align="center">Mouse Remoto</h1>

<p align="center">
  Use o celular como touchpad e controle de mídia do PC — feito para o PC ligado na TV.<br>
  Sem app para instalar no celular: é uma página web (PWA) servida pelo próprio PC.
</p>

---

## Recursos

- **Touchpad** com aceleração: movimentos lentos para precisão, rápidos para atravessar a tela.
- **Gestos:** toque = clique, dois dedos = clique direito e rolagem, toque + arrastar = segurar clique.
- **Vários monitores:** botões para levar o cursor direto para a TV e **trava** para ele não fugir para o monitor da mesa.
- **Mídia e teclado:** play/pause, volume, Espaço/F para players web e o teclado do celular para digitar no PC.
- **Instala como app** no Android e iPhone, com tela sempre ligada (modo HTTPS).
- **Ícone na bandeja** com QR Code e opção de **iniciar com o Windows**.
- **Protegido por token:** só quem escaneou o QR Code consegue controlar o PC.

## Requisitos

- **PC:** Windows 10/11 e Python 3.10+.
  Linux (X11) e macOS devem funcionar no modo terminal, mas não foram testados. Bandeja e autostart são só para Windows.
- **Celular:** qualquer navegador moderno (Chrome no Android, Safari no iPhone).
- PC e celular **na mesma rede Wi-Fi**.

## Instalação

```bash
git clone https://github.com/cellomrs/mouse_remoto.git
cd mouse_remoto
pip install -r server/requirements.txt
```

## Uso

### Início rápido

```bash
python server/main.py
```

1. Um QR Code aparece no terminal. Escaneie com a câmera do celular.
2. Na primeira vez, o Windows pergunta sobre o Firewall: permita na **Rede Privada**.
3. Pronto: deslize o dedo na área central para mover o cursor.

### Instalar como app (HTTPS)

Para adicionar à tela inicial do celular e impedir que a tela apague, o navegador exige HTTPS:

```bash
python server/main.py --https
```

Na primeira vez em cada celular:

1. Escaneie o QR Code e aceite o aviso de segurança.
2. Toque em **Baixar certificado** na faixa amarela e instale:
   - **Android:** Configurações › Segurança › Mais configurações › Criptografia e credenciais › Instalar um certificado › Certificado de CA.
   - **iPhone:** Ajustes › Perfil Baixado › Instalar. Depois: Ajustes › Geral › Sobre › Ajustes de Confiança de Certificados › ative **Mouse Remoto CA**.
3. Reabra o navegador, acesse o endereço e use **Adicionar à tela inicial**.

O certificado é de uma autoridade criada no seu PC e **só vale para a rede local**: ela não consegue emitir certificados para sites da internet (veja [Segurança](#segurança)).

> **iPhone:** o app instalado não compartilha dados com o Safari. Na primeira abertura, cole o endereço do QR Code no campo que aparece na tela.

### Em segundo plano (bandeja do sistema)

```bash
pythonw server/main.py --tray --https
```

Roda sem janela de terminal. Clique no ícone da bandeja para ver o QR Code. Pelo botão direito:

| Opção | O que faz |
|---|---|
| Mostrar QR Code | Janela com o QR Code e o endereço |
| Copiar endereço | Copia a URL de acesso |
| Iniciar com o Windows | Liga/desliga a inicialização automática com as opções atuais |
| Gerar novo token… | Troca a senha de acesso e desconecta os celulares conectados |
| Sair | Encerra o servidor |

> O `pythonw.exe` é tratado pelo Firewall como um programa diferente do `python.exe`: permita-o também na **Rede Privada**.

### Opções

| Opção | Descrição |
|---|---|
| `--https` | Serve via HTTPS (instalar como app, tela sempre ligada) |
| `--tray` | Segundo plano com ícone na bandeja |
| `--port N` | Porta (padrão `8000`) |
| `--host H` | Interface de rede (padrão `0.0.0.0`) |
| `--new-token` | Gera um novo token de acesso |

## Gestos e controles

| No touchpad | Ação |
|---|---|
| Deslizar 1 dedo | Mover o cursor |
| Tocar | Clique esquerdo |
| Tocar duas vezes | Duplo clique |
| Tocar, tocar de novo e arrastar | Segurar o clique (arrastar barra de progresso, janelas) |
| Tocar com 2 dedos | Clique direito |
| Deslizar 2 dedos | Rolagem |

| Botões | |
|---|---|
| **Tela 1 / Tela 2** | Leva o cursor ao centro da tela. **Segure** para renomear (ex.: "TV") |
| **🔓 Travar** | Prende o cursor na tela selecionada |
| **Esc · Espaço · F** | Teclas comuns. Espaço e F controlam YouTube/Netflix (play e tela cheia) |
| **⌨** | Abre o teclado do celular para digitar no PC |
| **⏮ ⏯ ⏭ 🔉 🔇 🔊** | Teclas de mídia do sistema (Spotify, players do Windows). Segure o volume para repetir |
| **Esquerdo · Direito** | Botões do mouse. Segure para manter pressionado |
| **Sens.** | Sensibilidade do cursor |

## Segurança

- O endereço do QR Code contém um **token de acesso** aleatório. Sem ele, ninguém na rede consegue conectar. Não compartilhe o QR Code.
- Para revogar o acesso, use **Gerar novo token…** na bandeja ou `--new-token`.
- Páginas de outros sites abertas no celular não conseguem se conectar ao servidor.
- A autoridade certificadora do modo HTTPS é restrita a `localhost`, ao nome do PC e a IPs de rede privada. Mesmo que a chave vaze, ela não serve para falsificar sites da internet no celular.
- No modo HTTP o token trafega sem criptografia na rede local. Em redes que não são suas, prefira `--https`.
- Ficam fora do Git: `server/token.txt`, `server/certs/` e `server/mouse_remoto.log`.

## Problemas comuns

| Sintoma | Solução |
|---|---|
| Celular não conecta | Mesma rede Wi-Fi? Liberou `python.exe`/`pythonw.exe` no Firewall na Rede Privada? |
| "Token inválido" | O token foi trocado. Escaneie o QR Code atual |
| "A porta 8000 já está em uso" | O servidor já está rodando (procure o ícone na bandeja) ou use `--port` |
| Não aparece "Adicionar à tela inicial" / tela apaga | Use `--https` e instale o certificado |
| Não controla o Gerenciador de Tarefas ou instaladores | Limite do Windows para janelas de administrador. Rode o servidor como administrador se precisar |
| Linux com Wayland não move o cursor | O Wayland bloqueia injeção de input. Use uma sessão X11 |

## Estrutura

```text
server/   Servidor Python (FastAPI, WebSocket, pynput, bandeja)
web/      PWA servido ao celular (HTML, CSS, JS, Service Worker)
tools/    Gerador dos ícones
docs/     Especificação técnica completa
```

A especificação, com arquitetura, protocolo WebSocket e decisões de projeto, está em [docs/especifica_o_de_projeto_mouse_remoto_com_python_e_pwa.md](docs/especifica_o_de_projeto_mouse_remoto_com_python_e_pwa.md).
